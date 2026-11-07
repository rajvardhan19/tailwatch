"""Verified email subscriptions and crash-aware, at-most-one-attempt outbox."""

import hashlib
import json
import secrets
from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from urllib.request import Request, build_opener
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text

from tailwatch.sources.sec.client import NoRedirect


class Sender(Protocol):
    external: bool

    def send(self, *, destination: str, subject: str, body: str, idempotency_key: str) -> str: ...


class StubSender:
    external = False

    def send(self, *, destination: str, subject: str, body: str, idempotency_key: str) -> str:
        return f"stub:{idempotency_key}"


class ResendSender:
    external = True

    def __init__(self, api_key: str, sender: str) -> None:
        if not api_key or "@" not in sender or "\n" in sender or "\r" in sender:
            raise ValueError("verified Resend sender and credential required")
        self._key, self._sender = api_key, sender

    def send(self, *, destination: str, subject: str, body: str, idempotency_key: str) -> str:
        raw = json.dumps(
            {"from": self._sender, "to": [destination], "subject": subject, "text": body}
        ).encode()
        request = Request(
            "https://api.resend.com/emails",
            data=raw,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
        )
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            payload = json.loads(response.read(65536))
        identifier = payload.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("provider acknowledgment missing")
        return identifier


def subscribe(conn: Connection, tenant: UUID, email: str) -> tuple[UUID, str, str]:
    if len(email) > 254 or email.count("@") != 1 or any(c.isspace() for c in email):
        raise ValueError("invalid email destination")
    identifier, verify, unsubscribe = uuid4(), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    conn.execute(
        text(
            "INSERT INTO subscriptions(subscription_id,tenant_id,email,"
            "verification_hash,unsubscribe_hash) "
            "VALUES(:id,:tenant,:email,:verify,:unsubscribe)"
        ),
        {
            "id": identifier,
            "tenant": tenant,
            "email": email,
            "verify": hashlib.sha256(verify.encode()).hexdigest(),
            "unsubscribe": hashlib.sha256(unsubscribe.encode()).hexdigest(),
        },
    )
    # Secrets go only to a separately sent verification message, never to the
    # subscribing API caller (otherwise they could verify arbitrary recipients).
    return identifier, verify, unsubscribe


def verify_subscription(conn: Connection, identifier: UUID, token: str) -> bool:
    row = conn.execute(
        text(
            "UPDATE subscriptions SET verified=true WHERE subscription_id=:id "
            "AND verification_hash=:hash AND active RETURNING subscription_id"
        ),
        {"id": identifier, "hash": hashlib.sha256(token.encode()).hexdigest()},
    ).first()
    return row is not None


def unsubscribe(conn: Connection, identifier: UUID, token: str) -> bool:
    return (
        conn.execute(
            text(
                "UPDATE subscriptions SET active=false WHERE subscription_id=:id "
                "AND unsubscribe_hash=:hash RETURNING subscription_id"
            ),
            {"id": identifier, "hash": hashlib.sha256(token.encode()).hexdigest()},
        ).first()
        is not None
    )


def enqueue(conn: Connection, tenant: UUID, episode: UUID, subscription: UUID) -> UUID:
    sub = conn.execute(
        text(
            "SELECT 1 FROM subscriptions WHERE subscription_id=:id AND tenant_id=:tenant "
            "AND verified AND active"
        ),
        {"id": subscription, "tenant": tenant},
    ).first()
    approval = conn.execute(
        text(
            "SELECT e.sequence,e.kind FROM alert_events e JOIN alert_episodes a USING(episode_id) "
            "WHERE a.episode_id=:id AND a.tenant_id=:tenant AND e.kind<>'outcome' "
            "ORDER BY e.sequence DESC LIMIT 1"
        ),
        {"id": episode, "tenant": tenant},
    ).first()
    if sub is None or approval is None or approval.kind != "approved":
        raise ValueError("verified active subscription and current approval required")
    identifier = uuid4()
    conn.execute(
        text(
            "INSERT INTO notification_outbox(delivery_id,episode_id,approved_sequence,"
            "subscription_id,state) "
            "VALUES(:id,:episode,:seq,:sub,'pending') ON CONFLICT DO NOTHING"
        ),
        {"id": identifier, "episode": episode, "seq": approval.sequence, "sub": subscription},
    )
    actual = conn.execute(
        text(
            "SELECT delivery_id FROM notification_outbox WHERE episode_id=:episode "
            "AND approved_sequence=:seq AND subscription_id=:sub"
        ),
        {"episode": episode, "seq": approval.sequence, "sub": subscription},
    ).scalar_one()
    return UUID(str(actual))


def deliver_one(engine: Engine, sender: Sender, *, live_allowed: Callable[[str], bool]) -> str:
    with engine.begin() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT * FROM notification_outbox WHERE state='pending' "
                    "ORDER BY updated_at,delivery_id FOR UPDATE SKIP LOCKED LIMIT 1"
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return "idle"
        delivery = dict(row)
        conn.execute(
            text(
                "UPDATE notification_outbox SET state='sending',updated_at=clock_timestamp() "
                "WHERE delivery_id=:id"
            ),
            {"id": delivery["delivery_id"]},
        )
    # Sending state is durable before any external effect. Crash after this point
    # requires reconciliation, never automatic blind retry.
    with engine.begin() as conn:
        episode = (
            conn.execute(
                text("SELECT * FROM alert_episodes WHERE episode_id=:id FOR UPDATE"),
                {"id": delivery["episode_id"]},
            )
            .mappings()
            .one()
        )
        sub = (
            conn.execute(
                text("SELECT * FROM subscriptions WHERE subscription_id=:id FOR UPDATE"),
                {"id": delivery["subscription_id"]},
            )
            .mappings()
            .one()
        )
        latest = (
            conn.execute(
                text(
                    "SELECT * FROM alert_events WHERE episode_id=:id AND kind<>'outcome' "
                    "ORDER BY sequence DESC LIMIT 1"
                ),
                {"id": episode["episode_id"]},
            )
            .mappings()
            .one()
        )
        state = "cancelled"
        provider_id = None
        permitted = (
            sub["tenant_id"] == episode["tenant_id"]
            and sub["active"]
            and sub["verified"]
            and latest["kind"] == "approved"
            and latest["sequence"] == delivery["approved_sequence"]
        )
        if sender.external:
            permitted = (
                permitted and not episode["synthetic"] and live_allowed(episode["model_hash"])
            )
        if permitted:
            try:
                provider_id = sender.send(
                    destination=sub["email"],
                    subject="Tailwatch reviewed signal",
                    body=json.dumps(latest["body"].get("public", {}), sort_keys=True),
                    idempotency_key=str(delivery["delivery_id"]),
                )
                state = "sent"
            except (OSError, ValueError, TimeoutError):
                state = "uncertain"
        conn.execute(
            text(
                "UPDATE notification_outbox SET state=:state,provider_id=:provider,"
                "updated_at=clock_timestamp() WHERE delivery_id=:id"
            ),
            {"state": state, "provider": provider_id, "id": delivery["delivery_id"]},
        )
    return state


def reconcile_abandoned(conn: Connection, older_than: datetime) -> int:
    result = conn.execute(
        text(
            "UPDATE notification_outbox SET state='uncertain',updated_at=clock_timestamp() "
            "WHERE state='sending' AND updated_at<:before"
        ),
        {"before": older_than},
    )
    return result.rowcount
