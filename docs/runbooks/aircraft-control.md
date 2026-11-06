# Reviewed company–aircraft links

C11 provides Python contracts for candidate generation, evidence review, immutable persistence, and operational resolution. It does not automatically approve links or enable flight tracking. Fixtures are synthetic; attribution precision has not been established.

## Candidate generation

`candidates` accepts a dated registry archive and row, dated company names, and dated evidence observations. Callers supply the knowledge cutoff, effective time, and positive registry freshness bound. Use the latest applicable archive from C10, not an older convenient match. Future company names, future registry observations, expired evidence, shared locations and intermediary evidence cannot support a candidate. Matching names and nonshared home-base observations only produce `possible` results. Multiple candidate companies remain visible; no ranking is presented as a probability.

Home-base inputs are reviewed observations with source digest, quote, locator and explicit validity/availability dates. They are not inferred from an airport's proximity to headquarters. C13-derived home-base coverage and real observations remain pending. Mark trustees, registered agents and shared premises as intermediary/shared; the application cannot infer every intermediary from a name. The registry parser does not currently import mailing addresses: address evidence must be supplied separately with retained source text.

## Review contract

`ControlReview` separates the roles `registrant`, `operator` and `controller`. Approval of one does not imply another. A stable airframe UUID is assigned by the reviewer after checking the provisional manufacturer/model/serial key; tail and ICAO aliases alone are insufficient. Reviews bind the exact archive hash and row, company UUID, role, effective interval, reviewer, rationale and alternative company IDs. Decisions are approved, rejected or possible.

Approval requires explicit privacy clearance and a direct document for the specified role covering the entire interval. Names, addresses and home-base observations cannot satisfy that requirement. Withheld or unresolved privacy status cannot be approved. Reviewers must check document semantics, identity continuity, source provenance, alternative explanations, and whether the company actually controls the aircraft. The software's quote-presence check does not establish those facts.

`append_review` uses a caller-owned `EvidenceRepository` transaction. Retain the registry bytes first. Pass the typed review as the fact payload and its exact `model_dump_json().encode()` SHA-256 as the fact digest. The key is `aircraft-link:<airframe UUID>:<company UUID>:<role>`, parser version `aircraft-control-v1`, locator `review`; the fact validity interval matches the review interval and `approved_at` records the review timestamp for every decision. Supply retained UTF-8 evidence documents keyed by their hashes. Company and alternative IDs must already be registered. PDF evidence must first have a separately provenance-tracked text extraction; this importer does not parse PDFs.

Publish the snapshot after appending. Reconsideration uses the evidence revision chain on the same airframe/company/role key; do not create a new airframe ID to bypass a rejection. Exact retries are idempotent. Corrected intervals supersede earlier intervals rather than reviving old decisions outside the correction.

## Resolution and remaining validation

`known_reviews` reads published evidence as of a cutoff and carries database publication time with each decision. `resolve_control` chooses the latest known revision per company before checking effective dates. Conflicting approved companies return ambiguous. Unknown, future, rejected, expired, stale-registry and changed-registry cases cannot yield an approved controller. A changed registry hash requires review again; this intentionally conservative first version has no automatic carry-forward. Always use the authoritative newest C10 archive and the complete known review set for the selected source.

No real attribution evaluation has run. Before enabling tracking, assemble a stratified, independently labeled sample that includes approved, rejected, possible, low-volume, intermediary and ambiguous candidates. Freeze the sample and reviewer decisions; report sample counts, controller-attribution precision, unresolved share, and error breakdown by stratum. Record uncertainty and sampling limitations. Do not substitute passing synthetic tests for this gate. Operational consumers must additionally enforce the approved public-company universe, source rights and flight-identity/coverage rules from later stages.
