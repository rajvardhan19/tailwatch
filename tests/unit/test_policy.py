from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from tailwatch.sources.policy import (
    Manifest,
    Mode,
    PolicyDenied,
    RequestBudget,
    Source,
    authorize,
    inventory,
    load_manifest,
)

NOW = datetime(2026, 11, 6, tzinfo=UTC)


def source() -> Source:
    return load_manifest(Path("config/sources.fixture.json")).sources[0]


def test_fixture_permission_cannot_authorize_research() -> None:
    authorize(source(), mode=Mode.FIXTURE, capability="fixture", at=NOW)
    with pytest.raises(PolicyDenied):
        authorize(source(), mode=Mode.RESEARCH, capability="fixture", at=NOW)


@pytest.mark.parametrize(
    "change",
    [
        {"enabled": False},
        {"permission": None},
        {"capabilities": []},
    ],
)
def test_undeclared_use_is_denied(change: dict[str, object]) -> None:
    record = source().model_dump() | change
    with pytest.raises(PolicyDenied):
        authorize(Source.model_validate(record), mode=Mode.FIXTURE, capability="fixture", at=NOW)


def test_expiry_retention_and_distribution_are_enforced() -> None:
    for flag in ("external_evidence", "b2b_distribution"):
        with pytest.raises(PolicyDenied):
            authorize(source(), mode=Mode.FIXTURE, capability="fixture", at=NOW, **{flag: True})
    with pytest.raises(PolicyDenied):
        authorize(
            source(), mode=Mode.FIXTURE, capability="fixture", at=datetime(2030, 1, 1, tzinfo=UTC)
        )
    record = source().model_dump()
    record["permission"]["retention"] = False
    with pytest.raises(PolicyDenied):
        authorize(Source.model_validate(record), mode=Mode.FIXTURE, capability="fixture", at=NOW)


def test_manifest_rejects_duplicates_unknown_fields_and_naive_times() -> None:
    record = load_manifest(Path("config/sources.fixture.json")).model_dump()
    record["sources"] = [record["sources"][0]] * 2
    with pytest.raises(ValidationError):
        Manifest.model_validate(record)
    with pytest.raises(ValidationError):
        Source.model_validate(source().model_dump() | {"surprise": True})
    record = source().model_dump()
    record["permission"]["valid_from"] = "2026-01-01T00:00:00"
    with pytest.raises(ValidationError):
        Source.model_validate(record)


def test_budget_reserves_before_work_and_is_thread_safe() -> None:
    budget = RequestBudget(
        Source.model_validate(
            source().model_dump()
            | {
                "max_requests": 100,
                "max_cost_microusd": 10,
            }
        )
    )

    def attempt(_: int) -> bool:
        try:
            budget.reserve(1)
            return True
        except PolicyDenied:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(100))) == 10
    with pytest.raises(ValueError):
        budget.reserve(-1)


def test_free_requests_still_exhaust_request_limit() -> None:
    budget = RequestBudget(Source.model_validate(source().model_dump() | {"max_requests": 1}))
    budget.reserve(0)
    with pytest.raises(PolicyDenied):
        budget.reserve(0)


def test_synthetic_inventory_does_not_claim_feasibility() -> None:
    report = inventory(load_manifest(Path("config/sources.fixture.json")), NOW)
    assert '"g0": "not_established"' in report
    assert "no measured real-data coverage sample" in report


def test_memory_budget_rejects_live_sources_and_other_configurations():
    fixture = source()
    budget = RequestBudget(fixture)
    budget.validate_for(fixture)
    other = Source.model_validate(fixture.model_dump() | {"source_id": "different"})
    with pytest.raises(PolicyDenied, match="match"):
        budget.validate_for(other)
    live = Source.model_validate(fixture.model_dump() | {"kind": "live"})
    with pytest.raises(PolicyDenied, match="durable"):
        RequestBudget(live).validate_for(live)
