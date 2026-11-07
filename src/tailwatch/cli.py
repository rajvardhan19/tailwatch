"""Offline-first CLI; importing or running help never contacts a provider."""

import argparse
import csv
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import ValidationError

from tailwatch import __version__
from tailwatch.demo import research_demo
from tailwatch.entities.repository import read_history_fixture
from tailwatch.sources.policy import inventory, load_manifest
from tailwatch.sources.registry import parse_master
from tailwatch.sources.sec.form4 import parse_form4
from tailwatch.sources.sec.plans import plan_candidates


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tailwatch evidence-linked research tools")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fixture", help="show offline fixture mode status (no model scores)")
    commands.add_parser("research-demo", help="export a deterministic synthetic feature run")
    source_inventory = commands.add_parser("inventory", help="inspect declared source feasibility")
    source_inventory.add_argument("manifest", type=Path)
    history = commands.add_parser("validate-history", help="validate dated entity fixture JSON")
    history.add_argument("path", type=Path)
    form4 = commands.add_parser("parse-form4", help="extract a local Form 4 XML/SGML file")
    form4.add_argument("path", type=Path)
    form4.add_argument("--accession", required=True)
    form4.add_argument("--filed-date", required=True, type=date.fromisoformat)
    plans = commands.add_parser(
        "plan-candidates", help="extract passages for manual Item 408 review"
    )
    plans.add_argument("path", type=Path)
    registry = commands.add_parser("inspect-registry", help="validate a local FAA MASTER snapshot")
    registry.add_argument("path", type=Path)
    registry.add_argument("--observed-at", required=True, type=datetime.fromisoformat)
    args = parser.parse_args(argv)
    if args.command == "research-demo":
        print(json.dumps(research_demo(), sort_keys=True))
    if args.command == "inspect-registry":
        try:
            archive = parse_master(
                args.path.read_bytes(),
                source_id="local-inspection",
                observed_at=args.observed_at,
                available_at=datetime.now(UTC),
            )
            print(
                json.dumps(
                    {
                        "digest": archive.digest,
                        "parser_version": archive.parser_version,
                        "row_count": archive.row_count,
                        "accepted": len(archive.records),
                        "quarantined": len(archive.quarantined),
                        "control_review_candidates": sum(
                            r.eligible_for_control_review for r in archive.records
                        ),
                        "tracking_enabled": False,
                    }
                )
            )
        except (OSError, ValueError, csv.Error) as error:
            parser.exit(2, f"Invalid registry: {error}\n")
    if args.command == "plan-candidates":
        try:
            print(
                json.dumps(
                    {
                        "requires_review": True,
                        "candidates": [
                            c.model_dump() for c in plan_candidates(args.path.read_bytes())
                        ],
                    }
                )
            )
        except (OSError, ValueError) as error:
            parser.exit(2, f"Invalid disclosure: {error}\n")
    if args.command == "parse-form4":
        try:
            print(
                parse_form4(
                    args.path.read_bytes(),
                    accession=args.accession,
                    filed_date=args.filed_date,
                    available_at=datetime.now(UTC),
                ).model_dump_json()
            )
        except (OSError, ValueError) as error:
            parser.exit(2, f"Invalid Form 4: {error}\n")
    if args.command == "validate-history":
        try:
            print(read_history_fixture(args.path).model_dump_json())
        except (OSError, ValidationError) as error:
            parser.exit(2, f"Invalid entity history: {error}\n")
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
