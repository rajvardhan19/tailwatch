"""A deterministic, explicitly synthetic fixture run with no network calls."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from tailwatch.features.runs import FeatureRun, PairFeatures, interaction
from tailwatch.features.signals import SilenceExposure, TravelEpisode, flight_signals, silence
from tailwatch.features.text_finance import Embedding, semantic_similarity


def research_demo() -> dict[str, object]:
    cutoff = datetime(2026, 1, 2, tzinfo=UTC)
    a, b = UUID(int=1), UUID(int=2)
    travel = TravelEpisode(
        episode_id=UUID(int=3),
        visitor=a,
        host=b,
        arrived_at=cutoff - timedelta(days=1),
        departed_at=cutoff - timedelta(hours=20),
        available_at=cutoff - timedelta(hours=19),
        host_weight=0.3,
        event_multiplier=1,
        evidence="synthetic:visit:1",
    )
    silence_signal = silence(
        [
            SilenceExposure(
                person=UUID(int=4),
                baseline_sales=10,
                recent_sales=0,
                baseline_days=100,
                recent_days=20,
                evidence="synthetic:exposure:1",
            )
        ],
        watermark=cutoff,
        cutoff=cutoff,
        coverage_complete=True,
    )
    embeddings = [
        Embedding(
            document_id="synthetic-document",
            chunk_id="1",
            revision="synthetic-v1",
            published_at=cutoff - timedelta(days=10),
            available_at=cutoff - timedelta(days=9),
            expires_at=cutoff + timedelta(days=90),
            vector=(1, 0),
            evidence="synthetic:text:1",
        )
    ]
    pairs = []
    for visitor, host in ((a, b), (b, a)):
        signals = flight_signals(
            [travel],
            visitor=visitor,
            host=host,
            cutoff=cutoff,
            covered_since=cutoff - timedelta(days=390),
            covered_until=cutoff,
        )
        signals["silence"] = silence_signal
        signals["semantic_similarity"] = semantic_similarity(embeddings, embeddings, cutoff)
        signals["flight_silence_interaction"] = interaction(signals["flight"], silence_signal)
        pairs.append(PairFeatures(visitor=visitor, host=host, features=signals))
    run = FeatureRun(
        run_id=UUID(int=5),
        cutoff=cutoff,
        code_version="synthetic-fixture-v1",
        config_digest=hashlib.sha256(b"research-defaults-v1").hexdigest(),
        universe=(a, b),
        source_snapshots=(),
        input_fact_ids=(),
        required_features=("flight", "silence", "semantic_similarity"),
        pairs=tuple(pairs),
        synthetic=True,
    )
    return {
        "synthetic": True,
        "production_enabled": False,
        "delivery_enabled": False,
        "model_probability": None,
        "evaluation": "INCONCLUSIVE",
        "run_hash": run.digest(),
        "run": json.loads(run.canonical()),
    }
