import math
from datetime import timedelta
from pathlib import Path

import pytest

from tailwatch.demo import research_demo
from tailwatch.features.runs import FeatureRun
from tailwatch.product.scoring import ScoredPair, score_run, select_candidates
from tailwatch.research.models import LogisticArtifact, fit_candidate
from tailwatch.research.report import ReportInput

RESEARCH = ReportInput.model_validate_json(Path("config/research.synthetic.json").read_bytes())


def candidate(*features: str) -> LogisticArtifact:
    return fit_candidate(list(RESEARCH.examples), RESEARCH.experiment, features, RESEARCH.fitted_at)


def demo_run() -> FeatureRun:
    return FeatureRun.model_validate(research_demo()["run"])


def test_contributions_reconstruct_raw_logit_and_missing_inputs_stay_unscorable():
    scored = score_run(demo_run(), candidate("flight"))
    assert [s.status for s in scored] == ["scored", "scored"]
    for pair in scored:
        assert pair.raw_logit is not None and pair.reference_logit is not None
        assert math.isclose(pair.reference_logit + sum(pair.contributions.values()), pair.raw_logit)
    traveller = next(s for s in scored if s.evidence)
    assert traveller.evidence == ("synthetic:visit:1",)
    unscorable = score_run(demo_run(), candidate("flight", "buying"))
    assert {s.status for s in unscorable} == {"unscorable"}
    assert all(s.unavailable == {"buying": "missing_source"} for s in unscorable)
    assert all(s.calibrated_probability is None and not s.contributions for s in unscorable)


def test_scoring_rejects_mixed_hindsight_and_uncalibrated_candidates():
    artifact = candidate("flight")
    real = artifact.model_copy(update={"synthetic": False})
    with pytest.raises(ValueError, match="mixed"):
        score_run(demo_run(), real)
    run = demo_run()
    later = artifact.model_copy(update={"fitted_at": run.cutoff + timedelta(seconds=1)})
    with pytest.raises(ValueError, match="after the feature cutoff"):
        score_run(run, later)
    with pytest.raises(ValueError, match="calibrated"):
        score_run(run, artifact.model_copy(update={"calibration": None}))


def test_selection_respects_threshold_budget_and_one_direction_per_pair():
    scored = score_run(demo_run(), candidate("flight"))
    ranked = sorted(scored, key=lambda s: -(s.calibrated_probability or 0))
    top = select_candidates(scored, threshold=0, budget=5)
    assert len(top) == 1 and top[0] == ranked[0]
    assert select_candidates(scored, threshold=1, budget=5) == ()
    for bad in ({"threshold": math.nan, "budget": 1}, {"threshold": 0.5, "budget": 0}):
        with pytest.raises(ValueError):
            select_candidates(scored, **bad)


def test_unscorable_pairs_cannot_carry_numeric_scores():
    pair = score_run(demo_run(), candidate("flight", "buying"))[0]
    with pytest.raises(ValueError):
        ScoredPair.model_validate(pair.model_dump() | {"raw_logit": 0.0})
    with pytest.raises(ValueError):
        ScoredPair.model_validate(pair.model_dump() | {"unavailable": {}})
