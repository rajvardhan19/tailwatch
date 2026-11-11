"""Live research air-traffic display from ADSB.lol: anonymous, privacy-filtered, ephemeral.

Positions are not completed flights. They are never retained as evidence and never feed
features or alerts. Identity fields (hex, callsign, registration, type) are dropped
inside this module, so nothing identifying reaches the API or the browser.

Privacy: aircraft flagged military, PIA or LADD in readsb's ``dbFlags`` are excluded
(https://github.com/wiedehopf/readsb/blob/dev/README-json.md: military = 1, PIA = 4,
LADD = 8). Flags exist only for aircraft in the provider's database, so the stronger
guarantees are structural: no identity leaves this module, positions are coarsened,
and only en-route traffic above MIN_ALTITUDE_FT is shown, hiding the take-off and
landing phases that would reveal which airport an aircraft uses.
"""

import json
import math
from collections.abc import Callable
from datetime import datetime
from typing import Any, NamedTuple
from urllib.request import Request, build_opener

from tailwatch.sources.policy import Budget, Mode, Source, authorize
from tailwatch.sources.sec.client import NoRedirect

CAPABILITY = "aircraft_positions"
RADIUS_NM = 250  # ADSB.lol's documented maximum for area queries
MAX_BYTES = 5_000_000
PRIVACY_FLAGS = 1 | 4 | 8
MIN_ALTITUDE_FT = 5000
MAX_POSITION_AGE_S = 30
GRID_DEGREES = 0.05  # ~5 km coarsening


class Hub(NamedTuple):
    name: str
    latitude: float
    longitude: float


# Regional query centres. Coverage is these 250 nm circles only: not global, not complete.
HUBS: tuple[Hub, ...] = (
    Hub("New York", 40.75, -73.9),
    Hub("Washington", 38.9, -77.0),
    Hub("Chicago", 41.9, -87.7),
    Hub("Atlanta", 33.7, -84.4),
    Hub("Dallas", 32.9, -97.0),
    Hub("Denver", 39.8, -104.9),
    Hub("Los Angeles", 34.0, -118.2),
    Hub("San Francisco", 37.6, -122.2),
    Hub("London", 51.5, -0.3),
    Hub("Paris", 48.9, 2.4),
    Hub("Frankfurt", 50.1, 8.6),
    Hub("Dubai", 25.25, 55.35),
    Hub("Singapore", 1.35, 103.9),
    Hub("Tokyo", 35.6, 139.8),
)

Fetcher = Callable[[str], bytes]


def _https_get(url: str) -> bytes:
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Tailwatch-research/0.1"},
    )
    with build_opener(NoRedirect()).open(request, timeout=20) as response:
        body: bytes = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("air-traffic response exceeds size limit")
    return body


def fetch_area(
    hub: Hub,
    *,
    source: Source,
    budget: Budget,
    at: datetime,
    get: Fetcher = _https_get,
) -> dict[str, Any]:
    """One budgeted, authorized area query. Every attempt consumes quota."""
    if source.kind != "live":
        raise ValueError("live air-traffic source required")
    authorize(source, mode=Mode.RESEARCH, capability=CAPABILITY, at=at)
    budget.validate_for(source)
    budget.reserve(0)
    url = f"https://api.adsb.lol/v2/point/{hub.latitude:.2f}/{hub.longitude:.2f}/{RADIUS_NM}"
    payload = json.loads(get(url))
    if not isinstance(payload, dict) or not isinstance(payload.get("ac"), list):
        raise ValueError("malformed air-traffic response")
    return payload


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def anonymize(payload: dict[str, Any]) -> tuple[dict[str, tuple[float, ...]], dict[str, int]]:
    """Keep airborne, en-route, non-privacy-listed positions; key by hex for merging only.

    Returns ({hex: (lon, lat, track, ground_speed_kt, altitude_ft)}, exclusion counts).
    Callers must strip the hex keys before publishing anything.
    """
    kept: dict[str, tuple[float, ...]] = {}
    excluded = {"privacy_listed": 0, "low_or_ground": 0, "stale_or_invalid": 0}
    for aircraft in payload.get("ac", []):
        if not isinstance(aircraft, dict) or not isinstance(aircraft.get("hex"), str):
            excluded["stale_or_invalid"] += 1
            continue
        flags = aircraft.get("dbFlags")
        if isinstance(flags, int) and not isinstance(flags, bool) and flags & PRIVACY_FLAGS:
            excluded["privacy_listed"] += 1
            continue
        altitude = aircraft.get("alt_baro")
        if not isinstance(altitude, int) or isinstance(altitude, bool):
            excluded["low_or_ground"] += 1  # "ground" or unknown altitude
            continue
        if altitude < MIN_ALTITUDE_FT:
            excluded["low_or_ground"] += 1
            continue
        lat, lon = _number(aircraft.get("lat")), _number(aircraft.get("lon"))
        age = _number(aircraft.get("seen_pos"))
        if (
            lat is None
            or lon is None
            or not -90 <= lat <= 90
            or not -180 <= lon <= 180
            or age is None
            or age > MAX_POSITION_AGE_S
        ):
            excluded["stale_or_invalid"] += 1
            continue
        track = _number(aircraft.get("track"))
        speed = _number(aircraft.get("gs"))
        kept[aircraft["hex"]] = (
            round(round(lon / GRID_DEGREES) * GRID_DEGREES, 2),
            round(round(lat / GRID_DEGREES) * GRID_DEGREES, 2),
            round(track % 360, 0) if track is not None else -1.0,
            round(min(max(speed, 0.0), 700.0), 0) if speed is not None else 0.0,
            float(round(altitude, -2)),
        )
    return kept, excluded
