import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tailwatch.research.backtest import (
    BacktestConfig,
    business_days_before,
    run_backtest,
    weekdays,
    wilson,
)
from tailwatch.sources.objects import LocalObjectStore
from tailwatch.sources.policy import Mode, PolicyDenied, Source, load_manifest
from tailwatch.sources.sec.client import RateLimiter, Response, SECClient

TEMPLATE = Path("tests/fixtures/sec/form4.xml").read_text()
LIVE = load_manifest(Path("config/sources.live.json"))
SEC = next(s for s in LIVE.sources if s.source_id == "sec-live")
SOURCE = Source.model_validate(
    SEC.model_dump()
    | {
        "permission": SEC.permission.model_dump()  # type: ignore[union-attr]
        | {
            "valid_from": datetime(2020, 1, 1, tzinfo=UTC),
            "valid_until": datetime(2100, 1, 1, tzinfo=UTC),
        }
    }
)


def form4(owner: str, trade: str) -> bytes:
    """The synthetic fixture rewritten as an open-market sale by `owner` on `trade`."""
    body = TEMPLATE.replace(
        "<transactionCode>P</transactionCode>", "<transactionCode>S</transactionCode>"
    )
    body = body.replace("<value>2026-01-01</value>", f"<value>{trade}</value>")
    body = body.replace(
        "<transactionAcquiredDisposedCode><value>A</value>",
        "<transactionAcquiredDisposedCode><value>D</value>",
    )
    return body.replace(
        "<rptOwnerCik>2</rptOwnerCik>", f"<rptOwnerCik>{owner}</rptOwnerCik>"
    ).encode()


class FakeSEC:
    def __init__(self) -> None:
        self.bodies: dict[str, bytes] = {}
        self.requested: list[str] = []

    def company(self, cik: str, name: str, rows: list[tuple[str, str, str, str]]) -> None:
        """rows: (form, filingDate, items, Form 4 trade date or '')."""
        recent: dict[str, list[str]] = {
            k: [] for k in ("form", "filingDate", "accessionNumber", "primaryDocument", "items")
        }
        for index, (form, filed, items, trade) in enumerate(rows):
            accession = f"{cik}-24-{index:06d}"
            recent["form"].append(form)
            recent["filingDate"].append(filed)
            recent["accessionNumber"].append(accession)
            recent["primaryDocument"].append("xslF345X05/form4.xml" if trade else "doc.htm")
            recent["items"].append(items)
            if trade:
                self.bodies[self.form4_url(cik, accession)] = form4("77", trade)
        payload = {"cik": cik, "name": name, "filings": {"recent": recent, "files": []}}
        self.bodies[f"https://data.sec.gov/submissions/CIK{cik}.json"] = json.dumps(
            payload
        ).encode()

    @staticmethod
    def form4_url(cik: str, accession: str) -> str:
        folder = accession.replace("-", "")
        return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{folder}/form4.xml"

    def get(self, url, headers):
        self.requested.append(url)
        return Response(200, self.bodies[url], {})


class Budget:
    def __init__(self, limit: int = 1000) -> None:
        self.limit, self.used = limit, 0

    def validate_for(self, source: Source) -> None:
        pass

    def reserve(self, cost_microusd: int) -> None:
        if self.used >= self.limit:
            raise PolicyDenied("durable budget exhausted")
        self.used += 1


def client(fake: FakeSEC, tmp_path: Path, budget: Budget | None = None) -> SECClient:
    return SECClient(
        source=SOURCE,
        mode=Mode.RESEARCH,
        user_agent="Tailwatch test ops@example.invalid",
        store=LocalObjectStore(tmp_path),
        transport=fake,
        record=lambda _: None,
        budget=budget or Budget(),
        limiter=RateLimiter(sleep=lambda _: None),
    )


def config(*cases: dict) -> BacktestConfig:
    return BacktestConfig.model_validate(
        {
            "name": "unit",
            "registered_at": "2024-01-01T00:00:00Z",
            "threshold": 0.5,
            "rationale": "unit test",
            "cases": list(cases),
        }
    )


DEAL = {
    "role": "deal",
    "cik": "0000000001",
    "name": "SYNTHETIC TARGET",
    "label": "Target",
    "acquirer": "Buyer",
    "announced": "2024-06-20",
}


