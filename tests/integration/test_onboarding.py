import json
import stat
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tailwatch.product.api import create_app, issue_key
from tailwatch.product.delivery import (
    FileSender,
    request_verification,
    send_verification_one,
    subscribe,
)
from tailwatch.product.messages import LinkSigner
from tailwatch.product.onboarding import provision_workspace

pytestmark = pytest.mark.integration
SIGNER = LinkSigner("integration-only-link-secret-0123456789", "http://127.0.0.1:8765")


def mailed_link(path: Path) -> tuple[str, str, str]:
    link = next(line for line in path.read_text().splitlines() if "/confirm#" in line)
    params = parse_qs(urlsplit(link.split(": ", 1)[1]).fragment)
    return params["action"][0], params["id"][0], params["token"][0]


def drain_verifications(database, mail: Path) -> None:
    sender = FileSender(mail)
    while send_verification_one(database, sender, signer=SIGNER, production_enabled=False) != (
        "idle"
    ):
        pass


def test_operator_provisioning_tokens_and_tenant_subscription_isolation(database, tmp_path):
    path = tmp_path / "credentials.json"
    result = provision_workspace(
        database, name="Synthetic operator fixture", email="fixture@example.invalid", output=path
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    secrets = json.loads(path.read_text())
    assert secrets["customer_key"] not in json.dumps(result)
    # Recipient secrets never reach the operator: only the recipient can confirm.
    assert not any("token" in name for name in secrets)
    with pytest.raises(FileExistsError):
        provision_workspace(
            database, name="must not replace", email="fixture@example.invalid", output=path
        )
    client = TestClient(create_app(database, signer=SIGNER))
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

    mail = tmp_path / "mail"
    drain_verifications(database, mail)
    message = secrets["verification_message_id"]
    mailed = (mail / f"{message}.txt").read_text()
    assert mailed.startswith("To: fixture@example.invalid\n")
    action, identifier, token = mailed_link(mail / f"{message}.txt")
    assert (action, identifier) == ("verify", message)
    body = {"token": token}
    unconfigured = TestClient(create_app(database))
    assert unconfigured.post(f"/verifications/{message}/confirm", json=body).status_code == 503
    # Tokens are bound to their purpose and message.
    assert client.post(f"/deliveries/{message}/unsubscribe", json=body).status_code == 404
    forged = {"token": ("A" if token[0] != "A" else "B") + token[1:]}
    assert client.post(f"/verifications/{message}/confirm", json=forged).status_code == 404
    assert client.post(f"/verifications/{message}/confirm", json=body).status_code == 200
    assert client.post(f"/verifications/{message}/confirm", json=body).status_code == 404
    assert client.get("/subscriptions", headers=auth).json()["items"][0]["verified"] is True
    with database.begin() as conn, pytest.raises(ValueError):
        request_verification(conn, UUID(sub), "integration-test")


def test_verification_requests_are_rate_limited_and_expire(database, tmp_path):
    tenant = uuid4()
    with database.begin() as conn:
        conn.execute(text("INSERT INTO tenants VALUES(:id,'verify-limit')"), {"id": tenant})
        sub, _, _ = subscribe(conn, tenant, "limit@example.invalid")
        messages = [request_verification(conn, sub, "integration-test") for _ in range(3)]
        with pytest.raises(ValueError, match="limit"):
            request_verification(conn, sub, "integration-test")
    mail = tmp_path / "mail"
    drain_verifications(database, mail)
    _, _, token = mailed_link(mail / f"{messages[0]}.txt")
    with database.begin() as conn:
        conn.execute(
            text(
                "UPDATE verification_messages SET created_at=clock_timestamp()-interval "
                "'25 hours' WHERE message_id=:id"
            ),
            {"id": messages[0]},
        )
    client = TestClient(create_app(database, signer=SIGNER))
    assert (
        client.post(f"/verifications/{messages[0]}/confirm", json={"token": token}).status_code
        == 404
    )
    _, _, fresh = mailed_link(mail / f"{messages[1]}.txt")
    assert (
        client.post(f"/verifications/{messages[1]}/confirm", json={"token": fresh}).status_code
        == 200
    )
    # The legacy operator-held token path still expires after 24 hours.
    with database.begin() as conn:
        expired, legacy, _ = subscribe(conn, tenant, "expired@example.invalid")
        conn.execute(
            text(
                "UPDATE subscriptions SET created_at=clock_timestamp()-interval '25 hours' "
                "WHERE subscription_id=:id"
            ),
            {"id": expired},
        )
    assert (
        client.post(f"/subscriptions/{expired}/verify", json={"token": legacy}).status_code == 404
    )


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
