"""Persist reviewed links with document hashes and operational publication time."""

import hashlib
from datetime import datetime

from sqlalchemy import text

from tailwatch.db.evidence import EvidenceRepository, Fact
from tailwatch.entities.aircraft_control import ControlReview, KnownReview
from tailwatch.sources.objects import ObjectStore
from tailwatch.sources.registry import RegistryArchive, parse_master


def append_review(
    repo: EvidenceRepository,
    store: ObjectStore,
    fact: Fact,
    archive: RegistryArchive,
    documents: dict[str, bytes],
) -> None:
    review = ControlReview.model_validate(fact.payload)
    retained = parse_master(
        store.get(archive.digest),
        source_id=archive.source_id,
        observed_at=archive.observed_at,
        available_at=archive.available_at,
    )
    if retained != archive:
        raise ValueError("registry differs from retained raw evidence")
    raw = review.model_dump_json().encode()
    if (
        fact.digest != hashlib.sha256(raw).hexdigest()
        or fact.logical_key
        != f"aircraft-link:{review.airframe_id}:{review.company_id}:{review.role}"
        or fact.parser_version != "aircraft-control-v1"
        or fact.evidence_locator != "review"
        or fact.valid_from != review.start
        or fact.valid_to != review.end
        or fact.approved_at != review.reviewed_at
        or archive.digest != review.registry_digest
        or archive.available_at > review.reviewed_at
    ):
        raise ValueError("review fact or registry binding mismatch")
    record = next((r for r in archive.records if r.row == review.registry_row), None)
    if (
        record is None
        or record.airframe_candidate_key != review.airframe_candidate_key
        or (review.decision == "approved" and not record.eligible_for_control_review)
    ):
        raise ValueError("unsupported registry identity")
    for company in (review.company_id, *review.alternatives):
        if (
            repo.connection.execute(
                text("SELECT 1 FROM entities WHERE entity_id=:id AND kind='company'"),
                {"id": company},
            ).first()
            is None
        ):
            raise ValueError("review references an unregistered company")
    for evidence in review.evidence:
        body = documents.get(evidence.digest)
        if body is None or hashlib.sha256(body).hexdigest() != evidence.digest:
            raise ValueError("missing or mismatched evidence document")
        if evidence.quote not in body.decode("utf-8"):
            raise ValueError("evidence quote absent from retained text")
    # Store only after all input checks, in the caller's transaction.
    for evidence in review.evidence:
        repo.capture(store, documents[evidence.digest])
    repo.capture(store, raw)
    repo.append(fact)


def known_reviews(
    repo: EvidenceRepository,
    *,
    source_id: str,
    cutoff: datetime,
    effective_at: datetime,
) -> list[KnownReview]:
    result = []
    for fact in repo.as_of(source_id=source_id, cutoff=cutoff, effective_at=effective_at):
        if not fact.logical_key.startswith("aircraft-link:"):
            continue
        if fact.parser_version != "aircraft-control-v1":
            raise ValueError("unsupported control review version")
        review = ControlReview.model_validate(fact.payload)
        published = repo.connection.execute(
            text(
                "SELECT greatest(published_at, recorded_at) FROM snapshot_publications "
                "WHERE snapshot_id=:id"
            ),
            {"id": fact.snapshot_id},
        ).scalar_one()
        result.append(
            KnownReview(
                review=review, known_at=max(published, review.reviewed_at), revision=fact.revision
            )
        )
    return result
