import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tailwatch.research.evaluation import evaluate_sealed, register_experiment, seal_evaluation
from tailwatch.research.experiments import Experiment

pytestmark = pytest.mark.integration


def test_test_access_is_durable_single_use_and_synthetic_cannot_pass(database):
    def partition(year):
        at = datetime(year, 1, 1, tzinfo=UTC)
        return {"start": at, "end": at + timedelta(days=10)}

    exp = Experiment(
        name=str(uuid4()),
        universe_digest="0" * 64,
        feature_version="v1",
        train=partition(2020),
        calibration=partition(2021),
        threshold=partition(2022),
        test=partition(2023),
        minimum_alerts=10,
        minimum_deals=5,
        alert_budget=10,
        precision_target=0.5,
        lift_target=2,
        seed=1,
        bootstrap_draws=200,
        synthetic=True,
        rationale="synthetic fixture",
    )
    register_experiment(database, exp)
    # Empty dataset commitment is global; clean up is deliberately not allowed.
    digest = hashlib.sha256(b"[]").hexdigest()
    seal_evaluation(
        database,
        exp,
        dataset_hash=digest,
        model_hash="synthetic",
        threshold=0.5,
        baseline_precision=0.1,
        eligible_deals=0,
    )
    kwargs = dict(
        model_hash="synthetic",
        dataset_hash=digest,
        threshold=0.5,
        baseline_precision=0.1,
        eligible_deals=0,
        as_of=datetime(2025, 1, 1, tzinfo=UTC),
    )
    assert evaluate_sealed(database, exp, [], **kwargs).decision == "INCONCLUSIVE"
    with pytest.raises(IntegrityError):
        evaluate_sealed(database, exp, [], **kwargs)
    with database.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM sealed_test_access WHERE dataset_hash=:id"),
                {"id": digest},
            ).scalar_one()
            == 1
        )


def test_sealed_scores_are_recomputed_from_committed_model_and_features(database):
    """Fabricated CI evidence verifies plumbing, not predictive performance."""
    import json

    from tailwatch.features.runs import PairFeatures
    from tailwatch.features.signals import Signal
    from tailwatch.research.datasets import Example
    from tailwatch.research.evaluation import artifact_predictions, feature_commitment
    from tailwatch.research.labels import Label
    from tailwatch.research.models import LogisticArtifact

    def partition(year):
        at = datetime(year, 1, 1, tzinfo=UTC)
        return {"start": at, "end": at + timedelta(days=10)}

    exp = Experiment(
        name=str(uuid4()),
        universe_digest="0" * 64,
        feature_version="v1",
        train=partition(2020),
        calibration=partition(2021),
        threshold=partition(2022),
        test=partition(2023),
        minimum_alerts=1,
        minimum_deals=1,
        alert_budget=1,
        precision_target=0.01,
        lift_target=1.01,
        seed=1,
        bootstrap_draws=200,
        synthetic=False,
        rationale="fabricated CI only",
    )
    model = LogisticArtifact(
        experiment_hash=exp.digest(),
        feature_names=("flight",),
        means=(0.0,),
        scales=(1.0,),
        coefficients=(1.0,),
        intercept=0.0,
        fitted_at=exp.threshold.start,
        l2=1.0,
        calibration=(1.0, 0.0),
        synthetic=False,
    )
    at = exp.test.start
    row = Example(
        episode_id=uuid4(),
        at=at,
        features_known_at=at,
        pair=PairFeatures(
            visitor=uuid4(), host=uuid4(), features={"flight": Signal(status="observed", value=1.0)}
        ),
        label=Label(
            state="positive",
            deal_id=uuid4(),
            announcement_at=at + timedelta(days=30),
            known_at=at + timedelta(days=180),
        ),
    )
    as_of = at + timedelta(days=365)
    predictions = artifact_predictions([row], model, exp, as_of)
    model_hash = hashlib.sha256(model.model_dump_json().encode()).hexdigest()
    digest = feature_commitment([row])
    register_experiment(database, exp)
    seal_evaluation(
        database,
        exp,
        dataset_hash=digest,
        model_hash=model_hash,
        threshold=0.5,
        baseline_precision=0.1,
        eligible_deals=1,
    )
    kwargs = dict(
        model_hash=model_hash,
        dataset_hash=digest,
        threshold=0.5,
        baseline_precision=0.1,
        eligible_deals=1,
        as_of=as_of,
        artifact=model,
        examples=[row],
    )
    with pytest.raises(ValueError, match="predictions"):
        evaluate_sealed(
            database, exp, [predictions[0].model_copy(update={"probability": 0.999})], **kwargs
        )
    result = evaluate_sealed(database, exp, predictions, **kwargs)
    assert result.provenance_verified and result.decision == "PASS"

    # Legacy score-only calls may produce diagnostics, never a promotable PASS.
    other = Experiment.model_validate(exp.model_dump() | {"name": str(uuid4())})
    p = predictions[0].model_copy(update={"episode_id": uuid4()})
    legacy_hash = hashlib.sha256(
        json.dumps(
            [
                (
                    str(p.episode_id),
                    p.at.isoformat(),
                    p.positive,
                    str(p.deal_id),
                    p.label_known_at.isoformat(),
                )
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    register_experiment(database, other)
    seal_evaluation(
        database,
        other,
        dataset_hash=legacy_hash,
        model_hash="unverified",
        threshold=0.5,
        baseline_precision=0.1,
        eligible_deals=1,
    )
    result = evaluate_sealed(
        database,
        other,
        [p],
        model_hash="unverified",
        dataset_hash=legacy_hash,
        threshold=0.5,
        baseline_precision=0.1,
        eligible_deals=1,
        as_of=as_of,
    )
    assert not result.provenance_verified and result.decision == "INCONCLUSIVE"
