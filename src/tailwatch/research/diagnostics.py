"""Reproducible development diagnostics; these reports cannot promote a model."""

import random
from collections import Counter
from datetime import datetime
from typing import Literal
from uuid import UUID

from tailwatch.research.datasets import Example, eligible, freeze_episodes
from tailwatch.research.experiments import Experiment
from tailwatch.research.models import fit_candidate
from tailwatch.sources.policy import Contract


class UncertaintyReport(Contract):
    status: Literal["estimated", "inconclusive"]
    grouping: Literal["deal_or_episode"] = "deal_or_episode"
    draws: int
    successful_draws: int
    failures: dict[str, int]
    coefficient_intervals: dict[str, tuple[float, float]]
    probability_interval: tuple[float, float] | None
    promotion_allowed: Literal[False] = False


def _interval(values: list[float]) -> tuple[float, float]:
    ordered = sorted(values)
    return ordered[int(0.025 * (len(values) - 1))], ordered[int(0.975 * (len(values) - 1))]


def grouped_uncertainty(
    examples: list[Example],
    experiment: Experiment,
    features: tuple[str, ...],
    fitted_at: datetime,
    reference: dict[str, float],
    *,
    l2: float = 1.0,
) -> UncertaintyReport:
    """Refit preprocessing, model and calibration on clustered resamples.

    Resample train/calibration separately, retaining every episode for a sampled
    deal. Coefficients are converted to original feature units before intervals.
    Test rows never participate. Failed draws are counted, not replaced.
    """
    if experiment.bootstrap_draws > 2000:
        raise ValueError("offline bootstrap limit is 2000 draws")
    frozen = freeze_episodes(examples)
    groups = []
    for partition, cutoff in (
        (experiment.train, experiment.calibration.start),
        (experiment.calibration, experiment.threshold.start),
    ):
        assert partition.end is not None
        rows = eligible(
            frozen, start=partition.start, end=partition.end, fit_at=cutoff, required=features
        )
        clustered: dict[str, list[Example]] = {}
        for row in rows:
            key = (
                "deal:" + str(row.label.deal_id)
                if row.label.deal_id
                else "episode:" + str(row.episode_id)
            )
            clustered.setdefault(key, []).append(row)
        groups.append([clustered[key] for key in sorted(clustered)])
    rng = random.Random(experiment.seed)
    coefficients: dict[str, list[float]] = {name: [] for name in features}
    probabilities: list[float] = []
    failures: Counter[str] = Counter()
    # Validate caller settings before counting stochastic failures.
    fit_candidate(frozen, experiment, features, fitted_at, l2=l2).predict(reference)
    for _ in range(experiment.bootstrap_draws):
        sample = [row for partition in groups for _ in partition for row in rng.choice(partition)]
        try:
            model = fit_candidate(sample, experiment, features, fitted_at, l2=l2)
            probability = model.predict(reference)
        except (ValueError, OverflowError) as error:
            reason = "one_class" if "two observed classes" in str(error) else "fit_failure"
            failures[reason] += 1
            continue
        probabilities.append(probability)
        for name, coefficient, scale in zip(
            features, model.coefficients, model.scales, strict=True
        ):
            coefficients[name].append(coefficient / scale)
    enough = len(probabilities) >= max(100, int(0.8 * experiment.bootstrap_draws))
    return UncertaintyReport(
        status="estimated" if enough else "inconclusive",
        draws=experiment.bootstrap_draws,
        successful_draws=len(probabilities),
        failures=dict(failures),
        coefficient_intervals={name: _interval(values) for name, values in coefficients.items()}
        if enough
        else {},
        probability_interval=_interval(probabilities) if enough else None,
    )


def cohort_coverage(
    examples: list[Example],
    expected_episodes: set[UUID],
    *,
    start: datetime,
    end: datetime,
    as_of: datetime,
    required: tuple[str, ...],
) -> dict[str, object]:
    frozen = freeze_episodes(examples)
    present = {e.episode_id for e in frozen}
    if present - expected_episodes:
        raise ValueError("examples outside frozen universe")
    accepted = eligible(frozen, start=start, end=end, fit_at=as_of, required=required)
    return {
        "expected_episodes": len(expected_episodes),
        "provided_episodes": len(present),
        "missing_episodes": len(expected_episodes - present),
        "eligible_episodes": len(accepted),
        "eligible_fraction": len(accepted) / len(expected_episodes) if expected_episodes else None,
        "excluded_or_unscorable": len(present) - len(accepted),
    }


def fit_ablations(
    examples: list[Example],
    experiment: Experiment,
    variants: dict[str, tuple[str, ...]],
    fitted_at: datetime,
) -> dict[str, object]:
    """Fit declared feature subsets on one complete-feature development cohort."""
    if not variants or any(not features for features in variants.values()):
        raise ValueError("explicit nonempty ablations required")
    if fitted_at >= experiment.test.start:
        raise ValueError("ablations must be fixed before test starts")
    required = tuple(sorted({f for features in variants.values() for f in features}))
    frozen = freeze_episodes(examples)
    cohort = [row for row in frozen if row.pair.scorable(required)]
    output: dict[str, object] = {}
    for name, features in sorted(variants.items()):
        try:
            model = fit_candidate(cohort, experiment, features, fitted_at)
            output[name] = {"status": "fitted", "artifact": model.model_dump(mode="json")}
        except (ValueError, OverflowError) as error:
            output[name] = {"status": "unavailable", "reason": str(error)}
    return {"promotion_allowed": False, "common_features": required, "variants": output}
