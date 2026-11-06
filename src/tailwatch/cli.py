"""Offline-first CLI; importing or running help never contacts a provider."""

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from tailwatch import __version__
from tailwatch.sources.policy import inventory, load_manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tailwatch evidence-linked research tools")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fixture", help="show offline fixture mode status (no model scores)")
    source_inventory = commands.add_parser("inventory", help="inspect declared source feasibility")
    source_inventory.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    if args.command == "inventory":
        try:
            print(inventory(load_manifest(args.manifest), datetime.now(UTC)))
        except (OSError, ValidationError) as error:
            parser.exit(2, f"Invalid source manifest: {error}\n")
    if args.command == "fixture":
        print(
            json.dumps(
                {
                    "mode": "fixture",
                    "synthetic": True,
                    "delivery_enabled": False,
                    "model_status": "not_implemented",
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
