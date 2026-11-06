# Tailwatch revised technical blueprint

Revision: November 6, 2026. Supersedes `original-blueprint.txt`. This document resolves specification defects; it does not assert that the fixes are implemented or that acquisition prediction works.

## 1. Scope and operating modes

Build a single-sector research pipeline first. Initial eligibility: US-listed operating companies with supported SEC filings, known security identities, and historically reconstructable company data. The sector is a versioned configuration decision made after a coverage inventory; no sector is assumed to have sufficient history. PE firms, private targets, foreign disclosure regimes, and consortium transactions are excluded from the initial model and counted as coverage exclusions.

The first product is an offline CLI that ingests fixtures, builds reproducible features, and exports a research report. Add customer API/dashboard/delivery only after validation. Modes:

| Mode | Inputs | Permitted output |
| --- | --- | --- |
| fixture | Synthetic or redistribution-permitted fixtures | Demonstration scores labeled synthetic; no predictive claims |
| research | Sources permitted for the declared use | Internal experiments; no customer delivery |
| shadow | Approved live sources and registered candidate model | Logged candidate alerts and human review; delivery disabled |
| production | Passed data, model, and operational gates | Approved alerts and subscribed deliveries |

Providers remain disabled until their capability/rights record is configured. No free-access assumption is inferred from lack of revenue. Record permissions for retention, backfill, derived output, external evidence display, and B2B redistribution. Provider pricing is a dated input to a budget, not a hardcoded entitlement. The audit contains source checks for SEC, FAA, OpenSky, ADS-B Exchange, and FlightAware; confirm applicable terms when onboarding.

## 2. Minimal architecture

Use Python 3.11+, a locked dependency set, typed domain records, SQLAlchemy/Alembic, PostgreSQL with PostGIS, and pytest. Add FastAPI for the later API phase. Pin compatible supported dependency versions when implementing; this document does not choose untested exact versions.

Use PostgreSQL in development and CI integration tests from the start. Pure computation tests do not require a database. Do not promise SQLite/PostGIS equivalence. Begin with one scheduled CLI worker, a database run ledger, and a transactional outbox. Redis and an elaborate agent hierarchy are unnecessary for the initial workload. Add a workflow platform only if measured scheduling/recovery requirements justify it.

Store permitted raw objects by content hash in a local object-store adapter for development and durable object storage in deployment. Raw fixtures and model artifacts must never be mistaken for live records. Keep credentials, licensed raw data, and large model files out of Git.

Execution: discover source work → capture raw objects → parse/stage → validate → publish a complete source snapshot → freeze run manifest → generate candidates/features → score → review → append approved alert → enqueue delivery. Each step is resumable and idempotent. A failed source snapshot never replaces the last complete snapshot.

## 3. Temporal and evidence contract

Every source-derived fact has a stable ID, source ID, raw-object reference, content hash, parser version, and evidence locator. Preserve UTC-aware event time, source-publication time, first-observed/ingested time, validity interval, and revision relationships. Unknown timestamps remain unknown rather than receiving invented midnight values.

Two distinct replay modes:

- **Operational replay:** only information actually available to the deployed system, including completed parsing and human link approvals, can be used. Derived availability is no earlier than all dependencies and their required processing/approval times.
- **Historical public-information simulation:** use evidenced source-publication times and a declared latency policy. An ownership link reconstructed today is not treated as historically approved. Tag reconstructed facts and report a sensitivity analysis or exclude them where contemporaneous support is insufficient. Do not present this as an exact replay of a system that did not exist.

An as-of query selects the version known by the cutoff and effective for the relevant event. Append corrections; never overwrite the historical view. A later departure, amended filing, office move, or revised financial value cannot alter an earlier operational replay. Annual FAA snapshots establish observations, not daily ownership history.

Each run freezes cutoff, replay mode, source snapshot IDs/watermarks, eligible-universe version, code/configuration versions, feature definitions, and model ID. Evidence links are structured references to exact rows/passages plus accessible source URLs where permitted. Report text must distinguish observation from inference.

## 4. Source contracts and ingestion

