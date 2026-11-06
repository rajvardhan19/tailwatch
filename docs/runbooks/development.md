# Local checks and CI

Run `uv sync --locked`, `make check`, and `make test` from the repository root. The supported Python range is 3.11–3.14; CI checks every minor version. Dependencies are resolved in `uv.lock`. No provider credentials are used by CI. Workflow permissions are read-only; dependency caching is keyed by the lockfile; jobs have ten-minute limits and upload no source data artifacts.

For the separate PostgreSQL integration suite, start a disposable development service:

```sh
docker compose up -d --wait
export TAILWATCH_TEST_DATABASE_URL=postgresql+psycopg://tailwatch:tailwatch@localhost:5432/tailwatch_test
uv run --locked pytest tests/integration
```

The service binds only to localhost. Its credentials are disposable development defaults. Integration tests fail when their database is missing; they do not silently skip. Do not point the test URL at production. PostgreSQL/PostGIS is also provisioned as an isolated GitHub Actions service. A checked-in constraint probe deliberately attempts an invalid insert and must observe a database constraint failure.

GitHub Actions use immutable commit references; the PostGIS image pins a major/minor tag. Image digest pinning can be added when publishing a deployment image. Database migrations and roundtrip checks are introduced with C04. Repository branch-protection settings are not changed automatically.
