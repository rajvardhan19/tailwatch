# Tailwatch commit-by-commit implementation plan

Target: https://github.com/rajvardhan19/tailwatch

Repository inspected November 6, 2026: `main` at `f20c1c9846573727d0c24ebc2b8bbfa4a08d9cfb`, containing only `.gitattributes`. This plan starts from that state. All application paths below are proposed, not existing functionality.

## How to use this plan

The documentation revision is C00. C01–C31 are future implementation commits, not work claimed complete. Follow numeric order; explicit dependencies describe the necessary prerequisites if work is later split across branches. Keep each commit runnable, with migrations and behavioral tests for its change. Split an entry if it becomes too large to review, retaining its acceptance checks and audit references. Do not manufacture empty commits just to match the numbering.

Use short feature branches and reviewed pull requests. Suggested PR grouping: C00; C01–C04; C05–C09; C10–C14; C15–C18; C19–C24; C25–C26; C27–C29; C30–C31. Grouping is flexible; gates are not. Keep the final evaluation artifact sealed until the experiment is registered. Never include credentials, unrestricted licensed data, customer destinations, or generated bulk artifacts in commits.

Default implementation decisions are in the [revised blueprint](technical-blueprint.md). All audit IDs below refer to [the audit](deep-design-audit.md). “Acceptance” means evidence required when that commit is implemented; it is not a completed test result.

## Proposed layout

```text
src/tailwatch/
  domain/          # typed identities, times, availability, evidence
  db/              # PostgreSQL repositories and migrations
  sources/         # provider adapters, raw objects, cursors
  entities/        # company/aircraft identity and review
  visits/          # normalized legs and candidate visits
  features/        # versioned deterministic computations
  research/        # labels, replay, training, evaluation
  workflow/        # run manifests, review, audit, outbox
  api/             # authenticated product and analyst endpoints
  cli.py
web/               # later dashboard over the same API
tests/{unit,integration,fixtures}/
config/            # example configuration, no secrets
docs/{decisions,runbooks,experiments}/
.github/workflows/
```

## Foundation: C00–C05

### C00 — `docs: revise technical blueprint and register implementation roadmap`

- Deliver README, corrected specification, preserved original, both audits, and audit-to-commit map. These files are the current change.
- Acceptance: all 43 findings have a corresponding design resolution and future implementation commit; links resolve locally; no runnable-product claims.
- Dependencies: none. This commit does not count as resolving an implementation issue.

### C01 — `chore: scaffold typed Python package and offline CLI`

- Add `pyproject.toml`, locked dependencies, `src/tailwatch`, test layout, development commands, `.gitignore`, and `.env.example`. Provide `tailwatch --help` and a fixture-only mode.
- Acceptance: clean install and CLI smoke check; no network requests or secrets required. Select and document compatible dependency versions at implementation time.
- Dependencies: C00. Do not add a license asserting rights to data you do not own; choose a repository code license separately if desired.

### C02 — `ci: run lint typing unit and PostgreSQL integration checks`

- Add GitHub Actions, dependency caching keyed by lockfile, PostgreSQL/PostGIS service, migration checks, and artifact limits. Use least-privilege workflow permissions and reproducible action references.
- Acceptance: CI passes on a clean runner; an intentionally invalid migration/contract fixture fails the relevant check. Do not call paid providers from CI.
- Dependencies: C01. Establish required status checks through repo settings when permissions allow.

### C03 — `feat: register source capabilities permissions and budgets`

- Add mode configuration, provider capability/rights manifest, history inventory command, cost ceilings, and a data-feasibility report. Document sector candidates using available coverage; freeze the initial sector/universe selection in configuration after inventory.
- Acceptance: undeclared use/retention/distribution or exhausted budget disables the relevant adapter; fixture mode stays usable. Record sample historical overlap and gaps instead of invented coverage.
- Dependencies: C01–C02. **G0:** historical feasibility and permitted use established before live bulk/backfill work. If unavailable, proceed with fixtures and mark real-data validation blocked.

