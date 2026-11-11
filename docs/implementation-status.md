# Implementation status

Updated November 10, 2026. The roadmap remains the full target; this file distinguishes shipped infrastructure from research gates.

| Step | Delivered | Remaining evidence/dependency |
| --- | --- | --- |
| C00 | Revised specification, roadmap, 43-finding audit map | Empirical claims remain unproven |
| C01 | Typed package, lockfile, offline CLI, unit-test commands | Candidate fitting and synthetic signals now implemented; real model pending |
| C02 | Python 3.11–3.14 quality jobs and isolated PostGIS integration CI | Branch protection not configured |
| C03 | Validated source manifest, permission checks, per-run request/cost guards, inventory command | Real-data inventory, sector selection, rights review, provider-specific budget configuration; G0 not established |
| C04 | Content-addressed raw objects, append-only PostgreSQL facts/revisions, atomic snapshot publication, distinct replay modes, Alembic migrations | Real source reconciliation remains pending; frozen run manifests are implemented |
| C05 | Typed dated company/security/office/role/relationship histories, evidence bindings, ticker lookup, frozen-universe export | Real reference data and coverage inventory pending |
| C06 | SEC HTTP adapter, rate limiting, hashed responses, durable index worklists and resumable capture | Bounded live command and three-company local metadata capture verified; scheduled ingestion still requires reviewed scope/coverage; single SEC worker only |
| C07 | Hardened Form 4 extraction, normalized versioned storage, reviewed transaction classification and amendment reconciliation | Three real Form 4 smoke samples parsed (seven rows); stratified comparison of 50 filings pending; unresolved amendments excluded |
| C08 | Item 408 passage candidates, evidence-bound reviewed quarterly records and termination count policy | Real disclosure validation and analyst review required |
| C09 | Typed event/calendar/coverage imports, dated corrections, explicit blackout exposure | Earnings/calendar/event vendors not selected or connected |
| C10 | FAA CSV archive parsing, immutable manifests, quarantines, dated alias observations and freshness-bounded lookup | Real archive dates/coverage validation, live acquisition, resolved airframe identities and privacy review pending |
| C11 | Dated candidate evidence, explicit role-specific review, privacy and direct-evidence checks, immutable decisions and operational resolution | Real stratified attribution evaluation, intermediary inventory, validated home-base observations and reviewer workflow remain pending |
| C12 | Typed airport histories/aliases, strict CSV normalization, WGS84 office distances, dated evidence links and company deduplication | Live source onboarding, archive reconciliation, coordinate/coverage validation and production spatial indexing pending |
| C13–C14 | Bounded flight/trace imports, raw retention, conservative ground-air-ground inference, coverage-aware visits and office attribution | ADSB.lol raw state adapter is a research candidate; real labeled-trace validation and coverage remain pending |
| C15–C18 | Deterministic travel, insider, silence, text/financial features; immutable input/run manifests | Real reference inputs, embeddings and exposure validation; synthetic CLI is not a predictive model |
| C19–C20 | Mature deal labels, frozen experiments, embargoes and matched baseline cohorts | Licensed labels, preregistered real sample sizes and untouched test data |
| C21–C23 | Logistic candidates, calibration, signed explanations, grouped refit uncertainty, matched-cohort ablations/coverage, sealed episode evaluation bound to exact model/features/labels, and retained-candidate scoring of frozen runs into review-only drafts | Real training/validation, empirical ablation and uncertainty reports; G3 not established |
| C24 | Executable-session returns and mature historical neighbors | Complete adjusted-price/calendar data and measured return results |
| C25–C26 | Timestamp/metadata-bound alert hashes, canonical episodes, review transitions, evidence-bound claims, analyst queue/history/outcome API, and bulk checkpoint manifests with verification commands | Staffed human review and independently administered checkpoint storage |
| C27–C28 | Tenant-scoped API including single-alert, pair, company and dated ticker-resolution views; key issue/revoke; watchlists; track record; recipient-only signed verification and unsubscribe links; audited outbox with idempotent provider reconciliation and operator resolution | Verified sender/domain, provider account and real notification configuration |
| C29 | Research-terminal dashboard: dependency-free evidence globe of displayable completed visits (with a list view), per-signal availability and signed contributions, gates, event tape, watchlists, evidence/history dialogs and connected API mode; validated dark palette, pausable motion and reduced-motion support | Real approved alerts and visit locations remain unavailable until release gates pass |
| C30 | Source-bound durable budgets with UTC rollover enforcement, checkpointed/resumable jobs, calendar deadlines, readiness checks, threshold-based `ops-status` monitoring, isolated CI restore drill and Docker packaging | Real provider stage wiring and scheduler deployment, monitoring destinations, independent backups and ten real shadow sessions |
| C31 | Immutable release manifests, exact model/app/config/schema checks, tenant limits, daily delivery quota, latest-session/source-freshness guards and kill switch | G0–G4 real evidence and limited rollout authorization/configuration; production remains disabled |

