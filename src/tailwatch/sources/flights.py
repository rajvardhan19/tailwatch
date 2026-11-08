"""Normalized completed legs, honest coverage, and bounded paged capture."""

import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, model_validator

from tailwatch.db.evidence import EvidenceRepository, Fact
from tailwatch.entities.history import Period
from tailwatch.sources.objects import ObjectStore
from tailwatch.sources.policy import Budget, Contract, Mode, NonEmpty, Source, authorize


class FlightLeg(Contract):
    source_id: NonEmpty
    leg_id: NonEmpty
    airframe_id: UUID | None  # unresolved identities never become visits
    origin: UUID | None
    destination: UUID | None
    departed_at: AwareDatetime
    arrived_at: AwareDatetime
    available_at: AwareDatetime
    timing: Literal["actual", "inferred"]
    evidence: NonEmpty

    @model_validator(mode="after")
    def ordered(self) -> "FlightLeg":
        if not self.departed_at < self.arrived_at <= self.available_at:
            raise ValueError("completed leg has impossible chronology")
        return self


class FlightCoverage(Period):
    airframe_id: UUID
    available_at: AwareDatetime
    status: Literal["complete", "partial", "outage", "unknown"]
    evidence: NonEmpty

    @model_validator(mode="after")
    def bounded(self) -> "FlightCoverage":
        if self.end is None or self.end > self.available_at:
            raise ValueError("coverage must be bounded by observation availability")
        return self


class FlightPage(Contract):
    source_id: NonEmpty
    watermark: AwareDatetime
    cadence: Literal["nightly", "realtime", "archive"]
    legs: tuple[FlightLeg, ...]
    coverage: tuple[FlightCoverage, ...]
    next_cursor: NonEmpty | None = None

    @model_validator(mode="after")
    def source_matches(self) -> "FlightPage":
        if any(
            leg.source_id != self.source_id or leg.arrived_at > self.watermark for leg in self.legs
        ):
            raise ValueError("leg does not match source/watermark")
        return self


class FlightBatch(Contract):
    source_id: NonEmpty
    status: Literal["complete", "incomplete", "quarantined"]
    watermark: AwareDatetime | None
    raw_digests: tuple[str, ...]
    legs: tuple[FlightLeg, ...]
    coverage: tuple[FlightCoverage, ...]
    conflicts: tuple[str, ...]
    reason: str | None = None


class FlightTransport(Protocol):
    def fetch(self, cursor: str | None) -> bytes: ...


def collect_flights(
    source: Source,
    mode: Mode,
    transport: FlightTransport,
    store: ObjectStore,
    now: Callable[[], datetime],
    budget: Budget,
    *,
    cost_microusd: int = 0,
    max_attempts: int = 3,
) -> FlightBatch:
    if max_attempts < 1 or max_attempts > 5:
        raise ValueError("retry count out of bounds")
    cursor = None
    seen: set[str] = set()
    pages: list[FlightPage] = []
    digests: list[str] = []
    reason = None
    try:
        while True:
            body = None
            for attempt in range(max_attempts):
                authorize(source, mode=mode, capability="flights", at=now())
                budget.validate_for(source)
                budget.reserve(cost_microusd)
                try:
                    body = transport.fetch(cursor)
                    break
                except OSError:
                    if attempt + 1 == max_attempts:
                        raise
            assert body is not None
            digests.append(store.put(body))
            page = FlightPage.model_validate_json(body)
            if page.source_id != source.source_id or page.watermark > now():
                raise ValueError("invalid source or future watermark")
            if any(leg.available_at > now() for leg in page.legs) or any(
                c.available_at > now() for c in page.coverage
            ):
                raise ValueError("future availability")
            if pages and (page.watermark != pages[0].watermark or page.cadence != pages[0].cadence):
                raise ValueError("pagination changed snapshot")
            pages.append(page)
            cursor = page.next_cursor
            if cursor is None:
                break
            if cursor in seen:
                raise ValueError("pagination cycle")
            seen.add(cursor)
    except (OSError, ValueError) as error:
        reason = type(error).__name__  # No credentials/provider response in logs.
    by_id: dict[str, FlightLeg] = {}
    conflicts: set[str] = set()
    for page in pages:
        for leg in page.legs:
            previous = by_id.get(leg.leg_id)
            if previous is not None and previous != leg:
                conflicts.add(leg.leg_id)
            by_id[leg.leg_id] = leg
    # Distinct IDs describing the identical economic leg are deduplicated. Any
    # overlapping legs of the same resolved airframe are quarantined otherwise.
    legs = sorted(by_id.values(), key=lambda leg: (leg.departed_at, leg.leg_id))
    unique: dict[tuple[object, ...], FlightLeg] = {}
    for leg in legs:
        key = (leg.airframe_id, leg.origin, leg.destination, leg.departed_at, leg.arrived_at)
        if leg.airframe_id is not None and key in unique:
            continue
        for other in unique.values():
            if (
                leg.airframe_id is not None
                and leg.airframe_id == other.airframe_id
                and leg.departed_at < other.arrived_at
                and other.departed_at < leg.arrived_at
            ):
                conflicts.update((leg.leg_id, other.leg_id))
        unique[key if leg.airframe_id else (leg.leg_id,)] = leg
    return FlightBatch(
        source_id=source.source_id,
        status="incomplete" if reason else "quarantined" if conflicts else "complete",
        watermark=pages[0].watermark if pages else None,
        raw_digests=tuple(digests),
        legs=tuple(leg for leg in unique.values() if leg.leg_id not in conflicts),
        coverage=tuple(c for page in pages for c in page.coverage),
        conflicts=tuple(sorted(conflicts)),
        reason=reason,
    )


def append_flight_batch(repo: "EvidenceRepository", store: ObjectStore, fact: "Fact") -> None:
    batch = FlightBatch.model_validate(fact.payload)
    raw = batch.model_dump_json().encode()
    if (
        fact.digest != hashlib.sha256(raw).hexdigest()
        or fact.source_id != batch.source_id
        or fact.parser_version != "flight-batch-v1"
        or fact.evidence_locator != "batch"
        or not fact.logical_key.startswith("flights:")
        or batch.status != "complete"
        or any(leg.available_at > fact.processed_at for leg in batch.legs)
    ):
        raise ValueError("incomplete or mismatched flight batch")
    for digest in batch.raw_digests:
        repo.capture(store, store.get(digest))
    repo.capture(store, raw)
    repo.append(fact)