### C04 — `feat: persist immutable evidence and temporal source snapshots`

- Add Alembic foundation, raw-object store interface, provenance records, revision chains, UTC types, source snapshots, availability states, and operational/public-information replay semantics.
- Acceptance: later revisions cannot change an earlier operational query; identical raw ingestion is idempotent; incomplete snapshots cannot become current; hashes verify stored fixture bytes.
- Dependencies: C03. This is the mandatory foundation for every downstream source.

### C05 — `feat: version companies securities offices and fiscal identities`

- Add stable company/security/insider IDs, dated ticker aliases, office and relationship history, fiscal calendars, and eligible-universe snapshots including delistings. Provide fixture/CSV import with provenance.
- Acceptance: ticker reuse, company renaming, office moves, missing currency, and post-cutoff updates resolve correctly; foreign keys and interval checks reject invalid records.
- Dependencies: C04.

## Source pipelines: C06–C14

### C06 — `feat: ingest SEC filings with resumable cursors and reconciliation`

- Add SEC adapter, shared request throttling, declared contact identity, backoff, raw capture, accession/index reconciliation, parser quarantine, and source publication/acceptance/first-seen times.
- Acceptance: offline HTTP fixtures exercise pagination, throttling, partial downloads, duplicates, late filings, and resume after interruption; completeness does not pass from one fresh row alone.
- Dependencies: C03–C05. Network smoke checks are bounded and separate from CI.

### C07 — `feat: normalize Form 4 transactions plans and amendments`

- Parse tables, security classes, transaction directions/codes, footnotes, reporting owners, role history, and tri-state plan/venue classifications. Add versioned economic-transaction grouping and amendment reconciliation; quarantine ambiguity.
- Acceptance: private placements, derivatives, mixed/old plans, joint filers, partial executions, missing fields, and original/amended filings do not create false discretionary purchases or duplicate episodes. Manually compare a stratified set of at least 50 permitted filings with source display; publish discrepancies and parser version.
- Dependencies: C06.

### C08 — `feat: extract typed Item 408 plan lifecycle events`

- Add quarter-scoped, passage-backed adoption, termination, modification/replacement, expiry, and completion records; deduplicate amended disclosure versions. Start with deterministic extraction/manual review; ambiguous text stays unknown.
- Acceptance: a voluntary expiry mention and a termination-plus-replacement are not counted as active termination; absent sections differ from explicit zero; amended 10-K data does not become an annual count.
- Dependencies: C06–C07.

### C09 — `feat: ingest earnings exposure and contextual event histories`

- Add adapters/imports for earnings, known blackout intervals, insider tenure, conferences, and corporate relationships with publication/effective times and coverage flags. Missing board-meeting coverage stays unknown.
- Acceptance: future earnings-date corrections cannot alter replay; fiscal-year offsets and timezone boundaries are covered; no source means unavailable, not “no event.”
- Dependencies: C05–C06; real adapters additionally require C03 permissions.

### C10 — `feat: snapshot aircraft registries and preserve identifier histories`

- Add FAA snapshot parsing, airframes, tail/ICAO aliases, registrant types, fractional flags, withholding/unknown states, and archive manifest.
- Acceptance: tail reassignment and sparse annual history do not backdate ownership; malformed/missing fields are quarantined or explicitly unknown; individuals/fractional candidates cannot enter active tracking.
- Dependencies: C03–C04.

### C11 — `feat: review company aircraft control links with evidence`

- Add candidate generation, intermediary-address exclusions, historical home-base evidence, alternative candidates, and human approval/rejection with effective periods. Separate registrant/operator/controller.
- Acceptance: shared trustees and shared airports cannot auto-confirm a link; future approval cannot enter past operational replay. Evaluate a stratified sample including rejected and low-volume candidates; record attribution precision and unresolved share.
- Dependencies: C05, C10. Home-base input initially accepts validated fixture observations; recompute from C13 real observations only when available.