| Source family | Required contract |
| --- | --- |
| SEC filings | Declared contact User-Agent, shared rate limiter, retry/backoff, accession cursor/index reconciliation, acceptance/publication/first-seen timestamps, amendments and parse quarantine |
| FAA registry | Snapshot date and hash, registrant types, fractional flags, stable airframes/identifier history, unresolved or withheld ownership, archive inventory |
| Flight provider | Permitted use, geographic/historical coverage, endpoint latency, source IDs, inferred versus actual events, pagination/cost limits, missing-leg and outage status |
| Company reference data | Stable company/security IDs, ticker history, fiscal calendar, public/private status, subsidiaries, office histories, insider role/tenure |
| Earnings and events | Publication history, coverage interval, timezone, event type, confidence; absence of an event is not proof of no event |
| Strategy text | Document body, exact version, publication time, kind, source rights, chunking and embedding model versions |
| Financials and prices | Fiscal period, original publication/vintage, currency, security class, adjustment policy, trading calendar, delisting/halts |
| Deal labels | Transaction taxonomy, involved companies, earliest evidenced public definitive announcement, source completeness and revision history |

Start with fixture adapters and only add real-source adapters after their prerequisites exist. OpenSky completed-flight latency differs from live state-vector latency; intraday filing rescoring reuses explicitly dated flight snapshots. Live landing detection is deferred.

For every source measure completeness against expected indexes/cursors and declared scope, not merely the newest row. A failed parse, withheld owner, unobserved aircraft, and confirmed zero activity are distinct states. Enforce budgets before requests, including pagination and retries. Keep immutable request/response metadata without secrets.

## 5. Domain model and database invariants

| Group | Entities and essential additions |
| --- | --- |
| Provenance | sources, source_permissions, raw_objects, evidence, source_snapshots, ingestion_runs, parse_failures, watermarks |
| Companies | companies, company_versions, securities, ticker_aliases, financial_observations, offices and office_versions, corporate_relationships |
| People/disclosures | insiders, insider_roles, filings, filing_versions, transaction_rows, economic_transactions, reporting_owners, plan_events, earnings_events, blackout_intervals |
| Aviation | aircraft, aircraft_identifiers, registry_observations, control_links, link_reviews, airports, airport_aliases, office_airport_links, flight_legs, visits, visit_host_candidates |
| Context | known_events, strategy_documents, document_chunks, embedding_versions, embeddings |
| Research | universe_snapshots, directed_pairs, deals, deal_events, labels, feature_definitions, feature_values, score_runs, models, experiment_manifests, evaluation_results |
| Product | alert_versions, review_decisions, audit_events, outcome_events, tenants, api_keys, watchlists, subscriptions, notification_outbox, delivery_attempts |

Use foreign keys, unique source identities, explicit revision links, and non-overlapping authoritative validity intervals. Identifier ambiguity is represented as competing candidates, not silently resolved. Keep CIKs and ticker symbols as strings. Monetary values use decimals and currency; probabilities are finite and bounded; interval endpoints are ordered; flight times and dwell cannot be negative. Use stable airport/aircraft IDs independently of changing codes.

Feature identity: `(directed_pair_id, as_of, feature_definition_version, score_run_id)`. Store value or null, availability status, coverage, evidence IDs, and computation metadata. Never encode unavailable as zero. Statuses include `observed`, `insufficient_history`, `stale`, `missing_source`, `ambiguous`, and `not_applicable`.

## 6. Ownership and visit inference

Separate registrant, operator, controller, and attributable company. Normalize names only to propose candidates. Shared trustee/registered-agent addresses do not establish company control. Home-base inference uses past observations and an explicit minimum coverage rule. Only human-approved confirmed/probable control links, with supporting evidence and valid periods, contribute to alerts. Possible links remain research candidates; the model cannot override that gate.

Exclude individuals, fractional operation, unresolved beneficial control, and unsupported charter attribution from alert inputs. Corporate legal form alone is insufficient. Record exclusions and unknown ownership; do not attempt to undo privacy measures. Review before activation, plus stratified samples of accepted/rejected/low-volume links. Prioritization by alert exposure is supplemental, not the validation sample.

Normalize flight feeds, deduplicate by source identity and carefully matched legs, and quarantine disagreements. Link arrival to a supported next departure at the same airport; missing coverage does not establish continuous parking. Only completed, sufficiently observed visits count in v1. Ongoing arrivals are displayed separately without a full dwell score. Retain observed aircraft dwell language, never claimed executive meeting duration.

Compute geodesic distance with explicit units; 50 km is a configurable starting radius. Normalize offices to one candidate per company. Exclude self/subsidiary sites and label home-base, maintenance, recurrent relationships, and known-event alternatives. Event overlap discounts once, not once per matched event.

