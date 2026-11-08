"""Small deterministic L2 logistic models; artifacts never self-activate."""

import math
from datetime import datetime

from pydantic import AwareDatetime, Field, model_validator

from tailwatch.research.datasets import Example, eligible
from tailwatch.research.experiments import Experiment
from tailwatch.sources.policy import Contract


def sigmoid(value: float) -> float:
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    exp = math.exp(value)
    return exp / (1 + exp)


class LogisticArtifact(Contract):
    experiment_hash: str
    feature_names: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    coefficients: tuple[float, ...]
    intercept: float
    fitted_at: AwareDatetime
    l2: float = Field(gt=0, allow_inf_nan=False)
    calibration: tuple[float, float] | None = None
    synthetic: bool

    @model_validator(mode="after")
    def valid_parameters(self) -> "LogisticArtifact":
        size = len(self.feature_names)
        values = (
            *self.means,
            *self.scales,
            *self.coefficients,
            self.intercept,
            *(self.calibration or ()),
        )
        if (
            not size
            or len(set(self.feature_names)) != size
            or any(len(v) != size for v in (self.means, self.scales, self.coefficients))
            or any(not math.isfinite(v) for v in values)
            or any(v <= 0 for v in self.scales)
        ):
            raise ValueError("invalid model parameters")
        return self

    def explain(self, values: dict[str, float]) -> tuple[float, dict[str, float]]:
        if set(values) != set(self.feature_names) or any(
            not math.isfinite(v) for v in values.values()
        ):
            raise ValueError("prediction schema mismatch")
        terms = {
            name: coef * (values[name] - mean) / scale
            for name, coef, mean, scale in zip(
                self.feature_names, self.coefficients, self.means, self.scales, strict=True
            )
        }
        logit = self.intercept + sum(terms.values())
        if not math.isfinite(logit) or any(not math.isfinite(v) for v in terms.values()):
            raise ValueError("prediction arithmetic overflow")
        return logit, terms

    def predict(self, values: dict[str, float]) -> float:
        logit, _ = self.explain(values)
        if self.calibration is None:
            raise ValueError("candidate is not calibrated")
        slope, intercept = self.calibration
        calibrated = slope * logit + intercept
        if not math.isfinite(calibrated):
            raise ValueError("calibration arithmetic overflow")
        return sigmoid(calibrated)


def _fit(
    matrix: list[list[float]],
    labels: list[int],
    weights: list[float],
    l2: float,
    max_iterations: int = 10000,
) -> tuple[list[float], float]:
    if not matrix or len(set(labels)) != 2:
        raise ValueError("fit requires two observed classes")
    dims = len(matrix[0])
    coefficients, intercept = [0.0] * dims, 0.0
    total = sum(weights)
    # Lipschitz bound for logistic gradient; deterministic full-batch descent.
    step = 1 / (
        0.25
        * sum(w * (1 + sum(v * v for v in row)) for row, w in zip(matrix, weights, strict=True))
        / total
        + l2
    )
    for _ in range(max_iterations):
        gradient = [l2 * c for c in coefficients]
        bias = 0.0
        for row, label, weight in zip(matrix, labels, weights, strict=True):
            error = (
                (
                    sigmoid(intercept + sum(c * x for c, x in zip(coefficients, row, strict=True)))
                    - label
                )
                * weight
                / total
            )
            bias += error
            for i, value in enumerate(row):
                gradient[i] += error * value
        if max(abs(bias), *(abs(g) for g in gradient)) < 1e-6:
            return coefficients, intercept
        coefficients = [c - step * g for c, g in zip(coefficients, gradient, strict=True)]
        intercept -= step * bias
    raise ValueError("model convergence failed")


def fit_candidate(
    examples: list[Example],
    experiment: Experiment,
    features: tuple[str, ...],
    fitted_at: datetime,
    *,
    l2: float = 1.0,
) -> LogisticArtifact:
    if not features or len(set(features)) != len(features) or not math.isfinite(l2) or l2 <= 0:
        raise ValueError("invalid model configuration")
    assert experiment.train.end is not None and experiment.calibration.end is not None
    if fitted_at < experiment.threshold.start:
        raise ValueError("fit precedes mature calibration partition")
    train = eligible(
        examples,
        start=experiment.train.start,
        end=experiment.train.end,
        fit_at=experiment.calibration.start,
        required=features,
    )
    matrix = [[float(e.pair.features[f].value) for f in features] for e in train]  # type: ignore[arg-type]
    if not matrix:
        raise ValueError("empty training cohort")
    means = [sum(row[i] for row in matrix) / len(matrix) for i in range(len(features))]
    scales = [
        math.sqrt(sum((row[i] - means[i]) ** 2 for row in matrix) / len(matrix)) or 1.0
        for i in range(len(features))
    ]
    standardized = [
        [(x - mean) / scale for x, mean, scale in zip(row, means, scales, strict=True)]
        for row in matrix
    ]
    coefficients, intercept = _fit(
        standardized,
        [int(e.label.state == "positive") for e in train],
        [e.sample_weight for e in train],
        l2,
    )
    artifact = LogisticArtifact(
        experiment_hash=experiment.digest(),
        feature_names=features,
        means=tuple(means),
        scales=tuple(scales),
        coefficients=tuple(coefficients),
        intercept=intercept,
        fitted_at=fitted_at,
        l2=l2,
        synthetic=experiment.synthetic,
    )
    calibration = eligible(
        examples,
        start=experiment.calibration.start,
        end=experiment.calibration.end,
        fit_at=experiment.threshold.start,
        required=features,
    )
    if any(e.sample_weight != 1 for e in calibration):
        raise ValueError("calibration requires representative unsampled examples")
    logits = [
        [artifact.explain({f: float(e.pair.features[f].value) for f in features})[0]]  # type: ignore[arg-type]
        for e in calibration
    ]
    slope, bias = _fit(
        logits,
        [int(e.label.state == "positive") for e in calibration],
        [1.0] * len(calibration),
        l2,
    )
    return LogisticArtifact.model_validate(
        artifact.model_dump() | {"calibration": (slope[0], bias)}
    )
