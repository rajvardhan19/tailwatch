from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tailwatch.product.alerts import (
    append_event,
    checkpoint,
    create_episode,
    events,
    verify_history,
)
from tailwatch.sources.objects import LocalObjectStore

pytestmark = pytest.mark.integration


def test_pair_cooldown_tenant_isolation_appendonly_and_maturity(connection, tmp_path):
    tenant, other, a, b = uuid4(), uuid4(), uuid4(), uuid4()
    for ident in (tenant, other):
        connection.execute(text("INSERT INTO tenants VALUES(:id,'synthetic')"), {"id": ident})
    kwargs = dict(tenant=tenant, model_hash="fixture", feature_run_hash="fixture", synthetic=True)
    episode = create_episode(connection, visitor=a, host=b, **kwargs)
    assert create_episode(connection, visitor=b, host=a, **kwargs) == episode
    for kind in ("draft", "review", "approved"):
        append_event(
            connection,
            tenant=tenant,
            episode=episode,
            kind=kind,
            actor="human",
            body={"synthetic": True},
        )
    assert events(connection, other, episode) == []
    assert verify_history(events(connection, tenant, episode))
    assert checkpoint(connection, tenant, episode, LocalObjectStore(tmp_path))
    with pytest.raises(ValueError, match="mature"):
        append_event(
            connection,
            tenant=tenant,
            episode=episode,
            kind="outcome",
            actor="human",
            body={"state": "no_deal", "coverage_complete": True, "evidence": "fixture"},
        )
    with pytest.raises(DBAPIError), connection.begin_nested():
        connection.execute(text("DELETE FROM alert_events WHERE episode_id=:id"), {"id": episode})


def test_hash_binds_time_metadata_and_checkpoint_prefix(connection, tmp_path):
    from datetime import timedelta

    from tailwatch.product.alerts import verify_checkpoint

    tenant, a, b = uuid4(), uuid4(), uuid4()
    connection.execute(text("INSERT INTO tenants VALUES(:id,'integrity fixture')"), {"id": tenant})
    kwargs = dict(
        tenant=tenant, visitor=a, host=b, model_hash="fixture", feature_run_hash="fixture"
    )
    episode = create_episode(connection, synthetic=True, **kwargs)
    assert create_episode(connection, synthetic=False, **kwargs) != episode
    store = LocalObjectStore(tmp_path)
    append_event(connection, tenant=tenant, episode=episode, kind="draft", actor="test", body={})
    retained = checkpoint(connection, tenant, episode, store)
    append_event(connection, tenant=tenant, episode=episode, kind="review", actor="test", body={})
    history = events(connection, tenant, episode)
    assert (
        verify_checkpoint(connection, tenant, episode, store, retained) == history[-1]["event_hash"]
    )
    assert history[-1]["event_hash"].startswith("v2:")
    for change in (
        {"recorded_at": history[0]["recorded_at"] + timedelta(seconds=1)},
        {"model_hash": "different"},
        {"visitor": uuid4()},
        {"synthetic": False},
    ):
        altered = [history[0] | change, history[1]]
        with pytest.raises(ValueError, match="hash mismatch"):
            verify_history(altered)
    wrong = store.put(b'{"tenant":"wrong","episode":"wrong","sequence":1,"head":"wrong"}')
    with pytest.raises(ValueError, match="checkpoint"):
        verify_checkpoint(connection, tenant, episode, store, wrong)
