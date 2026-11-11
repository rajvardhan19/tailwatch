"""Canonical pair episodes and append-only, verifiable review/outcome history."""

import hashlib
import json
from datetime import UTC, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text

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
            "AND pair_key=:pair AND synthetic=:synthetic "
            "AND created_at>clock_timestamp()-interval '180 days' "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"tenant": tenant, "pair": pair, "synthetic": synthetic},
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
            "SELECT e.*,a.tenant_id,a.visitor,a.host,a.model_hash,a.feature_run_hash,a.synthetic,"
            "a.created_at AS episode_created_at FROM alert_events e "
            "JOIN alert_episodes a USING(episode_id) "
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
    verify_history(history)
    recorded_at = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
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
    value["recorded_at"] = recorded_at.astimezone(UTC).isoformat()
    value["episode_context"] = _context(dict(row), "created_at")
    digest = "v2:" + hashlib.sha256(canonical(value)).hexdigest()
    conn.execute(
        text(
            "INSERT INTO alert_events(episode_id,sequence,kind,actor,body,"
            "previous_hash,event_hash,recorded_at) "
            "VALUES(:id,:seq,:kind,:actor,CAST(:body AS jsonb),:previous,:hash,:recorded)"
        ),
        {
            "id": episode,
            "seq": sequence,
            "kind": kind,
            "actor": actor,
            "body": json.dumps(body, allow_nan=False),
            "previous": previous,
            "hash": digest,
            "recorded": recorded_at,
        },
    )
    return digest


def _context(row: dict[str, Any], created_key: str) -> dict[str, object]:
    return {
        "tenant_id": str(row["tenant_id"]),
        "visitor": str(row["visitor"]),
        "host": str(row["host"]),
        "model_hash": row["model_hash"],
        "feature_run_hash": row["feature_run_hash"],
        "synthetic": row["synthetic"],
        "created_at": row[created_key].astimezone(UTC).isoformat(),
    }


def verify_history(history: list[dict[str, Any]]) -> str:
    previous = "0" * 64
    for i, event in enumerate(history, start=1):
        if event["sequence"] != i or event["previous_hash"] != previous:
            raise ValueError("broken event sequence")
        value = {k: event[k] for k in ("sequence", "kind", "actor", "body", "previous_hash")}
        value["episode_id"] = str(event["episode_id"])
        prefix = ""
        if event["event_hash"].startswith("v2:"):
            prefix = "v2:"
            value["recorded_at"] = event["recorded_at"].astimezone(UTC).isoformat()
            value["episode_context"] = _context(event, "episode_created_at")
        digest = prefix + hashlib.sha256(canonical(value)).hexdigest()
        if digest != event["event_hash"]:
            raise ValueError("event hash mismatch")
        previous = digest
    return previous


def _checkpoint_body(tenant: UUID, episode: UUID, history: list[dict[str, Any]]) -> bytes:
    head = verify_history(history)
    return canonical(
        {"tenant": str(tenant), "episode": str(episode), "sequence": len(history), "head": head}
    )


def checkpoint(
    conn: Connection, tenant: UUID, episode: UUID, independent_store: ObjectStore
) -> str:
    history = events(conn, tenant, episode)
    if not history:
        raise ValueError("cannot checkpoint an empty history")
    return independent_store.put(_checkpoint_body(tenant, episode, history))


def checkpoint_all(engine: Engine, independent_store: ObjectStore) -> dict[str, Any]:
    """Retain a verified head for every chain plus a content-addressed manifest.

    Store the manifest digest outside the database (e.g. operator log or backup
    record); a database administrator could otherwise rewrite both together.
    """
    retained: dict[str, dict[str, str]] = {}
    broken: list[str] = []
    with engine.connect() as conn:
        episodes = conn.execute(
            text("SELECT tenant_id,episode_id FROM alert_episodes ORDER BY episode_id")
        ).all()
        for tenant, episode in episodes:
            history = events(conn, tenant, episode)
            if not history:
                continue
            try:
                body = _checkpoint_body(tenant, episode, history)
            except ValueError:
                broken.append(str(episode))
                continue
            retained[str(episode)] = {
                "tenant": str(tenant),
                "checkpoint": independent_store.put(body),
            }
    manifest = independent_store.put(
        canonical({"kind": "alert-checkpoints-v1", "episodes": retained})
    )
    return {"manifest": manifest, "episodes": len(retained), "broken": broken}


def verify_checkpoints(
    engine: Engine, independent_store: ObjectStore, manifest: str
) -> dict[str, Any]:
    """Every retained head must still be a prefix of the current verified chain."""
    retained = json.loads(independent_store.get(manifest))
    if retained.get("kind") != "alert-checkpoints-v1" or not isinstance(
        retained.get("episodes"), dict
    ):
        raise ValueError("not an alert checkpoint manifest")
    mismatched: list[str] = []
    with engine.connect() as conn:
        for episode, item in sorted(retained["episodes"].items()):
            try:
                verify_checkpoint(
                    conn,
                    UUID(item["tenant"]),
                    UUID(episode),
                    independent_store,
                    item["checkpoint"],
                )
            except (ValueError, KeyError, TypeError, OSError):
                mismatched.append(episode)
    return {
        "manifest": manifest,
        "verified": len(retained["episodes"]) - len(mismatched),
        "mismatched": mismatched,
    }


def verify_checkpoint(
    conn: Connection, tenant: UUID, episode: UUID, independent_store: ObjectStore, digest: str
) -> str:
    retained = json.loads(independent_store.get(digest))
    history = events(conn, tenant, episode)
    current = verify_history(history)
    count = retained.get("sequence")
    if (
        retained.get("tenant") != str(tenant)
        or retained.get("episode") != str(episode)
        or type(count) is not int
        or count < 1
        or count > len(history)
        or retained.get("head") != history[count - 1]["event_hash"]
    ):
        raise ValueError("retained checkpoint does not match current history")
    return current
