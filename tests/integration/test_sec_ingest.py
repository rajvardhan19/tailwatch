from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

from tailwatch.sources.objects import LocalObjectStore
from tailwatch.sources.policy import Mode, PolicyDenied, Source, load_manifest
from tailwatch.sources.sec.client import RateLimiter, Response, SECClient
from tailwatch.sources.sec.ingest import SECJournal
from tailwatch.sources.sec.ownership_store import parse_downloaded_run

pytestmark = pytest.mark.integration


def test_resumable_worklist_tracks_missing_and_quarantined_filings(
    database: Engine, tmp_path: Path, monkeypatch
):
    source = Source.model_validate(
        load_manifest(Path("config/sources.fixture.json")).sources[0].model_dump()
        | {"source_id": "sec-fixture-" + uuid4().hex, "capabilities": ["sec"]}
    )
    body = Path("tests/fixtures/sec/master.idx").read_bytes()

    def filing(accession, form):
        header = (
            f"<SEC-HEADER>\n<ACCEPTANCE-DATETIME>20260102120000\n"
            f"ACCESSION NUMBER: {accession}\nCONFORMED SUBMISSION TYPE: {form}\n"
            "</SEC-HEADER>\n"
        ).encode()
        xml = (
            Path("tests/fixtures/sec/form4.xml")
            .read_bytes()
            .replace(
                b"<documentType>4</documentType>", f"<documentType>{form}</documentType>".encode()
            )
        )
        return header + b"<DOCUMENT><XML>" + xml + b"</XML></DOCUMENT>"

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
    assert parse_downloaded_run(database, store, run) == {
        "expected": 2,
        "parsed": 1,
        "complete": False,
    }

    def exhausted(url):
        raise PolicyDenied("daily quota exhausted")

    with monkeypatch.context() as patch:
        patch.setattr(client, "get", exhausted)
        with pytest.raises(PolicyDenied, match="quota"):
            journal.resume(run, client)
    # A fresh journal resumes persisted successes without refetching the index/first filing.
    resumed = SECJournal(database, store)
    resumed.discover(run, url, client)
    assert resumed.resume(run, client) == {"expected": 2, "downloaded": 2, "complete": True}
    assert resumed.resume(run, client)["complete"] is True
    assert transport.calls == 4
    assert parse_downloaded_run(database, store, run) == {
        "expected": 2,
        "parsed": 2,
        "complete": True,
    }
    assert parse_downloaded_run(database, store, run)["parsed"] == 2
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
    # Even a fully downloaded cursor must not hide lost/corrupted raw evidence.
    first_digest = store.put(first)
    retained = tmp_path / first_digest[:2] / first_digest
    retained.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="checksum"):
        resumed.resume(run, client)
    retained.unlink()
    with pytest.raises(FileNotFoundError):
        resumed.resume(run, client)
    assert transport.calls == 4
