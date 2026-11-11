from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tailwatch.product.alerts import append_event, create_episode
from tailwatch.product.api import create_app
from tailwatch.product.delivery import (
    FileSender,
    StubSender,
    deliver_one,
    enqueue_approved,
    reconcile_uncertain,
    resolve_delivery,
    subscribe,
    verify_subscription,
)
from tailwatch.product.messages import LinkSigner

pytestmark = pytest.mark.integration
SIGNER = LinkSigner("integration-only-link-secret-0123456789", "http://127.0.0.1:8765")


def approved_alert(database) -> tuple:
    tenant = uuid4()
    with database.begin() as conn:
        conn.execute(text("INSERT INTO tenants VALUES(:id,'outbox-fixture')"), {"id": tenant})
        subscription, token, _ = subscribe(conn, tenant, "outbox@example.invalid")
        assert verify_subscription(conn, subscription, token)
        episode = create_episode(
            conn,
            tenant=tenant,
            visitor=uuid4(),
            host=uuid4(),
            model_hash="fixture",
            feature_run_hash="fixture",
            synthetic=True,
        )
        for kind in ("draft", "review", "approved"):
            append_event(
                conn,
                tenant=tenant,
                episode=episode,
                kind=kind,
                actor="fixture",
                body={"public": {"visitor": "Fixture A\nBcc: injected", "host": "Fixture B"}},
            )
    return tenant, subscription, episode


def drain(database, sender, signer=SIGNER) -> list[str]:
    outcomes = []
    while (
        outcome := deliver_one(database, sender, live_allowed=lambda _: False, signer=signer)
    ) != ("idle"):
        outcomes.append(outcome)
    return outcomes


class LostAcknowledgment(FileSender):
    """Provider accepted the message but the response never arrived."""

    def send(self, **kwargs):
        super().send(**kwargs)
        raise TimeoutError()


def events(database, delivery) -> list[str]:
    with database.connect() as conn:
        return list(
            conn.execute(
                text("SELECT action FROM delivery_events WHERE delivery_id=:id ORDER BY event_id"),
                {"id": delivery},
            ).scalars()
        )


def test_lost_acknowledgment_reconciles_to_exactly_one_message(database, tmp_path):
    tenant, subscription, episode = approved_alert(database)
    with database.begin() as conn:
        late, token, _ = subscribe(conn, tenant, "late@example.invalid")
        verify_subscription(conn, late, token)
        conn.execute(
            text(
                "UPDATE subscriptions SET created_at=clock_timestamp()+interval '1 minute' "
                "WHERE subscription_id=:id"
            ),
            {"id": late},
        )
        [delivery] = enqueue_approved(conn, tenant, "integration-test")
        # Idempotent, and approvals are not backfilled to later subscribers.
        assert enqueue_approved(conn, tenant, "integration-test") == []
    mail = tmp_path / "mail"
    assert drain(database, LostAcknowledgment(mail)) == ["uncertain"]
    with pytest.raises(ValueError, match="idempotency"):
        reconcile_uncertain(database, StubSender(), live_allowed=lambda _: False, signer=SIGNER)
    retried = reconcile_uncertain(
        database, FileSender(mail), live_allowed=lambda _: False, signer=SIGNER
    )
    assert retried.get("sent", 0) >= 1
    assert [p.name for p in mail.iterdir()] == [f"{delivery}.txt"]
    message = (mail / f"{delivery}.txt").read_text()
    assert "SYNTHETIC TEST DATA" in message and "\nBcc:" not in message
    assert events(database, delivery) == [
        "enqueued",
        "sending",
        "uncertain",
        "retry_sending",
        "sent",
    ]
    with database.begin() as conn, pytest.raises(DBAPIError):
        conn.execute(text("UPDATE delivery_events SET actor='rewrite'"))

    link = next(line for line in message.splitlines() if line.startswith("Unsubscribe: "))
    params = parse_qs(urlsplit(link.split(": ", 1)[1]).fragment)
    assert params["action"] == ["unsubscribe"] and params["id"] == [str(delivery)]
    client = TestClient(create_app(database, signer=SIGNER))
    path = f"/deliveries/{delivery}/unsubscribe"
    assert client.post(path, json={"token": "x" * 43}).status_code == 404
    assert client.post(path, json={"token": params["token"][0]}).status_code == 200
    with database.connect() as conn:
        active = conn.execute(
            text("SELECT active FROM subscriptions WHERE subscription_id=:id"),
            {"id": subscription},
        ).scalar_one()
    assert active is False


def test_changed_payload_is_never_retried_and_operator_resolution_is_audited(database, tmp_path):
    tenant, _, _ = approved_alert(database)
    with database.begin() as conn:
        [delivery] = enqueue_approved(conn, tenant, "integration-test")

    class Timeout(StubSender):
        def send(self, **kwargs):
            raise TimeoutError()

    assert drain(database, Timeout()) == ["uncertain"]
    moved = LinkSigner("integration-only-link-secret-0123456789", "https://moved.example.invalid")
    mail = tmp_path / "mail"
    result = reconcile_uncertain(
        database, FileSender(mail), live_allowed=lambda _: False, signer=moved
    )
    assert result.get("skipped", 0) >= 1 and not mail.exists()
    with database.begin() as conn:
        conn.execute(
            text(
                "UPDATE notification_outbox SET first_attempt_at=clock_timestamp()-interval "
                "'23 hours' WHERE delivery_id=:id"
            ),
            {"id": delivery},
        )
    # Outside the provider's retention margin, the original payload is not retried either.
    reconcile_uncertain(database, FileSender(mail), live_allowed=lambda _: False, signer=SIGNER)
    assert not mail.exists()
    with database.begin() as conn:
        with pytest.raises(ValueError, match="evidence"):
            resolve_delivery(conn, delivery, state="cancelled", actor="operator", evidence=" ")
        resolve_delivery(
            conn,
            delivery,
            state="cancelled",
            actor="operator",
            evidence="provider log search: no message for this idempotency key",
        )
    with database.begin() as conn, pytest.raises(ValueError, match="uncertain"):
        resolve_delivery(conn, delivery, state="sent", actor="operator", evidence="retry")
    assert events(database, delivery)[-1] == "resolved_cancelled"