For candidate host H, define `a_H = exp(-distance_km / rho)` and `w_H = a_H / (b + sum(a_j))`, with `rho > 0` and `b > 0` frozen in feature configuration. Background weight is `b / (b + sum(a_j))`. These are heuristic attribution weights, not probabilities of a meeting. Do not multiply semantic fit into these weights in v1. Report candidate-set coverage and sensitivity to radius/rho/background changes. Default research values: rho=25 km and b=1; tune only on development data.

## 7. Versioned signal definitions

Window bounds are UTC half-open intervals `[start, end)` unless specified. Event decay uses actual elapsed days; trading-day exposure uses an exchange calendar. Cutoffs and unavailable cases are persisted.

**Flights:** sum qualifying completed-visit weights times decay. `w_dwell(d) = max(0, min(1, (d_hours - 1)/3))`; event multiplier starts at 0.2 for an evidenced overlap and 1 otherwise, with event-feed coverage reported separately. Decay uses arrival time and tau=14 days. Missing event coverage cannot be described as “no conference.” Thresholds are research defaults.

**Novelty:** at each qualifying visit's arrival, evaluate whether there were earlier qualifying visits to that host in the preceding 365 days, excluding the present episode. Insufficient historical observation yields unavailable. The pair feature uses the most recent qualifying visit in the current 30-day window; no current visit with complete coverage yields zero.

**Intensity:** visitor's count of qualifying travel episodes across hosts in the latest 30 days versus twelve preceding, non-overlapping 30-day windows. A multi-host candidate visit counts once. This requires up to 390 days of supported history; fewer than twelve complete baseline windows or zero baseline variance yields unavailable. Keep pair visit score distinct from visitor-wide intensity.

**Buying:** only evidenced open-market, non-derivative common-equity purchases classified as non-plan contribute. P alone and an unchecked/absent plan field are insufficient. Preserve true/false/unknown plan status and venue classification with evidence. Canonical economic transactions eliminate original/amended and joint-owner duplicate activity; ambiguous amendments are quarantined, not heuristically discarded. Group eligible purchases into person/issuer/trading-day episodes. Use count weighting in v1, not dollar magnitude. CEO/CFO role=1, other director=0.7, other officer=0.5, highest applicable role wins as of transaction time. Decay tau=30 days. Three distinct eligible natural-person buyers in the latest 14 event-days multiply by 1.5. Late filings enter only when available and retain event-date decay. Exclude entity-only reporting owners unless resolved to supported distinct people.

**Silence:** evaluate through an explicitly recorded observation watermark, initially two completed business days before the run, additionally constrained by ingestion completeness. This buffer is a latency heuristic, not a guarantee that all trades have been filed. Evaluate a trailing 45-calendar-day window and a preceding 365-day baseline. Count independent eligible open-market sale days for active insiders, with at least four baseline episodes and sufficient covered eligible exposure. Exclude known blackout days from both exposure periods; unknown company policy must be labeled, and guessed blackout schedules are research sensitivity cases only. For insider i, `lambda_i = baseline_sales_i / baseline_eligible_days_i * recent_eligible_days_i`. Average `lambda_i * I(recent_sales_i == 0)` over all eligible insiders. Zero eligible insiders/exposure yields unavailable. This is a heuristic count-model score, not a probability of misconduct or deal knowledge. Plan sales, changes in tenure, and exhausted holdings need separate controls and sensitivity analysis.

**Plan events:** parse adopted, actively terminated, modified/replaced, expired, completed, and unknown separately, with actual event date, insider ID, arrangement type, fiscal quarter, accession, passage, and revision linkage. Count unique active terminations of 10b5-1 arrangements from the latest available relevant quarter; exclude replacements and expiry/completion in v1. Explicit no-events is zero; absent/unparsed disclosure is unavailable. Include disclosure age; older than 180 days is stale by research default.

**Text:** name the feature semantic similarity. Compare recent published strategy chunks from four fiscal quarters with the host's latest available business-description chunks. Aggregate document vectors by a fixed equal-document mean, then normalize; transform cosine using `(cosine + 1)/2`. Zero vectors, model-version mismatch, absent text, and expired document coverage are unavailable. Preserve chunks and model revision. No deal announcements or future texts enter an earlier score. Do not equate similarity with strategic suitability.

