import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from tailwatch.features.categories import (
    BuyerPurchase,
    CategoryConfig,
    ClusterRule,
    category,
    insider_cluster,
    purchases,
)
from tailwatch.sources.sec.form4 import parse_form4

RULE = ClusterRule(min_buyers=3, window_days=14, lookback_days=90)


def buy(group: str, trade: str, filed: str, accession: str) -> BuyerPurchase:
    return BuyerPurchase(
        group=group,
        names=(group,),
        roles=("director",),
        trade_date=date.fromisoformat(trade),
        filed=date.fromisoformat(filed),
        shares=Decimal(100),
        price=None,
        accession=accession,
    )


def test_three_distinct_buyers_in_fourteen_days_trigger_once_public():
    rows = [
        buy("a", "2026-03-02", "2026-03-03", "1"),
        buy("b", "2026-03-05", "2026-03-06", "2"),
        buy("a", "2026-03-06", "2026-03-09", "3"),  # same group again: still one buyer
        buy("c", "2026-03-12", "2026-03-13", "4"),
    ]
    result = insider_cluster(rows, as_of=date(2026, 3, 20), rule=RULE)
    assert result["triggered"] and result["buyers"] == 3
    assert result["public_on"] == date(2026, 3, 13)  # the third group's filing date
    # One day earlier the third buyer had not filed: no trigger, no hindsight.
    assert not insider_cluster(rows, as_of=date(2026, 3, 12), rule=RULE)["triggered"]


def test_spread_out_buyers_and_old_purchases_do_not_trigger():
    spread = [
        buy("a", "2026-01-01", "2026-01-02", "1"),
        buy("b", "2026-01-10", "2026-01-12", "2"),
        buy("c", "2026-01-20", "2026-01-21", "3"),  # outside a 14-day window of "a"
    ]
    assert insider_cluster(spread, as_of=date(2026, 2, 1), rule=RULE)["buyers"] == 2
    old = [buy(g, "2025-01-05", "2025-01-06", g) for g in "abc"]
    assert insider_cluster(old, as_of=date(2026, 2, 1), rule=RULE)["buyers"] == 0


def test_joint_filers_on_one_filing_count_as_one_buyer_group():
    body = Path("tests/fixtures/sec/form4.xml").read_text()
    joint = body.replace(
        "</reportingOwner>",
        "</reportingOwner>\n  <reportingOwner><reportingOwnerId><rptOwnerCik>3</rptOwnerCik>"
        "<rptOwnerName>SECOND SYNTHETIC PERSON</rptOwnerName></reportingOwnerId>"
        "<reportingOwnerRelationship><isDirector>1</isDirector></reportingOwnerRelationship>"
        "</reportingOwner>",
        1,
    )
    parsed = parse_form4(
        joint.encode(),
        accession="0000000001-26-000001",
        filed_date=date(2026, 1, 2),
        available_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    rows = purchases(parsed, date(2026, 1, 2))
    assert rows and {row.group for row in rows} == {"0000000002+0000000003"}
    assert rows[0].roles == ("director", "officer")


def test_both_requires_the_two_triggers_within_the_window():
    flight, insider = date(2026, 3, 1), date(2026, 3, 20)
    assert category(flight, insider, both_window_days=30) == "both"
    assert category(flight, date(2026, 6, 1), both_window_days=30) == "insider"
    assert category(None, insider, both_window_days=30) == "insider"
    assert category(flight, None, both_window_days=30) == "flight"
    assert category(None, None, both_window_days=30) is None


def test_committed_category_report_matches_its_rules_and_source():
    config = CategoryConfig.model_validate_json(Path("config/signal-categories.json").read_bytes())
    report = json.loads(Path("src/tailwatch/static/categories.json").read_text())
    silence = json.loads(Path("src/tailwatch/static/backtest.json").read_text())
    assert report["rules"] == config.model_dump(mode="json")
    assert report["source_report"] == silence["config_digest"]
    assert report["registered_at"] <= report["generated_at"]
    assert [r["cik"] for r in report["results"]] == [r["cik"] for r in silence["results"]]
    for role in ("deal", "control"):
        observed = [
            r
            for r in report["results"]
            if r["role"] == role and r.get("insider", {}).get("status") == "observed"
        ]
        fired = [r for r in observed if r["category"] in {"insider", "both"}]
        assert report["summary"][role]["observed"] == len(observed)
        assert report["summary"][role]["insider_triggered"] == len(fired)
    for result in report["results"]:
        if "cutoff" in result:
            assert result["flight"]["status"] == "unavailable"
            if result["insider"]["public_on"]:
                anchor = result.get("announced") or result["as_of_deal_date"]
                assert result["insider"]["public_on"] < anchor
