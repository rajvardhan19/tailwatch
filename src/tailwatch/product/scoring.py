"""Score frozen feature runs with a retained candidate and prepare reviewable drafts.

Scoring never approves, delivers or activates anything. Drafts enter the same
append-only review chain as every other alert. Customer surfaces still require a
human approval and, for real data, the active release's exact model.
"""

import hashlib
import json
import math
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy import Connection, text

from tailwatch.features.runs import FeatureRun, PairFeatures
from tailwatch.product.alerts import append_event, create_episode, events
from tailwatch.research.models import LogisticArtifact
from tailwatch.sources.objects import ObjectStore
from tailwatch.sources.policy import Contract, NonEmpty

PUBLIC_STATEMENT = (
    "Observed activity is not proof of a meeting or deal. Signed contributions explain the "
    "model's raw logit; they are not causal effects or probability shares."
)


def model_digest(artifact: LogisticArtifact) -> str:
    """Same commitment used by sealed evaluation and release manifests."""
    return hashlib.sha256(artifact.model_dump_json().encode()).hexdigest()


def load_artifact(store: ObjectStore, digest: str) -> LogisticArtifact:
    artifact = LogisticArtifact.model_validate_json(store.get(digest))
    if model_digest(artifact) != digest:
        raise ValueError("retained model is not in canonical artifact form")
    return artifact


class ScoredPair(Contract):
    visitor: UUID
    host: UUID
    status: Literal["scored", "unscorable"]
    reference_logit: float | None = Field(default=None, allow_inf_nan=False)
    raw_logit: float | None = Field(default=None, allow_inf_nan=False)
    contributions: dict[str, float] = {}
    calibrated_probability: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    unavailable: dict[str, str] = {}
    evidence: tuple[str, ...] = ()

    @model_validator(mode="after")
    def consistent(self) -> "ScoredPair":
        scored = (self.raw_logit, self.reference_logit, self.calibrated_probability)
        if self.status == "scored":
            if any(v is None for v in scored) or self.unavailable or not self.contributions:
                raise ValueError("scored pairs need complete numeric output")
            if any(not math.isfinite(v) for v in self.contributions.values()):
                raise ValueError("non-finite contribution")
        elif any(v is not None for v in scored) or self.contributions or not self.unavailable:
            # Unavailable is never encoded as a zero score.
            raise ValueError("unscorable pairs carry reasons, never numbers")
        return self


def score_run(run: FeatureRun, artifact: LogisticArtifact) -> tuple[ScoredPair, ...]:
    """Score every frozen direction; missing required inputs stay explicitly unscorable."""
    if run.synthetic != artifact.synthetic:
        raise ValueError("synthetic and real runs/models cannot be mixed")
    if artifact.calibration is None:
        raise ValueError("calibrated candidate required")
    if artifact.fitted_at > run.cutoff:
        # A model fitted later would carry hindsight into an operational score.
        raise ValueError("model was fitted after the feature cutoff")
    output = []
    for pair in sorted(run.pairs, key=lambda p: (str(p.visitor), str(p.host))):
        missing = {
            name: (str(pair.features[name].status) if name in pair.features else "missing_source")
            for name in artifact.feature_names
            if name not in pair.features or pair.features[name].value is None
        }
        if missing:
            output.append(
                ScoredPair(
                    visitor=pair.visitor, host=pair.host, status="unscorable", unavailable=missing
                )
            )
            continue
        values = {name: float(pair.features[name].value) for name in artifact.feature_names}  # type: ignore[arg-type]
        logit, terms = artifact.explain(values)
        output.append(
            ScoredPair(
                visitor=pair.visitor,
                host=pair.host,
                status="scored",
                reference_logit=artifact.intercept,
                raw_logit=logit,
                contributions=terms,
                calibrated_probability=artifact.predict(values),
                evidence=tuple(
                    sorted(
                        {e for name in artifact.feature_names for e in pair.features[name].evidence}
                    )
                ),
            )
        )
    return tuple(output)


def select_candidates(
    scored: tuple[ScoredPair, ...], *, threshold: float, budget: int
) -> tuple[ScoredPair, ...]:
    """Highest calibrated scores within budget; one direction per unordered company pair."""
    if not math.isfinite(threshold) or not 0 <= threshold <= 1 or budget < 1:
        raise ValueError("threshold in [0, 1] and positive alert budget required")
    ranked = sorted(
        (
            s
            for s in scored
            if s.calibrated_probability is not None and s.calibrated_probability >= threshold
        ),
        key=lambda s: (-(s.calibrated_probability or 0), str(s.visitor), str(s.host)),
    )
    chosen: list[ScoredPair] = []
    seen: set[frozenset[UUID]] = set()
    for candidate in ranked:
        pair = frozenset((candidate.visitor, candidate.host))
        if pair in seen:
            continue
        seen.add(pair)
        chosen.append(candidate)
        if len(chosen) == budget:
            break
    return tuple(chosen)


