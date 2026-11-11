import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tailwatch.product.live import LiveFeeds
from tailwatch.sources import airtraffic
from tailwatch.sources.policy import PolicyDenied, Source, load_manifest
from tailwatch.sources.sec.client import CURRENT_FORM4_URL, validate_url
from tailwatch.sources.sec.current import ownership_document, parse_current, summarize
from tailwatch.sources.sec.form4 import parse_form4

LIVE = load_manifest(Path("config/sources.live.json"))
AIR = next(s for s in LIVE.sources if s.source_id == "adsblol-live")
NOW = datetime(2026, 11, 12, tzinfo=UTC)


class CountingBudget:
    def __init__(self, limit: int = 100) -> None:
        self.limit, self.used = limit, 0

    def validate_for(self, source: Source) -> None:
        pass

    def reserve(self, cost_microusd: int) -> None:
        if self.used >= self.limit:
            raise PolicyDenied("durable budget exhausted")
        self.used += 1


def aircraft(identity: str, **fields: object) -> dict[str, object]:
    base = {"hex": identity, "flight": "SECRET1", "r": "N1SECRET", "t": "GLF6"}
    base |= {"lat": 40.7612, "lon": -73.9123, "alt_baro": 41000, "seen_pos": 1.2}
    return base | {"track": 92.4, "gs": 451.7} | fields


def test_anonymize_drops_identity_privacy_listed_low_and_stale_traffic():
    payload = {
        "ac": [
            aircraft("a00001"),
            aircraft("a00002", dbFlags=8),  # LADD
            aircraft("a00003", dbFlags=4),  # PIA
            aircraft("a00004", dbFlags=1),  # military
            aircraft("a00005", alt_baro="ground"),
            aircraft("a00006", alt_baro=4900),
            aircraft("a00007", seen_pos=45),
            aircraft("a00008", lat=None),
            aircraft("a00009", dbFlags=2),  # "interesting" only: still anonymous, kept
        ]
    }
    kept, excluded = airtraffic.anonymize(payload)
    assert set(kept) == {"a00001", "a00009"}
    assert excluded == {"privacy_listed": 3, "low_or_ground": 2, "stale_or_invalid": 2}
    assert kept["a00001"] == (-73.9, 40.75, 92.0, 452.0, 41000.0)  # coarsened to 0.05 degrees
    assert all(isinstance(v, float) for v in kept["a00001"])


def test_fetch_area_authorizes_and_reserves_before_any_request():
    calls: list[str] = []

    def get(url: str) -> bytes:
        calls.append(url)
        return json.dumps({"ac": []}).encode()

    budget = CountingBudget(limit=1)
    hub = airtraffic.HUBS[0]
    airtraffic.fetch_area(hub, source=AIR, budget=budget, at=NOW, get=get)
    assert calls == ["https://api.adsb.lol/v2/point/40.75/-73.90/250"]
    with pytest.raises(PolicyDenied):
        airtraffic.fetch_area(hub, source=AIR, budget=budget, at=NOW, get=get)
    assert len(calls) == 1  # an exhausted budget never reaches the provider
    expired = datetime(2027, 3, 1, tzinfo=UTC)
    with pytest.raises(PolicyDenied):
        airtraffic.fetch_area(hub, source=AIR, budget=CountingBudget(), at=expired, get=get)
    disabled = AIR.model_copy(update={"enabled": False})
    with pytest.raises(PolicyDenied):
        airtraffic.fetch_area(hub, source=disabled, budget=CountingBudget(), at=NOW, get=get)
    with pytest.raises(ValueError, match="malformed"):
        airtraffic.fetch_area(
            hub, source=AIR, budget=CountingBudget(), at=NOW, get=lambda _: b'{"ac": 3}'
        )


ATOM = Path("tests/fixtures/sec/current_form4.atom").read_bytes()


def test_current_feed_groups_entries_and_skips_untrusted_links():
    newest, older = parse_current(ATOM)
    assert (newest.accession, newest.form, newest.issuer_name) == (
        "0000000001-26-000002",
        "4/A",
        "Later Inc",
    )
    assert older.issuer_cik == "0000000001" and older.reporting == ("Doe Jane",)
    assert older.link.startswith("https://www.sec.gov/Archives/edgar/data/1/")


def test_ownership_document_and_summary_never_invent_classification():
    listing = {"directory": {"item": [{"name": "doc4.xml"}, {"name": "x-index.htm"}]}}
    assert ownership_document(json.dumps(listing).encode()) == "doc4.xml"
    for names in ([], ["a.xml", "b.xml"], ["../escape.xml"]):
        body = json.dumps({"directory": {"item": [{"name": n} for n in names]}}).encode()
        with pytest.raises(ValueError):
            ownership_document(body)
    parsed = parse_form4(
        Path("tests/fixtures/sec/form4.xml").read_bytes(),
        accession="0000000001-26-000001",
        filed_date=NOW.date(),
        available_at=NOW,
    )
    summary = summarize(parsed)
    assert (summary["headline_code"], summary["plan_10b5_1"]) == ("P", False)
    assert summary["transactions"] == [
        {
            "code": "P",
            "label": "open-market or private purchase",
            "direction": "A",
            "shares": "100",
            "price": "12.50",
            "trade_date": summary["transactions"][0]["trade_date"],
            "table": "non_derivative",
        }
    ]
    assert summary["owners"][0]["roles"][:2] == ["director", "officer"]


