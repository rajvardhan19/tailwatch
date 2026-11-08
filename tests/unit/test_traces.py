import gzip
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from tailwatch.sources.objects import LocalObjectStore
from tailwatch.sources.traces import infer_legs, parse_trace

T = datetime(2026, 1, 1, tzinfo=UTC)
AVAILABLE = datetime(2026, 1, 2, tzinfo=UTC)


def trace(**change):
    return json.dumps(
        {
            "icao": "abcdef",
            "timestamp": T.timestamp(),
            "trace": [
                [0, 0, 0, "ground", 0, 0, 0, 0, None, "adsb_icao"],
                [60, 0, 0.01, 1000, 100, 0, 0, 0, None, "adsb_icao"],
                [120, 0, 0.02, "ground", 0, 0, 0, 0, None, "adsb_icao"],
            ],
            **change,
        }
    ).encode()


def infer(points, **kwargs):
    return infer_legs(
        points,
        source_id="fixture",
        airframe_id=UUID(int=1),
        airport_at=lambda p: UUID(int=2 if p.longitude == 0 else 3),
        cutoff=AVAILABLE,
        **kwargs,
    )


def test_gzip_archive_ground_air_ground_and_future_replay(tmp_path):
    store = LocalObjectStore(tmp_path)
    points = parse_trace(
        gzip.compress(trace()), approved_icao="abcdef", available_at=AVAILABLE, store=store
    )
    legs = infer(points)
    assert len(legs) == 1 and legs[0].timing == "inferred"
    assert legs[0].available_at == AVAILABLE
    assert store.get(points[0].evidence)[:2] == b"\x1f\x8b"
    assert (
        infer_legs(
            points,
            source_id="fixture",
            airframe_id=UUID(int=1),
            airport_at=lambda _: UUID(int=2),
            cutoff=T,
        )
        == ()
    )


@pytest.mark.parametrize("field,value", [(6, 1), (3, None), (9, "mlat"), (2, 170)])
def test_stale_unknown_low_quality_and_impossible_jump_break_leg(tmp_path, field, value):
    body = json.loads(trace())
    body["trace"][1][field] = value
    points = parse_trace(
        json.dumps(body).encode(),
        approved_icao="abcdef",
        available_at=AVAILABLE,
        store=LocalObjectStore(tmp_path),
    )
    assert infer(points) == ()


def test_gaps_ambiguous_airports_and_ground_only_never_become_flights(tmp_path):
    store = LocalObjectStore(tmp_path)
    points = parse_trace(trace(), approved_icao="abcdef", available_at=AVAILABLE, store=store)
    assert infer(points, max_gap_seconds=30) == ()
    assert (
        infer_legs(
            points,
            source_id="fixture",
            airframe_id=UUID(int=1),
            airport_at=lambda _: None,
            cutoff=AVAILABLE,
        )
        == ()
    )
    assert infer(tuple(p.model_copy(update={"ground": True}) for p in points)) == ()


def test_identity_privacy_and_conflicting_trace_rows_rejected(tmp_path):
    store = LocalObjectStore(tmp_path)
    for raw in (trace(icao="other"), trace(dbFlags=4), trace(dbFlags=8)):
        with pytest.raises(ValueError):
            parse_trace(raw, approved_icao="abcdef", available_at=AVAILABLE, store=store)
    body = json.loads(trace())
    body["trace"].append([0, 1, 1, "ground", 0, 0, 0, 0, None, "adsb_icao"])
    with pytest.raises(ValueError, match="conflicting"):
        parse_trace(
            json.dumps(body).encode(), approved_icao="abcdef", available_at=AVAILABLE, store=store
        )
