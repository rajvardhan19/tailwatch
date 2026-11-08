# Operations and limited release

## Local dashboard and API

```sh
uv sync --locked
uv run --locked uvicorn tailwatch.product.server:app --host 127.0.0.1 --port 8765
```

Open http://127.0.0.1:8765. Without `TAILWATCH_DATABASE_URL`, the dashboard is a clearly labeled synthetic demo. With a PostgreSQL URL, apply `uv run --locked alembic upgrade head` first and provision a tenant/customer key through an operator session. Keys stay in browser memory, never URLs or local storage. The dashboard supports evidence/history, filtering and watchlists; no real predictive result is fabricated. The Dockerfile serves the same app as a non-root user. `render.yaml` prepares a free demo deployment; no account has been provisioned and no deployment is active.

## Readiness and scheduling

`deadline` takes an explicitly imported exchange trading-session calendar and returns 08:30 America/New_York in UTC, including DST. Holidays are not guessed from weekdays. `claim_run` creates one durable session/config job; duplicate workers cannot claim it. Freeze source and feature manifests before processing. `finish_run` records complete/failed readiness. A stuck running job requires checking its persisted source/feature/delivery checkpoints before operator recovery; never assume it completed or restart external sends blindly.

`readiness` checks required source presence, completeness, content-hash verification, freshness, missingness and declared drift thresholds. Missing sources, outages, corrupt objects, budget exhaustion or incomplete parsing suppress external output. Thresholds must be registered from development observations. Production volume/cost/review-queue alert destinations still need operator configuration.

`DurableBudget` reserves count and micro-USD cost atomically in a committed transaction before provider access, across process restarts. It never refunds failed requests. Daily limits cannot silently change mid-period. Use it for live adapters; do not create a new in-memory budget on every retry. Resend delivery additionally reserves the active release's daily quota; the default free-first release schema caps that at 100 messages/day.

## Backup and recovery

CI creates a custom-format dump of its isolated synthetic PostgreSQL database, restores it to a fresh `_restore_test` database, records the dump hash, then reruns evidence/constraint regressions. This proves the synthetic database recovery path, not production disaster recovery. For production, retain encrypted database dumps and content-addressed raw objects in independently controlled storage; record hashes, schema/app/config versions and alert-chain checkpoints together. Restore into a new isolated database, verify object hashes, replay frozen runs, compare independent alert heads, and inspect all sending/uncertain outbox items before any activation. Never overwrite the original database during a drill.

Rotate API keys by issuing a new key, verifying the intended tenant/scope, then revoking the old digest. Rotate provider credentials through deployment secrets. Do not log or commit credentials. A database administrator can bypass application controls; separate operator, application and backup access before launch.

## Release guard and rollback

`ReleaseManifest` requires G0–G4 evidence references, exact app/model/config/schema hashes, a real sealed-test PASS, ten distinct successful real shadow runs matching the last ten declared trading sessions, approved tenant IDs, limited rollout/daily-mail caps, a restore-drill digest, rights expiry and release expiry. No such real evidence currently exists, so production activation is blocked.

`register_release` validates and appends the immutable manifest. `activate` revalidates it, checks the migrated schema and deployed `TAILWATCH_APPLICATION_COMMIT` / `TAILWATCH_CONFIG_HASH`, and appends an operator event. API and email delivery independently recheck the active model, tenant allowlist and rights/expiry; setting an environment flag alone cannot authorize production. `kill` disables production while retaining all audit history. Rollback means kill, select a previously registered schema-compatible application/model/config release, restore its code configuration without deleting history, revalidate gates, then activate that release. Never downgrade production migrations to erase audit data.

References are not proof by themselves: independently review coverage/rights, test data custody, trading-calendar completeness and restore evidence. Ten shadow sessions and source-provider setup cannot be truthfully completed using synthetic tests or in one implementation session. The repository delivers the mechanisms; those real-world gates remain pending.

## Daily production readiness

Activation and every real API/delivery check require a completed, non-synthetic run for the latest declared trading session whose 08:30 America/New_York deadline has passed. At that deadline a missing, running or failed new session blocks production; a prior successful shadow run is insufficient. Before the deadline, the previous session can remain eligible only while its source readiness is fresh. Weekends/holidays are taken from the declared calendar, not guessed.

`readiness` records `valid_until` from the earliest expiry of the required sources. Terminal runs used for production must retain that timestamp. Missing, invalid, naive or expired freshness timestamps block output. This check does not discover outages on its own: ingestion/monitoring workers must calculate truthful source health and freshness bounds.

Release manifests now require `calendar_valid_through` covering their entire lifetime and a complete ordered trading-session calendar for that interval. The ten shadow IDs reference consecutive sessions within that calendar; future sessions are allowed in the calendar but cannot count as completed shadow evidence. Older manifests without coverage bounds fail closed and must be registered anew with reviewed evidence. No migration or production data rewrite is needed.

## Live source budget binding

Live SEC, flight and ADSB.lol adapters require a budget whose source configuration matches the request. `RequestBudget` is for fixtures only; passing it for a live source fails before transport access. Live workers inject `DurableBudget(engine, source, utc_day)`. Custom budget implementations must honor both `validate_for(source)` and `reserve(cost_microusd)`.

The durable ledger uses the database UTC date to reject yesterday's or future-dated budget before reserving. Recreate the budget for the new UTC day at rollover; never replay requests against yesterday's unused quota. Concurrent workers and restarts share the same source/day count and cost limits. A changed daily limit is rejected rather than silently resetting the ledger. Retries remain charged even when provider access fails.

## Checkpointed pipeline execution

Migration `0008` adds immutable stage checkpoints and pipeline attempt events. `run_pipeline` freezes stage order, code revision, configuration and synthetic mode into the job key. A PostgreSQL advisory lock prevents concurrent workers from executing the same job. Every stage writes a content-addressed artifact, verifies it, and commits its checkpoint before proceeding. Resume verifies retained hashes and skips completed stages; corrupt objects invalidate the run rather than silently replaying unverified output.

Stage callbacks must be deterministic artifact transformations. A crash between artifact creation and checkpoint commit may repeat the stage. Do not put external messages or other non-idempotent effects in callbacks; use the separately reconciled outbox. Failed runs require `resume=True`; interrupted running jobs can be recovered once their database connection/lock is gone. Attempt events retain error types without callback text or secrets. Recovery never counts a synthetic job toward a real release.

An offline synthetic job can be exercised against the migrated local database:

```sh
uv run --locked alembic upgrade head
uv run --locked tailwatch run-fixture-job --objects .data/objects --session 2026-11-06
# Explicit retry after a failed attempt:
uv run --locked tailwatch run-fixture-job --objects .data/objects --session 2026-11-06 --resume
```

Use the returned `config_hash` when reviewing that pipeline's run records. Production configuration must supply a reviewed calendar, real source transformations and truthful readiness callback, plus an external scheduler invoking the due session. The fixture command does not poll providers or send mail.

## Operator release commands

The CLI exposes `release-status`, `release-register MANIFEST.json`, `release-activate HASH --actor OPERATOR`, and `release-kill --actor OPERATOR`. These use `TAILWATCH_DATABASE_URL`; registration does not activate, and activation uses the same schema/app/config/model/provenance/readiness/rights checks as the API. Status reports the kill switch and eligible model, not credentials. Database/gate failures exit nonzero with a redacted error category. Empty actor identities are rejected. These commands have not been used to activate a real release.
