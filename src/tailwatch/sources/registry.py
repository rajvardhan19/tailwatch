"""FAA MASTER observations, never inferred historical ownership or control."""

import csv
import hashlib
import io
import re
from datetime import datetime, timedelta
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from tailwatch.domain.time import utc
from tailwatch.sources.policy import Contract, NonEmpty

PARSER_VERSION: Literal["faa-master-v1"] = "faa-master-v1"
REQUIRED = {
    "N-NUMBER",
    "SERIAL NUMBER",
    "MFR MDL CODE",
    "TYPE REGISTRANT",
    "NAME",
    "STATUS CODE",
    "FRACT OWNER",
    "MODE S CODE HEX",
}
REGISTRANTS = {
    "1": "individual",
    "2": "partnership",
    "3": "corporation",
    "4": "co_owned",
    "5": "government",
    "7": "llc",
    "8": "noncitizen_corporation",
    "9": "noncitizen_co_owned",
}
TAIL = r"^N[1-9](?:[0-9]{0,4}|[0-9]{0,3}[A-HJ-NP-Z]|[0-9]{0,2}[A-HJ-NP-Z]{2})$"


class Registration(Contract):
    row: int = Field(ge=2)
    tail: str = Field(pattern=TAIL)
    serial: NonEmpty | None
    model_code: str | None = Field(pattern=r"^[0-9A-Z]{7}$")
    icao24: str | None = Field(pattern=r"^[0-9A-F]{6}$")
    registrant_code: str
    registrant_name: NonEmpty | None
    status: str
    fractional: bool | None
    disclosure: Literal["reported", "unknown", "withheld"]

    @property
    def airframe_candidate_key(self) -> str | None:
        # A candidate key, not a verified global airframe identity. Manufacturer
        # reference resolution and review remain necessary before linking feeds.
        if self.model_code is None or self.serial is None:
            return None
        return f"faa:{self.model_code}:{self.serial}"

    @property
    def registrant_type(self) -> str:
        return REGISTRANTS.get(self.registrant_code, "unknown")

    @property
    def eligible_for_control_review(self) -> bool:
        return (
            self.registrant_code in {"3", "7", "8"}
            and self.disclosure == "reported"
            and self.fractional is False
            and self.status == "V"
            and self.airframe_candidate_key is not None
            and self.icao24 is not None
        )


class Quarantine(Contract):
    row: int = Field(ge=2)
    reason: NonEmpty


class RegistryArchive(Contract):
    source_id: NonEmpty
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_version: Literal["faa-master-v1"] = PARSER_VERSION
    observed_at: AwareDatetime
    available_at: AwareDatetime
    records: tuple[Registration, ...]
    quarantined: tuple[Quarantine, ...]
    row_count: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent(self) -> "RegistryArchive":
        if self.observed_at > self.available_at:
            raise ValueError("registry observation follows availability")
        rows = [r.row for r in self.records] + [q.row for q in self.quarantined]
        if len(rows) != self.row_count or len(set(rows)) != len(rows):
            raise ValueError("archive row reconciliation failed")
        return self


