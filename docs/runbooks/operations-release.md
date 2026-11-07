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
