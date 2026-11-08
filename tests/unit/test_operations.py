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


def test_current_session_rolls_at_deadline_and_respects_holidays_dst():
    from tailwatch.operations.runtime import required_session

    # Explicit synthetic calendar: weekend and Monday holiday omitted.
    sessions = (date(2026, 3, 6), date(2026, 3, 10))
    valid = date(2026, 3, 11)
    assert required_session(sessions, valid, datetime(2026, 3, 9, 16, tzinfo=UTC)) == sessions[0]
    assert (
        required_session(sessions, valid, datetime(2026, 3, 10, 12, 29, tzinfo=UTC)) == sessions[0]
    )
    assert (
        required_session(sessions, valid, datetime(2026, 3, 10, 12, 30, tzinfo=UTC)) == sessions[1]
    )
    # Calendar coverage is based on New York date, not the next UTC date.
    assert required_session(sessions, valid, datetime(2026, 3, 12, 2, tzinfo=UTC)) == sessions[1]
    with pytest.raises(ValueError, match="expired"):
        required_session(sessions, valid, datetime(2026, 3, 12, 5, tzinfo=UTC))
    with pytest.raises(ValueError, match="aware"):
        required_session(sessions, valid, datetime(2026, 3, 10))


def test_readiness_expiry_tracks_earliest_required_source_only():
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def source(name, hours):
        return SourceHealth(
            source_id=name,
            fresh_until=now + timedelta(hours=hours),
            complete=True,
            hash_verified=True,
            missing_fraction=0,
            drift=0,
        )

    result = readiness(
        [source("flights", 3), source("sec", 2), source("optional", 1)],
        {"flights", "sec"},
        now,
        max_missing=0.1,
        max_drift=0.1,
    )
    assert result["ready"] is True
    assert result["valid_until"] == (now + timedelta(hours=2)).isoformat()
