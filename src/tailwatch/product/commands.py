"""Operator workflow commands. None of them approves alerts or activates a release.

Local sending writes rendered messages to a development mailbox directory. External
sending additionally needs TAILWATCH_ENABLE_PRODUCTION=1, provider credentials and an
active validated release; the delivery code rechecks those gates for every message.
"""

import argparse
import json
import os
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError

from tailwatch.features.runs import FeatureRun

if TYPE_CHECKING:
    from tailwatch.product.delivery import Sender
    from tailwatch.product.messages import LinkSigner


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

    key = commands.add_parser("issue-key", help="issue an API key into an owner-only file")
    key.add_argument("--tenant", type=UUID, required=True)
    key.add_argument("--scope", choices=("customer", "analyst"), required=True)
    key.add_argument("--output", type=Path, required=True)
    revoke = commands.add_parser("revoke-key", help="revoke an API key by its SHA-256 digest")
    revoke.add_argument("key_hash")

    request = commands.add_parser(
        "request-verification", help="queue a new recipient verification message"
    )
    request.add_argument("subscription", type=UUID)
    request.add_argument("--actor", required=True)

    def sending(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--mail-dir", type=Path, help="local development mailbox")
        parser.add_argument(
            "--external", action="store_true", help="send through the configured provider"
        )

    sending(commands.add_parser("send-verifications", help="send queued verification links"))
    enqueue = commands.add_parser(
        "outbox-enqueue", help="queue current approvals for verified subscriptions"
    )
    enqueue.add_argument("--tenant", type=UUID, required=True)
    enqueue.add_argument("--actor", required=True)
    sending(commands.add_parser("outbox-deliver", help="send pending approved alerts"))
    reconcile = commands.add_parser(
        "outbox-reconcile", help="mark abandoned sends uncertain; optionally retry idempotently"
    )
    reconcile.add_argument("--stale-minutes", type=int, default=30)
    reconcile.add_argument(
        "--retry", action="store_true", help="retry uncertain alerts with provider idempotency"
    )
    sending(reconcile)
    resolve = commands.add_parser(
        "outbox-resolve", help="record an audited decision for an uncertain delivery"
    )
    resolve.add_argument("delivery", type=UUID)
    resolve.add_argument("--state", choices=("sent", "cancelled"), required=True)
    resolve.add_argument("--actor", required=True)
    resolve.add_argument("--evidence", required=True, help="where the outcome was confirmed")
    resolve.add_argument("--provider-id")


def load_run(path: Path) -> FeatureRun:
    """Accept a canonical FeatureRun or the research-demo export with its committed hash."""
    data = json.loads(path.read_bytes())
    if isinstance(data, dict) and "run" in data and "run_hash" in data:
        run = FeatureRun.model_validate(data["run"])
        if run.digest() != data["run_hash"]:
            raise ValueError("feature run hash mismatch")
        return run
    return FeatureRun.model_validate(data)


def execute(args: argparse.Namespace, parser: argparse.ArgumentParser) -> bool:
    handler = HANDLERS.get(args.command)
    if handler is None:
        return False
    url = os.environ.get("TAILWATCH_DATABASE_URL")
    if not url:
        parser.exit(2, "TAILWATCH_DATABASE_URL is required; apply migrations first\n")
    engine = create_engine(url)
    try:
        print(json.dumps(handler(engine, args), sort_keys=True))
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


def _sender(args: argparse.Namespace) -> "Sender":
    from tailwatch.product.delivery import FileSender, ResendSender

    if args.external:
        if os.environ.get("TAILWATCH_ENABLE_PRODUCTION") != "1":
            raise ValueError("external sending requires TAILWATCH_ENABLE_PRODUCTION=1")
        return ResendSender(
            os.environ.get("TAILWATCH_RESEND_API_KEY", ""),
            os.environ.get("TAILWATCH_EMAIL_FROM", ""),
        )
    if args.mail_dir is None:
        raise ValueError("--mail-dir is required unless --external is used")
    return FileSender(args.mail_dir)


def _signer() -> "LinkSigner":
    from tailwatch.product.messages import signer_from_env

    signer = signer_from_env()
    if signer is None:
        raise ValueError("TAILWATCH_LINK_SECRET and TAILWATCH_PUBLIC_URL are required")
    return signer


def _drain(step: Callable[[], str], limit: int) -> dict[str, int]:
    if limit < 1:
        raise ValueError("positive limit required")
    counts: Counter[str] = Counter()
    for _ in range(limit):
        outcome = step()
        if outcome == "idle":
            break
        counts[outcome] += 1
        if outcome == "deferred":
            break  # Quota or release gate: later messages would be deferred as well.
    return dict(counts)


def _issue_key(engine: Engine, args: argparse.Namespace) -> dict[str, object]:
    from tailwatch.product.onboarding import issue_key_file

    return dict(issue_key_file(engine, tenant=args.tenant, scope=args.scope, output=args.output))


def _revoke_key(engine: Engine, args: argparse.Namespace) -> dict[str, object]:
    from tailwatch.product.onboarding import revoke_key

    revoke_key(engine, args.key_hash)
    return {"key_hash": args.key_hash, "revoked": True}


def _request(engine: Engine, args: argparse.Namespace) -> dict[str, object]:
    from tailwatch.product.delivery import request_verification

    with engine.begin() as conn:
        message = request_verification(conn, args.subscription, args.actor)
    return {"verification_message_id": str(message), "sent": False}


def _send_verifications(engine: Engine, args: argparse.Namespace) -> dict[str, object]:
    from tailwatch.product.delivery import send_verification_one

    sender, signer = _sender(args), _signer()
    counts = _drain(
        lambda: send_verification_one(
            engine, sender, signer=signer, production_enabled=bool(args.external)
        ),
        args.limit,
    )
    return {"external": bool(args.external), "outcomes": counts}


def _enqueue(engine: Engine, args: argparse.Namespace) -> dict[str, object]:
    from tailwatch.product.delivery import enqueue_approved

    with engine.begin() as conn:
        created = enqueue_approved(conn, args.tenant, args.actor)
    return {"enqueued": [str(d) for d in created]}


def _deliver(engine: Engine, args: argparse.Namespace) -> dict[str, object]:
    from tailwatch.product.delivery import deliver_one

    sender, signer = _sender(args), _signer()
    counts = _drain(
        lambda: deliver_one(
            engine, sender, live_allowed=lambda _: bool(args.external), signer=signer
        ),
        args.limit,
    )
    return {"external": bool(args.external), "outcomes": counts}


def _reconcile(engine: Engine, args: argparse.Namespace) -> dict[str, object]:
    from tailwatch.product.delivery import reconcile_abandoned, reconcile_uncertain

    if args.stale_minutes < 1:
        raise ValueError("positive staleness interval required")
    with engine.begin() as conn:
        abandoned = reconcile_abandoned(
            conn, datetime.now(UTC) - timedelta(minutes=args.stale_minutes)
        )
    retried: dict[str, int] = {}
    if args.retry:
        retried = reconcile_uncertain(
            engine,
            _sender(args),
            live_allowed=lambda _: bool(args.external),
            signer=_signer(),
            limit=args.limit,
        )
    return {"marked_uncertain": abandoned, "retried": retried}


def _resolve(engine: Engine, args: argparse.Namespace) -> dict[str, object]:
    from tailwatch.product.delivery import resolve_delivery

    with engine.begin() as conn:
        resolve_delivery(
            conn,
            args.delivery,
            state=args.state,
            actor=args.actor,
            evidence=args.evidence,
            provider_id=args.provider_id,
        )
    return {"delivery_id": str(args.delivery), "state": args.state}


HANDLERS: dict[str, Callable[[Engine, argparse.Namespace], dict[str, object]]] = {
    "draft-alerts": _draft,
    "issue-key": _issue_key,
    "revoke-key": _revoke_key,
    "request-verification": _request,
    "send-verifications": _send_verifications,
    "outbox-enqueue": _enqueue,
    "outbox-deliver": _deliver,
    "outbox-reconcile": _reconcile,
    "outbox-resolve": _resolve,
}
