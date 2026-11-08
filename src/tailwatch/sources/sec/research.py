"""Bounded live SEC research capture wired to durable quotas and raw journaling."""

import json
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, text

from tailwatch.operations.runtime import DurableBudget
from tailwatch.sources.objects import ObjectStore
from tailwatch.sources.policy import Mode, Source, authorize
from tailwatch.sources.sec.client import HTTPTransport, SECClient, Transport
from tailwatch.sources.sec.ingest import SECJournal


def capture_submissions(
    engine: Engine,
    source: Source,
    ciks: tuple[str, ...],
    store: ObjectStore,
    user_agent: str,
    *,
    transport: Transport | None = None,
) -> dict[str, Any]:
    if (
        not 1 <= len(ciks) <= 20
        or len(set(ciks)) != len(ciks)
        or any(not re.fullmatch(r"\d{10}", cik) for cik in ciks)
    ):
        raise ValueError("declare 1-20 distinct canonical CIKs")
    if source.kind != "live":
        raise ValueError("live research source required")
    authorize(source, mode=Mode.RESEARCH, capability="sec", at=datetime.now(UTC))
    # One SEC worker across processes/source aliases, retaining the 8rps limiter.
    with engine.connect() as lock:
        acquired = lock.execute(text("SELECT pg_try_advisory_lock(754019226) ")).scalar_one()
        lock.commit()
        if not acquired:
            raise ValueError("another SEC research worker is running")
        try:
            day = lock.execute(
                text("SELECT (clock_timestamp() AT TIME ZONE 'UTC')::date")
            ).scalar_one()
            lock.commit()
            journal = SECJournal(engine, store)
            client = SECClient(
                source=source,
                mode=Mode.RESEARCH,
                user_agent=user_agent,
                store=store,
                transport=transport if transport is not None else HTTPTransport(),
                record=journal.record_fetch,
                budget=DurableBudget(engine, source, day),
            )
            rows = []
            for cik in ciks:
                body, fetch = client.get(f"https://data.sec.gov/submissions/CIK{cik}.json")
                data = json.loads(body)
                if not isinstance(data, dict) or str(data.get("cik", "")).zfill(10) != cik:
                    raise ValueError("SEC response identity mismatch")
                filings = data.get("filings")
                if not isinstance(filings, dict) or not isinstance(data.get("name"), str):
                    raise ValueError("malformed SEC submissions metadata")
                recent = filings.get("recent")
                if not isinstance(recent, dict):
                    raise ValueError("malformed SEC recent filings")
                forms, accessions = recent.get("form", []), recent.get("accessionNumber", [])
                if (
                    not isinstance(forms, list)
                    or not isinstance(accessions, list)
                    or len(forms) != len(accessions)
                    or any(not isinstance(v, str) for v in forms + accessions)
                ):
                    raise ValueError("malformed SEC submissions columns")
                rows.append(
                    {
                        "cik": cik,
                        "name": data.get("name"),
                        "raw_digest": fetch.digest,
                        "received_at": fetch.received_at.isoformat(),
                        "recent_filings": len(forms),
                        "recent_form4": sum(form in {"4", "4/A"} for form in forms),
                    }
                )
            report = {
                "source_id": source.source_id,
                "companies": rows,
                "coverage_complete": False,
                "production_enabled": False,
                "note": "Current submissions metadata only; not historical coverage",
            }
            digest = store.put(json.dumps(report, sort_keys=True, separators=(",", ":")).encode())
            return report | {"report_hash": digest}
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(754019226)"))
            lock.commit()
