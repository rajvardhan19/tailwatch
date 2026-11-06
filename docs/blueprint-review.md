# Deal Signal: implementation corrections

Reviewed against the supplied November 5, 2026 blueprint. The workspace contains no starter implementation, so these are design findings, not verified code defects. The original is preserved in `original-blueprint.txt`.

The subsequent [detailed design audit](deep-design-audit.md) expands this first pass with source-verified corrections, prioritized findings, and concrete regression examples. Its source checks supersede the unverified-provider limitation of this initial review for the specific claims checked there.

## Fix before training

1. **Separate event time, public availability, and local ingestion.** Store `occurred_at`, `source_published_at`, and `ingested_at`. Define `known_at` explicitly for each replay mode: a historical public-information simulation differs from replaying what this deployed system actually knew. Preserve versions of corrected filings, ownership links, office locations, financials, and company identifiers. A current company cash balance or later ownership resolution must not enter an earlier feature vector. Persist source hashes and parser/model versions. `known_at` alone cannot prevent leakage from overwritten records or downstream transformations.

2. **Respect label maturity across chronological splits.** At a training cutoff, include negative examples only if their full 180-day outcome window has elapsed. Otherwise their label is pending, not false. Purge training observations whose outcome windows overlap evaluation; account for actual timestamps rather than assuming that a row-count gap equals 180 days. Fit scaling, feature selection, calibration, and thresholds using training/tuning periods only. Repeated daily observations of a pair are correlated: evaluate first-alert episodes and resample pair/deal or time blocks rather than independent daily rows. Freeze the final test period.

3. **Define the eligible universe before measuring performance.** Record the companies and pairs eligible on each day, including delisted firms. Apply the same eligibility and data-coverage rules to combined, flight-only, insider-only, and random baselines. If training samples negatives, correct for sampling or calibrate on the true deployment prevalence. Otherwise the model's output is not an interpretable deal probability. Define which transaction types count as deals and exclude pairs already publicly announced as of scoring time.

4. **Make missing coverage distinct from a zero signal.** Add coverage, freshness, parser-success, and insufficient-history flags. No observed flight is not evidence of no travel. No purchase record is not evidence of no buying when ingestion failed. Suppress notifications when required sources are stale; retain watchlist results with explicit limitations.

5. **Specify insider silence mathematically.** Estimate each insider's selling rate from a baseline preceding the test window, using only eligible, observed trading days. Multiply that rate by eligible exposure in the recent window to obtain lambda. Exclude blackout exposure from both estimation and evaluation. Define the denominator as all eligible regular sellers, not just silent sellers, and publish the eligible count. Treat zero eligible sellers as unavailable. An issuer-specific blackout schedule is preferable; the blueprint's fixed quarter-end window is a heuristic, not a known company policy. Do not use an earnings date learned later to retroactively change a live score.

6. **Keep Form 4 transactions and amendments identifiable.** Accession number identifies a filing, not an individual trade. Use filing plus table and row identity, retain transaction footnotes and reporting-owner identities, and model amendments as linked versions. Do not count an original and its amended replacement twice. A filing-level plan checkbox must not silently classify every transaction in a mixed filing. Preserve `true`, `false`, and `unknown` plan status, including historical schema gaps; unknown must not become discretionary buying. The SEC's rule describes the checkbox and adoption-date disclosures: https://www.sec.gov/files/rules/final/2022/33-11138.pdf.

## Fix before generating alerts

7. **Complete visit and ownership semantics.** Give aircraft stable internal identities and version their tail numbers, ICAO addresses, ownership, and approved company links. Require the link to be effective and known at scoring time. Deduplicate overlapping flight feeds. Pair arrival with the next observed departure from the same airport, flag missing legs, and never infer an overnight stay from missing coverage. For an ongoing visit, use only elapsed observed dwell as of the score time. Normalize host weights once per company, so several offices do not multiply a company's share. Treat candidate hosts as hypotheses rather than confirmed meetings.

8. **Remove feature ambiguities.** Novelty needs a prior-history window that excludes the current visit or visit episode; otherwise the first visit makes itself non-novel. Standardize intensity against comparable historical windows that exclude the current window, and handle zero variance and short history. Define purchase weighting explicitly (count, dollar value, or normalized value), with split/currency handling if relevant. Bound cosine similarity using a documented transform, retain embedding versions, and distinguish missing text from low fit. Define dwell interpolation at exactly one hour and four hours. Version every feature definition.

9. **Separate attribution, probability, and uncertainty.** Logistic contributions are signed log-odds contributions. Absolute percentages hide whether a feature increases or decreases the score; retain signed values, intercept, reference baseline, and a zero-denominator rule. A bootstrap interval describes fitted-model variability under its assumptions, not a guarantee about an individual deal. Validate probability calibration separately. Treat coverage and ownership confidence as separate fields rather than implying that the bootstrap incorporates them.

10. **Make the audit record verifiable and delivery idempotent.** A mutable database row containing its own hash can be rewritten together with that hash. Use immutable alert versions, an ordered hash chain, and independently retained checkpoints or immutable storage. Append outcome and skeptic-decision events instead of editing the original alert. Store every evaluated candidate and veto if skeptic performance is to be measured. Use a transactional outbox and a unique delivery key per alert version/channel/recipient so retries cannot resend notifications. Approval and subsequent edits must have explicit states.

## Scope and validation

Start with a deterministic, single-sector pipeline and fixtures, then replay real historical data. The sixteen named agent roles describe a possible later workflow; they are not required to validate whether the signal has value. Language-model output should be schema-validated and claims should cite structured evidence; an evidence URL by itself does not prove that the evidence supports the claim.

Before accepting a starter implementation, verify these behavioral cases:

- Adding future filings, ownership approvals, or corrected financials leaves an earlier replay unchanged.
- Reingesting a filing or rerunning a daily job produces no duplicate trades, visits, alerts, or deliveries.
- Amended and mixed-plan filings do not double-count or misclassify purchases.
- Incomplete outcome windows remain pending and cannot enter negative training labels.
- Ongoing visits never use future departure times; missing departures do not become long visits.
- Missing coverage, all-blackout windows, no eligible sellers, and zero-variance baselines produce defined unavailable results rather than inflated signals.
- Signed contributions reconstruct the model logit and zero contributions produce valid output.
- Train/tune/test transformations and label windows respect their temporal boundaries.
- Every report claim resolves to stored evidence available at its score time.

Provider prices, licensing terms, historical coverage, and production deployment have not been validated in this review. No live ingestion, trained model, or performance claim exists yet in this workspace.
