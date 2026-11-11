"""Three explainable signal categories: flight-triggered, insider cluster buying, and both.

Each category fires on an explicit, registered rule instead of a tuned score threshold,
so every signal says why it fired. Rules use only filings public by the evaluation date.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from pydantic import AwareDatetime, Field

from tailwatch.sources.policy import Contract, NonEmpty
from tailwatch.sources.sec.form4 import ParsedForm4


class ClusterRule(Contract):
    min_buyers: int = Field(default=3, ge=2)
    window_days: int = Field(default=14, ge=1)
    lookback_days: int = Field(default=90, ge=1)


class FlightRule(Contract):
    description: NonEmpty
    requires: NonEmpty


class CategoryConfig(Contract):
    name: NonEmpty
    registered_at: AwareDatetime
    rationale: NonEmpty
    insider: ClusterRule
    flight: FlightRule
    both_window_days: int = Field(default=30, ge=1)


class BuyerPurchase(Contract):
    """One open-market purchase row; joint filers on one filing form one buyer group."""

    group: NonEmpty
    names: tuple[str, ...]
    roles: tuple[str, ...]
    trade_date: date
    filed: date
    shares: Decimal | None
    price: Decimal | None
    accession: NonEmpty


def purchases(parsed: ParsedForm4, filed: date) -> list[BuyerPurchase]:
    """Non-derivative code P rows as filed; any reporting-owner type counts."""
    roles = sorted(
        {
            role
            for owner in parsed.owners
            for role, held in (
                ("director", owner.is_director),
                ("officer", owner.is_officer),
                ("10% owner", owner.is_ten_percent_owner),
            )
            if held
        }
    )
    group = "+".join(sorted(owner.cik or owner.name for owner in parsed.owners)) or parsed.accession
    return [
        BuyerPurchase(
            group=group,
            names=tuple(owner.name for owner in parsed.owners),
            roles=tuple(roles),
            trade_date=t.trade_date,
            filed=filed,
            shares=t.shares,
            price=t.price,
            accession=parsed.accession,
        )
        for t in parsed.transactions
        if t.table == "non_derivative" and t.code == "P"
    ]


def insider_cluster(rows: list[BuyerPurchase], *, as_of: date, rule: ClusterRule) -> dict[str, Any]:
    """Strongest buying cluster visible on `as_of`; triggered at `rule.min_buyers` groups."""
    visible = [
        p
        for p in rows
        if p.filed <= as_of and as_of - timedelta(days=rule.lookback_days) <= p.trade_date <= as_of
    ]
    best: list[BuyerPurchase] = []
    for start in sorted({p.trade_date for p in visible}):
        window = [
            p for p in visible if start <= p.trade_date < start + timedelta(days=rule.window_days)
        ]
        if len({p.group for p in window}) >= len({p.group for p in best}):
            best = window
    groups = {p.group for p in best}
    triggered = len(groups) >= rule.min_buyers
    public_on = None
    if triggered:
        # The cluster became knowable when the min_buyers-th distinct group had filed.
        seen: dict[str, date] = {}
        for p in sorted(best, key=lambda p: (p.filed, p.accession)):
            seen.setdefault(p.group, p.filed)
        public_on = sorted(seen.values())[rule.min_buyers - 1]
    shares = sum((p.shares or Decimal(0)) for p in best)
    return {
        "triggered": triggered,
        "buyers": len(groups),
        "window_start": min((p.trade_date for p in best), default=None),
        "window_end": max((p.trade_date for p in best), default=None),
        "public_on": public_on,
        "shares": str(shares) if best else None,
        "buyer_names": sorted({", ".join(p.names) for p in best}),
        "accessions": sorted({p.accession for p in best}),
    }


def category(
    flight_on: date | None, insider_on: date | None, *, both_window_days: int
) -> str | None:
    """'both' only when the two triggers became public within the configured window."""
    if flight_on and insider_on and abs((flight_on - insider_on).days) <= both_window_days:
        return "both"
    if flight_on and insider_on:
        return "flight" if flight_on >= insider_on else "insider"  # the more recent trigger
    if flight_on:
        return "flight"
    if insider_on:
        return "insider"
    return None
