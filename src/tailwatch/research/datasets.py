import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, Field

from tailwatch.features.runs import PairFeatures
from tailwatch.research.labels import Label
from tailwatch.sources.policy import Contract


class Example(Contract):
    episode_id: UUID
    at: AwareDatetime
    features_known_at: AwareDatetime
    pair: PairFeatures
    label: Label
    sample_weight: float = Field(default=1, gt=0, allow_inf_nan=False)


def eligible(
    examples: list[Example],
    *,
    start: datetime,
    end: datetime,
    fit_at: datetime,
    required: tuple[str, ...],
) -> list[Example]:
    return [
        e
        for e in examples
        if start <= e.at < end
        and e.features_known_at <= e.at
        and e.label.state in {"positive", "negative"}
        and e.label.known_at is not None
        and e.label.known_at <= fit_at
        and e.pair.scorable(required)
    ]


def matched_baselines(
    examples: list[Example],
    *,
    seed: int,
    budget: int,
    flight: str = "flight",
    insider: str = "buying",
) -> dict[str, list[UUID]]:
    if budget < 1:
        raise ValueError("positive budget required")
    cohort = [e for e in examples if e.pair.scorable((flight, insider))]

    def top(key: Callable[[Example], Any]) -> list[UUID]:
        ordered = sorted(cohort, key=key)
        seen = set()
        result = []
        for e in ordered:
            if e.episode_id not in seen:
                result.append(e.episode_id)
                seen.add(e.episode_id)
        return result[:budget]

    return {
        "flight": top(lambda e: (-float(e.pair.features[flight].value or 0), str(e.episode_id))),
        "insider": top(lambda e: (-float(e.pair.features[insider].value or 0), str(e.episode_id))),
        "random": top(lambda e: hashlib.sha256(f"{seed}:{e.episode_id}".encode()).hexdigest()),
    }
