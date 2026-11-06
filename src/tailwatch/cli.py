"""Offline-first CLI; importing or running help never contacts a provider."""

import argparse
import json
from collections.abc import Sequence

from tailwatch import __version__


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tailwatch evidence-linked research tools")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fixture", help="show offline fixture mode status (no model scores)")
    args = parser.parse_args(argv)
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
