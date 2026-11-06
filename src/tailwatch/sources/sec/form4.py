"""Loss-preserving Form 4 extraction; classification requires explicit review."""

import hashlib
import re
from datetime import date
from decimal import Decimal
from typing import Literal
from xml.etree.ElementTree import Element, ParseError

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring
from pydantic import AwareDatetime, Field

from tailwatch.sources.policy import Contract, NonEmpty

PARSER_VERSION = "form4-v1"


class Owner(Contract):
    cik: str
    name: str
    is_director: bool | None
    is_officer: bool | None
    is_ten_percent_owner: bool | None
    title: str | None


class Transaction(Contract):
    row_id: str
    table: Literal["non_derivative", "derivative"]
    row_number: int
    security_title: str
    trade_date: date
    code: str
    direction: Literal["A", "D"]
    shares: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    price: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    ownership: str | None
    footnote_ids: tuple[str, ...]
    locator: str
    raw_xml: str


class ParsedForm4(Contract):
    accession: str = Field(pattern=r"^\d{10}-\d{2}-\d{6}$")
    source_digest: str
    parser_version: str = PARSER_VERSION
    form: Literal["4", "4/A"]
    filed_date: date
    available_at: AwareDatetime
    original_filed_date: date | None
    issuer_cik: str
    issuer_name: str
    owners: tuple[Owner, ...]
    plan_checkbox: bool | None
    remarks: str | None
    footnotes: dict[str, str]
    transactions: tuple[Transaction, ...]


def text_of(element: Element, path: str, *, required: bool = False) -> str | None:
    node = element.find(path.lower())
    value = "".join(node.itertext()).strip() if node is not None else ""
    if not value and required:
        raise ValueError(f"missing required XML field: {path}")
    return value or None


def flag(value: str | None) -> bool | None:
    if value is None:
        return None
    if value.lower() in {"1", "true"}:
        return True
    if value.lower() in {"0", "false"}:
        return False
    raise ValueError("invalid XML boolean")


def cik(value: str | None) -> str:
    if value is None or not re.fullmatch(r"\d{1,10}", value):
        raise ValueError("invalid CIK")
    return value.zfill(10)


def ownership_root(body: bytes) -> Element:
    if len(body) > 25 * 1024 * 1024:
        raise ValueError("filing too large")
    blocks = re.findall(rb"<XML>\s*(.*?)\s*</XML>", body, flags=re.DOTALL | re.IGNORECASE)
    candidates = []
    for block in blocks or [body]:
        try:
            root = fromstring(block)
        except (DefusedXmlException, ParseError) as error:
            raise ValueError("unsafe or malformed ownership XML") from error
        if root.tag.split("}")[-1].lower() == "ownershipdocument":
            candidates.append(root)
    if len(candidates) != 1:
        raise ValueError("expected exactly one ownership document")
    root = candidates[0]
    for element in root.iter():
        element.tag = element.tag.split("}")[-1].lower()
    return root


