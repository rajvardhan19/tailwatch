"""Bounded readsb archive parsing and conservative inferred flight candidates."""

import gzip
import hashlib
import io
import json
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from geographiclib.geodesic import Geodesic  # type: ignore[import-untyped]
from pydantic import AwareDatetime, Field, model_validator

from tailwatch.sources.flights import FlightLeg
from tailwatch.sources.objects import ObjectStore
from tailwatch.sources.policy import Contract

MAX_BYTES = 16_000_000


class TracePoint(Contract):
    at: AwareDatetime
    available_at: AwareDatetime
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    ground: bool | None
    usable: bool
    evidence: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def chronology(self) -> "TracePoint":
        if self.available_at < self.at:
            raise ValueError("trace available before observation")
        return self


def _number(value: object) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):  # type: ignore[arg-type]
        raise ValueError("finite numeric trace field required")
    return float(value)  # type: ignore[arg-type]


def parse_trace(
    raw: bytes, *, approved_icao: str, available_at: datetime, store: ObjectStore
) -> tuple[TracePoint, ...]:
    """Import one already-authorized local archive; no network or identity inference."""
    if len(raw) > MAX_BYTES or not re.fullmatch(r"[0-9a-f]{6}", approved_icao):
        raise ValueError("bounded trace and reviewed ICAO required")
    if available_at.tzinfo is None or available_at.utcoffset() is None:
        raise ValueError("aware archive availability required")
    digest = store.put(raw)
    if raw[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as archive:
            raw = archive.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("expanded trace exceeds limit")
    body = json.loads(raw)
    if not isinstance(body, dict) or body.get("icao") != approved_icao:
        raise ValueError("archive identity does not match reviewed scope")
    flags = body.get("dbFlags", 0)
    if type(flags) is not int or flags & 12:
        raise ValueError("privacy-restricted trace excluded")
    base = _number(body.get("timestamp"))
    rows = body.get("trace")
    if not isinstance(rows, list) or len(rows) > 100_000:
        raise ValueError("bounded trace rows required")
    points: dict[datetime, TracePoint] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 9 or type(row[6]) is not int:
            raise ValueError("malformed trace row")
        at = datetime.fromtimestamp(base + _number(row[0]), UTC)
        if at > available_at:
            raise ValueError("trace position unavailable at archive cutoff")
        altitude = row[3]
        ground = True if altitude == "ground" else False if type(altitude) in (int, float) else None
        if ground is False:
            _number(altitude)
        source = (
            row[9]
            if len(row) > 9
            else (row[8] or {}).get("type")
            if isinstance(row[8], dict)
            else None
        )
        point = TracePoint(
            at=at,
            available_at=available_at,
            latitude=_number(row[1]),
            longitude=_number(row[2]),
            ground=ground,
            usable=not bool(row[6] & 1) and source == "adsb_icao" and ground is not None,
            evidence=digest,
        )
        if at in points and points[at] != point:
            raise ValueError("conflicting same-time trace positions")
        points[at] = point
    return tuple(points[key] for key in sorted(points))


def infer_legs(
    points: tuple[TracePoint, ...],
    *,
    source_id: str,
    airframe_id: UUID,
    airport_at: Callable[[TracePoint], UUID | None],
    cutoff: datetime,
    max_gap_seconds: int = 120,
    max_speed_mps: float = 400,
) -> tuple[FlightLeg, ...]:
    """Ground-air-ground candidates. Caller supplies dated, unambiguous airport resolution.

    A trace never establishes complete coverage. Missing/stale/ambiguous points,
    implausible jumps and receiver gaps break continuity instead of inventing dwell.
    """
    if not 1 <= max_gap_seconds <= 1800 or not math.isfinite(max_speed_mps) or max_speed_mps <= 0:
        raise ValueError("bounded registered trace thresholds required")
    ordered = sorted(
        (p for p in points if p.available_at <= cutoff and p.at <= cutoff), key=lambda p: p.at
    )
    if len({p.at for p in ordered}) != len(ordered):
        raise ValueError("duplicate timestamps must be reconciled before inference")
    origin: tuple[TracePoint, UUID] | None = None
    previous = None
    known_at = None
    airborne = False
    evidence: set[str] = set()
    result = []
    for point in ordered:
        broken = not point.usable or point.ground is None
        if previous is not None:
            delta = (point.at - previous.at).total_seconds()
            distance = Geodesic.WGS84.Inverse(
                previous.latitude, previous.longitude, point.latitude, point.longitude
            )["s12"]
            broken = broken or delta > max_gap_seconds or distance / delta > max_speed_mps
        if broken:
            origin, airborne, evidence = None, False, set()
            known_at = None
        previous = point
        if not point.usable or point.ground is None:
            continue
        known_at = max(known_at, point.available_at) if known_at else point.available_at
        evidence.add(point.evidence)
        if point.ground:
            airport = airport_at(point)
            if airport is not None and origin is not None and airborne:
                departure, departure_airport = origin
                identity = (
                    f"trace-v1:{source_id}:{airframe_id}:{departure.at.isoformat()}:"
                    f"{point.at.isoformat()}:{departure_airport}:{airport}:"
                    f"{max_gap_seconds}:{max_speed_mps}"
                )
                result.append(
                    FlightLeg(
                        source_id=source_id,
                        leg_id=hashlib.sha256(identity.encode()).hexdigest(),
                        airframe_id=airframe_id,
                        origin=departure_airport,
                        destination=airport,
                        departed_at=departure.at,
                        arrived_at=point.at,
                        available_at=known_at,
                        timing="inferred",
                        evidence="trace-v1:" + ",".join(sorted(evidence)),
                    )
                )
            origin = (point, airport) if airport is not None else None
            airborne = False
            evidence = {point.evidence}
            known_at = point.available_at
        elif point.ground is False and origin is not None:
            airborne = True
    return tuple(result)
