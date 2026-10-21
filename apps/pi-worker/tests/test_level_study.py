"""Smoke test for bench/level_study.py on a small SYNTHETIC universe (--quick).

Checks that the outputs exist, that every reported decision date precedes the pipeline's holdout cutoff, and that the
production level configuration in the grid reproduces the pipeline's own development-window results. The reference is
computed here with production functions (walk_forward's split and folds, selection_summary) on the production
build_events output the study cached, so the study's own re-simulation path is checked against the pipeline's.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from apex_worker.research.dataset import MODEL_FEATURES
from apex_worker.research.evaluate import selection_summary
from apex_worker.research.validation import ValidationConfig, embargo_limit, walk_forward

BENCH = Path(__file__).resolve().parents[1] / "bench" / "level_study.py"
IDS = ("h1_vcp_breakout", "h5_sector_rotation")  # h5 has development out-of-fold folds at this size, h1 has none
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _load_module():
    spec = importlib.util.spec_from_file_location("apex_level_study", BENCH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def study(tmp_path_factory):
    mod = _load_module()
    out = tmp_path_factory.mktemp("level-study")
    assert mod.main(["--quick", "--out", str(out), "--strategies", ",".join(IDS)]) == 0
    return mod, out, json.loads((out / "level_study.json").read_text())


def _decision_dates(node, key=""):
    """Every string in the report stored under a key that names a decision date (incl. the decision_dates audit list)."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _decision_dates(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from _decision_dates(v, key)
    elif isinstance(node, str) and "decision" in key:
        yield node


def test_outputs_exist_and_are_labelled_synthetic(study):
    _, out, res = study
    md = (out / "level_study.md").read_text()
    assert res["is_synthetic"] is True and res["data_source"] == "synthetic_fixture"
    assert "SYNTHETIC" in res["label"] and "NOT evidence" in res["label"]
    assert "SYNTHETIC" in md[:400] and "**PRODUCTION**" in md
    assert res["caveats"] and res["runtime_seconds"] > 0
    assert all(res["strategies"][s]["status"] == "ok" for s in IDS)
    assert set(res["baselines"]) == {"random_qualified", "momentum_20", "breakout_basic"}
    assert all(b["status"] == "ok" and b["consistency"]["ok"] for b in res["baselines"].values())
    for sid in IDS:
        rep = res["strategies"][sid]
        assert {"excursions", "entry", "grid", "selection", "breakdowns", "baseline_comparison"} <= set(rep)
        assert rep["grid"]["n_configs"] == len(rep["grid"]["rows"]) == res["grid_definition"]["n_configs"] == 12
        dfl = rep["grid"]["deflation"]
        assert dfl["n_configs_tested"] == 12
        assert 0 < dfl["best_vs_zero"]["reality_check"]["p_value"] <= 1
        assert 0 < dfl["best_vs_production"]["reality_check"]["p_value"] <= 1
        for cell in (c for rows in rep["breakdowns"].values() if isinstance(rows, list) for c in rows):
            assert cell["n"] >= 20 or cell.get("suppressed") is True


def test_every_reported_decision_date_precedes_the_holdout_cutoff(study):
    _, _, res = study
    cutoffs = res["holdout_discipline"]["cutoffs"]
    assert set(cutoffs) == set(IDS)
    for sid in IDS:
        rep = res["strategies"][sid]
        assert rep["holdout_cutoff"] == cutoffs[sid]
        dates = list(_decision_dates(rep))
        assert len(dates) > len(rep["decision_dates"]) > 0
        assert all(ISO.match(d) for d in dates)
        assert max(dates) < cutoffs[sid], sid
    base_cut = res["holdout_discipline"]["baseline_cutoff"]
    assert base_cut == min(cutoffs.values())  # baselines stop at the EARLIEST strategy cutoff
    for name, b in res["baselines"].items():
        dates = list(_decision_dates(b))
        assert dates and all(ISO.match(d) for d in dates) and max(dates) < base_cut, name
    for sid in IDS:
        assert res["strategies"][sid]["baseline_comparison"]["window_end_exclusive"] == base_cut


def test_production_config_matches_the_pipelines_development_results(study):
    mod, _, res = study
    u = mod.QUICK_UNIVERSE
    events = mod.load_inputs(u["stocks_per_sector"], u["start"], u["keep_stocks_per_sector"], IDS)[5]  # build_events
    for sid in IDS:
        rep = res["strategies"][sid]
        ev = events[events["strategy_id"] == sid]
        wf = walk_forward(ev, MODEL_FEATURES, ValidationConfig())  # the pipeline's own split and development folds
        assert rep["holdout_cutoff"] == str(wf.holdout_start.date())
        dev = ev[ev["filled"] & (ev["decision_date"] < wf.holdout_start)]

        prod = [r for r in rep["grid"]["rows"] if r["is_production"]]
        assert len(prod) == 1 and prod[0] == rep["grid"]["production"]
        p = prod[0]
        assert (p["stop"], p["target_r"], p["horizon"]) == ("structural", 2.0, 20)
        assert p["levels_rejected"] == 0 and p["bootstrap_draws"] == 2000
        assert p["entries"] == len(dev) == rep["dev"]["entries"]
        assert p["target_first_rate"] == round(float((dev["outcome"] == "TARGET").mean()), 4)
        assert p["stop_first_rate"] == round(float((dev["outcome"] == "STOP").mean()), 4)
        assert p["mean_net_return_10"] == pytest.approx(dev["return_net"].mean(), abs=1e-6)
        assert p["mean_net_r_10"] == pytest.approx(dev["r_multiple"].mean(), abs=1e-4)
        ss = selection_summary(dev, 25.0)  # the promotion policy's own economics at its 25 bps evaluation cost
        assert p["mean_net_return_25"] == pytest.approx(ss["mean_return_net"], abs=1e-6)
        assert p["p10_mean_net_return_25"] == ss["ci"]["p10"]
        assert rep["consistency"]["ok"] and rep["consistency"]["stop_dist_atr_feature_mismatch"] == 0
        vcfg = ValidationConfig()
        assert rep["dev"]["embargo_limit"] == str(embargo_limit(wf.holdout_start, vcfg.embargo_sessions).date())
        assert rep["dev"]["embargo_limit"] < rep["holdout_cutoff"]

        # development out-of-fold rows are exactly the pipeline's fold test blocks (masking the holdout changed nothing)
        ref = rep["pipeline_reference"]
        assert [f["fold"] for f in ref["walk_forward_dev_folds"]] == [f.fold for f in wf.folds]
        assert [f["test_n"] for f in ref["walk_forward_dev_folds"]] == [f.test_n for f in wf.folds]
        assert ref["oos_rows"] == len(wf.oos)
        mo = rep["selection"]["model_oos"]
        assert mo["available"] == (not wf.oos.empty)
        if mo["available"]:
            assert mo["n"] == len(wf.oos)
            assert mo["observed_rate"] == round(float(wf.oos["y"].mean()), 4)
            assert mo["mean_predicted"] == round(float(wf.oos["p_target"].mean()), 4)
    assert res["strategies"]["h5_sector_rotation"]["selection"]["model_oos"]["available"]
