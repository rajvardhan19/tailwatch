from datetime import UTC, datetime
from pathlib import Path

import pytest

from tailwatch.sources.objects import LocalObjectStore
from tailwatch.sources.policy import Mode, Source, load_manifest
from tailwatch.sources.sec.client import (
    HTTPTransport,
    RateLimiter,
    Response,
    SECClient,
    validate_url,
)
from tailwatch.sources.sec.index import acceptance_time, parse_master_index


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers):
        self.calls.append((url, headers))
        reply = self.responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def fixture_source():
    return Source.model_validate(
        load_manifest(Path("config/sources.fixture.json")).sources[0].model_dump()
        | {"capabilities": ["sec"]}
    )


def test_retries_record_raw_response_and_honor_contact(tmp_path):
    transport = FakeTransport(
        [Response(429, b"busy", {"Retry-After": "3"}), Response(200, b"index", {})]
    )
    delays, fetches = [], []
    client = SECClient(
        source=fixture_source(),
        mode=Mode.FIXTURE,
        user_agent="Synthetic Research test@example.invalid",
        store=LocalObjectStore(tmp_path),
        transport=transport,
        record=fetches.append,
        sleep=delays.append,
        limiter=RateLimiter(sleep=lambda _: None),
    )
    assert client.get("https://www.sec.gov/Archives/edgar/daily-index/test.idx")[0] == b"index"
    assert delays == [3]
    assert [f.status for f in fetches] == [429, 200]
    assert all(call[1]["User-Agent"].endswith("test@example.invalid") for call in transport.calls)


@pytest.mark.parametrize(
    "url",
    [
        "http://www.sec.gov/Archives/edgar/test",
        "https://evil.test/a",
        "https://www.sec.gov@evil.test/Archives/edgar/a",
        "https://www.sec.gov/Archives/edgar/../x",
        "https://www.sec.gov/Archives/edgar/%2e%2e/x",
    ],
)
def test_untrusted_urls_rejected(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_rate_limiter_reserves_shared_slots():
    now = [0.0]

    def advance(delay):
        now[0] += delay

    limiter = RateLimiter(clock=lambda: now[0], sleep=advance)
    for _ in range(9):
        limiter.wait()
    assert now[0] == 1.0


def test_parser_refuses_partial_index_and_preserves_cik():
    body = Path("tests/fixtures/sec/master.idx").read_bytes()
    assert parse_master_index(body)[0].indexed_cik == "0000000001"
    assert len(parse_master_index(body + body.splitlines()[-1] + b"\n")) == 2
    with pytest.raises(ValueError):
        parse_master_index(body + b"malformed|row\n")
    with pytest.raises(ValueError):
        parse_master_index(b"<html>Access denied</html>")
    assert acceptance_time(b"<ACCEPTANCE-DATETIME>20260102120000").astimezone(UTC) == datetime(
        2026, 1, 2, 17, tzinfo=UTC
    )
    with pytest.raises(ValueError):
        acceptance_time(b"<ACCEPTANCE-DATETIME>20261101013000")


def test_fixture_permissions_cannot_use_network_transport(tmp_path):
    with pytest.raises(ValueError, match="HTTP transport"):
        SECClient(
            source=fixture_source(),
            mode=Mode.FIXTURE,
            user_agent="Test test@example.invalid",
            store=LocalObjectStore(tmp_path),
            transport=HTTPTransport(),
            record=lambda _: None,
        )


def test_403_does_not_retry(tmp_path):
    transport = FakeTransport([Response(403, b"denied", {})])
    client = SECClient(
        source=fixture_source(),
        mode=Mode.FIXTURE,
        user_agent="Test test@example.invalid",
        store=LocalObjectStore(tmp_path),
        transport=transport,
        record=lambda _: None,
    )
    with pytest.raises(ValueError, match="403"):
        client.get("https://www.sec.gov/Archives/edgar/test")
    assert len(transport.calls) == 1
