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
