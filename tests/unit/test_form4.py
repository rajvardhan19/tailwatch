from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tailwatch.sources.sec.amendments import (
    AmendmentResolution,
    active_transactions,
    purchase_episodes,
)
from tailwatch.sources.sec.form4 import TradeReview, eligible_buy, parse_form4

RAW = Path("tests/fixtures/sec/form4.xml").read_bytes()
ORIGINAL = "0000000001-26-000001"
AMENDED = "0000000001-26-000002"
NOW = datetime(2026, 1, 2, tzinfo=UTC)
LATER = datetime(2026, 1, 4, tzinfo=UTC)


def parse(raw=RAW, accession=ORIGINAL, available=NOW):
    return parse_form4(
        raw, accession=accession, filed_date=available.date(), available_at=available
    )


def review(filing):
    return TradeReview(
        row_id=filing.transactions[0].row_id,
        source_digest=filing.source_digest,
        known_at=NOW,
        reviewer="fixture-reviewer",
        evidence_locator="footnote:F1",
        venue="open_market",
        security_class="common_equity",
        plan="non_plan",
        natural_person_cik="0000000002",
        economic_event_id="fixture-purchase-1",
    )


def test_preserves_decimal_roles_evidence_without_inventing_buy_signal():
    filing = parse()
    row = filing.transactions[0]
    assert row.price == Decimal("12.50") and row.shares == Decimal("100")
    assert row.footnote_ids == ("F1",)
    assert filing.owners[0].is_director and filing.owners[0].is_officer
    assert filing.plan_checkbox is False
    assert not eligible_buy(filing, row, None, NOW)
    assert eligible_buy(filing, row, review(filing), NOW)
    assert not eligible_buy(filing, row, review(filing), datetime(2026, 1, 1, tzinfo=UTC))


@pytest.mark.parametrize(
    "field,value",
    [
        ("venue", "private"),
        ("venue", "unknown"),
        ("plan", "plan"),
        ("plan", "unknown"),
        ("security_class", "other"),
    ],
)
def test_private_derivative_or_unknown_classification_never_counts(field, value):
    filing = parse()
    reviewed = TradeReview.model_validate(review(filing).model_dump() | {field: value})
    assert not eligible_buy(filing, filing.transactions[0], reviewed, NOW)


def test_missing_checkbox_is_unknown_and_derivative_table_remains_distinct():
    filing = parse(
        RAW.replace(b"<aff10b5One>0</aff10b5One>", b"").replace(b"nonDerivative", b"derivative")
    )
    assert filing.plan_checkbox is None
    assert filing.transactions[0].table == "derivative"
    assert not eligible_buy(filing, filing.transactions[0], review(filing), NOW)


def test_joint_owners_do_not_duplicate_transaction_rows():
    owner = RAW.split(b"<reportingOwner>")[1].split(b"</reportingOwner>")[0]
    owner = owner.replace(b"<rptOwnerCik>2", b"<rptOwnerCik>3")
    filing = parse(
        RAW.replace(
            b"<nonDerivativeTable>",
            b"<reportingOwner>" + owner + b"</reportingOwner><nonDerivativeTable>",
        )
    )
    assert len(filing.owners) == 2 and len(filing.transactions) == 1
    assert len(active_transactions([filing, filing], as_of=NOW)) == 1


def test_amendment_is_pending_until_review_and_does_not_rewrite_past():
    original = parse()
    changed = RAW.replace(
        b"<documentType>4</documentType>",
        b"<documentType>4/A</documentType><dateOfOriginalSubmission>2026-01-02</dateOfOriginalSubmission>",
    )
    amended = parse(changed.replace(b"<value>100</value>", b"<value>50</value>"), AMENDED, LATER)
    assert active_transactions([original, amended], as_of=NOW) == [
        (original, original.transactions[0])
    ]
    assert active_transactions([original, amended], as_of=LATER) == []
    resolved = AmendmentResolution(
        amendment_accession=AMENDED,
        amendment_digest=amended.source_digest,
        known_at=LATER,
        reviewer="fixture-reviewer",
        evidence_locator="amendment-review:1",
        replacements={amended.transactions[0].row_id: original.transactions[0].row_id},
    )
    assert active_transactions([original, amended], as_of=LATER, resolutions=(resolved,)) == [
        (amended, amended.transactions[0])
    ]


def test_xml_entities_and_broken_references_are_rejected():
    with pytest.raises(ValueError, match="unsafe"):
        parse(
            b'<!DOCTYPE ownershipDocument [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
            b"<ownershipDocument>&x;</ownershipDocument>"
        )
    with pytest.raises(ValueError, match="missing footnote"):
        parse(RAW.replace(b'footnoteId id="F1"', b'footnoteId id="missing"'))
    with pytest.raises(ValueError):
        parse(RAW.replace(b"<value>12.50</value>", b"<value>NaN</value>"))


def test_sgml_wrapper_is_supported():
    parsed = parse(b"<SEC-DOCUMENT>\n<DOCUMENT><XML>" + RAW + b"</XML></DOCUMENT>")
    assert parsed.filed_date == date(2026, 1, 2)


def test_partial_executions_are_one_reviewed_person_day_episode():
    row = RAW.split(b"<nonDerivativeTransaction>")[1].split(b"</nonDerivativeTransaction>")[0]
    filing = parse(
        RAW.replace(
            b"</nonDerivativeTable>",
            b"<nonDerivativeTransaction>"
            + row
            + b"</nonDerivativeTransaction></nonDerivativeTable>",
        )
    )
    first = review(filing)
    second = TradeReview.model_validate(
        first.model_dump() | {"row_id": filing.transactions[1].row_id}
    )
    assert len(filing.transactions) == 2
    assert len(purchase_episodes([filing, filing], as_of=NOW, reviews=(first, second))) == 1
    assert purchase_episodes([filing], as_of=NOW, reviews=()) == []


def test_amendment_first_discovery_cannot_resurrect_late_original():
    original = parse(available=LATER)
    amended = parse(
        RAW.replace(b"<documentType>4</documentType>", b"<documentType>4/A</documentType>"),
        AMENDED,
        NOW,
    )
    assert active_transactions([amended, original], as_of=LATER) == []
