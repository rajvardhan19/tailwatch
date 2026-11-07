from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from tailwatch.operations.release import allowed_model, kill
from tailwatch.operations.runtime import DurableBudget, claim_run, finish_run
from tailwatch.sources.policy import PolicyDenied, Source, load_manifest

pytestmark = pytest.mark.integration


def test_budget_survives_new_worker_and_duplicate_run_cannot_claim(database):
    source = Source.model_validate(
        load_manifest(Path("config/sources.fixture.json")).sources[0].model_dump()
        | {"source_id": "fixture-" + uuid4().hex, "max_requests": 1, "max_cost_microusd": 5}
    )
    day = datetime.now(UTC).date()
    DurableBudget(database, source, day).reserve(5)
    with pytest.raises(PolicyDenied):
        DurableBudget(database, source, day).reserve(0)
    config = uuid4().hex
    run = claim_run(database, day, config, True)
    assert run and claim_run(database, day, config, True) is None
    finish_run(database, run, {"ready": True})
    with pytest.raises(ValueError):
        finish_run(database, run, {"ready": True})
    assert allowed_model(database) is None
    kill(database, "integration-test")
    assert allowed_model(database) is None


def test_environment_flag_and_permissive_callback_cannot_bypass_real_release(database):
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from tailwatch.product.alerts import append_event, create_episode
    from tailwatch.product.api import create_app, issue_key
    from tailwatch.product.delivery import (
        StubSender,
        deliver_one,
        enqueue,
        subscribe,
        verify_subscription,
    )

    tenant = uuid4()
    with database.begin() as conn:
        conn.execute(text("INSERT INTO tenants VALUES(:id,'gate-fixture')"), {"id": tenant})
        token = issue_key(conn, tenant, "customer")
        episode = create_episode(
            conn,
            tenant=tenant,
            visitor=uuid4(),
            host=uuid4(),
            model_hash="unvalidated",
            feature_run_hash="unvalidated",
            synthetic=False,
        )
        for kind in ("draft", "review", "approved"):
            append_event(
                conn,
                tenant=tenant,
                episode=episode,
                kind=kind,
                actor="fixture",
                body={"public": {"headline": "must stay hidden"}},
            )
        sub, verify, _ = subscribe(conn, tenant, "never-send@example.invalid")
        verify_subscription(conn, sub, verify)
        enqueue(conn, tenant, episode, sub)
    client = TestClient(create_app(database, production_enabled=True))
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/health").json()["production_enabled"] is False
    assert client.get("/alerts", headers=headers).json()["items"] == []
    assert client.get(f"/alerts/{episode}/history", headers=headers).status_code == 404

    class External(StubSender):
        external = True

        def send(self, **kwargs):
            raise AssertionError("must never send")

    assert deliver_one(database, External(), live_allowed=lambda _: True) == "cancelled"
