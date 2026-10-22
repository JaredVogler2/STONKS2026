"""Quant V2 milestone M1: execution, inference, matched controls and book accounting (amendments 02, 04, 06, 11).

Test names cite the acceptance case (Vxx) or finding (QV2-xxx). The intraday truth model lives in intraday_paths.py.
"""

import math

import numpy as np
import pandas as pd
import pytest

from apex_worker.research import inference as I
from apex_worker.research.book import run_book
from apex_worker.research.controls import Market, matched_controls, per_signal_net, simulate_signal, stop_distance_atr
from apex_worker.research.execution import ExecConfig, simulate
from apex_worker.research.execution_v2 import (
    Costs,
    EntryPolicy,
    ExecutionPolicy,
    ExitPolicy,
    OrderPlan,
    plan_from_levels,
    simulate_v2,
)
from apex_worker.research.realeq import analysis as A
from apex_worker.signals.levels import LevelConfig, Levels, compute_levels, tick_round
from intraday_paths import atr_series, paths, truth

ZERO = Costs(0.0)


def pol(entry="E0", exit_="X0", through=0, horizon=20, trail=3.0, bps=0.0):
    return ExecutionPolicy(EntryPolicy(entry, trade_through_ticks=through), ExitPolicy(exit_, horizon, trail), Costs(bps))