### C12 — `feat: normalize airports and compute versioned office proximity`

- Add stable airport IDs, typed code aliases, geodesic proximity, office deduplication, and valid-time airport-office links.
- Acceptance: no-IATA airports, local-code collisions, coordinate order, distance units, and just-inside/outside radius fixtures behave correctly; multiple offices do not multiply host attribution.
- Dependencies: C05.

### C13 — `feat: ingest licensed flight legs with coverage and cost controls`

- Implement the selected provider adapter behind a common contract, plus replayable fixtures. Store actual/inferred timing, source IDs, provider watermark, missing coverage, deduplication, and conflicting-leg quarantine.
- Acceptance: retries and pagination respect budgets; provider outages do not become zero flights; nightly completed-flight sources are never represented as live arrivals; ambiguous identities remain unresolved.
- Dependencies: C03–C04, C10–C12. **G1:** reproducible source snapshots, supported identities, and explicit historical gaps.

### C14 — `feat: infer completed visits and background-aware host weights`

- Pair supported arrivals/departures, retain observation coverage, classify ordinary-trip alternatives, apply one event discount, and allocate candidate-host plus background weight using the blueprint formula.
- Acceptance: missing departures yield incomplete visits; future departure cannot enter an earlier score; lone distant host gets less than full attribution; duplicate offices and multi-event matches cannot inflate counts.
- Dependencies: C09, C11–C13.

## Deterministic features: C15–C18

### C15 — `feat: compute versioned flight novelty and intensity signals`

- Implement dwell/recency weighting, prior-episode novelty, and visitor-wide intensity with declared window bounds, configuration versions, and evidence IDs.
- Acceptance: exact 1/4-hour boundaries, current-episode exclusion, multi-host deduplication, zero variance, and the full intensity lookback are covered. Insufficient observation returns unavailable.
- Dependencies: C14.

### C16 — `feat: compute buying silence and termination signals`

- Implement purchase episodes/role weighting, distinct natural-person clusters, recent observation watermark, blackout-adjusted exposures, fixed eligible-insider denominator, and quarter-scoped termination age.
- Acceptance: silent-denominator example increases from 1 to 1.1; empty exposure, no eligible insiders, late filings, unknown plans, and stale disclosure do not generate inflated signals. Report sensitivity to the count-model assumptions.
- Dependencies: C07–C09.

### C17 — `feat: version strategy embeddings and financial ratio features`

- Add permitted text/financial imports, chunk/equal-document aggregation, embedding revision registry, similarity transform, currency/security normalization, ratio availability, and uncertain role estimates.
- Acceptance: mismatched embedding versions, zero vectors, future text, restated cash, and invalid market-cap denominators are handled. Reproduction uses cached permitted embeddings, not a new model call.
- Dependencies: C03–C05. Real-source integrations use bounded sample checks; mocked embedding outputs cover CI.

### C18 — `feat: freeze reproducible pair feature runs and coverage manifests`

- Generate both directional hypotheses from a frozen universe, publish feature snapshots atomically, calculate the signed-capable interaction, and support deterministic offline report export. Required missing features yield unscorable, not zero.
- Acceptance: a full fixture run repeats exactly; adding future filings/links/text/departures cannot alter an earlier replay; concurrent/interrupted runs cannot expose partial feature vectors.
- Dependencies: C15–C17. **G2:** deterministic signals and complete provenance verified.

## Research and model gate: C19–C24

### C19 — `feat: curate definitive deal events and mature directional labels`

- Add taxonomy, earliest public announcement evidence, direction, canonical deal identity, announcement/withdrawal lifecycle, pending follow-up, and label coverage.
- Acceptance: press release before filing establishes the earlier announcement; already-announced pairs are excluded; incomplete 180-day examples remain pending; reverse journeys cannot double-count a deal.
- Dependencies: C05–C06, C18.

