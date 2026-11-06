"""No inferred blackout policy or guessed exchange calendar enters observed exposure."""

import hashlib
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy import text

from tailwatch.db.evidence import EvidenceRepository, Fact
from tailwatch.domain.time import utc
from tailwatch.sources.objects import ObjectStore
from tailwatch.sources.policy import Contract, NonEmpty


class ContextEvent(Contract):
    record_type: Literal["context_event"] = "context_event"
    event_id: UUID
    kind: Literal["earnings", "blackout", "conference", "board_meeting"]
    company_id: UUID | None
    start: AwareDatetime
    end: AwareDatetime
    available_at: AwareDatetime
    certainty: Literal["confirmed", "tentative", "proxy"]
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_text: NonEmpty
    timezone: NonEmpty
    venue: str | None = None

    @model_validator(mode="after")
    def coherent(self) -> "ContextEvent":
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("unknown event timezone") from error
        if self.end <= self.start:
            raise ValueError("context event interval must be nonempty")
        if self.kind in {"earnings", "blackout"} and self.company_id is None:
            raise ValueError("earnings/blackout events require a company")
        return self


class Coverage(Contract):
    company_id: UUID
    kind: Literal["earnings", "blackout", "conference", "board_meeting"]
    start: AwareDatetime
    end: AwareDatetime
    available_at: AwareDatetime
    complete: bool
    basis: Literal["confirmed", "proxy", "unknown"]
    evidence_id: NonEmpty

    @model_validator(mode="after")
    def ordered(self) -> "Coverage":
        if self.end <= self.start:
            raise ValueError("coverage interval must be nonempty")
        return self


class Session(Contract):
    session_date: date
    opens_at: AwareDatetime
    closes_at: AwareDatetime

    @model_validator(mode="after")
    def ordered(self) -> "Session":
        if self.closes_at <= self.opens_at:
            raise ValueError("session interval must be nonempty")
        return self


class TradingCalendar(Contract):
    exchange: NonEmpty
    timezone: NonEmpty
    version: NonEmpty
    evidence_id: NonEmpty
    available_at: AwareDatetime
    start: AwareDatetime
    end: AwareDatetime
    sessions: tuple[Session, ...]

    @model_validator(mode="after")
    def coherent(self) -> "TradingCalendar":
        try:
            zone = ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("unknown exchange timezone") from error
        if self.end <= self.start:
            raise ValueError("calendar coverage must be nonempty")
        if len({s.session_date for s in self.sessions}) != len(self.sessions):
            raise ValueError("duplicate session dates")
        previous = None
        for session in self.sessions:
            if session.opens_at.astimezone(zone).date() != session.session_date:
                raise ValueError("session date disagrees with exchange timezone")
            if not self.start <= session.opens_at < session.closes_at <= self.end:
                raise ValueError("session is outside calendar coverage")
            if previous and session.opens_at < previous.closes_at:
                raise ValueError("sessions must be sorted and non-overlapping")
            previous = session
        return self


class Exposure(Contract):
    status: Literal[
        "observed", "research_proxy", "missing_source", "insufficient_history", "ambiguous"
    ]
    eligible_dates: tuple[date, ...] | None
    excluded_dates: tuple[date, ...] | None
    evidence_ids: tuple[str, ...]


def eligible_exposure(
    *,
    company_id: UUID,
    start: datetime,
    end: datetime,
    as_of: datetime,
    calendar: TradingCalendar,
    coverage: Coverage | None,
    events: list[ContextEvent],
    allow_proxy: bool = False,
) -> Exposure:
    start, end, cutoff = utc(start), utc(end), utc(as_of)
    if end <= start:
        raise ValueError("exposure window must be nonempty")
    evidence: tuple[str, ...] = (calendar.evidence_id,)
    if calendar.available_at > cutoff:
        return Exposure(
            status="missing_source", eligible_dates=None, excluded_dates=None, evidence_ids=evidence
        )
    if not calendar.start <= start < end <= calendar.end or end > cutoff:
        return Exposure(
            status="insufficient_history",
            eligible_dates=None,
            excluded_dates=None,
            evidence_ids=evidence,
        )
    if (
        coverage is None
        or coverage.company_id != company_id
        or coverage.kind != "blackout"
        or coverage.available_at > cutoff
        or not coverage.complete
        or not coverage.start <= start < end <= coverage.end
        or coverage.basis == "unknown"
    ):
        return Exposure(
            status="missing_source", eligible_dates=None, excluded_dates=None, evidence_ids=evidence
        )
    evidence += (coverage.evidence_id,)
    proxy = coverage.basis == "proxy"
    if proxy and not allow_proxy:
        return Exposure(
            status="ambiguous", eligible_dates=None, excluded_dates=None, evidence_ids=evidence
        )
    relevant = [
        event
        for event in events
        if event.company_id == company_id
        and event.kind == "blackout"
        and event.available_at <= cutoff
        and event.start < end
        and start < event.end
    ]
    if any(
        e.certainty == "tentative" or (e.certainty == "proxy" and not allow_proxy) for e in relevant
    ):
        return Exposure(
            status="ambiguous", eligible_dates=None, excluded_dates=None, evidence_ids=evidence
        )
    proxy = proxy or any(event.certainty == "proxy" for event in relevant)
    evidence += tuple(sorted({event.source_digest for event in relevant}))
    eligible, excluded = [], []
    for session in calendar.sessions:
        if not start <= session.opens_at < session.closes_at <= end:
            continue
        if any(
            event.start < session.closes_at and session.opens_at < event.end for event in relevant
        ):
            excluded.append(session.session_date)
        else:
            eligible.append(session.session_date)
    return Exposure(
        status="research_proxy" if proxy else "observed",
        eligible_dates=tuple(eligible),
        excluded_dates=tuple(excluded),
        evidence_ids=evidence,
    )


def append_context(repo: EvidenceRepository, store: ObjectStore, raw: bytes, fact: Fact) -> None:
    event = ContextEvent.model_validate(fact.payload)
    if hashlib.sha256(raw).hexdigest() != event.source_digest or fact.digest != event.source_digest:
        raise ValueError("context source hash mismatch")
    if event.evidence_text not in raw.decode("utf-8-sig"):
        raise ValueError("context evidence text is absent from source")
    if fact.logical_key != f"context:{event.event_id}" or fact.processed_at < event.available_at:
        raise ValueError("context identity or availability disagrees with evidence fact")
    if fact.valid_to is not None or fact.valid_from > event.available_at:
        raise ValueError("context knowledge envelope must retain historical scheduled events")
    if (
        event.company_id is not None
        and not repo.connection.execute(
            text("SELECT 1 FROM entities WHERE entity_id=:id AND kind='company'"),
            {"id": event.company_id},
        ).first()
    ):
        raise ValueError("context company identity is not registered")
    repo.capture(store, raw)
    repo.append(fact)


def known_events(repo: EvidenceRepository, source_id: str, cutoff: datetime) -> list[ContextEvent]:
    return [
        ContextEvent.model_validate(f.payload)
        for f in repo.as_of(source_id=source_id, cutoff=cutoff, effective_at=cutoff)
        if f.logical_key.startswith("context:")
    ]
