import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from tailwatch.db.clock import database_now
from tailwatch.db.evidence import EvidenceRepository, Fact
from tailwatch.domain.time import ReplayMode
from tailwatch.sources.objects import LocalObjectStore

pytestmark = pytest.mark.integration


def day(n: int) -> datetime:
    return datetime(2026, 1, n, tzinfo=UTC)


def snapshot(repo: EvidenceRepository, n: int, expected: int = 1) -> UUID:
    ident = uuid4()
    repo.register_source("synthetic")
    repo.begin_snapshot(
        snapshot_id=ident,
        source_id="synthetic",
        created_at=day(n),
        watermark=day(n),
        expected_records=expected,
        manifest_hash=hashlib.sha256(str(n).encode()).hexdigest(),
    )
    return ident


def fact(
    repo: EvidenceRepository,
    store: LocalObjectStore,
    batch: UUID,
    n: int,
    previous: Fact | None = None,
) -> Fact:
    return Fact(
        fact_id=uuid4(),
        source_id="synthetic",
        snapshot_id=batch,
        logical_key="company-office",
        revision=previous.revision + 1 if previous else 1,
        supersedes=previous.fact_id if previous else None,
        digest=repo.capture(store, f"fixture-{n}".encode()),
        evidence_locator="row:1",
        parser_version="fixture-v1",
        source_published_at=day(n),
        ingested_at=day(n),
        processed_at=day(n),
        valid_from=day(1),
        payload={"office": f"office-{n}"},
    )


def test_later_correction_cannot_change_earlier_operational_replay(
    connection: Connection,
    tmp_path: Path,
) -> None:
    repo = EvidenceRepository(connection)
    store = LocalObjectStore(tmp_path)
    first = fact(repo, store, snapshot(repo, 1), 1)
    repo.append(first)
    repo.publish(first.snapshot_id, day(2))
    cutoff = database_now(connection)
    before = repo.as_of(source_id="synthetic", cutoff=cutoff, effective_at=day(1))
    corrected = fact(repo, store, snapshot(repo, 5), 5, first)
    repo.append(corrected)
    repo.publish(corrected.snapshot_id, day(6))
    assert repo.as_of(source_id="synthetic", cutoff=cutoff, effective_at=day(1)) == before
    assert before == [first]
    assert repo.as_of(
        source_id="synthetic", cutoff=database_now(connection), effective_at=day(1)
    ) == [corrected]


def test_incomplete_failed_and_late_mutation_are_rejected(
    connection: Connection,
    tmp_path: Path,
) -> None:
    repo = EvidenceRepository(connection)
    first = fact(repo, LocalObjectStore(tmp_path), snapshot(repo, 1), 1)
    repo.append(first)
    repo.publish(first.snapshot_id, day(2))
    incomplete = snapshot(repo, 3)
    with pytest.raises(DBAPIError, match="incomplete"), connection.begin_nested():
        repo.publish(incomplete, day(4))
    assert repo.current_snapshot("synthetic", database_now(connection)) == first.snapshot_id
    failed = snapshot(repo, 4, expected=0)
    connection.execute(
        text("INSERT INTO parse_failures VALUES (:id, 'bad.xml', 'invalid XML')"), {"id": failed}
    )
    with pytest.raises(DBAPIError, match="failed"), connection.begin_nested():
        repo.publish(failed, day(5))
    with pytest.raises(DBAPIError, match="already published"), connection.begin_nested():
        connection.execute(
            text("INSERT INTO parse_failures VALUES (:id, 'late', 'late parse')"),
            {"id": first.snapshot_id},
        )


def test_reingestion_is_idempotent_and_changed_identity_fails(
    connection: Connection,
    tmp_path: Path,
) -> None:
    repo = EvidenceRepository(connection)
    store = LocalObjectStore(tmp_path)
    item = fact(repo, store, snapshot(repo, 1), 1)
    repo.append(item)
    repo.publish(item.snapshot_id, day(2))
    repo.append(item)
    repo.publish(item.snapshot_id, day(2))
    assert repo.capture(store, b"fixture-1") == item.digest
    assert connection.execute(text("SELECT count(*) FROM evidence_facts")).scalar_one() == 1
    with pytest.raises(ValueError, match="reused"):
        repo.append(Fact.model_validate(item.model_dump() | {"payload": {"office": "changed"}}))
    with pytest.raises(ValueError, match="cannot change"):
        repo.publish(item.snapshot_id, day(3))


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE evidence_facts SET payload='{}'",
        "DELETE FROM evidence_facts",
        "TRUNCATE evidence_facts CASCADE",
    ],
)
def test_database_enforces_append_only(
    connection: Connection,
    tmp_path: Path,
    sql: str,
) -> None:
    repo = EvidenceRepository(connection)
    repo.append(fact(repo, LocalObjectStore(tmp_path), snapshot(repo, 1), 1))
    with pytest.raises(DBAPIError, match="append-only"), connection.begin_nested():
        connection.execute(text(sql))


