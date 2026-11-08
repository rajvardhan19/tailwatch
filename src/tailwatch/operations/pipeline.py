"""Restartable pure-artifact stages; notifications stay in the separate outbox."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Connection, Engine, text

from tailwatch.operations.runtime import claim_run, config_digest, finish_run
from tailwatch.sources.objects import ObjectStore


@dataclass(frozen=True)
class Stage:
    name: str
    execute: Callable[[dict[str, bytes]], bytes]


def _event(conn: Connection, run: str, kind: str, detail: dict[str, object]) -> None:
    conn.execute(
        text(
            "INSERT INTO pipeline_events(run_id,kind,detail) "
            "VALUES(:run,:kind,CAST(:detail AS jsonb))"
        ),
        {"run": run, "kind": kind, "detail": json.dumps(detail, allow_nan=False)},
    )


def run_pipeline(
    engine: Engine,
    store: ObjectStore,
    *,
    session: date,
    stages: tuple[Stage, ...],
    code_revision: str,
    configuration: dict[str, object],
    synthetic: bool,
    health: Callable[[dict[str, bytes]], dict[str, object]],
    resume: bool = False,
) -> dict[str, Any]:
    """Hold a process-independent advisory lock and commit each verified artifact.

    Stages must be deterministic, restart-safe transformations with no external
    side effects. Crash before checkpoint can repeat a stage. External sends are
    deliberately excluded; only the outbox can dispatch them.
    """
    names = [stage.name for stage in stages]
    if not names or any(not name.strip() for name in names) or len(set(names)) != len(names):
        raise ValueError("ordered distinct named stages required")
    if not code_revision.strip():
        raise ValueError("code revision required")
    config = config_digest(
        {
            "stages": names,
            "code_revision": code_revision,
            "configuration": configuration,
            "synthetic": synthetic,
        }
    )
    run = claim_run(engine, session, config, synthetic)
    with engine.connect() as lock:
        if run is None:
            run = str(
                lock.execute(
                    text(
                        "SELECT run_id FROM scheduled_runs "
                        "WHERE session_date=:day AND config_hash=:config"
                    ),
                    {"day": session, "config": config},
                ).scalar_one()
            )
        acquired = lock.execute(
            text("SELECT pg_try_advisory_lock(hashtextextended(:run,0))"), {"run": run}
        ).scalar_one()
        lock.commit()
        if not acquired:
            raise ValueError("pipeline already running in another worker")
        try:
            status = lock.execute(
                text("SELECT status FROM scheduled_runs WHERE run_id=:id"), {"id": run}
            ).scalar_one()
            lock.commit()
            with engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            "SELECT stage,ordinal,artifact_hash FROM pipeline_checkpoints "
                            "WHERE run_id=:id ORDER BY ordinal"
                        ),
                        {"id": run},
                    )
                    .mappings()
                    .all()
                )
            outputs: dict[str, bytes] = {}
            hashes: dict[str, str] = {}
            # Verify every retained object, even on a completed-run replay.
            for index, row in enumerate(rows):
                if index >= len(stages) or row["ordinal"] != index or row["stage"] != names[index]:
                    raise ValueError("checkpoint stage order differs from frozen configuration")
                outputs[row["stage"]] = store.get(row["artifact_hash"])
                hashes[row["stage"]] = row["artifact_hash"]
            if status == "complete":
                if len(rows) != len(stages):
                    raise ValueError("completed run lacks checkpoints")
                return {"run_id": run, "config_hash": config, "status": status, "artifacts": hashes}
            if status == "failed" and not resume:
                raise ValueError("failed pipeline requires explicit resume")
            with engine.begin() as conn:
                if status == "failed":
                    conn.execute(
                        text(
                            "UPDATE scheduled_runs SET status='running',completed_at=NULL "
                            "WHERE run_id=:id"
                        ),
                        {"id": run},
                    )
                _event(conn, run, "resumed" if rows or status == "failed" else "started", {})
            for index in range(len(rows), len(stages)):
                stage = stages[index]
                artifact = stage.execute(dict(outputs))
                digest = store.put(artifact)
                outputs[stage.name] = store.get(digest)
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "INSERT INTO pipeline_checkpoints(run_id,stage,ordinal,artifact_hash) "
                            "VALUES(:run,:stage,:ordinal,:hash)"
                        ),
                        {"run": run, "stage": stage.name, "ordinal": index, "hash": digest},
                    )
                    _event(conn, run, "checkpoint", {"stage": stage.name, "artifact_hash": digest})
                hashes[stage.name] = digest
            result = health(dict(outputs))
            finish_run(engine, run, result)
            with engine.begin() as conn:
                _event(conn, run, "finished", {"ready": result.get("ready") is True})
            return {
                "run_id": run,
                "config_hash": config,
                "status": "complete" if result.get("ready") is True else "failed",
                "artifacts": hashes,
            }
        except Exception as error:
            # Preserve only the error type: callbacks may contain sensitive data.
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE scheduled_runs SET status='failed',completed_at=clock_timestamp() "
                        "WHERE run_id=:id AND status IN ('running','complete')"
                    ),
                    {"id": run},
                )
                _event(conn, run, "failed", {"error_type": type(error).__name__})
            raise
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(hashtextextended(:run,0))"), {"run": run})
            lock.commit()
