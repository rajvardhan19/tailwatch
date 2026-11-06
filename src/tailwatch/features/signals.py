"""Versioned research signals with explicit observation requirements."""

import math
import statistics
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from tailwatch.domain.time import Availability, utc
from tailwatch.sources.policy import Contract, NonEmpty


class Signal(Contract):
    value: float | None = Field(default=None, allow_inf_nan=False)
    status: Availability
    evidence: tuple[str, ...] = ()
    version: str = "signals-v1"
    reason: str | None = None

    @model_validator(mode="after")
    def availability(self) -> "Signal":
        if (self.status == Availability.OBSERVED) != (self.value is not None):
            raise ValueError("only observed signals have numeric values")
        return self


class TravelEpisode(Contract):
    episode_id: UUID
    visitor: UUID
    host: UUID
    arrived_at: AwareDatetime
    departed_at: AwareDatetime
    available_at: AwareDatetime
    host_weight: float = Field(ge=0, le=1, allow_inf_nan=False)
    event_multiplier: float = Field(ge=0, le=1, allow_inf_nan=False)
    evidence: NonEmpty

    @model_validator(mode="after")
    def ordered(self) -> "TravelEpisode":
        if not self.arrived_at < self.departed_at <= self.available_at or self.visitor == self.host:
            raise ValueError("invalid completed directional travel episode")
        return self


def dwell_weight(hours: float) -> float:
    if not math.isfinite(hours) or hours < 0:
        raise ValueError("invalid dwell")
    return max(0, min(1, (hours - 1) / 3))


def flight_signals(
    episodes: list[TravelEpisode],
    *,
    visitor: UUID,
    host: UUID,
    cutoff: datetime,
    covered_since: datetime | None,
    covered_until: datetime | None,
) -> dict[str, Signal]:
    cutoff = utc(cutoff)
    missing = Signal(status=Availability.INSUFFICIENT_HISTORY)
    if covered_since is None or covered_until is None or utc(covered_until) < cutoff:
        return {name: missing for name in ("flight", "novelty", "intensity")}
    covered_since = utc(covered_since)
    visible = [
        e
        for e in episodes
        if e.visitor == visitor
        and e.available_at <= cutoff
        and e.arrived_at < cutoff
        and dwell_weight((e.departed_at - e.arrived_at).total_seconds() / 3600) > 0
    ]
    # Conflicting duplicate observations are not silently arbitrated.
    dedup: dict[tuple[UUID, UUID], TravelEpisode] = {}
    for episode in visible:
        key = episode.episode_id, episode.host
        if key in dedup and dedup[key] != episode:
            raise ValueError("conflicting travel episode")
        dedup[key] = episode
    visible = list(dedup.values())
    recent = [e for e in visible if e.host == host and cutoff - timedelta(days=30) <= e.arrived_at]
    evidence = tuple(sorted({e.evidence for e in recent}))
    flight = missing
    if covered_since <= cutoff - timedelta(days=30):
        flight = Signal(
            status=Availability.OBSERVED,
            evidence=evidence,
            value=sum(
                e.host_weight
                * e.event_multiplier
                * dwell_weight((e.departed_at - e.arrived_at).total_seconds() / 3600)
                * math.exp(-(cutoff - e.arrived_at).total_seconds() / 86400 / 14)
                for e in recent
            ),
        )
    novelty = missing
    if not recent and flight.value is not None:
        novelty = Signal(status=Availability.OBSERVED, value=0)
    elif recent:
        latest = max(recent, key=lambda e: (e.arrived_at, str(e.episode_id)))
        if covered_since <= latest.arrived_at - timedelta(days=365):
            prior = any(
                e.host == host
                and e.episode_id != latest.episode_id
                and latest.arrived_at - timedelta(days=365) <= e.arrived_at < latest.arrived_at
                for e in visible
            )
            novelty = Signal(
                status=Availability.OBSERVED, value=float(not prior), evidence=(latest.evidence,)
            )
    intensity = missing
    if covered_since <= cutoff - timedelta(days=390):
        counts = [
            len(
                {
                    e.episode_id
                    for e in visible
                    if cutoff - timedelta(days=30 * (i + 1))
                    <= e.arrived_at
                    < cutoff - timedelta(days=30 * i)
                }
            )
            for i in range(13)
        ]
        variance = statistics.pvariance(counts[1:])
        if variance:
            intensity = Signal(
                status=Availability.OBSERVED,
                value=(counts[0] - statistics.mean(counts[1:])) / math.sqrt(variance),
            )
        else:
            intensity = Signal(
                status=Availability.INSUFFICIENT_HISTORY, reason="zero_baseline_variance"
            )
    return {"flight": flight, "novelty": novelty, "intensity": intensity}


