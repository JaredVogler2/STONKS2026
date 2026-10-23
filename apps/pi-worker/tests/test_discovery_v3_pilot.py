"""Discovery V3 pilot 1 (PR-C): preregistration integrity, the new agents and the execution-policy override."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from apex_worker.discovery.v3 import agents as A
from apex_worker.discovery.v3.plans import PILOT_1
from apex_worker.research.realeq.protocol_v2 import BoundariesV2

PILOT_1_SHA256 = "11d0af37c60b46b9393fcffb7b01e0f5ad77ee34fd35f20d92d42524635ae2c7"  # docs/discovery-v3/PILOT_PREREGISTRATION.md


def test_pilot_plan_is_the_preregistered_one():
    """If this fails the plan changed after registration: register a NEW plan id instead of editing this one."""
    assert PILOT_1.sha256() == PILOT_1_SHA256 and not PILOT_1.fixture_only
    trials = [p for h in PILOT_1.hypotheses for p in h.policies]
    assert len(trials) == 10 and len({h.family_id for h in PILOT_1.hypotheses}) == 4
    assert all(p.varies_axis for h in PILOT_1.hypotheses for p in h.ablations)
    assert all(not h.blocked_inputs and h.release_mode == "RESEARCH_ONLY" for h in PILOT_1.hypotheses)
    doc = (Path(__file__).resolve().parents[3] / "docs" / "discovery-v3" / "PILOT_PREREGISTRATION.md").read_text()
    assert PILOT_1_SHA256 in doc


def _fake_pre(n=400, syms=30, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    frames = {f"S{i}": pd.DataFrame({"ret_20": rng.normal(0, 0.05 * (1 + (np.arange(n) > 250)), n)}, index=idx)
              for i in range(syms)}
    eligible = {s: np.ones(n, bool) for s in frames}
    return SimpleNamespace(stocks=sorted(frames), frames=frames, eligible=eligible, sessions=idx)


def test_dispersion_gate_is_causal():
    pre = _fake_pre()
    full = A.dispersion_percentile(pre)
    cut = 300
    trunc = SimpleNamespace(stocks=pre.stocks, frames={s: f.iloc[: cut + 1] for s, f in pre.frames.items()},
                            eligible={s: e[: cut + 1] for s, e in pre.eligible.items()}, sessions=pre.sessions[: cut + 1])
    assert np.allclose(A.dispersion_percentile(trunc), full[: cut + 1], equal_nan=True)
    assert np.isnan(full[:59]).all() and np.nanmax(full) <= 1.0
    assert np.nanmean(full[260:300]) > np.nanmean(full[100:240])  # the dispersion regime change is visible


def test_spy_regime_and_random_weights_agents():
    n = 60
    idx = pd.date_range("2021-01-01", periods=n, freq="B")
    reg = pd.Series(["trending_up"] * 20 + ["risk_off"] * 10 + ["volatility_shock"] * 5 + ["rebound"] * 25, index=idx)
    pre = SimpleNamespace(sessions=idx, regime=reg, stocks=[f"S{i}" for i in range(20)],
                          eligible={f"S{i}": np.ones(n, bool) for i in range(20)})
    b = BoundariesV2("", "", "", "", "", "", (-10, -5), (5, 55), n)
    w = A.w_spy_regime(pre, b, {"abstain": ["risk_off", "volatility_shock"]})
    rows = w.dropna()
    assert list(rows.iloc[:, 0]) == [1.0, 0.0, 1.0]  # in, out at the risk-off close, back in at the rebound close
    assert rows.index[1] == idx[20] and rows.index[2] == idx[35]
    r1, r2 = A.w_random(pre, b, {"n": 5, "every": 10, "seed": 3}), A.w_random(pre, b, {"n": 5, "every": 10, "seed": 3})
    assert r1.equals(r2) and set(np.round(r1.dropna().sum(axis=1), 9)) == {1.0}
    assert ((r1.dropna() > 0).sum(axis=1) == 5).all()


@pytest.fixture(scope="module")
def data():
    from apex_worker.discovery.v3.pipeline import load_data

    return load_data("fixture", per_sector=4)


def test_policy_override_changes_nothing_at_the_registered_horizon_and_extends_holds(data):
    from apex_worker.discovery.v3.contracts import PolicySpec
    from apex_worker.discovery.v3.pipeline import execution_policy
    from apex_worker.research.realeq import study_v2 as S

    pre, b, q = data.pre, data.b, data.quarantine
    sigs = A.sel_momentum_top(pre, b, {"n": 2, "every": 20})
    default = S.run_arm("ovr", "E0X1", sigs, pre, b, q, controls=False)
    same = S.run_arm("ovr", "E0X1", sigs, pre, b, q, controls=False,
                     policy=execution_policy(PolicySpec("p-x", "momentum_top", "E0X1")))
    assert default.rows.drop(columns=["ctrl_trades"], errors="ignore").equals(
        same.rows.drop(columns=["ctrl_trades"], errors="ignore"))
    long = S.run_arm("ovr", "E0X1", sigs, pre, b, q, controls=False,
                     policy=execution_policy(PolicySpec("p-y", "momentum_top", "E0X1", params={"horizon": 40})))
    assert long.rows["held"].max() > 20 >= default.rows["held"].max()
    with pytest.raises(ValueError):
        execution_policy(PolicySpec("p-z", "momentum_top", "E0X1", params={"horizon": 200}))


def test_pilot_runs_end_to_end_on_the_fixture(data, tmp_path):
    from apex_worker.discovery.v3.pipeline import run_experiment

    s = run_experiment(PILOT_1, data, tmp_path, experiment_id="demo-pilot-test", progress=lambda _m: None, n_boot=99)
    assert s["trials"] == 10 and s["aggregate"] == "NO_TRADE" and s["self_checks"]["engine_valid"]
    res = json.loads((tmp_path / "results.json").read_text())["trials"]
    assert all(r["holm_m"] == 10 for r in res.values())
    f1 = next(r for r in res.values() if r["policy_id"] == "f1-s1-idle-spy-h40")
    assert f1["execution"]["exit"]["horizon_sessions"] == 40
    mom = next(r for r in res.values() if r["policy_id"] == "f5-momentum-ew-monthly")
    assert mom["metrics"]["turnover_per_year"] > 0 and mom["comparator"] == "spy_hold"
    assert "cost_sensitivity" in f1 and set(f1["cost_sensitivity"]) == {"10bps", "50bps"}
