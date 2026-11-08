"""Production guard: a switch alone cannot bypass research and readiness evidence."""

import hashlib
import os
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy import Connection, Engine, text

from tailwatch.operations.runtime import required_session
from tailwatch.sources.policy import Contract, NonEmpty


class ReleaseManifest(Contract):
    application_commit: NonEmpty
    model_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_version: Literal["0008"] = "0008"
    gate_evidence: dict[str, str]
    operator: NonEmpty
    rights_valid_until: AwareDatetime
    expires_at: AwareDatetime
    rollout_limit: int = Field(ge=1, le=100)
    daily_email_limit: int = Field(ge=1, le=100)
    shadow_run_ids: tuple[str, ...] = Field(min_length=10, max_length=10)
    trading_sessions: tuple[date, ...] = Field(min_length=10)
    calendar_valid_through: date
    approved_tenants: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    restore_drill_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    synthetic: Literal[False] = False

    @model_validator(mode="after")
    def gates(self) -> "ReleaseManifest":
        if set(self.gate_evidence) != {"G0", "G1", "G2", "G3", "G4"} or any(
            not value.strip() for value in self.gate_evidence.values()
        ):
            raise ValueError("all G0-G4 evidence references required")
        if len(set(self.shadow_run_ids)) != len(self.shadow_run_ids):
            raise ValueError("distinct shadow runs required")
        if tuple(sorted(set(self.trading_sessions))) != self.trading_sessions:
            raise ValueError("ordered distinct trading sessions required")
        if (
            self.calendar_valid_through
            < self.expires_at.astimezone(ZoneInfo("America/New_York")).date()
            or self.trading_sessions[-1] > self.calendar_valid_through
        ):
            raise ValueError("calendar coverage must span release lifetime and all sessions")
        if (
            len(set(self.approved_tenants)) != len(self.approved_tenants)
            or len(self.approved_tenants) > self.rollout_limit
        ):
            raise ValueError("approved tenants exceed limited rollout")
        return self

    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


def _validate(conn: Connection, manifest: ReleaseManifest) -> None:
    schema = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    if schema != manifest.schema_version:
        raise ValueError("release schema is incompatible")
    now = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
    if min(manifest.expires_at, manifest.rights_valid_until) <= now:
        raise ValueError("release or source rights expired")
    evaluation = (
        conn.execute(
            text(
                "SELECT r.result,e.manifest,a.model_hash FROM research_evaluations r "
                "JOIN sealed_test_access a USING(dataset_hash) JOIN research_experiments e "
                "USING(experiment_hash) "
                "WHERE r.dataset_hash=:id"
            ),
            {"id": manifest.dataset_hash},
        )
        .mappings()
        .first()
    )
    if (
        evaluation is None
        or evaluation["result"].get("decision") != "PASS"
        or evaluation["manifest"].get("synthetic") is not False
        or evaluation["model_hash"] != manifest.model_hash
    ):
        raise ValueError("real sealed-test PASS required for this exact model")
    runs = (
        conn.execute(
            text(
                "SELECT session_date,status,synthetic,config_hash,readiness FROM scheduled_runs "
                "WHERE run_id=ANY(CAST(:ids AS uuid[])) ORDER BY session_date"
            ),
            {"ids": list(manifest.shadow_run_ids)},
        )
        .mappings()
        .all()
    )
    if (
        len(runs) != len(manifest.shadow_run_ids)
        or len({r["session_date"] for r in runs}) < 10
        or any(
            r["status"] != "complete"
            or r["synthetic"]
            or r["config_hash"] != manifest.config_hash
            or r["readiness"].get("ready") is not True
            for r in runs
        )
    ):
        raise ValueError("ten successful real shadow sessions required")
    shadow_end = runs[-1]["session_date"]
    if shadow_end > required_session(
        manifest.trading_sessions, manifest.calendar_valid_through, now
    ):
        raise ValueError("shadow session preparation deadline has not passed")
    shadow_calendar = [s for s in manifest.trading_sessions if s <= shadow_end]
    if [r["session_date"] for r in runs] != shadow_calendar[-10:]:
        raise ValueError("shadow runs must cover ten consecutive declared trading sessions")
    if not 0 <= (now.astimezone(ZoneInfo("America/New_York")).date() - shadow_end).days <= 7:
        raise ValueError("shadow evidence is stale or future dated")

    _validate_current_run(conn, manifest, now)


