import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tailwatch.db.evidence import Fact
from tailwatch.entities.aircraft_control import ControlReview, LinkEvidence, resolve_control
from tailwatch.entities.aircraft_control_store import append_review, known_reviews
from tailwatch.entities.repository import EntityRepository
from tailwatch.sources.objects import LocalObjectStore
from tailwatch.sources.registry import parse_master

pytestmark = pytest.mark.integration


def test_review_evidence_binding_rejection_revision_and_operational_cutoff(connection, tmp_path):
    entities = EntityRepository(connection)
    repo = entities.evidence
    store = LocalObjectStore(tmp_path)
    source = f"review-{uuid4()}"
    company, airframe = uuid4(), uuid4()
    entities.register(company, "company")
    repo.register_source(source)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    raw = (
        b"N-NUMBER,SERIAL NUMBER,MFR MDL CODE,TYPE REGISTRANT,NAME,STATUS CODE,"
        b"FRACT OWNER,MODE S CODE HEX\n123AB,SYN-1,1234567,3,SYNTHETIC,V,,ABC123\n"
    )
    archive = parse_master(raw, source_id="fixture", observed_at=start, available_at=start)
    repo.capture(store, raw)
    document = b"Synthetic Company controls synthetic airframe."
    digest = hashlib.sha256(document).hexdigest()
    evidence = LinkEvidence(
        start=start,
        digest=digest,
        locator="paragraph 1",
        quote=document.decode(),
        available_at=start,
        kind="controller_document",
    )
    review = ControlReview(
        start=start,
        airframe_id=airframe,
        company_id=company,
        role="controller",
        decision="approved",
        registry_digest=archive.digest,
        registry_row=2,
        airframe_candidate_key="faa:1234567:SYN-1",
        reviewed_at=start,
        reviewer="synthetic-reviewer",
        rationale="Checked",
        evidence=[evidence],
        privacy="cleared",
    )

    def stage(value, previous=None):
        body = value.model_dump_json().encode()
        batch = uuid4()
        repo.begin_snapshot(
            snapshot_id=batch,
            source_id=source,
            created_at=start,
            watermark=start,
            expected_records=1,
            manifest_hash=hashlib.sha256(body).hexdigest(),
        )
        return Fact(
            fact_id=uuid4(),
            snapshot_id=batch,
            source_id=source,
            logical_key=f"aircraft-link:{airframe}:{company}:controller",
            revision=previous.revision + 1 if previous else 1,
            supersedes=previous.fact_id if previous else None,
            digest=hashlib.sha256(body).hexdigest(),
            parser_version="aircraft-control-v1",
            evidence_locator="review",
            ingested_at=start,
            processed_at=start,
            approved_at=value.reviewed_at,
            valid_from=value.start,
            valid_to=value.end,
            payload=value.model_dump(mode="json"),
        )

    first = stage(review)
    with pytest.raises(ValueError, match="document"):
        append_review(repo, store, first, archive, {digest: b"altered"})
    append_review(repo, store, first, archive, {digest: document})
    before = datetime.now(UTC)
    assert known_reviews(repo, source_id=source, cutoff=before, effective_at=start) == []
    repo.publish(first.snapshot_id, start)
    append_review(repo, store, first, archive, {digest: document})
    after = datetime.now(UTC)
    initial = known_reviews(repo, source_id=source, cutoff=after, effective_at=start)
    assert initial[0].known_at > before
    assert (
        resolve_control(
            initial,
            archive,
            airframe_id=airframe,
            role="controller",
            cutoff=before,
            effective_at=start,
            max_registry_age=timedelta(days=1),
        ).state
        == "unresolved"
    )
    rejected = ControlReview.model_validate(review.model_dump() | {"decision": "rejected"})
    second = stage(rejected, first)
    append_review(repo, store, second, archive, {digest: document})
    repo.publish(second.snapshot_id, start)
    assert known_reviews(repo, source_id=source, cutoff=after, effective_at=start) == initial
    current = known_reviews(repo, source_id=source, cutoff=datetime.now(UTC), effective_at=start)
    assert len(current) == 1
    assert current[0].review.decision == "rejected"
    assert (
        resolve_control(
            current,
            archive,
            airframe_id=airframe,
            role="controller",
            cutoff=datetime.now(UTC),
            effective_at=start,
            max_registry_age=timedelta(days=1),
        ).state
        == "unresolved"
    )
