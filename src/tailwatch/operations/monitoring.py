"""Read-only operational status with explicit, operator-declared alert thresholds.

This reports what the database shows. It does not discover provider outages on its
own and it never repairs state; operators act through the audited commands.
"""

from typing import Any

from pydantic import Field
from sqlalchemy import Engine, text

from tailwatch.operations.release import allowed_model
from tailwatch.sources.policy import Contract


class Thresholds(Contract):
    pending_minutes: int = Field(default=60, ge=1)
    sending_minutes: int = Field(default=30, ge=1)
    review_hours: int = Field(default=24, ge=1)
    running_minutes: int = Field(default=120, ge=1)
    budget_fraction: float = Field(default=0.9, gt=0, le=1, allow_inf_nan=False)


def _states(engine: Engine, table: str) -> dict[str, dict[str, float | int]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT state,count(*) AS n,"
                f"extract(epoch FROM clock_timestamp()-min(updated_at)) AS oldest "
                f"FROM {table} GROUP BY state"
            )
        ).all()
    return {r.state: {"count": r.n, "oldest_seconds": float(r.oldest)} for r in rows}


def ops_status(engine: Engine, thresholds: Thresholds) -> dict[str, Any]:
    alerts: list[str] = []
    with engine.connect() as conn:
        control = (
            conn.execute(text("SELECT killed,active_release FROM runtime_control WHERE singleton"))
            .mappings()
            .one()
        )
        schema = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        runs = (
            conn.execute(
                text(
                    "SELECT run_id,session_date,status,synthetic,started_at,completed_at,"
                    "extract(epoch FROM clock_timestamp()-started_at) AS age "
                    "FROM scheduled_runs ORDER BY started_at DESC LIMIT 10"
                )
            )
            .mappings()
            .all()
        )
        stuck = conn.execute(
            text(
                "SELECT count(*) FROM scheduled_runs WHERE status='running' "
                "AND started_at < clock_timestamp() - make_interval(mins => :minutes)"
            ),
            {"minutes": thresholds.running_minutes},
        ).scalar_one()
        backlog = conn.execute(
            text("""
              SELECT e.kind,count(*) AS n,
                     extract(epoch FROM clock_timestamp()-min(e.recorded_at)) AS oldest
              FROM alert_episodes a JOIN LATERAL (
                SELECT kind,recorded_at FROM alert_events WHERE episode_id=a.episode_id
                AND kind<>'outcome' ORDER BY sequence DESC LIMIT 1
              ) e ON e.kind IN ('draft','review') GROUP BY e.kind
            """)
        ).all()
        budgets = (
            conn.execute(
                text(
                    "SELECT source_id,requests,request_limit,cost,cost_limit FROM budget_accounts "
                    "WHERE budget_day=(clock_timestamp() AT TIME ZONE 'UTC')::date "
                    "ORDER BY source_id"
                )
            )
            .mappings()
            .all()
        )
    outbox, verification = (
        _states(engine, "notification_outbox"),
        _states(engine, "verification_messages"),
    )
    review = {r.kind: {"count": r.n, "oldest_seconds": float(r.oldest)} for r in backlog}

    if stuck:
        alerts.append(f"runs:{stuck}_running_beyond_{thresholds.running_minutes}m")
    if runs and runs[0]["status"] == "failed":
        alerts.append("runs:latest_failed")
    for name, states in (("outbox", outbox), ("verification", verification)):
        if states.get("pending", {}).get("oldest_seconds", 0) > thresholds.pending_minutes * 60:
            alerts.append(f"{name}:pending_beyond_{thresholds.pending_minutes}m")
        if states.get("sending", {}).get("oldest_seconds", 0) > thresholds.sending_minutes * 60:
            alerts.append(f"{name}:sending_abandoned_reconcile_required")
        if states.get("uncertain", {}).get("count", 0):
            alerts.append(f"{name}:uncertain_requires_resolution")
    for kind, item in review.items():
        if item["oldest_seconds"] > thresholds.review_hours * 3600:
            alerts.append(f"review:{kind}_beyond_{thresholds.review_hours}h")
    for budget in budgets:
        for used, limit in (("requests", "request_limit"), ("cost", "cost_limit")):
            if budget[limit] and budget[used] >= thresholds.budget_fraction * budget[limit]:
                alerts.append(f"budget:{budget['source_id']}:{used}_near_limit")
    return {
        "schema": schema,
        "killed": control["killed"],
        "active_release": control["active_release"],
        "eligible_model": allowed_model(engine),
        "runs": [
            {
                "run_id": str(r["run_id"]),
                "session": r["session_date"].isoformat(),
                "status": r["status"],
                "synthetic": r["synthetic"],
                "age_seconds": float(r["age"]),
            }
            for r in runs
        ],
        "outbox": outbox,
        "verification": verification,
        "review": review,
        "budgets": [dict(b) for b in budgets],
        "thresholds": thresholds.model_dump(),
        "alerts": alerts,
        "healthy": not alerts,
    }
