import json
import os

import pytest

from tailwatch.cli import main

pytestmark = pytest.mark.integration


def test_fixture_job_replay_and_release_commands(database, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TAILWATCH_DATABASE_URL", os.environ["TAILWATCH_TEST_DATABASE_URL"])
    args = ["run-fixture-job", "--objects", str(tmp_path / "objects"), "--session", "2026-11-06"]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "complete"
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == first
    assert main(["release-kill", "--actor", "integration-test"]) == 0
    assert json.loads(capsys.readouterr().out)["killed"] is True
    assert main(["release-status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["killed"] is True and status["eligible_model"] is None
    with pytest.raises(SystemExit) as error:
        main(["release-activate", "missing-release", "--actor", "integration-test"])
    assert error.value.code == 2
    assert "rejected" in capsys.readouterr().err
