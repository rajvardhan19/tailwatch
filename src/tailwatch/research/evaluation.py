"""Episode-level sealed evaluation with durable single-access bookkeeping."""

import hashlib
import json
import math
import random
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field
from sqlalchemy import Engine, text

from tailwatch.research.experiments import Experiment
from tailwatch.sources.policy import Contract


class Prediction(Contract):
    episode_id: UUID
    at: AwareDatetime
    probability: float = Field(ge=0, le=1, allow_inf_nan=False)
    positive: bool
    deal_id: UUID | None = None
    announcement_at: AwareDatetime | None = None
    label_known_at: AwareDatetime


class Evaluation(Contract):
    decision: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    alerts: int
    successes: int
    independent_deals: int
    precision: float | None
    recall: float | None
    precision_interval: tuple[float, float] | None
    lift: float | None
    brier: float | None
    log_loss: float | None
    median_lead_days: float | None
    calibration_bins: tuple[dict[str, float], ...]
    one_class_bootstrap_draws: int
    reason: str


def evaluate(
    predictions: list[Prediction],
    experiment: Experiment,
    *,
    threshold: float,
    baseline_precision: float,
    eligible_deals: int,
    as_of: datetime,
) -> Evaluation:
    if not 0 <= threshold <= 1 or not 0 <= baseline_precision <= 1 or eligible_deals < 0:
        raise ValueError("invalid frozen evaluation settings")
    assert experiment.test.end is not None
    # First chronological direction/score for each episode is frozen. A later
    # high-scoring update cannot turn an earlier wrong direction into success.
    first: dict[UUID, Prediction] = {}
    for p in sorted(predictions, key=lambda p: (p.at, str(p.episode_id))):
        if not experiment.test.start <= p.at < experiment.test.end or p.label_known_at > as_of:
            raise ValueError("test rows outside partition or immature labels")
        if p.label_known_at < p.at + timedelta(days=180):
            raise ValueError("labels require complete 180-day followup")
        if (
            p.announcement_at is not None
            and p.positive
            and not p.at < p.announcement_at <= p.at + timedelta(days=180)
        ):
            raise ValueError("positive announcement outside prediction horizon")
        if p.positive and (p.deal_id is None or p.announcement_at is None):
            raise ValueError("positive lacks definitive deal evidence")
        first.setdefault(p.episode_id, p)
    cohort = list(first.values())
    selected = sorted(
        (p for p in cohort if p.probability >= threshold),
        key=lambda p: (-p.probability, p.at, str(p.episode_id)),
    )[: experiment.alert_budget]
    hits: set[UUID] = set()
    outcomes = []
    for p in selected:
        success = p.positive and p.deal_id not in hits
        outcomes.append(int(success))
        if success and p.deal_id:
            hits.add(p.deal_id)
    n, k = len(selected), sum(outcomes)
    precision = k / n if n else None
    interval = None
    one_class = 0
    if n:
        rng = random.Random(experiment.seed)
        draws = []
        for _ in range(experiment.bootstrap_draws):
            sample = [rng.choice(outcomes) for _ in outcomes]
            one_class += int(len(set(sample)) == 1)
            draws.append(sum(sample) / n)
        draws.sort()
        # Wilson interval prevents all-success tiny samples from claiming certainty.
        z = 1.959963984540054
        center = (k / n + z * z / (2 * n)) / (1 + z * z / n)
        half = z * math.sqrt((k / n) * (1 - k / n) / n + z * z / (4 * n * n)) / (1 + z * z / n)
        interval = (
            max(0, min(center - half, draws[int(0.025 * (len(draws) - 1))])),
            min(1, max(center + half, draws[int(0.975 * (len(draws) - 1))])),
        )
    lift = (
        precision / baseline_precision if precision is not None and baseline_precision > 0 else None
    )
    bins = []
    for i in range(10):
        group = [
            p
            for p in cohort
            if i / 10 <= p.probability < (i + 1) / 10 or (i == 9 and p.probability == 1)
        ]
        if group:
            bins.append(
                {
                    "count": float(len(group)),
                    "predicted": sum(p.probability for p in group) / len(group),
                    "observed": sum(p.positive for p in group) / len(group),
                }
            )
    leads = sorted(
        (p.announcement_at - p.at).total_seconds() / 86400
        for p in selected
        if p.positive and p.announcement_at is not None
    )
    enough = n >= experiment.minimum_alerts and eligible_deals >= experiment.minimum_deals
    decision: Literal["PASS", "FAIL", "INCONCLUSIVE"] = "INCONCLUSIVE"
    reason = "insufficient_samples_or_undefined_baseline"
    if enough and lift is not None and interval is not None:
        decision = (
            "PASS"
            if interval[0] >= experiment.precision_target and lift >= experiment.lift_target
            else "FAIL"
        )
        reason = "registered_targets"
    if experiment.synthetic:
        decision, reason = "INCONCLUSIVE", "synthetic_evaluation_cannot_promote"
    return Evaluation(
        decision=decision,
        alerts=n,
        successes=k,
        independent_deals=len(hits),
        precision=precision,
        recall=len(hits) / eligible_deals if eligible_deals else None,
        precision_interval=interval,
        lift=lift,
        brier=sum((p.probability - int(p.positive)) ** 2 for p in cohort) / len(cohort)
        if cohort
        else None,
        log_loss=-sum(
            int(p.positive) * math.log(max(p.probability, 1e-15))
            + (1 - int(p.positive)) * math.log(max(1 - p.probability, 1e-15))
            for p in cohort
        )
        / len(cohort)
        if cohort
        else None,
        median_lead_days=leads[len(leads) // 2] if leads else None,
        calibration_bins=tuple(bins),
        one_class_bootstrap_draws=one_class,
        reason=reason,
    )


def register_experiment(engine: Engine, experiment: Experiment) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO research_experiments(experiment_hash,manifest) "
                "VALUES(:id,CAST(:body AS jsonb)) ON CONFLICT DO NOTHING"
            ),
            {"id": experiment.digest(), "body": experiment.model_dump_json()},
        )


