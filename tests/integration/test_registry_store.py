import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tailwatch.db.clock import database_now
from tailwatch.db.evidence import EvidenceRepository, Fact
from tailwatch.sources.objects import LocalObjectStore
from tailwatch.sources.registry import lookup
from tailwatch.sources.registry_store import append_registry, known_archives

pytestmark = pytest.mark.integration


def test_archive_publication_raw_integrity_and_prior_cutoff(connection, tmp_path):
    repo = EvidenceRepository(connection)
    store = LocalObjectStore(tmp_path)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    source = f"synthetic-{uuid4()}"
    batch = uuid4()
    raw = (
        b"N-NUMBER,SERIAL NUMBER,MFR MDL CODE,TYPE REGISTRANT,NAME,STATUS CODE,"
        b"FRACT OWNER,MODE S CODE HEX\n123AB,SYN-1,1234567,3,SYNTHETIC,V,,ABC123\n"
    )
    digest = hashlib.sha256(raw).hexdigest()
    repo.register_source(source)
    repo.begin_snapshot(
        snapshot_id=batch,
        source_id=source,
        created_at=start,
        watermark=start,
        expected_records=1,
        manifest_hash=digest,
    )
    fact = Fact(
        fact_id=uuid4(),
        source_id=source,
        snapshot_id=batch,
        logical_key=f"registry:{start.isoformat()}",
        revision=1,
        digest=digest,
        evidence_locator="MASTER.txt",
        parser_version="faa-master-v1",
        ingested_at=start,
        processed_at=start,
        valid_from=start,
        payload={
            "kind": "faa_registry",
            "observed_at": start.isoformat(),
            "row_count": 1,
            "accepted_count": 1,
            "quarantined_count": 0,
        },
    )
    append_registry(repo, store, raw, fact)
    before = database_now(connection)
    assert known_archives(repo, store, source_id=source, cutoff=before) == []
    repo.publish(batch, start)
    append_registry(repo, store, raw, fact)  # Exact retry after publication.
    assert known_archives(repo, store, source_id=source, cutoff=before) == []
    archives = known_archives(repo, store, source_id=source, cutoff=database_now(connection))
    assert archives[0].records[0].tail == "N123AB"
    assert archives[0].available_at > before
    from datetime import timedelta

    assert (
        lookup(
            archives,
            source_id=source,
            alias="N123AB",
            alias_kind="tail",
            cutoff=before,
            effective_at=start,
            max_age=timedelta(days=1),
        ).state
        == "missing_source"
    )
    with pytest.raises(ValueError, match="manifest"):
        append_registry(repo, store, raw + b"broken\n", fact)
