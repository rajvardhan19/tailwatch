"""Point-in-time research backtest of the insider-silence component on real SEC filings.

Scope, fixed in the registered config before any computation: for each verified
definitive deal, the target's insider-silence signal is computed with the project's own
`silence()` as of two days before the earliest public announcement evidence, using only
Form 4 filings filed before that cutoff. Matched non-deal peers are scored on the same
date. The flight component needs historical flight coverage and reviewed aircraft
links, which are not available, so it is reported unavailable, never assumed.

Results include misses and false alarms. This is a small, hand-selected research sample:
it is not a validated model, a sealed evaluation, or evidence for any release gate.
"""

import hashlib
import json
import math
import re
import uuid
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from datetime import UTC, date, datetime, time, timedelta
from statistics import median
from typing import Any, Literal

from pydantic import AwareDatetime, Field, model_validator

from tailwatch.domain.time import Availability
from tailwatch.features.signals import SilenceExposure, silence
from tailwatch.sources.policy import Contract, NonEmpty, PolicyDenied
from tailwatch.sources.sec.client import SECClient
from tailwatch.sources.sec.form4 import ParsedForm4, parse_form4

MERGER_TARGET_FORMS = {"DEFM14A", "PREM14A", "DEFM14C", "PREM14C", "SC 14D9", "SC TO-T"}
DOCUMENT = re.compile(r"^(?:xsl[\w.-]+/)?([A-Za-z0-9][\w.-]{0,120}\.xml)$")
PAGE = re.compile(r"^CIK\d{10}-submissions-\d{3}\.json$")
PERSON = uuid.UUID("7b0f8a52-5f1b-4d39-9a43-2d59f4a3c1e0")
FLIGHT_UNAVAILABLE = "unavailable: no historical flight coverage or reviewed aircraft links"


class Case(Contract):
    role: Literal["deal", "control"]
    cik: str = Field(pattern=r"^\d{10}$")
    name: NonEmpty  # exact current SEC entity name, checked before scoring
    label: NonEmpty  # display name at the time of the deal
    acquirer: str | None = None
    announced: date | None = None
    matched_to: str | None = Field(default=None, pattern=r"^\d{10}$")

    @model_validator(mode="after")
    def shape(self) -> "Case":
        if self.role == "deal" and (not self.announced or not self.acquirer or self.matched_to):
            raise ValueError("deals need an announcement date and an acquirer")
        if self.role == "control" and (not self.matched_to or self.announced or self.acquirer):
            raise ValueError("controls reference one deal and carry no announcement")
        return self


