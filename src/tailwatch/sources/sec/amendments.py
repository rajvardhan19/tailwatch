"""Conservative partial-amendment reconciliation with explicit row mappings."""

from datetime import date

from pydantic import AwareDatetime

from tailwatch.sources.policy import Contract, NonEmpty
from tailwatch.sources.sec.form4 import ParsedForm4, TradeReview, Transaction, eligible_buy


class AmendmentResolution(Contract):
    amendment_accession: NonEmpty
    amendment_digest: NonEmpty
    known_at: AwareDatetime
    reviewer: NonEmpty
    evidence_locator: NonEmpty
    replacements: dict[str, str]  # amendment row ID -> original row ID


def active_transactions(
    filings: list[ParsedForm4],
    *,
    as_of: AwareDatetime,
    resolutions: tuple[AmendmentResolution, ...] = (),
) -> list[tuple[ParsedForm4, Transaction]]:
    visible = [filing for filing in filings if filing.available_at <= as_of]
    unique: dict[str, ParsedForm4] = {}
    for filing in visible:
        prior = unique.get(filing.accession)
        if prior and prior.source_digest != filing.source_digest:
            raise ValueError("conflicting source versions require explicit review")
        if prior and prior.parser_version != filing.parser_version:
            raise ValueError("choose one parser version before reconciliation")
        if prior is None or filing.available_at < prior.available_at:
            unique[filing.accession] = filing
    active: dict[str, tuple[ParsedForm4, Transaction]] = {}
    # Discovery order cannot resurrect a base filing first seen after its amendment.
    for filing in sorted(
        unique.values(), key=lambda f: (f.form == "4/A", f.available_at, f.accession)
    ):
        if filing.form == "4":
            active.update((row.row_id, (filing, row)) for row in filing.transactions)
            continue
        candidates = {
            key: value
            for key, value in active.items()
            if value[0].issuer_cik == filing.issuer_cik
            and ({o.cik for o in value[0].owners} & {o.cik for o in filing.owners})
            and (
                filing.original_filed_date is None
                or value[0].filed_date == filing.original_filed_date
            )
        }
        matches = [
            r
            for r in resolutions
            if r.amendment_accession == filing.accession
            and r.amendment_digest == filing.source_digest
            and r.known_at <= as_of
        ]
        if len(matches) > 1:
            raise ValueError("conflicting amendment resolutions")
        if not matches:
            # Unknown partial correction: neither original candidates nor amendment count.
            for key in candidates:
                active.pop(key)
            continue
        resolution = matches[0]
        row_ids = {row.row_id for row in filing.transactions}
        if (
            set(resolution.replacements) != row_ids
            or len(set(resolution.replacements.values())) != len(row_ids)
            or not set(resolution.replacements.values()).issubset(candidates)
        ):
            raise ValueError(
                "amendment requires a complete one-to-one reviewed replacement mapping"
            )
        for row in filing.transactions:
            active.pop(resolution.replacements[row.row_id])
            active[row.row_id] = (filing, row)
    return [active[key] for key in sorted(active)]


class PurchaseEpisode(Contract):
    issuer_cik: str
    person_cik: str
    trade_date: date
    row_ids: tuple[str, ...]
    economic_event_ids: tuple[str, ...]


def purchase_episodes(
    filings: list[ParsedForm4],
    *,
    as_of: AwareDatetime,
    reviews: tuple[TradeReview, ...],
    resolutions: tuple[AmendmentResolution, ...] = (),
) -> list[PurchaseEpisode]:
    """One count per reviewed natural person/issuer/day, not per execution or co-filer."""
    known: dict[tuple[str, str], TradeReview] = {}
    for review in reviews:
        if review.known_at > as_of:
            continue
        key = (review.row_id, review.source_digest)
        previous = known.get(key)
        if previous and previous.known_at == review.known_at and previous != review:
            raise ValueError("conflicting same-time reviews")
        if previous is None or previous.known_at < review.known_at:
            known[key] = review
    groups: dict[tuple[str, str, date], list[TradeReview]] = {}
    for filing, transaction in active_transactions(filings, as_of=as_of, resolutions=resolutions):
        selected_review = known.get((transaction.row_id, filing.source_digest))
        if selected_review and eligible_buy(filing, transaction, selected_review, as_of):
            groups.setdefault(
                (filing.issuer_cik, selected_review.natural_person_cik, transaction.trade_date), []
            ).append(selected_review)
    return [
        PurchaseEpisode(
            issuer_cik=key[0],
            person_cik=key[1],
            trade_date=key[2],
            row_ids=tuple(sorted(r.row_id for r in group)),
            economic_event_ids=tuple(sorted({r.economic_event_id for r in group})),
        )
        for key, group in sorted(groups.items())
    ]