### C20 — `feat: register immutable experiments and evaluation partitions`

- Add a machine-validated experiment manifest for universe, train/calibration/threshold/test intervals, label embargoes, sample requirements, alert budget, precision/lift targets, cooldown, seed, bootstrap, and fail/inconclusive criteria.
- Acceptance: missing target values, overlapping label windows, or fit-time unknown labels reject registration; manifest hashes cannot silently change after test evaluation. Populate numeric criteria from development inventory before test inspection.
- Dependencies: C18–C19. These values are deliberately not guessed in this planning document.

### C21 — `feat: replay temporal datasets with matched baseline eligibility`

- Build as-of datasets, complete-feature comparison cohorts, full-universe coverage accounting, deterministic negative sampling, and episode grouping. Produce flight-only, insider-only, and seeded-random baseline inputs.
- Acceptance: September example is pending in October; future corrections do not leak; delisted firms remain eligible historically; candidate pruning cannot silently shrink random-baseline denominators.
- Dependencies: C20.

### C22 — `feat: train calibrate and version regularized pair models`

- Fit train-only preprocessing/logistic models, separate calibration, sampling/prevalence handling, coefficient/reference logit storage, signed contributions, and suitable grouped/bootstrap uncertainty. Register candidate artifacts without activating them.
- Acceptance: contributions reconstruct the raw logit; zero totals are valid; one-class bootstrap draws and convergence failures are explicit; calibrated outputs stay separate from raw-logit attribution.
- Dependencies: C21.

### C23 — `feat: evaluate sealed models and enforce promotion decisions`

- Report matched-budget episode precision/recall, uncertainty, independent counts, lead time, calibration, coverage, and model ablations. Enforce PASS/FAIL/INCONCLUSIVE against C20 criteria and record test-access history.
- Acceptance: one deal with 100 daily alerts counts once; veto-everything cannot win by a false-alarm metric; inadequate samples cannot pass; failed/inconclusive candidates cannot become production models.
- Dependencies: C22. **G3:** only a documented sealed-test PASS permits predictive product activation. A valid FAIL is a useful research deliverable, not a reason to relax the gate.

### C24 — `feat: measure executable benchmark returns and historical lookalikes`

- Add next-tradable-open entry, 5/30-session return policy, adjusted prices, benchmark, halts/delistings, unavailable results, and optional mature-case retrieval including failed neighbors.
- Acceptance: premarket alerts cannot use prior close; holidays and halts shift the executable entry correctly; future outcomes and current-deal duplicates never enter lookalikes.
- Dependencies: C17, C19–C23. Optional for first predictive validation; required before publishing these metrics/features.

## Auditable product: C25–C29

### C25 — `feat: append immutable alert review and outcome events`

- Add versioned alerts, canonical pair episodes, draft/review/approved/vetoed/superseded states, hash-chain verification, and independently retained checkpoints. Track announcement outcome separately from returns and withdrawal.
- Acceptance: corrections append versions; mutated old events break verification; one episode cannot become multiple successes; missing maturity never appears as negative.
- Dependencies: C18–C19, C23. A fixture-only implementation may proceed before G3 but activation remains blocked.

### C26 — `feat: constrain evidence review and human alert approval`

- Add analyst review flow, schema-bound optional LLM extraction/skeptic/report drafting, claim-to-evidence checks, prompt/model/cost logging, and pre/post-review evaluation. Keep fallback deterministic summaries and fail-closed approval.
- Acceptance: malicious instructions inside source text cannot invoke tools or approve alerts; unsupported numbers/claims fail validation; timeout does not approve; historical core evaluation excludes unrestricted LLM hindsight.
- Dependencies: C25. No need to create sixteen independent agents.

### C27 — `feat: expose tenant-scoped alerts history and watchlist APIs`

