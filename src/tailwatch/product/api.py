"""Tenant-scoped API. No query-string secrets, draft leaks, or implicit promotion."""

import hashlib
import secrets
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import Field
from sqlalchemy import Connection, Engine, text

from tailwatch.operations.release import allowed_model
from tailwatch.product.alerts import append_event, events, verify_history
from tailwatch.product.messages import LinkSigner
from tailwatch.sources.policy import Contract, NonEmpty

if TYPE_CHECKING:
    from tailwatch.product.live import LiveFeeds


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


class SubscriptionToken(Contract):
    token: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class ReviewInput(Contract):
    kind: Literal["review", "approved", "vetoed", "superseded"]
    note: NonEmpty = Field(max_length=2000)
    human_confirmed: bool


class OutcomeInput(Contract):
    state: Literal["pending", "announced", "withdrawn", "no_deal"]
    evidence: tuple[NonEmpty, ...] = Field(default=(), max_length=20)
    coverage_complete: bool = False
    note: NonEmpty = Field(max_length=2000)
    human_confirmed: bool


# Customer-visible episodes: tenant-owned, currently approved, and either explicitly
# synthetic or produced by the active release's exact model. Outcomes stay separate.
VISIBLE = """
  SELECT a.episode_id,a.visitor,a.host,a.created_at,a.synthetic,e.body,e.sequence,
         o.body AS outcome
  FROM alert_episodes a JOIN LATERAL (
    SELECT kind,body,sequence FROM alert_events WHERE episode_id=a.episode_id
    AND kind<>'outcome' ORDER BY sequence DESC LIMIT 1
  ) e ON e.kind='approved' LEFT JOIN LATERAL (
    SELECT body FROM alert_events WHERE episode_id=a.episode_id
    AND kind='outcome' ORDER BY sequence DESC LIMIT 1
  ) o ON true
  WHERE a.tenant_id=:tenant AND (a.synthetic OR a.model_hash=CAST(:production_model AS text))
  AND (CAST(:after AS uuid) IS NULL OR a.episode_id>CAST(:after AS uuid))
"""


def _public_item(row: Any) -> dict[str, Any]:
    return {
        "episode_id": str(row["episode_id"]),
        "visitor": str(row["visitor"]),
        "host": str(row["host"]),
        "created_at": row["created_at"].isoformat(),
        "synthetic": row["synthetic"],
        "public": row["body"].get("public", {}),
        "approval_sequence": row["sequence"],
        "outcome": (row["outcome"] or {}).get("state", "pending"),
    }


