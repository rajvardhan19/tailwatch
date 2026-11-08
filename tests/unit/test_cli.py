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


def test_bundled_development_report_runs_without_credentials(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tailwatch.cli",
            "research-report",
            "config/research.synthetic.json",
            "--objects",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["promotion_allowed"] is False
    digest = payload["report_hash"]
    report = json.loads((tmp_path / digest[:2] / digest).read_text())
    assert report["synthetic"] is True and report["coverage"]["missing_episodes"] == 0
