# Audit resolution and implementation ownership

All 43 findings are addressed at the specification level in the [revised blueprint](technical-blueprint.md). **Implementation status for every row is planned, not completed.** A source-verified factual correction is not proof that a future parser, model, or deployment implements it correctly. C00 is the documentation revision; commit references below identify future verification work.

| Audit ID | Required design resolution | Blueprint section | Implementation commits |
| --- | --- | --- | --- |
| A1 | P/S classification requires venue, security type, and evidence | 7 | C07, C16 |
| A2 | Tri-state plan classification and historical applicability | 7 | C07, C16 |
| A3 | Product-specific distribution rights and dated budgets | 1, 4 | C03, C13, C31 |
| A4 | Use permissions checked before commercial ingestion/use | 1, 4 | C03, C13 |
| A5 | Per-endpoint latency/watermarks; no implied live flights | 4, 12 | C13, C30 |
| A6 | Observed ownership intervals; annual archives not daily truth | 3, 6 | C10, C11, C21 |
| A7 | Withheld/unresolved ownership is explicit missing coverage | 4, 6 | C10, C13, C30 |
| A8 | Fiscal, earnings, tenure, office, events, financial source contracts | 4, 5 | C03, C05, C09, C17 |
| B1 | Separate registrant/operator/controller; reviewed attribution | 6 | C10, C11 |
| B2 | Shared-intermediary exclusions and past-only home-base support | 6 | C11, C13 |
| B3 | Approval before activation plus stratified audit samples | 6 | C11 |
| B4 | Stable airport ID and typed aliases | 5, 6 | C12 |
| B5 | Explicit units, UTC times, observation types and conflicts | 5, 6 | C04, C12, C13 |
| B6 | Supported completed aircraft dwell; no passenger/meeting claims | 6, 10 | C14, C26 |
| B7 | Background attribution mass and company-level deduplication | 6 | C12, C14 |
| B8 | Ordinary-trip alternatives and one event discount | 6, 7 | C09, C14, C23 |
| C1 | Economic episodes, joint-owner/amendment handling, dated roles | 7 | C07, C16 |
| C2 | Exposure-based heuristic, independent episodes, assumption checks | 7, 9 | C16, C23 |
| C3 | Explicit observation cutoff and filing latency sensitivity | 4, 7 | C06, C16 |
| C4 | Expiry/completion/modification distinct from termination | 7 | C08, C16 |
| C5 | Quarter scope, source identity, explicit zero, age/staleness | 7 | C08, C16 |
| C6 | Signed statistical interaction, no guaranteed overlap bonus | 7, 9 | C18, C22 |
| C7 | Frozen feature formulas, bounds, counts, and unavailable rules | 6, 7 | C14–C18 |
| C8 | Semantic similarity only, fixed chunks/models and hindsight limits | 7, 10 | C17, C23, C26 |
| C9 | Public-company initial scope; dated financial/security data | 1, 7, 8 | C05, C17, C18 |
| D1 | Directed control-acquisition label, canonical deal/episode identity | 8 | C18, C19, C23, C25 |
| D2 | First public definitive announcement evidence, not form category | 3, 8, 9 | C06, C19 |
| D3 | Mature labels, purged temporal partitions, train-only transforms | 3, 9 | C04, C19–C23 |
| D4 | Frozen universe and common comparison cohort plus coverage | 8, 9 | C05, C18, C21, C23 |
| D5 | Matched budgets, sample requirements, uncertainty, inconclusive gate | 9 | C20, C23 |
| D6 | Episode evaluation and declared grouped/time bootstrap | 8, 9 | C20–C23 |
| D7 | Separate calibration, coverage, intervals and signed attribution | 9 | C22, C23, C29 |
| D8 | Executable next-open returns and explicit unavailable outcomes | 9 | C24 |
| D9 | Persist all review decisions; matched-volume evaluation | 10 | C23, C25, C26 |
| D10 | Mature then-known neighbors and versioned retrieval | 10 | C24 |
| E1 | Complete evidence/entity/research/product schema | 5 | C04–C05, C07–C14, C19–C28 |
| E2 | DB invariants, PostgreSQL from start, no SQLite equivalence claim | 2, 5 | C02, C04, C05, C10, C12 |
| E3 | Atomic snapshots, immutable manifests, idempotent/resumable runs | 2, 3, 12 | C04, C06, C18, C30 |
| E4 | Deterministic authority, schema-constrained LLMs, human approval | 10 | C26 |
| E5 | Append-only versions/checkpoints and realistic outbox guarantees | 10, 11 | C25, C28, C30 |
| E6 | Tenant isolation, scoped keys, bounded endpoints and safe destinations | 11 | C27, C28 |
| E7 | Completeness/readiness, exchange calendar, recovery drills | 4, 12 | C06, C13, C30 |
| E8 | Cost ceilings, complete watchlists, outcome states and retention | 1, 4, 10, 11 | C03, C24–C25, C27–C31 |

## Additional clarifications introduced during revision

- C14 scores only completed supported visits in v1, avoiding unsupported continuous-ground assumptions for ongoing arrivals.
- C15 requires 390 days of observation for a current 30-day intensity window plus twelve prior 30-day baseline windows.
- C19/C21 require full outcome follow-up for training positives as well as negatives, avoiding label-maturity selection bias.
- C22 distinguishes raw-logit explanations from a subsequently calibrated probability.
- C28 explicitly handles ambiguous provider acknowledgment; database deduplication alone cannot guarantee exactly-once external delivery.
- The blueprint replaces the original sixteen-agent requirement with deterministic workflow code and optional constrained helpers.
