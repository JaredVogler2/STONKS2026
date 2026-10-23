"""Discovery V3 pilot 2: the additive limit-on-close entry arm, close-funded books with side-specific costs, the
close-rebalanced weights book, the explicit eligibility mask, buffered momentum, obj-v3-2 and the registered plan."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from apex_worker.discovery.v3 import agents as A
from apex_worker.discovery.v3 import objective as O
from apex_worker.discovery.v3 import portfolio as PF
from apex_worker.discovery.v3.contracts import AllocationSpec
from apex_worker.discovery.v3.plans import PILOT_1, PILOT_2
from apex_worker.research.execution_v2 import (
    CLOSE,
    EntryPolicy,
    ExecutionPolicy,
    ExitPolicy,
    OrderPlan,
    plan_from_levels,
    simulate_v2,
)

PILOT_2_SHA256 = "5a2cc40c5ba414791eb2ddaf73ac83a29f5a7aaee20eb2ccdeb642c98fdd8c0e"  # docs/discovery-v3/PILOT_2_PREREGISTRATION.md


def test_pilot_2_is_the_preregistered_plan_and_pilot_1_is_unchanged():
    """If this fails the plan changed after registration: register a NEW plan id instead of editing this one."""
    assert PILOT_1.sha256() == "11d0af37c60b46b9393fcffb7b01e0f5ad77ee34fd35f20d92d42524635ae2c7"
    assert PILOT_2.sha256() == PILOT_2_SHA256 and PILOT_2.objective_id == "obj-v3-2" and not PILOT_2.fixture_only
    trials = [p for h in PILOT_2.hypotheses for p in h.policies]
    assert len(trials) == 7 and len({h.family_id for h in PILOT_2.hypotheses}) == 3
    assert all(p.params.get("cost_model") == "cost-v3-2" for p in trials)
    doc = (Path(__file__).resolve().parents[3] / "docs" / "discovery-v3" / "PILOT_2_PREREGISTRATION.md").read_text()
    assert PILOT_2_SHA256 in doc


# ---- the EC (limit-on-close) entry arm -------------------------------------------------------------------------------
def _bars():
    o = np.array([100, 101, 99, 103, 104, 105, 106, 107], float)
    h = np.array([101, 102, 103, 104, 105, 106, 107, 108], float)
    low = np.array([99, 95, 98, 102, 103, 104, 105, 106], float)
    c = np.array([100, 100.5, 102, 103.5, 104.5, 105.5, 106.5, 107.5], float)
    return o, h, low, c


def _ec(horizon=3, valid=2):
    return ExecutionPolicy(EntryPolicy("EC", valid), ExitPolicy("X1", horizon))


def test_ec_fills_at_the_close_inside_the_band_with_no_fill_session_exposure():
    o, h, low, c = _bars()
    plan = OrderPlan(limit=101.0, min_open=99.0, stop=96.0, target=None)
    tr = simulate_v2(o, h, low, c, 0, plan, _ec())
    # session 1's low (95) breaches the stop, but the position is bought at that session's close: not stopped out
    assert tr.filled and tr.fill_kind == CLOSE and tr.entry_index == 1 and tr.entry_price == 100.5
    assert tr.pess.outcome == "TIME" and tr.pess.exit_index == 4 and tr.pess.exit_price == 104.5
    assert tr.mae_bounds[1] <= 0.0 <= tr.mfe_bounds[0] + 1e-12  # excursions start at the fill price


def test_ec_skips_a_close_outside_the_band_and_tries_the_next_session():
    o, h, low, c = _bars()
    tr = simulate_v2(o, h, low, c, 0, OrderPlan(limit=102.0, min_open=101.0, stop=96.0, target=None), _ec())
    assert tr.filled and tr.entry_index == 2 and tr.entry_price == 102.0  # 100.5 < min; 102.0 inside
    none = simulate_v2(o, h, low, c, 0, OrderPlan(limit=99.0, min_open=98.0, stop=96.0, target=None), _ec())
    assert not none.filled and none.status == "NO_FILL"


def test_ec_stops_apply_from_the_next_session():
    o, h, low, c = _bars()
    low = low.copy()
    low[2] = 97.0  # breaches a stop at 97.5 on the session after the fill
    tr = simulate_v2(o, h, low, c, 0, OrderPlan(limit=101.0, min_open=99.0, stop=97.5, target=None), _ec())
    assert tr.entry_index == 1 and tr.pess.outcome == "STOP" and tr.pess.exit_index == 2 and tr.pess.exit_price == 97.5


def test_ec_uses_the_e0_band_and_leaves_e0_unchanged():
    assert plan_from_levels(99, 101, 96, 110, 100, 1.0, EntryPolicy("EC")) == \
        plan_from_levels(99, 101, 96, 110, 100, 1.0, EntryPolicy("E0"))
    o, h, low, c = _bars()
    e0 = simulate_v2(o, h, low, c, 0, OrderPlan(101.0, 99.0, 96.0, None), ExecutionPolicy(EntryPolicy("E0", 2),
                                                                                           ExitPolicy("X1", 3)))
    assert e0.fill_kind == "OPEN" and e0.entry_index == 1 and e0.entry_price == 101.0
    assert e0.pess.outcome == "STOP" and e0.pess.exit_index == 1  # the open fill IS exposed to session 1's low


# ---- the book: close entries and side-specific costs ------------------------------------------------------------------
def _one_trade(kind="TIME"):
    return pd.DataFrame([{"symbol": "AAA", "sector": None, "entry_index": 2, "exit_index": 5, "entry_price": 10.0,
                          "exit_price": 11.0, "priority": 1.0, "exit_kind": kind}])


def _px():
    closes = {"AAA": np.array([10, 10, 10, 10.5, 10.8, 11, 11, 11], float)}
    spy_o = np.array([100, 100, 101, 102, 103, 104, 105, 106], float)
    spy_c = np.array([100, 100.5, 102, 102.5, 103.5, 104.5, 105.5, 106.5], float)
    return closes, spy_o, spy_c


def test_close_entry_has_no_entry_day_pnl_and_side_costs_apply():
    closes, so, sc = _px()
    alloc = AllocationSpec(slots=1, max_exposure=1.0)
    r = PF.run_portfolio(_one_trade(), closes, so, sc, 1, 7, alloc, 0.0, 0.0, entry_at="close")
    assert r.returns[1] == pytest.approx(0.0, abs=1e-12)  # bought at the close of session 2 at that close
    assert r.equity[-1] == pytest.approx(1.1, abs=1e-12) and r.taken == 1
    paid = PF.run_portfolio(_one_trade(), closes, so, sc, 1, 7, alloc, 0.0, 0.0, entry_at="close",
                            entry_cost_bps=21.0, exit_cost_bps=2.0, stop_exit_cost_bps=25.0)
    assert paid.equity[-1] == pytest.approx(11.0 * (1 - 0.0002) / (10.0 * (1 + 0.0021)), rel=1e-12)
    stop = PF.run_portfolio(_one_trade("STOP"), closes, so, sc, 1, 7, alloc, 0.0, 0.0, entry_at="close",
                            entry_cost_bps=21.0, exit_cost_bps=2.0, stop_exit_cost_bps=25.0)
    assert stop.equity[-1] == pytest.approx(11.0 * (1 - 0.0025) / (10.0 * (1 + 0.0021)), rel=1e-12)
    assert paid.stock_traded_notional > 0 and paid.traded_notional == pytest.approx(paid.stock_traded_notional)


def test_close_entry_in_an_spy_idle_book_sells_spy_at_that_close():
    closes, so, sc = _px()
    alloc = AllocationSpec(slots=1, max_exposure=1.0, idle_capital="SPY")
    r = PF.run_portfolio(_one_trade(), closes, so, sc, 1, 7, alloc, 0.0, 0.0, entry_at="close")
    assert r.returns[1] == pytest.approx(sc[2] / sc[1] - 1, rel=1e-12)  # SPY all of session 2, then the stock
    # the slot is sized from the previous close's equity (1.005); SPY worth 1.02 at this close leaves 0.015 in SPY
    assert r.stock_exposure[1] == pytest.approx(1.005 / 1.02, rel=1e-12)
    assert r.idle_exposure[1] == pytest.approx(0.015 / 1.02, rel=1e-12)
    o = PF.run_portfolio(_one_trade(), closes, so, sc, 1, 7, alloc, 0.0, 0.0)  # pilot-1 open funding differs
    assert o.returns[1] != pytest.approx(r.returns[1], rel=1e-9)
    assert r.stock_traded_notional < r.traded_notional  # SPY sweeps are not stock turnover (finding A-2)


def test_close_rebalanced_weights_trade_one_session_after_the_decision():
    idx = pd.date_range("2024-01-01", periods=6, freq="B")
    px = pd.Series([10, 10, 11, 12, 12, 13], index=idx, dtype=float)
    w = pd.DataFrame(np.nan, index=idx, columns=["AAA"])
    w.iloc[1] = 1.0  # decided at the close of session 1
    r, x = PF.close_rebalance_returns(w, {"AAA": px}, cost_bps=10.0)
    assert r.iloc[2] == pytest.approx(-0.001, rel=1e-9)  # traded at the close of session 2: cost only
    assert r.iloc[3] == pytest.approx(12 / 11 - 1, rel=1e-12) and x.iloc[2] == 1.0 and x.iloc[1] == 0.0


# ---- agents ------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def data():
    from apex_worker.discovery.v3.pipeline import load_data

    return load_data("fixture", per_sector=4)


def test_explicit_mask_has_no_market_wide_breaker(data):
    pre = data.pre
    m = A.explicit_mask(pre)
    prot = np.vstack([pre.eligible[s] for s in pre.stocks])
    assert m.shape == prot.shape and not (prot & ~m).any()  # the protocol mask only removes names from the explicit one
    closed = ~prot.any(axis=0) & m.any(axis=0)  # sessions the breaker closed for everyone, still open explicitly
    assert closed.sum() > 0


def test_buffered_momentum_turns_over_less_and_random_control_is_seeded(data):
    pre, b = data.pre, data.b
    base = {"n": 5, "every": 20, "mask": "explicit"}
    tight = A.w_momentum_buffered(pre, b, {**base, "keep_rank": 5}).dropna(how="all")
    loose = A.w_momentum_buffered(pre, b, {**base, "keep_rank": 15}).dropna(how="all")
    assert set(np.round(tight.sum(axis=1), 9)) <= {1.0} and set(np.round(loose.sum(axis=1), 9)) <= {1.0}
    assert loose.diff().abs().sum().sum() < tight.diff().abs().sum().sum()
    r1, r2 = A.w_random_buffered(pre, b, {**base, "seed": 3}), A.w_random_buffered(pre, b, {**base, "seed": 3})
    assert r1.equals(r2) and not r1.equals(A.w_random_buffered(pre, b, {**base, "seed": 4}))


# ---- objective v2 --------------------------------------------------------------------------------------------------------
def test_obj_v3_2_judges_drawdown_relative_to_the_comparator_and_stock_turnover():
    m = {"max_drawdown": -0.40, "turnover_per_year": 90.0, "stock_turnover_per_year": 12.0, "avg_stock_exposure": 1.0,
         "max_positions": 10}
    assert O.constraint_violations(m, O.OBJ_V3_2, comparator={"max_drawdown": -0.34}) == []  # within 10 pp of SPY
    v = O.constraint_violations(m | {"max_drawdown": -0.46}, O.OBJ_V3_2, comparator={"max_drawdown": -0.34})
    assert len(v) == 1 and "comparator" in v[0]
    assert O.constraint_violations(m | {"stock_turnover_per_year": 41.0}, O.OBJ_V3_2, comparator={"max_drawdown": -0.34})
    with pytest.raises(ValueError):
        O.constraint_violations(m, O.OBJ_V3_2)
    assert O.PRINCIPAL_OBJECTIVE.sha256().startswith("8911f89e")  # obj-v3-1 unchanged


def test_pilot_2_runs_end_to_end_on_the_fixture(data, tmp_path):
    from apex_worker.discovery.v3.pipeline import run_experiment

    s = run_experiment(PILOT_2, data, tmp_path, experiment_id="demo-pilot2-test", progress=lambda _m: None, n_boot=99)
    assert s["trials"] == 7 and s["aggregate"] == "NO_TRADE" and s["self_checks"]["engine_valid"]
    res = json.loads((tmp_path / "results.json").read_text())["trials"]
    by = {r["policy_id"]: r for r in res.values()}
    assert all(r["holm_m"] == 7 and r["objective"]["objective_id"] == "obj-v3-2" for r in res.values())
    g1c, g1o = by["g1-s1-close-spy-idle"], by["g1-s1-open-spy-idle"]
    assert g1c["execution_at"] == "close" and g1c["side_costs_bps"] == {"entry": 2.0, "time_exit": 2.0, "stop_exit": 25.0}
    assert g1o["execution_at"] == "open" and g1o["side_costs_bps"]["entry"] == 21.0
    assert g1c["execution"]["entry"]["arm"] == "EC" and "25bps_uniform" in g1c["cost_sensitivity"]
    g2 = by["g2-momentum-quarterly-buffered-close"]
    assert g2["side_costs_bps"] == {"rebalance": 2.0} and g2["metrics"]["avg_stock_exposure"] > 0.95
    assert g2["selection_vs_random_books"]["k"] == 20 and "test" in g2["selection_vs_random_books"]
    assert by["g2-momentum-quarterly-buffered-open"]["side_costs_bps"] == {"rebalance": 21.0}
    assert by["g2-momentum-monthly-unbuffered-close"]["metrics"]["stock_turnover_per_year"] > \
        g2["metrics"]["stock_turnover_per_year"]
