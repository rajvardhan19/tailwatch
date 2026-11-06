"""Archive FAA observations through the immutable evidence publication boundary."""

from datetime import datetime

from sqlalchemy import text

from tailwatch.db.evidence import EvidenceRepository, Fact
from tailwatch.sources.objects import ObjectStore
from tailwatch.sources.registry import PARSER_VERSION, RegistryArchive, parse_master


def append_registry(repo: EvidenceRepository, store: ObjectStore, raw: bytes, fact: Fact) -> None:
    archive = parse_master(
        raw,
        source_id=fact.source_id,
        observed_at=fact.valid_from,
        available_at=fact.processed_at,
    )
    manifest = {
        "kind": "faa_registry",
        "observed_at": archive.observed_at.isoformat(),
        "row_count": archive.row_count,
        "accepted_count": len(archive.records),
        "quarantined_count": len(archive.quarantined),
    }
    if (
        fact.digest != archive.digest
        or fact.parser_version != PARSER_VERSION
        or fact.logical_key != f"registry:{archive.observed_at.isoformat()}"
        or fact.payload != manifest
        or fact.valid_to is not None
        or fact.evidence_locator != "MASTER.txt"
        or fact.valid_from > fact.ingested_at
    ):
        raise ValueError("registry fact does not match raw archive and manifest")
    repo.capture(store, raw)
    repo.append(fact)


def known_archives(
    repo: EvidenceRepository,
    store: ObjectStore,
    *,
    source_id: str,
    cutoff: datetime,
) -> list[RegistryArchive]:
    archives = []
    for fact in repo.as_of(source_id=source_id, cutoff=cutoff, effective_at=cutoff):
        if fact.payload.get("kind") != "faa_registry":
            continue
        if fact.parser_version != PARSER_VERSION:
            raise ValueError("registry parser version unavailable")
        known_at = repo.connection.execute(
            text(
                "SELECT greatest(published_at, recorded_at) FROM snapshot_publications "
                "WHERE snapshot_id=:id"
            ),
            {"id": fact.snapshot_id},
        ).scalar_one()
        archive = parse_master(
            store.get(fact.digest),
            source_id=source_id,
            observed_at=fact.valid_from,
            available_at=max(fact.processed_at, known_at),
        )
        archives.append(archive)
    return archives
