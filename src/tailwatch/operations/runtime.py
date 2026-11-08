import hashlib
import json
import math
from datetime import UTC, date, datetime, time
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, Field
from sqlalchemy import Engine, text

from tailwatch.sources.policy import Contract, PolicyDenied, Source


class DurableBudget:
    """Atomic cross-process reservation, committed before a provider call."""

    def __init__(self, engine: Engine, source: Source, day: date) -> None:
        self.engine, self.source, self.day = engine, source, day

    def reserve(self, cost_microusd: int) -> None:
        if type(cost_microusd) is not int or cost_microusd < 0:
            raise ValueError("nonnegative integer cost required")
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO budget_accounts(source_id,budget_day,request_limit,cost_limit) "
                    "VALUES(:id,:day,:requests,:cost) ON CONFLICT DO NOTHING"
                ),
                {
                    "id": self.source.source_id,
                    "day": self.day,
                    "requests": self.source.max_requests,
                    "cost": self.source.max_cost_microusd,
                },
            )
            row = conn.execute(
                text(
                    "UPDATE budget_accounts SET requests=requests+1,cost=cost+:charge "
                    "WHERE source_id=:id AND budget_day=:day AND requests<request_limit AND "
                    "cost+:charge<=cost_limit "
                    "AND request_limit=:requests AND cost_limit=:cost RETURNING requests"
                ),
                {
                    "id": self.source.source_id,
                    "day": self.day,
                    "charge": cost_microusd,
                    "requests": self.source.max_requests,
                    "cost": self.source.max_cost_microusd,
                },
            ).first()
            if row is None:
                raise PolicyDenied("durable budget exhausted or configuration changed")


class SourceHealth(Contract):
    source_id: str
    fresh_until: AwareDatetime
    complete: bool
    hash_verified: bool
    missing_fraction: float = Field(ge=0, le=1, allow_inf_nan=False)
    drift: float = Field(ge=0, allow_inf_nan=False)


def readiness(
    sources: list[SourceHealth],
    required: set[str],
    at: datetime,
    *,
    max_missing: float,
    max_drift: float,
) -> dict[str, object]:
    if not 0 <= max_missing <= 1 or not math.isfinite(max_drift) or max_drift < 0 or not required:
        raise ValueError("explicit readiness thresholds and required sources needed")
    failures = []
    by_id = {s.source_id: s for s in sources}
    if len(by_id) != len(sources):
        raise ValueError("duplicate health source")
    for identifier in sorted(required):
        source = by_id.get(identifier)
        if (
            source is None
            or not source.complete
            or not source.hash_verified
            or source.fresh_until < at
        ):
            failures.append(f"{identifier}:missing_stale_or_corrupt")
        elif source.missing_fraction > max_missing or source.drift > max_drift:
            failures.append(f"{identifier}:distribution_change")
    return {
        "ready": not failures,
        "failures": failures,
        "valid_until": min(
            (s.fresh_until for s in sources if s.source_id in required), default=at
        ).isoformat(),
    }


def deadline(session: date, trading_sessions: set[date]) -> datetime:
    if session not in trading_sessions:
        raise ValueError("not a declared trading session")
    return datetime.combine(session, time(8, 30), ZoneInfo("America/New_York")).astimezone(UTC)


def claim_run(engine: Engine, session: date, config_hash: str, synthetic: bool) -> str | None:
    identifier = uuid4()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO scheduled_runs(run_id,session_date,config_hash,status,synthetic) "
                "VALUES(:id,:day,:config,'running',:synthetic) ON CONFLICT DO NOTHING "
                "RETURNING run_id"
            ),
            {"id": identifier, "day": session, "config": config_hash, "synthetic": synthetic},
        ).first()
    return str(identifier) if row else None


def finish_run(engine: Engine, run_id: str, health: dict[str, object]) -> None:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "UPDATE scheduled_runs SET status=:status,readiness=CAST(:body AS jsonb),"
                "completed_at=clock_timestamp() WHERE run_id=:id AND status='running' "
                "RETURNING run_id"
            ),
            {
                "id": run_id,
                "status": "complete" if health.get("ready") is True else "failed",
                "body": json.dumps(health, allow_nan=False),
            },
        ).first()
        if row is None:
            raise ValueError("run is absent or already terminal")


def config_digest(configuration: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(configuration, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def required_session(
    sessions: tuple[date, ...], calendar_valid_through: date, at: datetime
) -> date:
    """Most recent declared session whose New York preparation deadline has passed."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("aware readiness time required")
    if not sessions or tuple(sorted(set(sessions))) != sessions:
        raise ValueError("ordered distinct trading sessions required")
    if calendar_valid_through < at.astimezone(ZoneInfo("America/New_York")).date():
        raise ValueError("trading calendar coverage expired")
    if sessions[-1] > calendar_valid_through:
        raise ValueError("session outside declared calendar coverage")
    declared = set(sessions)
    due = [day for day in sessions if deadline(day, declared) <= at]
    if not due:
        raise ValueError("no completed preparation deadline in calendar")
    return due[-1]
