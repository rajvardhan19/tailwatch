import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from tailwatch.cli import main
from tailwatch.operations.runtime import DurableBudget
from tailwatch.product.alerts import append_event, canonical, create_episode
from tailwatch.product.delivery import StubSender, deliver_one, enqueue, subscribe
from tailwatch.product.delivery import verify_subscription as legacy_verify
from tailwatch.sources.objects import LocalObjectStore
from tailwatch.sources.policy import Source, load_manifest

pytestmark = pytest.mark.integration


def approved_episode(database) -> tuple:
    tenant = uuid4()
    with database.begin() as conn:
        conn.execute(text("INSERT INTO tenants VALUES(:id,'ops-fixture')"), {"id": tenant})
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
            append_event(conn, tenant=tenant, episode=episode, kind=kind, actor="fixture", body={})
    return tenant, episode


def test_ops_status_exits_nonzero_with_machine_readable_alerts(database, monkeypatch, capsys):
    monkeypatch.setenv("TAILWATCH_DATABASE_URL", os.environ["TAILWATCH_TEST_DATABASE_URL"])
    tenant, episode = approved_episode(database)
    with database.begin() as conn:
        sub, token, _ = subscribe(conn, tenant, "ops@example.invalid")
        legacy_verify(conn, sub, token)
        enqueue(conn, tenant, episode, sub)

    class Timeout(StubSender):
        def send(self, **kwargs):
            raise TimeoutError()

    while deliver_one(database, Timeout(), live_allowed=lambda _: False) != "idle":
        pass
    source = Source.model_validate(
        load_manifest(Path("config/sources.fixture.json")).sources[0].model_dump()
        | {"source_id": "ops-" + uuid4().hex, "max_requests": 1, "max_cost_microusd": 0}
    )
    DurableBudget(database, source, datetime.now(UTC).date()).reserve(0)

    with pytest.raises(SystemExit) as exit_code:
        main(["ops-status"])
    assert exit_code.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == "0010" and report["healthy"] is False
    assert "outbox:uncertain_requires_resolution" in report["alerts"]
    assert f"budget:{source.source_id}:requests_near_limit" in report["alerts"]
    assert report["outbox"]["uncertain"]["count"] >= 1
    with pytest.raises(SystemExit) as invalid:
        main(["ops-status", "--budget-fraction", "0"])
    assert invalid.value.code == 2


def test_checkpoint_manifest_detects_divergent_retained_heads(
    database, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("TAILWATCH_DATABASE_URL", os.environ["TAILWATCH_TEST_DATABASE_URL"])
    tenant, episode = approved_episode(database)
    objects = tmp_path / "independent"
    assert main(["checkpoint-alerts", "--objects", str(objects)]) == 0
    retained = json.loads(capsys.readouterr().out)
    assert retained["broken"] == [] and retained["episodes"] >= 1
    assert main(["verify-checkpoints", retained["manifest"], "--objects", str(objects)]) == 0
    assert json.loads(capsys.readouterr().out)["mismatched"] == []

    store = LocalObjectStore(objects)
    forged_head = store.put(
        canonical(
            {
                "tenant": str(tenant),
                "episode": str(episode),
                "sequence": 3,
                "head": "v2:" + "0" * 64,
            }
        )
    )
    forged = store.put(
        canonical(
            {
                "kind": "alert-checkpoints-v1",
                "episodes": {str(episode): {"tenant": str(tenant), "checkpoint": forged_head}},
            }
        )
    )
    with pytest.raises(SystemExit) as exit_code:
        main(["verify-checkpoints", forged, "--objects", str(objects)])
    assert exit_code.value.code == 1
    assert json.loads(capsys.readouterr().out)["mismatched"] == [str(episode)]
