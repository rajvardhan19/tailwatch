"""Typed airport identities and reproducible WGS84 airport-office proximity."""

import csv
import hashlib
import io
import math
from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from geographiclib.geodesic import Geodesic  # type: ignore[import-untyped]
from pydantic import AwareDatetime, Field, model_validator

from tailwatch.db.evidence import EvidenceRepository, Fact
from tailwatch.domain.time import utc
from tailwatch.entities.history import History, OfficeHistory, Period
from tailwatch.entities.repository import EntityRepository
from tailwatch.sources.objects import ObjectStore
from tailwatch.sources.policy import Contract, NonEmpty


class Coordinates(Contract):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class AirportAlias(Contract):
    kind: Literal["ident", "icao", "iata", "gps", "local"]
    code: str = Field(pattern=r"^[A-Z0-9-]+$")


class AirportPeriod(Period):
    name: NonEmpty
    country: str = Field(pattern=r"^[A-Z]{2}$")
    location: Coordinates
    facility_type: Literal[
        "small_airport",
        "medium_airport",
        "large_airport",
        "heliport",
        "seaplane_base",
        "balloonport",
        "closed",
    ]
    aliases: tuple[AirportAlias, ...]

    @model_validator(mode="after")
    def unique_aliases(self) -> "AirportPeriod":
        if len({(a.kind, a.code) for a in self.aliases}) != len(self.aliases):
            raise ValueError("duplicate airport alias")
        return self


class AirportHistory(History[AirportPeriod]):
    kind: Literal["airport"] = "airport"
    provider: NonEmpty
    provider_id: NonEmpty


def parse_ourairports(raw: bytes, observed_at: datetime) -> list[AirportHistory]:
    """Current CSV observations cannot establish airport history before their date.

    Reject the whole import on malformed rows/duplicate IDs: otherwise omissions
    could turn an ambiguous code into an apparently unique match.
    """
    observed_at = utc(observed_at)
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
    required = {
        "id",
        "ident",
        "type",
        "name",
        "latitude_deg",
        "longitude_deg",
        "iso_country",
        "icao_code",
        "iata_code",
        "gps_code",
        "local_code",
    }
    header = reader.fieldnames or []
    if not required <= set(header) or len(set(header)) != len(header):
        raise ValueError("missing or duplicate airport columns")
    result = []
    seen = set()
    for row in reader:
        if None in row or any(v is None for v in row.values()):
            raise ValueError("malformed airport row")
        provider_id = row["id"].strip()
        if not provider_id.isascii() or not provider_id.isdigit() or int(provider_id) <= 0:
            raise ValueError("invalid airport ID")
        provider_id = str(int(provider_id))
        if provider_id in seen:
            raise ValueError("duplicate airport ID")
        seen.add(provider_id)
        aliases = [
            {"kind": kind, "code": row[column].strip().upper()}
            for kind, column in (
                ("ident", "ident"),
                ("icao", "icao_code"),
                ("iata", "iata_code"),
                ("gps", "gps_code"),
                ("local", "local_code"),
            )
            if row[column].strip()
        ]
        result.append(
            AirportHistory.model_validate(
                {
                    "entity_id": uuid5(NAMESPACE_URL, f"https://ourairports.com/id/{provider_id}"),
                    "provider": "ourairports",
                    "provider_id": provider_id,
                    "periods": [
                        {
                            "start": observed_at,
                            "name": row["name"],
                            "country": row["iso_country"],
                            "facility_type": row["type"],
                            "location": {
                                "latitude": row["latitude_deg"],
                                "longitude": row["longitude_deg"],
                            },
                            "aliases": aliases,
                        }
                    ],
                }
            )
        )
    return sorted(result, key=lambda a: str(a.entity_id))


def append_airport(repo: EvidenceRepository, store: ObjectStore, raw: bytes, fact: Fact) -> None:
    """Import a reviewed, dated history JSON; CSV observations can be staged into it."""
    history = AirportHistory.model_validate_json(raw)
    if (
        fact.payload != history.model_dump(mode="json")
        or fact.digest != hashlib.sha256(raw).hexdigest()
        or fact.logical_key != f"airport:{history.entity_id}"
        or fact.valid_from != history.periods[0].start
        or fact.valid_to is not None
        or fact.parser_version != "airport-history-v1"
        or fact.evidence_locator != "document"
    ):
        raise ValueError("airport history evidence mismatch")
    repo.capture(store, raw)
    repo.append(fact)


