from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tailwatch.entities.airports import (
    AirportHistory,
    Coordinates,
    ProximityLink,
    ProximitySnapshot,
    distance_km,
    parse_ourairports,
    resolve_alias,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
HEADER = (
    "id,ident,type,name,latitude_deg,longitude_deg,iso_country,"
    "icao_code,iata_code,gps_code,local_code\n"
)
ROW = "1,SYN-001,small_airport,Synthetic Airport,40,-74,US,,,SYN1,01\n"


def test_no_iata_and_local_code_collision_require_explicit_namespace_and_scope():
    histories = parse_ourairports(
        (HEADER + ROW + ROW.replace("1,SYN-001", "2,SYN-002").replace(",US,", ",CA,")).encode(),
        START,
    )
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_alias(histories, kind="local", code="01", effective_at=START)
    assert resolve_alias(histories, kind="local", code="01", effective_at=START, country="US")
    assert resolve_alias(histories, kind="iata", code="SYN1", effective_at=START) is None
    assert resolve_alias(histories, kind="ident", code="SYN-001", effective_at=START)
    assert (
        resolve_alias(histories, kind="local", code="01", effective_at=START - timedelta(seconds=1))
        is None
    )


def test_stable_identity_survives_code_change_and_period_end_is_exclusive():
    first = parse_ourairports((HEADER + ROW).encode(), START)[0]
    later = parse_ourairports((HEADER + ROW.replace("SYN-001", "NEW-001")).encode(), START)[0]
    assert first.entity_id == later.entity_id
    payload = first.model_dump()
    payload["periods"][0]["end"] = START + timedelta(days=1)
    history = AirportHistory.model_validate(payload)
    assert (
        resolve_alias(
            [history], kind="ident", code="SYN-001", effective_at=START + timedelta(days=1)
        )
        is None
    )


@pytest.mark.parametrize(
    "row",
    [ROW + ROW, ROW.replace("40,-74", "91,-74"), ROW.replace("40,-74", "nan,-74"), "1,short\n"],
)
def test_invalid_airport_import_is_not_silently_partial(row):
    with pytest.raises(ValueError):
        parse_ourairports((HEADER + row).encode(), START)


def test_wgs84_distances_units_coordinate_order_and_antimeridian():
    assert distance_km(
        Coordinates(latitude=0, longitude=0), Coordinates(latitude=0, longitude=1)
    ) == pytest.approx(111.319490793, abs=1e-8)
    assert distance_km(
        Coordinates(latitude=0, longitude=0), Coordinates(latitude=1, longitude=0)
    ) == pytest.approx(110.574388558, abs=1e-8)
    assert distance_km(
        Coordinates(latitude=0, longitude=179.9), Coordinates(latitude=0, longitude=-179.9)
    ) == pytest.approx(22.263898159, abs=1e-8)
    assert (
        distance_km(Coordinates(latitude=90, longitude=0), Coordinates(latitude=90, longitude=90))
        == 0
    )


def test_duplicate_offices_do_not_multiply_company_attribution():
    airport, company = uuid4(), uuid4()
    links = tuple(
        ProximityLink(
            start=START,
            airport_id=airport,
            company_id=company,
            office_id=uuid4(),
            distance_km=distance,
            airport_fact_id=uuid4(),
            office_fact_id=uuid4(),
        )
        for distance in (5, 5, 10)
    )
    snapshot = ProximitySnapshot(
        cutoff=START,
        effective_at=START,
        radius_km=50,
        coverage="inputs_present",
        input_fact_ids=(),
        links=links,
    )
    assert snapshot.company_distances(airport) == {company: 5}
    assert snapshot.digest() == snapshot.digest()
