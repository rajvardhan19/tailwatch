# Company histories and SEC pipeline

## Reference histories (C05)

`uv run --locked tailwatch validate-history tests/fixtures/entities/company.json` validates a synthetic history. JSON history imports retain every effective interval; an undated current-company CSV is deliberately not treated as historical truth. Models cover companies, securities, offices, person/issuer role histories, and corporate relationships. Register company/person identities before dependent histories. Each correction is a new evidence `Fact` containing a full history, bound through `EntityRepository.append` before snapshot publication. Periods are sorted, half-open, and non-overlapping.

Ticker lookup requires exchange, effective date, and knowledge cutoff and rejects multiple matches. `EntityRepository.universe` exports canonical bytes and a SHA-256 digest for a sector/date, including all supporting fact IDs. Persist those bytes in the object store when registering an experiment. A later delisting does not remove the company from its earlier eligible universe. Currency format is validated; provider-specific currency/security verification remains part of source onboarding.

## SEC discovery and capture (C06)

The adapter implements a **single-worker** daily/quarterly master-index workflow, rather than assuming a single submissions API response contains all filings. Source references: [SEC developer resources](https://www.sec.gov/about/developer-resources) and [submissions API scope](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).

Configure a source with `sec` capability, valid research permissions, and request limits. Inject `HTTPTransport`, a declared organization/contact User-Agent, a content-addressed store, and `SECJournal.record_fetch` into `SECClient`. Live discovery requires an explicit set of ten-digit indexed CIKs. No source or contact is enabled by default; tests use a fake transport and synthetic SEC headers.

`SECJournal.discover(run_id, index_url, client, forms, ciks)` freezes the selected accession worklist and raw index hash. `resume` commits each filing independently and skips persisted successes. Reuse the run ID to resume; create a new run ID to recheck an updated daily/quarterly index for late additions. A changed form/CIK selection cannot silently reuse a run. This worklist is the cursor; every selected accession remains missing, quarantined, or downloaded until reconciled. The index's associated CIK is not automatically the filing issuer or insider identity; C07 reads those from the XML.

All clients in one process share an eight-requests-per-second limiter, including retries. The SEC limit is aggregate across machines, so deploy only one SEC worker until a distributed limiter is introduced. HTTP redirects and non-SEC paths are rejected; responses are size-bounded and declared length checked. 429 and transient server errors retry with bounded backoff and Retry-After; a long Retry-After defers the job, and 403 is never bypassed. Every received response is stored with its hash/status/times; connection failures have no response body and are logged as failed attempts if the retry budget is exhausted.

Downloaded accession/form headers must match the frozen index. Acceptance timestamps are interpreted in Eastern time and retained separately from first observation; they are **not** claimed as exact public-availability timestamps. Unparseable headers are quarantined. Raw-download completeness is not XML parse completeness and cannot enable model alerts. Quarantine history is append-only even when a subsequent attempt succeeds.

Budgets currently apply per client instance; the provider is free and no paid adapter exists. They are not durable daily account limits. Rights/history review, sector selection, and real-source sampling remain outstanding. The tests do not establish SEC coverage or predictive validity.

## Form 4 extraction and reconciliation (C07)

```sh
uv run --locked tailwatch parse-form4 tests/fixtures/sec/form4.xml \
  --accession 0000000001-26-000001 --filed-date 2026-01-02
```

The parser accepts standalone ownership XML or a full filing's XML block, rejects XML entities and broken footnote references, and preserves transaction decimals, table/row identities, owners, role flags, linked footnotes, normalized row XML, original raw hashes, and the filing-level plan checkbox. Raw bytes remain authoritative; normalized XML is not an exact byte copy.

Extraction does **not** assert that code P is an open-market common-stock purchase, that unchecked plan information means non-plan, or that a named owner is a natural person. `TradeReview` explicitly binds classification to a raw digest, transaction row, its evidence locator, review time, reviewer, resolved person, and economic event. `purchase_episodes` applies reviewed amendment resolution before counting at most one eligible purchase episode per person/issuer/day. Joint owners and partial executions do not multiply rows or episodes automatically.

Unknown amendments suspend matching original candidates and exclude amendment rows until an explicit one-to-one replacement map is supplied. Partial replacement keeps unaffected original rows. Additions, cancellations, and ambiguous multi-amendment chains require further review policies; they are not guessed. An older operational view never sees a future amendment or review. Conflicting raw/parser versions must be explicitly selected before reconciliation.

`parse_downloaded_run(engine, store, run_id)` bridges the durable SEC worklist to versioned ownership storage. Missing downloads or quarantined XML keep parse completeness false. A later parse retry does not create duplicate transaction rows. Operational reads include database recording time so historical source metadata cannot backdate processing knowledge.

The required stratified comparison of at least 50 real filings has **not** been performed. C07 is fixture-tested implementation, not production-validated SEC parsing. [SEC Form 4 instructions](https://www.sec.gov/files/form4.pdf) and [ownership XML specifications](https://www.sec.gov/submit-filings/technical-specifications) are the source contracts to use for that validation.