The software foundations across C00–C31 are implemented. This is not a claim that every empirical acceptance criterion or live integration is complete. Each remaining dependency above is part of the roadmap, not a successful result inferred from fixtures. The detailed commit plan remains the target contract.

`research-demo` computes deterministic synthetic features with no probability and an INCONCLUSIVE evaluation status. The browser dashboard starts without credentials. Real API output and external email require an active evidence-backed release; an environment flag or permissive sender callback cannot bypass it. Bounded local SEC metadata and three filing smoke captures have run. No scheduled live ingestion, production deployment, paid service, or external notification has been enabled.

The free-first research choices and their limitations are recorded in [free-first stack](runbooks/free-first-stack.md). See [operations and release](runbooks/operations-release.md) for startup, recovery and deployment gates.


## Completion reconciliation — November 8, 2026

The original C00–C31 sequence has implementation coverage, with follow-up PRs closing concrete gaps found during review. Research diagnostics, checkpointed pipeline execution, historical trace conversion, operator/recipient onboarding, alert integrity and exact sealed model/data provenance are now implemented and tested.

This is not an end-to-end live production release. Remaining work includes real-source acquisitions and domain-specific reconciliation, mapping approved providers into scheduled production stages, verified email dispatch configuration, monitoring/backup deployment and measured production performance. Historical trace parsing and generic pipeline recovery do not prove live source completeness. Optional production spatial indexing awaits actual scale measurements. These deployment/integration tasks must stay visible rather than being represented as completed by fixture tests.

G0–G5 remain unsatisfied: obtain reviewed rights and coverage, curate/validate real reference and label data, fit and evaluate on untouched real data, perform independent restore review, and accumulate ten real consecutive trading-session shadow runs. No test fixture, including explicitly fabricated release-gate integration rows, is evidence for those gates. There are no real model or launch claims in this repository.


## Workflow completion — November 10, 2026

Every step of the blueprint's execution chain now has an operator-runnable implementation, not only library functions: frozen features, retained candidate, scored review-only drafts, human approval, enqueue, verified delivery, reconciliation, outcomes, checkpoints and monitoring. Migration `0010` adds the verification outbox and the delivery audit trail. The [synthetic operator walkthrough](runbooks/synthetic-walkthrough.md) runs the full chain locally, and an integration test runs it in CI. Recipient secrets no longer pass through the operator, and uncertain sends are resolved either by a provider idempotent retry or by an audited operator decision.

This completes the software roadmap. It does not satisfy any gate. G0–G5 still require things software cannot supply: reviewed source rights and coverage, curated real reference and label data, a registered real experiment with a sealed-test PASS, provider and scheduler deployment with real source stage transformations, independent backups and checkpoint storage, verified email configuration, and ten consecutive real shadow sessions. Synthetic runs, fabricated integration rows and this walkthrough are not evidence for any gate.

## Live research feeds — November 11, 2026

The terminal can now stream real data in research mode: anonymous, privacy-filtered ADS-B positions around 14 hubs from ADSB.lol, and SEC EDGAR's latest Form 4 filings with as-filed transaction details. Both are authorized against a maintainer-reviewed manifest (`config/sources.live.json`), reserved in durable daily budgets, polled only while viewed, and backed off on provider errors. SEC responses are journaled; air-traffic responses are display-only. These feeds produce no signals, scores or alerts, and they do not establish coverage (G0) or any other gate. See [live research feeds](runbooks/live-research.md).
