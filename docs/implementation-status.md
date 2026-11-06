# Implementation status

Updated November 6, 2026. The roadmap remains the full target; this file distinguishes shipped infrastructure from research gates.

| Step | Delivered | Remaining evidence/dependency |
| --- | --- | --- |
| C00 | Revised specification, roadmap, 43-finding audit map | Empirical claims remain unproven |
| C01 | Typed package, lockfile, offline CLI, unit-test commands | No signal/model yet |
| C02 | Python 3.11–3.14 quality jobs and isolated PostGIS integration CI | Branch protection not configured |
| C03 | Validated source manifest, permission checks, per-run request/cost guards, inventory command | Real-data inventory, sector selection, rights review, durable paid-provider budget ledger; G0 not established |
| C04 | Content-addressed raw objects, append-only PostgreSQL facts/revisions, atomic snapshot publication, distinct replay modes, Alembic migrations | Domain-specific source reconciliation and run manifests are later steps |
| C05–C31 | Planned | Not implemented |

No live adapter, notification sender, model, or customer API is enabled. `fixture` reports mode status; it is not an end-to-end scoring demonstration. Provider coverage and predictive success are not inferred from synthetic tests.

Next implementation group: C05–C09, beginning with dated company/security/office identities, then SEC ingestion/parsing. Live ingestion remains subject to source permissions and G0; offline fixtures can proceed independently.
