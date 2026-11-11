"""Explicit operator CLI; registering or enabling a release never bypasses gates."""

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from tailwatch.operations.release import (
    ReleaseManifest,
    activate,
    allowed_model,
    kill,
    register_release,
)


def add_commands(commands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    commands.add_parser("release-status", help="inspect kill switch and production eligibility")
    register = commands.add_parser(
        "release-register", help="validate and retain a release manifest"
    )
    register.add_argument("path", type=Path)
    activation = commands.add_parser(
        "release-activate", help="activate an already validated release"
    )
    activation.add_argument("release_hash")
    activation.add_argument("--actor", required=True)
    shutdown = commands.add_parser(
        "release-kill", help="disable production and append operator audit"
    )
    shutdown.add_argument("--actor", required=True)
    status = commands.add_parser(
        "ops-status", help="report runs, outboxes, review backlog and budgets; exit 1 on alerts"
    )
    status.add_argument("--pending-minutes", type=int, default=60)
    status.add_argument("--sending-minutes", type=int, default=30)
    status.add_argument("--review-hours", type=int, default=24)
    status.add_argument("--running-minutes", type=int, default=120)
    status.add_argument("--budget-fraction", type=float, default=0.9)
    retain = commands.add_parser(
        "checkpoint-alerts", help="retain verified alert-chain heads in an independent store"
    )
    retain.add_argument("--objects", type=Path, required=True)
    check = commands.add_parser(
        "verify-checkpoints", help="compare retained heads with current alert history"
    )
    check.add_argument("manifest")
    check.add_argument("--objects", type=Path, required=True)


def execute(args: argparse.Namespace, parser: argparse.ArgumentParser) -> bool:
    if args.command not in {
        "release-status",
        "release-register",
        "release-activate",
        "release-kill",
        "ops-status",
        "checkpoint-alerts",
        "verify-checkpoints",
    }:
        return False
    url = os.environ.get("TAILWATCH_DATABASE_URL")
    if not url:
        parser.exit(2, "TAILWATCH_DATABASE_URL is required; apply migrations first\n")
    engine = create_engine(url)
    unhealthy = False
    output: dict[str, object]
    try:
        if args.command in {"ops-status", "checkpoint-alerts", "verify-checkpoints"}:
            output = _operations(engine, args)
            unhealthy = bool(
                output.get("alerts") or output.get("broken") or output.get("mismatched")
            )
        elif args.command == "release-register":
            manifest = ReleaseManifest.model_validate_json(args.path.read_bytes())
            output = {
                "release_hash": register_release(engine, manifest),
                "activated": False,
            }
        elif args.command == "release-activate":
            activate(engine, args.release_hash, args.actor)
            output = {"release_hash": args.release_hash, "activated": True}
        elif args.command == "release-kill":
            kill(engine, args.actor)
            output = {"killed": True}
        else:
            with engine.connect() as conn:
                row = (
                    conn.execute(
                        text("SELECT killed,active_release FROM runtime_control WHERE singleton")
                    )
                    .mappings()
                    .one()
                )
                schema = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            output = dict(row) | {
                "schema": schema,
                "eligible_model": allowed_model(engine),
                "note": "API exposure also requires TAILWATCH_ENABLE_PRODUCTION=1",
            }
        print(json.dumps(output, sort_keys=True))
    except (ValueError, OSError, SQLAlchemyError) as error:
        parser.exit(
            2,
            f"Release command rejected: {type(error).__name__}; "
            "inspect gate evidence and configuration\n",
        )
    finally:
        engine.dispose()
    if unhealthy:
        # Machine-readable report already printed; nonzero exit lets schedulers alert.
        raise SystemExit(1)
    return True


def _operations(engine: Engine, args: argparse.Namespace) -> dict[str, object]:
    from tailwatch.sources.objects import LocalObjectStore

    if args.command == "ops-status":
        from tailwatch.operations.monitoring import Thresholds, ops_status

        return ops_status(
            engine,
            Thresholds(
                pending_minutes=args.pending_minutes,
                sending_minutes=args.sending_minutes,
                review_hours=args.review_hours,
                running_minutes=args.running_minutes,
                budget_fraction=args.budget_fraction,
            ),
        )
    from tailwatch.product.alerts import checkpoint_all, verify_checkpoints

    store = LocalObjectStore(args.objects)
    if args.command == "checkpoint-alerts":
        return checkpoint_all(engine, store)
    return verify_checkpoints(engine, store, args.manifest)
