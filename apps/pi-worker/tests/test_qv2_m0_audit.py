"""Quant V2 milestone M0: P0 research-correctness audit (amendments/quant-v2-2026-10-22/02, 11).

Each test names the finding (QV2-xxx) or acceptance case (Vxx) it settles. The intraday "truth" simulator below is a
reference model of the protocol-1 order semantics on a continuous intraday price path; the daily simulator only sees
that path's OHLC. Random paths check that daily-bar accounting is never MORE favourable than any intraday path
consistent with the same bars, and pin down exactly where it is biased.
"""

import decimal

import numpy as np
import pandas as pd
import pytest

from apex_worker.research.execution import ExecConfig, simulate
from apex_worker.research.realeq import analysis as A
from apex_worker.signals import levels as L
from apex_worker.signals.levels import Levels, compute_levels, tick_round


# ---- QV2-001 / V01: decimal rounding mode in tick_round ------------------------------------------------------------
def test_v01_tick_round_floor_and_half_up_exact_cents():
    assert tick_round(11.235, down=True) == 11.23
    assert tick_round(11.235) == 11.24
    assert tick_round(97.999, down=True) == 97.99
    assert tick_round(10.0, down=True) == 10.0
    rng = np.random.default_rng(1)
    for p in rng.uniform(1, 1000, 2000).round(5):
        down = tick_round(float(p), down=True)
        assert down <= p + 1e-12 and p - down < 0.01 + 1e-12 and abs(round(down * 100) - down * 100) < 1e-6
        ref = float(decimal.Decimal(str(float(p))).quantize(decimal.Decimal("0.01"), rounding=decimal.ROUND_HALF_UP))
        assert tick_round(float(p)) == ref


def test_v01_compute_levels_reference_case():
    lv = compute_levels(close=100, atr=2, invalidation_hint=97, resistance=None)
    assert isinstance(lv, Levels)
    assert (lv.entry_min, lv.entry_max, lv.stop, lv.target, lv.reward_to_risk_net) == (99.0, 101.0, 97.0, 106.0, 1.81)


def test_v01_rounding_mode_is_the_validated_decimal_constant():
    # decimal's rounding constants ARE strings; a misspelt mode is rejected, so the string form was never silently ignored
    assert decimal.ROUND_FLOOR == "ROUND_FLOOR"
    with pytest.raises(TypeError):
        decimal.Decimal("1.005").quantize(decimal.Decimal("0.01"), rounding="ROUND_FLORR")
    assert L.ROUND_FLOOR is decimal.ROUND_FLOOR  # explicit import since M0 (behaviour unchanged)


# ---- QV2-002 / V07-V11: daily-bar fills and exits vs a continuous intraday path ----------------------------------------
def _paths(rng, days=40, steps=24, p0=100.0):
    """Continuous piecewise-linear intraday paths with overnight gaps; returns paths and their OHLC."""
    out, price = [], p0
    for _ in range(days):
        price *= float(np.exp(rng.normal(0, 0.012)))  # overnight gap
        walk = price * np.exp(np.cumsum(np.concatenate([[0.0], rng.normal(0, 0.004, steps - 1)])))
        if rng.random() < 0.15:  # occasional intraday shock
            k = int(rng.integers(1, steps))
            walk[k:] *= float(np.exp(rng.normal(0, 0.03)))
        out.append(walk)
        price = float(walk[-1])
    o = np.array([p[0] for p in out])
    h = np.array([p.max() for p in out])
    low = np.array([p.min() for p in out])
    c = np.array([p[-1] for p in out])
    return out, o, h, low, c


