from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tailwatch.db.evidence import EvidenceRepository, Fact
from tailwatch.domain.time import Availability
from tailwatch.features.runs import FeatureRun, PairFeatures, publish_feature_run
from tailwatch.features.signals import Signal
from tailwatch.sources.objects import LocalObjectStore

pytestmark = pytest.mark.integration


def test_feature_run_publication_is_atomic_and_exact_retry_safe(connection, tmp_path):
    repo, store = EvidenceRepository(connection), LocalObjectStore(tmp_path)
    a, b = uuid4(), uuid4()
    at = datetime(2026, 1, 1, tzinfo=UTC)
    run = FeatureRun(
        run_id=uuid4(),
        cutoff=at,
        code_version="synthetic-v1",
        config_digest="0" * 64,
        universe=(a, b),
        source_snapshots=(),
        input_fact_ids=(),
        required_features=("flight",),
        synthetic=True,
        pairs=tuple(
            PairFeatures(
                visitor=x, host=y, features={"flight": Signal(status=Availability.MISSING_SOURCE)}
            )
            for x, y in ((a, b), (b, a))
        ),
    )
    source, batch = f"fixture-{uuid4()}", uuid4()
    repo.register_source(source)
    repo.begin_snapshot(
        snapshot_id=batch,
        source_id=source,
        created_at=at,
        watermark=at,
        expected_records=1,
        manifest_hash=run.digest(),
    )
    fact = Fact(
        fact_id=uuid4(),
        source_id=source,
        snapshot_id=batch,
        logical_key=f"feature-run:{run.run_id}",
        revision=1,
        digest=run.digest(),
        evidence_locator="run",
        parser_version="feature-run-v1",
        ingested_at=at,
        processed_at=at,
        valid_from=at,
        payload=run.model_dump(mode="json"),
    )
    before = datetime.now(UTC)
    assert repo.as_of(source_id=source, cutoff=before, effective_at=at) == []
    publish_feature_run(repo, store, run, fact, at)
    publish_feature_run(repo, store, run, fact, at)
    assert repo.as_of(source_id=source, cutoff=before, effective_at=at) == []
    assert len(repo.as_of(source_id=source, cutoff=datetime.now(UTC), effective_at=at)) == 1