def test_only_the_exact_current_feed_url_may_carry_a_query():
    validate_url(CURRENT_FORM4_URL)
    for url in (
        CURRENT_FORM4_URL.replace("type=4", "type=8-K"),
        CURRENT_FORM4_URL + "&x=1",
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany",
    ):
        with pytest.raises(ValueError):
            validate_url(url)


def test_live_traffic_merges_hubs_without_identity_and_polls_only_when_watched():
    requested: list[str] = []

    def get(url: str) -> bytes:
        requested.append(url)
        return json.dumps({"ac": [aircraft("abc123"), aircraft("def456", lat=51.47)]}).encode()

    hubs = airtraffic.HUBS[:2]
    feeds = LiveFeeds(
        None,  # type: ignore[arg-type]  # the injected budget means no database is used
        LIVE,
        None,  # type: ignore[arg-type]
        None,  # no SEC User-Agent: the SEC feed stays disabled
        hubs=hubs,
        air_get=get,
        budget_for=lambda _: CountingBudget(),
        hub_interval=0.01,
        idle_after=0.5,
    )
    assert feeds.enabled() == {"traffic": True, "filings": False}
    assert requested == []  # nothing is fetched before someone watches
    snapshot = feeds.traffic()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and len(snapshot["hubs"]) < 2:
        time.sleep(0.05)
        snapshot = feeds.traffic()
    feeds.stop()
    assert {h["name"] for h in snapshot["hubs"]} == {"New York", "Washington"}
    assert len(snapshot["aircraft"]) == 2  # the same aircraft seen by two hubs counts once
    text = json.dumps(snapshot)
    for secret in ("abc123", "def456", "SECRET1", "N1SECRET", "GLF6"):
        assert secret not in text


def test_warm_up_refills_every_stale_hub_before_settling_to_the_slow_pace():
    feeds = LiveFeeds(
        None,  # type: ignore[arg-type]
        LIVE,
        None,  # type: ignore[arg-type]
        None,
        hubs=airtraffic.HUBS[:4],
        air_get=lambda _: json.dumps({"ac": [aircraft("abc123")]}).encode(),
        budget_for=lambda _: CountingBudget(),
        hub_interval=30.0,  # the steady pace alone would take two minutes
        warmup_interval=0.01,
    )
    feeds.traffic()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and len(feeds.traffic()["hubs"]) < 4:
        time.sleep(0.05)
    snapshot = feeds.traffic()
    feeds.stop()
    assert (len(snapshot["hubs"]), snapshot["hub_total"]) == (4, 4)


class QuotaGetter:
    def __init__(self, remaining: int) -> None:
        self.quota = airtraffic.ProviderQuota(
            limit=500, remaining=remaining, reset_at=time.time() + 3600
        )

    def __call__(self, url: str) -> bytes:
        return b'{"ac": []}'


def test_provider_quota_headers_pace_and_pause_polling():
    quota = airtraffic.ProviderQuota()
    quota.update(
        {
            "X-RateLimit-Limit": "500, 500;w=86400",
            "x-ratelimit-remaining": "386",
            "x-ratelimit-reset": "77692",
        }
    )
    assert (quota.limit, quota.remaining) == (500, 386)
    assert quota.reset_at is not None and 77_000 < quota.reset_at - time.time() <= 77_692
    feeds = LiveFeeds(
        None,  # type: ignore[arg-type]
        LIVE,
        None,  # type: ignore[arg-type]
        None,
        hubs=airtraffic.HUBS[:1],
        air_get=QuotaGetter(400),
        budget_for=lambda _: CountingBudget(),
    )
    assert feeds._next_air_wait() == feeds.warmup_interval  # plenty left: quick warm-up
    feeds.air_get = QuotaGetter(45)  # 30 usable above the reserve: spread over the hour
    assert feeds._next_air_wait() == pytest.approx(3600 / 30, rel=0.05)
    feeds.air_get = QuotaGetter(10)  # inside the reserve: wait for the provider's reset
    assert feeds._next_air_wait() > 3500 and feeds._air.status == "paused_provider_quota"
    assert LIVE.sources[0].max_requests <= 450  # durable cap stays under the provider's 500


def test_one_failing_hub_backs_off_alone_while_the_others_refresh():
    broken = airtraffic.HUBS[1].name

    def get(url: str) -> bytes:
        if f"{airtraffic.HUBS[1].latitude:.2f}" in url:
            return b'{"ac": [truncated'  # a cut-off provider response
        return json.dumps({"ac": [aircraft("abc123")]}).encode()

    feeds = LiveFeeds(
        None,  # type: ignore[arg-type]
        LIVE,
        None,  # type: ignore[arg-type]
        None,
        hubs=airtraffic.HUBS[:4],
        air_get=get,
        budget_for=lambda _: CountingBudget(),
        hub_interval=30.0,
        warmup_interval=0.01,
    )
    feeds.traffic()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and len(feeds.traffic()["hubs"]) < 3:
        time.sleep(0.05)
    snapshot = feeds.traffic()
    feeds.stop()
    assert broken not in {h["name"] for h in snapshot["hubs"]}
    assert len(snapshot["hubs"]) == 3 and snapshot["status"] == "live"
    assert snapshot["detail"] == "1 hub retrying: JSONDecodeError"
