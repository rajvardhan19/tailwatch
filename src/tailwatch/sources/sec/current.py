"""EDGAR's latest Form 4 filings: grouped by accession, with optional parsed details.

Transaction codes are shown as filed. They are not reviewed classifications and do not
establish a discretionary open-market purchase (see TradeReview / eligible_buy).
"""

import json
import re
from typing import Any, Literal

from defusedxml import ElementTree
from pydantic import AwareDatetime, Field

from tailwatch.sources.policy import Contract
from tailwatch.sources.sec.client import SECClient
from tailwatch.sources.sec.form4 import ParsedForm4, parse_form4

ATOM = "{http://www.w3.org/2005/Atom}"
TITLE = re.compile(
    r"^(?P<form>4(?:/A)?) - (?P<name>.+?) \((?P<cik>\d{10})\) \((?P<role>[A-Za-z ]+)\)$"
)
ACCESSION = re.compile(r"accession-number=(\d{10}-\d{2}-\d{6})$")
INDEX_LINK = re.compile(
    r"^https://www\.sec\.gov/Archives/edgar/data/\d{1,10}/\d{18}/[\w.-]+-index\.html?$"
)
XML_NAME = re.compile(r"^[A-Za-z0-9][\w.-]{0,120}\.xml$")
CODES = {
    "P": "open-market or private purchase",
    "S": "open-market or private sale",
    "A": "grant or award",
    "M": "derivative exercise or conversion",
    "X": "in-the-money derivative exercise",
    "F": "tax or exercise-price withholding",
    "G": "gift",
    "D": "disposition to the issuer",
    "C": "conversion",
    "J": "other acquisition or disposition",
}


class CurrentFiling(Contract):
    accession: str = Field(pattern=r"^\d{10}-\d{2}-\d{6}$")
    form: Literal["4", "4/A"]
    issuer_name: str | None
    issuer_cik: str | None
    reporting: tuple[str, ...]
    filed_at: AwareDatetime
    link: str = Field(pattern=INDEX_LINK.pattern)


def parse_current(body: bytes) -> list[CurrentFiling]:
    """Group the feed's per-entity entries (issuer + reporting owners) by accession."""
    root = ElementTree.fromstring(body)
    groups: dict[str, dict[str, Any]] = {}
    for entry in root.findall(f"{ATOM}entry"):
        title = TITLE.match((entry.findtext(f"{ATOM}title") or "").strip())
        identity = ACCESSION.search((entry.findtext(f"{ATOM}id") or "").strip())
        link = entry.find(f"{ATOM}link")
        href = link.get("href", "") if link is not None else ""
        updated = (entry.findtext(f"{ATOM}updated") or "").strip()
        if not title or not identity or not INDEX_LINK.match(href) or not updated:
            continue  # Malformed entries are skipped, never guessed.
        group = groups.setdefault(
            identity.group(1),
            {"form": title["form"], "issuer": None, "reporting": [], "updated": updated},
        )
        group.setdefault("link", href)
        if title["role"].strip().lower() == "issuer":
            group["issuer"] = (title["name"], title["cik"])
            group["link"] = href
        elif title["name"] not in group["reporting"]:
            group["reporting"].append(title["name"])
    filings = [
        CurrentFiling(
            accession=accession,
            form=group["form"],
            issuer_name=group["issuer"][0] if group["issuer"] else None,
            issuer_cik=group["issuer"][1] if group["issuer"] else None,
            reporting=tuple(group["reporting"]),
            filed_at=group["updated"],
            link=group["link"],
        )
        for accession, group in groups.items()
    ]
    return sorted(filings, key=lambda f: (f.filed_at, f.accession), reverse=True)


def ownership_document(index_body: bytes) -> str:
    """Pick the single ownership XML from an EDGAR filing directory listing."""
    listing = json.loads(index_body)
    items = listing.get("directory", {}).get("item", []) if isinstance(listing, dict) else []
    names = [
        item.get("name")
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and XML_NAME.match(item["name"])
        and item["name"] != "FilingSummary.xml"
    ]
    if len(names) != 1:
        raise ValueError("expected exactly one ownership XML document")
    return str(names[0])


def summarize(parsed: ParsedForm4) -> dict[str, Any]:
    rows = [t for t in parsed.transactions if t.table == "non_derivative"] or list(
        parsed.transactions
    )
    codes = [t.code for t in rows]
    headline = "P" if "P" in codes else "S" if "S" in codes else (codes[0] if codes else None)
    return {
        "status": "parsed",
        "issuer_name": parsed.issuer_name,
        "owners": [
            {
                "name": owner.name,
                "roles": [
                    role
                    for role, held in (
                        ("director", owner.is_director),
                        ("officer", owner.is_officer),
                        ("10% owner", owner.is_ten_percent_owner),
                    )
                    if held
                ]
                + ([owner.title] if owner.title else []),
            }
            for owner in parsed.owners
        ],
        "plan_10b5_1": parsed.plan_checkbox,
        "headline_code": headline,
        "headline_label": CODES.get(headline or "", "no transactions reported"),
        "transactions": [
            {
                "code": t.code,
                "label": CODES.get(t.code, "other"),
                "direction": t.direction,
                "shares": str(t.shares) if t.shares is not None else None,
                "price": str(t.price) if t.price is not None else None,
                "trade_date": t.trade_date.isoformat(),
                "table": t.table,
            }
            for t in rows[:6]
        ],
    }


def filing_details(client: SECClient, filing: CurrentFiling) -> dict[str, Any]:
    """Two budgeted, journaled requests: the directory listing, then the ownership XML."""
    folder = filing.link.rsplit("/", 1)[0]
    listing, _ = client.get(f"{folder}/index.json")
    body, fetch = client.get(f"{folder}/{ownership_document(listing)}")
    parsed = parse_form4(
        body,
        accession=filing.accession,
        filed_date=filing.filed_at.date(),
        available_at=fetch.received_at,
    )
    return summarize(parsed)
