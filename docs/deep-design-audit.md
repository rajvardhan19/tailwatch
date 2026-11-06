# Deal Signal: detailed design audit

Review date: November 5, 2026. Scope: the complete supplied blueprint and the first design review. No application code, real datasets, credentials, or model artifacts were supplied. This is a specification audit, not a code/security audit or evidence that the proposed signals predict acquisitions.

**Assessment:** the blueprint is suitable for guiding a research prototype, but its current definitions could produce misleading signals and overstated backtest results. Resolve the data and evaluation blockers before building customer notifications. The sixteen-role workflow is not the main technical challenge; historical data availability and valid inference are.

Labels below distinguish **correction** (a factual error or internal inconsistency), **gap** (an implementation rule is missing), and **hypothesis** (an empirical assumption requiring validation). P0 blocks credible validation or the intended launch; P1 should be resolved before operational alerts; P2 is a hardening or usability improvement. Priorities are review judgments, not measured failure rates.

## A. Source assumptions and commercial feasibility

### A1 — P0 correction: code P does not mean open-market common-stock buying

The SEC defines P to include private purchases and derivative securities. Code S likewise includes private sales. A P/S-only filter cannot implement the stated open-market signals. Separate non-derivative securities, identify security class and acquisition/disposition direction, and use footnotes/context for transaction venue. If venue cannot be established, label it unknown rather than open-market. Test a private placement and derivative purchase alongside an ordinary stock purchase. [SEC Form 4 instructions](https://www.sec.gov/files/form4.pdf).

### A2 — P0 correction: an unchecked plan box is not proof of discretionary trading

SEC guidance says the checkbox does not apply to plans adopted before the amendments' effective date. Use historical schema/plan applicability and footnotes; preserve unknown status. Do not allow a missing XML field to default to false. [SEC guidance, Question 120.31](https://www.sec.gov/rules-regulations/staff-guidance/corporation-finance-interpretations/exchange-act-rules).

### A3 — P0 correction: the proposed provider budget may not cover B2B distribution

FlightAware's published tiers distinguish internal business/B2C use under Standard from B2B commercialization under Premium. Standard's $100 minimum is therefore not a sufficient budget assumption for selling alerts/API access to firms; Premium currently lists a $1,000 minimum. Obtain terms covering this particular derived-data product, historical retention, evidence display, and downstream API distribution. These are published tier distinctions, not a legal conclusion about a negotiated contract. [FlightAware pricing and authorized uses](https://www.flightaware.com/commercial/aeroapi/).

### A4 — P1 correction: licensing does not begin only when someone pays

The blueprint's final cost advice conflicts with its earlier licensing cautions. OpenSky includes some internal for-profit uses within commercial use; ADS-B Exchange explicitly requires a commercial license for company projects, including internal ones. Select an approved research/prototype scope before ingestion and get the applicable permission before commercial use. [OpenSky FAQ](https://opensky-network.org/about/faq), [ADS-B Exchange policy](https://support.adsbexchange.com/hc/en-us/articles/37363886073613-I-m-building-a-project-for-my-company-but-we-aren-t-making-money-off-of-it-can-I-get-a-free-API-key).

### A5 — P1 correction: live positions and completed flight records have different latency

OpenSky says flights and inferred airports are generated in a nightly batch for the finished UTC day. Intraday Form 4 rescoring can reuse the last flight snapshot, but cannot imply that this provides current completed flights. Store source-specific watermarks; a separate validated position-to-flight detector is needed for live visits. [OpenSky FAQ, question 14](https://opensky-network.org/about/faq).

### A6 — P0 gap: annual registry snapshots cannot reconstruct daily ownership

The FAA does provide annual archives, but an annual snapshot does not establish every ownership change within that year. Never apply an end-of-year owner to earlier months without contemporaneous evidence. Define conservative validity intervals and unknown periods. First inventory which years and dates are actually available; the existence of an archive listing is not proof of a complete history. [FAA download and archive page](https://www.faa.gov/licenses_certificates/aircraft_certification/aircraft_registry/releasable_aircraft_download/index.cfm).

### A7 — P1 gap: FAA ownership withholding is another coverage limitation

The FAA advertises a process for withholding owner names and addresses. PIA, LADD, and missing registry ownership information are different limitations. Add explicit unresolved/withheld ownership states and measure their incidence; do not assume every corporate registrant is publicly resolvable. [FAA registry notice](https://www.faa.gov/licenses_certificates/aircraft_certification/aircraft_registry).

### A8 — P1 gap: the acquisition plan omits required datasets

The signals require point-in-time earnings calendars, fiscal quarter boundaries, officer tenure, company address histories, subsidiary relationships, currencies, security identifiers, and a source for known events. None has an ingestion contract. Board meetings in particular cannot be assumed to have a complete public schedule. List source, coverage, publication latency, history, and licensing for each dependency. Absence from an event feed means “no event found in this feed,” not “no event occurred.”

## B. Entities, aircraft, and visits

### B1 — P1 correction: a corporate registration does not establish corporate business travel

An LLC or trust name is insufficient evidence of a tracked operating company's control. Conversely, a registered corporate owner may lease an aircraft to somebody else. Separate registrant, operator, beneficial controller, and the company whose travel can reasonably be attributed. Name normalization produces candidates, not automatic confirmation. Record supporting documents, approval, effective interval, and contrary evidence.

### B2 — P1 gap: trust addresses and nearby home bases can be shared

An owner-trustee or registered-agent address can link unrelated entities, and many companies share the same home airport. Require independent address evidence and exclude known intermediary addresses from company matching. Record alternative candidates. Home-base estimation must use only earlier observations and sufficient observed nights; sparse overnight coverage should not yield a confident home base.

### B3 — P1 gap: selection by alert volume creates a circular review process

The top aircraft by alert volume cannot be the only ownership-validation sample: erroneous ownership can itself generate high volume. Review before activation, then prioritize by exposure and uncertainty. Add a stratified audit sample of low-volume and rejected candidates. Aircraft counts alone do not measure ownership precision or false exclusions.

### B4 — P1 gap: airport code is not a stable, universal identifier

Use a stable airport ID and separately typed ICAO, IATA, local, and provider identifiers. OurAirports documents differing identifier semantics and a persistent numeric ID. Retain code aliases and dataset versions. Test small airfields without IATA codes and ambiguous local codes. [OurAirports dictionary](https://ourairports.com/help/data-dictionary.html).

### B5 — P1 gap: aircraft timestamps and units are underspecified

Preserve UTC observation times, inferred versus actual arrival/departure, uncertainty, and source flight IDs. Use geodesic distances in known units, validate latitude/longitude ordering, and test points around the 50 km boundary. Reject impossible time ordering and quarantine conflicting feeds. The same model must distinguish airframe identity from changeable tail/ICAO identifiers.

### B6 — P1 correction: aircraft ground time is not executive meeting duration

A jet can reposition or remain parked after passengers leave. Preserve “observed aircraft dwell” wording and never infer passenger identity or a meeting from it. Missing departure observations must not become unlimited dwell. Four hours, one hour, and the 50 km radius are hypotheses to tune on development data, not established travel rules.

### B7 — P1 correction: city normalization forces every landing toward a tracked host

If weights over tracked hosts sum to one, a lone tracked office receives all weight even when the trip is unrelated. The set of tracked companies then changes the score without a change in travel. Add a documented unrelated-destination/background component and coverage measure. For illustration, if three equally plausible hosts have total relevant-host mass 0.3, each receives 0.1 and background receives 0.7; these are illustrative values, not estimated probabilities. Deduplicate offices by company before weighting.

### B8 — P1 gap: ordinary trips are insufficiently controlled

Separate home-base returns, own-company sites, subsidiaries, maintenance stops, charter operation, and recurring customer/supplier routes. Many routine relationships may explain a trip. Preserve relationship evidence and evaluate the model against these hard negatives. A conference match should be one hypothesis, not an automatic explanation; multiple event overlaps need a documented aggregation rule.

## C. Insider, strategy, and feature definitions

### C1 — P1 gap: multiple filing rows do not necessarily mean multiple independent purchases

Partial executions, joint reporting owners, indirect holdings, and amendments can duplicate economic activity. Preserve raw rows, then group economic transactions with explicit provenance. Count distinct people for the buyer-cluster feature, excluding entity reporting owners unless a separate policy applies. Define whose role wins when someone is both officer and director, and freeze that role as of the event.

### C2 — P1 hypothesis: four annual sales do not establish a Poisson selling process

Four sales might be four executions on one day, quarterly vesting, or a depleted plan. Count independent selling episodes and examine seasonality, plan status, tenure, and remaining holdings where available. The expression exp(-lambda) assumes the stated count model; validate its calibration before interpreting lambda as surprise. Do not describe a heuristic silence score as a measured probability of suspicious behavior.

### C3 — P1 gap: zero recent sales may reflect reporting latency

Set an observation watermark that allows for filing availability. At a premarket run, a recent transaction may not yet have been reported. Preserve event-time and filing-time distinctions and do not infer that an insider stopped trading on the latest observed day. Treat partial observation windows explicitly.

### C4 — P1 correction: “not required to disclose expiry” does not imply all mentions are active cancellations

SEC guidance says expiration/completion need not be disclosed; it does not forbid issuers from mentioning it. Also, qualifying modifications are treated as termination plus adoption. Classify expiry, completion, termination, modification/replacement, and uncertain text separately. A raw count of mentions can misclassify routine replacements. [SEC Item 408 guidance](https://www.sec.gov/rules-regulations/staff-guidance/corporation-finance-interpretations/regulation-s-k), [SEC final rule](https://www.sec.gov/files/rules/final/2022/33-11138.pdf).

### C5 — P1 gap: a quarterly termination count needs a reporting-period contract

Use event identities, arrangement type, relevant fiscal quarter, accession, and source passage. Do not treat a 10-K's annual scope as twelve months of comparable Item 408 events or sum amended filings. A missing section is unknown; an explicit no-change disclosure is zero. Define how long an observed termination feature remains active and whether age is a separate input.

### C6 — P1 correction: the “overlap bonus” does not encode temporal overlap

Multiplying decayed flight and silence scores yields a nonzero interaction even if their underlying events are weeks apart. Either call it a statistical interaction or define an explicit timing window. A fitted coefficient may be negative; logistic regression does not guarantee a bonus. Preserve negative estimates instead of forcing a favorable story.

### C7 — P1 gap: feature definitions disagree or remain incomplete

City weighting is described once as distance plus strategic fit and later as distance only. Choose and version a definition. Using fit both in host assignment and as a model feature introduces dependence that should be tested by ablation. Specify buying magnitude, cluster-window boundaries, decay anchor, novelty lookback excluding the present episode, and zero-variance intensity handling. Define whether intensity measures all visitor trips or visits to this particular host.

### C8 — P1 hypothesis: semantic similarity is not strategic acquisition fit

Similar text can identify competitors; complementary acquisitions may have low similarity. Separate semantic similarity from a claimed acquisition thesis. Specify chunking, aggregation, freshness, document types, and missing-text handling. Version the embedding model and prohibit deal-announcement text from pre-announcement features. Any LLM interpretation of historical documents can contain learned future knowledge: use constrained evidence extraction and disclose that replay limitation.

### C9 — P1 gap: the universe and financial features are incompatible in places

Private targets and PE firms need not have public market capitalization or SEC insider-trading coverage. Either restrict the initial model to supported public-company pairs or define separate feature/coverage policies. Store security/share-class mappings, point-in-time financials, currency, and valuation dates. Size and cash are weak role indicators; label buyer/target as an estimate and allow unknown, consortium, and merger-of-equals cases.

## D. Model, labels, and evaluation

### D1 — P0 gap: the prediction unit and outcome are not fixed

Specify whether success means any deal between the two companies or specifically V acquiring H. Use a canonical pair/deal identity for evaluation and directed travel records for features. Reversed journeys must not count as two successfully predicted deals. Define acquisitions, minority stakes, asset purchases, rumors, signed agreements, withdrawals, and completed transactions separately.

### D2 — P0 gap: filings are not automatically first-announcement labels

An 8-K, S-4, or proxy can follow a press release. Extract and verify the earliest public announcement timestamp from evidence, distinguish rumors from definitive announcements, and exclude already-public pairs. Daily filing dates are too coarse for intraday claims. SEC itself notes that public document availability can lag acceptance. [SEC access and timing FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions).

### D3 — P0 gap: mature labels and point-in-time transformations remain mandatory

Keep pending outcomes until 180 days of follow-up exist. At each training cutoff, exclude rows whose labels could not yet be known. Purge overlapping outcome windows at split boundaries, fit transformations only on training data, and use tuning data for thresholds/calibration. Version company/office/ownership/strategy histories, not only flights. Quarterly retraining must not use the preceding quarter's unresolved examples as negatives.

### D4 — P0 gap: candidate selection can manufacture apparent lift

Define the deployment candidate universe before scoring. If combined-model candidates require flights, comparing against random pairs with no comparable eligibility inflates lift. Report both end-to-end recall over the intended universe and conditional precision among scoreable candidates. Disclose coverage exclusions and maintain the same negative sampling and data access rules for competing models.

### D5 — P1 gap: precision alone rewards sending almost no alerts

Compare methods at matched alert budgets or recall, report alert count and independent deal count, and include precision-recall curves, calibration, and uncertainty. A method with one correct alert should not win by precision alone. Choose a minimum sample requirement and practical improvement criterion before viewing the held-out test. If evidence is insufficient, report inconclusive rather than passed.

### D6 — P1 gap: repeated pair-days invalidate naive uncertainty estimates

Daily alerts and correlated acquirers are not independent experiments. Evaluate first-alert episodes with a fixed cooldown and resample suitable pair/deal/time blocks. Record how bootstrap fits with no positive class or failed optimization are handled. Two hundred bootstrap samples is a starting compute budget, not a guarantee of a reliable 90% interval. Evaluate robustness to sector/time regimes and large repeat acquirers.

### D7 — P1 correction: a logistic score is not automatically a calibrated probability

Regularization, sampling, class weighting, and drift can change calibration. Assess out-of-time calibration and proper scoring metrics, then calibrate using separate development data if supported by sample size. Do not equate attribution percentages, prediction intervals, ownership confidence, and data coverage. [Scikit-learn calibration documentation](https://scikit-learn.org/stable/modules/calibration).

### D8 — P1 gap: returns require an executable timing convention

Specify the next tradable price after alert publication, trading-session rather than calendar-day horizons, adjusted returns/dividends, benchmark, delistings, trading halts, and overlapping positions. A 06:00 UTC alert cannot buy at the previous close. Market-subtracted return is a chosen benchmark metric, not necessarily risk-adjusted alpha; if presenting a trading strategy, include costs and portfolio rules. Keep private targets marked not applicable.

### D9 — P1 gap: the skeptic needs a controlled evaluation

Save pre-skeptic scores, evidence, decisions, and all mature veto outcomes. Compare with and without the skeptic at matched output volume. Reporting only that vetoed alerts were usually false can flatter a veto-everything policy in a rare-event setting. Historical skeptic judgments must not use later announcements or unrestricted current web searches. A veto should alter delivery status, not silently rewrite the model's probability.

### D10 — P1 gap: lookalike retrieval can leak future outcomes

Use only cases whose features and displayed outcomes were available at alert time. Version scaling and similarity rules, show unsuccessful comparable cases as well as successes, and retain the retrieval set. Exclude the current transaction and near-duplicate episodes. “Similar pattern” must not be represented as evidence that the same outcome will follow.

## E. Schema, workflow, and product

### E1 — P1 gap: several necessary entities are absent

Add explicit evidence/raw objects, source snapshots, ingestion runs and watermarks, filing transactions/versions, insiders and role history, financial observations, earnings calendars, pairs, model versions, score runs, review decisions, tenants, watchlists, and notification deliveries. Plan events need insider IDs and actual event dates; visits need aircraft/flight evidence references. Keep features uniquely identified by pair, as-of time, definition version, and run.

### E2 — P1 gap: database invariants and portability are undefined

Define foreign keys, uniqueness, non-overlapping ownership intervals, UTC timestamps, nonnegative durations, probability bounds, ordered interval endpoints, and currency-aware decimals. CIKs/tickers are identifiers, not arithmetic values. SQLite and PostGIS do not provide identical geospatial, timezone, JSON, or concurrency semantics: share logical models but use explicit dialect migrations and run integration checks against production PostgreSQL.

### E3 — P1 gap: daily and intraday runs can observe mixed snapshots

Freeze an as-of cutoff plus source watermark manifest for each run. Stage ingestion before atomically promoting a complete snapshot. Prevent overlapping runs from approving conflicting alert versions. Define retries, dead-letter handling, restart/resume behavior, and deterministic recomputation. Persist code/config/model versions and dependency hashes with every score.

### E4 — P1 gap: the language-model authority boundary is unclear

Use a deterministic workflow for state transitions and numeric computation. Define which decisions require human approval, who can override vetoes, and what happens on timeout or malformed output. Retrieved filings and websites are untrusted data: they cannot change agent instructions or cause tool execution. Validate extracted fields and bind each report claim to a source passage and computed metric, not just a URL.

### E5 — P1 gap: approval, audit, and delivery need separate states

Use draft, review, approved, vetoed, superseded, and delivery outcomes with immutable decision history. Append changes and outcomes instead of mutating the original alert. Use an outbox, unique delivery keys, retries, and a send-time check of the approved version. A corrected alert should produce an explicit correction rather than rewriting the public record. Protect a hash chain with independently retained checkpoints; hashes in one mutable database do not prevent rewriting.

### E6 — P1 gap: API keys alone do not isolate customers

Define tenant ownership and authorization for watchlists and delivery destinations, scoped/hashed/revocable keys, pagination and stable cursors, bounded queries, error contracts, and audit-log redaction. Validate webhook destinations to prevent access to internal services, keep webhook secrets out of logs, and bind subscriptions to verified recipients. Do not expose draft/vetoed alerts through public endpoints by accident.

### E7 — P1 gap: monitoring measures newest rows instead of completeness

One fresh filing can hide many missed filings. Reconcile expected indexes/cursors, expected counts, parse quarantines, and source-specific delays. Separate provider outage, a quiet aircraft, and identity loss. Use the trading calendar and America/New_York timezone for premarket deadlines. Document safe degraded operation and alert suppression. Exercise backup restoration and retry behavior, not only ten successful scheduled runs.

### E8 — P2 gap: cost and product contracts are incomplete

Estimate collection/backfill/pagination/retries, storage, embeddings, model review, and manual verification separately. Enforce provider and model budgets before making calls; record cache hits and per-run spend. Define watchlist list/update/delete behavior, unsubscribe, retention, public-log content, and accessible evidence for users without provider subscriptions. The public track record should distinguish pending, expired negative, announced positive, withdrawn deal, and unmeasurable return states.

## Concrete failure examples to use as regression checks

- **Forced destination attribution:** an airport has one tracked host 49 km away. Normalized inverse-distance weighting gives that host 100% of the visit even if another untracked company is beside the airport. Expanding the tracked universe changes the first host's score. Host scores must preserve a background/unknown component and disclose candidate coverage.
- **Immature negative:** a pair is scored September 1 and used as a negative in an October 1 training run. Its 180-day outcome is still unresolved. A December announcement would reveal that the training label was wrong; the October run must have treated it as pending.
- **Pseudo-replication:** one pair generates 100 daily alerts before its deal announcement. Those are 100 correlated rows, not 100 independently predicted deals. First-alert episode metrics count one success, and uncertainty estimation respects that grouping.
- **Unstable silence denominator:** one silent insider has lambda 2, giving an average of 2 if the denominator counts only silent insiders. A second insider becomes silent with lambda 0.2 and that average falls to 1.1 despite broader silence. With a fixed eligible denominator of two, the corresponding values are 1 and 1.1. Define the intended statistic and denominator explicitly.
- **Temporal leakage through dwell:** an aircraft arrives at 05:00 UTC and departs at 10:00 UTC. A 06:00 UTC score cannot use the eventual five-hour dwell. It has at most one hour of elapsed ground-time evidence, subject to observation coverage.
- **Apparent overlap without simultaneous events:** with a 14-day decay, a flight 28 days ago retains exp(-2), about 0.135, of its initial weight. Multiplying it by a current silence score produces a nonzero interaction without establishing a contemporaneous visit and selling halt.

## Recommended acceptance sequence

1. **Data feasibility:** a source/rights/history matrix and a representative sample showing whether ownership, flights, and insider data genuinely overlap over a useful historical period. No predictive claims yet.
2. **Point-in-time foundation:** append-only raw data and versions, source watermarks, explicit unavailable states, stable entity IDs, and leakage/incremental-replay checks.
3. **Deterministic signals:** fixtures covering private purchases, old/mixed plans, amendments, shared trustees, ambiguous airports, incomplete dwell, blackout exposure, and low-history baselines.
4. **Frozen experiment:** explicit universe, outcome taxonomy, label maturity, temporal splits, alert episodes, equal-budget baselines, and predeclared pass/inconclusive criteria.
5. **Shadow operation:** no customer notifications; review evidence fidelity, calibration, false positives, cost, recovery, and data freshness. Compare skeptic versus baseline decisions.
6. **Product delivery:** activate only with applicable data rights, approved content, customer isolation, immutable history, and tested idempotent notifications.

## Matters not established by this audit

Whether the signals predict deals; how many historical positive examples exist; attainable precision; commercial contract terms for this exact product; complete historical flight/ownership coverage; and software correctness remain unverified. The source pages support the factual corrections above. The architecture, statistical failure examples, and recommended fixes are reviewer analysis, not claims endorsed by those providers.