**Size/role:** point-in-time market-cap ratio and cash/host-market-cap ratio use consistent currencies and security aggregation; nonpositive/missing denominator yields unavailable. Keep ratio features separate from model outcome; visitor is not assumed to be acquirer. Role output remains an explicitly uncertain estimate.

**Interaction:** multiply flight and silence features only when both are observed; name it `flight_silence_interaction`, not overlap bonus. Allow either coefficient sign. Explicit temporal-coincidence features require a later registered experiment.

## 8. Prediction target and candidate universe

Directed example `(V,H,t)` predicts an evidenced public definitive announcement of V acquiring control of H in `(t, t+180 days]`. Travel direction is evidence, not proof of buyer direction. Generate both directed hypotheses for each eligible company pair, with separate directional features; exclude self and already-announced pairs. Minority stakes, asset deals, rumors, mergers of equals, and consortiums are separate/excluded categories in v1.

Freeze the eligible universe as of each date, including subsequently delisted companies. Do not require a flight to enter the universe. Track feature coverage and scoreability separately. Define the initial common complete-feature subset for combined versus single-signal comparison; additionally report each method's deployable coverage and end-to-end recall over the full eligible universe. Sparse flight candidates may optimize computation only if omitted pairs receive the documented zero/unavailable states and remain in evaluation denominators.

Deduplicate public pair episodes across orientations using stable sorted company IDs. One definitive deal can count as at most one recall success. Pair alerts use a 180-day episode cooldown; updates retain the original episode ID and timestamp. The first delivered direction is frozen for directed success evaluation; an opposite-direction later prediction cannot retroactively turn an incorrect direction into a success. Report undirected success separately.

## 9. Training, replay, and acceptance

Start with regularized logistic regression and deterministic non-ML baselines. Unavailable required features mean unscorable for the initial complete-feature model; never silently impute zero. Any later missingness-aware model is a separately versioned experiment with train-only preprocessing.

Training samples use full 180-day follow-up under adequate deal-label coverage, including positives, to avoid differential maturity sampling. Pending or incomplete follow-up is excluded. Labels preserve first-public-announcement evidence, discovery time, and revisions. Use earliest available definitive press releases/filings, not filing category alone.

Split chronologically: training → calibration development → threshold development → sealed test. Purge overlapping label windows and never allow labels unavailable at a fit cutoff. Walk-forward fits obey the same rules. Fit scalers, feature selection, embedding-derived learned transformations, and hyperparameters only in their allowed training/development partitions. Quarterly retraining is an attempted job, not automatic promotion.

Compare combined, flight-only, insider-only, and seeded random rankings on the same eligibility and matched episode alert budgets. Record negative sampling and prevalence; calibration uses representative deployment data. Include PR curves, Brier/log loss, calibration bins with counts, precision/recall, lead time, independent deals, coverage, and uncertainty. Cluster/block bootstrap by appropriate pair/deal and temporal units; predeclare resampling method and handling of one-class or failed fits. Start with 200 successful fits and assess stability of interval endpoints; insufficient samples produce unavailable intervals.

Probability intervals describe model variability, not an individual outcome guarantee. Contributions are signed log-odds: `c_k=beta_k*(x_k-reference_k)` on the fitted feature scale, with reference logit retained so contributions reconstruct the raw logit. If calibrated, display calibrated probability separately; contributions explain the underlying logistic model. Absolute shares may be secondary with a zero-sum policy; they are not probability percentages or causal effects.

Before opening test outcomes, freeze experiment parameters: sector/universe, historical interval, minimum independent deals/episodes, alert budget, target precision, minimum useful lift, uncertainty method, and interval-width limits. Values must be justified from the development-data inventory and intended use; unset values make the evaluation gate incomplete. PASS requires the declared precision lower bound and combined-model improvement lower bound to exceed their registered targets with sufficient independent samples. FAIL or INCONCLUSIVE prevents production-model promotion. Do not repeatedly inspect the same test period while tuning; changed hypotheses require new untouched evaluation or a clearly labeled exploratory result.

Measure optional host returns from the next tradable regular-session open strictly after publication using total-return-adjusted prices and a preregistered market benchmark. Define 5/30 trading-session endpoints, holidays, halts, and delisting treatment. Missing executable prices yield unavailable, not a fabricated return. These returns do not establish portfolio alpha; portfolio claims need separate cost/exposure rules.