def _truth(paths, t, emin, emax, stop, target, valid=2, horizon=20):
    """Protocol-1 semantics on the intraday path: resting limit at entry_max from the open (no fill on a session that
    opens below entry_min), then a resting stop and target from the moment of the fill; time exit at fill+H close."""
    fill = None
    for d in range(t + 1, min(len(paths), t + 1 + valid)):
        p = paths[d]
        if emin <= p[0] <= emax:
            fill = (d, 0, float(p[0]))
            break
        if p[0] > emax:
            hit = np.flatnonzero(p <= emax)
            if len(hit):
                fill = (d, int(hit[0]), float(emax))
                break
    if fill is None:
        return None
    fd, fk, px = fill
    hi = lo = px
    last = min(len(paths) - 1, fd + horizon)
    for d in range(fd, last + 1):
        p = paths[d]
        k0 = fk if d == fd else 0
        if d > fd and p[0] <= stop:
            return {"exit": float(p[0]), "fill": px, "fill_day": fd, "exit_day": d, "reason": "GAP_STOP",
                    "mfe": hi / px - 1, "mae": min(lo, p[0]) / px - 1}
        if d > fd and p[0] >= target:
            return {"exit": float(p[0]), "fill": px, "fill_day": fd, "exit_day": d, "reason": "GAP_TARGET",
                    "mfe": max(hi, p[0]) / px - 1, "mae": lo / px - 1}
        for raw in p[k0 + 1 :] if d == fd else p[1:]:
            x = float(raw)
            if x <= stop:  # a monotone segment from inside (stop, target) crosses at most one barrier
                return {"exit": stop, "fill": px, "fill_day": fd, "exit_day": d, "reason": "STOP", "mfe": hi / px - 1,
                        "mae": stop / px - 1}
            if x >= target:
                return {"exit": target, "fill": px, "fill_day": fd, "exit_day": d, "reason": "TARGET",
                        "mfe": target / px - 1, "mae": lo / px - 1}
            hi, lo = max(hi, x), min(lo, x)
        if d == fd + horizon:
            return {"exit": float(p[-1]), "fill": px, "fill_day": fd, "exit_day": d, "reason": "TIME",
                    "mfe": hi / px - 1, "mae": lo / px - 1}
    return {"exit": float(paths[last][-1]), "fill": px, "fill_day": fd, "exit_day": last, "reason": "DATA_END",
            "mfe": hi / px - 1, "mae": lo / px - 1}


@pytest.fixture(scope="module")
def scenarios():
    rng = np.random.default_rng(20261022)
    out = []
    for _ in range(3000):
        paths, o, h, low, c = _paths(rng)
        t = int(rng.integers(2, 10))
        close, atr = float(c[t]), float(np.mean(h[t - 2 : t + 1] - low[t - 2 : t + 1])) or 1.0
        emin, emax = close - 0.5 * atr, close + min(0.5 * atr, 0.02 * close)
        stop = (emin + emax) / 2 - rng.uniform(1.0, 3.0) * atr
        target = (emin + emax) / 2 + rng.uniform(1.5, 2.5) * ((emin + emax) / 2 - stop)
        out.append((paths, o, h, low, c, t, emin, emax, stop, target))
    return out


def test_qv2_002_daily_pnl_is_pessimistic_except_a_skipped_fill_day_target(scenarios):
    """CONFIRMED (QV2-002): the daily rule "only the stop can trigger on the fill session" is not a uniform worst case.
    When the path reaches the target after the fill on the fill day, a resting target sells there; the daily simulator
    holds instead, and a gap above the target the next morning books MORE than was achievable. Every optimistic case
    must be exactly that mechanism; every other difference is pessimistic. Net direction is path-dependent (not a bound)."""
    filled = optimistic = pessimistic = 0
    excess = []
    for paths, o, h, low, c, t, emin, emax, stop, target in scenarios:
        d = simulate(o, h, low, c, t, emin, emax, stop, target, ExecConfig(cost_bps_per_side=0.0))
        tr = _truth(paths, t, emin, emax, stop, target)
        assert d.filled == (tr is not None), "fill decision must match the path"
        if not d.filled:
            continue
        filled += 1
        assert d.entry_index == tr["fill_day"] and d.entry_price == pytest.approx(tr["fill"], abs=1e-6)
        net_truth = tr["exit"] / tr["fill"] - 1
        # TradeOutcome stores returns rounded to 6 dp, hence the 1e-6 tolerance
        if d.return_net > net_truth + 1e-6:
            optimistic += 1
            excess.append(d.return_net - net_truth)
            assert tr["reason"] == "TARGET" and tr["exit_day"] == tr["fill_day"], (tr["reason"], tr["exit_day"], d.outcome)
        elif d.return_net < net_truth - 1e-6:
            pessimistic += 1
    assert filled > 1500
    assert optimistic > 0  # the mechanism exists; frequencies on these synthetic paths are reported in M0_AUDIT.md
    assert max(excess) < 0.10  # bounded by one overnight gap above the target in this generator


def test_qv2_002_daily_mfe_mae_are_day_granular_bounds_not_post_fill_observations(scenarios):
    strict_mfe = 0
    for paths, o, h, low, c, t, emin, emax, stop, target in scenarios:
        d = simulate(o, h, low, c, t, emin, emax, stop, target, ExecConfig(cost_bps_per_side=0.0))
        tr = _truth(paths, t, emin, emax, stop, target)
        if not d.filled:
            continue
        assert d.mfe >= tr["mfe"] - 1e-6 and d.mae <= tr["mae"] + 1e-6
        strict_mfe += d.mfe > tr["mfe"] + 1e-6
    assert strict_mfe > 0  # CONFIRMED: whole-day highs before the fill / after the exit inflate reported MFE


