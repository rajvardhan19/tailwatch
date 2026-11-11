import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tailwatch.db.evidence import Fact
from tailwatch.entities.history import CompanyHistory, SecurityHistory
from tailwatch.entities.repository import EntityRepository, read_history_fixture
from tailwatch.product.alerts import append_event, create_episode
from tailwatch.product.api import create_app, issue_key
from tailwatch.sources.objects import LocalObjectStore

pytestmark = pytest.mark.integration
START = datetime(2026, 1, 1, tzinfo=UTC)


def alert(conn, tenant, visitor, host, kinds, public):
    episode = create_episode(
        conn,
        tenant=tenant,
        visitor=visitor,
        host=host,
        model_hash="fixture",
        feature_run_hash="fixture",
        synthetic=True,
    )
    for kind in kinds:
        append_event(
            conn,
            tenant=tenant,
            episode=episode,
            kind=kind,
            actor="fixture",
            body={"internal": {"score": "analyst-only"}, "public": public},
        )
    return str(episode)


def test_pair_company_single_alert_views_and_analyst_outcomes(database):
    tenant, other = uuid4(), uuid4()
    a, b, c = uuid4(), uuid4(), uuid4()
    with database.begin() as conn:
        for ident in (tenant, other):
            conn.execute(text("INSERT INTO tenants VALUES(:id,'views-fixture')"), {"id": ident})
        customer = {"Authorization": "Bearer " + issue_key(conn, tenant, "customer")}
        analyst = {"Authorization": "Bearer " + issue_key(conn, tenant, "analyst")}
        outsider = {"Authorization": "Bearer " + issue_key(conn, other, "customer")}
        approved = alert(conn, tenant, a, b, ("draft", "review", "approved"), {"headline": "AB"})
        draft = alert(conn, tenant, c, a, ("draft",), {"headline": "CA"})
    client = TestClient(create_app(database))

    assert client.get(f"/alerts/{approved}", headers=customer).json()["outcome"] == "pending"
    for path, headers in ((f"/alerts/{draft}", customer), (f"/alerts/{approved}", outsider)):
        assert client.get(path, headers=headers).status_code == 404
    reverse = client.get(f"/pairs/{b}/{a}", headers=customer).json()
    assert [(i["episode_id"], i["direction"]) for i in reverse["items"]] == [(approved, "reverse")]
    assert client.get(f"/pairs/{a}/{a}", headers=customer).status_code == 422
    signals = client.get(f"/companies/{a}/signals", headers=customer).json()["items"]
    assert [(s["episode_id"], s["role"], s["counterpart"]) for s in signals] == [
        (approved, "visitor", str(b))
    ]
    assert "analyst-only" not in client.get(f"/companies/{a}/signals", headers=customer).text

    assert client.get("/analyst/alerts", headers=customer).status_code == 403
    queue = client.get("/analyst/alerts", headers=analyst, params={"state": "draft"}).json()
    assert [i["episode_id"] for i in queue["items"]] == [draft]
    assert queue["items"][0]["body"]["internal"] == {"score": "analyst-only"}
    detail = client.get(f"/analyst/alerts/{approved}", headers=analyst).json()
    assert detail["chain_verified"] is True and len(detail["items"]) == 3

    outcome = {"state": "announced", "note": "Checked filing", "human_confirmed": True}
    path = f"/analyst/alerts/{approved}/outcome"
    assert client.post(path, headers=customer, json=outcome).status_code == 403
    assert client.post(path, headers=analyst, json=outcome).status_code == 409
    early = outcome | {"state": "no_deal", "evidence": ["label-review"], "coverage_complete": True}
    assert client.post(path, headers=analyst, json=early).status_code == 409
    announced = outcome | {"evidence": ["press-release:fixture"]}
    assert client.post(path, headers=analyst, json=announced).status_code == 200
    assert (
        client.post(f"/analyst/alerts/{draft}/outcome", headers=analyst, json=announced).status_code
        == 409
    )
    assert client.get(f"/alerts/{approved}", headers=customer).json()["outcome"] == "announced"
    review = {"kind": "superseded", "note": "Correction pending", "human_confirmed": True}
    assert (
        client.post(f"/analyst/alerts/{approved}/review", headers=analyst, json=review).status_code
        == 200
    )
    latest = client.get(f"/analyst/alerts/{approved}", headers=analyst).json()["items"][-1]
    # Supersession carries the approved public content, never outcome text.
    assert latest["kind"] == "superseded" and latest["body"]["public"] == {"headline": "AB"}
    assert client.get(f"/alerts/{approved}", headers=customer).status_code == 404


