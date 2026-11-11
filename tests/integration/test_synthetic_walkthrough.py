"""End-to-end operator walkthrough on synthetic data, through the CLI and API only."""

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from tailwatch.cli import main
from tailwatch.product.api import create_app
from tailwatch.product.messages import signer_from_env

pytestmark = pytest.mark.integration


def run(capsys, *args: str) -> dict:
    assert main(list(args)) == 0
    return json.loads(capsys.readouterr().out)


def mailed_link(path: Path, label: str) -> tuple[str, str]:
    line = next(line for line in path.read_text().splitlines() if "/confirm#" in line)
    assert line.startswith(label)
    params = parse_qs(urlsplit(line.split(": ", 1)[1]).fragment)
    return params["id"][0], params["token"][0]


def test_operator_walkthrough_from_frozen_features_to_reviewed_delivery(
    database, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("TAILWATCH_DATABASE_URL", os.environ["TAILWATCH_TEST_DATABASE_URL"])
    monkeypatch.setenv("TAILWATCH_LINK_SECRET", "walkthrough-only-link-secret-0123456789")
    monkeypatch.setenv("TAILWATCH_PUBLIC_URL", "http://127.0.0.1:8765")
    objects, mail = str(tmp_path / "objects"), tmp_path / "mail"
    client = TestClient(create_app(database, signer=signer_from_env()))

    # Operator provisions a workspace; only the recipient's mailbox gets a link.
    workspace = run(
        capsys,
        "provision-workspace",
        "--name",
        "Synthetic walkthrough",
        "--email",
        "walkthrough@example.invalid",
        "--output",
        str(tmp_path / "workspace.json"),
    )
    tenant = workspace["tenant_id"]
    customer = {
        "Authorization": "Bearer "
        + json.loads((tmp_path / "workspace.json").read_text())["customer_key"]
    }
    analyst_key = run(
        capsys,
        "issue-key",
        "--tenant",
        tenant,
        "--scope",
        "analyst",
        "--output",
        str(tmp_path / "analyst.json"),
    )
    analyst = {
        "Authorization": "Bearer " + json.loads((tmp_path / "analyst.json").read_text())["key"]
    }
    assert run(capsys, "send-verifications", "--mail-dir", str(mail))["outcomes"]["sent"] >= 1
    message, token = mailed_link(
        mail / f"{workspace['verification_message_id']}.txt", "Confirm within 24 hours"
    )
    assert client.post(f"/verifications/{message}/confirm", json={"token": token}).status_code == (
        200
    )

    # Research: retain a synthetic candidate and draft from a frozen feature run.
    model = run(
        capsys,
        "fit-candidate",
        "config/research.synthetic.json",
        "--objects",
        objects,
        "--feature",
        "flight",
    )
    assert main(["research-demo"]) == 0
    (tmp_path / "run.json").write_text(capsys.readouterr().out)
    drafted = run(
        capsys,
        "draft-alerts",
        "--run",
        str(tmp_path / "run.json"),
        "--model-hash",
        model["model_hash"],
        "--objects",
        objects,
        "--tenant",
        tenant,
        "--threshold",
        "0",
        "--budget",
        "1",
        "--actor",
        "walkthrough",
        "--visits",
        str(tmp_path / "run.json"),
    )
    [episode] = drafted["drafted"]

    # Nothing can be queued until a human analyst approves the draft.
    enqueue = ("outbox-enqueue", "--tenant", tenant, "--actor", "walkthrough")
    assert run(capsys, *enqueue)["enqueued"] == []
    queue = client.get("/analyst/alerts", headers=analyst).json()["items"]
    assert [item["episode_id"] for item in queue] == [episode]
    for kind in ("review", "approved"):
        decision = {"kind": kind, "note": "Synthetic evidence checked", "human_confirmed": True}
        response = client.post(f"/analyst/alerts/{episode}/review", headers=analyst, json=decision)
        assert response.status_code == 200
    [delivery] = run(capsys, *enqueue)["enqueued"]
    assert run(capsys, "outbox-deliver", "--mail-dir", str(mail))["outcomes"]["sent"] >= 1
    sent = (mail / f"{delivery}.txt").read_text()
    assert sent.startswith("To: walkthrough@example.invalid\nSubject: Tailwatch synthetic test")
    assert "Calibrated probability: unavailable" in sent

    # Customer views, track record and independently retained chain heads.
    public = client.get(f"/alerts/{episode}", headers=customer).json()["public"]
    assert public["synthetic"] and public["calibrated_probability"] is None
    assert [v["destination"]["code"] for v in public["visits"]] == ["SYN-HBR"]
    assert public["conditions"]["intensity"]["status"] == "insufficient_history"
    track = client.get("/track-record", headers=customer).json()["items"]
    assert [(i["episode_id"], i["outcome"]) for i in track] == [(episode, "pending")]
    retained = run(capsys, "checkpoint-alerts", "--objects", str(tmp_path / "independent"))
    verified = run(
        capsys,
        "verify-checkpoints",
        retained["manifest"],
        "--objects",
        str(tmp_path / "independent"),
    )
    assert verified["mismatched"] == []

    # The recipient unsubscribes from the delivered message; the operator revokes access.
    identifier, unsubscribe = mailed_link(mail / f"{delivery}.txt", "Unsubscribe")
    assert identifier == delivery
    response = client.post(f"/deliveries/{delivery}/unsubscribe", json={"token": unsubscribe})
    assert response.status_code == 200
    assert client.get("/subscriptions", headers=customer).json()["items"][0]["active"] is False
    run(capsys, "revoke-key", analyst_key["key_hash"])
    assert client.get("/analyst/alerts", headers=analyst).status_code == 401