def _validate_current_run(conn: Connection, manifest: ReleaseManifest, now: datetime) -> None:
    session = required_session(manifest.trading_sessions, manifest.calendar_valid_through, now)
    row = (
        conn.execute(
            text(
                "SELECT status,synthetic,readiness,completed_at FROM scheduled_runs "
                "WHERE session_date=:day AND config_hash=:config"
            ),
            {"day": session, "config": manifest.config_hash},
        )
        .mappings()
        .first()
    )
    if (
        row is None
        or row["status"] != "complete"
        or row["synthetic"]
        or row["completed_at"] is None
        or row["completed_at"] > now
        or not isinstance(row["readiness"], dict)
        or row["readiness"].get("ready") is not True
    ):
        raise ValueError("latest due session requires a completed real ready run")
    valid_until = row["readiness"].get("valid_until")
    if not isinstance(valid_until, str):
        raise ValueError("source readiness expiry required")
    expiry = datetime.fromisoformat(valid_until)
    if expiry.tzinfo is None or expiry.utcoffset() is None or expiry <= now:
        raise ValueError("source readiness expired")


def register_release(engine: Engine, manifest: ReleaseManifest) -> str:
    with engine.begin() as conn:
        _validate(conn, manifest)
        conn.execute(
            text(
                "INSERT INTO releases(release_hash,manifest,model_hash,dataset_hash) "
                "VALUES(:hash,CAST(:body AS jsonb),:model,:data) ON CONFLICT DO NOTHING"
            ),
            {
                "hash": manifest.digest(),
                "body": manifest.model_dump_json(),
                "model": manifest.model_hash,
                "data": manifest.dataset_hash,
            },
        )
    return manifest.digest()


def activate(engine: Engine, release_hash: str, actor: str) -> None:
    if not actor.strip():
        raise ValueError("operator identity required")
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT manifest FROM releases WHERE release_hash=:id"), {"id": release_hash}
        ).scalar_one()
        manifest = ReleaseManifest.model_validate(row)
        _validate(conn, manifest)
        if (
            os.environ.get("TAILWATCH_APPLICATION_COMMIT") != manifest.application_commit
            or os.environ.get("TAILWATCH_CONFIG_HASH") != manifest.config_hash
        ):
            raise ValueError("deployed application/configuration does not match release")
        conn.execute(
            text("UPDATE runtime_control SET killed=false,active_release=:id WHERE singleton"),
            {"id": release_hash},
        )
        conn.execute(
            text(
                "INSERT INTO runtime_events(actor,action,release_hash) "
                "VALUES(:actor,'activate',:id)"
            ),
            {"actor": actor, "id": release_hash},
        )


def kill(engine: Engine, actor: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("UPDATE runtime_control SET killed=true WHERE singleton"))
        conn.execute(
            text("INSERT INTO runtime_events(actor,action) VALUES(:actor,'kill')"), {"actor": actor}
        )


def allowed_model(engine: Engine, tenant: UUID | None = None) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT r.manifest FROM runtime_control c JOIN releases r ON "
                "r.release_hash=c.active_release "
                "WHERE c.singleton AND NOT c.killed"
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        try:
            manifest = ReleaseManifest.model_validate(row)
        except ValueError:
            return None
        if (
            os.environ.get("TAILWATCH_APPLICATION_COMMIT") != manifest.application_commit
            or os.environ.get("TAILWATCH_CONFIG_HASH") != manifest.config_hash
        ):
            return None
        if tenant is not None and tenant not in manifest.approved_tenants:
            return None
        try:
            _validate(conn, manifest)
        except ValueError:
            return None
        return manifest.model_hash


def reserve_live_delivery(engine: Engine, tenant: UUID, model_hash: str) -> None:
    from datetime import UTC, datetime

    from tailwatch.operations.runtime import DurableBudget
    from tailwatch.sources.policy import Source

    if allowed_model(engine, tenant) != model_hash:
        raise ValueError("production release disallows this model or tenant")
    with engine.connect() as conn:
        body = conn.execute(
            text(
                "SELECT r.manifest FROM runtime_control c JOIN releases r "
                "ON r.release_hash=c.active_release WHERE NOT c.killed"
            )
        ).scalar_one()
    manifest = ReleaseManifest.model_validate(body)
    budget = DurableBudget(
        engine,
        Source(
            source_id="resend",
            kind="live",
            capabilities=("email",),
            latency_seconds=0,
            max_requests=manifest.daily_email_limit,
            max_cost_microusd=0,
        ),
        datetime.now(UTC).date(),
    )
    budget.reserve(0)
