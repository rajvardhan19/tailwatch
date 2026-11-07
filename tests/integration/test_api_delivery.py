from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tailwatch.product.alerts import append_event, create_episode
from tailwatch.product.api import create_app, issue_key
from tailwatch.product.delivery import (
    StubSender,
    deliver_one,
    enqueue,
    reconcile_abandoned,
    subscribe,
    unsubscribe,
    verify_subscription,
)

pytestmark = pytest.mark.integration


def setup(database):
    tenant, other, company = uuid4(), uuid4(), uuid4()
    with database.begin() as conn:
        for ident in (tenant, other):
            conn.execute(text("INSERT INTO tenants VALUES(:id,'synthetic')"), {"id": ident})
        conn.execute(text("INSERT INTO entities VALUES(:id,'company')"), {"id": company})
        key = issue_key(conn, tenant, "customer")
        other_key = issue_key(conn, other, "customer")
        analyst = issue_key(conn, tenant, "analyst")
        episode = create_episode(
            conn,
            tenant=tenant,
            visitor=company,
            host=uuid4(),
            model_hash="fixture",
            feature_run_hash="fixture",
            synthetic=True,
        )
        append_event(
            conn,
            tenant=tenant,
            episode=episode,
            kind="draft",
            actor="fixture",
            body={"secret": "internal", "public": {"headline": "Synthetic observation"}},
        )
    return tenant, key, other_key, analyst, episode, company


def test_customer_isolation_draft_privacy_watchlist_and_revocation(database):
    tenant, key, other, analyst, episode, company = setup(database)
    client = TestClient(create_app(database))
    auth = {"Authorization": f"Bearer {key}"}
    assert client.get("/alerts", headers=auth).json()["items"] == []
    assert client.get("/alerts", params={"api_key": key}).status_code == 401
    assert client.get("/alerts", headers=auth, params={"limit": 101}).status_code == 422
    body = {"kind": "review", "note": "Reviewed evidence", "human_confirmed": True}
    assert (
        client.post(f"/analyst/alerts/{episode}/review", headers=auth, json=body).status_code == 403
    )
    admin = {"Authorization": f"Bearer {analyst}"}
    assert (
        client.post(f"/analyst/alerts/{episode}/review", headers=admin, json=body).status_code
        == 200
    )
    body["kind"] = "approved"
    assert (
        client.post(f"/analyst/alerts/{episode}/review", headers=admin, json=body).status_code
        == 200
    )
    assert len(client.get("/alerts", headers=auth).json()["items"]) == 1
    assert (
        client.get(
            f"/alerts/{episode}/history", headers={"Authorization": f"Bearer {other}"}
        ).status_code
        == 404
    )
    assert "internal" not in client.get(f"/alerts/{episode}/history", headers=auth).text
    assert (
        client.put("/watchlist", headers=auth, json={"companies": [str(company)]}).status_code
        == 200
    )
    assert client.get("/watchlist", headers={"Authorization": f"Bearer {other}"}).json() == {
        "companies": []
    }
    assert client.delete(f"/watchlist/{company}", headers=auth).status_code == 200
    assert client.post("/keys/revoke-self", headers=auth).status_code == 200
    assert client.get("/alerts", headers=auth).status_code == 401


def test_outbox_rechecks_approval_unsubscribe_and_ambiguous_failure(database):
    tenant, _, _, _, episode, _ = setup(database)
    with database.begin() as conn:
        for kind in ("review", "approved"):
            append_event(
                conn,
                tenant=tenant,
                episode=episode,
                kind=kind,
                actor="fixture",
                body={"public": {"synthetic": True}},
            )
        subscription, verify, unsub = subscribe(conn, tenant, "synthetic@example.invalid")
        with pytest.raises(ValueError):
            enqueue(conn, tenant, episode, subscription)
        assert verify_subscription(conn, subscription, verify)
        delivery = enqueue(conn, tenant, episode, subscription)
        assert enqueue(conn, tenant, episode, subscription) == delivery
        assert unsubscribe(conn, subscription, unsub)
    assert deliver_one(database, StubSender(), live_allowed=lambda _: False) == "cancelled"
    with database.begin() as conn:
        sub, token, _ = subscribe(conn, tenant, "second@example.invalid")
        verify_subscription(conn, sub, token)
        enqueue(conn, tenant, episode, sub)

    class TimeoutSender(StubSender):
        def send(self, **kwargs):
            raise TimeoutError()

    assert deliver_one(database, TimeoutSender(), live_allowed=lambda _: False) == "uncertain"
    assert deliver_one(database, StubSender(), live_allowed=lambda _: False) == "idle"
    with database.begin() as conn:
        assert reconcile_abandoned(conn, datetime.now(UTC) - timedelta(hours=1)) == 0
