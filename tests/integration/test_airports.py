import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tailwatch.db.evidence import Fact
from tailwatch.entities.airports import AirportHistory, append_airport, proximity_snapshot
from tailwatch.entities.history import OfficeHistory
from tailwatch.entities.repository import EntityRepository
from tailwatch.sources.objects import LocalObjectStore

pytestmark = pytest.mark.integration


def test_proximity_boundary_deduplication_and_correction_replay(connection, tmp_path):
    entities = EntityRepository(connection)
    repo = entities.evidence
    store = LocalObjectStore(tmp_path)
    source = f"synthetic-{uuid4()}"
    start = datetime(2026, 1, 1, tzinfo=UTC)
    company = uuid4()
    entities.register(company, "company")
    repo.register_source(source)

    def publish(history, previous=None):
        body = history.model_dump_json().encode()
        batch = uuid4()
        repo.begin_snapshot(
            snapshot_id=batch,
            source_id=source,
            created_at=start,
            watermark=start,
            expected_records=1,
            manifest_hash=hashlib.sha256(body).hexdigest(),
        )
        airport = isinstance(history, AirportHistory)
        fact = Fact(
            fact_id=uuid4(),
            snapshot_id=batch,
            source_id=source,
            logical_key=f"{'airport' if airport else 'entity'}:{history.entity_id}",
            revision=previous.revision + 1 if previous else 1,
            supersedes=previous.fact_id if previous else None,
            digest=repo.capture(store, body),
            evidence_locator="document",
            parser_version="airport-history-v1" if airport else "v1",
            ingested_at=start,
            processed_at=start,
            valid_from=history.periods[0].start,
            payload=history.model_dump(mode="json"),
        )
        if airport:
            append_airport(repo, store, body, fact)
        else:
            entities.append(fact)
        repo.publish(batch, start)
        return fact

    airport = AirportHistory(
        entity_id=uuid4(),
        provider="synthetic",
        provider_id="1",
        periods=[
            dict(
                start=start,
                name="Synthetic Airport",
                country="US",
                location=dict(latitude=0, longitude=0),
                facility_type="small_airport",
                aliases=[],
            )
        ],
    )
    first = publish(airport)
    for lon in (0.449, 0.449, 0.45):
        publish(
            OfficeHistory(
                entity_id=uuid4(),
                company_id=company,
                periods=[
                    dict(
                        start=start,
                        end=start + timedelta(days=10),
                        kind="hq",
                        latitude=0,
                        longitude=lon,
                        address="Synthetic Office",
                    )
                ],
            )
        )
    cutoff = datetime.now(UTC)

    def snapshot(at=cutoff):
        return proximity_snapshot(
            entities,
            airport_source=source,
            office_source=source,
            cutoff=at,
            effective_at=start,
            radius_km=50,
        )

    original = snapshot()
    assert len(original.links) == 2  # The third office is just outside 50 km.
    assert len(original.company_distances(airport.entity_id)) == 1
    assert original.links[0].end == start + timedelta(days=10)
    changed = airport.model_dump()
    changed["periods"][0]["location"]["longitude"] = 1
    publish(AirportHistory.model_validate(changed), first)
    assert snapshot().digest() == original.digest()
    current = snapshot(datetime.now(UTC))
    assert not current.links
    assert current.coverage == "inputs_present"
    assert current.digest() != original.digest()
    missing = proximity_snapshot(
        entities,
        airport_source="missing",
        office_source=source,
        cutoff=datetime.now(UTC),
        effective_at=start,
    )
    assert missing.coverage == "missing_source"
