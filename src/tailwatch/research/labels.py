from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from tailwatch.domain.time import utc
from tailwatch.sources.policy import Contract, NonEmpty


class Announcement(Contract):
    published_at: AwareDatetime
    known_at: AwareDatetime
    evidence: NonEmpty
    definitive: bool

    @model_validator(mode="after")
    def chronology(self) -> "Announcement":
        if self.known_at < self.published_at:
            raise ValueError("announcement known before publication")
        return self


class Deal(Contract):
    deal_id: UUID
    acquirer: UUID
    target: UUID
    taxonomy: Literal["control", "minority", "asset", "rumor", "equals", "consortium"]
    announcements: tuple[Announcement, ...] = Field(min_length=1)
    withdrawn_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def distinct(self) -> "Deal":
        if self.acquirer == self.target:
            raise ValueError("self deal")
        return self


class Label(Contract):
    state: Literal["positive", "negative", "pending", "excluded"]
    deal_id: UUID | None = None
    announcement_at: AwareDatetime | None = None
    known_at: AwareDatetime | None = None


def label_pair(
    acquirer: UUID,
    target: UUID,
    at: datetime,
    *,
    as_of: datetime,
    covered_until: datetime,
    deals: list[Deal],
    coverage_complete: bool,
) -> Label:
    at, as_of, covered_until = utc(at), utc(as_of), utc(covered_until)
    end = at + timedelta(days=180)
    if acquirer == target:
        return Label(state="excluded")
    visible = []
    for deal in deals:
        if deal.taxonomy != "control" or {deal.acquirer, deal.target} != {acquirer, target}:
            continue
        known = [a for a in deal.announcements if a.definitive and a.known_at <= as_of]
        if known:
            first = min(known, key=lambda a: a.published_at)
            if first.published_at <= at:
                return Label(state="excluded")
            visible.append((deal, first))
    # Positives must mature too; otherwise label availability selects the sample.
    if not coverage_complete or min(as_of, covered_until) < end:
        return Label(state="pending")
    positives = [
        (d, a)
        for d, a in visible
        if d.acquirer == acquirer and d.target == target and at < a.published_at <= end
    ]
    if positives:
        deal, announcement = min(positives, key=lambda item: item[1].published_at)
        return Label(
            state="positive",
            deal_id=deal.deal_id,
            announcement_at=announcement.published_at,
            known_at=max(end, announcement.known_at),
        )
    return Label(state="negative", known_at=end)
