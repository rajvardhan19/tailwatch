"""Immutable source evidence repositories using caller-owned transactions."""

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy import Connection, text

from tailwatch.domain.time import ReplayMode, utc
from tailwatch.sources.objects import ObjectStore
from tailwatch.sources.policy import Contract, NonEmpty


class Fact(Contract):
    fact_id: UUID
    source_id: NonEmpty
    snapshot_id: UUID
    logical_key: NonEmpty
    revision: int = Field(ge=1, strict=True)
    supersedes: UUID | None = None
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_locator: NonEmpty
    parser_version: NonEmpty
    occurred_at: AwareDatetime | None = None
    source_published_at: AwareDatetime | None = None
    ingested_at: AwareDatetime
    processed_at: AwareDatetime
    approved_at: AwareDatetime | None = None
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None = None
    payload: dict[str, Any]

    @model_validator(mode="after")
    def chronology(self) -> "Fact":
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("empty validity interval")
        if self.processed_at < self.ingested_at:
            raise ValueError("processing predates ingestion")
        if self.source_published_at and self.source_published_at > self.ingested_at:
            raise ValueError("public availability follows ingestion")
        if self.approved_at and self.approved_at < self.processed_at:
            raise ValueError("approval predates processing")
        if (self.revision == 1) != (self.supersedes is None):
            raise ValueError("revision must link its predecessor")
        json.dumps(self.payload, allow_nan=False)
        return self


class EvidenceRepository:
    def __init__(self, connection: Connection) -> None:
        if connection.dialect.name != "postgresql":
            raise ValueError("PostgreSQL is required")
        self.connection = connection

    def register_source(self, source_id: str) -> None:
        self.connection.execute(
            text("INSERT INTO sources VALUES (:id) ON CONFLICT DO NOTHING"), {"id": source_id}
        )

    def capture(self, store: ObjectStore, data: bytes) -> str:
        digest = store.put(data)
        self.connection.execute(
            text("INSERT INTO raw_objects VALUES (:digest, :size) ON CONFLICT DO NOTHING"),
            {"digest": digest, "size": len(data)},
        )
        return digest

    def begin_snapshot(
        self,
        *,
        snapshot_id: UUID,
        source_id: str,
        created_at: datetime,
        watermark: datetime,
        expected_records: int,
        manifest_hash: str,
    ) -> None:
        parameters = {
            "id": snapshot_id,
            "source": source_id,
            "created": utc(created_at),
            "watermark": utc(watermark),
            "expected": expected_records,
            "hash": manifest_hash,
        }
        row = self.connection.execute(
            text("""
            INSERT INTO source_snapshots VALUES
              (:id, :source, :created, :watermark, :expected, :hash)
            ON CONFLICT DO NOTHING RETURNING snapshot_id
        """),
            parameters,
        ).first()
        if row is None:
            existing = (
                self.connection.execute(
                    text("SELECT * FROM source_snapshots WHERE snapshot_id=:id"),
                    {"id": snapshot_id},
                )
                .mappings()
                .one()
            )
            wanted = {
                "snapshot_id": snapshot_id,
                "source_id": source_id,
                "created_at": utc(created_at),
                "watermark": utc(watermark),
                "expected_records": expected_records,
                "manifest_hash": manifest_hash,
            }
            if dict(existing) != wanted:
                raise ValueError("snapshot ID reused with different manifest")

    def append(self, fact: Fact) -> None:
        values = fact.model_dump()
        values["payload"] = json.dumps(fact.payload, allow_nan=False)
        # Serialize same-snapshot writers; a retry of an existing fact is safe
        # even after publication, but never silently accepts changed contents.
        self.connection.execute(
            text("SELECT snapshot_id FROM source_snapshots WHERE snapshot_id=:id FOR UPDATE"),
            {"id": fact.snapshot_id},
        )
        existing = (
            self.connection.execute(
                text("SELECT * FROM evidence_facts WHERE fact_id=:id"), {"id": fact.fact_id}
            )
            .mappings()
            .first()
        )
        if existing is not None:
            if Fact.model_validate(dict(existing)) != fact:
                raise ValueError("fact ID reused with different evidence")
            return
        columns = list(Fact.model_fields)
        expressions = [
            "CAST(:payload AS jsonb)" if key == "payload" else f":{key}" for key in columns
        ]
        self.connection.execute(
            text(
                f"INSERT INTO evidence_facts ({', '.join(columns)}) "
                f"VALUES ({', '.join(expressions)})"
            ),
            values,
        )

    def publish(self, snapshot_id: UUID, at: datetime) -> None:
        self.connection.execute(
            text("SELECT snapshot_id FROM source_snapshots WHERE snapshot_id=:id FOR UPDATE"),
            {"id": snapshot_id},
        )
        existing = self.connection.execute(
            text("SELECT published_at FROM snapshot_publications WHERE snapshot_id=:id"),
            {"id": snapshot_id},
        ).scalar_one_or_none()
        if existing is not None:
            if existing != utc(at):
                raise ValueError("publication time cannot change on retry")
            return
        self.connection.execute(
            text("INSERT INTO snapshot_publications (snapshot_id, published_at) VALUES (:id, :at)"),
            {"id": snapshot_id, "at": utc(at)},
        )

    def as_of(
        self,
        *,
        source_id: str,
        cutoff: datetime,
        effective_at: datetime,
        mode: ReplayMode = ReplayMode.OPERATIONAL,
        public_latency_seconds: int | None = None,
    ) -> list[Fact]:
        mode = ReplayMode(mode)
        if mode == ReplayMode.OPERATIONAL:
            visibility = "greatest(p.published_at, p.recorded_at) <= :cutoff"
        else:
            if type(public_latency_seconds) is not int or public_latency_seconds < 0:
                raise ValueError("public simulation requires a nonnegative explicit latency")
            visibility = "f.source_published_at + make_interval(secs => :latency) <= :cutoff"
        # Choose latest known revision FIRST, then apply its corrected validity.
        rows = self.connection.execute(
            text(f"""
            WITH known AS (
              SELECT f.*, row_number() OVER (
                PARTITION BY f.logical_key ORDER BY f.revision DESC
              ) AS rank FROM evidence_facts f
              JOIN snapshot_publications p USING (snapshot_id)
              WHERE f.source_id=:source AND {visibility}
            ) SELECT * FROM known WHERE rank=1 AND valid_from <= :effective
              AND (valid_to IS NULL OR :effective < valid_to) ORDER BY logical_key
        """),
            {
                "source": source_id,
                "cutoff": utc(cutoff),
                "effective": utc(effective_at),
                "latency": public_latency_seconds,
            },
        ).mappings()
        return [Fact.model_validate({k: v for k, v in row.items() if k != "rank"}) for row in rows]

    def current_snapshot(self, source_id: str, cutoff: datetime) -> UUID | None:
        """Newest complete watermark, never a failed or staged batch."""
        value = self.connection.execute(
            text("""
            SELECT s.snapshot_id FROM source_snapshots s
            JOIN snapshot_publications p USING (snapshot_id)
            WHERE s.source_id=:source AND greatest(p.published_at, p.recorded_at) <= :cutoff
            ORDER BY s.watermark DESC, p.published_at DESC, s.snapshot_id DESC LIMIT 1
        """),
            {"source": source_id, "cutoff": utc(cutoff)},
        ).scalar_one_or_none()
        return UUID(str(value)) if value is not None else None
