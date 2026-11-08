"""Explicit operator CLI; registering or enabling a release never bypasses gates."""

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, text
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


def execute(args: argparse.Namespace, parser: argparse.ArgumentParser) -> bool:
    if args.command not in {
        "release-status",
        "release-register",
        "release-activate",
        "release-kill",
    }:
        return False
    url = os.environ.get("TAILWATCH_DATABASE_URL")
    if not url:
        parser.exit(2, "TAILWATCH_DATABASE_URL is required; apply migrations first\n")
    engine = create_engine(url)
    try:
        if args.command == "release-register":
            manifest = ReleaseManifest.model_validate_json(args.path.read_bytes())
            output: dict[str, object] = {
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
    return True
