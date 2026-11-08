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


def test_release_checks_deployed_config_tenant_and_kill(database, monkeypatch):
    """Fabricated CI rows exercise plumbing, never establish real release evidence."""
    from datetime import timedelta

    from sqlalchemy import text

    from tailwatch.operations.release import ReleaseManifest, activate, register_release

    now = datetime.now(UTC)
    digest = uuid4().hex * 2
    model = uuid4().hex * 2
    tenant = uuid4()
    config = uuid4().hex * 2
    from zoneinfo import ZoneInfo

    today = now.astimezone(ZoneInfo("America/New_York")).date()
    sessions = tuple(today - timedelta(days=i) for i in range(10, -1, -1))
    runs = []
    with database.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO research_experiments(experiment_hash,manifest) "
                "VALUES(:id,CAST(:body AS jsonb))"
            ),
            {"id": digest, "body": '{"synthetic":false,"note":"fabricated integration test only"}'},
        )
        conn.execute(
            text(
                "INSERT INTO sealed_test_access(dataset_hash,experiment_hash,model_hash) "
                "VALUES(:id,:id,:model)"
            ),
            {"id": digest, "model": model},
        )
        conn.execute(
            text(
                "INSERT INTO research_evaluations(dataset_hash,result) "
                "VALUES(:id,CAST(:body AS jsonb))"
            ),
            {"id": digest, "body": '{"decision":"PASS","note":"fabricated integration test only"}'},
        )
    for session in sessions:
        run = claim_run(database, session, config, False)
        assert run
        finish_run(
            database, run, {"ready": True, "valid_until": (now + timedelta(days=1)).isoformat()}
        )
        runs.append(run)
    from tailwatch.operations.runtime import required_session

    due = required_session(sessions, today + timedelta(days=2), now)
    shadow_ids = [run for run, session in zip(runs, sessions, strict=True) if session <= due][-10:]
    manifest = ReleaseManifest(
        application_commit="a" * 40,
        model_hash=model,
        dataset_hash=digest,
        config_hash=config,
        gate_evidence={f"G{i}": "fabricated CI evidence" for i in range(5)},
        operator="test",
        rights_valid_until=now + timedelta(days=1),
        expires_at=now + timedelta(days=1),
        rollout_limit=1,
        daily_email_limit=1,
        shadow_run_ids=tuple(shadow_ids),
        trading_sessions=sessions + (today + timedelta(days=1), today + timedelta(days=2)),
        calendar_valid_through=today + timedelta(days=2),
        approved_tenants=(tenant,),
        restore_drill_digest="a" * 64,
    )
    from tailwatch.operations.release import _validate_current_run

    with database.connect() as conn, pytest.raises(ValueError, match="latest due session"):
        _validate_current_run(conn, manifest, now + timedelta(days=2))
    identifier = register_release(database, manifest)
    with pytest.raises(ValueError, match="deployed"):
        activate(database, identifier, "test")
    monkeypatch.setenv("TAILWATCH_APPLICATION_COMMIT", manifest.application_commit)
    monkeypatch.setenv("TAILWATCH_CONFIG_HASH", config)
    try:
        activate(database, identifier, "test")
        assert allowed_model(database, tenant) == model
        assert allowed_model(database, uuid4()) is None
        import json

        valid_health = {"ready": True, "valid_until": (now + timedelta(days=1)).isoformat()}
        for health in (
            {"ready": True},
            {"ready": False},
            {"ready": True, "valid_until": "2000-01-01T00:00:00+00:00"},
            {"ready": True, "valid_until": "2099-01-01T00:00:00"},
        ):
            with database.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE scheduled_runs SET readiness=CAST(:body AS jsonb) "
                        "WHERE session_date=:day AND config_hash=:config"
                    ),
                    {"body": json.dumps(health), "day": due, "config": config},
                )
            assert allowed_model(database, tenant) is None
        with database.begin() as conn:
            conn.execute(
                text(
                    "UPDATE scheduled_runs SET readiness=CAST(:body AS jsonb) "
                    "WHERE session_date=:day AND config_hash=:config"
                ),
                {"body": json.dumps(valid_health), "day": due, "config": config},
            )
        assert allowed_model(database, tenant) == model
        monkeypatch.setenv("TAILWATCH_CONFIG_HASH", "wrong")
        assert allowed_model(database, tenant) is None
    finally:
        kill(database, "test-cleanup")
    assert allowed_model(database, tenant) is None