def test_public_simulation_is_explicit_and_unknown_publication_is_not_visible(
    connection: Connection,
    tmp_path: Path,
) -> None:
    repo = EvidenceRepository(connection)
    item = fact(repo, LocalObjectStore(tmp_path), snapshot(repo, 5), 5)
    item = Fact.model_validate(item.model_dump() | {"source_published_at": day(1)})
    repo.append(item)
    repo.publish(item.snapshot_id, day(6))
    assert repo.as_of(source_id="synthetic", cutoff=day(3), effective_at=day(1)) == []
    with pytest.raises(ValueError, match="latency"):
        repo.as_of(
            source_id="synthetic",
            cutoff=day(3),
            effective_at=day(1),
            mode=ReplayMode.PUBLIC_SIMULATION,
        )
    assert repo.as_of(
        source_id="synthetic",
        cutoff=day(3),
        effective_at=day(1),
        mode=ReplayMode.PUBLIC_SIMULATION,
        public_latency_seconds=0,
    ) == [item]
    assert (
        repo.as_of(
            source_id="synthetic",
            cutoff=day(3),
            effective_at=day(1),
            mode=ReplayMode.PUBLIC_SIMULATION,
            public_latency_seconds=3 * 86400,
        )
        == []
    )
    unknown = fact(repo, LocalObjectStore(tmp_path), snapshot(repo, 7), 7)
    unknown = Fact.model_validate(
        unknown.model_dump()
        | {
            "logical_key": "unknown-publication",
            "source_published_at": None,
        }
    )
    repo.append(unknown)
    repo.publish(unknown.snapshot_id, day(8))
    assert repo.as_of(
        source_id="synthetic",
        cutoff=day(9),
        effective_at=day(1),
        mode=ReplayMode.PUBLIC_SIMULATION,
        public_latency_seconds=0,
    ) == [item]


def test_corrected_validity_does_not_resurrect_old_revision(
    connection: Connection,
    tmp_path: Path,
) -> None:
    repo = EvidenceRepository(connection)
    store = LocalObjectStore(tmp_path)
    first = fact(repo, store, snapshot(repo, 1), 1)
    repo.append(first)
    repo.publish(first.snapshot_id, day(2))
    replacement = fact(repo, store, snapshot(repo, 5), 5, first)
    replacement = Fact.model_validate(replacement.model_dump() | {"valid_from": day(4)})
    repo.append(replacement)
    repo.publish(replacement.snapshot_id, day(6))
    assert (
        repo.as_of(source_id="synthetic", cutoff=database_now(connection), effective_at=day(1))
        == []
    )


def test_publication_cannot_precede_review(connection: Connection, tmp_path: Path) -> None:
    repo = EvidenceRepository(connection)
    item = fact(repo, LocalObjectStore(tmp_path), snapshot(repo, 1), 1)
    item = Fact.model_validate(item.model_dump() | {"approved_at": day(4)})
    repo.append(item)
    with pytest.raises(DBAPIError, match="predates"), connection.begin_nested():
        repo.publish(item.snapshot_id, day(2))


def test_revision_must_belong_to_same_logical_fact(connection: Connection, tmp_path: Path) -> None:
    repo = EvidenceRepository(connection)
    store = LocalObjectStore(tmp_path)
    first = fact(repo, store, snapshot(repo, 1), 1)
    repo.append(first)
    second = fact(repo, store, snapshot(repo, 5), 5, first)
    second = Fact.model_validate(second.model_dump() | {"logical_key": "different-company"})
    with pytest.raises(DBAPIError, match="revision chain"), connection.begin_nested():
        repo.append(second)
