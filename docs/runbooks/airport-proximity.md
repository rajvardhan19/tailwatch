# Airport identities and office proximity

C12 adds typed airport histories, strict local CSV normalization, evidence-backed history imports, and reproducible office-proximity snapshots. No live airport feed or automatic geocoder is connected.

## Identity and dates

`parse_ourairports` reads a local UTF-8 CSV with explicit observation time. It uses the provider's numeric ID to derive a deterministic UUID; code changes do not create a new airport. The [OurAirports data dictionary](https://ourairports.com/help/data-dictionary.html) describes the source fields. Ident, ICAO, IATA, GPS and local codes remain separate namespaces. Blank IATA codes are valid. Local-code resolution can be scoped by country; unresolved collisions raise an error. Closed facilities remain identifiable but are excluded from proximity calculations.

Malformed rows, missing required columns and duplicate IDs reject the entire CSV import. Do not quietly skip records and then claim a code uniquely identifies an airport. This parser requires the current `icao_code` column; older schemas require an explicit adapter, not inference from `gps_code`.

An observed CSV creates periods starting at its supplied observation time. It does not prove older airport coordinates or codes. Retain the original CSV separately in the evidence store with its archive metadata before producing reviewed history documents. Automatic archive acquisition, CSV-to-history reconciliation and source licensing/coverage validation remain pending. In particular, a missing row must not silently carry an old airport forward: reviewed histories need explicit closure or interval termination.

`AirportHistory` uses complete, ordered, nonoverlapping periods. Import its exact JSON through `append_airport` with the corresponding raw SHA-256, key `airport:<UUID>`, parser `airport-history-v1`, locator `document`, first-period validity start and no upper fact-envelope bound. Publish through the existing evidence repository. Corrections supersede the complete history under the same key. `airport_histories` retrieves the version visible at the operational cutoff; pass only these selected revisions to `resolve_alias`.

## Derived proximity

`proximity_snapshot` reads airport and C05 office histories from their configured source IDs at one knowledge cutoff and effective time. It uses [GeographicLib's WGS84 inverse distance](https://geographiclib.sourceforge.io/html/python/examples.html), with explicit latitude/longitude fields, and converts meters to kilometers. The default inclusive search radius is 50 km; negative, zero and nonfinite radii are rejected. Radius membership uses unrounded distances.

Each airport–office link retains both evidence fact IDs, the intersecting validity interval, stable office/company IDs and distance. `company_distances(airport_id)` returns only the minimum office distance per company. Duplicate offices and multiple nearby offices therefore cannot multiply host attribution. The returned map is specific to the snapshot's effective time; do not reuse it for another date or treat a link's interval as proof that the nearest office never changes.

The snapshot includes algorithm version, cutoff, effective time, radius, ordered evidence IDs and links, with a canonical digest for downstream run manifests. Changed coordinates or source revisions change the snapshot; later corrections cannot alter a replay at an earlier cutoff. Persist the returned snapshot and digest when freezing a research run.

`missing_source` distinguishes absent airport/office inputs from a valid geometric result with no nearby offices. `inputs_present` does not certify full provider coverage or coordinate accuracy. Production-scale spatial indexing, geocoding accuracy review, the approved company universe and flight/visit interpretation remain later-stage responsibilities. Proximity alone is not evidence of a meeting.
