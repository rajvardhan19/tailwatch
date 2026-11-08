# Implementation status

Updated November 8, 2026. The roadmap remains the full target; this file distinguishes shipped infrastructure from research gates.

| Step | Delivered | Remaining evidence/dependency |
| --- | --- | --- |
| C00 | Revised specification, roadmap, 43-finding audit map | Empirical claims remain unproven |
| C01 | Typed package, lockfile, offline CLI, unit-test commands | Candidate fitting and synthetic signals now implemented; real model pending |
| C02 | Python 3.11–3.14 quality jobs and isolated PostGIS integration CI | Branch protection not configured |
| C03 | Validated source manifest, permission checks, per-run request/cost guards, inventory command | Real-data inventory, sector selection, rights review, provider-specific budget configuration; G0 not established |
| C04 | Content-addressed raw objects, append-only PostgreSQL facts/revisions, atomic snapshot publication, distinct replay modes, Alembic migrations | Real source reconciliation remains pending; frozen run manifests are implemented |
| C05 | Typed dated company/security/office/role/relationship histories, evidence bindings, ticker lookup, frozen-universe export | Real reference data and coverage inventory pending |
| C06 | SEC HTTP adapter, rate limiting, hashed responses, durable index worklists and resumable capture | Not enabled by default; explicit CIK scope/contact/permissions required; single SEC worker only |
| C07 | Hardened Form 4 extraction, normalized versioned storage, reviewed transaction classification and amendment reconciliation | Stratified comparison of 50 real filings pending; unresolved amendments excluded |
| C08 | Item 408 passage candidates, evidence-bound reviewed quarterly records and termination count policy | Real disclosure validation and analyst review required |
| C09 | Typed event/calendar/coverage imports, dated corrections, explicit blackout exposure | Earnings/calendar/event vendors not selected or connected |
| C10 | FAA CSV archive parsing, immutable manifests, quarantines, dated alias observations and freshness-bounded lookup | Real archive dates/coverage validation, live acquisition, resolved airframe identities and privacy review pending |
| C11 | Dated candidate evidence, explicit role-specific review, privacy and direct-evidence checks, immutable decisions and operational resolution | Real stratified attribution evaluation, intermediary inventory, validated home-base observations and reviewer workflow remain pending |
| C12 | Typed airport histories/aliases, strict CSV normalization, WGS84 office distances, dated evidence links and company deduplication | Live source onboarding, archive reconciliation, coordinate/coverage validation and production spatial indexing pending |
| C13–C14 | Bounded flight imports, raw retention, quarantine, coverage-aware visits and probabilistic office attribution | ADSB.lol raw state adapter is a research candidate; validated historical trace-to-leg conversion and real coverage remain pending |
| C15–C18 | Deterministic travel, insider, silence, text/financial features; immutable input/run manifests | Real reference inputs, embeddings and exposure validation; synthetic CLI is not a predictive model |
| C19–C20 | Mature deal labels, frozen experiments, embargoes and matched baseline cohorts | Licensed labels, preregistered real sample sizes and untouched test data |
| C21–C23 | Logistic candidates, calibration, signed explanations, sealed test access and conservative episode evaluation | Real training/validation, full empirical ablations and grouped model uncertainty; G3 not established |
| C24 | Executable-session returns and mature historical neighbors | Complete adjusted-price/calendar data and measured return results |
| C25–C26 | Immutable alert events, canonical episodes, review transitions, evidence-bound claims and checkpoints | Human review operations and independently controlled checkpoint storage |
| C27–C28 | Tenant-scoped API, keys, watchlists, public history/track record, verified subscriptions and durable outbox | Operator onboarding, verified sender/domain, provider reconciliation and real notification configuration |
| C29 | Responsive dashboard, synthetic demo, evidence/history dialogs, watchlists and connected API mode | Real approved alerts remain unavailable until release gates pass |
| C30 | Durable budgets/jobs, calendar deadlines, readiness checks, isolated CI restore drill and Docker packaging | Live scheduler orchestration, production monitoring destinations, independent backups and ten real shadow sessions |
| C31 | Immutable release manifests, exact model/app/config/schema checks, tenant limits, daily delivery quota, latest-session/source-freshness guards and kill switch | G0–G4 real evidence and limited rollout authorization/configuration; production remains disabled |

The software foundations across C00–C31 are implemented. This is not a claim that every empirical acceptance criterion or live integration is complete. Each remaining dependency above is part of the roadmap, not a successful result inferred from fixtures. The detailed commit plan remains the target contract.

`research-demo` computes deterministic synthetic features with no probability and an INCONCLUSIVE evaluation status. The browser dashboard starts without credentials. Real API output and external email require an active evidence-backed release; an environment flag or permissive sender callback cannot bypass it. No live ingestion, production deployment, paid service, or external notification has been enabled.

The free-first research choices and their limitations are recorded in [free-first stack](runbooks/free-first-stack.md). See [operations and release](runbooks/operations-release.md) for startup, recovery and deployment gates.
