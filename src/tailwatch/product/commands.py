"""Operator workflow commands: score/draft alerts. None of them approves or activates."""

import argparse
import json
import os
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError

from tailwatch.features.runs import FeatureRun

COMMANDS = {"draft-alerts"}


def add_commands(commands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    draft = commands.add_parser(
        "draft-alerts", help="score a frozen feature run and append review-only drafts"
    )
    draft.add_argument("--run", type=Path, required=True, help="FeatureRun or research-demo JSON")
    draft.add_argument("--model-hash", required=True, help="retained candidate artifact digest")
    draft.add_argument("--objects", type=Path, required=True)
    draft.add_argument("--tenant", type=UUID, required=True)
    draft.add_argument("--threshold", type=float, required=True)
    draft.add_argument("--budget", type=int, required=True)
    draft.add_argument("--actor", required=True)
    draft.add_argument(
        "--public-evidence",
        action="append",
        default=[],
        help="evidence ID whose external display rights were reviewed (repeatable)",
    )


def load_run(path: Path) -> FeatureRun:
    """Accept a canonical FeatureRun or the research-demo export with its committed hash."""
    data = json.loads(path.read_bytes())
    if isinstance(data, dict) and "run" in data and "run_hash" in data:
        run = FeatureRun.model_validate(data["run"])
        if run.digest() != data["run_hash"]:
            raise ValueError("feature run hash mismatch")
        return run
    return FeatureRun.model_validate(data)


def _engine(parser: argparse.ArgumentParser) -> Engine:
    url = os.environ.get("TAILWATCH_DATABASE_URL")
    if not url:
        parser.exit(2, "TAILWATCH_DATABASE_URL is required; apply migrations first\n")
    return create_engine(url)


def execute(args: argparse.Namespace, parser: argparse.ArgumentParser) -> bool:
    if args.command not in COMMANDS:
        return False
    engine = _engine(parser)
    try:
        output = _draft(engine, args)
        print(json.dumps(output, sort_keys=True))
    except (OSError, ValueError, SQLAlchemyError) as error:
        parser.exit(2, f"{args.command} rejected: {type(error).__name__}\n")
    finally:
        engine.dispose()
    return True


def _draft(engine: Engine, args: argparse.Namespace) -> dict[str, object]:
    from tailwatch.product.scoring import (
        draft_alerts,
        load_artifact,
        score_run,
        scoring_record,
        select_candidates,
    )
    from tailwatch.sources.objects import LocalObjectStore

    store = LocalObjectStore(args.objects)
    artifact = load_artifact(store, args.model_hash)
    run = load_run(args.run)
    scored = score_run(run, artifact)
    selected = select_candidates(scored, threshold=args.threshold, budget=args.budget)
    score_digest = store.put(
        scoring_record(run, artifact, scored, threshold=args.threshold, budget=args.budget)
    )
    permitted = set(args.public_evidence)
    if run.synthetic:
        # Synthetic fixture references carry no third-party display rights question.
        permitted |= {e for s in selected for e in s.evidence if e.startswith("synthetic:")}
    with engine.begin() as conn:
        results = draft_alerts(
            conn,
            tenant=args.tenant,
            run=run,
            artifact=artifact,
            candidates=selected,
            actor=args.actor,
            public_evidence=frozenset(permitted),
            score_digest=score_digest,
        )
    return {
        "run_hash": run.digest(),
        "model_hash": args.model_hash,
        "score_digest": score_digest,
        "synthetic": run.synthetic,
        "scored": sum(s.status == "scored" for s in scored),
        "unscorable": sum(s.status == "unscorable" for s in scored),
        "selected": len(selected),
        "drafted": [str(r.episode_id) for r in results if r.status == "drafted"],
        "existing_episodes": [str(r.episode_id) for r in results if r.status != "drafted"],
        "approval": "human_required",
        "delivery_enabled": False,
    }
