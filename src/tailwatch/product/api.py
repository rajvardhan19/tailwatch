"""Tenant-scoped API. No query-string secrets, draft leaks, or implicit promotion."""

import hashlib
import secrets
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import Field
from sqlalchemy import Connection, Engine, text

from tailwatch.operations.release import allowed_model
from tailwatch.product.alerts import append_event, events
from tailwatch.sources.policy import Contract, NonEmpty


class Principal(Contract):
    tenant: UUID
    scope: Literal["customer", "analyst"]
    key_hash: str


def issue_key(conn: Connection, tenant: UUID, scope: Literal["customer", "analyst"]) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        text("INSERT INTO api_keys(key_hash,tenant_id,scope) VALUES(:hash,:tenant,:scope)"),
        {"hash": hashlib.sha256(token.encode()).hexdigest(), "tenant": tenant, "scope": scope},
    )
    return token


class WatchlistInput(Contract):
    companies: tuple[UUID, ...] = Field(max_length=100)


class ReviewInput(Contract):
    kind: Literal["review", "approved", "vetoed", "superseded"]
    note: NonEmpty = Field(max_length=2000)
    human_confirmed: bool


def create_app(engine: Engine, *, production_enabled: bool = False) -> FastAPI:
    app = FastAPI(title="Tailwatch", version="0.1.0")

    def authenticate(authorization: Annotated[str | None, Header()] = None) -> Principal:
        if (
            authorization is None
            or not authorization.startswith("Bearer ")
            or len(authorization) > 200
        ):
            raise HTTPException(401, "Bearer API key required")
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT tenant_id,scope FROM api_keys WHERE key_hash=:hash AND NOT revoked"
                    ),
                    {"hash": digest},
                )
                .mappings()
                .first()
            )
        if row is None:
            raise HTTPException(401, "Invalid or revoked API key")
        return Principal(tenant=row["tenant_id"], scope=row["scope"], key_hash=digest)

    def analyst(principal: Annotated[Principal, Depends(authenticate)]) -> Principal:
        if principal.scope != "analyst":
            raise HTTPException(403, "Analyst authorization required")
        return principal

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "production_enabled": bool(production_enabled and allowed_model(engine)),
        }

    @app.get("/alerts")
    def alerts(
        principal: Annotated[Principal, Depends(authenticate)],
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        after: UUID | None = None,
    ) -> dict[str, Any]:
        model = allowed_model(engine, principal.tenant) if production_enabled else None
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    text("""
              SELECT a.episode_id,a.visitor,a.host,a.created_at,a.synthetic,e.body,e.sequence
              FROM alert_episodes a JOIN LATERAL (
                SELECT kind,body,sequence FROM alert_events WHERE episode_id=a.episode_id
                AND kind<>'outcome' ORDER BY sequence DESC LIMIT 1
              ) e ON true WHERE a.tenant_id=:tenant AND e.kind='approved'
              AND (a.synthetic OR a.model_hash=CAST(:production_model AS text))
              AND (CAST(:after AS uuid) IS NULL OR a.episode_id>CAST(:after AS uuid))
              ORDER BY a.episode_id LIMIT :limit
            """),
                    {
                        "tenant": principal.tenant,
                        "production_model": model,
                        "after": after,
                        "limit": limit + 1,
                    },
                )
                .mappings()
                .all()
            )
        output = [
            {
                "episode_id": str(r["episode_id"]),
                "visitor": str(r["visitor"]),
                "host": str(r["host"]),
                "created_at": r["created_at"].isoformat(),
                "synthetic": r["synthetic"],
                "public": r["body"].get("public", {}),
                "approval_sequence": r["sequence"],
            }
            for r in rows[:limit]
        ]
        return {
            "items": output,
            "next_cursor": str(rows[limit - 1]["episode_id"]) if len(rows) > limit else None,
        }

    @app.get("/alerts/{episode}/history")
    def history(
        episode: UUID, principal: Annotated[Principal, Depends(authenticate)]
    ) -> dict[str, Any]:
        with engine.connect() as conn:
            meta = conn.execute(
                text(
                    "SELECT synthetic,model_hash FROM alert_episodes "
                    "WHERE episode_id=:id AND tenant_id=:tenant"
                ),
                {"id": episode, "tenant": principal.tenant},
            ).first()
            rows = events(conn, principal.tenant, episode)
        if (
            meta is None
            or (
                not meta[0]
                and (not production_enabled or allowed_model(engine, principal.tenant) != meta[1])
            )
            or not any(e["kind"] == "approved" for e in rows)
        ):
            raise HTTPException(404, "Alert not found")
        return {
            "items": [
                {
                    "sequence": e["sequence"],
                    "kind": e["kind"],
                    "recorded_at": e["recorded_at"].isoformat(),
                    "public": e["body"].get("public", {}),
                    "outcome": e["body"].get("state") if e["kind"] == "outcome" else None,
                }
                for e in rows
                if e["kind"] in {"approved", "superseded", "outcome"}
            ]
        }

    @app.get("/watchlist")
    def watchlist(principal: Annotated[Principal, Depends(authenticate)]) -> dict[str, Any]:
        with engine.connect() as conn:
            ids = conn.execute(
                text(
                    "SELECT company_id FROM watchlist WHERE tenant_id=:tenant "
                    "ORDER BY company_id LIMIT 100"
                ),
                {"tenant": principal.tenant},
            ).scalars()
            return {"companies": [str(i) for i in ids]}

    @app.get("/track-record")
    def track_record(
        principal: Annotated[Principal, Depends(authenticate)],
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        after: UUID | None = None,
    ) -> dict[str, Any]:
        model = allowed_model(engine, principal.tenant) if production_enabled else None
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    text("""
                SELECT a.episode_id,a.visitor,a.host,a.created_at,a.synthetic,p.body,
                       o.body AS outcome
                FROM alert_episodes a JOIN LATERAL (
                    SELECT body FROM alert_events WHERE episode_id=a.episode_id
                    AND kind='approved' ORDER BY sequence LIMIT 1
                ) p ON true LEFT JOIN LATERAL (
                    SELECT body FROM alert_events WHERE episode_id=a.episode_id
                    AND kind='outcome' ORDER BY sequence DESC LIMIT 1
                ) o ON true WHERE a.tenant_id=:tenant
                AND (a.synthetic OR a.model_hash=CAST(:model AS text))
                AND (CAST(:after AS uuid) IS NULL OR a.episode_id>CAST(:after AS uuid))
                ORDER BY a.episode_id LIMIT :limit
            """),
                    {
                        "tenant": principal.tenant,
                        "model": model,
                        "after": after,
                        "limit": limit + 1,
                    },
                )
                .mappings()
                .all()
            )
        return {
            "items": [
                {
                    "episode_id": str(r["episode_id"]),
                    "visitor": str(r["visitor"]),
                    "host": str(r["host"]),
                    "created_at": r["created_at"].isoformat(),
                    "synthetic": r["synthetic"],
                    "public": r["body"].get("public", {}),
                    "outcome": (r["outcome"] or {}).get("state", "pending"),
                }
                for r in rows[:limit]
            ],
            "next_cursor": str(rows[limit - 1]["episode_id"]) if len(rows) > limit else None,
        }

    @app.put("/watchlist")
    def replace_watchlist(
        body: WatchlistInput, principal: Annotated[Principal, Depends(authenticate)]
    ) -> dict[str, int]:
        with engine.begin() as conn:
            conn.execute(
                text("SELECT tenant_id FROM tenants WHERE tenant_id=:id FOR UPDATE"),
                {"id": principal.tenant},
            )
            for company in set(body.companies):
                if not conn.execute(
                    text("SELECT 1 FROM entities WHERE entity_id=:id AND kind='company'"),
                    {"id": company},
                ).first():
                    raise HTTPException(422, "Unknown company identity")
            conn.execute(
                text("DELETE FROM watchlist WHERE tenant_id=:tenant"), {"tenant": principal.tenant}
            )
            for company in set(body.companies):
                conn.execute(
                    text("INSERT INTO watchlist(tenant_id,company_id) VALUES(:tenant,:company)"),
                    {"tenant": principal.tenant, "company": company},
                )
        return {"count": len(set(body.companies))}

    @app.delete("/watchlist/{company}")
    def remove_watch(
        company: UUID, principal: Annotated[Principal, Depends(authenticate)]
    ) -> dict[str, bool]:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM watchlist WHERE tenant_id=:tenant AND company_id=:company"),
                {"tenant": principal.tenant, "company": company},
            )
        return {"removed": True}

    @app.post("/keys/revoke-self")
    def revoke(principal: Annotated[Principal, Depends(authenticate)]) -> dict[str, bool]:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE api_keys SET revoked=true WHERE key_hash=:hash"),
                {"hash": principal.key_hash},
            )
        return {"revoked": True}

    @app.post("/analyst/alerts/{episode}/review")
    def review(
        episode: UUID, body: ReviewInput, principal: Annotated[Principal, Depends(analyst)]
    ) -> dict[str, str]:
        if not body.human_confirmed:
            raise HTTPException(422, "Human evidence review required")
        with engine.begin() as conn:
            history = events(conn, principal.tenant, episode)
            if not history:
                raise HTTPException(404, "Alert not found")
            # Approval copies only already prepared public content; arbitrary
            # analyst notes, internal evidence and drafts never become customer prose.
            public: dict[str, Any] = next(
                (e["body"]["public"] for e in reversed(history) if "public" in e["body"]), {}
            )
            try:
                digest = append_event(
                    conn,
                    tenant=principal.tenant,
                    episode=episode,
                    kind=body.kind,
                    actor=principal.key_hash,
                    body={"note": body.note, "human_confirmed": True, "public": public},
                )
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
        return {"event_hash": digest}

    return app
