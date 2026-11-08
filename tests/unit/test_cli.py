import json
import subprocess
import sys


def test_fixture_cli_is_explicitly_synthetic() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tailwatch.cli", "fixture"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["synthetic"] is True
    assert payload["delivery_enabled"] is False
    assert payload["model_status"] == "not_activated"


def test_help_needs_no_credentials() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tailwatch.cli", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "fixture" in result.stdout
