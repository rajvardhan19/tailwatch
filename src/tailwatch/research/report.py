"""Validated offline development report input and retained output artifact."""

from uuid import UUID

from pydantic import AwareDatetime, Field

from tailwatch.research.datasets import Example
from tailwatch.research.diagnostics import cohort_coverage, fit_ablations, grouped_uncertainty
from tailwatch.research.experiments import Experiment
from tailwatch.sources.objects import ObjectStore
from tailwatch.sources.policy import Contract


class ReportInput(Contract):
    experiment: Experiment
    examples: tuple[Example, ...]
    expected_episodes: frozenset[UUID]
    features: tuple[str, ...] = Field(min_length=1)
    variants: dict[str, tuple[str, ...]]
    fitted_at: AwareDatetime
    reference: dict[str, float]


def build_report(request: ReportInput, store: ObjectStore) -> str:
    experiment = request.experiment
    if request.fitted_at >= experiment.test.start:
        raise ValueError("development report must be fixed before test starts")
    if any(row.at >= experiment.threshold.start for row in request.examples):
        raise ValueError("development report cannot inspect threshold or test observations")
    assert experiment.calibration.end is not None
    required = tuple(
        sorted(set(request.features) | {f for v in request.variants.values() for f in v})
    )
    coverage = cohort_coverage(
        list(request.examples),
        set(request.expected_episodes),
        start=experiment.train.start,
        end=experiment.calibration.end,
        as_of=request.fitted_at,
        required=required,
    )
    import json

    supplied = request.model_dump(mode="json")
    supplied["expected_episodes"] = sorted(supplied["expected_episodes"])
    input_hash = store.put(
        json.dumps(supplied, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    )
    report = {
        "input_hash": input_hash,
        "synthetic": experiment.synthetic,
        "promotion_allowed": False,
        "experiment_hash": experiment.digest(),
        "coverage": coverage,
        "uncertainty": grouped_uncertainty(
            list(request.examples),
            experiment,
            request.features,
            request.fitted_at,
            request.reference,
        ).model_dump(mode="json"),
        "ablations": fit_ablations(
            list(request.examples), experiment, request.variants, request.fitted_at
        ),
    }
    return store.put(
        json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    )