def parse_form4(
    body: bytes, *, accession: str, filed_date: date, available_at: AwareDatetime
) -> ParsedForm4:
    from xml.etree.ElementTree import tostring

    root = ownership_root(body)
    form = text_of(root, "documentType", required=True)
    if form not in {"4", "4/A"}:
        raise ValueError("not a Form 4 filing")
    notes: dict[str, str] = {}
    for note in root.findall("footnotes/footnote"):
        identity = note.get("id")
        if not identity or identity in notes:
            raise ValueError("missing or duplicate footnote identity")
        notes[identity] = "".join(note.itertext()).strip()
    owners = []
    for owner in root.findall("reportingowner"):
        owners.append(
            Owner(
                cik=cik(text_of(owner, "reportingOwnerId/rptOwnerCik")),
                name=text_of(owner, "reportingOwnerId/rptOwnerName", required=True) or "",
                is_director=flag(text_of(owner, "reportingOwnerRelationship/isDirector")),
                is_officer=flag(text_of(owner, "reportingOwnerRelationship/isOfficer")),
                is_ten_percent_owner=flag(
                    text_of(owner, "reportingOwnerRelationship/isTenPercentOwner")
                ),
                title=text_of(owner, "reportingOwnerRelationship/officerTitle"),
            )
        )
    if not owners or len({owner.cik for owner in owners}) != len(owners):
        raise ValueError("missing or duplicate reporting owners")
    rows = []
    for table, xml_table, xml_row in (
        ("non_derivative", "nonderivativetable", "nonderivativetransaction"),
        ("derivative", "derivativetable", "derivativetransaction"),
    ):
        for index, node in enumerate(root.findall(f"{xml_table}/{xml_row}"), start=1):
            references = tuple(sorted({ref.get("id", "") for ref in node.iter("footnoteid")}))
            if not set(references).issubset(notes):
                raise ValueError("transaction references missing footnote")
            raw_shares = text_of(node, "transactionAmounts/transactionShares/value")
            raw_price = text_of(node, "transactionAmounts/transactionPricePerShare/value")
            rows.append(
                Transaction.model_validate(
                    {
                        "row_id": f"{accession}:{table}:{index}",
                        "table": table,
                        "row_number": index,
                        "security_title": text_of(node, "securityTitle/value", required=True),
                        "trade_date": text_of(node, "transactionDate/value", required=True),
                        "code": text_of(node, "transactionCoding/transactionCode", required=True),
                        "direction": text_of(
                            node,
                            "transactionAmounts/transactionAcquiredDisposedCode/value",
                            required=True,
                        ),
                        "shares": raw_shares,
                        "price": raw_price,
                        "ownership": text_of(
                            node, "ownershipNature/directOrIndirectOwnership/value"
                        ),
                        "footnote_ids": references,
                        "locator": f"/{xml_table}/{xml_row}[{index}]",
                        "raw_xml": tostring(node, encoding="unicode"),
                    }
                )
            )
    return ParsedForm4.model_validate(
        {
            "accession": accession,
            "source_digest": hashlib.sha256(body).hexdigest(),
            "form": form,
            "filed_date": filed_date,
            "available_at": available_at,
            "original_filed_date": text_of(root, "dateOfOriginalSubmission"),
            "issuer_cik": cik(text_of(root, "issuer/issuerCik")),
            "issuer_name": text_of(root, "issuer/issuerName", required=True),
            "owners": owners,
            "plan_checkbox": flag(text_of(root, "aff10b5One")),
            "remarks": text_of(root, "remarks"),
            "footnotes": notes,
            "transactions": rows,
        }
    )


class TradeReview(Contract):
    row_id: NonEmpty
    source_digest: NonEmpty
    known_at: AwareDatetime
    reviewer: NonEmpty
    evidence_locator: NonEmpty
    venue: Literal["open_market", "private", "unknown"]
    security_class: Literal["common_equity", "other", "unknown"]
    plan: Literal["plan", "non_plan", "unknown"]
    natural_person_cik: str = Field(pattern=r"^\d{10}$")
    economic_event_id: NonEmpty


def eligible_buy(
    filing: ParsedForm4, transaction: Transaction, review: TradeReview | None, as_of: AwareDatetime
) -> bool:
    """Extraction alone never upgrades code P or an unchecked plan box into a signal."""
    if review is None or filing.available_at > as_of or review.known_at > as_of:
        return False
    if review.known_at < filing.available_at:
        return False
    if (
        review.row_id != transaction.row_id
        or review.source_digest != filing.source_digest
        or transaction not in filing.transactions
    ):
        raise ValueError("review does not match this source transaction")
    if review.evidence_locator not in {
        transaction.locator,
        *(f"footnote:{key}" for key in transaction.footnote_ids),
    }:
        raise ValueError("review evidence must reference this row or its linked footnotes")
    return (
        transaction.table == "non_derivative"
        and transaction.code == "P"
        and transaction.direction == "A"
        and transaction.shares is not None
        and transaction.shares > 0
        and review.venue == "open_market"
        and review.security_class == "common_equity"
        and review.plan == "non_plan"
        and review.natural_person_cik in {owner.cik for owner in filing.owners}
    )
