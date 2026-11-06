"""Evidence-bound Item 408 extraction candidates and reviewed quarterly disclosures."""

import hashlib
import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from typing import Final, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from tailwatch.db.evidence import EvidenceRepository, Fact
from tailwatch.domain.time import utc
from tailwatch.sources.objects import ObjectStore
from tailwatch.sources.policy import Contract, NonEmpty

EXTRACTOR_VERSION: Final = "item408-text-v1"


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "div", "tr", "br", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        if tag in {"p", "div", "tr", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def disclosure_text(raw: bytes) -> str:
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("disclosure exceeds size limit")
    parser = TextExtractor()
    parser.feed(raw.decode("utf-8-sig"))
    return "\n".join(
        line for part in "".join(parser.parts).splitlines() if (line := " ".join(part.split()))
    )


class Candidate(Contract):
    start: int
    end: int
    text: str
    # Hints only, not counted events or assertions about an insider's motivation.
    hint: Literal["adopted", "terminated", "modified", "expired", "completed", "unknown"]


def plan_candidates(raw: bytes) -> list[Candidate]:
    text = disclosure_text(raw)
    candidates = []
    for match in re.finditer(r"[^\n]+", text):
        passage = match.group()
        if not re.search(r"10b5.?1|trading (?:plan|arrangement)|408\(a\)", passage, re.I):
            continue
        kinds = {
            label
            for pattern, label in (
                (r"\badopt(?:ed|ion)\b", "adopted"),
                (r"\bterminat(?:ed|ion)\b", "terminated"),
                (r"\bmodif(?:ied|ication)|\breplac", "modified"),
                (r"\bexpir", "expired"),
                (r"\bcomplet", "completed"),
            )
            if re.search(pattern, passage, re.I)
        }
        # Negation or multiple actions requires review; never promote a keyword to a fact.
        hint = (
            next(iter(kinds))
            if len(kinds) == 1 and not re.search(r"\b(?:not|no|none|neither)\b", passage, re.I)
            else "unknown"
        )
        candidates.append(
            Candidate.model_validate(
                {"start": match.start(), "end": match.end(), "text": passage, "hint": hint}
            )
        )
    return candidates


class Passage(Contract):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: NonEmpty

    @model_validator(mode="after")
    def nonempty(self) -> "Passage":
        if self.end <= self.start:
            raise ValueError("empty passage interval")
        return self


class PlanEvent(Contract):
    event_id: NonEmpty
    plan_id: NonEmpty
    insider_id: UUID
    role: Literal["officer", "director"]
    event_date: date
    event: Literal["adopted", "terminated", "modified", "expired", "completed", "unknown"]
    arrangement: Literal["10b5_1", "non_10b5_1", "unknown"]
    replacement_of: str | None = None
    evidence: Passage


class PlanDisclosure(Contract):
    kind: Literal["plan_disclosure"] = "plan_disclosure"
    company_id: UUID
    accession: str = Field(pattern=r"^\d{10}-\d{2}-\d{6}$")
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    extractor_version: Literal["item408-text-v1"] = EXTRACTOR_VERSION
    form: Literal["10-Q", "10-Q/A", "10-K", "10-K/A"]
    quarter_start: date
    quarter_end: date
    available_at: AwareDatetime
    reviewed_at: AwareDatetime
    reviewer: NonEmpty
    status: Literal["events", "no_events", "unknown"]
    status_evidence: Passage
    events: tuple[PlanEvent, ...] = ()
    supersedes_accession: str | None = None

    @model_validator(mode="after")
    def coherent(self) -> "PlanDisclosure":
        if not timedelta(days=1) <= self.quarter_end - self.quarter_start <= timedelta(days=100):
            raise ValueError("supply the relevant fiscal quarter, not an annual period")
        if self.reviewed_at < self.available_at:
            raise ValueError("review predates disclosure availability")
        if (self.status == "events") != bool(self.events):
            raise ValueError("events require events status; no_events/unknown must have no events")
        if len({event.event_id for event in self.events}) != len(self.events):
            raise ValueError("duplicate event IDs")
        if self.form.endswith("/A") and self.supersedes_accession is None:
            raise ValueError("amended disclosure requires explicit original accession")
        if self.supersedes_accession == self.accession:
            raise ValueError("disclosure cannot supersede itself")
        for event in self.events:
            if not self.quarter_start <= event.event_date <= self.quarter_end:
                raise ValueError("event is outside the disclosed fiscal quarter")
        return self


def validate_disclosure(disclosure: PlanDisclosure, raw: bytes) -> None:
    if hashlib.sha256(raw).hexdigest() != disclosure.source_digest:
        raise ValueError("disclosure does not match raw source")
    text = disclosure_text(raw)
    for passage in (disclosure.status_evidence, *(event.evidence for event in disclosure.events)):
        if text[passage.start : passage.end] != passage.text:
            raise ValueError("reviewed passage does not match extracted source text")


def append_disclosure(repo: EvidenceRepository, store: ObjectStore, raw: bytes, fact: Fact) -> None:
    disclosure = PlanDisclosure.model_validate(fact.payload)
    validate_disclosure(disclosure, raw)
    if (
        fact.digest != disclosure.source_digest
        or fact.logical_key != f"plan:{disclosure.accession}"
    ):
        raise ValueError("fact identity disagrees with disclosure")
    if fact.processed_at < disclosure.reviewed_at:
        raise ValueError("fact processing predates the review it depends on")
    repo.capture(store, raw)
    repo.append(fact)


class TerminationSignal(Contract):
    count: int | None
    status: Literal["observed", "missing_source", "ambiguous", "stale"]
    accession: str | None = None


def termination_signal(
    disclosures: list[PlanDisclosure], *, company_id: UUID, as_of: datetime
) -> TerminationSignal:
    cutoff = utc(as_of)
    visible = [
        d
        for d in disclosures
        if d.company_id == company_id and max(d.available_at, d.reviewed_at) <= cutoff
    ]
    by_accession: dict[str, PlanDisclosure] = {}
    for disclosure in visible:
        if (
            disclosure.accession in by_accession
            and by_accession[disclosure.accession] != disclosure
        ):
            return TerminationSignal(count=None, status="ambiguous")
        by_accession[disclosure.accession] = disclosure
    if not visible:
        return TerminationSignal(count=None, status="missing_source")
    superseded = set()
    for disclosure in by_accession.values():
        if disclosure.supersedes_accession:
            original = by_accession.get(disclosure.supersedes_accession)
            if (
                original is None
                or original.quarter_start != disclosure.quarter_start
                or original.quarter_end != disclosure.quarter_end
                or original.available_at > disclosure.available_at
            ):
                return TerminationSignal(count=None, status="ambiguous")
            superseded.add(original.accession)
    active = [d for d in by_accession.values() if d.accession not in superseded]
    if not active:
        return TerminationSignal(count=None, status="ambiguous")
    latest = max(d.quarter_end for d in active)
    candidates = [d for d in active if d.quarter_end == latest]
    if len(candidates) != 1:
        return TerminationSignal(count=None, status="ambiguous")
    selected = candidates[0]
    if cutoff - selected.available_at > timedelta(days=180):
        return TerminationSignal(count=None, status="stale", accession=selected.accession)
    if selected.status == "unknown" or any(
        event.event == "unknown" or event.arrangement == "unknown" for event in selected.events
    ):
        return TerminationSignal(count=None, status="ambiguous", accession=selected.accession)
    count = sum(
        event.event == "terminated"
        and event.arrangement == "10b5_1"
        and event.replacement_of is None
        for event in selected.events
    )
    return TerminationSignal(count=count, status="observed", accession=selected.accession)