def parse_master(
    raw: bytes,
    *,
    source_id: str,
    observed_at: datetime,
    available_at: datetime,
) -> RegistryArchive:
    """Strict CSV envelope; malformed rows remain counted, never silently dropped.

    Missing name means unknown, not proof of withholding. FAA blank fractional
    flag explicitly means nonfractional; absent columns are a schema failure.
    """
    reader = csv.reader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
    try:
        header = [v.strip().upper() for v in next(reader)]
    except StopIteration as exc:
        raise ValueError("empty registry file") from exc
    # The export may contain a trailing empty CSV column.
    if header and header[-1] == "":
        header.pop()
    if len(header) != len(set(header)) or not REQUIRED <= set(header) or "" in header:
        raise ValueError("missing or duplicate registry columns")
    records: list[Registration] = []
    failures: list[Quarantine] = []
    aliases: dict[tuple[str, str], int] = {}
    collisions: set[int] = set()
    count = 0
    for number, fields in enumerate(reader, start=2):
        count += 1
        if len(fields) == len(header) + 1 and fields[-1].strip() == "":
            fields.pop()
        # Preserve collision evidence even if another field in the row is bad.
        # Short rows can still expose a conflicting tail at its declared column.
        partial = dict(zip(header, (v.strip().upper() for v in fields), strict=False))
        identifiers = {
            "tail": partial.get("N-NUMBER", ""),
            "icao24": partial.get("MODE S CODE HEX", ""),
        }
        if partial.get("MFR MDL CODE") and partial.get("SERIAL NUMBER"):
            identifiers["airframe"] = partial["MFR MDL CODE"] + ":" + partial["SERIAL NUMBER"]
        for kind, value in identifiers.items():
            if value:
                key = (kind, value)
                if key in aliases:
                    collisions.update((number, aliases[key]))
                aliases[key] = number
        try:
            if len(fields) != len(header):
                raise ValueError("incorrect column count")
            row = dict(zip(header, (v.strip() for v in fields), strict=True))
            tail = "N" + row["N-NUMBER"].upper()
            if not re.fullmatch(TAIL, tail):
                raise ValueError("invalid N-number")
            fractional = row["FRACT OWNER"].upper()
            name = row["NAME"] or None
            disclosed = name is not None and name.upper() not in {
                "WITHHELD",
                "PRIVATE",
                "REDACTED",
                "UNKNOWN",
                "NAME WITHHELD",
            }
            records.append(
                Registration(
                    row=number,
                    tail=tail,
                    serial=row["SERIAL NUMBER"] or None,
                    model_code=row["MFR MDL CODE"].upper() or None,
                    icao24=row["MODE S CODE HEX"].upper() or None,
                    registrant_code=row["TYPE REGISTRANT"],
                    registrant_name=name,
                    status=row["STATUS CODE"],
                    fractional={"Y": True, "": False}.get(fractional),
                    disclosure="reported" if disclosed else "unknown",
                )
            )
        except ValueError:
            # Raw content stays in the object store; errors do not leak names.
            failures.append(Quarantine(row=number, reason="malformed registry row"))
    # Duplicate aliases or candidate identities make ALL implicated rows unsafe.
    failed_rows = {q.row for q in failures}
    failures.extend(
        Quarantine(row=r, reason="ambiguous registry identity") for r in collisions - failed_rows
    )
    return RegistryArchive(
        source_id=source_id,
        digest=hashlib.sha256(raw).hexdigest(),
        observed_at=observed_at,
        available_at=available_at,
        records=tuple(r for r in records if r.row not in collisions),
        quarantined=tuple(sorted(failures, key=lambda q: q.row)),
        row_count=count,
    )


class RegistryLookup(Contract):
    state: Literal["observed", "missing_source", "stale", "absent_or_quarantined", "ambiguous"]
    digest: str | None = None
    registration: Registration | None = None


def lookup(
    archives: list[RegistryArchive],
    *,
    source_id: str,
    alias: str,
    alias_kind: Literal["tail", "icao24"],
    cutoff: datetime,
    effective_at: datetime,
    max_age: timedelta,
) -> RegistryLookup:
    """Use only the newest known observation at/before the requested time.

    A caller must declare a freshness bound. Even an observed result establishes
    a snapshot registration, not continuous ownership between snapshots.
    """
    cutoff, effective_at = utc(cutoff), utc(effective_at)
    if max_age <= timedelta(0) or effective_at > cutoff:
        raise ValueError("positive freshness and nonfuture effective time required")
    if alias_kind not in {"tail", "icao24"}:
        raise ValueError("unsupported alias kind")
    known = [
        a
        for a in archives
        if a.source_id == source_id and a.available_at <= cutoff and a.observed_at <= effective_at
    ]
    if not known:
        return RegistryLookup(state="missing_source")
    newest = max(a.observed_at for a in known)
    selected = [a for a in known if a.observed_at == newest]
    if len({a.digest for a in selected}) != 1:
        return RegistryLookup(state="ambiguous")
    archive = selected[0]
    if effective_at - newest >= max_age:
        return RegistryLookup(state="stale", digest=archive.digest)
    matches = [r for r in archive.records if getattr(r, alias_kind) == alias.strip().upper()]
    if len(matches) != 1:
        return RegistryLookup(state="absent_or_quarantined", digest=archive.digest)
    return RegistryLookup(state="observed", digest=archive.digest, registration=matches[0])
