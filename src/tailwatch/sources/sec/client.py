import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from tailwatch.sources.objects import ObjectStore
from tailwatch.sources.policy import Budget, Mode, RequestBudget, Source, authorize

MAX_BYTES = 25 * 1024 * 1024
# EDGAR's latest-filings Atom feed for Form 4. The only permitted URL with a query
# string: it is a fixed constant, never assembled from caller input.
CURRENT_FORM4_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&company=&dateb="
    "&owner=include&start=0&count=40&output=atom"
)


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    headers: Mapping[str, str]


class Transport(Protocol):
    def get(self, url: str, headers: Mapping[str, str]) -> Response: ...


def validate_url(url: str) -> None:
    if url == CURRENT_FORM4_URL:
        return
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.query
        or parsed.fragment
        or "%" in parsed.path
        or ".." in parsed.path
        or "\\" in parsed.path
    ):
        raise ValueError("invalid SEC URL")
    allowed = (parsed.netloc == "www.sec.gov" and parsed.path.startswith("/Archives/edgar/")) or (
        parsed.netloc == "data.sec.gov" and parsed.path.startswith("/submissions/")
    )
    if not allowed:
        raise ValueError("SEC URL is outside permitted hosts/paths")


class NoRedirect(HTTPRedirectHandler):
    # Returning None causes urllib to raise on redirects instead of following them.
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class HTTPTransport:
    def get(self, url: str, headers: Mapping[str, str]) -> Response:
        validate_url(url)
        try:
            reply = build_opener(NoRedirect()).open(Request(url, headers=dict(headers)), timeout=20)
        except HTTPError as error:
            reply = error
        with reply:
            body = reply.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("SEC response exceeds size limit")
            length = reply.headers.get("Content-Length")
            if length and int(length) != len(body):
                raise OSError("truncated SEC response")
            return Response(reply.code, body, dict(reply.headers.items()))


class RateLimiter:
    """Shared by all clients in this process. Deployment is a single SEC worker."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.clock, self.sleep = clock, sleep
        self.next_slot = 0.0
        self.lock = Lock()

    def wait(self) -> None:
        with self.lock:
            now = self.clock()
            if self.next_slot > now:
                self.sleep(self.next_slot - now)
            self.next_slot = self.clock() + 0.125  # eight requests/sec, including retries


SHARED_LIMITER = RateLimiter()


@dataclass(frozen=True)
class Fetch:
    url: str
    status: int
    digest: str
    requested_at: datetime
    received_at: datetime


class SECClient:
    def __init__(
        self,
        *,
        source: Source,
        mode: Mode,
        user_agent: str,
        store: ObjectStore,
        transport: Transport,
        record: Callable[[Fetch], None],
        budget: Budget | None = None,
        limiter: RateLimiter = SHARED_LIMITER,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not re.fullmatch(r"[^\r\n]+\s+[^\s@]+@[^\s@]+\.[^\s@]+", user_agent):
            raise ValueError("User-Agent must declare organization and contact email")
        self.source, self.mode, self.user_agent = source, mode, user_agent
        self.store, self.transport, self.record = store, transport, record
        if isinstance(transport, HTTPTransport) and source.kind != "live":
            raise ValueError("HTTP transport cannot use fixture permissions")
        self.limiter, self.sleep, self.now = limiter, sleep, now
        self.budget: Budget = budget if budget is not None else RequestBudget(source)
        self.budget.validate_for(source)

    def get(self, url: str) -> tuple[bytes, Fetch]:
        validate_url(url)
        for attempt in range(4):
            authorize(self.source, mode=self.mode, capability="sec", at=self.now())
            self.budget.validate_for(self.source)
            self.budget.reserve(0)  # Every attempt, including a retry, consumes quota.
            self.limiter.wait()
            requested_at = self.now()
            try:
                response = self.transport.get(
                    url, {"User-Agent": self.user_agent, "Accept-Encoding": "identity"}
                )
            except OSError:
                if attempt == 3:
                    raise
                self.sleep(2**attempt)
                continue
            if len(response.body) > MAX_BYTES:
                raise ValueError("SEC response exceeds size limit")
            fetch = Fetch(
                url, response.status, self.store.put(response.body), requested_at, self.now()
            )
            self.record(fetch)
            if response.status == 200:
                return response.body, fetch
            if response.status not in {429, 500, 502, 503, 504}:
                raise ValueError(f"SEC returned HTTP {response.status}; no retry")
            if attempt < 3:
                retry = next(
                    (v for k, v in response.headers.items() if k.lower() == "retry-after"), ""
                )
                try:
                    delay = (
                        float(retry)
                        if retry.isdigit()
                        else (parsedate_to_datetime(retry) - self.now()).total_seconds()
                    )
                except (TypeError, ValueError, OverflowError):
                    delay = float(2**attempt)
                if delay > 60:
                    raise ValueError("SEC requested a long retry delay; defer this job")
                self.sleep(max(float(2**attempt), delay))
        raise OSError("SEC retry budget exhausted")
