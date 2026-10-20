import json

import numpy as np
import pandas as pd
import pytest

from apex_worker.research.dataset import MODEL_FEATURES
from apex_worker.research.evaluate import PromotionPolicy, challenger_beats_champion, evaluate_strategy
from apex_worker.research.model import CompetingModel, fit_isotonic
from apex_worker.research.redteam import REDTEAM_STRATEGY, planted_alpha_events
from apex_worker.research.validation import walk_forward


@pytest.fixture(scope="module")
def planted():
    return planted_alpha_events()


def test_model_recovers_planted_relationship_out_of_sample(planted):
    rep, _wf = evaluate_strategy(planted, REDTEAM_STRATEGY, MODEL_FEATURES, "synthetic_fixture")
    d = rep.to_dict()
    assert d["holdout"]["brier_skill"] > 0.05, d["holdout"]
    assert d["holdout"]["ece"] < 0.06
    assert all(f["brier_skill"] > 0 for f in d["walk_forward"]["folds"])
    # synthetic data can never exceed EXPERIMENTAL
    assert d["confidence_status"] == "EXPERIMENTAL"
    assert any("SYNTHETIC" in w for w in d["bias_warnings"])


def test_shuffled_labels_collapse_to_no_skill(planted):
    wf = walk_forward(planted[planted.strategy_id == REDTEAM_STRATEGY], MODEL_FEATURES, shuffle_labels=True)
    assert wf.holdout_metrics["brier_skill"] < 0.01


def test_holdout_is_never_used_for_fitting(planted):
    wf = walk_forward(planted, MODEL_FEATURES)
    trained_through = pd.Timestamp(wf.final_model.metadata["trained_through"])
    assert trained_through < wf.holdout_start
    # purging: every training label window ends before the holdout (minus embargo)
    assert wf.purged_count > 0


def test_model_json_roundtrip_and_no_code_execution(planted):
    wf = walk_forward(planted, MODEL_FEATURES)
    text = wf.final_model.to_json()
    again = CompetingModel.from_json(text)
    rows = planted[list(MODEL_FEATURES)].head(5).to_dict("records")
    np.testing.assert_allclose(again.predict(rows), wf.final_model.predict(rows), rtol=1e-6)
    p = again.predict(rows)
    np.testing.assert_allclose(p.sum(axis=1), 1.0, atol=1e-9)  # coherent outcome partition
    bad = json.loads(text)
    bad["kind"] = "pickle"
    with pytest.raises(ValueError):
        CompetingModel.from_json(json.dumps(bad))
    bad = json.loads(text)
    bad["isotonic_target"]["y"] = list(reversed(bad["isotonic_target"]["y"]))
    with pytest.raises(ValueError):
        CompetingModel.from_json(json.dumps(bad))


def test_isotonic_is_monotone():
    rng = np.random.default_rng(3)
    s = rng.random(500)
    y = (rng.random(500) < s).astype(float)
    xs, ys = fit_isotonic(s, y)
    assert ys == sorted(ys) and xs == sorted(xs)


def test_champion_challenger_policy(planted):
    good = evaluate_strategy(planted, REDTEAM_STRATEGY, MODEL_FEATURES, "synthetic_fixture")[0].to_dict()
    shuffled = planted.copy()
    shuffled["outcome"] = np.random.default_rng(1).permutation(shuffled["outcome"].to_numpy())
    bad = evaluate_strategy(shuffled, REDTEAM_STRATEGY, MODEL_FEATURES, "synthetic_fixture")[0].to_dict()
    assert bad["confidence_status"] == "UNVALIDATED"
    ok, why = challenger_beats_champion(bad, good)
    assert not ok and "failed" in why
    ok, _ = challenger_beats_champion(good, None)
    assert ok
    same, why = challenger_beats_champion(good, good, PromotionPolicy())
    assert not same  # equal performance never displaces an incumbent


def test_permutation_gate_separates_real_edge_from_shuffled_noise(planted):
    """promotion-policy-2: the holdout skill must beat the 95% quantile of K shuffled-label walk-forwards."""
    policy = PromotionPolicy(shuffle_permutations=8)
    d = evaluate_strategy(planted, REDTEAM_STRATEGY, MODEL_FEATURES, "synthetic_fixture", policy=policy)[0].to_dict()
    sc, g = d["shuffled_control"], d["gates"]
    assert d["policy_version"] == "promotion-policy-2"
    assert sc["permutations"] == 8 and sc["skill_mean"] == g["shuffled_skill"]
    assert g["brier_skill"] > g["shuffled_skill_q"] == sc["skill_q95"]
    assert g["permutation_p"] == round(1 / 9, 4)  # beats every shuffle: the smallest p K=8 permutations allow
    assert not any("quantile" in r for r in d["status_reasons"])

    noise = planted.copy()
    noise["outcome"] = np.random.default_rng(5).permutation(noise["outcome"].to_numpy())
    bad = evaluate_strategy(noise, REDTEAM_STRATEGY, MODEL_FEATURES, "synthetic_fixture", policy=policy)[0].to_dict()
    assert bad["confidence_status"] == "UNVALIDATED"
    assert bad["gates"]["permutation_p"] > 0.05  # no edge: indistinguishable from its own shuffles
