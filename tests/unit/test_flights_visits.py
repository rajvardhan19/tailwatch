from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from tailwatch.features.visits import attribute_hosts, completed_visits
from tailwatch.sources.flights import (
    FlightBatch,
    FlightCoverage,
    FlightLeg,
    FlightPage,
    collect_flights,
)
from tailwatch.sources.objects import LocalObjectStore
from tailwatch.sources.policy import Mode, RequestBudget, Source, load_manifest

T = datetime(2026, 1, 2, tzinfo=UTC)
AIRCRAFT, A, B = uuid4(), uuid4(), uuid4()


def legs():
    return (
        FlightLeg(
            source_id="synthetic",
            leg_id="1",
            airframe_id=AIRCRAFT,
            origin=A,
            destination=B,
            departed_at=T,
            arrived_at=T + timedelta(hours=1),
            available_at=T + timedelta(hours=1),
            timing="inferred",
            evidence="row1",
        ),
        FlightLeg(
            source_id="synthetic",
            leg_id="2",
            airframe_id=AIRCRAFT,
            origin=B,
            destination=A,
            departed_at=T + timedelta(hours=5),
            arrived_at=T + timedelta(hours=6),
            available_at=T + timedelta(hours=6),
            timing="inferred",
            evidence="row2",
        ),
    )


def batch():
    return FlightBatch(
        source_id="synthetic",
        status="complete",
        watermark=T + timedelta(days=1),
        raw_digests=(),
        legs=legs(),
        conflicts=(),
        coverage=(
            FlightCoverage(
                start=T,
                end=T + timedelta(days=1),
                available_at=T + timedelta(days=1),
                airframe_id=AIRCRAFT,
                status="complete",
                evidence="coverage",
            ),
        ),
    )


def test_future_departure_and_incomplete_coverage_never_create_completed_visits():
    assert not completed_visits(batch(), T + timedelta(hours=2))[0].completed
    assert completed_visits(batch(), T + timedelta(days=1))[0].completed
    missing = FlightBatch.model_validate(batch().model_dump() | {"coverage": []})
    assert not completed_visits(missing, T + timedelta(days=1))[0].completed
    outage = FlightBatch.model_validate(batch().model_dump() | {"status": "incomplete"})
    assert completed_visits(outage, T + timedelta(days=1)) == []


def test_host_background_own_company_and_event_discount_once():
    visitor, host = uuid4(), uuid4()
    output = attribute_hosts(
        {visitor: 0, host: 50}, visitor=visitor, overlapping_events=("conference", "board")
    )
    assert visitor not in output.weights
    assert 0 < output.weights[host] < 0.2
    assert sum(output.weights.values()) + output.background == pytest.approx(1)
    assert output.event_multiplier == 0.2
    assert not attribute_hosts({host: 0}, visitor=visitor, home_base=True).weights


def test_pagination_budget_retry_conflict_and_outage(tmp_path):
    source = Source.model_validate(
        load_manifest(Path("config/sources.fixture.json")).sources[0].model_dump()
        | {"source_id": "synthetic", "capabilities": ["flights"], "max_requests": 2}
    )
    page = FlightPage(
        source_id="synthetic",
        watermark=T + timedelta(days=1),
        cadence="nightly",
        legs=legs(),
        coverage=(),
        next_cursor="next",
    )

    class Transport:
        def fetch(self, cursor):
            return page.model_dump_json().encode()

    result = collect_flights(
        source,
        Mode.FIXTURE,
        Transport(),
        LocalObjectStore(tmp_path),
        lambda: T + timedelta(days=2),
        RequestBudget(source),
    )
    assert result.status == "incomplete"  # Repeated page token cannot run forever.
    assert result.reason == "ValueError"

    class Broken:
        def fetch(self, cursor):
            raise OSError("secret-provider-detail")

    failed = collect_flights(
        source,
        Mode.FIXTURE,
        Broken(),
        LocalObjectStore(tmp_path),
        lambda: T + timedelta(days=2),
        RequestBudget(source),
    )
    assert failed.status == "incomplete" and not failed.legs
    assert "secret" not in failed.model_dump_json()
