"""Canonical pair episodes and append-only, verifiable review/outcome history."""

import hashlib
import json
from datetime import timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from tailwatch.sources.objects import ObjectStore

Kind = Literal["draft", "review", "approved", "vetoed", "superseded", "outcome"]
TRANSITIONS = {
    None: {"draft"},
    "draft": {"review", "superseded"},
    "review": {"approved", "vetoed", "superseded"},
    "approved": {"superseded"},
    "vetoed": {"superseded"},
    "superseded": set(),
}


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def create_episode(
    conn: Connection,
    *,
    tenant: UUID,
    visitor: UUID,
    host: UUID,
    model_hash: str,
    feature_run_hash: str,
    synthetic: bool,
) -> UUID:
    if visitor == host:
        raise ValueError("self pair")
    pair = "/".join(sorted((str(visitor), str(host))))
    lock = int.from_bytes(
        hashlib.sha256(f"{tenant}:{pair}".encode()).digest()[:8], "big", signed=True
    )
    conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
    existing = conn.execute(
        text(
            "SELECT episode_id FROM alert_episodes WHERE tenant_id=:tenant "
            "AND pair_key=:pair AND created_at>clock_timestamp()-interval '180 days' "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"tenant": tenant, "pair": pair},
    ).scalar_one_or_none()
    if existing:
        return UUID(str(existing))
    episode = uuid4()
    conn.execute(
        text(
            "INSERT INTO alert_episodes(episode_id,tenant_id,visitor,host,pair_key,model_hash,"
            "feature_run_hash,synthetic) "
            "VALUES(:id,:tenant,:visitor,:host,:pair,:model,:run,:synthetic)"
        ),
        {
            "id": episode,
            "tenant": tenant,
            "visitor": visitor,
            "host": host,
            "pair": pair,
            "model": model_hash,
            "run": feature_run_hash,
            "synthetic": synthetic,
        },
    )
    return episode


def events(conn: Connection, tenant: UUID, episode: UUID) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            "SELECT e.* FROM alert_events e JOIN alert_episodes a USING(episode_id) "
            "WHERE a.tenant_id=:tenant AND a.episode_id=:id ORDER BY sequence"
        ),
        {"tenant": tenant, "id": episode},
    ).mappings()
    return [dict(r) for r in rows]


def append_event(
    conn: Connection, *, tenant: UUID, episode: UUID, kind: Kind, actor: str, body: dict[str, Any]
) -> str:
    row = (
        conn.execute(
            text(
                "SELECT * FROM alert_episodes WHERE tenant_id=:tenant AND episode_id=:id FOR UPDATE"
            ),
            {"tenant": tenant, "id": episode},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ValueError("episode not found")
    history = events(conn, tenant, episode)
    state = next((e["kind"] for e in reversed(history) if e["kind"] != "outcome"), None)
    if kind != "outcome" and kind not in TRANSITIONS.get(state, set()):
        raise ValueError("invalid review state transition")
    if not actor.strip():
        raise ValueError("review actor required")
    if kind == "outcome":
        if state not in {"approved", "superseded"} or body.get("state") not in {
            "pending",
            "announced",
            "withdrawn",
            "no_deal",
        }:
            raise ValueError("invalid outcome")
        now = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
        if body["state"] == "no_deal" and (
            now < row["created_at"] + timedelta(days=180)
            or body.get("coverage_complete") is not True
        ):
            raise ValueError("negative outcome lacks mature complete followup")
        if body["state"] != "pending" and not body.get("evidence"):
            raise ValueError("outcome requires evidence")
    sequence = len(history) + 1
    previous = history[-1]["event_hash"] if history else "0" * 64
    value = {
        "episode_id": str(episode),
        "sequence": sequence,
        "kind": kind,
        "actor": actor,
        "body": body,
        "previous_hash": previous,
    }
    digest = hashlib.sha256(canonical(value)).hexdigest()
    conn.execute(
        text(
            "INSERT INTO alert_events(episode_id,sequence,kind,actor,body,"
            "previous_hash,event_hash) "
            "VALUES(:id,:seq,:kind,:actor,CAST(:body AS jsonb),:previous,:hash)"
        ),
        {
            "id": episode,
            "seq": sequence,
            "kind": kind,
            "actor": actor,
            "body": json.dumps(body, allow_nan=False),
            "previous": previous,
            "hash": digest,
        },
    )
    return digest


def verify_history(history: list[dict[str, Any]]) -> str:
    previous = "0" * 64
    for i, event in enumerate(history, start=1):
        if event["sequence"] != i or event["previous_hash"] != previous:
            raise ValueError("broken event sequence")
        value = {k: event[k] for k in ("sequence", "kind", "actor", "body", "previous_hash")}
        value["episode_id"] = str(event["episode_id"])
        digest = hashlib.sha256(canonical(value)).hexdigest()
        if digest != event["event_hash"]:
            raise ValueError("event hash mismatch")
        previous = digest
    return previous


def checkpoint(
    conn: Connection, tenant: UUID, episode: UUID, independent_store: ObjectStore
) -> str:
    history = events(conn, tenant, episode)
    head = verify_history(history)
    return independent_store.put(
        canonical(
            {"tenant": str(tenant), "episode": str(episode), "sequence": len(history), "head": head}
        )
    )
