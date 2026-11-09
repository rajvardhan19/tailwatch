from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import Connection, text

from tailwatch.db.clock import database_now
from tailwatch.sources.objects import LocalObjectStore
from tailwatch.sources.sec.form4 import ParsedForm4, parse_form4
from tailwatch.sources.sec.ownership_store import known_form4s, save_form4

pytestmark = pytest.mark.integration


def test_versioned_ownership_storage_is_idempotent_and_not_backdated(
    connection: Connection, tmp_path
):
    raw = Path("tests/fixtures/sec/form4.xml").read_bytes()
    filing = parse_form4(
        raw,
        accession="0000000001-26-000001",
        filed_date=date(2026, 1, 2),
        available_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    store = LocalObjectStore(tmp_path)
    before = database_now(connection)
    identifier = save_form4(connection, store, raw, filing)
    assert save_form4(connection, store, raw, filing) == identifier
    assert (
        connection.execute(
            text("SELECT count(*) FROM ownership_rows WHERE document_id=:id"), {"id": identifier}
        ).scalar_one()
        == 1
    )
    assert all(f.source_digest != filing.source_digest for f in known_form4s(connection, before))
    assert any(
        f.source_digest == filing.source_digest
        for f in known_form4s(connection, database_now(connection))
    )
    changed = ParsedForm4.model_validate(filing.model_dump() | {"issuer_name": "Wrong"})
    with pytest.raises(ValueError, match="parser output changed"):
        save_form4(connection, store, raw, changed)