class BacktestConfig(Contract):
    name: NonEmpty
    registered_at: AwareDatetime
    threshold: float = Field(gt=0, allow_inf_nan=False)
    rationale: NonEmpty
    recent_days: int = Field(default=45, ge=1)
    baseline_days: int = Field(default=365, ge=30)
    watermark_business_days: int = Field(default=2, ge=0)
    cutoff_days_before: int = Field(default=2, ge=1)
    control_horizon_days: int = Field(default=180, ge=1)
    cases: tuple[Case, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def references(self) -> "BacktestConfig":
        deals = {c.cik for c in self.cases if c.role == "deal"}
        keys = [(c.role, c.cik, c.matched_to) for c in self.cases]
        if len(keys) != len(set(keys)) or len(deals) != sum(c.role == "deal" for c in self.cases):
            raise ValueError("duplicate cases")
        if any(c.matched_to not in deals for c in self.cases if c.role == "control"):
            raise ValueError("every control must reference a configured deal")
        return self

    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


Row = dict[str, str]


def _columns(block: dict[str, Any]) -> list[Row]:
    keys = ("form", "filingDate", "accessionNumber", "primaryDocument", "items")
    columns = [block.get(k, []) for k in keys]
    if any(not isinstance(c, list) or len(c) != len(columns[0]) for c in columns):
        raise ValueError("malformed SEC submissions columns")
    return [
        dict(zip(keys, (str(v or "") for v in values), strict=True))
        for values in zip(*columns, strict=True)
    ]


def filings(client: SECClient, cik: str, start: date) -> tuple[str, list[Row]]:
    """Entity name and filing rows, loading older pages only when they reach `start`."""
    data = json.loads(client.get(f"https://data.sec.gov/submissions/CIK{cik}.json")[0])
    if not isinstance(data, dict) or str(data.get("cik", "")).zfill(10) != cik:
        raise ValueError("SEC response identity mismatch")
    rows = _columns(data["filings"]["recent"])
    for page in data["filings"].get("files", []):
        if (
            isinstance(page, dict)
            and PAGE.match(str(page.get("name", "")))
            and str(page.get("filingTo", "")) >= start.isoformat()
        ):
            older = json.loads(client.get(f"https://data.sec.gov/submissions/{page['name']}")[0])
            rows += _columns(older)
    return str(data.get("name", "")), rows


def announcement(rows: list[Row], claimed: date) -> tuple[date, str] | None:
    """Earliest public evidence near the claimed date, if an Item 1.01 8-K confirms a deal."""
    near = [
        r
        for r in rows
        if claimed - timedelta(days=3)
        <= date.fromisoformat(r["filingDate"])
        <= claimed + timedelta(days=10)
    ]
    definitive = [r for r in near if r["form"] == "8-K" and "1.01" in r["items"].split(",")]
    if not definitive:
        return None
    public = [date.fromisoformat(r["filingDate"]) for r in near if r["form"] in {"8-K", "425"}]
    return min([claimed, *public]), definitive[0]["accessionNumber"]


def business_days_before(day: date, count: int) -> date:
    while count:
        day -= timedelta(days=1)
        count -= day.weekday() < 5
    return day


def weekdays(start: date, end: date) -> int:
    return sum((start + timedelta(days=i)).weekday() < 5 for i in range((end - start).days))


def _fetch(client: SECClient, cik: str, row: Row) -> ParsedForm4 | None:
    """One Form 4 document; None means quarantined. Budget refusals propagate."""
    document = DOCUMENT.match(row["primaryDocument"])
    if not document:
        return None
    folder = row["accessionNumber"].replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{folder}/{document.group(1)}"
    try:
        body, fetch = client.get(url)
        return parse_form4(
            body,
            accession=row["accessionNumber"],
            filed_date=date.fromisoformat(row["filingDate"]),
            available_at=fetch.received_at,
        )
    except PolicyDenied:
        raise
    except (ValueError, OSError):
        return None


def score(
    client: SECClient,
    cik: str,
    rows: list[Row],
    *,
    cutoff: datetime,
    config: BacktestConfig,
    workers: int = 1,
) -> dict[str, Any]:
    """Insider silence as of `cutoff`, from Form 4s filed before the cutoff day only."""
    watermark_day = business_days_before(cutoff.date(), config.watermark_business_days)
    recent_start = watermark_day - timedelta(days=config.recent_days)
    baseline_start = recent_start - timedelta(days=config.baseline_days)
    forms = [
        r
        for r in rows
        if r["form"] in {"4", "4/A"}
        and baseline_start <= date.fromisoformat(r["filingDate"]) < cutoff.date()
    ]
    sales: dict[str, set[date]] = defaultdict(set)
    insiders: set[str] = set()
    quarantined = 0
    # Bounded concurrency hides per-request TLS latency; SECClient's shared limiter
    # still caps the rate, and the first budget refusal cancels everything queued.
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_fetch, client, cik, row) for row in forms]
        done, _ = wait(futures, return_when=FIRST_EXCEPTION)
        failed = next((f for f in done if f.exception() is not None), None)
        if failed is not None:
            pool.shutdown(cancel_futures=True)
            raise failed.exception()  # type: ignore[misc]
    for future in futures:
        parsed = future.result()
        if parsed is None:
            quarantined += 1
            continue
        owners = [o.cik for o in parsed.owners if o.is_director or o.is_officer]
        insiders.update(owners)
        for t in parsed.transactions:
            if t.table == "non_derivative" and t.code == "S":
                for owner in owners:
                    sales[owner].add(t.trade_date)
    exposures = []
    for owner in sorted(insiders):
        days = sales[owner]
        exposures.append(
            SilenceExposure(
                person=uuid.uuid5(PERSON, owner),
                baseline_sales=sum(baseline_start <= d < recent_start for d in days),
                recent_sales=sum(recent_start <= d < watermark_day for d in days),
                baseline_days=weekdays(baseline_start, recent_start),
                recent_days=weekdays(recent_start, watermark_day),
                evidence=f"form4:{cik}:{owner}",
            )
        )
    watermark = datetime.combine(watermark_day, time(0), UTC)
    signal = silence(
        exposures, watermark=watermark, cutoff=cutoff, coverage_complete=not quarantined
    )
    habitual = [e for e in exposures if e.baseline_sales >= 4]
    return {
        "status": str(signal.status),
        "value": signal.value,
        "flagged": signal.status == Availability.OBSERVED
        and (signal.value or 0) >= config.threshold,
        "cutoff": cutoff.isoformat(),
        "watermark": watermark.isoformat(),
        "form4_filings": len(forms),
        "quarantined": quarantined,
        "insiders": len(exposures),
        "habitual_sellers": len(habitual),
        "silent_habitual_sellers": sum(e.recent_sales == 0 for e in habitual),
    }


