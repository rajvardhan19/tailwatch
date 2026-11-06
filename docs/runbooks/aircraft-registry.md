# Aircraft registry observations

C10 adds offline FAA MASTER CSV inspection and immutable archive storage. No registry downloader or active aircraft tracking is enabled. All committed examples are synthetic.

```sh
uv run --locked tailwatch inspect-registry tests/fixtures/registry/MASTER.txt --observed-at 2026-01-01T00:00:00Z
```

The CLI prints reconciliation counts and a raw-content hash; it does not print registrant names. A candidate for control review is not an approved company-aircraft link.

## Archive contract

`parse_master` requires an explicit observation timestamp and availability timestamp. The observation must come from a trustworthy archive manifest; do not substitute certificate issuance, last activity, or an assumed annual date. The parser cannot authenticate the archive date itself.

The [FAA file specification](https://www.faa.gov/licenses_certificates/aircraft_certification/aircraft_registry/media/ardata.pdf) defines the supported columns and registrant codes. A present but blank fractional flag means nonfractional; an absent column rejects the file. Unknown flags, missing names, missing airframe identity, nonvalid registrations, individual registrants and fractional ownership cannot pass candidate screening. Missing names and common redaction placeholders stay unknown; missing data does not prove why the FAA withheld a record. Do not reconstruct withheld identity from other sources.

CSV schema failures reject the archive. Malformed rows and duplicate tails, ICAO addresses or provisional airframe keys are quarantined with row locators. All implicated duplicates are excluded. An archive with quarantined rows can be published as an observation with explicit counts; its missing records never establish absence of aircraft activity.

`append_registry` validates raw hash, parser version, row counts, timestamp and logical key before storing through `EvidenceRepository`. Use a caller-owned transaction, one manifest fact per archive, and publish only after that append succeeds. Manifest payload has `kind=faa_registry`, `observed_at` (ISO timestamp), `row_count`, `accepted_count`, and `quarantined_count`; the fact key is `registry:<observed_at ISO>`, locator `MASTER.txt`, and `valid_from` the observation timestamp. Raw bytes are retained in the content-addressed store. Exact retries are safe; corrections use the existing evidence revision chain. Observations of different dates have distinct keys.

`known_archives` exposes only published facts visible at the operational cutoff, carrying the database publication time into subsequent lookup. It reparses retained bytes with the recorded parser version. This initial API loads archives into memory; a bounded indexed projection will be needed for production-scale repeated queries.

## Historical interpretation

`lookup` requires a source, typed tail/ICAO alias, knowledge cutoff, effective time, and explicit maximum age. It never uses a future observation or fills a missing record from an older archive. Equal-time conflicting archives are ambiguous. Sparse yearly snapshots cannot establish daily ownership: the caller's freshness bound expires them, and even a fresh result means only registration observed at that snapshot.

Tail numbers and transponder addresses are aliases, not permanent airframe identities. Manufacturer/model plus serial is only a provisional candidate key; reference resolution and human review are still needed before linking flight feeds. This stage does not infer controller, operator, passengers, home base, or ownership duration. C11 supplies reviewed control attribution. Real registry coverage, privacy handling, licensing review and historical archive validation remain G0/G1 dependencies.
