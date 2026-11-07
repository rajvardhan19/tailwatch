from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tailwatch.domain.time import Availability
from tailwatch.features.runs import PairFeatures
from tailwatch.features.signals import Signal
from tailwatch.research.datasets import Example, matched_baselines
from tailwatch.research.evaluation import Prediction, evaluate
from tailwatch.research.experiments import Experiment
from tailwatch.research.labels import Announcement, Deal, Label, label_pair
from tailwatch.research.models import fit_candidate
from tailwatch.research.returns import PriceSession, executable_return


def dt(year):
    return datetime(year, 1, 1, tzinfo=UTC)


def experiment(synthetic=True):
    return Experiment(
        name="synthetic",
        universe_digest="0" * 64,
        feature_version="v1",
        train={"start": dt(2020), "end": dt(2020) + timedelta(days=10)},
        calibration={"start": dt(2021), "end": dt(2021) + timedelta(days=10)},
        threshold={"start": dt(2022), "end": dt(2022) + timedelta(days=10)},
        test={"start": dt(2023), "end": dt(2023) + timedelta(days=150)},
        minimum_alerts=10,
        minimum_deals=5,
        alert_budget=10,
        precision_target=0.5,
        lift_target=2,
        seed=7,
        bootstrap_draws=200,
        synthetic=synthetic,
        rationale="Synthetic validation only",
    )


def test_labels_mature_positives_and_earliest_definitive_release():
    a, b = uuid4(), uuid4()
    deal = Deal(
        deal_id=uuid4(),
        acquirer=a,
        target=b,
        taxonomy="control",
        announcements=(
            Announcement(
                published_at=dt(2023) + timedelta(days=50),
                known_at=dt(2023) + timedelta(days=51),
                evidence="release",
                definitive=True,
            ),
            Announcement(
                published_at=dt(2023) + timedelta(days=52),
                known_at=dt(2023) + timedelta(days=52),
                evidence="filing",
                definitive=True,
            ),
        ),
    )
    assert (
        label_pair(
            a,
            b,
            dt(2023),
            as_of=dt(2023) + timedelta(days=90),
            covered_until=dt(2024),
            deals=[deal],
            coverage_complete=True,
        ).state
        == "pending"
    )
    mature = label_pair(
        a, b, dt(2023), as_of=dt(2024), covered_until=dt(2024), deals=[deal], coverage_complete=True
    )
    assert (
        mature.state == "positive" and mature.announcement_at == deal.announcements[0].published_at
    )
    assert (
        label_pair(
            b,
            a,
            dt(2023),
            as_of=dt(2024),
            covered_until=dt(2024),
            deals=[deal],
            coverage_complete=True,
        ).state
        == "negative"
    )
    assert (
        label_pair(
            a,
            b,
            dt(2023) + timedelta(days=60),
            as_of=dt(2024),
            covered_until=dt(2024),
            deals=[deal],
            coverage_complete=True,
        ).state
        == "excluded"
    )


def test_embargoes_reject_overlapping_followup():
    payload = experiment().model_dump()
    payload["calibration"]["start"] = dt(2020) + timedelta(days=20)
    with pytest.raises(ValueError, match="label windows"):
        Experiment.model_validate(payload)


def test_model_training_calibration_and_signed_contributions():
    a, b = uuid4(), uuid4()
    examples = []
    for year in (2020, 2021):
        for i, x in enumerate((-2.0, -1.0, 1.0, 2.0)):
            at = dt(year) + timedelta(days=i)
            examples.append(
                Example(
                    episode_id=uuid4(),
                    at=at,
                    features_known_at=at,
                    pair=PairFeatures(
                        visitor=a,
                        host=b,
                        features={
                            "flight": Signal(status=Availability.OBSERVED, value=x),
                            "buying": Signal(status=Availability.OBSERVED, value=-x),
                        },
                    ),
                    label=Label(
                        state="positive" if x > 0 else "negative", known_at=at + timedelta(days=180)
                    ),
                )
            )
    model = fit_candidate(examples, experiment(), ("flight", "buying"), dt(2022))
    logit, terms = model.explain({"flight": 1.0, "buying": -1.0})
    assert logit == pytest.approx(model.intercept + sum(terms.values()))
    assert model.coefficients[1] < 0
    assert model.predict({"flight": 1.0, "buying": -1.0}) > 0.5
    baselines = matched_baselines(examples, seed=1, budget=3)
    assert baselines == matched_baselines(examples, seed=1, budget=3)
    assert all(len(v) == 3 for v in baselines.values())