def seller_history(deal_8k: bool = True) -> list[tuple[str, str, str, str]]:
    rows = [
        ("4", f"2023-{m:02d}-1{d}", "", f"2023-{m:02d}-1{d - 2}")
        for m, d in ((8, 5), (9, 5), (10, 6), (11, 6), (12, 5), (2, 6))
    ]
    rows[-1] = ("4", "2024-02-16", "", "2024-02-14")
    if deal_8k:
        rows.append(("8-K", "2024-06-20", "1.01,9.01", ""))
    # Filed after the cutoff: a recent sale that must stay invisible to the score.
    rows.append(("4", "2024-06-19", "", "2024-06-03"))
    return rows


def test_silent_habitual_seller_is_flagged_without_looking_past_the_cutoff(tmp_path):
    fake = FakeSEC()
    fake.company("0000000001", "SYNTHETIC TARGET", seller_history())
    fake.company(
        "0000000003",
        "SYNTHETIC PEER",
        [
            *[("4", f"2023-{m:02d}-15", "", f"2023-{m:02d}-13") for m in (8, 9, 10, 11, 12)],
            ("4", "2024-05-20", "", "2024-05-16"),  # still selling in the recent window
        ],
    )
    fake.company("0000000004", "LATER TARGET", [("DEFM14A", "2024-09-01", "", "")])
    fake.company("0000000005", "SOMEONE ELSE", [])
    report = run_backtest(
        client(fake, tmp_path),
        workers=4,  # the concurrent path must give the same point-in-time result
        config=config(
            DEAL,
            {
                "role": "control",
                "cik": "0000000003",
                "name": "SYNTHETIC PEER",
                "label": "Peer",
                "matched_to": "0000000001",
            },
            {
                "role": "control",
                "cik": "0000000004",
                "name": "LATER TARGET",
                "label": "Later",
                "matched_to": "0000000001",
            },
            {
                "role": "control",
                "cik": "0000000005",
                "name": "EXPECTED NAME",
                "label": "Wrong",
                "matched_to": "0000000001",
            },
        ),
    )
    deal, peer, later, wrong = report["results"]
    assert deal["announced"] == "2024-06-20" and deal["status"] == "observed"
    assert deal["flagged"] is True and deal["value"] > 0.5 and deal["silent_habitual_sellers"] == 1
    assert deal["cutoff"] == "2024-06-18T00:00:00+00:00"
    late = FakeSEC.form4_url("0000000001", "0000000001-24-000007")  # filed 2024-06-19
    assert late in fake.bodies and late not in fake.requested
    assert peer["status"] == "observed" and peer["value"] == 0 and peer["flagged"] is False
    assert later["excluded"] == "became a merger target within the horizon"
    assert "SEC entity name" in wrong["excluded"]
    summary = report["summary"]
    assert (summary["deal"]["scored"], summary["deal"]["flagged"]) == (1, 1)
    assert (summary["control"]["scored"], summary["control"]["flagged"], summary["lift"]) == (
        1,
        0,
        None,
    )
    assert report["results"][0]["flight"].startswith("unavailable")


def test_unverified_deals_and_their_controls_are_excluded_not_scored(tmp_path):
    fake = FakeSEC()
    fake.company("0000000001", "SYNTHETIC TARGET", seller_history(deal_8k=False))
    report = run_backtest(
        client(fake, tmp_path),
        config(
            DEAL,
            {
                "role": "control",
                "cik": "0000000003",
                "name": "SYNTHETIC PEER",
                "label": "Peer",
                "matched_to": "0000000001",
            },
        ),
    )
    assert report["results"][0]["excluded"] == "no Item 1.01 8-K near the announcement"
    assert report["results"][1]["excluded"] == "matched deal was excluded"
    assert report["summary"]["deal"]["scored"] == 0


def test_exhausted_budget_stops_the_run_instead_of_quarantining(tmp_path):
    fake = FakeSEC()
    fake.company("0000000001", "SYNTHETIC TARGET", seller_history())
    with pytest.raises(PolicyDenied):
        run_backtest(client(fake, tmp_path, Budget(limit=3)), config(DEAL))


def test_calendar_and_interval_helpers():
    assert business_days_before(date(2024, 6, 18), 2) == date(2024, 6, 14)  # Tue -> Fri
    assert weekdays(date(2024, 6, 10), date(2024, 6, 17)) == 5
    assert wilson(0, 0) is None
    low, high = wilson(5, 10)
    assert 0.2 < low < 0.5 < high < 0.8
    with pytest.raises(ValueError):
        config(
            {
                "role": "control",
                "cik": "0000000003",
                "name": "X",
                "label": "X",
                "matched_to": "0000000009",
            }
        )
