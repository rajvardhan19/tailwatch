"""Research-mode live feeds for the terminal: air traffic and latest Form 4 filings.

Polling happens only while a viewer is active, every request is authorized against the
source manifest and reserved in a durable daily budget, provider errors back off, and
the SEC worker shares the single-worker advisory lock with `capture-sec`. Nothing here
produces signals, scores or alerts.
"""

import json
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from tailwatch.features.categories import ClusterRule, insider_cluster, purchases
from tailwatch.operations.runtime import DurableBudget
from tailwatch.sources import airtraffic
from tailwatch.sources.objects import ObjectStore
from tailwatch.sources.policy import Budget, Manifest, Mode, PolicyDenied, Source
from tailwatch.sources.sec.client import (
    CURRENT_FORM4_URL,
    HTTPTransport,
    SECClient,
    Transport,
)
from tailwatch.sources.sec.current import filing_details, parse_current
from tailwatch.sources.sec.ingest import SECJournal

SEC_WORKER_LOCK = 754019226  # shared with capture_submissions: one SEC worker at a time
COMPANY_TTL_SECONDS = 900
COMPANY_QUEUE_LIMIT = 20
LOOKUPS_PER_POLL = 5
CLUSTER_CHECKS_PER_POLL = 2
CLUSTER_RECHECK_SECONDS = 6 * 3600
CLUSTER_QUEUE_LIMIT = 30
CLUSTER_FILINGS_LIMIT = 20
FLIGHT_PENDING = (
    "Needs human-reviewed company-aircraft links and completed-visit coverage; "
    "the live air feed is anonymous, so no flight signal can fire yet."
)
# ADSB.lol's free API allows ~500 requests/day, so each hub refreshes about every
# 10 minutes; snapshots stay displayable (as dead-reckoned estimates) for 20 minutes.
HUB_STALE_SECONDS = 1200
QUOTA_RESERVE = 15  # never spend the provider's last requests
LOW_QUOTA = 60  # below this, spread what is left until the provider's reset


@dataclass
class _Feed:
    status: str = "idle"
    failures: int = 0
    paused_until: float = 0.0
    updated_at: float = 0.0
    detail: str | None = None


@dataclass
class _HubSnapshot:
    fetched: float
    aircraft: dict[str, tuple[float, ...]]
    excluded: dict[str, int]


@dataclass
class _Filings:
    order: list[str] = field(default_factory=list)
    filings: dict[str, dict[str, Any]] = field(default_factory=dict)
    details: dict[str, dict[str, Any]] = field(default_factory=dict)


def _live(manifest: Manifest, capability: str) -> Source | None:
    return next(
        (s for s in manifest.sources if s.kind == "live" and capability in s.capabilities),
        None,
    )