def create_app(
    engine: Engine,
    *,
    production_enabled: bool = False,
    signer: LinkSigner | None = None,
    reference_source: str | None = None,
    live: "LiveFeeds | None" = None,
    public_view: bool = False,
    public_tenant: UUID | None = None,
) -> FastAPI:
    # A public deployment serves no interactive API documentation.
    hidden = {"docs_url": None, "redoc_url": None, "openapi_url": None} if public_view else {}
    app = FastAPI(title="Tailwatch", version="0.1.0", **hidden)  # type: ignore[arg-type]

    def visible(
        principal: "Principal",
        condition: str,
        params: dict[str, Any],
        limit: int,
        after: UUID | None,
    ) -> dict[str, Any]:
        model = allowed_model(engine, principal.tenant) if production_enabled else None
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    text(VISIBLE + condition + " ORDER BY a.episode_id LIMIT :limit"),
                    params
                    | {
                        "tenant": principal.tenant,
                        "production_model": model,
                        "after": after,
                        "limit": limit + 1,
                    },
                )
                .mappings()
                .all()
            )
        return {
            "rows": rows[:limit],
            "next_cursor": str(rows[limit - 1]["episode_id"]) if len(rows) > limit else None,
        }

    def link_signer() -> LinkSigner:
        if signer is None:
            raise HTTPException(503, "Recipient links are not configured")
        return signer

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

    def reader(authorization: Annotated[str | None, Header()] = None) -> Principal:
        """Read-only routes: a key's own tenant, or the operator's published workspace."""
        if authorization is None and public_tenant is not None:
            return Principal(tenant=public_tenant, scope="customer", key_hash="public-view")
        return authenticate(authorization)

    def analyst(principal: Annotated[Principal, Depends(authenticate)]) -> Principal:
        if principal.scope != "analyst":
            raise HTTPException(403, "Analyst authorization required")
        return principal

    @app.post("/subscriptions/{subscription}/verify")
    def verify_destination(subscription: UUID, body: SubscriptionToken) -> dict[str, bool]:
        from tailwatch.product.delivery import verify_subscription

        with engine.begin() as conn:
            if not verify_subscription(conn, subscription, body.token):
                raise HTTPException(404, "Invalid, expired or consumed verification")
        return {"verified": True}

    @app.post("/subscriptions/{subscription}/unsubscribe")
    def unsubscribe_destination(subscription: UUID, body: SubscriptionToken) -> dict[str, bool]:
        from tailwatch.product.delivery import unsubscribe

        with engine.begin() as conn:
            if not unsubscribe(conn, subscription, body.token):
                raise HTTPException(404, "Invalid subscription token")
        return {"active": False}

    @app.post("/verifications/{message}/confirm")
    def confirm_verification_link(message: UUID, body: SubscriptionToken) -> dict[str, bool]:
        from tailwatch.product.delivery import confirm_verification

        keys = link_signer()
        with engine.begin() as conn:
            if not confirm_verification(conn, message, body.token, keys):
                raise HTTPException(404, "Invalid, expired or consumed verification")
        return {"verified": True}

    @app.post("/deliveries/{delivery}/unsubscribe")
    def unsubscribe_link(delivery: UUID, body: SubscriptionToken) -> dict[str, bool]:
        from tailwatch.product.delivery import unsubscribe_delivery

        keys = link_signer()
        with engine.begin() as conn:
            if not unsubscribe_delivery(conn, delivery, body.token, keys):
                raise HTTPException(404, "Invalid unsubscribe link")
        return {"active": False}

    @app.get("/subscriptions")
    def subscriptions(
        principal: Annotated[Principal, Depends(authenticate)],
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        after: UUID | None = None,
    ) -> dict[str, Any]:
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    text(
                        "SELECT subscription_id,email,verified,active FROM subscriptions "
                        "WHERE tenant_id=:tenant AND (CAST(:after AS uuid) IS NULL "
                        "OR subscription_id>CAST(:after AS uuid)) "
                        "ORDER BY subscription_id LIMIT :limit"
                    ),
                    {"tenant": principal.tenant, "after": after, "limit": limit + 1},
                )
                .mappings()
                .all()
            )
        return {
            "items": [dict(row) for row in rows[:limit]],
            "next_cursor": str(rows[limit - 1]["subscription_id"]) if len(rows) > limit else None,
        }

    @app.delete("/subscriptions/{subscription}")
    def disable_subscription(
        subscription: UUID, principal: Annotated[Principal, Depends(authenticate)]
    ) -> dict[str, bool]:
        with engine.begin() as conn:
            found = conn.execute(
                text(
                    "UPDATE subscriptions SET active=false "
                    "WHERE subscription_id=:id AND tenant_id=:tenant RETURNING subscription_id"
                ),
                {"id": subscription, "tenant": principal.tenant},
            ).first()
            if found is None:
                raise HTTPException(404, "Subscription not found")
        return {"active": False}

    @app.get("/live/status")
    def live_status() -> dict[str, Any]:
        # Public: says only whether research feeds are configured, never their data.
        status: dict[str, Any] = {"enabled": live is not None}
        if live is not None:
            status |= live.enabled() | {"open": public_view}
        status["public_alerts"] = public_tenant is not None
        return status

    def live_viewer(authorization: Annotated[str | None, Header()] = None) -> None:
        # Public view serves the research feeds openly; otherwise a key is required.
        if not public_view:
            authenticate(authorization)

    @app.get("/live/company/{cik}", dependencies=[Depends(live_viewer)])
    def live_company(cik: str) -> dict[str, Any]:
        result = live.company(cik) if live is not None else None
        if result is None:
            raise HTTPException(404, "Only companies shown on this site can be looked up")
        return result

    @app.get("/live/traffic", dependencies=[Depends(live_viewer)])
    def live_traffic() -> dict[str, Any]:
        if live is None or not live.enabled()["traffic"]:
            raise HTTPException(404, "Live air traffic is not configured")
        return live.traffic()

    @app.get("/live/filings", dependencies=[Depends(live_viewer)])
    def live_filings() -> dict[str, Any]:
        if live is None or not live.enabled()["filings"]:
            raise HTTPException(404, "Live SEC filings are not configured")
        return live.filings()

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "production_enabled": bool(production_enabled and allowed_model(engine)),
        }

    @app.get("/alerts")
    def alerts(
        principal: Annotated[Principal, Depends(reader)],
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        after: UUID | None = None,
    ) -> dict[str, Any]:
        page = visible(principal, "", {}, limit, after)
        return {
            "items": [_public_item(r) for r in page["rows"]],
            "next_cursor": page["next_cursor"],
        }

    @app.get("/alerts/{episode}")
    def alert(episode: UUID, principal: Annotated[Principal, Depends(reader)]) -> dict[str, Any]:
        page = visible(principal, " AND a.episode_id=:episode", {"episode": episode}, 1, None)
        if not page["rows"]:
            raise HTTPException(404, "Alert not found")
        return _public_item(page["rows"][0])

    @app.get("/pairs/{visitor}/{host}")
    def pair_history(
        visitor: UUID,
        host: UUID,
        principal: Annotated[Principal, Depends(reader)],
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        after: UUID | None = None,
    ) -> dict[str, Any]:
        if visitor == host:
            raise HTTPException(422, "A pair needs two different companies")
        pair = "/".join(sorted((str(visitor), str(host))))
        page = visible(principal, " AND a.pair_key=:pair", {"pair": pair}, limit, after)
        return {
            "visitor": str(visitor),
            "host": str(host),
            # Episodes keep their first direction; the reverse hypothesis is labelled.
            "items": [
                _public_item(r)
                | {"direction": "as_requested" if r["visitor"] == visitor else "reverse"}
                for r in page["rows"]
            ],
            "next_cursor": page["next_cursor"],
        }

    @app.get("/companies/resolve")
    def resolve_company(
        principal: Annotated[Principal, Depends(authenticate)],
        ticker: Annotated[str, Query(pattern=r"^[A-Z0-9][A-Z0-9.-]{0,11}$")],
        exchange: Annotated[str, Query(pattern=r"^[A-Z0-9]{2,10}$")],
        at: datetime | None = None,
    ) -> dict[str, Any]:
        from tailwatch.db.clock import database_now
        from tailwatch.entities.repository import AmbiguousTicker, EntityRepository

        if reference_source is None:
            raise HTTPException(503, "Reference identity source is not configured")
        if at is not None and (at.tzinfo is None or at.utcoffset() is None):
            raise HTTPException(422, "Timezone-aware date required")
        with engine.connect() as conn:
            known = database_now(conn)
            effective = at or known
            if effective > known:
                raise HTTPException(422, "Future identities are not resolved")
            try:
                company = EntityRepository(conn).resolve_company(
                    ticker,
                    exchange,
                    source_id=reference_source,
                    cutoff=known,
                    effective_at=effective,
                )
            except AmbiguousTicker as error:
                raise HTTPException(
                    409, "Ticker is ambiguous on that date; use a company identifier"
                ) from error
        if company is None:
            raise HTTPException(404, "No active security used that ticker on that date")
        return {
            "company_id": str(company),
            "ticker": ticker,
            "exchange": exchange,
            "effective_at": effective.isoformat(),
            "known_as_of": known.isoformat(),
        }

    @app.get("/companies/{company}/signals")
    def company_signals(
        company: UUID,
        principal: Annotated[Principal, Depends(reader)],
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        after: UUID | None = None,
    ) -> dict[str, Any]:
        page = visible(
            principal,
            " AND (a.visitor=:company OR a.host=:company)",
            {"company": company},
            limit,
            after,
        )
        return {
            "company_id": str(company),
            "items": [
                _public_item(r)
                | {
                    "role": "visitor" if r["visitor"] == company else "host",
                    "counterpart": str(r["host"] if r["visitor"] == company else r["visitor"]),
                }
                for r in page["rows"]
            ],
            "next_cursor": page["next_cursor"],
        }

    @app.get("/alerts/{episode}/history")
    def history(episode: UUID, principal: Annotated[Principal, Depends(reader)]) -> dict[str, Any]:
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
        principal: Annotated[Principal, Depends(reader)],
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

    @app.post("/analyst/alerts/{episode}/outcome")
    def record_outcome(
        episode: UUID, body: OutcomeInput, principal: Annotated[Principal, Depends(analyst)]
    ) -> dict[str, str]:
        if not body.human_confirmed:
            raise HTTPException(422, "Human evidence review required")
        with engine.begin() as conn:
            if not events(conn, principal.tenant, episode):
                raise HTTPException(404, "Alert not found")
            try:
                # No "public" key: later review transitions must never copy outcome text.
                digest = append_event(
                    conn,
                    tenant=principal.tenant,
                    episode=episode,
                    kind="outcome",
                    actor=principal.key_hash,
                    body={
                        "state": body.state,
                        "evidence": list(body.evidence),
                        "coverage_complete": body.coverage_complete,
                        "note": body.note,
                        "human_confirmed": True,
                    },
                )
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
        return {"event_hash": digest}

    @app.get("/analyst/alerts")
    def review_queue(
        principal: Annotated[Principal, Depends(analyst)],
        state: Literal["draft", "review", "approved", "vetoed", "superseded"] = "draft",
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        after: UUID | None = None,
    ) -> dict[str, Any]:
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    text("""
                      SELECT a.episode_id,a.visitor,a.host,a.created_at,a.synthetic,a.model_hash,
                             a.feature_run_hash,e.sequence,e.recorded_at,e.body
                      FROM alert_episodes a JOIN LATERAL (
                        SELECT kind,sequence,recorded_at,body FROM alert_events
                        WHERE episode_id=a.episode_id AND kind<>'outcome'
                        ORDER BY sequence DESC LIMIT 1
                      ) e ON e.kind=:state WHERE a.tenant_id=:tenant
                      AND (CAST(:after AS uuid) IS NULL OR a.episode_id>CAST(:after AS uuid))
                      ORDER BY a.episode_id LIMIT :limit
                    """),
                    {
                        "tenant": principal.tenant,
                        "state": state,
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
                    "model_hash": r["model_hash"],
                    "feature_run_hash": r["feature_run_hash"],
                    "state": state,
                    "sequence": r["sequence"],
                    "updated_at": r["recorded_at"].isoformat(),
                    "body": r["body"],
                }
                for r in rows[:limit]
            ],
            "next_cursor": str(rows[limit - 1]["episode_id"]) if len(rows) > limit else None,
        }

    @app.get("/analyst/alerts/{episode}")
    def review_history(
        episode: UUID, principal: Annotated[Principal, Depends(analyst)]
    ) -> dict[str, Any]:
        with engine.connect() as conn:
            history = events(conn, principal.tenant, episode)
        if not history:
            raise HTTPException(404, "Alert not found")
        try:
            head: str | None = verify_history(history)
        except ValueError:
            head = None
        return {
            "episode_id": str(episode),
            "chain_verified": head is not None,
            "chain_head": head,
            "items": [
                {
                    "sequence": e["sequence"],
                    "kind": e["kind"],
                    "actor": e["actor"],
                    "recorded_at": e["recorded_at"].isoformat(),
                    "event_hash": e["event_hash"],
                    "body": e["body"],
                }
                for e in history
            ],
        }

    return app
