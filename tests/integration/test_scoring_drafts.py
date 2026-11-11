import json
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tailwatch.cli import main
from tailwatch.demo import research_demo
from tailwatch.product.api import create_app, issue_key

pytestmark = pytest.mark.integration


def test_scored_drafts_stay_private_until_review_and_never_redraft_an_episode(
    database, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("TAILWATCH_DATABASE_URL", os.environ["TAILWATCH_TEST_DATABASE_URL"])
    objects = str(tmp_path / "objects")
    fit = ["fit-candidate", "config/research.synthetic.json", "--objects", objects]
    assert main([*fit, "--feature", "flight"]) == 0
    model = json.loads(capsys.readouterr().out)
    assert model["promotion_allowed"] is False and model["synthetic"] is True
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(research_demo()))
    tenant = uuid4()
    with database.begin() as conn:
        conn.execute(text("INSERT INTO tenants VALUES(:id,'scoring-fixture')"), {"id": tenant})
        customer = issue_key(conn, tenant, "customer")
        analyst = issue_key(conn, tenant, "analyst")
    draft = [
        "draft-alerts",
        "--run",
        str(run_path),
        "--model-hash",
        model["model_hash"],
        "--objects",
        objects,
        "--tenant",
        str(tenant),
        "--threshold",
        "0",
        "--budget",
        "5",
        "--actor",
        "integration-test",
    ]
    assert main(draft) == 0
    first = json.loads(capsys.readouterr().out)
    assert (first["scored"], first["unscorable"], first["selected"]) == (2, 0, 1)
    assert len(first["drafted"]) == 1 and first["approval"] == "human_required"
    assert main(draft) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["drafted"] == [] and second["existing_episodes"] == first["drafted"]

    client = TestClient(create_app(database))
    auth = {"Authorization": f"Bearer {customer}"}
    assert client.get("/alerts", headers=auth).json()["items"] == []
    episode = first["drafted"][0]
    for kind in ("review", "approved"):
        response = client.post(
            f"/analyst/alerts/{episode}/review",
            headers={"Authorization": f"Bearer {analyst}"},
            json={"kind": kind, "note": "Checked synthetic evidence", "human_confirmed": True},
        )
        assert response.status_code == 200
    items = client.get("/alerts", headers=auth).json()["items"]
    assert [i["episode_id"] for i in items] == [episode]
    public = items[0]["public"]
    assert public["synthetic"] is True and public["calibrated_probability"] is None
    assert public["evidence"] == ["synthetic:visit:1"] and public["contributions"]
    assert "score_digest" not in json.dumps(items) and "experiment_hash" not in json.dumps(items)


def test_draft_command_rejects_tampered_runs_and_unknown_models(
    database, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("TAILWATCH_DATABASE_URL", os.environ["TAILWATCH_TEST_DATABASE_URL"])
    objects = str(tmp_path / "objects")
    assert main(["fit-candidate", "config/research.synthetic.json", "--objects", objects]) == 0
    model = json.loads(capsys.readouterr().out)["model_hash"]
    tampered = research_demo()
    tampered["run_hash"] = "0" * 64
    path = tmp_path / "run.json"
    path.write_text(json.dumps(tampered))
    common = ["--objects", objects, "--tenant", str(uuid4()), "--threshold", "0.5"]
    common += ["--budget", "1", "--actor", "integration-test", "--run", str(path)]
    for model_hash in (model, "f" * 64):
        with pytest.raises(SystemExit) as error:
            main(["draft-alerts", "--model-hash", model_hash, *common])
        assert error.value.code == 2
    assert "rejected" in capsys.readouterr().err
