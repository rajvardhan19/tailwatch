import json
import stat
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tailwatch.product.api import create_app, issue_key
from tailwatch.product.delivery import subscribe
from tailwatch.product.onboarding import provision_workspace

pytestmark = pytest.mark.integration


def test_operator_provisioning_tokens_and_tenant_subscription_isolation(database, tmp_path):
    path = tmp_path / "credentials.json"
    result = provision_workspace(
        database, name="Synthetic operator fixture", email="fixture@example.invalid", output=path
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    secrets = json.loads(path.read_text())
    assert secrets["customer_key"] not in json.dumps(result)
    with pytest.raises(FileExistsError):
        provision_workspace(
            database, name="must not replace", email="fixture@example.invalid", output=path
        )
    client = TestClient(create_app(database))
    auth = {"Authorization": "Bearer " + secrets["customer_key"]}
    listing = client.get("/subscriptions", headers=auth).json()
    assert len(listing["items"]) == 1 and listing["items"][0]["verified"] is False
    assert "hash" not in json.dumps(listing) and "token" not in json.dumps(listing)
    other = uuid4()
    with database.begin() as conn:
        conn.execute(text("INSERT INTO tenants VALUES(:id,'other')"), {"id": other})
        key = issue_key(conn, other, "customer")
    sub = secrets["subscription_id"]
    assert (
        client.delete(
            f"/subscriptions/{sub}", headers={"Authorization": "Bearer " + key}
        ).status_code
        == 404
    )
    body = {"token": secrets["verification_token"]}
    assert client.post(f"/subscriptions/{sub}/verify", json=body).status_code == 200
    assert client.post(f"/subscriptions/{sub}/verify", json=body).status_code == 404
    assert (
        client.post(f"/subscriptions/{sub}/unsubscribe", json={"token": "x" * 43}).status_code
        == 404
    )
    assert (
        client.post(
            f"/subscriptions/{sub}/unsubscribe", json={"token": secrets["unsubscribe_token"]}
        ).status_code
        == 200
    )
    assert client.get("/subscriptions", headers=auth).json()["items"][0]["active"] is False
    with database.begin() as conn:
        expired, token, _ = subscribe(conn, UUID(secrets["tenant_id"]), "expired@example.invalid")
        conn.execute(
            text(
                "UPDATE subscriptions SET created_at=clock_timestamp()-interval '25 hours' "
                "WHERE subscription_id=:id"
            ),
            {"id": expired},
        )
    assert client.post(f"/subscriptions/{expired}/verify", json={"token": token}).status_code == 404


def test_invalid_destination_rolls_back_provisioning_and_removes_secret_file(database, tmp_path):
    path = tmp_path / "failed.json"
    with pytest.raises(ValueError):
        provision_workspace(database, name="bad-email-fixture", email="invalid", output=path)
    assert not path.exists()
    with database.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM tenants WHERE name='bad-email-fixture'")
            ).scalar_one()
            == 0
        )