# ---- QV2-006 / V12: costs are applied exactly once ----------------------------------------------------------------------
def test_v12_cost_applied_once_simulate_vs_post_applied():
    rng = np.random.default_rng(7)
    checked = 0
    for _ in range(500):
        _, o, h, low, c = _paths(rng)
        t = 3
        close = float(c[t])
        emin, emax, stop = close - 0.75, close + 0.75, close - 3.0
        target = close + 6.0
        base = simulate(o, h, low, c, t, emin, emax, stop, target, ExecConfig(cost_bps_per_side=0.0))
        for bps in (10.0, 25.0, 50.0):
            r = simulate(o, h, low, c, t, emin, emax, stop, target, ExecConfig(cost_bps_per_side=bps))
            assert (r.filled, r.outcome, r.entry_index, r.exit_index) == (base.filled, base.outcome, base.entry_index, base.exit_index)
            if r.filled:
                assert r.return_net == pytest.approx(A.net_at([base.return_gross], bps)[0], abs=2e-6)
                assert r.r_multiple == pytest.approx(A.r_at([base.entry_price], [base.exit_price], [stop], bps)[0], abs=2e-4)
                checked += 1
    assert checked > 300


# ---- V02: the research pipeline is deterministic ----------------------------------------------------------------------
def test_v02_event_build_is_deterministic_on_a_fixture():
    from datetime import date

    from apex_worker.demo.synthetic import generate
    from apex_worker.features.panel import panel_from_synthetic
    from apex_worker.research.dataset import DatasetSpec, build_events
    from apex_worker.signals.strategies.library import STRATEGIES

    def once():
        market = generate(stocks_per_sector=2, start=date(2024, 1, 2), end=date(2025, 12, 31))
        panel = panel_from_synthetic(market)
        bars = {s: market.adjusted(s) for s in market.bars}
        ev = build_events(panel, bars, DatasetSpec(strategies=list(STRATEGIES.values()), execution=ExecConfig(cost_bps_per_side=0.0)))
        return ev, int(pd.util.hash_pandas_object(ev, index=True).sum())

    ev1, h1 = once()
    _ev2, h2 = once()
    assert len(ev1) > 20 and h1 == h2


# ---- QV2-003 / V19-V20: the protocol-1 bootstrap tail is not a calibrated p-value -----------------------------------------
def _old_p(values, weeks, n_boot=400):
    means = A.boot_means(values, weeks, n_boot=n_boot, seed=11)
    return float((means <= 0).mean())


def _clustered_null(rng, n_dates=15, per_date=9, hold=20, span=350):
    """Zero-mean trades: one 20-session window per trade from a shared market + sector + idiosyncratic daily process.
    Decision dates fall in n_dates distinct weeks; trades in adjacent weeks share most of their holding windows."""
    m = rng.normal(0, 0.009, span + hold)
    sectors = rng.normal(0, 0.006, (11, span + hold))
    starts = np.sort(rng.choice(np.arange(0, span, 5), n_dates, replace=False))
    vals, weeks = [], []
    for s in starts:
        for _ in range(per_date):
            sec = int(rng.integers(11))
            idio = rng.normal(0, 0.012, hold)
            vals.append(float((m[s : s + hold] + sectors[sec, s : s + hold] + idio).sum()))
            weeks.append(str(s // 5))
    v = np.array(vals)
    return v - 0.0, np.array(weeks)  # true mean is exactly 0 by construction (no drift terms)


def test_qv2_003_old_bootstrap_tail_rejects_true_nulls_too_often_under_clustering():
    rng = np.random.default_rng(3)
    reps, alpha = 300, 0.05
    rejections = sum(_old_p(*_clustered_null(rng)) < alpha for _ in range(reps))
    rate = rejections / reps
    # CONFIRMED defect: with 15 decision weeks and overlapping 20-session holds the nominal 5% test rejects a true null
    # far more often (measured rate recorded in docs/quant-v2/M0_AUDIT.md); protocol-2 must not use it as a p-value
    assert rate > 2 * alpha, rate


# ---- V03 support: per-symbol content hashes localise dataset drift without publishing prices ------------------------------
def test_v03_content_hashes_localise_a_single_changed_bar():
    from apex_worker.research.realeq.data import content_hashes

    idx = pd.bdate_range("2024-12-02", "2025-01-31")
    df = pd.DataFrame({"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 1000}, index=idx)
    a = content_hashes(df)
    df2 = df.copy()
    df2.iloc[-1, df2.columns.get_loc("volume")] = 1001  # e.g. a late print revising the final session's volume
    b = content_hashes(df2)
    assert a["all"] != b["all"] and a["last_session"] != b["last_session"]
    assert a["by_year"]["2024"] == b["by_year"]["2024"] and a["by_year"]["2025"] != b["by_year"]["2025"]
    assert all(len(v) == 16 for v in a["by_year"].values())
