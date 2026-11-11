"""Verified email subscriptions and crash-aware, audited outboxes.

Alert deliveries and recipient verifications share one discipline: a durable
'sending' state and the exact payload digest commit before any external effect.
Ambiguous outcomes become 'uncertain'. Only a provider idempotent retry (same key,
byte-identical payload, inside the provider's retention window) or an audited
operator decision resolves them. Exactly-once email is never claimed.
"""

import hashlib
import json
import os
import re
import secrets
import tempfile
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.request import Request, build_opener
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text

from tailwatch.operations.release import allowed_model, reserve_live_delivery
from tailwatch.product.messages import LinkSigner, Message, render_alert, render_verification
from tailwatch.sources.sec.client import NoRedirect

VERIFICATIONS_PER_DAY = 3
# Retry only well inside the provider's idempotency retention window.
IDEMPOTENCY_MARGIN = timedelta(hours=1)


class Sender(Protocol):
    external: bool
    idempotency_window: timedelta | None

    def send(self, *, destination: str, subject: str, body: str, idempotency_key: str) -> str: ...


class StubSender:
    external = False
    idempotency_window: timedelta | None = None

    def send(self, *, destination: str, subject: str, body: str, idempotency_key: str) -> str:
        return f"stub:{idempotency_key}"


class FileSender:
    """Local development mailbox. Same key plus same payload is idempotent, like Resend."""

    external = False
    idempotency_window: timedelta | None = timedelta(hours=24)

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def send(self, *, destination: str, subject: str, body: str, idempotency_key: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", idempotency_key):
            raise ValueError("delivery UUID idempotency key required")
        content = f"To: {destination}\nSubject: {subject}\n\n{body}".encode()
        target = self.directory / f"{idempotency_key}.txt"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.read_bytes() != content:
                raise ValueError("idempotency key reused with a different payload") from None
        finally:
            temporary.unlink(missing_ok=True)
        return f"file:{idempotency_key}"


class ResendSender:
    external = True
    # https://resend.com/docs/dashboard/emails/idempotency-keys (checked 2026-11-10):
    # keys are retained 24 hours; same key + same payload returns the original email ID.
    idempotency_window: timedelta | None = timedelta(hours=24)

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
    local, separator, domain = email.rpartition("@")
    labels = domain.split(".")
    if (
        len(email) > 254
        or not separator
        or not 1 <= len(local) <= 64
        or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or len(labels) < 2
        or any(
            not 1 <= len(label) <= 63
            or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
            for label in labels
        )
    ):
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
    # Callers must not hand these to an API caller or operator: recipients receive
    # their own signed links through request_verification and delivered messages.
    return identifier, verify, unsubscribe


def verify_subscription(conn: Connection, identifier: UUID, token: str) -> bool:
    row = conn.execute(
        text(
            "UPDATE subscriptions SET verified=true WHERE subscription_id=:id "
            "AND verification_hash=:hash AND active AND NOT verified "
            "AND created_at > clock_timestamp() - interval '24 hours' "
            "RETURNING subscription_id"
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


def _audit(
    conn: Connection,
    *,
    actor: str,
    action: str,
    delivery: UUID | None = None,
    message: UUID | None = None,
    detail: dict[str, object] | None = None,
) -> None:
    conn.execute(
        text(
            "INSERT INTO delivery_events(delivery_id,message_id,actor,action,detail) "
            "VALUES(:delivery,:message,:actor,:action,CAST(:detail AS jsonb))"
        ),
        {
            "delivery": delivery,
            "message": message,
            "actor": actor,
            "action": action,
            "detail": json.dumps(detail or {}, allow_nan=False, sort_keys=True),
        },
    )


def request_verification(conn: Connection, subscription: UUID, actor: str) -> UUID:
    """Queue a recipient-only verification message; rate limited per subscription."""
    row = conn.execute(
        text("SELECT active,verified FROM subscriptions WHERE subscription_id=:id FOR UPDATE"),
        {"id": subscription},
    ).first()
    if row is None or not row.active or row.verified:
        raise ValueError("active unverified subscription required")
    recent = conn.execute(
        text(
            "SELECT count(*) FROM verification_messages WHERE subscription_id=:id "
            "AND created_at > clock_timestamp() - interval '24 hours'"
        ),
        {"id": subscription},
    ).scalar_one()
    if recent >= VERIFICATIONS_PER_DAY:
        raise ValueError("verification request limit reached for this destination")
    message = uuid4()
    conn.execute(
        text(
            "INSERT INTO verification_messages(message_id,subscription_id,state) "
            "VALUES(:id,:sub,'pending')"
        ),
        {"id": message, "sub": subscription},
    )
    _audit(conn, actor=actor, action="requested", message=message)
    return message


def confirm_verification(
    conn: Connection, message: UUID, token: str, signer: LinkSigner, actor: str = "recipient"
) -> bool:
    """Single-use, 24-hour recipient confirmation of a delivered verification link."""
    if not signer.check("verify", message, token):
        return False
    row = conn.execute(
        text("""
          WITH consumed AS (
            UPDATE verification_messages SET consumed_at=clock_timestamp(),
              updated_at=clock_timestamp()
            WHERE message_id=:id AND consumed_at IS NULL AND state IN ('sent','uncertain')
            AND created_at > clock_timestamp() - interval '24 hours'
            RETURNING subscription_id
          )
          UPDATE subscriptions s SET verified=true FROM consumed c
          WHERE s.subscription_id=c.subscription_id AND s.active AND NOT s.verified
          RETURNING s.subscription_id
        """),
        {"id": message},
    ).first()
    if row is not None:
        _audit(conn, actor=actor, action="verified", message=message)
    return row is not None


def unsubscribe_delivery(
    conn: Connection, delivery: UUID, token: str, signer: LinkSigner, actor: str = "recipient"
) -> bool:
    """Recipient unsubscribe from the signed link carried by a delivered alert."""
    if not signer.check("unsubscribe", delivery, token):
        return False
    row = conn.execute(
        text(
            "UPDATE subscriptions s SET active=false FROM notification_outbox o "
            "WHERE o.delivery_id=:id AND s.subscription_id=o.subscription_id "
            "RETURNING s.subscription_id"
        ),
        {"id": delivery},
    ).first()
    if row is not None:
        _audit(conn, actor=actor, action="unsubscribed", delivery=delivery)
    return row is not None


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
    created = conn.execute(
        text(
            "INSERT INTO notification_outbox(delivery_id,episode_id,approved_sequence,"
            "subscription_id,state) "
            "VALUES(:id,:episode,:seq,:sub,'pending') ON CONFLICT DO NOTHING RETURNING delivery_id"
        ),
        {"id": uuid4(), "episode": episode, "seq": approval.sequence, "sub": subscription},
    ).scalar_one_or_none()
    if created is not None:
        _audit(conn, actor="enqueue", action="enqueued", delivery=created)
        return UUID(str(created))
    actual = conn.execute(
        text(
            "SELECT delivery_id FROM notification_outbox WHERE episode_id=:episode "
            "AND approved_sequence=:seq AND subscription_id=:sub"
        ),
        {"episode": episode, "seq": approval.sequence, "sub": subscription},
    ).scalar_one()
    return UUID(str(actual))


def enqueue_approved(conn: Connection, tenant: UUID, actor: str) -> list[UUID]:
    """Queue each current approval for verified, active destinations subscribed before it."""
    created = [
        UUID(str(identifier))
        for identifier in conn.execute(
            text("""
              INSERT INTO notification_outbox(delivery_id,episode_id,approved_sequence,
                                              subscription_id,state)
              SELECT gen_random_uuid(),a.episode_id,e.sequence,s.subscription_id,'pending'
              FROM alert_episodes a JOIN LATERAL (
                SELECT kind,sequence,recorded_at FROM alert_events WHERE episode_id=a.episode_id
                AND kind<>'outcome' ORDER BY sequence DESC LIMIT 1
              ) e ON e.kind='approved'
              JOIN subscriptions s ON s.tenant_id=a.tenant_id AND s.verified AND s.active
                AND s.created_at<=e.recorded_at
              WHERE a.tenant_id=:tenant
              ON CONFLICT DO NOTHING RETURNING delivery_id
            """),
            {"tenant": tenant},
        ).scalars()
    ]
    for delivery in created:
        _audit(conn, actor=actor, action="enqueued", delivery=delivery)
    return created


Outcome = Literal["sent", "cancelled", "uncertain", "deferred"]

_ALERT_ROWS = """
  SELECT o.delivery_id,o.payload_digest,o.state,s.email,e.body,a.synthetic
  FROM notification_outbox o JOIN subscriptions s USING(subscription_id)
  JOIN alert_events e ON e.episode_id=o.episode_id AND e.sequence=o.approved_sequence
  JOIN alert_episodes a ON a.episode_id=o.episode_id
"""


def _render(row: Any, signer: LinkSigner | None) -> Message:
    return render_alert(
        row.email,
        row.body.get("public", {}),
        synthetic=row.synthetic,
        delivery_id=row.delivery_id,
        signer=signer,
    )


def _transmit(
    sender: Sender, message: Message, key: UUID, reserve: Callable[[], None] | None
) -> tuple[Outcome, str | None, str]:
    if reserve is not None:
        try:
            reserve()
        except ValueError as error:
            # Nothing left this process: retry later rather than marking it ambiguous.
            return "deferred", None, type(error).__name__
    try:
        provider = sender.send(
            destination=message.destination,
            subject=message.subject,
            body=message.body,
            idempotency_key=str(key),
        )
    except (OSError, ValueError, TimeoutError) as error:
        return "uncertain", None, type(error).__name__
    return "sent", provider, "provider_acknowledged"


def _attempt_alert(
    engine: Engine,
    sender: Sender,
    delivery: UUID,
    message: Message,
    *,
    live_allowed: Callable[[str], bool],
    actor: str,
    previous: Literal["pending", "uncertain"],
) -> Outcome:
    with engine.begin() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT o.approved_sequence,a.tenant_id,a.synthetic,a.model_hash,"
                    "s.tenant_id AS subscription_tenant,s.active,s.verified "
                    "FROM notification_outbox o JOIN alert_episodes a USING(episode_id) "
                    "JOIN subscriptions s USING(subscription_id) WHERE o.delivery_id=:id "
                    "FOR UPDATE OF o,a,s"
                ),
                {"id": delivery},
            )
            .mappings()
            .one()
        )
        latest = conn.execute(
            text(
                "SELECT kind,sequence FROM alert_events e JOIN notification_outbox o "
                "USING(episode_id) WHERE o.delivery_id=:id AND e.kind<>'outcome' "
                "ORDER BY e.sequence DESC LIMIT 1"
            ),
            {"id": delivery},
        ).one()
        permitted = (
            row["subscription_tenant"] == row["tenant_id"]
            and row["active"]
            and row["verified"]
            and latest.kind == "approved"
            and latest.sequence == row["approved_sequence"]
        )
        if sender.external:
            permitted = (
                permitted
                and not row["synthetic"]
                and live_allowed(row["model_hash"])
                and allowed_model(engine, row["tenant_id"]) == row["model_hash"]
            )
        provider: str | None = None
        state: Outcome
        if permitted:
            reserve = (
                (lambda: reserve_live_delivery(engine, row["tenant_id"], row["model_hash"]))
                if sender.external
                else None
            )
            state, provider, reason = _transmit(sender, message, delivery, reserve)
        elif previous == "uncertain":
            # The first attempt may already have reached the recipient.
            state, reason = "uncertain", "no_longer_permitted_outcome_unknown"
        else:
            state, reason = "cancelled", "no_longer_permitted"
        stored = previous if state == "deferred" else state
        conn.execute(
            text(
                "UPDATE notification_outbox SET state=:state,"
                "provider_id=coalesce(:provider,provider_id),updated_at=clock_timestamp() "
                "WHERE delivery_id=:id"
            ),
            {"state": stored, "provider": provider, "id": delivery},
        )
        _audit(conn, actor=actor, action=state, delivery=delivery, detail={"reason": reason})
    return state


