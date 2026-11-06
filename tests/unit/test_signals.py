from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tailwatch.domain.time import Availability
from tailwatch.features.runs import FeatureRun, PairFeatures, interaction
from tailwatch.features.signals import (
    Signal,
    SilenceExposure,
    TravelEpisode,
    dwell_weight,
    flight_signals,
    silence,
)
from tailwatch.features.text_finance import (
    Embedding,
    FinancialValue,
    financial_ratio,
    semantic_similarity,
)

T = datetime(2026, 1, 1, tzinfo=UTC)
A, B = uuid4(), uuid4()


def test_dwell_boundaries_novelty_current_exclusion_and_intensity_history():
    assert [dwell_weight(x) for x in (0, 1, 2.5, 4, 8)] == [0, 0, 0.5, 1, 1]
    episode = TravelEpisode(
        episode_id=uuid4(),
        visitor=A,
        host=B,
        arrived_at=T - timedelta(days=1),
        departed_at=T - timedelta(hours=20),
        available_at=T - timedelta(hours=19),
        host_weight=0.5,
        event_multiplier=1,
        evidence="visit",
    )
    result = flight_signals(
        [episode, episode],
        visitor=A,
        host=B,
        cutoff=T,
        covered_since=T - timedelta(days=390),
        covered_until=T,
    )
    assert result["novelty"].value == 1
    assert 0 < result["flight"].value < 0.5
    assert result["intensity"].value is None  # All twelve baseline counts are zero.
    missing = flight_signals(
        [episode],
        visitor=A,
        host=B,
        cutoff=T,
        covered_since=T - timedelta(days=30),
        covered_until=T,
    )
    assert missing["novelty"].value is None
    future = TravelEpisode.model_validate(
        episode.model_dump() | {"available_at": T + timedelta(days=1)}
    )
    assert (
        flight_signals(
            [future],
            visitor=A,
            host=B,
            cutoff=T,
            covered_since=T - timedelta(days=390),
            covered_until=T,
        )["flight"].value
        == 0
    )


def test_silence_fixed_denominator_increases_from_one_to_one_point_one():
    exposures = [
        SilenceExposure(
            person=uuid4(),
            baseline_sales=10,
            recent_sales=0,
            baseline_days=100,
            recent_days=20,
            evidence="a",
        ),
        SilenceExposure(
            person=uuid4(),
            baseline_sales=4,
            recent_sales=1,
            baseline_days=400,
            recent_days=20,
            evidence="b",
        ),
    ]
    assert silence(exposures, watermark=T, cutoff=T, coverage_complete=True).value == 1
    exposures[1] = SilenceExposure.model_validate(exposures[1].model_dump() | {"recent_sales": 0})
    assert silence(exposures, watermark=T, cutoff=T, coverage_complete=True).value == 1.1
    assert silence([], watermark=T, cutoff=T, coverage_complete=True).value is None
    assert silence(exposures, watermark=T, cutoff=T, coverage_complete=False).value is None


def test_cached_embedding_versions_zero_vectors_and_future_text():
    def embedding(vector, **kwargs):
        return Embedding.model_validate(
            dict(
                document_id="doc",
                chunk_id="1",
                revision="v1",
                published_at=T - timedelta(days=1),
                available_at=T - timedelta(days=1),
                expires_at=T + timedelta(days=1),
                vector=vector,
                evidence="text",
            )
            | kwargs
        )

    assert semantic_similarity([embedding([1, 0])], [embedding([-1, 0])], T).value == 0
    assert semantic_similarity([embedding([0, 0])], [embedding([1, 0])], T).value is None
    assert (
        semantic_similarity([embedding([1, 0])], [embedding([1, 0], revision="v2")], T).value
        is None
    )
    assert (
        semantic_similarity(
            [embedding([1, 0])], [embedding([1, 0], available_at=T + timedelta(hours=1))], T
        ).value
        is None
    )


def test_financial_corrections_preserve_past_ratio():
    first = FinancialValue(
        company_id=A,
        metric="cash",
        effective_at=T - timedelta(days=2),
        available_at=T - timedelta(days=1),
        revision=1,
        value=10,
        currency="USD",
        evidence="cash",
    )
    denominator = FinancialValue(
        company_id=B,
        metric="market_cap",
        effective_at=T - timedelta(days=2),
        available_at=T - timedelta(days=1),
        revision=1,
        value=100,
        currency="USD",
        evidence="cap",
    )
    correction = FinancialValue.model_validate(
        first.model_dump() | {"revision": 2, "value": 50, "available_at": T + timedelta(days=1)}
    )
    assert financial_ratio([first, correction], [denominator], T).value == 0.1
    assert financial_ratio([first, correction], [denominator], T + timedelta(days=1)).value == 0.5


def test_complete_directional_manifest_and_missing_interaction():
    missing = Signal(status=Availability.MISSING_SOURCE)
    observed = Signal(status=Availability.OBSERVED, value=2)
    assert interaction(missing, observed).value is None
    pair = PairFeatures(visitor=A, host=B, features={"flight": missing})
    assert not pair.scorable(("flight",))
    with pytest.raises(ValueError):
        FeatureRun(
            run_id=uuid4(),
            cutoff=T,
            code_version="test",
            config_digest="0" * 64,
            universe=(A, B),
            source_snapshots=(),
            input_fact_ids=(),
            required_features=("flight",),
            pairs=(pair,),
            synthetic=True,
        )