def append_history(repo, store, source, history):
    batch = uuid4()
    body = history.model_dump_json().encode()
    repo.evidence.register_source(source)
    repo.evidence.begin_snapshot(
        snapshot_id=batch,
        source_id=source,
        created_at=START,
        watermark=START,
        expected_records=1,
        manifest_hash=hashlib.sha256(body).hexdigest(),
    )
    repo.append(
        Fact(
            fact_id=uuid4(),
            source_id=source,
            snapshot_id=batch,
            logical_key=f"entity:{history.entity_id}",
            revision=1,
            digest=repo.evidence.capture(store, body),
            evidence_locator="document",
            parser_version="v1",
            ingested_at=START,
            processed_at=START,
            valid_from=history.periods[0].start,
            payload=history.model_dump(mode="json"),
        )
    )
    repo.evidence.publish(batch, START)


def security(company: UUID, start: str, end: str | None) -> SecurityHistory:
    period = {"start": start, "ticker": "SYN", "exchange": "XNAS", "currency": "USD"}
    return SecurityHistory(
        entity_id=uuid4(),
        company_id=company,
        periods=[period | {"end": end, "active": True}],
    )


def test_ticker_resolution_is_dated_and_refuses_ambiguity(database, tmp_path):
    source = "api-" + uuid4().hex
    template = read_history_fixture(Path("tests/fixtures/entities/company.json"))
    first = CompanyHistory.model_validate(template.model_dump() | {"entity_id": uuid4()})
    second = CompanyHistory.model_validate(template.model_dump() | {"entity_id": uuid4()})
    store = LocalObjectStore(tmp_path)
    tenant = uuid4()
    with database.begin() as conn:
        repo = EntityRepository(conn)
        for history in (
            first,
            second,
            security(first.entity_id, "2026-01-01T00:00:00Z", "2026-01-10T00:00:00Z"),
            security(second.entity_id, "2026-01-10T00:00:00Z", None),
            security(second.entity_id, "2026-01-20T00:00:00Z", "2026-01-21T00:00:00Z"),
        ):
            append_history(repo, store, source, history)
        conn.execute(text("INSERT INTO tenants VALUES(:id,'resolve-fixture')"), {"id": tenant})
        auth = {"Authorization": "Bearer " + issue_key(conn, tenant, "customer")}
    client = TestClient(create_app(database, reference_source=source))

    def resolve(at: str):
        return client.get(
            "/companies/resolve",
            headers=auth,
            params={"ticker": "SYN", "exchange": "XNAS", "at": at},
        )

    assert resolve("2026-01-05T00:00:00Z").json()["company_id"] == str(first.entity_id)
    assert resolve("2026-01-15T00:00:00Z").json()["company_id"] == str(second.entity_id)
    assert resolve("2026-01-20T12:00:00Z").status_code == 409
    assert resolve("2025-12-31T00:00:00Z").status_code == 404
    assert resolve("2026-01-05T00:00:00").status_code == 422
    assert resolve("2999-01-01T00:00:00Z").status_code == 422
    unconfigured = TestClient(create_app(database))
    params = {"ticker": "SYN", "exchange": "XNAS"}
    assert unconfigured.get("/companies/resolve", headers=auth, params=params).status_code == 503
