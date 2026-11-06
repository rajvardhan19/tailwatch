from datetime import UTC, datetime
from enum import StrEnum


class Availability(StrEnum):
    OBSERVED = "observed"
    INSUFFICIENT_HISTORY = "insufficient_history"
    STALE = "stale"
    MISSING_SOURCE = "missing_source"
    AMBIGUOUS = "ambiguous"
    NOT_APPLICABLE = "not_applicable"


class ReplayMode(StrEnum):
    OPERATIONAL = "operational"
    PUBLIC_SIMULATION = "public_simulation"


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)