## 10. Review, reports, and audit history

The workflow is deterministic code. Optional LLM helpers extract passages, propose ownership links, and draft skeptical notes; they cannot compute authoritative numbers, approve their own links, change probabilities, or send notifications. Treat all retrieved content as untrusted. Constrain outputs to schemas and provided evidence; reject unsupported claims. Record model/prompt versions, cost, and immutable outputs. Historical LLM judgments have possible pretrained future knowledge and are excluded from core predictive evaluation by default.

Maintain pre-review scores and all veto outcomes. Human analyst approves evidence/link changes and final alerts during initial production; later automation is a separate policy change. No reviewer or timeout defaults to approval. Compare the skeptic against no-skeptic outputs at matched volume; track lost true positives as well as removed false positives.

Alert states: draft → in_review → approved or vetoed. Correction creates a new version and supersedes the old version; it requires approval again. Scoring, review, and delivery are distinct. Store numeric score status, calibrated probability if supported, model interval, ownership/data coverage, signed contributions, claim/evidence references, reviewer decision, and version identifiers.

Append alert, review, and outcome events to an ordered hash chain with independently retained immutable checkpoints. A hash in a mutable row is not tamper prevention. Never revise the original public prediction silently. Outcome events distinguish pending, announced, horizon-elapsed-negative, withdrawn-after-announcement, and incomplete-follow-up. Withdrawal does not erase success for an announcement target. Returns have independent availability states.

Lookalikes are optional, post-validation: only mature prior cases with then-known features/outcomes, versioned retrieval, unsuccessful as well as successful neighbors, and no duplicate/current transaction.

## 11. Product API and delivery

All external surfaces read approved immutable alert versions. Scope and hash API keys, support expiry/revocation, enforce tenant authorization, bound query costs, paginate with stable cursors, and redact secrets from logs.

| Endpoint | Contract |
| --- | --- |
| GET /v1/alerts and /v1/alerts/{id} | Approved current versions, cursor pagination, evidence rights filters, coverage and model versions |
| GET /v1/pairs/{visitor}/{host} | Directional history, availability states, cutoff/model metadata; resolve dated ticker aliases |
| GET /v1/companies/{ticker}/signals | Company signals plus pair-specific signals explicitly identified by counterpart |
| POST/GET/PATCH/DELETE /v1/watchlists[/{id}] | Tenant-owned tickers/sectors, validated identities, idempotent creation |
| GET /v1/track-record | Original approved predictions plus append-only outcomes/corrections and pending denominators |

Add internal review endpoints separately with analyst authorization. Customer APIs never reveal draft/vetoed research candidates accidentally. Start notifications with one provider/channel; add Slack/push separately. Require verified destinations and active subscriptions, support unsubscribe, and constrain webhook endpoints against internal-address/redirect abuse.

Use a transactional outbox with unique `(alert_version, subscription, channel)` keys. Recheck approval/supersession/subscription state immediately before sending. Delivery is at-least-once unless the provider supports an idempotency key: an ambiguous timeout can otherwise cause an external duplicate. Persist provider IDs, ambiguous states, reconciliation, and bounded retries; do not promise exactly-once email. Corrections are explicit new notifications.

## 12. Operations and gates

Use the US trading calendar and `America/New_York` timezone; initial target completion is 08:30 local time on trading days. Retain UTC internally. Source-specific latency allowances determine readiness, not a universal newest-row check. Intraday jobs freeze new manifests and cannot overlap approvals inconsistently.

Monitor source completeness/freshness, parse quarantine, identity coverage, feature missingness, distributions, score/alert volume, costs, queue age, and review backlog. Missing mandatory inputs or failed completeness checks suppress external alerts. Preserve safe partial research output with explicit status. Test retry storms, interrupted runs, provider timeouts, outbox reconciliation, credential rotation, and backup restoration.

Gates: G0 data/rights feasibility → G1 reproducible source and entity foundation → G2 deterministic feature correctness → G3 sealed evaluation PASS → G4 shadow and operational readiness → G5 limited production rollout. Model-dependent product activation cannot bypass G3; UI work with clearly synthetic fixtures may continue if useful. Ten consecutive successful trading-day shadow runs are a minimum operational check, not evidence of predictive accuracy.

See the implementation plan for commit ownership of every requirement and the audit map for coverage of all 43 findings.
