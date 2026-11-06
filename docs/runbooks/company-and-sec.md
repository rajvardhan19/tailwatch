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
