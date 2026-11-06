# Tailwatch / Deal Signal

Research project for evidence-linked acquisition signals from corporate aircraft activity, insider disclosures, and company text.

**Status: company and SEC research pipeline implemented with synthetic validation.** Live sources are not enabled. No trained model, validated prediction accuracy, or customer alerts exist yet.

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
```

Dependency installation needs package-index access once; these commands then run without provider credentials. `fixture` reports synthetic mode and never invents a deal probability. The root is the application checkout; any local nested `tailwatch/` clone is ignored and left untouched.
