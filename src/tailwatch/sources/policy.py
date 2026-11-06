"""Fail-closed source capabilities and per-run request reservations."""

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

NonEmpty = Annotated[str, Field(min_length=1, pattern=r"\S")]
Count = Annotated[int, Field(strict=True, ge=0)]


class Mode(StrEnum):
    FIXTURE = "fixture"
    RESEARCH = "research"
    SHADOW = "shadow"
    PRODUCTION = "production"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Permission(Contract):
    reference: NonEmpty
    reviewed_by: NonEmpty
    valid_from: AwareDatetime
    valid_until: AwareDatetime
    modes: tuple[Mode, ...]
    retention: bool = False
    derived_output: bool = False
    external_evidence: bool = False
    b2b_distribution: bool = False

    @model_validator(mode="after")
    def ordered(self) -> "Permission":
        if self.valid_until <= self.valid_from:
            raise ValueError("permission interval must be nonempty")
        return self


class CoverageSample(Contract):
    start: AwareDatetime
    end: AwareDatetime
    evidence: NonEmpty
    rows_observed: Count
    synthetic: bool

    @model_validator(mode="after")
    def ordered(self) -> "CoverageSample":
        if self.end <= self.start:
            raise ValueError("coverage interval must be nonempty")
        return self


class Source(Contract):
    source_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*$")]
    enabled: bool = False
    kind: Literal["fixture", "live"]
    capabilities: tuple[NonEmpty, ...]
    latency_seconds: Count
    permission: Permission | None = None
    coverage: tuple[CoverageSample, ...] = ()
    max_requests: Count = 0
    max_cost_microusd: Count = 0


class Manifest(Contract):
    version: Literal[1]
    sector: NonEmpty | None = None
    required_sources: tuple[NonEmpty, ...]
    sources: tuple[Source, ...]

    @model_validator(mode="after")
    def references(self) -> "Manifest":
        ids = [source.source_id for source in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate source IDs")
        if not set(self.required_sources).issubset(ids):
            raise ValueError("required source is not declared")
        if not self.required_sources:
            raise ValueError("declare at least one required source")
        return self


def load_manifest(path: Path) -> Manifest:
    return Manifest.model_validate_json(path.read_text())


class PolicyDenied(ValueError):
    pass


def authorize(
    source: Source,
    *,
    mode: Mode,
    capability: str,
    at: datetime,
    external_evidence: bool = False,
    b2b_distribution: bool = False,
) -> None:
    if at.tzinfo is None or at.utcoffset() is None:
        raise PolicyDenied("authorization time must be timezone-aware")
    if not source.enabled or capability not in source.capabilities:
        raise PolicyDenied("source disabled or capability undeclared")
    if (mode == Mode.FIXTURE) != (source.kind == "fixture"):
        raise PolicyDenied("fixture and live sources cannot be mixed")
    permission = source.permission
    if permission is None or mode not in permission.modes:
        raise PolicyDenied("use permission is undeclared")
    if not permission.valid_from <= at < permission.valid_until:
        raise PolicyDenied("use permission is not currently valid")
    if not permission.retention or not permission.derived_output:
        raise PolicyDenied("retention and derived-output permissions are required")
    if external_evidence and not permission.external_evidence:
        raise PolicyDenied("external evidence display is not permitted")
    if b2b_distribution and not permission.b2b_distribution:
        raise PolicyDenied("B2B distribution is not permitted")


class RequestBudget:
    """Thread-safe, conservative per-run reservations, charged even after failure.

    No refunds: retries and each page reserve separately. This is not a durable
    daily/customer ledger. Until that exists, no live adapter can be enabled.
    """

    def __init__(self, source: Source) -> None:
        self._request_limit = source.max_requests
        self._cost_limit = source.max_cost_microusd
        self._requests = 0
        self._cost = 0
        self._lock = Lock()

    def reserve(self, cost_microusd: int) -> None:
        if type(cost_microusd) is not int or cost_microusd < 0:
            raise ValueError("cost must be a nonnegative integer in micro-USD")
        with self._lock:
            if self._requests + 1 > self._request_limit:
                raise PolicyDenied("request budget exhausted")
            if self._cost + cost_microusd > self._cost_limit:
                raise PolicyDenied("cost budget exhausted")
            self._requests += 1
            self._cost += cost_microusd


def inventory(manifest: Manifest, at: datetime) -> str:
    """Report observations, never infer historical completeness from a sample."""
    rows: list[dict[str, object]] = []
    for source in manifest.sources:
        reasons = []
        try:
            authorize(source, mode=Mode.RESEARCH, capability=source.capabilities[0], at=at)
        except (PolicyDenied, IndexError) as error:
            reasons.append(str(error) or "no declared capabilities")
        if not any(not sample.synthetic and sample.rows_observed for sample in source.coverage):
            reasons.append("no measured real-data coverage sample")
        rows.append(
            {
                "source_id": source.source_id,
                "blockers": reasons,
                "coverage_samples": [sample.model_dump(mode="json") for sample in source.coverage],
            }
        )
    return json.dumps(
        {
            "as_of": at.astimezone(UTC).isoformat(),
            "sector": manifest.sector,
            "g0": "not_established",
            "sources": rows,
            "note": "Samples do not establish completeness or historical overlap. "
            "G0 requires a reviewed coverage/rights report.",
        },
        sort_keys=True,
    )