def deliver_one(
    engine: Engine,
    sender: Sender,
    *,
    live_allowed: Callable[[str], bool],
    signer: LinkSigner | None = None,
    actor: str = "outbox-worker",
) -> str:
    if sender.external and signer is None:
        raise ValueError("external delivery requires signed unsubscribe links")
    with engine.begin() as conn:
        row = conn.execute(
            text(
                _ALERT_ROWS + "WHERE o.state='pending' ORDER BY o.updated_at,o.delivery_id "
                "FOR UPDATE OF o SKIP LOCKED LIMIT 1"
            )
        ).first()
        if row is None:
            return "idle"
        message = _render(row, signer)
        # Sending state and exact payload are durable before any external effect.
        # Crash after this point requires reconciliation, never a blind resend.
        conn.execute(
            text(
                "UPDATE notification_outbox SET state='sending',payload_digest=:digest,"
                "first_attempt_at=clock_timestamp(),updated_at=clock_timestamp() "
                "WHERE delivery_id=:id"
            ),
            {"digest": message.digest(), "id": row.delivery_id},
        )
        _audit(
            conn,
            actor=actor,
            action="sending",
            delivery=row.delivery_id,
            detail={"payload_digest": message.digest()},
        )
    return _attempt_alert(
        engine,
        sender,
        row.delivery_id,
        message,
        live_allowed=live_allowed,
        actor=actor,
        previous="pending",
    )