def airport_histories(
    repo: EvidenceRepository, source_id: str, cutoff: datetime, effective_at: datetime
) -> list[tuple[Fact, AirportHistory]]:
    return [
        (f, AirportHistory.model_validate(f.payload))
        for f in repo.as_of(source_id=source_id, cutoff=cutoff, effective_at=effective_at)
        if f.logical_key.startswith("airport:") and f.parser_version == "airport-history-v1"
    ]


def resolve_alias(
    histories: list[AirportHistory],
    *,
    kind: str,
    code: str,
    effective_at: datetime,
    country: str | None = None,
) -> UUID | None:
    if kind not in {"ident", "icao", "iata", "gps", "local"}:
        raise ValueError("unknown airport code namespace")
    if len({h.entity_id for h in histories}) != len(histories):
        raise ValueError("select one known revision per airport before resolving aliases")
    matches = set()
    for history in histories:
        period = history.at(effective_at)
        if period and (country is None or period.country == country.upper()):
            if any(a.kind == kind and a.code == code.strip().upper() for a in period.aliases):
                matches.add(history.entity_id)
    if len(matches) > 1:
        raise ValueError("ambiguous airport code; explicit country/provider identity required")
    return next(iter(matches), None)


def distance_km(first: Coordinates, second: Coordinates) -> float:
    return (
        float(
            Geodesic.WGS84.Inverse(
                first.latitude, first.longitude, second.latitude, second.longitude
            )["s12"]
        )
        / 1000
    )


class ProximityLink(Period):
    airport_id: UUID
    office_id: UUID
    company_id: UUID
    distance_km: float = Field(ge=0, allow_inf_nan=False)
    airport_fact_id: UUID
    office_fact_id: UUID


class ProximitySnapshot(Contract):
    algorithm: Literal["wgs84-nearest-office-v1"] = "wgs84-nearest-office-v1"
    cutoff: AwareDatetime
    effective_at: AwareDatetime
    radius_km: float = Field(gt=0, allow_inf_nan=False)
    coverage: Literal["inputs_present", "missing_source"]
    input_fact_ids: tuple[UUID, ...]
    links: tuple[ProximityLink, ...]

    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    def company_distances(self, airport_id: UUID) -> dict[UUID, float]:
        result: dict[UUID, float] = {}
        for link in self.links:
            if link.airport_id == airport_id:
                result[link.company_id] = min(
                    result.get(link.company_id, math.inf), link.distance_km
                )
        return result


def proximity_snapshot(
    entities: EntityRepository,
    *,
    airport_source: str,
    office_source: str,
    cutoff: datetime,
    effective_at: datetime,
    radius_km: float = 50,
) -> ProximitySnapshot:
    cutoff, effective_at = utc(cutoff), utc(effective_at)
    if effective_at > cutoff or not math.isfinite(radius_km) or radius_km <= 0:
        raise ValueError("nonfuture time and positive finite kilometer radius required")
    airports = airport_histories(entities.evidence, airport_source, cutoff, effective_at)
    offices = [
        (f, h)
        for f, h in entities.histories(office_source, cutoff, effective_at)
        if isinstance(h, OfficeHistory)
    ]
    links = []
    for airport_fact, airport in airports:
        airport_period = airport.at(effective_at)
        if airport_period is None or airport_period.facility_type == "closed":
            continue
        for office_fact, office in offices:
            office_period = office.at(effective_at)
            if office_period is None:
                continue
            distance = distance_km(
                airport_period.location,
                Coordinates(latitude=office_period.latitude, longitude=office_period.longitude),
            )
            if distance > radius_km:
                continue
            ends = [p.end for p in (airport_period, office_period) if p.end is not None]
            links.append(
                ProximityLink(
                    start=max(airport_period.start, office_period.start),
                    end=min(ends) if ends else None,
                    airport_id=airport.entity_id,
                    office_id=office.entity_id,
                    company_id=office.company_id,
                    distance_km=distance,
                    airport_fact_id=airport_fact.fact_id,
                    office_fact_id=office_fact.fact_id,
                )
            )
    return ProximitySnapshot(
        cutoff=cutoff,
        effective_at=effective_at,
        radius_km=radius_km,
        coverage="inputs_present" if airports and offices else "missing_source",
        input_fact_ids=tuple(sorted({f.fact_id for f, _ in airports + offices}, key=str)),
        links=tuple(
            sorted(
                links,
                key=lambda link: (str(link.airport_id), str(link.company_id), str(link.office_id)),
            )
        ),
    )