class LiveFeeds:
    def __init__(
        self,
        engine: Engine,
        manifest: Manifest,
        store: ObjectStore,
        user_agent: str | None,
        *,
        hubs: tuple[airtraffic.Hub, ...] = airtraffic.HUBS,
        air_get: airtraffic.Fetcher | None = None,
        sec_transport: Transport | None = None,
        budget_for: Callable[[Source], Budget] | None = None,
        hub_interval: float = 43.0,
        warmup_interval: float = 5.0,  # faster bursts draw HTTP 429 from ADSB.lol
        sec_interval: float = 60.0,
        idle_after: float = 120.0,
        details_per_poll: int = 4,
        known_companies: Iterable[str] = (),
        autostart: bool = True,
        cluster_rule: ClusterRule | None = None,
    ) -> None:
        self.engine, self.store, self.user_agent = engine, store, user_agent
        self.air_source = _live(manifest, airtraffic.CAPABILITY)
        self.sec_source = _live(manifest, "sec") if user_agent else None
        self.hubs = hubs
        self.air_get: airtraffic.Fetcher = air_get or airtraffic.ProviderHTTP()
        self.sec_transport = sec_transport
        self.budget_for = budget_for or self._durable_budget
        self.hub_interval, self.sec_interval = hub_interval, sec_interval
        self.warmup_interval = warmup_interval
        self.idle_after, self.details_per_poll = idle_after, details_per_poll
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._viewed = 0.0
        self._air, self._sec = _Feed(), _Feed()
        self._hubs: dict[str, _HubSnapshot] = {}
        # Per-hub retry state: name -> (consecutive failures, retry not before, reason).
        self._hub_errors: dict[str, tuple[int, float, str]] = {}
        self._filings = _Filings()
        # Watchlist lookups: only companies the site shows, queued for the SEC worker.
        self._known = {c for c in known_companies if re.fullmatch(r"\d{10}", c)}
        self._companies: dict[str, tuple[float, dict[str, Any]]] = {}
        self._queue: list[str] = []
        self._sec_wake = threading.Event()
        self.autostart = autostart  # tests that drive the workers by hand turn this off
        # Insider-cluster checks for issuers whose filings show open-market purchases.
        self.cluster_rule = cluster_rule or ClusterRule()
        self._clusters: dict[str, tuple[float, dict[str, Any]]] = {}
        self._cluster_queue: list[str] = []

    # ----- lifecycle -----------------------------------------------------------------

    def enabled(self) -> dict[str, bool]:
        return {"traffic": self.air_source is not None, "filings": self.sec_source is not None}

    def touch(self) -> None:
        with self._lock:
            self._viewed = time.monotonic()
            if self._threads or not self.autostart:
                return
            for target, source in (
                (self._air_loop, self.air_source),
                (self._sec_loop, self.sec_source),
            ):
                if source is not None:
                    thread = threading.Thread(target=target, daemon=True, name=target.__name__)
                    self._threads.append(thread)
                    thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._sec_wake.set()
        for thread in self._threads:
            thread.join(timeout=5)

    def _watched(self) -> bool:
        return time.monotonic() - self._viewed < self.idle_after

    def _durable_budget(self, source: Source) -> Budget:
        with self.engine.connect() as conn:
            day: date = conn.execute(
                text("SELECT (clock_timestamp() AT TIME ZONE 'UTC')::date")
            ).scalar_one()
        return DurableBudget(self.engine, source, day)

    def _failed(self, feed: _Feed, error: Exception) -> None:
        code = getattr(error, "code", None)
        with self._lock:
            feed.detail = type(error).__name__ + (f" {code}" if isinstance(code, int) else "")
            if isinstance(error, PolicyDenied):
                # Budget exhausted or permission expired: stay quiet, never retry hot.
                feed.status, feed.paused_until = "paused_policy", time.monotonic() + 900
            else:
                feed.failures += 1
                feed.status = "provider_error"
                feed.paused_until = time.monotonic() + min(600.0, 15.0 * 2**feed.failures)

    def _wait_turn(self, feed: _Feed, interval: float) -> bool:
        """Sleep until it is this feed's turn; False when stopping."""
        while not self._stop.is_set():
            if self._watched() and time.monotonic() >= feed.paused_until:
                return True
            self._stop.wait(min(2.0, interval))
        return False

    # ----- air traffic ---------------------------------------------------------------

    def _air_loop(self) -> None:
        index = 0
        assert self.air_source is not None
        while self._wait_turn(self._air, self.hub_interval):
            now = time.monotonic()
            due = [h for h in self.hubs if self._hub_errors.get(h.name, (0, 0.0, ""))[1] <= now]
            if not due:
                self._stop.wait(2.0)
                continue
            hub = due[index % len(due)]
            index += 1
            try:
                payload = airtraffic.fetch_area(
                    hub,
                    source=self.air_source,
                    budget=self.budget_for(self.air_source),
                    at=datetime.now(UTC),
                    get=self.air_get,
                )
                kept, excluded = airtraffic.anonymize(payload)
                with self._lock:
                    self._hubs[hub.name] = _HubSnapshot(time.time(), kept, excluded)
                    self._air.status, self._air.failures = "live", 0
                    self._air.updated_at = time.time()
                    self._hub_errors.pop(hub.name, None)
                    self._air.detail = self._hub_summary()
            except PolicyDenied as error:
                self._failed(self._air, error)  # budget/permission: the whole feed pauses
            except (OSError, ValueError, SQLAlchemyError) as error:
                # A provider error backs off only this hub; the others keep refreshing.
                code = getattr(error, "code", None)
                reason = type(error).__name__ + (f" {code}" if isinstance(code, int) else "")
                with self._lock:
                    failures = self._hub_errors.get(hub.name, (0, 0.0, ""))[0] + 1
                    retry = time.monotonic() + min(600.0, 30.0 * 2 ** (failures - 1))
                    self._hub_errors[hub.name] = (failures, retry, reason)
                    self._air.detail = self._hub_summary()
            self._stop.wait(self._next_air_wait())

    def _hub_summary(self) -> str | None:
        if not self._hub_errors:
            return None
        reasons = sorted({reason for _, _, reason in self._hub_errors.values()})
        count = len(self._hub_errors)
        return f"{count} hub{'s' if count != 1 else ''} retrying: {', '.join(reasons)}"

    def _next_air_wait(self) -> float:
        """Refill stale coverage at ~1 request/s after idling, then settle to the pace."""
        now = time.time()
        with self._lock:
            fresh = sum(now - s.fetched <= HUB_STALE_SECONDS for s in self._hubs.values())
            fresh += len(self._hub_errors)  # retrying hubs wait their own backoff
        wait = self.warmup_interval if fresh < len(self.hubs) else self.hub_interval
        quota = getattr(self.air_get, "quota", None)
        if isinstance(quota, airtraffic.ProviderQuota) and quota.remaining is not None:
            seconds_left = max(60.0, (quota.reset_at or now + 86400) - now)
            usable = quota.remaining - QUOTA_RESERVE
            if usable <= 0:
                with self._lock:
                    self._air.status = "paused_provider_quota"
                return seconds_left
            if usable < LOW_QUOTA:
                wait = max(wait, seconds_left / usable)
        return wait

    def traffic(self) -> dict[str, Any]:
        self.touch()
        now = time.time()
        merged: dict[str, tuple[float, tuple[float, ...], str]] = {}
        hubs, excluded = [], {"privacy_listed": 0, "low_or_ground": 0, "stale_or_invalid": 0}
        with self._lock:
            status, detail = self._air.status, self._air.detail
            quota = getattr(self.air_get, "quota", None)
            for name, snapshot in self._hubs.items():
                if now - snapshot.fetched > HUB_STALE_SECONDS:
                    continue  # stale coverage is dropped, never shown as current
                hubs.append(
                    {
                        "name": name,
                        "age_seconds": round(now - snapshot.fetched),
                        "aircraft": len(snapshot.aircraft),
                    }
                )
                for key, value in snapshot.excluded.items():
                    excluded[key] += value
                for identity, row in snapshot.aircraft.items():
                    if identity not in merged or snapshot.fetched > merged[identity][0]:
                        merged[identity] = (snapshot.fetched, row, name)
        hubs.sort(key=lambda h: str(h["name"]))
        region = {str(h["name"]): index for index, h in enumerate(hubs)}
        # Identity keys are dropped here; sorting removes any order that could carry them.
        aircraft = sorted(
            [*row, round(now - fetched), region[hub]] for fetched, row, hub in merged.values()
        )
        return {
            "enabled": self.air_source is not None,
            "status": status,
            "source": "ADSB.lol live ADS-B · ODbL 1.0",
            "coverage": f"{airtraffic.RADIUS_NM} nm around {len(self.hubs)} hubs; "
            "not global and not complete",
            "privacy": "anonymous positions; military, PIA and LADD aircraft and all traffic "
            f"below {airtraffic.MIN_ALTITUDE_FT:,} ft are excluded",
            "fields": ["lon", "lat", "track", "ground_speed_kt", "altitude_ft", "age_s", "hub"],
            "aircraft": aircraft,
            "hubs": hubs,
            "hub_total": len(self.hubs),
            "detail": detail,
            "provider_quota": (
                {"limit": quota.limit, "remaining": quota.remaining}
                if isinstance(quota, airtraffic.ProviderQuota) and quota.remaining is not None
                else None
            ),
            "refresh": "each hub about every 10 minutes (ADSB.lol free tier: ~500 requests/day); "
            "between refreshes positions are estimated along the reported track",
            "excluded": excluded,
        }

    # ----- SEC filings ---------------------------------------------------------------

    def _sec_loop(self) -> None:
        last_feed = 0.0
        while self._wait_turn(self._sec, self.sec_interval):
            # Lookups can wake the worker early; the feed itself stays on its own pace.
            feed = time.monotonic() - last_feed >= self.sec_interval - 1
            try:
                self._poll_sec(feed=feed)
                if feed:
                    last_feed = time.monotonic()
            except (OSError, ValueError, SQLAlchemyError) as error:
                self._failed(self._sec, error)
            with self._lock:
                queued = bool(self._queue)
            self._sec_wake.wait(1.0 if queued else self.sec_interval)
            self._sec_wake.clear()

    def company(self, cik: str) -> dict[str, Any] | None:
        """Cached recent insider filings for a company the site shows; None if unknown."""
        if not re.fullmatch(r"\d{10}", cik) or self.sec_source is None:
            return None
        self.touch()
        with self._lock:
            if cik not in self._known:
                return None
            fetched, data = self._companies.get(cik, (0.0, {}))
            fresh = time.time() - fetched < COMPANY_TTL_SECONDS
            if not fresh and cik not in self._queue and len(self._queue) < COMPANY_QUEUE_LIMIT:
                self._queue.append(cik)
                self._sec_wake.set()
        if data:
            return data | {"status": "live" if fresh else "refreshing"}
        return {"cik": cik, "status": "pending", "filings": []}

    def _lookup(self, client: SECClient, cik: str) -> dict[str, Any]:
        data = json.loads(client.get(f"https://data.sec.gov/submissions/CIK{cik}.json")[0])
        if not isinstance(data, dict) or str(data.get("cik", "")).zfill(10) != cik:
            raise ValueError("SEC response identity mismatch")
        recent = data.get("filings", {}).get("recent", {})
        rows = zip(
            recent.get("form", []),
            recent.get("filingDate", []),
            recent.get("accessionNumber", []),
            strict=False,
        )
        filings = []
        for form, filed, accession in rows:
            if form in {"4", "4/A"} and re.fullmatch(r"\d{10}-\d{2}-\d{6}", str(accession)):
                folder = str(accession).replace("-", "")
                filings.append(
                    {
                        "form": form,
                        "filed": filed,
                        "accession": accession,
                        "link": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{folder}/"
                        f"{accession}-index.htm",
                    }
                )
            if len(filings) == 8:
                break
        return {"cik": cik, "name": str(data.get("name", "")), "filings": filings}

    def _check_cluster(self, client: SECClient, cik: str) -> dict[str, Any]:
        """Insider purchases filed in the last window: is there a cluster right now?"""
        from tailwatch.research.backtest import _fetch, filings

        today = datetime.now(UTC).date()
        start = today - timedelta(days=self.cluster_rule.window_days + 3)
        name, rows = filings(client, cik, start)
        recent = [
            r
            for r in rows
            if r["form"] in {"4", "4/A"} and date.fromisoformat(r["filingDate"]) >= start
        ][:CLUSTER_FILINGS_LIMIT]
        found = []
        for row in recent:
            parsed = _fetch(client, cik, row)
            if parsed is not None:
                found += purchases(parsed, date.fromisoformat(row["filingDate"]))
        rule = ClusterRule(
            min_buyers=self.cluster_rule.min_buyers,
            window_days=self.cluster_rule.window_days,
            lookback_days=self.cluster_rule.window_days + 3,
        )
        result = insider_cluster(found, as_of=today, rule=rule)
        return {
            "cik": cik,
            "name": name,
            "checked_at": datetime.now(UTC).isoformat(),
            "filings_read": len(recent),
            **{k: (v.isoformat() if isinstance(v, date) else v) for k, v in result.items()},
        }

    def signals(self) -> dict[str, Any]:
        """Three categories. Insider clusters are live; flight and both await reviewed links."""
        self.touch()
        with self._lock:
            checked = [r for _, r in self._clusters.values() if "unavailable" not in r]
            queued = len(self._cluster_queue)
        fired = sorted(
            (r for r in checked if r.get("triggered")),
            key=lambda r: str(r.get("public_on")),
            reverse=True,
        )
        building = sorted(
            (r for r in checked if not r.get("triggered") and r.get("buyers", 0) >= 2),
            key=lambda r: -int(r.get("buyers", 0)),
        )
        rule = self.cluster_rule
        return {
            "enabled": self.sec_source is not None,
            "rules": {
                "insider": f"{rule.min_buyers}+ distinct insiders (any reporting-owner type, "
                f"joint filers once) buying on the open market (Form 4 code P) within "
                f"{rule.window_days} days, using filings already public.",
                "flight": "A reviewed company aircraft completes a ground stop of at least one "
                "hour near another tracked company's offices.",
                "both": "Flight and insider triggers for the same company within 30 days.",
            },
            "categories": {
                "flight": {"status": "unavailable", "reason": FLIGHT_PENDING, "items": []},
                "insider": {"status": "live", "items": fired, "building": building[:6]},
                "both": {
                    "status": "unavailable",
                    "reason": "Needs the flight category.",
                    "items": [],
                },
            },
            "checked": len(checked),
            "queued": queued,
        }

    def _poll_sec(self, feed: bool = True) -> None:
        assert self.sec_source is not None and self.user_agent is not None
        with self.engine.connect() as lock:
            acquired = lock.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": SEC_WORKER_LOCK}
            ).scalar_one()
            lock.commit()
            if not acquired:
                with self._lock:
                    self._sec.status = "waiting_for_other_sec_worker"
                return
            try:
                client = SECClient(
                    source=self.sec_source,
                    mode=Mode.RESEARCH,
                    user_agent=self.user_agent,
                    store=self.store,
                    transport=self.sec_transport or HTTPTransport(),
                    record=SECJournal(self.engine, self.store).record_fetch,
                    budget=self.budget_for(self.sec_source),
                )
                current = parse_current(client.get(CURRENT_FORM4_URL)[0]) if feed else []
                with self._lock:
                    self._known.update(f.issuer_cik for f in current if f.issuer_cik)
                    for filing in current:
                        if filing.accession not in self._filings.filings:
                            self._filings.order.append(filing.accession)
                        self._filings.filings[filing.accession] = filing.model_dump(mode="json")
                    self._filings.order.sort(
                        key=lambda a: self._filings.filings[a]["filed_at"], reverse=True
                    )
                    for stale in self._filings.order[60:]:
                        self._filings.filings.pop(stale, None)
                        self._filings.details.pop(stale, None)
                    del self._filings.order[60:]
                    pending = [f for f in current if f.accession not in self._filings.details][
                        : self.details_per_poll
                    ]
                    if feed:
                        self._sec.status, self._sec.failures = "live", 0
                        self._sec.updated_at = time.time()
                for filing in pending:
                    try:
                        details = filing_details(client, filing)
                    except PolicyDenied:
                        raise
                    except (OSError, ValueError) as error:
                        details = {"status": "unavailable", "reason": type(error).__name__}
                    with self._lock:
                        self._filings.details[filing.accession] = details
                with self._lock:
                    for accession, details in self._filings.details.items():
                        codes = {t.get("code") for t in details.get("transactions", [])}
                        cik = self._filings.filings.get(accession, {}).get("issuer_cik")
                        checked = self._clusters.get(cik or "", (0.0, {}))[0]
                        if (
                            "P" in codes
                            and cik
                            and time.time() - checked > CLUSTER_RECHECK_SECONDS
                            and cik not in self._cluster_queue
                            and len(self._cluster_queue) < CLUSTER_QUEUE_LIMIT
                        ):
                            self._cluster_queue.append(cik)
                    checks = self._cluster_queue[:CLUSTER_CHECKS_PER_POLL]
                    del self._cluster_queue[:CLUSTER_CHECKS_PER_POLL]
                for cik in checks:
                    try:
                        result = self._check_cluster(client, cik)
                    except PolicyDenied:
                        raise
                    except (OSError, ValueError) as error:
                        result = {"cik": cik, "unavailable": type(error).__name__}
                    with self._lock:
                        self._clusters[cik] = (time.time(), result)
                with self._lock:
                    lookups = self._queue[:LOOKUPS_PER_POLL]
                    del self._queue[:LOOKUPS_PER_POLL]
                for cik in lookups:
                    try:
                        result = self._lookup(client, cik)
                    except PolicyDenied:
                        raise
                    except (OSError, ValueError) as error:
                        result = {"cik": cik, "unavailable": type(error).__name__, "filings": []}
                    with self._lock:
                        self._companies[cik] = (time.time(), result)
            finally:
                lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": SEC_WORKER_LOCK})
                lock.commit()

    def filings(self) -> dict[str, Any]:
        self.touch()
        with self._lock:
            items = [
                self._filings.filings[a]
                | {"details": self._filings.details.get(a, {"status": "pending"})}
                for a in self._filings.order[:40]
            ]
            status, updated = self._sec.status, self._sec.updated_at
        return {
            "enabled": self.sec_source is not None,
            "status": status,
            "source": "SEC EDGAR latest Form 4 filings · public data",
            "updated_at": datetime.fromtimestamp(updated, UTC).isoformat() if updated else None,
            "note": "Transaction codes as filed; not reviewed classifications or signals.",
            "filings": items,
        }
