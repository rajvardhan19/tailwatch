# Local checks and CI

Run `uv sync --locked`, `make check`, and `make test` from the repository root. The supported Python range is 3.11–3.14; CI checks every minor version. Dependencies are resolved in `uv.lock`. No provider credentials are used by CI. Workflow permissions are read-only; dependency caching is keyed by the lockfile; jobs have ten-minute limits and upload no source data artifacts.

For the separate PostgreSQL integration suite, start a disposable development service:

```sh
docker compose up -d --wait
export TAILWATCH_TEST_DATABASE_URL=postgresql+psycopg://tailwatch:tailwatch@localhost:5432/tailwatch_test
uv run --locked pytest tests/integration
```

The service binds only to localhost. Its credentials are disposable development defaults. Integration tests fail when their database is missing; they do not silently skip. Do not point the test URL at production. PostgreSQL/PostGIS is also provisioned as an isolated GitHub Actions service. A checked-in constraint probe deliberately attempts an invalid insert and must observe a database constraint failure.

GitHub Actions use immutable commit references; the PostGIS image pins a major/minor tag. Image digest pinning can be added when publishing a deployment image. CI checks migration upgrade, integration behavior, downgrade, re-upgrade, and integration behavior again on its disposable database. Repository branch-protection settings are not changed automatically.

## Source feasibility

```sh
uv run --locked tailwatch inventory config/sources.fixture.json
```

The checked-in manifest contains synthetic data only. `sector` remains unset and G0 remains `not_established`: no real provider coverage, historical overlap, or use agreement has been supplied. The command deliberately does not turn the presence of a sample into a feasibility PASS. Before live work, register permitted capabilities, reviewed permission evidence and expiry, explicit retention/derived-output/distribution rights, source-specific latency, measured coverage samples, and request/cost limits. Freeze the sector only after that inventory supports the choice.

`RequestBudget` reserves integer micro-USD and request count before each call/page/retry, conservatively retaining failed-call reservations. It is a thread-safe **per-run** guard, not a persistent account/day ledger. No live adapter is present. Durable provider-wide budget accounting and restart reconciliation are prerequisites to adding a live paid adapter; recreating this object must not be used to reset a production spending cap.

## Temporal evidence store

To apply the provenance migration to the disposable Compose database:

```sh
export TAILWATCH_DATABASE_URL=postgresql+psycopg://tailwatch:tailwatch@localhost:5432/tailwatch_test
uv run --locked alembic upgrade head
```

`EvidenceRepository` uses a caller-owned SQLAlchemy connection/transaction. Register a source, capture raw bytes through `LocalObjectStore`, begin a snapshot with a hashed manifest and expected count, append typed `Fact` revisions, then publish. Each snapshot is an **incremental batch**, not a replacement of every prior source row. A correction links its prior fact ID; an expired validity interval can remove applicability without deleting history. Tombstone semantics for domain entities come with their later domain models.

Snapshots with missing records or any quarantined parse failure cannot publish; a failed batch must be replaced by a new snapshot ID after remediation. Counts are a local completeness guard; external SEC/provider reconciliation is still required in later adapters. Published batches cannot accept later rows. Same-ID retries require identical data. Store objects atomically by SHA-256 and verify checksums on reads. Object capture preceding a failed database transaction can leave an unreferenced object; garbage collection is deferred and must preserve all referenced hashes.

Operational as-of queries use both declared publication and the database-stamped recording time, preventing an imported historical timestamp from backdating deployed-system knowledge. They select the latest visible revision before applying its corrected validity interval. Publish in a short transaction, commit it, and freeze consumer manifests using committed snapshots; historical concurrent transaction visibility must ultimately be represented by the run manifest (C18), not inferred from timestamps alone.

Public-information simulation is a separate explicit enum and requires a nonnegative latency policy. It uses evidenced source-publication time, excludes unknown publication times, and does not pretend that historical reconstruction was operational observation. Callers must retain the replay mode and latency in reports/manifests. Future ownership approval and LLM reconstruction policies belong to the corresponding domain steps, not this generic fact store.

Database triggers reject updates, deletes, truncation, invalid revision chains, and publication before evidence processing/review. These guards protect application writes, not a malicious database owner who can disable triggers. Independent audit checkpoints arrive in C25. Downgrading this foundation deletes provenance tables: run downgrade roundtrips only on isolated test databases. Do not use downgrade as a production data-recovery procedure.
