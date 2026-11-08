from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import text

from tailwatch.operations.pipeline import Stage, run_pipeline
from tailwatch.sources.objects import LocalObjectStore

pytestmark = pytest.mark.integration


def test_failure_resume_preserves_checkpoint_and_corruption_blocks_replay(database, tmp_path):
    store = LocalObjectStore(tmp_path)
    calls = []
    broken = True

    def first(_):
        calls.append("first")
        return b"retained source"

    def second(previous):
        calls.append("second")
        if broken:
            raise ValueError("must never appear in audit details")
        return previous["first"] + b" -> feature"

    kwargs = dict(
        session=datetime.now(UTC).date(),
        stages=(Stage("first", first), Stage("second", second)),
        code_revision=uuid4().hex,
        configuration={},
        synthetic=True,
        health=lambda _: {"ready": True},
    )
    with pytest.raises(ValueError):
        run_pipeline(database, store, **kwargs)
    with pytest.raises(ValueError, match="explicit resume"):
        run_pipeline(database, store, **kwargs)
    broken = False
    result = run_pipeline(database, store, resume=True, **kwargs)
    assert result["status"] == "complete" and calls == ["first", "second", "second"]
    assert run_pipeline(database, store, **kwargs) == result
    with database.connect() as conn:
        details = (
            conn.execute(
                text("SELECT detail FROM pipeline_events WHERE run_id=:id"),
                {"id": result["run_id"]},
            )
            .scalars()
            .all()
        )
    assert "must never appear" not in str(details)
    digest = result["artifacts"]["first"]
    (tmp_path / digest[:2] / digest).write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="checksum"):
        run_pipeline(database, store, **kwargs)
    with database.connect() as conn:
        assert (
            conn.execute(
                text("SELECT status FROM scheduled_runs WHERE run_id=:id"), {"id": result["run_id"]}
            ).scalar_one()
            == "failed"
        )


def test_second_worker_cannot_execute_same_pipeline(database, tmp_path):
    entered, release = Event(), Event()

    def slow(_):
        entered.set()
        assert release.wait(10)
        return b"fixture"

    kwargs = dict(
        session=datetime.now(UTC).date(),
        stages=(Stage("only", slow),),
        code_revision=uuid4().hex,
        configuration={},
        synthetic=True,
        health=lambda _: {"ready": True},
    )
    store = LocalObjectStore(tmp_path)
    with ThreadPoolExecutor(max_workers=1) as workers:
        future = workers.submit(run_pipeline, database, store, **kwargs)
        try:
            assert entered.wait(10)
            with pytest.raises(ValueError, match="another worker"):
                run_pipeline(database, store, **kwargs)
        finally:
            release.set()
        assert future.result()["status"] == "complete"