def reconcile_abandoned(conn: Connection, older_than: datetime) -> int:
    """Interrupted 'sending' rows may have reached the provider: never assume either way."""
    stale_deliveries = (
        conn.execute(
            text(
                "UPDATE notification_outbox SET state='uncertain',updated_at=clock_timestamp() "
                "WHERE state='sending' AND updated_at<:before RETURNING delivery_id"
            ),
            {"before": older_than},
        )
        .scalars()
        .all()
    )
    stale_messages = (
        conn.execute(
            text(
                "UPDATE verification_messages SET state='uncertain',updated_at=clock_timestamp() "
                "WHERE state='sending' AND updated_at<:before RETURNING message_id"
            ),
            {"before": older_than},
        )
        .scalars()
        .all()
    )
    for delivery in stale_deliveries:
        _audit(conn, actor="reconciler", action="abandoned", delivery=delivery)
    for message in stale_messages:
        _audit(conn, actor="reconciler", action="abandoned", message=message)
    return len(stale_deliveries) + len(stale_messages)


def reconcile_uncertain(
    engine: Engine,
    sender: Sender,
    *,
    live_allowed: Callable[[str], bool],
    signer: LinkSigner | None,
    limit: int = 50,
    actor: str = "reconciler",
) -> dict[str, int]:
    """Retry ambiguous alerts with the original key and byte-identical payload.

    Inside the provider's idempotency window this either returns the original
    provider ID or performs the single missing send. Outside it, or if the payload
    would differ, the row stays uncertain for an audited operator decision.
    """
    window = sender.idempotency_window
    if window is None or window <= IDEMPOTENCY_MARGIN:
        raise ValueError("sender lacks provider idempotency; resolve deliveries manually")
    if sender.external and signer is None:
        raise ValueError("external delivery requires signed unsubscribe links")
    counts: Counter[str] = Counter()
    claimed: list[tuple[UUID, Message]] = []
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                _ALERT_ROWS + "WHERE o.state='uncertain' AND o.payload_digest IS NOT NULL "
                "AND o.first_attempt_at > clock_timestamp() - make_interval(secs => :window) "
                "ORDER BY o.first_attempt_at,o.delivery_id "
                "LIMIT :limit FOR UPDATE OF o SKIP LOCKED"
            ),
            {"window": (window - IDEMPOTENCY_MARGIN).total_seconds(), "limit": limit},
        ).all()
        for row in rows:
            message = _render(row, signer)
            if message.digest() != row.payload_digest:
                _audit(
                    conn,
                    actor=actor,
                    action="retry_skipped",
                    delivery=row.delivery_id,
                    detail={"reason": "payload_changed"},
                )
                counts["skipped"] += 1
                continue
            conn.execute(
                text(
                    "UPDATE notification_outbox SET state='sending',updated_at=clock_timestamp() "
                    "WHERE delivery_id=:id"
                ),
                {"id": row.delivery_id},
            )
            _audit(conn, actor=actor, action="retry_sending", delivery=row.delivery_id)
            claimed.append((row.delivery_id, message))
    for delivery, message in claimed:
        outcome = _attempt_alert(
            engine,
            sender,
            delivery,
            message,
            live_allowed=live_allowed,
            actor=actor,
            previous="uncertain",
        )
        counts[outcome] += 1
    return dict(counts)


