from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

from tailwatch.sources.objects import LocalObjectStore
from tailwatch.sources.policy import Mode, Source, load_manifest
from tailwatch.sources.sec.client import RateLimiter, Response, SECClient
from tailwatch.sources.sec.ingest import SECJournal

pytestmark = pytest.mark.integration


def test_resumable_worklist_tracks_missing_and_quarantined_filings(
    database: Engine, tmp_path: Path
):
    source = Source.model_validate(
        load_manifest(Path("config/sources.fixture.json")).sources[0].model_dump()
        | {"source_id": "sec-fixture-" + uuid4().hex, "capabilities": ["sec"]}
    )
    body = Path("tests/fixtures/sec/master.idx").read_bytes()

    def filing(accession, form):
        return (
            f"<SEC-HEADER>\n<ACCEPTANCE-DATETIME>20260102120000\n"
            f"ACCESSION NUMBER: {accession}\nCONFORMED SUBMISSION TYPE: {form}\n"
            "</SEC-HEADER>\n"
        ).encode()

    first = filing("0000000001-26-000001", "4")
    second = filing("0000000002-26-000002", "4/A")

    class Transport:
        responses = [
            Response(200, body, {}),
            Response(200, first, {}),
            Response(200, b"truncated filing", {}),
            Response(200, second, {}),
        ]
        calls = 0

        def get(self, url, headers):
            self.calls += 1
            return self.responses.pop(0)

    transport = Transport()
    store = LocalObjectStore(tmp_path)
    journal = SECJournal(database, store)
    client = SECClient(
        source=source,
        mode=Mode.FIXTURE,
        user_agent="Fixture Test test@example.invalid",
        store=store,
        transport=transport,
        record=journal.record_fetch,
        limiter=RateLimiter(sleep=lambda _: None),
    )
    run = uuid4()
    url = "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR1/master.20260102.idx"
    journal.discover(run, url, client)
    assert journal.resume(run, client) == {"expected": 2, "downloaded": 1, "complete": False}
    # A fresh journal resumes persisted successes without refetching the index/first filing.
    resumed = SECJournal(database, store)
    resumed.discover(run, url, client)
    assert resumed.resume(run, client) == {"expected": 2, "downloaded": 2, "complete": True}
    assert resumed.resume(run, client)["complete"] is True
    assert transport.calls == 4
    with database.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM sec_attempts WHERE run_id=:id AND outcome='quarantined'"
                ),
                {"id": run},
            ).scalar_one()
            == 1
        )
