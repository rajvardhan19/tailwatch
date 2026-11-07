"""Executable-session return policy; unknown exits never imply zero return."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import AwareDatetime, Field

from tailwatch.sources.policy import Contract


class PriceSession(Contract):
    opened_at: AwareDatetime
    closed_at: AwareDatetime
    available_at: AwareDatetime
    adjusted_open: Decimal = Field(gt=0, allow_inf_nan=False)
    adjusted_close: Decimal = Field(gt=0, allow_inf_nan=False)
    benchmark_open: Decimal = Field(gt=0, allow_inf_nan=False)
    benchmark_close: Decimal = Field(gt=0, allow_inf_nan=False)
    tradable: bool


def executable_return(
    sessions: list[PriceSession], alert_at: datetime, as_of: datetime, horizon: int
) -> dict[str, object]:
    if horizon not in {5, 30}:
        raise ValueError("registered horizons are 5 or 30 sessions")
    known = sorted(
        (s for s in sessions if s.opened_at > alert_at and s.available_at <= as_of),
        key=lambda s: s.opened_at,
    )
    if len({s.opened_at for s in known}) != len(known):
        raise ValueError("duplicate price sessions")
    first = next((i for i, s in enumerate(known) if s.tradable), None)
    if first is None or first + horizon > len(known):
        return {"status": "unavailable", "reason": "missing_executable_entry_or_followup"}
    entry, exit = known[first], known[first + horizon - 1]
    if not exit.tradable or exit.closed_at > as_of:
        return {"status": "unavailable", "reason": "halt_or_unavailable_exit"}
    stock = float(exit.adjusted_close / entry.adjusted_open - 1)
    benchmark = float(exit.benchmark_close / entry.benchmark_open - 1)
    return {
        "status": "observed",
        "entry": entry.opened_at.isoformat(),
        "exit": exit.closed_at.isoformat(),
        "return": stock,
        "benchmark_return": benchmark,
        "excess_return": stock - benchmark,
    }


class HistoricalCase(Contract):
    case_id: UUID
    companies: tuple[UUID, UUID]
    outcome_known_at: AwareDatetime
    success: bool
    vector: tuple[float, ...]


def lookalikes(
    cases: list[HistoricalCase],
    vector: tuple[float, ...],
    current_pair: set[UUID],
    cutoff: datetime,
    limit: int = 5,
) -> list[HistoricalCase]:
    if not 1 <= limit <= 20:
        raise ValueError("bounded retrieval required")
    eligible = [
        c
        for c in cases
        if c.outcome_known_at <= cutoff
        and set(c.companies) != current_pair
        and len(c.vector) == len(vector)
    ]
    return sorted(
        eligible,
        key=lambda c: (
            sum((a - b) ** 2 for a, b in zip(c.vector, vector, strict=True)),
            str(c.case_id),
        ),
    )[:limit]