def test_repeated_daily_alerts_and_veto_everything_cannot_pass():
    episode, deal = uuid4(), uuid4()
    rows = [
        Prediction(
            episode_id=episode,
            at=dt(2023) + timedelta(days=i),
            probability=0.9,
            positive=True,
            deal_id=deal,
            announcement_at=dt(2023) + timedelta(days=120),
            label_known_at=dt(2025),
        )
        for i in range(100)
    ]
    result = evaluate(
        rows,
        experiment(False),
        threshold=0.5,
        baseline_precision=0.1,
        eligible_deals=10,
        as_of=dt(2025),
    )
    assert result.alerts == result.successes == 1 and result.decision == "INCONCLUSIVE"
    veto = evaluate(
        rows,
        experiment(False),
        threshold=1,
        baseline_precision=0.1,
        eligible_deals=10,
        as_of=dt(2025),
    )
    assert veto.alerts == 0 and veto.precision is None and veto.decision == "INCONCLUSIVE"


def test_executable_entry_skips_prior_open_and_halts():
    sessions = [
        PriceSession(
            opened_at=dt(2023) + timedelta(days=i, hours=14),
            closed_at=dt(2023) + timedelta(days=i, hours=21),
            available_at=dt(2024),
            adjusted_open=100,
            adjusted_close=110,
            benchmark_open=100,
            benchmark_close=105,
            tradable=i != 1,
        )
        for i in range(10)
    ]
    calendar = tuple(s.opened_at for s in sessions)
    result = executable_return(
        sessions, dt(2023) + timedelta(hours=15), dt(2024), 5, expected_opens=calendar
    )
    assert (
        executable_return(
            sessions[:3] + sessions[4:], dt(2023), dt(2024), 5, expected_opens=calendar
        )["reason"]
        == "missing_price_session"
    )
    assert (
        executable_return(sessions, dt(2023), dt(2024), 5)["reason"] == "missing_session_calendar"
    )
    assert result["entry"] == sessions[2].opened_at.isoformat()
    assert result["excess_return"] == pytest.approx(0.05)
    assert executable_return(sessions, dt(2023), dt(2024), 30)["status"] == "unavailable"


def test_model_artifacts_reject_invalid_parameters_and_overflow():
    from tailwatch.research.models import LogisticArtifact

    fields = dict(
        experiment_hash="fixture",
        feature_names=("x",),
        means=(0.0,),
        scales=(1.0,),
        coefficients=(1.0,),
        intercept=0.0,
        fitted_at=dt(2024),
        l2=1.0,
        calibration=(1.0, 0.0),
        synthetic=True,
    )
    for change in (
        {"scales": (0.0,)},
        {"coefficients": (float("nan"),)},
        {"means": ()},
        {"calibration": (float("inf"), 0.0)},
    ):
        with pytest.raises(ValueError):
            LogisticArtifact(**(fields | change))
    model = LogisticArtifact(**(fields | {"coefficients": (1e308,)}))
    with pytest.raises(ValueError, match="overflow"):
        model.predict({"x": 1e308})


def test_embedding_overflow_never_becomes_neutral_similarity():
    from tailwatch.features.text_finance import Embedding, semantic_similarity

    embedding = Embedding(
        document_id="synthetic",
        chunk_id="1",
        revision="v1",
        published_at=dt(2020),
        available_at=dt(2020),
        expires_at=dt(2025),
        vector=(1e308, 1e308),
        evidence="fixture",
    )
    assert semantic_similarity([embedding], [embedding], dt(2024)).status == Availability.AMBIGUOUS
