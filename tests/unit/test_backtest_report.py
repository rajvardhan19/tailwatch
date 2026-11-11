"""The committed Past deals report must be exactly what the registered config produces."""

import json
from pathlib import Path

from tailwatch.research.backtest import BacktestConfig, summarize

REPORT = json.loads(Path("src/tailwatch/static/backtest.json").read_text())
CONFIG = BacktestConfig.model_validate_json(
    Path("config/backtest.insider-silence.json").read_bytes()
)


def test_report_was_generated_from_the_registered_config_after_registration():
    assert REPORT["config_digest"] == CONFIG.digest()
    assert REPORT["threshold"] == CONFIG.threshold
    assert REPORT["registered_at"] == CONFIG.registered_at.isoformat()
    assert REPORT["registered_at"] <= REPORT["generated_at"]
    expected = sorted(CONFIG.cases, key=lambda case: case.role != "deal")
    assert [(r["role"], r["cik"], r["matched_to"]) for r in REPORT["results"]] == [
        (c.role, c.cik, c.matched_to) for c in expected
    ]


def test_summary_recomputes_and_no_case_is_scored_on_or_after_its_announcement():
    assert json.loads(json.dumps(summarize(REPORT["results"]))) == REPORT["summary"]
    for result in REPORT["results"]:
        assert result["flight"].startswith("unavailable")
        if result.get("flagged"):
            assert result["status"] == "observed" and result["value"] >= CONFIG.threshold
        if "cutoff" in result:
            anchor = result.get("announced") or result["as_of_deal_date"]
            assert result["cutoff"][:10] < anchor
            assert result["watermark"] <= result["cutoff"]
