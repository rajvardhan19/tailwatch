# Offline research and model gates

C19–C24 implement typed definitive deal evidence, mature directional labels, immutable experiment registration, temporal cohorts, matched baseline rankings, L2 logistic candidates, separate calibration, sealed episode evaluation, executable-price returns and mature-case retrieval. These APIs do not generate or claim real predictive success.

Labels use earliest known definitive publication evidence. Both positive and negative examples require full 180-day followup and complete label coverage; already-announced pairs are excluded across orientations. Taxonomy excludes minority/asset/rumor/equals/consortium events. Withdrawal does not undo a public-announcement success. Coverage and canonical deal IDs must be independently curated.

Experiment manifests require explicit numeric sample/budget/precision/lift criteria with rationale. They do not guess values from a sealed test. Train, calibration, threshold and test intervals have 180-day label embargoes. Train-only standardization and regularized full-batch logistic fitting store coefficients, intercept and feature schema. Calibration is separate and requires representative unsampled data. Nonconvergence or one-class fit is an error. Contributions reconstruct the raw logit; calibrated probabilities are not attributed as additive percentages. Artifacts are candidates only.

Register the experiment in PostgreSQL, then seal the model hash, label-data commitment, development-selected threshold, matched-baseline precision and eligible-deal count before accessing the test. `evaluate_sealed` verifies that exact seal and durably commits single-use test access before computation; a failure still consumes access. Results append afterward. The data custodian must tie the label commitment to the independently retained complete feature/label artifact and full-universe coverage report. This is not protection against a database administrator or a researcher opening the raw test outside the application.

Evaluation freezes the first episode prediction and counts a definitive deal at most once. Reports include precision, recall, Brier/log loss, calibration bins, lead time, episode bootstrap and a conservative Wilson envelope. One-class bootstrap draws are reported; they are not successful refits. The current bootstrap is over independent episode outcomes, not a fitted-coefficient uncertainty estimator. Full pair/time-block model refits, PR curves and ablation reports still require the registered research dataset and experiment policy. Inadequate samples or undefined baseline produce INCONCLUSIVE; synthetic evaluations can never PASS. No production promotion occurs here.

Returns use adjusted opens/closes and benchmark values, next executable open after alert, 5/30 observed sessions, halts and explicit unavailable exits. Callers must supply complete exchange-session price coverage (including missing/delisted records); sparse price lists must not be treated as a calendar. No real price vendor or historical returns have been validated. Lookalikes use only mature known outcomes, include failed cases, and exclude the current pair in either orientation.

Return calculations require an explicit complete exchange-session calendar (`expected_opens`). Missing prices within entry discovery or the holding horizon make the result unavailable, rather than shortening a 5/30-session horizon. Prices must be available after session close.

## Development uncertainty and ablations

`diagnostics.grouped_uncertainty` resamples whole deal groups (or episodes without a deal ID) separately inside train and calibration partitions. Each draw refits preprocessing, regularized coefficients and calibration; test rows are excluded. Coefficient intervals use original feature units. One-class and fitting failures are counted without replacement, and fewer than 100 successful draws or an 80% success rate yields inconclusive intervals. The reference feature vector must be fixed before reviewing test outcomes. These diagnostics never authorize promotion.

`fit_ablations` fits declared feature subsets on the same union-of-required-features cohort before test starts. `cohort_coverage` preserves the explicit frozen-universe denominator and reports missing/unscorable episodes. Baselines freeze the first chronological episode observation; later high scores cannot replace it, and conflicting same-time observations are rejected. Real reports still require representative data and independent review of the grouping scheme.

## Sealed feature/model provenance

For a promotable sealed result, commit the full test `Example` records (features, labels, availability and identities) using `feature_commitment`, hash the exact `LogisticArtifact.model_dump_json()` with SHA-256, and preregister those hashes with the fixed threshold/baseline settings. Generate scores with `artifact_predictions` and pass the artifact plus examples to `evaluate_sealed`. The evaluator recomputes scores, checks the model's experiment/synthetic identity and pre-test fit time, verifies the full dataset hash, then records single-use test access before calculation. Changed supplied probabilities are rejected.

Legacy score-only evaluation remains available for diagnostics, but an otherwise passing result becomes INCONCLUSIVE without verified model/data provenance. Production release validation independently requires `provenance_verified=true`. This proves consistency between submitted artifacts, not that a dataset is complete, licensed, unbiased, or independently held; G0/G3 review still has to establish those facts. Do not use sealed test data to select ablations or bootstrap settings.

`tailwatch research-report INPUT.json --objects DIRECTORY` accepts the `ReportInput` contract: experiment, train/calibration examples, full expected episode IDs, primary feature names, declared ablation variants, fitted-at cutoff and a fixed reference vector. It rejects threshold/test observations, retains canonical input and report objects, and prints only the report digest and non-promotion flag. Use `inspect-trace FILE --icao REVIEWED_HEX --available-at TIMESTAMP --objects DIRECTORY` to inspect a local authorized trace without fetching network data or inventing coverage.

## Insider-silence backtest (Past deals)

`tailwatch backtest config/backtest.insider-silence.json --manifest config/sources.backtest.json --source sec-backtest --objects .data/sec-objects --output src/tailwatch/static/backtest.json` runs a point-in-time research backtest of the insider-silence component on real SEC data. It needs `TAILWATCH_DATABASE_URL`, for the durable budget and journal, and `TAILWATCH_SEC_USER_AGENT`. It takes the single SEC worker lock, so the live filings feed pauses while it runs. Every response is journaled in `sec_fetches` and the object store.

- **Registered first.** The config fixes the threshold, windows and every case before any score exists. Its digest is written into the report, and a test checks that the committed report matches the registered config.
- **Deals.** Each is verified against the target's own filings: an Item 1.01 8-K within 10 days of the expected date. The announcement date is the earliest of that date and any nearby 8-K or 425 filing. Unverified deals are excluded, never scored.
- **Point in time.** Each case is scored with `features.signals.silence` as of two days before the announcement, using only Form 4s *filed* before that day. The observation watermark sits two business days earlier still. The recent window is 45 days; the baseline is the 365 days before it.
- **What counts as a sale.** Non-derivative code S trade days by reporting owners flagged director or officer. Habitual sellers need at least four baseline sale days; companies without any are reported as insufficient history.
- **Controls.** Same-sector peers are scored on the matched deal's date. Peers that file merger-target forms (proxy statements or tender responses) within 180 days are excluded.
- **Reporting.** The Past deals view and the report list successes, misses, false alarms and unscorable cases, with Wilson intervals and a hit-rate ÷ false-alarm-rate lift.

**Limitations.**
- Only insider silence is tested. The flight component needs historical flight coverage and reviewed company-aircraft links, so it is reported unavailable.
- The deal sample is small and hand-selected, so it is not representative.
- 10b5-1 plan status is unknown before April 2023 and is not excluded.
- Blackout periods are not modelled, and weekdays approximate trading days.
- The peer exclusion cannot tell a target proxy from an acquirer proxy, so a peer that files a merger proxy as an acquirer is also dropped (conservative). In the first run this removed Diamondback Energy.
- A case whose filings cannot all be retrieved is reported as missing source rather than scored on partial data. In the first run, transient connection failures left Cadence (April 2021) unscored.
- This is research evidence about one signal. It is not a validated model, a sealed evaluation or release-gate evidence. Changing the threshold after seeing these results would make any rerun exploratory, and it must be labelled as such.