def wilson(successes: int, total: int) -> tuple[float, float] | None:
    if not total:
        return None
    z = 1.959963984540054
    p = successes / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return (max(0.0, centre - half), min(1.0, centre + half))


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, Any] = {}
    for role in ("deal", "control"):
        scored = [r for r in results if r["role"] == role and r.get("status") == "observed"]
        flagged = sum(r["flagged"] for r in scored)
        groups[role] = {
            "configured": sum(r["role"] == role for r in results),
            "scored": len(scored),
            "flagged": flagged,
            "rate": flagged / len(scored) if scored else None,
            "rate_interval": wilson(flagged, len(scored)),
            "median_value": median(r["value"] for r in scored) if scored else None,
            "unscorable": sum(
                r["role"] == role and r.get("status") not in {None, "observed"} for r in results
            ),
            "excluded": sum(r["role"] == role and bool(r.get("excluded")) for r in results),
        }
    hit, false = groups["deal"]["rate"], groups["control"]["rate"]
    groups["lift"] = hit / false if hit is not None and false else None
    return groups


def run_backtest(
    client: SECClient,
    config: BacktestConfig,
    *,
    progress: Callable[[str], None] = lambda _: None,
    workers: int = 1,
) -> dict[str, Any]:
    ordered = sorted(config.cases, key=lambda c: c.role != "deal")
    announced: dict[str, date] = {}
    results: list[dict[str, Any]] = []
    for index, case in enumerate(ordered, start=1):
        result: dict[str, Any] = {
            "role": case.role,
            "cik": case.cik,
            "name": case.name,
            "label": case.label,
            "acquirer": case.acquirer,
            "matched_to": case.matched_to,
            "flight": FLIGHT_UNAVAILABLE,
        }
        anchor = case.announced or announced.get(case.matched_to or "")
        if anchor is None:
            results.append(result | {"excluded": "matched deal was excluded"})
            continue
        start = anchor - timedelta(days=config.baseline_days + config.recent_days + 30)
        progress(f"[{index}/{len(ordered)}] {case.role} {case.name}")
        sec_name, rows = filings(client, case.cik, start)
        if sec_name.casefold() != case.name.casefold():
            results.append(result | {"excluded": f"SEC entity name is {sec_name!r}"})
            continue
        if case.role == "deal":
            assert case.announced is not None
            evidence = announcement(rows, case.announced)
            if evidence is None:
                results.append(result | {"excluded": "no Item 1.01 8-K near the announcement"})
                continue
            anchor, accession = evidence
            announced[case.cik] = anchor
            folder = accession.replace("-", "")
            result |= {
                "announced": anchor.isoformat(),
                "evidence": f"https://www.sec.gov/Archives/edgar/data/{int(case.cik)}/{folder}/",
            }
        else:
            horizon = anchor + timedelta(days=config.control_horizon_days)
            merger = [
                r
                for r in rows
                if r["form"] in MERGER_TARGET_FORMS
                and anchor < date.fromisoformat(r["filingDate"]) <= horizon
            ]
            if merger:
                results.append(result | {"excluded": "became a merger target within the horizon"})
                continue
            result["as_of_deal_date"] = anchor.isoformat()
        cutoff = datetime.combine(anchor - timedelta(days=config.cutoff_days_before), time(0), UTC)
        results.append(
            result | score(client, case.cik, rows, cutoff=cutoff, config=config, workers=workers)
        )
    return {
        "name": config.name,
        "config_digest": config.digest(),
        "registered_at": config.registered_at.isoformat(),
        "generated_at": datetime.now(UTC).isoformat(),
        "threshold": config.threshold,
        "rationale": config.rationale,
        "method": {
            "signal": "insider buying silence (features.signals.silence), research defaults",
            "recent_days": config.recent_days,
            "baseline_days": config.baseline_days,
            "watermark_business_days": config.watermark_business_days,
            "cutoff_days_before_announcement": config.cutoff_days_before,
            "control_horizon_days": config.control_horizon_days,
            "sales": "non-derivative Form 4 code S trade days by directors and officers",
        },
        "limitations": [
            "Insider silence only; the flight component could not be backtested.",
            "Small, hand-selected sample of large, well-known deals; not representative.",
            "10b5-1 plan status is unknown before April 2023 and is not excluded.",
            "Blackout periods are unknown and not excluded; weekdays approximate trading days.",
            "Controls are sector peers matched by hand, not a random draw.",
            "A peer filing merger proxies as an acquirer is also excluded (conservative).",
            "Connection failures leave a case unscored (missing source), never partially scored.",
            "Not a validated model, sealed evaluation or release-gate evidence.",
        ],
        "summary": summarize(results),
        "results": results,
    }
