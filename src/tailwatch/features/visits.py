"""Completed observed aircraft visits; never inferred executive meetings."""

import math
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import AwareDatetime, Field

from tailwatch.domain.time import utc
from tailwatch.sources.flights import FlightBatch, FlightCoverage
from tailwatch.sources.policy import Contract


class Visit(Contract):
    episode_id: UUID
    airframe_id: UUID
    airport_id: UUID
    arrived_at: AwareDatetime
    departed_at: AwareDatetime | None
    available_at: AwareDatetime
    completed: bool
    evidence: tuple[str, ...]
    reason: str | None = None


def covered(
    coverage: tuple[FlightCoverage, ...],
    aircraft: UUID,
    start: datetime,
    end: datetime,
    cutoff: datetime,
) -> bool:
    intervals = [
        c
        for c in coverage
        if c.airframe_id == aircraft
        and c.available_at <= cutoff
        and c.start < end
        and c.end is not None
        and c.end > start
    ]
    if any(c.status != "complete" for c in intervals):
        return False
    cursor = start
    for c in sorted(intervals, key=lambda c: c.start):
        if c.start > cursor:
            return False
        assert c.end is not None
        cursor = max(cursor, c.end)
    return cursor >= end


def completed_visits(batch: FlightBatch, cutoff: datetime) -> list[Visit]:
    cutoff = utc(cutoff)
    if batch.status != "complete":
        return []
    legs = sorted(
        (leg for leg in batch.legs if leg.available_at <= cutoff),
        key=lambda leg: (leg.departed_at, leg.leg_id),
    )
    result = []
    for arrival in legs:
        if arrival.airframe_id is None or arrival.destination is None:
            continue
        next_leg = next(
            (
                leg
                for leg in legs
                if leg.airframe_id == arrival.airframe_id and leg.departed_at >= arrival.arrived_at
            ),
            None,
        )
        supported = (
            next_leg is not None
            and next_leg.origin == arrival.destination
            and next_leg.departed_at > arrival.arrived_at
            and covered(
                batch.coverage,
                arrival.airframe_id,
                arrival.arrived_at,
                next_leg.departed_at,
                cutoff,
            )
        )
        result.append(
            Visit(
                episode_id=uuid5(NAMESPACE_URL, f"{arrival.source_id}:{arrival.leg_id}:visit"),
                airframe_id=arrival.airframe_id,
                airport_id=arrival.destination,
                arrived_at=arrival.arrived_at,
                departed_at=next_leg.departed_at if supported and next_leg else None,
                available_at=max(
                    arrival.available_at,
                    next_leg.available_at,
                    *(
                        c.available_at
                        for c in batch.coverage
                        if c.airframe_id == arrival.airframe_id and c.available_at <= cutoff
                    ),
                )
                if supported and next_leg
                else arrival.available_at,
                completed=bool(supported),
                evidence=(arrival.evidence, next_leg.evidence)
                if supported and next_leg
                else (arrival.evidence,),
                reason=None if supported else "missing_departure_or_continuity",
            )
        )
    return result


class Attribution(Contract):
    weights: dict[UUID, float]
    background: float = Field(ge=0, le=1)
    event_multiplier: float = Field(ge=0, le=1)
    event_coverage: bool
    alternatives: tuple[str, ...]


def attribute_hosts(
    distances: dict[UUID, float],
    *,
    visitor: UUID,
    excluded_companies: set[UUID] | None = None,
    home_base: bool = False,
    maintenance: bool = False,
    overlapping_events: tuple[str, ...] = (),
    event_coverage: bool = False,
    rho_km: float = 25,
    background: float = 1,
) -> Attribution:
    if not math.isfinite(rho_km) or not math.isfinite(background) or rho_km <= 0 or background <= 0:
        raise ValueError("positive finite attribution configuration required")
    if any(not math.isfinite(d) or d < 0 for d in distances.values()):
        raise ValueError("invalid host distance")
    excluded = (excluded_companies or set()) | {visitor}
    weights = {
        company: math.exp(-distance / rho_km)
        for company, distance in distances.items()
        if company not in excluded and not home_base and not maintenance
    }
    denominator = background + sum(weights.values())
    return Attribution(
        weights={c: w / denominator for c, w in weights.items()},
        background=background / denominator,
        event_multiplier=0.2 if overlapping_events else 1,
        event_coverage=event_coverage,
        alternatives=tuple(
            sorted(
                set(overlapping_events)
                | ({"home_base"} if home_base else set())
                | ({"maintenance"} if maintenance else set())
            )
        ),
    )
