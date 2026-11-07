from datetime import UTC, date, datetime, timedelta

import pytest

from tailwatch.operations.release import ReleaseManifest
from tailwatch.operations.runtime import SourceHealth, deadline, readiness


def test_deadline_uses_new_york_dst_and_declared_sessions():
    winter, summer = date(2026, 1, 5), date(2026, 7, 6)
    assert deadline(winter, {winter, summer}).hour == 13
    assert deadline(summer, {winter, summer}).hour == 12
    with pytest.raises(ValueError):
        deadline(date(2026, 7, 4), {winter, summer})


def test_source_outage_corruption_missingness_and_drift_fail_readiness():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    source = SourceHealth(
        source_id="flight",
        fresh_until=now + timedelta(days=1),
        complete=True,
        hash_verified=True,
        missing_fraction=0,
        drift=0,
    )
    assert readiness([source], {"flight"}, now, max_missing=0.1, max_drift=0.2)["ready"]
    for change in (
        {"complete": False},
        {"hash_verified": False},
        {"missing_fraction": 0.5},
        {"drift": 0.5},
        {"fresh_until": now - timedelta(seconds=1)},
    ):
        assert not readiness(
            [SourceHealth.model_validate(source.model_dump() | change)],
            {"flight"},
            now,
            max_missing=0.1,
            max_drift=0.2,
        )["ready"]
    assert not readiness([], {"flight"}, now, max_missing=0.1, max_drift=0.2)["ready"]


def test_production_manifest_cannot_omit_gates_or_use_synthetic_evidence():
    with pytest.raises(ValueError):
        ReleaseManifest.model_validate({"synthetic": True, "gate_evidence": {}})
