from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from tailwatch.features.runs import PairFeatures
from tailwatch.features.signals import Signal
from tailwatch.research.datasets import Example, freeze_episodes, matched_baselines
from tailwatch.research.diagnostics import cohort_coverage, fit_ablations, grouped_uncertainty
from tailwatch.research.experiments import Experiment
from tailwatch.research.labels import Label


def fixture():
    def dt(year):
        return datetime(year, 1, 1, tzinfo=UTC)

    exp = Experiment(
        name="fixture",
        universe_digest="0" * 64,
        feature_version="v1",
        **{
            name: {"start": dt(year), "end": dt(year) + timedelta(days=10)}
            for name, year in zip(
                ("train", "calibration", "threshold", "test"), range(2020, 2024), strict=True
            )
        },
        minimum_alerts=10,
        minimum_deals=5,
        alert_budget=10,
        precision_target=0.5,
        lift_target=2,
        seed=42,
        bootstrap_draws=200,
        synthetic=True,
        rationale="test only",
    )
    rows = []
    for year in (2020, 2021):
        for i, value in enumerate((-2.0, -1.0, 1.0, 2.0)):
            at = dt(year) + timedelta(days=i)
            rows.append(
                Example(
                    episode_id=UUID(int=year * 10 + i),
                    at=at,
                    features_known_at=at,
                    pair=PairFeatures(
                        visitor=UUID(int=1),
                        host=UUID(int=2),
                        features={
                            "flight": Signal(status="observed", value=value),
                            "buying": Signal(status="observed", value=-value),
                        },
                    ),
                    label=Label(
                        state="positive" if value > 0 else "negative",
                        deal_id=UUID(int=year) if value > 0 else None,
                        known_at=at + timedelta(days=180),
                    ),
                )
            )
    return exp, rows, dt(2022)


def test_baseline_freezes_first_observation_and_rejects_tied_conflicts():
    _, rows, _ = fixture()
    first = rows[0]
    later = first.model_copy(update={"at": first.at + timedelta(days=1), "pair": rows[-1].pair})
    assert matched_baselines(rows + [later], seed=1, budget=3) == matched_baselines(
        rows, seed=1, budget=3
    )
    with pytest.raises(ValueError, match="conflicting"):
        freeze_episodes([first, first.model_copy(update={"pair": rows[-1].pair})])


def test_coverage_retains_missing_universe_and_ablations_fit_one_cohort():
    exp, rows, at = fixture()
    expected = {r.episode_id for r in rows} | {UUID(int=999)}
    result = cohort_coverage(
        rows,
        expected,
        start=exp.train.start,
        end=exp.calibration.end,
        as_of=at,
        required=("flight", "buying"),
    )
    assert result["missing_episodes"] == 1 and result["eligible_fraction"] == 8 / 9
    variants = {"full": ("flight", "buying"), "flight": ("flight",), "insider": ("buying",)}
    result = fit_ablations(rows, exp, variants, at)
    assert all(v["status"] == "fitted" for v in result["variants"].values())
    assert result["promotion_allowed"] is False
    with pytest.raises(ValueError, match="before test"):
        fit_ablations(rows, exp, variants, exp.test.start)


def test_grouped_bootstrap_records_failures_and_is_deterministic():
    exp, rows, at = fixture()
    result = grouped_uncertainty(rows, exp, ("flight",), at, {"flight": 1.0})
    assert result == grouped_uncertainty(
        list(reversed(rows)), exp, ("flight",), at, {"flight": 1.0}
    )
    assert result.successful_draws + sum(result.failures.values()) == 200
    assert result.failures["one_class"] > 0
    assert result.promotion_allowed is False
    assert result.status == "inconclusive" and result.probability_interval is None
