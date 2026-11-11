"""Research-mode live feeds for the terminal: air traffic and latest Form 4 filings.

Polling happens only while a viewer is active, every request is authorized against the
source manifest and reserved in a durable daily budget, provider errors back off, and
the SEC worker shares the single-worker advisory lock with `capture-sec`. Nothing here
produces signals, scores or alerts.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

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
        warmup_interval: float = 2.0,
        sec_interval: float = 60.0,
        idle_after: float = 120.0,
        details_per_poll: int = 4,
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

    # ----- lifecycle -----------------------------------------------------------------

    def enabled(self) -> dict[str, bool]:
        return {"traffic": self.air_source is not None, "filings": self.sec_source is not None}

    def touch(self) -> None:
        with self._lock:
            self._viewed = time.monotonic()
            if self._threads:
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
        merged: dict[str, tuple[float, tuple[float, ...]]] = {}
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
                        merged[identity] = (snapshot.fetched, row)
        # Identity keys are dropped here; sorting removes any order that could carry them.
        aircraft = sorted([*row, round(now - fetched)] for fetched, row in merged.values())
        return {
            "enabled": self.air_source is not None,
            "status": status,
            "source": "ADSB.lol live ADS-B · ODbL 1.0",
            "coverage": f"{airtraffic.RADIUS_NM} nm around {len(self.hubs)} hubs; "
            "not global and not complete",
            "privacy": "anonymous positions; military, PIA and LADD aircraft and all traffic "
            f"below {airtraffic.MIN_ALTITUDE_FT:,} ft are excluded",
            "fields": ["lon", "lat", "track", "ground_speed_kt", "altitude_ft", "age_s"],
            "aircraft": aircraft,
            "hubs": sorted(hubs, key=lambda h: str(h["name"])),
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
        while self._wait_turn(self._sec, self.sec_interval):
            try:
                self._poll_sec()
            except (OSError, ValueError, SQLAlchemyError) as error:
                self._failed(self._sec, error)
            self._stop.wait(self.sec_interval)

    def _poll_sec(self) -> None:
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
                current = parse_current(client.get(CURRENT_FORM4_URL)[0])
                with self._lock:
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
