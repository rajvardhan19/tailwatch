# Implementation status

Updated November 6, 2026. The roadmap remains the full target; this file distinguishes shipped infrastructure from research gates.

| Step | Delivered | Remaining evidence/dependency |
| --- | --- | --- |
| C00 | Revised specification, roadmap, 43-finding audit map | Empirical claims remain unproven |
| C01 | Typed package, lockfile, offline CLI, unit-test commands | No signal/model yet |
| C02 | Python 3.11–3.14 quality jobs and isolated PostGIS integration CI | Branch protection not configured |
| C03 | Validated source manifest, permission checks, per-run request/cost guards, inventory command | Real-data inventory, sector selection, rights review, durable paid-provider budget ledger; G0 not established |
| C04 | Content-addressed raw objects, append-only PostgreSQL facts/revisions, atomic snapshot publication, distinct replay modes, Alembic migrations | Domain-specific source reconciliation and run manifests are later steps |
| C05 | Typed dated company/security/office/role/relationship histories, evidence bindings, ticker lookup, frozen-universe export | Real reference data and coverage inventory pending |
| C06 | SEC HTTP adapter, rate limiting, hashed responses, durable index worklists and resumable capture | Not enabled by default; explicit CIK scope/contact/permissions required; single SEC worker only |
| C07 | Hardened Form 4 extraction, normalized versioned storage, reviewed transaction classification and amendment reconciliation | Stratified comparison of 50 real filings pending; unresolved amendments excluded |
| C08 | Item 408 passage candidates, evidence-bound reviewed quarterly records and termination count policy | Real disclosure validation and analyst review required |
| C09 | Typed event/calendar/coverage imports, dated corrections, explicit blackout exposure | Earnings/calendar/event vendors not selected or connected |
| C10 | FAA CSV archive parsing, immutable manifests, quarantines, dated alias observations and freshness-bounded lookup | Real archive dates/coverage validation, live acquisition, resolved airframe identities and privacy review pending |
| C11 | Dated candidate evidence, explicit role-specific review, privacy and direct-evidence checks, immutable decisions and operational resolution | Real stratified attribution evaluation, intermediary inventory, validated home-base observations and reviewer workflow remain pending |
| C12 | Typed airport histories/aliases, strict CSV normalization, WGS84 office distances, dated evidence links and company deduplication | Live source onboarding, archive reconciliation, coordinate/coverage validation and production spatial indexing pending |
| C13–C31 | Planned | Not implemented |

No live source, notification sender, model, or customer API is enabled. The SEC adapter is available for explicit configuration; development/CI use synthetic transports. `fixture` reports mode status; it is not an end-to-end scoring demonstration. Provider coverage and predictive success are not inferred from synthetic tests.

Next implementation group: C13–C14, covering flight imports and visits. Live ingestion remains subject to source permissions and G0; offline fixtures can proceed independently.
