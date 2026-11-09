import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from tailwatch.sources.objects import LocalObjectStore
from tailwatch.sources.policy import PolicyDenied, Source
from tailwatch.sources.sec.client import Response
from tailwatch.sources.sec.research import capture_submissions

pytestmark = pytest.mark.integration


def test_live_command_wiring_uses_journal_durable_limits_and_worker_lock(database, tmp_path):
    now = datetime.now(UTC)
    source = Source(
        source_id="sec-ci-" + uuid4().hex,
        kind="live",
        enabled=True,
        capabilities=("sec",),
        latency_seconds=1,
        max_requests=1,
        max_cost_microusd=0,
        permission={
            "reference": "synthetic transport only",
            "reviewed_by": "CI",
            "valid_from": now - timedelta(days=1),
            "valid_until": now + timedelta(days=1),
            "modes": ["research"],
            "retention": True,
            "derived_output": True,
        },
    )

    class Fake:
        calls = 0

        def get(self, url, headers):
            self.calls += 1
            return Response(
                200,
                json.dumps(
                    {
                        "cik": 1,
                        "name": "Synthetic Co",
                        "filings": {
                            "recent": {"form": ["4"], "accessionNumber": ["0000000001-26-000001"]}
                        },
                    }
                ).encode(),
                {},
            )

    fake = Fake()
    args = (
        database,
        source,
        ("0000000001",),
        LocalObjectStore(tmp_path),
        "Fixture test@example.invalid",
    )
    with database.connect() as lock:
        lock.execute(text("SELECT pg_advisory_lock(754019226)"))
        try:
            with pytest.raises(ValueError, match="another SEC"):
                capture_submissions(*args, transport=fake)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(754019226)"))
            lock.commit()
    assert fake.calls == 0
    result = capture_submissions(*args, transport=fake)
    assert result["companies"][0]["recent_form4"] == 1 and not result["coverage_complete"]
    with pytest.raises(PolicyDenied):
        capture_submissions(*args, transport=fake)
    assert fake.calls == 1
    with database.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM sec_fetches WHERE digest=:id"),
                {"id": result["companies"][0]["raw_digest"]},
            ).scalar_one()
            >= 1
        )
