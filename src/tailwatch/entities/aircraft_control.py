"""Evidence-assisted candidates and explicit, dated human control decisions."""

import re
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from tailwatch.domain.time import utc
from tailwatch.entities.history import Period
from tailwatch.sources.policy import Contract, NonEmpty
from tailwatch.sources.registry import RegistryArchive

Role = Literal["registrant", "operator", "controller"]


class LinkEvidence(Period):
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    locator: NonEmpty
    quote: NonEmpty
    available_at: AwareDatetime
    kind: Literal[
        "name",
        "address",
        "home_base",
        "registrant_document",
        "operator_document",
        "controller_document",
    ]
    intermediary: bool = False
    shared: bool = False


class CompanyCandidate(Period):
    company_id: UUID
    name: NonEmpty
    available_at: AwareDatetime
    evidence: tuple[LinkEvidence, ...] = ()


class Candidate(Contract):
    company_id: UUID
    reasons: tuple[str, ...]
    evidence: tuple[LinkEvidence, ...]
    # These are suggestions to review, never approval or probabilities.
    status: Literal["possible"] = "possible"


def normalized_name(name: str) -> str:
    return " ".join(re.findall(r"[A-Z0-9]+", name.upper()))


def candidates(
    archive: RegistryArchive,
    row: int,
    companies: list[CompanyCandidate],
    *,
    cutoff: datetime,
    effective_at: datetime,
    max_registry_age: timedelta,
) -> list[Candidate]:
    cutoff, effective_at = utc(cutoff), utc(effective_at)
    if effective_at > cutoff or max_registry_age <= timedelta(0):
        raise ValueError("effective time follows knowledge cutoff")
    if (
        archive.available_at > cutoff
        or archive.observed_at > effective_at
        or effective_at - archive.observed_at >= max_registry_age
    ):
        return []
    records = [r for r in archive.records if r.row == row]
    if len(records) != 1 or not records[0].eligible_for_control_review:
        return []
    record = records[0]
    if len({c.company_id for c in companies}) != len(companies):
        raise ValueError("duplicate company candidate")
    result = []
    for company in companies:
        if (
            company.available_at > cutoff
            or company.start > effective_at
            or (company.end is not None and effective_at >= company.end)
        ):
            continue
        evidence = tuple(
            e
            for e in company.evidence
            if e.available_at <= cutoff
            and e.start <= effective_at
            and (e.end is None or effective_at < e.end)
            and not e.intermediary
            and not e.shared
        )
        reasons: set[str] = {e.kind for e in evidence}
        if normalized_name(company.name) == normalized_name(record.registrant_name or ""):
            reasons.add("registry_name_match")
        if reasons:
            result.append(
                Candidate(
                    company_id=company.company_id, reasons=tuple(sorted(reasons)), evidence=evidence
                )
            )
    return sorted(result, key=lambda c: str(c.company_id))


class ControlReview(Period):
    airframe_id: UUID
    company_id: UUID
    role: Role
    decision: Literal["approved", "rejected", "possible"]
    registry_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    registry_row: int = Field(ge=2)
    airframe_candidate_key: NonEmpty
    reviewed_at: AwareDatetime
    reviewer: NonEmpty
    rationale: NonEmpty
    alternatives: tuple[UUID, ...] = ()
    evidence: tuple[LinkEvidence, ...] = Field(min_length=1)
    privacy: Literal["cleared", "unknown", "withheld"] = "unknown"

    @model_validator(mode="after")
    def reviewed_evidence(self) -> "ControlReview":
        if self.company_id in self.alternatives or len(set(self.alternatives)) != len(
            self.alternatives
        ):
            raise ValueError("alternative companies must be distinct")
        if any(e.available_at > self.reviewed_at for e in self.evidence):
            raise ValueError("review uses future evidence")
        if self.decision == "approved":
            if self.privacy != "cleared":
                raise ValueError("approval requires explicit privacy clearance")
            direct = [
                e
                for e in self.evidence
                if e.kind == f"{self.role}_document"
                and not e.intermediary
                and not e.shared
                and e.start <= self.start
                and (e.end is None or (self.end is not None and self.end <= e.end))
            ]
            if not direct:
                raise ValueError("approval requires direct role evidence covering the interval")
        return self


class KnownReview(Contract):
    review: ControlReview
    known_at: AwareDatetime
    revision: int = Field(ge=1)

    @model_validator(mode="after")
    def knowledge_time(self) -> "KnownReview":
        if self.known_at < self.review.reviewed_at:
            raise ValueError("knowledge predates review")
        return self


class ControlResolution(Contract):
    state: Literal["approved", "unresolved", "ambiguous", "unsupported"]
    company_id: UUID | None = None


def resolve_control(
    reviews: list[KnownReview],
    archive: RegistryArchive,
    *,
    airframe_id: UUID,
    role: Role,
    cutoff: datetime,
    effective_at: datetime,
    max_registry_age: timedelta,
) -> ControlResolution:
    cutoff, effective_at = utc(cutoff), utc(effective_at)
    if effective_at > cutoff or max_registry_age <= timedelta(0):
        raise ValueError("effective time follows knowledge cutoff")
    if (
        archive.available_at > cutoff
        or archive.observed_at > effective_at
        or effective_at - archive.observed_at >= max_registry_age
    ):
        return ControlResolution(state="unsupported")
    latest: dict[UUID, KnownReview] = {}
    for item in reviews:
        r = item.review
        if item.known_at > cutoff or r.airframe_id != airframe_id or r.role != role:
            continue
        previous = latest.get(r.company_id)
        if previous is not None and item.revision == previous.revision and item != previous:
            return ControlResolution(state="ambiguous")
        if previous is None or item.revision > previous.revision:
            latest[r.company_id] = item
    approved = []
    for item in latest.values():
        r = item.review
        if r.decision != "approved" or r.start > effective_at or (r.end and effective_at >= r.end):
            continue
        records = [record for record in archive.records if record.row == r.registry_row]
        if (
            archive.digest != r.registry_digest
            or len(records) != 1
            or not records[0].eligible_for_control_review
            or records[0].airframe_candidate_key != r.airframe_candidate_key
        ):
            continue
        approved.append(r.company_id)
    if len(approved) > 1:
        return ControlResolution(state="ambiguous")
    return (
        ControlResolution(state="approved", company_id=approved[0])
        if approved
        else ControlResolution(state="unresolved")
    )