def evaluate_sealed(
    engine: Engine,
    experiment: Experiment,
    predictions: list[Prediction],
    *,
    model_hash: str,
    dataset_hash: str,
    threshold: float,
    baseline_precision: float,
    eligible_deals: int,
    as_of: datetime,
) -> Evaluation:
    # Dataset commitment excludes model scores. Independent release reviewers must
    # match this commitment to the frozen full feature/label artifact.
    labels = sorted(
        (
            str(p.episode_id),
            p.at.isoformat(),
            p.positive,
            str(p.deal_id),
            p.label_known_at.isoformat(),
        )
        for p in predictions
    )
    actual = hashlib.sha256(json.dumps(labels, separators=(",", ":")).encode()).hexdigest()
    if actual != dataset_hash:
        raise ValueError("sealed label commitment mismatch")
    with engine.connect() as conn:
        seal = conn.execute(
            text(
                "SELECT dataset_hash,model_hash,threshold,baseline_precision,eligible_deals "
                "FROM research_seals WHERE experiment_hash=:id"
            ),
            {"id": experiment.digest()},
        ).first()
        if seal is None or tuple(seal) != (
            dataset_hash,
            model_hash,
            threshold,
            baseline_precision,
            eligible_deals,
        ):
            raise ValueError("test configuration was not sealed before access")
    # Access commits BEFORE computation. Failures do not permit an unlogged retry.
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sealed_test_access(dataset_hash,experiment_hash,model_hash) "
                "VALUES(:data,:experiment,:model)"
            ),
            {"data": dataset_hash, "experiment": experiment.digest(), "model": model_hash},
        )
    result = evaluate(
        predictions,
        experiment,
        threshold=threshold,
        baseline_precision=baseline_precision,
        eligible_deals=eligible_deals,
        as_of=as_of,
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO research_evaluations(dataset_hash,result) "
                "VALUES(:id,CAST(:body AS jsonb))"
            ),
            {"id": dataset_hash, "body": result.model_dump_json()},
        )
    return result


def seal_evaluation(
    engine: Engine,
    experiment: Experiment,
    *,
    dataset_hash: str,
    model_hash: str,
    threshold: float,
    baseline_precision: float,
    eligible_deals: int,
) -> None:
    if not 0 <= threshold <= 1 or not 0 <= baseline_precision <= 1 or eligible_deals < 0:
        raise ValueError("invalid development-selected evaluation settings")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO research_seals(experiment_hash,dataset_hash,model_hash,threshold,"
                "baseline_precision,eligible_deals) "
                "VALUES(:exp,:data,:model,:threshold,:baseline,:deals)"
            ),
            {
                "exp": experiment.digest(),
                "data": dataset_hash,
                "model": model_hash,
                "threshold": threshold,
                "baseline": baseline_precision,
                "deals": eligible_deals,
            },
        )
