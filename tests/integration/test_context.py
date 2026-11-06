import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import Connection

from tailwatch.context.events import ContextEvent, append_context, known_events
from tailwatch.db.evidence import EvidenceRepository, Fact
from tailwatch.entities.repository import EntityRepository
from tailwatch.sources.objects import LocalObjectStore

pytestmark = pytest.mark.integration


def test_future_schedule_correction_preserves_earlier_knowledge(connection: Connection, tmp_path):
    repo = EvidenceRepository(connection)
    store = LocalObjectStore(tmp_path)
    source = "context-fixture"
    repo.register_source(source)
    company, event_id = uuid4(), uuid4()
    EntityRepository(connection).register(company, "company")
    available = datetime(2026, 1, 1, tzinfo=UTC)

    def put(event_day, previous=None):
        raw = f"Synthetic earnings announcement scheduled January {event_day}".encode()
        digest = hashlib.sha256(raw).hexdigest()
        event = ContextEvent(
            event_id=event_id,
            kind="earnings",
            company_id=company,
            start=datetime(2026, 1, event_day, 16, tzinfo=UTC),
            end=datetime(2026, 1, event_day, 17, tzinfo=UTC),
            available_at=available,
            certainty="confirmed",
            source_digest=digest,
            evidence_text=raw.decode(),
            timezone="UTC",
        )
        batch = uuid4()
        repo.begin_snapshot(
            snapshot_id=batch,
            source_id=source,
            created_at=available,
            watermark=available,
            expected_records=1,
            manifest_hash=digest,
        )
        item = Fact(
            fact_id=uuid4(),
            source_id=source,
            snapshot_id=batch,
            logical_key=f"context:{event_id}",
            revision=2 if previous else 1,
            supersedes=previous.fact_id if previous else None,
            digest=digest,
            evidence_locator="body",
            parser_version="fixture-v1",
            ingested_at=available,
            processed_at=available,
            valid_from=available,
            payload=event.model_dump(mode="json"),
        )
        append_context(repo, store, raw, item)
        repo.publish(batch, available)
        return item

    original = put(15)
    cutoff = datetime.now(UTC)
    assert known_events(repo, source, cutoff)[0].start.day == 15
    put(20, original)
    assert known_events(repo, source, cutoff)[0].start.day == 15
    assert known_events(repo, source, datetime.now(UTC))[0].start.day == 20
