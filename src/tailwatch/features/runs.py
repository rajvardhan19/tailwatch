"""Frozen directional feature manifests and atomic evidence publication."""

import hashlib
import json
from datetime import datetime
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from tailwatch.db.evidence import EvidenceRepository, Fact
from tailwatch.domain.time import Availability
from tailwatch.features.signals import Signal
from tailwatch.sources.objects import ObjectStore
from tailwatch.sources.policy import Contract, NonEmpty


class PairFeatures(Contract):
    visitor: UUID
    host: UUID
    features: dict[str, Signal]

    @model_validator(mode="after")
    def not_self(self) -> "PairFeatures":
        if self.visitor == self.host:
            raise ValueError("self pair")
        return self

    def scorable(self, required: tuple[str, ...]) -> bool:
        return bool(required) and all(
            name in self.features and self.features[name].value is not None for name in required
        )


def interaction(flight: Signal, silence: Signal) -> Signal:
    if flight.value is None or silence.value is None:
        return Signal(status=Availability.MISSING_SOURCE)
    return Signal(
        status=Availability.OBSERVED,
        value=flight.value * silence.value,
        evidence=tuple(sorted(set(flight.evidence + silence.evidence))),
    )


class FeatureRun(Contract):
    run_id: UUID
    cutoff: AwareDatetime
    code_version: NonEmpty
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    universe: tuple[UUID, ...]
    source_snapshots: tuple[UUID, ...]
    input_fact_ids: tuple[UUID, ...]
    required_features: tuple[NonEmpty, ...] = Field(min_length=1)
    pairs: tuple[PairFeatures, ...]
    synthetic: bool

    @model_validator(mode="after")
    def complete_directions(self) -> "FeatureRun":
        expected = {(a, b) for a in self.universe for b in self.universe if a != b}
        actual = [(p.visitor, p.host) for p in self.pairs]
        if (
            len(set(self.universe)) != len(self.universe)
            or len(set(actual)) != len(actual)
            or set(actual) != expected
        ):
            raise ValueError("freeze both directions of the complete universe")
        return self

    def canonical(self) -> bytes:
        data = self.model_dump(mode="json")
        for name in ("universe", "source_snapshots", "input_fact_ids", "required_features"):
            data[name] = sorted(data[name])
        data["pairs"] = sorted(data["pairs"], key=lambda p: (p["visitor"], p["host"]))
        return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

    def digest(self) -> str:
        return hashlib.sha256(self.canonical()).hexdigest()


def publish_feature_run(
    repo: EvidenceRepository, store: ObjectStore, run: FeatureRun, fact: Fact, at: datetime
) -> None:
    if (
        fact.digest != run.digest()
        or fact.payload != run.model_dump(mode="json")
        or fact.logical_key != f"feature-run:{run.run_id}"
        or fact.parser_version != "feature-run-v1"
        or fact.valid_from != run.cutoff
        or fact.valid_to is not None
        or fact.processed_at < run.cutoff
        or fact.revision != 1
    ):
        raise ValueError("feature manifest mismatch")
    # Pin exact observed input IDs, rather than rerunning an as-of query after a
    # concurrent source publication. C04's publication guard protects this vector.
    from sqlalchemy import text

    for input_id in run.input_fact_ids:
        visible = repo.connection.execute(
            text(
                "SELECT 1 FROM evidence_facts f JOIN snapshot_publications p USING(snapshot_id) "
                "WHERE f.fact_id=:id AND greatest(p.published_at,p.recorded_at)<=:cutoff "
                "AND f.snapshot_id=ANY(:snapshots)"
            ),
            {"id": input_id, "cutoff": run.cutoff, "snapshots": list(run.source_snapshots)},
        ).first()
        if visible is None:
            raise ValueError("input fact is not visible in the frozen manifest")
    repo.capture(store, run.canonical())
    repo.append(fact)
    repo.publish(fact.snapshot_id, at)
