import json
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tailwatch.product.api import create_app, issue_key
from tailwatch.product.live import SEC_WORKER_LOCK, LiveFeeds
from tailwatch.sources import airtraffic
from tailwatch.sources.objects import LocalObjectStore
from tailwatch.sources.policy import Manifest, load_manifest
from tailwatch.sources.sec.client import CURRENT_FORM4_URL, Response

pytestmark = pytest.mark.integration
LIVE = load_manifest(Path("config/sources.live.json"))
AGENT = "Tailwatch integration test ops@example.invalid"
FEED = Path("tests/fixtures/sec/current_form4.atom").read_bytes()


def isolated(max_requests: int) -> Manifest:
    """Unique source IDs keep durable daily budgets independent between tests."""
    suffix = uuid4().hex[:8]
    sources = tuple(
        s.model_copy(update={"source_id": f"{s.source_id}-{suffix}", "max_requests": max_requests})
        for s in LIVE.sources
    )
    return Manifest(
        version=1, required_sources=tuple(s.source_id for s in sources), sources=sources
    )


def payload(_: str) -> bytes:
    return json.dumps(
        {"ac": [{"hex": "abc123", "lat": 40.7, "lon": -73.9, "alt_baro": 30000, "seen_pos": 1}]}
    ).encode()


def test_live_routes_require_a_key_and_hide_unconfigured_feeds(database, tmp_path):
    tenant = uuid4()
    with database.begin() as conn:
        conn.execute(text("INSERT INTO tenants VALUES(:id,'live-fixture')"), {"id": tenant})
        auth = {"Authorization": "Bearer " + issue_key(conn, tenant, "customer")}
    plain = TestClient(create_app(database))
    assert plain.get("/live/status").json() == {"enabled": False}
    assert plain.get("/live/traffic", headers=auth).status_code == 404
    feeds = LiveFeeds(
        database,
        isolated(100),
        LocalObjectStore(tmp_path),
        None,
        hubs=airtraffic.HUBS[:1],
        air_get=payload,
        hub_interval=0.01,
    )
    client = TestClient(create_app(database, live=feeds))
    assert client.get("/live/status").json() == {
        "enabled": True,
        "traffic": True,
        "filings": False,
        "open": False,
    }
    assert client.get("/live/traffic").status_code == 401
    assert client.get("/live/filings", headers=auth).status_code == 404
    deadline = time.monotonic() + 5
    body = client.get("/live/traffic", headers=auth).json()
    while time.monotonic() < deadline and not body["aircraft"]:
        time.sleep(0.05)
        body = client.get("/live/traffic", headers=auth).json()
    assert len(body["aircraft"]) == 1 and "abc123" not in json.dumps(body)
    # Local view (loopback-only servers) renders feeds without a key; nothing else opens.
    local = TestClient(create_app(database, live=feeds, live_local_view=True))
    assert local.get("/live/status").json()["open"] is True
    assert local.get("/live/traffic").status_code == 200
    assert local.get("/alerts").status_code == 401
    feeds.stop()


def test_durable_budget_exhaustion_pauses_the_air_feed(database, tmp_path):
    requested: list[str] = []

    def get(url: str) -> bytes:
        requested.append(url)
        return payload(url)

    feeds = LiveFeeds(
        database,
        isolated(2),
        LocalObjectStore(tmp_path),
        None,
        hubs=airtraffic.HUBS[:3],
        air_get=get,
        hub_interval=0.01,
    )
    feeds.traffic()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and feeds.traffic()["status"] != "paused_policy":
        time.sleep(0.05)
    feeds.stop()
    assert feeds.traffic()["status"] == "paused_policy"
    assert len(requested) == 2  # the third hub was refused before any request


class FakeSEC:
    def __init__(self) -> None:
        self.urls: list[str] = []
        folder = "https://www.sec.gov/Archives/edgar/data/1/000000000126000001"
        later = "https://www.sec.gov/Archives/edgar/data/3/000000000126000002"
        listing = json.dumps({"directory": {"item": [{"name": "doc4.xml"}]}}).encode()
        self.bodies = {
            CURRENT_FORM4_URL: FEED,
            f"{folder}/index.json": listing,
            f"{folder}/doc4.xml": Path("tests/fixtures/sec/form4.xml").read_bytes(),
            f"{later}/index.json": json.dumps({"directory": {"item": []}}).encode(),
        }

    def get(self, url, headers):
        self.urls.append(url)
        assert headers["User-Agent"] == AGENT
        return Response(200, self.bodies[url], {})


def test_sec_poll_journals_responses_parses_details_and_yields_the_worker_lock(database, tmp_path):
    transport = FakeSEC()
    feeds = LiveFeeds(
        database, isolated(50), LocalObjectStore(tmp_path), AGENT, sec_transport=transport
    )
    with database.connect() as other:
        other.execute(text("SELECT pg_advisory_lock(:key)"), {"key": SEC_WORKER_LOCK})
        feeds._poll_sec()
        assert feeds.filings()["status"] == "waiting_for_other_sec_worker"
        assert transport.urls == []
        other.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": SEC_WORKER_LOCK})
    feeds._poll_sec()
    result = feeds.filings()
    feeds.stop()
    assert result["status"] == "live"
    by_accession = {f["accession"]: f for f in result["filings"]}
    parsed = by_accession["0000000001-26-000001"]["details"]
    assert parsed["status"] == "parsed" and parsed["headline_code"] == "P"
    # A filing without a single ownership XML is reported unavailable, not guessed.
    assert by_accession["0000000001-26-000002"]["details"]["status"] == "unavailable"
    with database.connect() as conn:
        journaled = conn.execute(
            text("SELECT count(*) FROM sec_fetches WHERE url = ANY(:urls)"),
            {"urls": list(transport.bodies)},
        ).scalar_one()
    assert journaled == len(transport.urls) == 4
