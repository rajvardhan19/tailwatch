import hashlib
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from tailwatch.db.evidence import EvidenceRepository
from tailwatch.domain.time import utc
from tailwatch.sources.objects import ObjectStore
from tailwatch.sources.sec.form4 import PARSER_VERSION, ParsedForm4, parse_form4
from tailwatch.sources.sec.index import Filing


def save_form4(connection: Connection, store: ObjectStore, raw: bytes, filing: ParsedForm4) -> str:
    if hashlib.sha256(raw).hexdigest() != filing.source_digest:
        raise ValueError("parsed filing does not match raw evidence")
    document_id = hashlib.sha256(
        f"{filing.accession}:{filing.source_digest}:{filing.parser_version}".encode()
    ).hexdigest()
    EvidenceRepository(connection).capture(store, raw)
    payload = filing.model_dump(mode="json")
    inserted = connection.execute(
        text("""
      INSERT INTO ownership_documents(document_id,accession,source_digest,parser_version,
                                      first_seen_at,parsed)
      VALUES (:id,:accession,:digest,:parser,:seen,CAST(:parsed AS jsonb))
      ON CONFLICT DO NOTHING RETURNING document_id
    """),
        {
            "id": document_id,
            "accession": filing.accession,
            "digest": filing.source_digest,
            "parser": filing.parser_version,
            "seen": filing.available_at,
            "parsed": json.dumps(payload),
        },
    ).first()
    if inserted is None:
        existing = connection.execute(
            text("SELECT parsed FROM ownership_documents WHERE document_id=:id"),
            {"id": document_id},
        ).scalar_one()
        # A later observation of identical bytes is not a new transaction or parser result.
        expected = {k: v for k, v in payload.items() if k != "available_at"}
        if {k: v for k, v in existing.items() if k != "available_at"} != expected:
            raise ValueError("parser output changed without a new parser version")
        return document_id
    for row in filing.transactions:
        connection.execute(
            text("""
          INSERT INTO ownership_rows VALUES (:id,:row,CAST(:payload AS jsonb))
        """),
            {"id": document_id, "row": row.row_id, "payload": row.model_dump_json()},
        )
    return document_id


def known_form4s(connection: Connection, cutoff: datetime) -> list[ParsedForm4]:
    records = connection.execute(
        text("""
      SELECT parsed, greatest(first_seen_at,recorded_at) AS known_at
      FROM ownership_documents WHERE greatest(first_seen_at,recorded_at) <= :cutoff
      ORDER BY accession,parser_version
    """),
        {"cutoff": utc(cutoff)},
    ).mappings()
    return [
        ParsedForm4.model_validate(row["parsed"] | {"available_at": row["known_at"]})
        for row in records
    ]


def parse_downloaded_run(engine: Engine, store: ObjectStore, run_id: UUID) -> dict[str, int | bool]:
    """A raw-complete index remains parse-incomplete until every selected Form 4 succeeds."""
    with engine.connect() as connection:
        manifest = connection.execute(
            text("SELECT manifest FROM sec_runs WHERE run_id=:id"), {"id": run_id}
        ).scalar_one()
        downloads = {
            r["accession"]: dict(r)
            for r in connection.execute(
                text("SELECT * FROM sec_downloads WHERE run_id=:id"), {"id": run_id}
            ).mappings()
        }
    records = [Filing.model_validate(row) for row in manifest if row["form"] in {"4", "4/A"}]
    parsed = 0
    for record in records:
        observed = downloads.get(record.accession)
        if observed is None:
            continue
        try:
            raw = store.get(observed["digest"])
            filing = parse_form4(
                raw,
                accession=record.accession,
                filed_date=record.filed_date,
                available_at=observed["first_seen_at"],
            )
            if filing.form != record.form:
                raise ValueError("XML form type disagrees with frozen index")
            with engine.begin() as connection:
                save_form4(connection, store, raw, filing)
            parsed += 1
            outcome, detail = "parsed", "ownership rows stored"
        except (OSError, ValueError) as error:
            outcome, detail = "quarantined", str(error)[:500]
        with engine.begin() as connection:
            connection.execute(
                text("""
              INSERT INTO ownership_parse_attempts(run_id,accession,parser_version,outcome,detail)
              VALUES (:id,:accession,:parser,:outcome,:detail)
            """),
                {
                    "id": run_id,
                    "accession": record.accession,
                    "parser": PARSER_VERSION,
                    "outcome": outcome,
                    "detail": detail,
                },
            )
    return {"expected": len(records), "parsed": parsed, "complete": parsed == len(records)}
