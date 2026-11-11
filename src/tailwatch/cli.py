"""Offline-first CLI; importing or running help never contacts a provider."""

import argparse
import csv
import json
import os
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import ValidationError

from tailwatch import __version__
from tailwatch.demo import research_demo
from tailwatch.entities.repository import read_history_fixture
from tailwatch.operations import commands as operator_commands
from tailwatch.product import commands as product_commands
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
    job = commands.add_parser("run-fixture-job", help="run/resume checkpointed synthetic research")
    job.add_argument("--objects", type=Path, required=True)
    job.add_argument("--session", type=date.fromisoformat, required=True)
    job.add_argument("--resume", action="store_true")
    onboarding = commands.add_parser(
        "provision-workspace", help="create tenant, customer key and queued recipient verification"
    )
    onboarding.add_argument("--name", required=True)
    onboarding.add_argument("--email", required=True)
    onboarding.add_argument("--output", type=Path, required=True)
    report = commands.add_parser("research-report", help="retain offline development diagnostics")
    report.add_argument("path", type=Path)
    report.add_argument("--objects", type=Path, required=True)
    fit = commands.add_parser(
        "fit-candidate", help="fit and retain a calibrated candidate; never activates it"
    )
    fit.add_argument("path", type=Path, help="validated research input (research-report format)")
    fit.add_argument("--objects", type=Path, required=True)
    fit.add_argument("--feature", action="append", dest="features", help="override features")
    trace = commands.add_parser("inspect-trace", help="inspect an already authorized local archive")
    trace.add_argument("path", type=Path)
    trace.add_argument("--icao", required=True)
    trace.add_argument("--available-at", type=datetime.fromisoformat, required=True)
    trace.add_argument("--objects", type=Path, required=True)
    operator_commands.add_commands(commands)
    product_commands.add_commands(commands)
    sec_capture = commands.add_parser(
        "capture-sec", help="capture bounded live SEC submissions metadata"
    )
    sec_capture.add_argument("manifest", type=Path)
    sec_capture.add_argument("--source", required=True)
    sec_capture.add_argument("--cik", action="append", required=True)
    sec_capture.add_argument("--objects", type=Path, required=True)
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
    if operator_commands.execute(args, parser) or product_commands.execute(args, parser):
        return 0
    if args.command in {"research-report", "fit-candidate", "inspect-trace"}:
        from tailwatch.sources.objects import LocalObjectStore

        try:
            store = LocalObjectStore(args.objects)
            if args.command == "research-report":
                from tailwatch.research.report import ReportInput, build_report

                digest = build_report(
                    ReportInput.model_validate_json(args.path.read_bytes()), store
                )
                print(json.dumps({"report_hash": digest, "promotion_allowed": False}))
            elif args.command == "fit-candidate":
                from tailwatch.product.scoring import model_digest
                from tailwatch.research.models import fit_candidate
                from tailwatch.research.report import ReportInput

                request = ReportInput.model_validate_json(args.path.read_bytes())
                features = tuple(args.features or request.features)
                artifact = fit_candidate(
                    list(request.examples), request.experiment, features, request.fitted_at
                )
                digest = store.put(artifact.model_dump_json().encode())
                if digest != model_digest(artifact):
                    raise ValueError("retained artifact commitment mismatch")
                print(
                    json.dumps(
                        {
                            "model_hash": digest,
                            "features": list(features),
                            "synthetic": artifact.synthetic,
                            "calibrated": artifact.calibration is not None,
                            "promotion_allowed": False,
                        }
                    )
                )
            else:
                from tailwatch.sources.traces import parse_trace

                points = parse_trace(
                    args.path.read_bytes(),
                    approved_icao=args.icao,
                    available_at=args.available_at,
                    store=store,
                )
                print(
                    json.dumps(
                        {
                            "points": len(points),
                            "usable": sum(p.usable for p in points),
                            "complete_coverage": False,
                            "tracking_enabled": False,
                        }
                    )
                )
        except (OSError, ValueError) as error:
            parser.exit(2, f"Offline input rejected: {type(error).__name__}\n")
        return 0
    if args.command == "capture-sec":
        from sqlalchemy import create_engine
        from sqlalchemy.exc import SQLAlchemyError

        from tailwatch.sources.objects import LocalObjectStore
        from tailwatch.sources.sec.research import capture_submissions

        url, agent = (
            os.environ.get("TAILWATCH_DATABASE_URL"),
            os.environ.get("TAILWATCH_SEC_USER_AGENT"),
        )
        if not url or not agent:
            parser.exit(2, "Database URL and SEC User-Agent environment settings are required\n")
        engine = create_engine(url)
        try:
            manifest = load_manifest(args.manifest)
            source = next((s for s in manifest.sources if s.source_id == args.source), None)
            if source is None:
                raise ValueError("source absent from manifest")
            print(
                json.dumps(
                    capture_submissions(
                        engine, source, tuple(args.cik), LocalObjectStore(args.objects), agent
                    ),
                    sort_keys=True,
                )
            )
        except (OSError, ValueError, SQLAlchemyError) as error:
            parser.exit(2, f"SEC research capture rejected: {type(error).__name__}\n")
        finally:
            engine.dispose()
        return 0
    if args.command == "provision-workspace":
        from sqlalchemy import create_engine

        from tailwatch.product.onboarding import provision_workspace

        url = os.environ.get("TAILWATCH_DATABASE_URL")
        if not url:
            parser.exit(2, "TAILWATCH_DATABASE_URL is required; apply migrations first\n")
        engine = create_engine(url)
        try:
            print(
                json.dumps(
                    provision_workspace(
                        engine, name=args.name, email=args.email, output=args.output
                    ),
                    sort_keys=True,
                )
            )
        except (OSError, ValueError) as error:
            parser.exit(2, f"Provisioning failed: {type(error).__name__}\n")
        finally:
            engine.dispose()
    if args.command == "run-fixture-job":
        from sqlalchemy import create_engine

        from tailwatch.operations.pipeline import Stage, run_pipeline
        from tailwatch.sources.objects import LocalObjectStore

        url = os.environ.get("TAILWATCH_DATABASE_URL")
        if not url:
            parser.exit(2, "TAILWATCH_DATABASE_URL is required; apply migrations first\n")
        engine = create_engine(url)
        try:
            result = run_pipeline(
                engine,
                LocalObjectStore(args.objects),
                session=args.session,
                stages=(
                    Stage(
                        "features", lambda _: json.dumps(research_demo(), sort_keys=True).encode()
                    ),
                ),
                code_revision=__version__ + ":research-demo-v1",
                configuration={},
                synthetic=True,
                health=lambda _: {"ready": True, "synthetic": True},
                resume=args.resume,
            )
            print(json.dumps(result, sort_keys=True))
        except (OSError, ValueError) as error:
            parser.exit(2, f"Fixture job failed: {type(error).__name__}\n")
        finally:
            engine.dispose()
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
                    "model_status": "not_activated",
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
