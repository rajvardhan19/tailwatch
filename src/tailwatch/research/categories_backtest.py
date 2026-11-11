"""Replay the three signal categories over the past-deal cases from journaled SEC data.

No network: every response comes from the SEC journal recorded by the insider-silence
backtest, so the same point-in-time filings are reused. Each case is evaluated as of the
day before its cutoff (two days ahead of the earliest announcement evidence), using only
filings filed by then. A case with any missing filing is reported incomplete, never
scored on partial data. The flight category cannot be replayed: it needs historical
flight coverage and reviewed company-aircraft links.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, text

from tailwatch.features.categories import CategoryConfig, category, insider_cluster, purchases
from tailwatch.research.backtest import _fetch, filings, wilson
from tailwatch.sources.objects import ObjectStore
from tailwatch.sources.sec.client import Fetch

FLIGHT_UNAVAILABLE = "unavailable: no historical flight coverage or reviewed aircraft links"


class ReplayClient:
    """Serves previously journaled SEC responses by URL; never touches the network."""

    def __init__(self, engine: Engine, store: ObjectStore) -> None:
        self.engine, self.store = engine, store

    def get(self, url: str) -> tuple[bytes, Fetch]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT digest,requested_at,received_at FROM sec_fetches "
                    "WHERE url=:url AND status=200 ORDER BY received_at DESC LIMIT 1"
                ),
                {"url": url},
            ).first()
        if row is None:
            raise OSError("response was never journaled")
        return self.store.get(row.digest), Fetch(
            url, 200, row.digest, row.requested_at, row.received_at
        )


def replay_case(
    client: ReplayClient, case: dict[str, Any], config: CategoryConfig
) -> dict[str, Any]:
    cutoff = datetime.fromisoformat(case["cutoff"])
    as_of = cutoff.date() - timedelta(days=1)  # filings filed before the cutoff day only
    start = as_of - timedelta(days=config.insider.lookback_days + 30)
    _, rows = filings(client, case["cik"], start)  # type: ignore[arg-type]
    forms = [
        r
        for r in rows
        if r["form"] in {"4", "4/A"} and start <= date.fromisoformat(r["filingDate"]) <= as_of
    ]
    found = []
    missing = 0
    for row in forms:
        parsed = _fetch(client, case["cik"], row)  # type: ignore[arg-type]
        if parsed is None:
            missing += 1
            continue
        found += purchases(parsed, date.fromisoformat(row["filingDate"]))
    insider = insider_cluster(found, as_of=as_of, rule=config.insider)
    status = "observed" if not missing else "incomplete"
    insider_on = insider["public_on"] if status == "observed" and insider["triggered"] else None
    anchor = date.fromisoformat(case.get("announced") or case["as_of_deal_date"])
    return {
        "insider": {
            **{k: (v.isoformat() if isinstance(v, date) else v) for k, v in insider.items()},
            "status": status,
            "form4_filings": len(forms),
            "missing": missing,
            "lead_days": (anchor - insider_on).days if insider_on else None,
        },
        "flight": {"status": "unavailable", "reason": FLIGHT_UNAVAILABLE},
        "category": category(None, insider_on, both_window_days=config.both_window_days),
    }


def run_category_backtest(
    engine: Engine, store: ObjectStore, silence_report: dict[str, Any], config: CategoryConfig
) -> dict[str, Any]:
    client = ReplayClient(engine, store)
    results = []
    for case in silence_report["results"]:
        keep = {k: case.get(k) for k in ("role", "cik", "label", "acquirer", "matched_to")}
        keep |= {k: case[k] for k in ("announced", "as_of_deal_date", "evidence") if k in case}
        if case.get("excluded") or "cutoff" not in case:
            results.append(keep | {"excluded": case.get("excluded") or "not evaluated"})
            continue
        results.append(keep | {"cutoff": case["cutoff"]} | replay_case(client, case, config))
    summary: dict[str, Any] = {}
    for role in ("deal", "control"):
        observed = [
            r
            for r in results
            if r["role"] == role and r.get("insider", {}).get("status") == "observed"
        ]
        hits = [r for r in observed if r["category"] in {"insider", "both"}]
        summary[role] = {
            "configured": sum(r["role"] == role for r in results),
            "observed": len(observed),
            "insider_triggered": len(hits),
            "rate": len(hits) / len(observed) if observed else None,
            "rate_interval": wilson(len(hits), len(observed)),
            "incomplete": sum(
                r["role"] == role and r.get("insider", {}).get("status") == "incomplete"
                for r in results
            ),
            "excluded": sum(r["role"] == role and bool(r.get("excluded")) for r in results),
        }
    leads = sorted(
        r["insider"]["lead_days"]
        for r in results
        if r["role"] == "deal" and r["category"] == "insider"
    )
    summary["deal"]["median_lead_days"] = leads[len(leads) // 2] if leads else None
    return {
        "name": config.name,
        "registered_at": config.registered_at.isoformat(),
        "generated_at": datetime.now(UTC).isoformat(),
        "rules": config.model_dump(mode="json"),
        "source_report": silence_report["config_digest"],
        "summary": summary,
        "results": results,
        "limitations": [
            "Flight-triggered and both categories cannot be replayed: no historical flight "
            "coverage or reviewed company-aircraft links exist yet.",
            "Insiders who know about a pending deal may not legally buy, so insider clusters "
            "measure insider confidence more than takeover risk.",
            "Same small, hand-selected deal and peer sample as the insider-silence study.",
            "Purchase codes are as filed; 10b5-1 plan status before April 2023 is unknown.",
        ],
    }