class Purchase(Contract):
    economic_id: NonEmpty
    person: UUID
    occurred_at: AwareDatetime
    available_at: AwareDatetime
    role: str
    evidence: NonEmpty


def buying(purchases: list[Purchase], cutoff: datetime, *, coverage_complete: bool) -> Signal:
    cutoff = utc(cutoff)
    if not coverage_complete:
        return Signal(status=Availability.MISSING_SOURCE)
    daily: dict[tuple[UUID, object], tuple[float, Purchase]] = {}
    economic: dict[str, Purchase] = {}
    for purchase in purchases:
        if (
            purchase.available_at > cutoff
            or not cutoff - timedelta(days=90) <= purchase.occurred_at < cutoff
        ):
            continue
        if purchase.economic_id in economic and economic[purchase.economic_id] != purchase:
            return Signal(status=Availability.AMBIGUOUS)
        economic[purchase.economic_id] = purchase
        weight = {"ceo": 1.0, "cfo": 1.0, "director": 0.7, "officer": 0.5}.get(purchase.role)
        if weight is None:
            return Signal(status=Availability.AMBIGUOUS)
        key = purchase.person, purchase.occurred_at.date()
        if key not in daily or weight > daily[key][0]:
            daily[key] = weight, purchase
    score = sum(
        w * math.exp(-(cutoff - p.occurred_at).total_seconds() / 86400 / 30)
        for w, p in daily.values()
    )
    people = {p.person for _, p in daily.values() if p.occurred_at >= cutoff - timedelta(days=14)}
    return Signal(
        status=Availability.OBSERVED,
        value=score * (1.5 if len(people) >= 3 else 1),
        evidence=tuple(sorted({p.evidence for _, p in daily.values()})),
    )


class SilenceExposure(Contract):
    person: UUID
    baseline_sales: int = Field(ge=0, strict=True)
    recent_sales: int = Field(ge=0, strict=True)
    baseline_days: int = Field(ge=0, strict=True)
    recent_days: int = Field(ge=0, strict=True)
    evidence: NonEmpty


def silence(
    exposures: list[SilenceExposure],
    *,
    watermark: datetime,
    cutoff: datetime,
    coverage_complete: bool,
) -> Signal:
    if utc(watermark) > utc(cutoff):
        raise ValueError("future observation watermark")
    if not coverage_complete:
        return Signal(status=Availability.MISSING_SOURCE)
    if len({e.person for e in exposures}) != len(exposures):
        raise ValueError("duplicate insider denominator")
    eligible = [e for e in exposures if e.baseline_sales >= 4]
    if not eligible or any(not e.baseline_days or not e.recent_days for e in eligible):
        return Signal(status=Availability.INSUFFICIENT_HISTORY)
    return Signal(
        status=Availability.OBSERVED,
        value=sum(
            e.baseline_sales / e.baseline_days * e.recent_days if e.recent_sales == 0 else 0
            for e in eligible
        )
        / len(eligible),
        evidence=tuple(sorted({e.evidence for e in eligible})),
    )