def resolve_delivery(
    conn: Connection,
    delivery: UUID,
    *,
    state: Literal["sent", "cancelled"],
    actor: str,
    evidence: str,
    provider_id: str | None = None,
) -> None:
    """Audited operator decision for an uncertain delivery, e.g. from provider logs."""
    if not actor.strip() or not evidence.strip():
        raise ValueError("operator identity and resolution evidence required")
    row = conn.execute(
        text(
            "UPDATE notification_outbox SET state=:state,"
            "provider_id=coalesce(:provider,provider_id),updated_at=clock_timestamp() "
            "WHERE delivery_id=:id AND state='uncertain' RETURNING delivery_id"
        ),
        {"state": state, "provider": provider_id, "id": delivery},
    ).first()
    if row is None:
        raise ValueError("only uncertain deliveries can be resolved")
    _audit(
        conn,
        actor=actor,
        action=f"resolved_{state}",
        delivery=delivery,
        detail={"evidence": evidence, "provider_id": provider_id},
    )


def send_verification_one(
    engine: Engine,
    sender: Sender,
    *,
    signer: LinkSigner,
    production_enabled: bool,
    actor: str = "verification-worker",
) -> str:
    """Send one queued verification link to its recipient only.

    External verification mail shares the alert discipline: it needs an active
    validated release that includes the tenant, and it consumes the daily quota.
    """
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT m.message_id,s.email FROM verification_messages m "
                "JOIN subscriptions s USING(subscription_id) WHERE m.state='pending' "
                "ORDER BY m.updated_at,m.message_id FOR UPDATE OF m SKIP LOCKED LIMIT 1"
            )
        ).first()
        if row is None:
            return "idle"
        message = render_verification(row.email, row.message_id, signer)
        conn.execute(
            text(
                "UPDATE verification_messages SET state='sending',payload_digest=:digest,"
                "first_attempt_at=clock_timestamp(),updated_at=clock_timestamp() "
                "WHERE message_id=:id"
            ),
            {"digest": message.digest(), "id": row.message_id},
        )
        _audit(conn, actor=actor, action="sending", message=row.message_id)
    identifier = row.message_id
    with engine.begin() as conn:
        sub = (
            conn.execute(
                text(
                    "SELECT s.tenant_id,s.active,s.verified FROM subscriptions s "
                    "JOIN verification_messages m USING(subscription_id) "
                    "WHERE m.message_id=:id FOR UPDATE OF s,m"
                ),
                {"id": identifier},
            )
            .mappings()
            .one()
        )
        permitted = sub["active"] and not sub["verified"]
        model = None
        if sender.external:
            model = allowed_model(engine, sub["tenant_id"]) if production_enabled else None
            permitted = permitted and model is not None
        provider: str | None = None
        state: Outcome
        if permitted:
            reserve = (
                (lambda: reserve_live_delivery(engine, sub["tenant_id"], str(model)))
                if sender.external
                else None
            )
            state, provider, reason = _transmit(sender, message, identifier, reserve)
        else:
            state, reason = "cancelled", "not_permitted"
        conn.execute(
            text(
                "UPDATE verification_messages SET state=:state,provider_id=:provider,"
                "updated_at=clock_timestamp() WHERE message_id=:id"
            ),
            {
                "state": "pending" if state == "deferred" else state,
                "provider": provider,
                "id": identifier,
            },
        )
        _audit(conn, actor=actor, action=state, message=identifier, detail={"reason": reason})
    return state
