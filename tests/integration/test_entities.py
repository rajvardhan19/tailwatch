import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Connection
from sqlalchemy.exc import DBAPIError

from tailwatch.db.evidence import Fact
from tailwatch.entities.history import OfficeHistory, SecurityHistory
from tailwatch.entities.repository import AmbiguousTicker, EntityRepository, read_history_fixture
from tailwatch.sources.objects import LocalObjectStore

pytestmark = pytest.mark.integration
START = datetime(2026, 1, 1, tzinfo=UTC)


def append_history(repo, store, history, previous=None):
    batch = uuid4()
    body = history.model_dump_json().encode()
    repo.evidence.register_source("synthetic")
    repo.evidence.begin_snapshot(
        snapshot_id=batch,
        source_id="synthetic",
        created_at=START,
        watermark=START,
        expected_records=1,
        manifest_hash=hashlib.sha256(body).hexdigest(),
    )
    item = Fact(
        fact_id=uuid4(),
        source_id="synthetic",
        snapshot_id=batch,
        logical_key=f"entity:{history.entity_id}",
        revision=previous.revision + 1 if previous else 1,
        supersedes=previous.fact_id if previous else None,
        digest=repo.evidence.capture(store, body),
        evidence_locator="document",
        parser_version="v1",
        ingested_at=START,
        processed_at=START,
        valid_from=history.periods[0].start,
        payload=history.model_dump(mode="json"),
    )
    repo.append(item)
    repo.evidence.publish(batch, START)
    return item


def test_history_revisions_ticker_reuse_and_frozen_universe(connection: Connection, tmp_path: Path):
    repo = EntityRepository(connection)
    store = LocalObjectStore(tmp_path)
    company = read_history_fixture(Path("tests/fixtures/entities/company.json"))
    append_history(repo, store, company)
    security = SecurityHistory(
        entity_id=uuid4(),
        company_id=company.entity_id,
        periods=[
            {
                "start": START,
                "end": "2026-01-10T00:00:00Z",
                "ticker": "SYN",
                "exchange": "XNAS",
                "currency": "USD",
                "active": True,
            }
        ],
    )
    append_history(repo, store, security)
    cutoff = datetime.now(UTC)
    assert (
        repo.resolve_ticker("SYN", "XNAS", source_id="synthetic", cutoff=cutoff, effective_at=START)
        == security.entity_id
    )
    frozen = repo.universe(
        source_id="synthetic", cutoff=cutoff, effective_at=START, sector="synthetic"
    )
    assert json.loads(frozen[1])["members"] == [str(company.entity_id)]
    assert (
        json.loads(
            repo.universe(
                source_id="synthetic",
                cutoff=cutoff,
                effective_at=datetime(2026, 1, 11, tzinfo=UTC),
                sector="synthetic",
            )[1]
        )["members"]
        == []
    )
    replacement = SecurityHistory(
        entity_id=uuid4(),
        company_id=company.entity_id,
        periods=[
            {
                "start": "2026-01-10T00:00:00Z",
                "ticker": "SYN",
                "exchange": "XNAS",
                "currency": "USD",
                "active": True,
            }
        ],
    )
    append_history(repo, store, replacement)
    assert (
        repo.universe(source_id="synthetic", cutoff=cutoff, effective_at=START, sector="synthetic")
        == frozen
    )
    assert (
        repo.resolve_ticker(
            "SYN",
            "XNAS",
            source_id="synthetic",
            cutoff=datetime.now(UTC),
            effective_at=datetime(2026, 1, 11, tzinfo=UTC),
        )
        == replacement.entity_id
    )
    collision = SecurityHistory.model_validate(security.model_dump() | {"entity_id": uuid4()})
    append_history(repo, store, collision)
    with pytest.raises(AmbiguousTicker):
        repo.resolve_ticker(
            "SYN", "XNAS", source_id="synthetic", cutoff=datetime.now(UTC), effective_at=START
        )


def test_office_correction_preserves_earlier_view_and_company_fk(
    connection: Connection, tmp_path: Path
):
    repo = EntityRepository(connection)
    store = LocalObjectStore(tmp_path)
    company = read_history_fixture(Path("tests/fixtures/entities/company.json"))
    append_history(repo, store, company)
    office = OfficeHistory(
        entity_id=uuid4(),
        company_id=company.entity_id,
        periods=[
            {
                "start": START,
                "kind": "hq",
                "latitude": 40,
                "longitude": -74,
                "address": "Old HQ",
            }
        ],
    )
    first = append_history(repo, store, office)
    cutoff = datetime.now(UTC)
    corrected = office.model_dump()
    corrected["periods"][0]["address"] = "Corrected HQ"
    append_history(repo, store, OfficeHistory.model_validate(corrected), first)
    earlier = dict((h.entity_id, h) for _, h in repo.histories("synthetic", cutoff, START))
    assert earlier[office.entity_id].periods[0].address == "Old HQ"
    orphan = OfficeHistory.model_validate(
        office.model_dump() | {"entity_id": uuid4(), "company_id": uuid4()}
    )
    with pytest.raises(DBAPIError), connection.begin_nested():
        append_history(repo, store, orphan)
