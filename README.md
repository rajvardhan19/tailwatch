# Tailwatch / Deal Signal

Research project for evidence-linked acquisition signals from corporate aircraft activity, insider disclosures, and company text.

**Status: research pipeline, guarded API, dashboard and release controls implemented with synthetic validation.** Real-data evaluation and production gates remain pending. No validated prediction accuracy or live customer alerts are claimed.

- [Revised technical blueprint](docs/technical-blueprint.md): implementation contract; supersedes the original design.
- [Commit-by-commit implementation plan](docs/implementation-plan.md): ordered changes, dependencies, acceptance checks, and release gates.
- [Audit resolution map](docs/audit-resolution.md): all 43 findings mapped to design decisions and implementation commits.
- [Implementation status](docs/implementation-status.md): delivered components and outstanding gates.
- [Development runbook](docs/runbooks/development.md): database setup, migrations, source policy, and replay contracts.
- [Company and SEC runbook](docs/runbooks/company-and-sec.md): dated identities, resumable filing capture, Form 4 parsing, plan review, and event context.
- [Aircraft registry runbook](docs/runbooks/aircraft-registry.md): archived observations, quarantines, historical alias lookup and candidate exclusions.
- [Aircraft control runbook](docs/runbooks/aircraft-control.md): candidate evidence, dated role-specific decisions, rejection and replay.
- [Airport proximity runbook](docs/runbooks/airport-proximity.md): typed codes, dated coordinates, WGS84 distances and company deduplication.
- [Detailed audit](docs/deep-design-audit.md): findings, primary sources, and regression scenarios.
- [Original supplied blueprint](docs/original-blueprint.txt): preserved reference, not the current implementation contract.

Start with a reproducible offline research pipeline. Real providers, predictive claims, and customer delivery are enabled only after their respective data, evaluation, and operational gates pass.

## Development

Install Python 3.11–3.14 and uv, then run:

```sh
uv sync --locked
uv run --locked tailwatch --help
uv run --locked tailwatch fixture
uv run --locked tailwatch inventory config/sources.fixture.json
make check
make test
uv run --locked tailwatch research-demo
uv run --locked uvicorn tailwatch.product.server:app --host 127.0.0.1 --port 8765
```

Dependency installation needs package-index access once; these commands then run without provider credentials. `fixture` reports synthetic mode and never invents a deal probability. The root is the application checkout; any local nested `tailwatch/` clone is ignored and left untouched.

Open http://127.0.0.1:8765 for the synthetic dashboard. See the [operations and release runbook](docs/runbooks/operations-release.md) for database/API setup and the [free-first stack](docs/runbooks/free-first-stack.md) for provider decisions. The default preview needs no credentials and sends no notifications.

Additional operator commands: `tailwatch run-fixture-job --help` runs checkpointed synthetic stages; `tailwatch provision-workspace --help` creates a tenant and unverified destination in a configured local database. Both require migrations through `0008`. They do not activate production.

Run the bundled synthetic development report without credentials:

```sh
uv run --locked tailwatch research-report config/research.synthetic.json --objects .data/reports
```

The command retains its input, grouped uncertainty and ablation artifacts and prints their content-addressed report hash. It cannot promote a model. `uv run --locked tailwatch release-status` inspects release eligibility once a database is configured.