def bars(rows):
    a = np.array(rows, float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


# ---- simulate_v2 against continuous intraday paths (QV2-002, V07-V11) -----------------------------------------------
ARMS = [("E0", "X0", 0), ("E1", "X0", 0), ("E0", "X1", 0), ("E0", "X2", 0), ("E1", "X2", 0), ("E0", "X0", 1)]


@pytest.fixture(scope="module")
def cases():
    rng = np.random.default_rng(20261023)
    out = []
    for i in range(2000):
        ps, o, h, low, c = paths(rng)
        atr = atr_series(h, low, c)
        t = int(rng.integers(3, 10))
        close, a = float(c[t]), float(atr[t])
        emin, emax = close - 0.5 * a, close + min(0.5 * a, 0.02 * close)
        mid = (emin + emax) / 2
        tight = i % 2 == 1  # half the cases use a tight stop and target so that ambiguous bars actually occur
        stop = mid - (rng.uniform(0.6, 1.0) if tight else rng.uniform(1.0, 3.0)) * a
        target = mid + (rng.uniform(0.6, 1.2) if tight else rng.uniform(1.5, 2.5)) * (mid - stop)
        target = max(target, emax + 0.1 * a)  # as compute_levels: the target is always above the entry zone
        out.append((ps, o, h, low, c, atr, t, close, a, emin, emax, stop, target))
    return out


@pytest.mark.parametrize(("entry", "exit_", "through"), ARMS)
def test_v07_v11_truth_lies_between_pessimistic_and_optimistic_legs(cases, entry, exit_, through):
    p = pol(entry, exit_, through)
    filled = ambiguous = 0
    for ps, o, h, low, c, atr, t, close, a, emin, emax, stop, target in cases:
        plan = plan_from_levels(emin, emax, stop, target, close, a, p.entry)
        if plan is None:
            continue
        tr = simulate_v2(o, h, low, c, t, plan, p, atr)
        tg = plan.target if p.exit.uses_target else None
        ref = truth(ps, t, plan.limit, plan.min_open, plan.stop, tg, trail_atr=3.0 if exit_ == "X2" else None,
                    atr=atr, closes=c, through=through * 0.01)
        assert tr.filled == (ref is not None)
        if ref is None:
            continue
        filled += 1
        assert tr.entry_index == ref["fill_day"] and tr.entry_price == pytest.approx(ref["fill"], abs=1e-6)
        net_true = ref["exit"] / ref["fill"] - 1
        assert tr.net(ZERO, "pess") <= net_true + 1e-9 <= tr.net(ZERO, "opt") + 2e-9
        if tr.resolved:
            assert (tr.pess.exit_index, tr.pess.exit_price) == (ref["exit_day"], pytest.approx(ref["exit"], abs=1e-9))
            assert tr.mfe_bounds[0] - 1e-6 <= ref["mfe"] <= tr.mfe_bounds[1] + 1e-6
            assert tr.mae_bounds[0] - 1e-6 <= ref["mae"] <= tr.mae_bounds[1] + 1e-6
        else:
            ambiguous += 1
    assert filled > 300
    if exit_ == "X0":
        assert ambiguous > 0  # the bars do leave some X0 outcomes open
    else:
        assert ambiguous == 0  # no target, and limit entries come from above: daily bars decide every outcome


def test_v02_e0x0_reproduces_protocol1_wherever_the_fill_day_target_is_not_touched(cases):
    """Protocol-1 is the v2 engine minus the fill-day target rule: identical exits elsewhere (regression link)."""
    compared = 0
    for _ps, o, h, low, c, _atr, t, _close, _a, emin, emax, stop, target in cases:
        v1 = simulate(o, h, low, c, t, emin, emax, stop, target, ExecConfig(cost_bps_per_side=0.0))
        v2 = simulate_v2(o, h, low, c, t, OrderPlan(emax, emin, stop, target), pol())
        assert v1.filled == v2.filled
        if not v1.filled:
            continue
        assert (v1.entry_index, v1.entry_price) == (v2.entry_index, pytest.approx(v2.entry_price, abs=1e-6))
        if h[v2.entry_index] >= target or not v2.resolved:
            continue
        compared += 1
        assert (v1.outcome, v1.exit_index) == (v2.pess.outcome, v2.pess.exit_index)
        assert v1.exit_price == pytest.approx(v2.pess.exit_price, abs=1e-6)
    assert compared > 800


# ---- deterministic fixtures (amendment 02 section B, cases 1-8) -------------------------------------------------------
PLAN = OrderPlan(limit=101.0, min_open=99.0, stop=97.0, target=106.0)


def test_v07_v08_limit_fill_then_stop_is_implied_by_path_continuity():
    """Open 102 above the zone, low 96 below the stop. A continuous path from 102 to 96 must cross 101 first, so the
    limit filled before the stop: STOP is certain, not an ordering guess. A low before the touch cannot exist."""
    o, h, low, c = bars([[100, 101, 99, 100], [102, 102.5, 96, 98]] + [[98, 99, 97.5, 98]] * 25)
    tr = simulate_v2(o, h, low, c, 0, PLAN, pol())
    assert (tr.fill_kind, tr.entry_price, tr.pess.outcome, tr.pess.exit_index, tr.resolved) == ("LIMIT", 101.0, "STOP", 1, True)


def test_v11_gap_through_stop_exits_at_the_worse_open():
    o, h, low, c = bars([[100, 101, 99, 100], [100, 101, 99.5, 100.5], [95, 96, 94, 95]] + [[95, 96, 94, 95]] * 25)
    tr = simulate_v2(o, h, low, c, 0, PLAN, pol())
    assert (tr.fill_kind, tr.pess.outcome, tr.pess.exit_price, tr.pess.gap) == ("OPEN", "STOP", 95.0, True)
    assert tr.net(ZERO) < 97.0 / 100.0 - 1


def test_open_below_the_zone_is_no_buy_even_if_it_rebounds_above():
    o, h, low, c = bars([[100, 101, 99, 100], [98, 103, 97.5, 102], [100.5, 101, 100, 100.5]] + [[100.5, 101, 100, 100.5]] * 25)
    tr = simulate_v2(o, h, low, c, 0, PLAN, pol())
    assert (tr.entry_index, tr.entry_price, tr.fill_kind) == (2, 100.5, "OPEN")


def test_v10_same_bar_stop_and_target_is_unresolved_with_both_legs():
    o, h, low, c = bars([[100, 101, 99, 100], [100, 101, 99.5, 100.5], [100, 107, 96, 100]] + [[100, 101, 99, 100]] * 25)
    tr = simulate_v2(o, h, low, c, 0, PLAN, pol())
    assert (tr.ambiguity, tr.pess.outcome, tr.opt.outcome, tr.pess.exit_price, tr.opt.exit_price) == (
        "SAME_BAR_STOP_TARGET", "STOP", "TARGET", 97.0, 106.0)


def test_open_fill_day_target_sells_the_same_day_unlike_protocol1():
    o, h, low, c = bars([[100, 101, 99, 100], [100, 106.5, 99.5, 104], [110, 111, 109, 110]] + [[110, 111, 109, 110]] * 25)
    v2 = simulate_v2(o, h, low, c, 0, PLAN, pol())
    v1 = simulate(o, h, low, c, 0, 99.0, 101.0, 97.0, 106.0, ExecConfig(cost_bps_per_side=0.0))
    assert (v2.pess.outcome, v2.pess.exit_index, v2.pess.exit_price, v2.resolved) == ("TARGET", 1, 106.0, True)
    assert (v1.outcome, v1.exit_index, v1.exit_price) == ("TARGET", 2, 110.0)  # protocol-1 held into the gap


def test_limit_fill_day_target_touch_is_an_order_ambiguity_with_branch_bounds():
    """The high may have printed before the fill: sell at the target or hold. Next morning gaps above the target, so
    holding is the better branch; the legs are the worse and the better branch, never a guess."""
    o, h, low, c = bars([[100, 101, 99, 100], [103, 106.5, 100.5, 102], [109, 110, 108, 109]] + [[109, 110, 108, 109]] * 25)
    tr = simulate_v2(o, h, low, c, 0, PLAN, pol())
    assert (tr.fill_kind, tr.ambiguity) == ("LIMIT", "FILL_DAY_TARGET_ORDER")
    assert (tr.pess.outcome, tr.pess.exit_price, tr.opt.outcome, tr.opt.exit_price) == ("TARGET", 106.0, "TARGET", 109.0)
    o2, h2, l2, c2 = o.copy(), h.copy(), low.copy(), c.copy()
    c2[1] = 106.2  # a close above the target proves a post-fill touch
    tr2 = simulate_v2(o2, h2, l2, c2, 0, PLAN, pol())
    assert (tr2.resolved, tr2.pess.outcome, tr2.pess.exit_index) == (True, "TARGET", 1)


def test_v09_prefill_high_is_not_certain_mfe():
    o, h, low, c = bars([[100, 101, 99, 100], [104, 105.5, 100.2, 100.6]] + [[100.6, 101, 100, 100.6]] * 25)
    tr = simulate_v2(o, h, low, c, 0, PLAN, pol("E0", "X1"))
    v1 = simulate(o, h, low, c, 0, 99.0, 101.0, 97.0, 106.0, ExecConfig(cost_bps_per_side=0.0))
    assert tr.fill_kind == "LIMIT" and tr.observed_intraday_path is False
    assert tr.mfe_bounds[0] == pytest.approx(0.0, abs=1e-9)  # certain: only the fill price (the close is lower)
    assert tr.mfe_bounds[1] == pytest.approx(105.5 / 101.0 - 1, abs=1e-6)
    assert v1.mfe == pytest.approx(105.5 / 101.0 - 1, abs=1e-6)  # protocol-1 reported the bound as an observation


def test_a1_touch_fill_sensitivity_also_requires_the_target_to_trade_through():
    """A high exactly at the target fills the resting sell at 0 ticks but not when one tick of trade-through is required."""
    o, h, low, c = bars([[100, 101, 99, 100], [100, 101, 99.5, 100.5], [101, 106, 100, 102]] + [[100, 101, 99, 100]] * 25)
    touch, through = simulate_v2(o, h, low, c, 0, PLAN, pol()), simulate_v2(o, h, low, c, 0, PLAN, pol(through=1))
    assert (touch.pess.outcome, touch.pess.exit_index, touch.pess.exit_price) == ("TARGET", 2, 106.0)
    assert through.fill_kind == "OPEN" and through.pess.outcome == "TIME"


def test_v14_limit_expires_after_exactly_two_sessions():
    o, h, low, c = bars([[100, 101, 99, 100], [103, 104, 102, 103], [103, 104, 101.5, 103], [103, 104, 100, 103]] + [[103] * 4] * 25)
    assert simulate_v2(o, h, low, c, 0, PLAN, pol()).status == "NO_FILL"


def test_v18_time_exit_is_twenty_bar_sessions_after_the_fill():
    o, h, low, c = bars([[100, 101, 99, 100]] + [[100.5, 101, 100, 100.5]] * 30)
    tr = simulate_v2(o, h, low, c, 0, PLAN, pol())
    assert (tr.pess.outcome, tr.entry_index, tr.pess.exit_index, tr.sessions_held()) == ("TIME", 1, 21, 20)


def test_missing_bars_are_skipped_never_invented():
    rows = [[100, 101, 99, 100], [math.nan] * 4, [100.5, 101, 100, 100.5]] + [[100.5, 101, 100, 100.5]] * 30
    o, h, low, c = bars(rows)
    tr = simulate_v2(o, h, low, c, 0, PLAN, pol())
    assert tr.entry_index == 2 and "MISSING_BAR_IN_ENTRY_WINDOW" in tr.flags
    o[22] = h[22] = low[22] = c[22] = math.nan  # the horizon bar (fill 2 + 20) is missing
    tr = simulate_v2(o, h, low, c, 0, PLAN, pol())
    assert (tr.pess.outcome, tr.pess.exit_index, tr.pess.gap) == ("TIME", 23, True)
    assert "TIME_EXIT_DELAYED_MISSING_BAR" in tr.flags


def test_monotone_rally_event_log():
    """Case 8: hand-built monotone path. Fill at the open of t+1, target reached on t+4 at exactly the target."""
    o, h, low, c = bars([[100, 101, 99, 100], [100, 101.5, 99.8, 101.4], [101.4, 103.0, 101.3, 102.9],
                         [102.9, 104.8, 102.8, 104.7], [104.7, 106.4, 104.6, 106.3]] + [[106.3, 107, 106, 106.8]] * 25)
    tr = simulate_v2(o, h, low, c, 0, PLAN, pol())
    v1 = simulate(o, h, low, c, 0, 99.0, 101.0, 97.0, 106.0, ExecConfig(cost_bps_per_side=0.0))
    assert (tr.entry_index, tr.entry_price, tr.pess.outcome, tr.pess.exit_index, tr.pess.exit_price) == (1, 100.0, "TARGET", 4, 106.0)
    assert (v1.entry_index, v1.outcome, v1.exit_index, v1.exit_price) == (1, "TARGET", 4, 106.0)
    assert tr.mfe_bounds == (pytest.approx(0.06), pytest.approx(0.06)) and tr.mae_bounds[0] == pytest.approx(-0.002)


def test_x2_trailing_stop_uses_only_prior_closes_and_never_loosens():
    rng = np.random.default_rng(5)
    _ps, o, h, low, c = paths(rng)
    atr = atr_series(h, low, c)
    plan = OrderPlan(float(c[3]) * 1.02, float(c[3]) * 0.98, float(c[3]) * 0.9, None)
    base = simulate_v2(o, h, low, c, 3, plan, pol("E0", "X2"), atr)
    if base.filled and base.pess.exit_index > base.entry_index + 2:
        d = base.pess.exit_index
        o2, h2, l2, c2 = o.copy(), h.copy(), low.copy(), c.copy()
        h2[d], c2[d] = h2[d] * 1.5, c2[d] * 1.4  # changing session d itself cannot move the stop used on d
        again = simulate_v2(o2, h2, l2, c2, 3, plan, pol("E0", "X2"), atr)
        assert again.pess.exit_index == d and again.pess.outcome == base.pess.outcome
    o, h, low, c = bars([[100, 101, 99, 100]] + [[100 + i, 101 + i, 99.5 + i, 100.8 + i] for i in range(10)] +
                        [[110, 110.5, 98, 99]] * 20)
    atr = np.full(len(c), 1.0)
    tr = simulate_v2(o, h, low, c, 0, OrderPlan(101.0, 99.0, 97.0, None), pol("E0", "X2", trail=2.0), atr)
    assert tr.pess.outcome == "STOP" and tr.pess.exit_price == pytest.approx(109.8 - 2.0)


def test_v12_costs_are_applied_once_and_match_protocol1_formulas(cases):
    for _ps, o, h, low, c, _atr, t, _close, _a, emin, emax, stop, target in cases[:300]:
        tr = simulate_v2(o, h, low, c, t, OrderPlan(emax, emin, stop, target), pol())
        if not tr.filled:
            continue
        gross = tr.pess.exit_price / tr.entry_price - 1
        for bps in (0, 10, 25, 50):
            assert tr.net(Costs(bps)) == pytest.approx(float(A.net_at([gross], bps)[0]), abs=1e-12)
            assert tr.r_multiple(Costs(bps), stop) == pytest.approx(
                float(A.r_at([tr.entry_price], [tr.pess.exit_price], [stop], bps)[0]), abs=1e-9)


def test_e1_plan_sits_below_the_close_with_a_stop_guard():
    pl = plan_from_levels(99.0, 101.0, 97.0, 106.0, 100.0, 2.0, EntryPolicy("E1", e1_below_close_atr=0.75))
    assert (pl.limit, pl.min_open, pl.stop, pl.target) == (98.5, 97.01, 97.0, 106.0)
    assert plan_from_levels(99.0, 101.0, 97.0, 106.0, 100.0, 2.0, EntryPolicy("E1", e1_below_close_atr=1.5)) is None


def test_compute_levels_stop_distance_option_is_additive():
    assert compute_levels(100, 2, 97, None) == compute_levels(100, 2, 97, None, stop_distance_atr=None)
    lv = compute_levels(100, 2, 97, None, stop_distance_atr=2.2)
    assert isinstance(lv, Levels) and lv.stop == tick_round(100.0 - 2.2 * 2, down=True)
    clamped = compute_levels(100, 2, None, None, stop_distance_atr=5.0)
    assert clamped.stop == tick_round(100.0 - 3 * 2, down=True)  # the live [1, 3] ATR clamp still applies


# ---- inference (QV2-003/004, V19-V22, V26-V27) ---------------------------------------------------------------------
def test_block_test_mean_se_and_reproducibility():
    rng = np.random.default_rng(0)
    idx = rng.integers(0, 600, 400)
    v = rng.normal(0.002, 0.05, 400)
    a = I.block_test(v, idx, 600, block_len=20, n_boot=999, seed=3)
    b = I.block_test(v, idx, 600, block_len=20, n_boot=999, seed=3)
    assert a == b and a.status == "OK" and a.mean == pytest.approx(v.mean())
    assert a.ci90[0] < a.mean < a.ci90[1] and a.lower_95 == pytest.approx(a.ci90[0])
    assert 0 < a.p_one_sided < 1


def test_v20_v26_few_decision_clusters_give_no_p_value():
    """H8-shaped: 140 trades but only 15 decision weeks in one 75-session stretch -> INSUFFICIENT_CLUSTERS."""
    rng = np.random.default_rng(1)
    v, ix = I.simulate_design(I.Design("h8", span=75, dates=15, per_date=9), rng)
    res = I.block_tests(v, ix, 75)
    assert res["summary"]["status"] == "INSUFFICIENT_CLUSTERS" and res["summary"]["p_one_sided_max"] is None
    assert all(res[f"L{L}"]["p_one_sided"] is None for L in I.BLOCK_LENGTHS)


def test_v21_same_date_shock_inflates_the_block_standard_error():
    rng = np.random.default_rng(2)
    days = rng.choice(700, 150, replace=False)
    shock = {d: rng.normal(0, 0.05) for d in days}
    idx = np.repeat(days, 6)
    v = np.array([shock[d] + rng.normal(0, 0.01) for d in idx])
    r = I.block_test(v, idx, 700, n_boot=499)
    assert r.design_effect > 3  # six trades per date are ~one observation, not six


def test_v19_v27_type_one_error_is_controlled_and_planted_effects_are_found():
    """Reduced battery (the full 1,000-replicate battery is bench/qv2_m1_inference_battery.py, M1_ENGINE.md)."""
    rows = I.calibrate([I.Design("presample", 680, 120, 3), I.Design("fat", 680, 120, 3, tail_df=3, hetero=True)],
                       reps=160, effects=(0.0,), n_boot=299, alphas=(0.05,))
    for r in rows:
        assert r["conservative_reject_0.05"] <= 0.09, r
    power = I.calibrate([I.Design("presample", 680, 120, 3)], reps=80, effects=(0.02,), n_boot=299, alphas=(0.05,))
    assert power[0]["conservative_reject_0.05"] >= 0.7


def test_holm_skips_missing_p_values():
    adj = I.holm({"S1": 0.01, "S2": None, "S3": 0.04})
    assert adj == {"S1": 0.02, "S2": None, "S3": 0.04}


# ---- matched controls and shared simulation (QV2-007) -----------------------------------------------------------------
@pytest.fixture(scope="module")
def market():
    rng = np.random.default_rng(9)
    sessions = pd.bdate_range("2020-01-01", periods=160)
    bars_, ctx, elig, sector = {}, {}, {}, {}
    for i in range(30):
        s = f"S{i:02d}"
        _ps, o, h, low, c = paths(rng, days=160, p0=float(rng.uniform(20, 200)))
        df = pd.DataFrame({"open": o, "high": h, "low": low, "close": c, "volume": 1e6}, index=sessions)
        bars_[s] = df
        atr = pd.Series(atr_series(h, low, c, n=14), index=sessions)
        ctx[s] = pd.DataFrame({"close": df["close"], "atr_14": atr, "atr_pct_14": atr / df["close"]})
        elig[s] = pd.Series(atr.notna().to_numpy(), index=sessions)
        sector[s] = ("tech", "energy", "utilities")[i % 3]
    return Market.from_frames(sessions, bars_, ctx, elig, sector)


def test_controls_same_session_matched_stratum_same_geometry_and_seeded(market):
    p = ExecutionPolicy(costs=Costs(25.0))
    lc = LevelConfig(cost_bps_per_side=25.0, min_net_rr=0.0)
    res = simulate_signal(market, "S00", 60, p, lc, invalidation_hint=float(market.c["S00"][60]) - 2.0 * market.atr["S00"][60])
    assert res is not None
    k = stop_distance_atr(res.levels, float(market.atr["S00"][60]))
    a = matched_controls(market, "S00", 60, k, p, lc, np.random.default_rng(1), k=5, exclude=frozenset({"S03"}))
    b = matched_controls(market, "S00", 60, k, p, lc, np.random.default_rng(1), k=5, exclude=frozenset({"S03"}))
    assert a == b and a.n == len(set(a.symbols)) and "S00" not in a.symbols and "S03" not in a.symbols
    assert a.match in ("sector_vol", "sector", "vol", "pool")
    if a.match.startswith("sector"):
        assert all(market.sector[s] == "tech" for s in a.symbols)
    for s in a.symbols:
        lv = compute_levels(float(market.c[s][60]), float(market.atr[s][60]), None, float(market.resistance[s][60]), lc,
                            stop_distance_atr=k)
        assert abs(stop_distance_atr(lv, float(market.atr[s][60])) - min(max(k, 1.0), 3.0)) < 0.02 / market.atr[s][60] + 1e-9
    assert a.value_pess == pytest.approx(np.mean([per_signal_net(tr, p) for tr in a.trades]))


def test_per_signal_value_counts_no_fill_as_zero_and_incomplete_as_missing():
    from apex_worker.research.execution_v2 import Leg, TradeV2

    p = ExecutionPolicy()
    assert per_signal_net(TradeV2(False, "NO_FILL"), p) == 0.0
    assert per_signal_net(TradeV2(False, "DATA_END"), p) is None
    leg = Leg("DATA_END", 5, 101.0)
    short = TradeV2(True, "FILLED", 1, 100.0, "OPEN", leg, leg, flags=("ENDED_BEFORE_HORIZON",))
    assert per_signal_net(short, p) is None  # a label cut short by the data end is incomplete, not a 5-session trade


# ---- book accounting (V16) -----------------------------------------------------------------------------------------------
def test_v16_ten_slots_hundred_same_day_signals_sector_cap_and_no_cash_creation():
    n = 40
    closes = {f"S{i:03d}": np.full(n, 100.0) for i in range(100)}
    rows = [{"symbol": f"S{i:03d}", "sector": "tech" if i < 50 else f"sec{i % 7}", "entry_index": 5, "exit_index": 15,
             "entry_price": 100.0, "exit_price": 101.0, "priority": float(i % 13)} for i in range(100)]
    rows.append({"symbol": "S000", "sector": "tech", "entry_index": 6, "exit_index": 9, "entry_price": 100.0,
                 "exit_price": 100.0, "priority": 99.0})
    bk = run_book(pd.DataFrame(rows), closes, n, cost_bps=0.0, slots=10, sector_cap=0.30)
    assert bk.taken == 10 and bk.max_positions == 10
    assert bk.skipped["slots_full"] + bk.skipped["sector_cap"] + bk.skipped["duplicate_symbol"] == 91
    assert bk.equity[-1] == pytest.approx(1.0 + 10 * 0.1 * 0.01)
    assert bk.avg_exposure <= 1.0 + 1e-12


def test_book_same_session_exit_does_not_fund_that_sessions_entries():
    n = 20
    closes = {"A": np.full(n, 100.0), "B": np.full(n, 100.0)}
    rows = [{"symbol": "A", "sector": "x", "entry_index": 1, "exit_index": 5, "entry_price": 100.0, "exit_price": 100.0},
            {"symbol": "B", "sector": "y", "entry_index": 5, "exit_index": 9, "entry_price": 100.0, "exit_price": 100.0}]
    bk = run_book(pd.DataFrame(rows), closes, n, cost_bps=0.0, slots=1, sector_cap=1.0)
    assert bk.taken == 1 and bk.skipped["slots_full"] == 1  # A still occupies the only slot at B's entry
    spy = np.linspace(100, 110, n)
    bk2 = run_book(pd.DataFrame(rows[:1]), closes, n, cost_bps=0.0, slots=1, sector_cap=1.0, spy_close=spy)
    s = bk2.summary()
    assert s["cagr"] is None and s["exposure_matched_spy_total_return"] > 0