def scoring_record(
    run: FeatureRun,
    artifact: LogisticArtifact,
    scored: tuple[ScoredPair, ...],
    *,
    threshold: float,
    budget: int,
) -> bytes:
    """Canonical retained record of every direction, including unscorable reasons."""
    return json.dumps(
        {
            "run_hash": run.digest(),
            "model_hash": model_digest(artifact),
            "synthetic": run.synthetic,
            "threshold": threshold,
            "budget": budget,
            "scored": [s.model_dump(mode="json") for s in scored],
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


class Place(Contract):
    code: str = Field(pattern=r"^[A-Z0-9-]{2,8}$")
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class PublicVisit(Contract):
    """Location of one completed visit whose evidence may be displayed externally."""

    evidence_id: NonEmpty
    origin: Place
    destination: Place
    arrived_at: AwareDatetime
    departed_at: AwareDatetime
    synthetic: bool

    @model_validator(mode="after")
    def completed(self) -> "PublicVisit":
        if self.departed_at <= self.arrived_at or self.origin.code == self.destination.code:
            raise ValueError("a visit needs distinct places and a completed dwell")
        return self


def public_content(
    run: FeatureRun,
    model_hash: str,
    candidate: ScoredPair,
    pair: PairFeatures,
    *,
    public_evidence: frozenset[str],
    visits: tuple[PublicVisit, ...] = (),
) -> dict[str, Any]:
    """Customer-facing draft content: only permitted evidence and completed visits."""
    shown = [e for e in candidate.evidence if e in public_evidence]
    located = []
    for visit in visits:
        if visit.evidence_id not in shown:
            continue
        if visit.synthetic != run.synthetic or visit.departed_at > run.cutoff:
            # Locations must be as synthetic as the run and complete before its cutoff.
            raise ValueError("visit location is not usable for this frozen run")
        located.append(visit.model_dump(mode="json"))
    return {
        "visitor": str(candidate.visitor),
        "host": str(candidate.host),
        "as_of": run.cutoff.isoformat(),
        "synthetic": run.synthetic,
        "model_hash": model_hash,
        # Synthetic candidates are plumbing checks, so no probability is published.
        "calibrated_probability": None if run.synthetic else candidate.calibrated_probability,
        "probability_status": "synthetic_not_reported" if run.synthetic else "calibrated",
        "reference_logit": candidate.reference_logit,
        "contributions": candidate.contributions,
        # Every frozen signal keeps its availability state; unavailable is never zero.
        "conditions": {
            name: {
                "status": str(signal.status),
                "value": signal.value,
                "in_model": name in candidate.contributions,
                "contribution": candidate.contributions.get(name),
            }
            for name, signal in sorted(pair.features.items())
        },
        "visits": located,
        "evidence": shown,
        "withheld_evidence": len(candidate.evidence) - len(shown),
        "statement": PUBLIC_STATEMENT,
    }


class DraftResult(Contract):
    visitor: UUID
    host: UUID
    episode_id: UUID
    status: Literal["drafted", "existing_episode"]
    event_hash: str | None = None


def draft_alerts(
    conn: Connection,
    *,
    tenant: UUID,
    run: FeatureRun,
    artifact: LogisticArtifact,
    candidates: tuple[ScoredPair, ...],
    actor: NonEmpty,
    public_evidence: frozenset[str] = frozenset(),
    score_digest: str | None = None,
    visits: tuple[PublicVisit, ...] = (),
) -> tuple[DraftResult, ...]:
    """Append draft events only. Approval remains a separate human review transition."""
    if not actor.strip():
        raise ValueError("draft actor required")
    frozen = {(p.visitor, p.host): p for p in run.pairs}
    model_hash, run_hash = model_digest(artifact), run.digest()
    results = []
    for candidate in candidates:
        if candidate.status != "scored" or (candidate.visitor, candidate.host) not in frozen:
            raise ValueError("drafts require scored directions from this frozen run")
        episode = create_episode(
            conn,
            tenant=tenant,
            visitor=candidate.visitor,
            host=candidate.host,
            model_hash=model_hash,
            feature_run_hash=run_hash,
            synthetic=run.synthetic,
        )
        existing = conn.execute(
            text("SELECT model_hash,feature_run_hash FROM alert_episodes WHERE episode_id=:id"),
            {"id": episode},
        ).one()
        if events(conn, tenant, episode) or tuple(existing) != (model_hash, run_hash):
            # The 180-day episode keeps its first direction, timestamp and history.
            results.append(
                DraftResult(
                    visitor=candidate.visitor,
                    host=candidate.host,
                    episode_id=episode,
                    status="existing_episode",
                )
            )
            continue
        body = {
            "internal": {
                "run_id": str(run.run_id),
                "run_hash": run_hash,
                "cutoff": run.cutoff.isoformat(),
                "code_version": run.code_version,
                "config_digest": run.config_digest,
                "source_snapshots": sorted(str(s) for s in run.source_snapshots),
                "model_hash": model_hash,
                "experiment_hash": artifact.experiment_hash,
                "score_digest": score_digest,
                "score": candidate.model_dump(mode="json"),
            },
            "public": public_content(
                run,
                model_hash,
                candidate,
                frozen[(candidate.visitor, candidate.host)],
                public_evidence=public_evidence,
                visits=visits,
            ),
        }
        digest = append_event(
            conn, tenant=tenant, episode=episode, kind="draft", actor=actor, body=body
        )
        results.append(
            DraftResult(
                visitor=candidate.visitor,
                host=candidate.host,
                episode_id=episode,
                status="drafted",
                event_hash=digest,
            )
        )
    return tuple(results)
