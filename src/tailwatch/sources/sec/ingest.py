"""Commit each completed filing independently so failures do not reset the cursor."""

import json
import re
from uuid import UUID

from sqlalchemy import Engine, text

from tailwatch.db.evidence import EvidenceRepository
from tailwatch.sources.objects import ObjectStore
from tailwatch.sources.sec.client import Fetch, SECClient
from tailwatch.sources.sec.index import Filing, parse_master_index, verify_filing


class SECJournal:
    def __init__(self, engine: Engine, store: ObjectStore) -> None:
        self.engine, self.store = engine, store

    def record_fetch(self, fetch: Fetch) -> None:
        with self.engine.begin() as connection:
            EvidenceRepository(connection).capture(self.store, self.store.get(fetch.digest))
            connection.execute(
                text("""
              INSERT INTO sec_fetches (url,status,digest,requested_at,received_at)
              VALUES (:url,:status,:digest,:requested,:received)
            """),
                {
                    "url": fetch.url,
                    "status": fetch.status,
                    "digest": fetch.digest,
                    "requested": fetch.requested_at,
                    "received": fetch.received_at,
                },
            )

    def discover(
        self,
        run_id: UUID,
        index_url: str,
        client: SECClient,
        forms: frozenset[str] = frozenset({"4", "4/A", "10-Q", "10-Q/A", "10-K", "10-K/A"}),
        ciks: frozenset[str] | None = None,
    ) -> None:
        if not forms or (ciks is not None and any(not re.fullmatch(r"\d{10}", c) for c in ciks)):
            raise ValueError("declare nonempty forms and canonical ten-digit CIKs")
        if client.source.kind == "live" and not ciks:
            raise ValueError("live discovery requires an explicit tracked CIK universe")
        selection = {"forms": sorted(forms), "ciks": sorted(ciks) if ciks is not None else None}
        with self.engine.connect() as connection:
            existing = connection.execute(
                text("SELECT index_url,source_id,selection FROM sec_runs WHERE run_id=:id"),
                {"id": run_id},
            ).first()
            if existing:
                if (
                    existing.index_url != index_url
                    or existing.source_id != client.source.source_id
                    or existing.selection != selection
                ):
                    raise ValueError("run ID reused for different source, index, or selection")
                return  # Resume the frozen worklist, never silently replace a changed index.
        body, fetch = client.get(index_url)
        by_accession: dict[str, Filing] = {}
        for filing in parse_master_index(body):
            if filing.form not in forms or (ciks is not None and filing.indexed_cik not in ciks):
                continue
            previous = by_accession.get(filing.accession)
            if previous and (
                previous.form != filing.form or previous.filed_date != filing.filed_date
            ):
                raise ValueError("conflicting index metadata for accession")
            by_accession.setdefault(filing.accession, filing)
        selected = [f.model_dump(mode="json") for f in by_accession.values()]
        with self.engine.begin() as connection:
            repo = EvidenceRepository(connection)
            repo.register_source(client.source.source_id)
            repo.capture(self.store, body)
            connection.execute(
                text("""
              INSERT INTO sec_runs(run_id,source_id,index_url,index_digest,manifest,selection)
              VALUES (:id,:source,:url,:digest,CAST(:manifest AS jsonb),CAST(:selection AS jsonb))
            """),
                {
                    "id": run_id,
                    "source": client.source.source_id,
                    "url": index_url,
                    "digest": fetch.digest,
                    "manifest": json.dumps(selected),
                    "selection": json.dumps(selection),
                },
            )

    def resume(self, run_id: UUID, client: SECClient) -> dict[str, int | bool]:
        with self.engine.connect() as connection:
            run = (
                connection.execute(
                    text("SELECT source_id,manifest FROM sec_runs WHERE run_id=:id"), {"id": run_id}
                )
                .mappings()
                .one()
            )
            if run["source_id"] != client.source.source_id:
                raise ValueError("client source disagrees with frozen run")
            records = [Filing.model_validate(row) for row in run["manifest"]]
            completed = set(
                connection.execute(
                    text("SELECT accession FROM sec_downloads WHERE run_id=:id"), {"id": run_id}
                ).scalars()
            )
        for record in records:
            if record.accession in completed:
                continue
            try:
                body, fetch = client.get(record.url)
                accepted = verify_filing(body, record)
                # The acceptance stamp is metadata, not an asserted public publication time.
                if accepted > fetch.received_at:
                    raise ValueError("acceptance timestamp is in the future")
                with self.engine.begin() as connection:
                    EvidenceRepository(connection).capture(self.store, body)
                    connection.execute(
                        text("""
                      INSERT INTO sec_downloads VALUES (:run,:accession,:digest,:accepted,:seen)
                      ON CONFLICT DO NOTHING
                    """),
                        {
                            "run": run_id,
                            "accession": record.accession,
                            "digest": fetch.digest,
                            "accepted": accepted,
                            "seen": fetch.received_at,
                        },
                    )
                    connection.execute(
                        text("""
                      INSERT INTO sec_attempts(run_id,accession,outcome,detail)
                      VALUES (:run,:accession,'downloaded','raw filing captured')
                    """),
                        {"run": run_id, "accession": record.accession},
                    )
                completed.add(record.accession)
            except (OSError, ValueError) as error:
                with self.engine.begin() as connection:
                    connection.execute(
                        text("""
                      INSERT INTO sec_attempts(run_id,accession,outcome,detail)
                      VALUES (:run,:accession,:outcome,:detail)
                    """),
                        {
                            "run": run_id,
                            "accession": record.accession,
                            "outcome": "failed" if isinstance(error, OSError) else "quarantined",
                            "detail": str(error)[:500],
                        },
                    )
        return {
            "expected": len(records),
            "downloaded": len(completed),
            "complete": len(completed) == len(records),
        }