- Add FastAPI contracts, hashed/scoped/revocable keys, customer isolation, bounded queries, cursor pagination, all watchlist operations, separate analyst authorization, and permitted evidence presentation.
- Acceptance: cross-tenant reads/writes fail; revoked keys fail; drafts/vetoes do not leak; stale/unknown signals remain explicit; ticker-history ambiguity does not select the wrong company.
- Dependencies: C25–C26.

### C28 — `feat: deliver approved subscriptions through a durable outbox`

- Implement one notification channel, verified destinations, unsubscribe, unique delivery keys, pre-send state checks, reconciliation, ambiguous-timeout handling, and provider idempotency where supported. Add webhook protection when implementing a webhook channel.
- Acceptance: crash before/after provider acknowledgment is covered; superseded approval and unsubscribed destinations do not send; logs exclude secrets; unsupported exactly-once guarantees are not claimed.
- Dependencies: C27. Use stub/sandbox delivery in CI and shadow mode.

### C29 — `feat: add evidence-first dashboard and transparent track record`

- Build dashboard over existing API: approved alerts, directional pair history, unavailable coverage, signed contributions, evidence, watchlists, and append-only outcomes. Choose the smallest compatible frontend stack at implementation time.
- Acceptance: keyboard/accessibility and responsive checks; no fabricated probabilities; pending outcomes are visible; corrections preserve original predictions; customer UI cannot reach analyst-only data. Demo mode is prominently synthetic.
- Dependencies: C27–C28. Actual website implementation follows the applicable website tooling workflow when that work starts.

## Operations and release: C30–C31

### C30 — `ops: add scheduled runs readiness monitors and recovery drills`

- Add trading-calendar scheduling, 08:30 America/New_York deadline, source completeness/readiness, drift/missingness/volume/cost checks, locks and resume, backup/restore, key rotation, and runbooks.
- Acceptance: DST and holidays, corrupt snapshots, provider outage, budget exhaustion, concurrent jobs, interrupted review/delivery, and a real restore drill produce the documented safe states.
- Dependencies: C25–C29. **G4:** ten consecutive successful trading-day shadow runs, tested recovery, evidence review, and G3 still valid. Source rights remain applicable.

### C31 — `release: record launch evidence and enable limited production`

- Register the immutable release manifest, approved model, configuration, rights references, operators, deployment/rollback instructions, and explicit rollout limits. Add production activation guard checks and a kill switch.
- Acceptance: missing G0–G4 evidence prevents activation; rollback returns to the prior compatible application/model/configuration without deleting audit history. Production sends start only for configured verified subscriptions.
- Dependencies: C30. **G5:** limited rollout; observe readiness and delivery before increasing scope. Quarterly retraining creates new candidates that must pass promotion checks again.

## Decisions that need evidence, not guesses

| Decision | Owner/output | Latest point |
| --- | --- | --- |
| Initial sector and historical interval | Maintainer approves coverage report | C03/G0 |
| Flight/text/price provider and applicable rights | Maintainer records capability/rights manifest | Before each real adapter/backfill |
| Minimum sample sizes, alert budget, precision/lift targets | Research manifest justified with development inventory | C20, before test inspection |
| Unknown coverage and acceptable interval widths | Registered model/readiness policy | C20 and G3 |
| Hosting, alert channel, destinations, operating budget | Deployment config and runbook | C28–C31 |

External approvals or inaccessible history can block a gate; they do not block offline fixtures and deterministic correctness work. Do not present pending decisions as completed or invent research results to advance the plan.

## Definition of done for every implementation PR

The PR explains the behavior changed and relevant audit IDs, includes necessary migrations and behavioral tests, passes established CI, and updates its contract/runbook when behavior changes. No undocumented source rights, hidden zero-imputation, future-data access, or automatically enabled notifications. Review test evidence appropriate to the change; do not add tests that only restate documentation or implementation details.
