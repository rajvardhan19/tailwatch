"""Bounded free-provider state capture; positions are NOT completed flight legs."""

import re
from datetime import datetime
from urllib.request import Request, build_opener

from tailwatch.sources.objects import ObjectStore
from tailwatch.sources.policy import Budget, Mode, Source, authorize
from tailwatch.sources.sec.client import NoRedirect


def capture_state(
    icao24: str,
    *,
    approved_aircraft: set[str],
    source: Source,
    mode: Mode,
    at: datetime,
    budget: Budget,
    store: ObjectStore,
) -> str:
    if not re.fullmatch(r"[0-9a-f]{6}", icao24) or icao24 not in approved_aircraft:
        raise ValueError("explicit reviewed aircraft allowlist required")
    if source.source_id != "adsblol" or source.kind != "live":
        raise ValueError("ADSB.lol live source required")
    authorize(source, mode=mode, capability="aircraft_states", at=at)
    budget.validate_for(source)
    budget.reserve(0)
    request = Request(
        f"https://api.adsb.lol/v2/icao/{icao24}",
        headers={"Accept": "application/json", "User-Agent": "Tailwatch-research/0.1"},
    )
    with build_opener(NoRedirect()).open(request, timeout=20) as response:
        body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise ValueError("state response exceeds size limit")
    return store.put(body)
