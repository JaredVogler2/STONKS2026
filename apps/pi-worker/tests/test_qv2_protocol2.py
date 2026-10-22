"""Quant V2 M2-M4: universe U1, frozen realeq-protocol-2, v2 features, S1-S5 plugins, study, runner (amendments 03-06, 12).

Synthetic data only (engineering controls, never evidence about markets). Test names cite the acceptance case (Vxx).
"""

import json
import math
import re
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest

from apex_worker.features.panel import AssetMeta, build_panel
from apex_worker.features.quant_v2 import residual_scores
from apex_worker.market.adapters.alpaca import AlpacaAdapter
from apex_worker.market.calendar import default_calendar
from apex_worker.market.ratelimit import TokenBucket
from apex_worker.research.realeq import study_v2 as SV
from apex_worker.research.realeq import universe_v2 as U
from apex_worker.research.realeq.data_v2 import data_end, quarantine_dates
from apex_worker.research.realeq.fixture_v2 import factor_frames_v2
from apex_worker.research.realeq.protocol import PROTOCOL_VERSION
from apex_worker.research.realeq.protocol_v2 import PROTOCOL_V2, compute_boundaries_v2, protocol_v2_sha256
from apex_worker.research.realeq.runner import RunConfig, run
from apex_worker.signals.strategies.quant_v2 import (
    QV2_STRATEGIES,
    S5Config,
    allocation_returns,
    earnings_event_frame,
    retest_sessions,
    s5_weights,
)

REPO = Path(__file__).resolve().parents[3]


# ---- frozen protocol --------------------------------------------------------------------------------------------------
def test_protocol_v2_hash_is_pinned_in_the_human_copy():
    text = (REPO / "research-results/real-equities/PROTOCOL_V2.md").read_text()
    stated = set(re.findall(r"\b[0-9a-f]{64}\b", text))
    assert protocol_v2_sha256() in stated, "PROTOCOL_V2 changed: that is a new protocol version, not an edit"
    assert "realeq-protocol-2" in text and "protocol_sha256: " + protocol_v2_sha256() in text


def test_protocol_v2_bounds_research_and_never_enables_trading():
    p = PROTOCOL_V2
    arms = sum(len(v.get("arms", [])) for v in p["strategies"].values())
    assert arms <= p["research_budget"]["grid_arms_max"] == 12
    assert p["mode"] == "RESEARCH_ONLY" and p["production_promotion_allowed"] is False and p["group_actionable_allowed"] is False
    assert p["strategies"]["S4"]["status"] == "NOT_EVALUABLE_MISSING_INPUT"
    assert p["inference"]["primary_family"] == ["S1", "S2", "S3", "S5"] and p["primary_cost_bps"] == 25
    assert p["chronology"]["known_old_holdout"].startswith("2025-05-02..2026-09-21")


def test_protocol_1_remains_the_default():
    assert RunConfig(source="fixture", experiment_id="demo-x", out_dir=Path("/tmp/x")).protocol == PROTOCOL_VERSION


def test_v45_boundaries_keep_presample_labels_before_the_contaminated_window():
    cal = default_calendar()
    idx = pd.DatetimeIndex(pd.to_datetime([d.isoformat() for d in cal.sessions_between(date(2016, 1, 4), date(2026, 10, 16))]))
    b = compute_boundaries_v2(idx)
    assert (b.presample_start, b.presample_end, b.dev_start) == ("2017-01-03", "2019-09-20", "2019-10-23")
    lo, hi = b.presample_range
    assert lo == 252 and idx[hi + PROTOCOL_V2["chronology"]["label_sessions"]] < pd.Timestamp("2019-10-23")
    assert idx[b.dev_range[1] + 22] <= idx[-1]


# ---- universe (M2) ----------------------------------------------------------------------------------------------------
def test_u1_snapshot_one_listing_per_issuer_and_contains_u0():
    rows = U.snapshot()["rows"]
    assert len(rows) == 503 and U.snapshot()["classification"] == "SURVIVORSHIP_LIMITED_CURRENT_CONSTITUENTS"
    assert sorted(U.dropped_share_classes()) == ["FOX", "GOOG", "NWS"]
    assert len(U.u1_stocks()) == 500 and set(U.u0_stocks()) <= set(U.u1_stocks())
    assert PROTOCOL_V2["universe"]["snapshot_sha256"] == U.snapshot_sha256()
    assert "CC BY-SA 4.0" in U.snapshot()["source"]["license"]


def test_membership_starts_at_date_added():
    added = {c.symbol: c.date_added for c in U.u1_constituents()}
    late = next(s for s, d in added.items() if d >= "2023-01-01")
    idx = pd.DatetimeIndex(["2019-06-03", added[late], "2026-01-05"])
    mem = U.membership(idx, [late])[late]
    assert list(mem) == [False, True, True]


def test_panel_ranks_and_breadth_use_members_only():
    idx = pd.bdate_range("2020-01-01", periods=300)
    rng = np.random.default_rng(0)

    def bars(drift):
        c = 50 * np.exp(np.cumsum(rng.normal(drift, 0.01, len(idx))))
        return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 2e6}, index=idx)

    b = {"SPY": bars(0.0003), "AAA": bars(0.0), "BBB": bars(0.0), "CCC": bars(0.01)}  # CCC: extreme momentum
    meta = {"SPY": AssetMeta("SPY", None, "benchmark")} | {s: AssetMeta(s, "tech", "stock") for s in ("AAA", "BBB", "CCC")}
    member = {s: pd.Series(True, index=idx) for s in ("AAA", "BBB")} | {"CCC": pd.Series(idx >= idx[250], index=idx)}
    full = build_panel(b, meta, "SPY", {}, membership=member)
    without = build_panel({k: v for k, v in b.items() if k != "CCC"}, {k: v for k, v in meta.items() if k != "CCC"},
                          "SPY", {})
    t = idx[200]  # CCC not yet a member: it must not enter AAA's rank
    assert full.features["AAA"].loc[t, "xs_rank_ret_20"] == pytest.approx(without.features["AAA"].loc[t, "xs_rank_ret_20"])
    assert math.isnan(full.features["CCC"].loc[t, "xs_rank_ret_20"])
    assert not math.isnan(full.features["CCC"].loc[idx[-1], "xs_rank_ret_20"])


# ---- v2 features and leakage (V24, V33) -------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def small():
    bars = factor_frames_v2(per_sector=4, seed=3)
    b = compute_boundaries_v2(bars["SPY"].index)
    return bars, b, SV.prepare(bars)


def test_v24_v2_features_are_causal_under_truncation(small):
    bars, _b, pre = small
    cut = pre.sessions[1500]
    short = {s: df[df.index <= cut] for s, df in bars.items()}
    pre2 = SV.prepare(short)
    cols = ["v2_sector_pct_ret_20", "v2_leader", "v2_peak_age_10", "v2_pullback_atr", "v2_trough_low",
            "v2_resid_score_20", "v2_xs_pct_resid", "v2_sector_breadth"]
    for s in pre.stocks[:12]:
        a = pre.frames[s].loc[:cut, cols].iloc[300:]
        b2 = pre2.frames[s].loc[:cut, cols].iloc[300:]
        pd.testing.assert_frame_equal(a, b2, check_exact=False, rtol=1e-9, atol=1e-12)


def test_v33_residual_betas_never_use_the_scoring_window():
    rng = np.random.default_rng(1)
    n = 400
    x1, x2 = rng.normal(0, 0.01, n), rng.normal(0, 0.005, n)
    y = 1.1 * x1 + 0.5 * x2 + rng.normal(0, 0.01, n)
    base, _ = residual_scores(y, x1, x2)
    y2 = y.copy()
    y2[380:] += 0.03  # a jump inside the last scoring window
    moved, _ = residual_scores(y2, x1, x2)
    # betas at t come from [t-145, t-20]: the score at 399 sees the jump as residual, betas fitted before 380 are unchanged
    assert moved[399] > base[399] + 1
    assert np.allclose(base[:380], moved[:380], equal_nan=True)


def _frame(n=30, **cols):
    idx = pd.bdate_range("2021-01-01", periods=n)
    base = {"close": 100.0, "atr_14": 2.0, "dist_sma50_atr": 1.0, "sma50_slope_20": 0.02, "ret_20": 0.05, "gap_1": 0.0,
            "v2_sector_breadth": 0.6, "rs_bench_20": 0.01, "rel_volume_1": 1.0}
    df = pd.DataFrame({k: [v] * n for k, v in base.items()}, index=idx)
    for k, v in cols.items():
        df[k] = v
    return df


S1_OK = {"v2_peak_age_10": 3.0, "v2_leader_at_peak": 1.0, "v2_pullback_atr": 1.0, "v2_prev_high": 99.0, "v2_trough_low": 97.0}


@pytest.mark.parametrize(("change", "fires"), [
    ({}, True),
    ({"v2_peak_age_10": 1.0}, False),  # peak too recent: no pullback yet
    ({"v2_peak_age_10": 6.0}, False),  # stale peak
    ({"v2_pullback_atr": 0.3}, False),  # too shallow
    ({"v2_pullback_atr": 1.8}, False),  # too deep
    ({"v2_leader_at_peak": 0.0}, False),  # not a leader at the peak
    ({"dist_sma50_atr": -0.1}, False),  # below SMA50
    ({"sma50_slope_20": -0.01}, False),  # SMA50 falling
    ({"v2_prev_high": 101.0}, False),  # no confirmation close above the prior high
    ({"v2_peak_age_10": float("nan")}, False),  # missing input never fires
])
def test_v31_s1_trigger_scenarios(change, fires):
    f = _frame(**(S1_OK | change))
    tf = QV2_STRATEGIES["s1_leader_pullback"].trigger_frame(f)
    assert bool(tf["trigger"].iloc[-1]) is fires
    if fires:
        assert tf["invalidation_hint"].iloc[-1] == pytest.approx(97.0 - 0.25 * 2.0)


def test_v32_s1_mechanism_pool_is_leaders_in_any_pullback_state():
    f = _frame(v2_leader=1.0)
    assert QV2_STRATEGIES["s1_leader_pullback"].mechanism_pool(f).all()
    assert not QV2_STRATEGIES["s1_leader_pullback"].mechanism_pool(_frame(v2_leader=0.0)).any()


@pytest.mark.parametrize(("change", "fires"), [
    ({}, True), ({"v2_xs_pct_resid": 0.7}, False), ({"ret_20": -0.01}, False), ({"v2_sector_breadth": 0.4}, False),
    ({"gap_1": 0.08}, False), ({"v2_xs_pct_resid": float("nan")}, False),
])
def test_s2_trigger_scenarios_and_rebalance_cadence(change, fires):
    f = _frame(n=25, **({"v2_xs_pct_resid": 0.9, "v2_xs_pct_ret_20": 0.5} | change))
    trig = QV2_STRATEGIES["s2_residual_momentum"].trigger_frame(f)["trigger"].to_numpy()
    assert trig[20] is np.bool_(fires) and not trig[21:25].any() and not trig[1:5].any()
    pool = QV2_STRATEGIES["s2_residual_momentum"].mechanism_pool(f).to_numpy()
    assert not pool[20]  # the raw-momentum pool uses the raw percentile (0.5 here), not the residual one


def _s3_frame(n=12):
    f = _frame(n=n, breakout_today=0.0, squeeze_pct_120=0.2, base_length=15.0, base_depth_pct=0.08, rs_sector_20=0.01,
               pivot_high_20=100.0, v2_low=101.0)
    f.loc[f.index[5], ["breakout_today", "rel_volume_1", "close"]] = [1.0, 2.0, 102.0]
    return f


def test_v35_s3_immediate_and_retest_share_the_opportunity():
    f = _s3_frame()
    f.loc[f.index[7], ["v2_low", "close"]] = [100.5, 101.0]  # revisits pivot + 0.5 ATR, closes above the pivot
    f.loc[f.index[8], ["v2_low", "close"]] = [100.2, 101.0]  # a second retest of the same breakout is ignored
    a = QV2_STRATEGIES["s3a_breakout_immediate"].trigger_frame(f)
    c = QV2_STRATEGIES["s3c_breakout_retest"].trigger_frame(f)
    assert list(np.flatnonzero(a["trigger"])) == [5] and a["opportunity"].iloc[5] == 5
    assert list(np.flatnonzero(c["trigger"])) == [7] and c["opportunity"].iloc[7] == 5
    assert c["invalidation_hint"].iloc[7] == pytest.approx(100.0 - 0.5 * 2.0)


def test_a1_s3_retest_of_an_older_breakout_is_not_hidden_by_a_newer_one():
    """Breakouts at 5 and 6. Session 7 retests breakout 5 (low <= 100 + 1, close > 100) but not breakout 6 (close <
    102.5). Before A1 only the most recent breakout was checked, so this retest was lost."""
    f = _s3_frame()
    f.loc[f.index[6:], "pivot_high_20"] = 102.5
    f.loc[f.index[6], ["breakout_today", "rel_volume_1", "close", "v2_low"]] = [1.0, 2.0, 103.0, 102.6]
    f.loc[f.index[7], ["v2_low", "close"]] = [100.5, 101.0]
    a = QV2_STRATEGIES["s3a_breakout_immediate"].trigger_frame(f)
    assert list(np.flatnonzero(a["trigger"])) == [5, 6]
    c = QV2_STRATEGIES["s3c_breakout_retest"].trigger_frame(f)
    assert list(np.flatnonzero(c["trigger"])) == [7] and c["opportunity"].iloc[7] == 5
    assert retest_sessions(f) == {5: (7, pytest.approx(100.0 - 0.5 * 2.0))}


def test_v36_s3_runaway_breakout_has_no_retest_signal():
    f = _s3_frame()
    f.loc[f.index[9], ["v2_low", "close"]] = [100.5, 101.0]  # 4 sessions later: outside the 1-3 session window
    assert not QV2_STRATEGIES["s3c_breakout_retest"].trigger_frame(f)["trigger"].any()
    g = _s3_frame()
    g.loc[g.index[6], ["v2_low", "close"]] = [100.5, 99.5]  # revisited but closed below the pivot: no retest
    assert not QV2_STRATEGIES["s3c_breakout_retest"].trigger_frame(g)["trigger"].any()


def test_v39_s4_without_point_in_time_events_never_triggers():
    tf = QV2_STRATEGIES["s4_earnings_continuation"].trigger_frame(_frame(n=40))
    assert not tf["trigger"].any()
    assert QV2_STRATEGIES["s4_earnings_continuation"].spec.requires_events is True


def test_v37_v38_event_availability_maps_to_the_first_tradable_session():
    cal = default_calendar()
    days = cal.sessions_between(date(2024, 5, 1), date(2024, 5, 10))
    idx = pd.DatetimeIndex(pd.to_datetime([d.isoformat() for d in days]))
    close = {d: pd.Timestamp(cal.session(d.date()).close_utc) for d in idx}
    ev = [{"available_at": "2024-05-02T20:15:00Z"},  # 16:15 ET after the close -> next session (May 3)
          {"available_at": "2024-05-06T12:00:00Z"},  # 08:00 ET pre-market -> that session (May 6)
          {"available_at": None},                   # unknown publication time: dropped, never guessed
          {"available_at": "2024-05-09T20:00:00Z"}]  # exactly at the close -> the next session (May 10)
    flag = earnings_event_frame(ev, idx, close)
    assert [str(d.date()) for d in idx[flag.to_numpy()]] == ["2024-05-03", "2024-05-06", "2024-05-10"]


def test_s4_mechanics_on_planted_events():
    f = _frame(n=10, clv_1=0.8, rel_volume_1=2.5)
    f["close"] = [100, 100, 100, 105, 104.5, 104.8, 104.8, 104.8, 104.8, 104.8]
    f["ev_reaction_session"] = [0, 0, 0, 1, 0, 0, 0, 0, 0, 0]
    tf = QV2_STRATEGIES["s4_earnings_continuation"].trigger_frame(f)
    assert list(np.flatnonzero(tf["trigger"])) == [5]  # two completed sessions kept >= 75% of the +5% reaction


def test_v40_s5_weights_abstain_and_cap_and_accounting():
    idx = pd.bdate_range("2020-01-01", periods=30)

    def etf(r60, rv, up):
        return pd.DataFrame({"ret_60": r60, "rv_20": rv, "dist_sma50_atr": 1.0 if up else -1.0}, index=idx)

    etfs = {"XA": etf(0.10, 0.2, True), "XB": etf(0.05, 0.2, True), "XC": etf(0.20, 0.2, False), "XD": etf(0.01, 0.2, True)}
    reg = pd.Series("trending_up", index=idx)
    w = s5_weights(etfs, reg, idx, S5Config(rebalance=10, top_n=3))
    row = w.iloc[0]
    assert row["XC"] == 0 and row[["XA", "XB", "XD"]].sum() == pytest.approx(1.0)
    reg.iloc[10] = "risk_off"
    w2 = s5_weights(etfs, reg, idx, S5Config(rebalance=10, top_n=3))
    assert w2.iloc[10].sum() == 0 and w2.iloc[1].isna().all()
    flat = pd.Series(100.0, index=idx)
    rets, expo = allocation_returns(w2, {k: flat for k in etfs}, {k: flat for k in etfs}, cost_bps=25)
    assert rets.iloc[1] == pytest.approx(-0.0025 * 1.0)  # buying 100% at the open costs 25 bps once
    assert rets.iloc[11] == pytest.approx(-0.0025 * 1.0)  # selling everything into cash on the risk-off rebalance
    assert expo.iloc[12] == 0 and (rets.iloc[12:21] == 0).all()  # cash: no return, no cost
    assert rets.iloc[21] == pytest.approx(-0.0025)  # the next rebalance (session 20, trending) buys back in
    _r, _x, xo = allocation_returns(w2, {k: flat for k in etfs}, {k: flat for k in etfs}, cost_bps=25,
                                    with_open_exposure=True)
    assert xo.iloc[1] == pytest.approx(1.0) and expo.iloc[0] == 0  # bought at the open of 1: invested intraday only
    assert xo.iloc[11] == 0 and expo.iloc[10] == pytest.approx(1.0)  # sold at the open of 11: invested overnight only


def test_a1_s5_exposure_matched_benchmark_follows_the_rebalance_timing():
    """On the day S5 buys at the open, the benchmark must earn SPY's open-to-close move only; on the day it sells at
    the open, SPY's overnight move only. A0 applied yesterday's close exposure to the whole close-to-close return."""
    from types import SimpleNamespace

    idx = pd.bdate_range("2020-01-01", periods=30)
    feats = pd.DataFrame({"ret_60": 0.1, "rv_20": 0.2, "dist_sma50_atr": 1.0}, index=idx)
    flat = pd.Series(100.0, index=idx)
    spy_c = pd.Series(100.0, index=idx)
    spy_o = spy_c.copy()
    spy_o.iloc[1], spy_c.iloc[1] = 102.0, 101.0  # +2% overnight, -0.98% intraday
    spy_o.iloc[11], spy_c.iloc[11] = 99.0, 100.0  # -1% overnight from 100, +1.01% intraday
    spy_c.iloc[10] = 100.0
    etfs = ("XA", "XB", "XC")  # top 3: three qualifiers make a fully invested book
    bars = {e: pd.DataFrame({"open": flat, "close": flat}) for e in etfs} | {
        "SPY": pd.DataFrame({"open": spy_o, "close": spy_c})}
    reg = pd.Series("trending_up", index=idx)
    reg.iloc[10] = "risk_off"
    pre = SimpleNamespace(etf_features=dict.fromkeys(etfs, feats), bars=bars, sessions=idx, regime=reg)
    data = SV.S5Data(pre)
    _r, x, xo, bench = data.series(pre, "R10", 0.0)
    assert xo.iloc[1] == pytest.approx(1.0) and x.iloc[0] == 0
    assert bench.iloc[1] == pytest.approx(101.0 / 102.0 - 1)  # intraday only; A0 gave 0.0
    assert x.iloc[10] == pytest.approx(1.0) and xo.iloc[11] == 0
    assert bench.iloc[11] == pytest.approx(99.0 / 100.0 - 1)  # overnight only; A0 gave the full +0%


# ---- dispositions (amendment 04 section 6) -------------------------------------------------------------------------------
def _primary(status="OK", mean=0.01, p=0.001, up=0.02):
    return {"summary": {"status": status, "mean": mean, "p_one_sided_max": p, "upper_95_max": up}}


GOOD = {"mean_per_signal_net": 0.004, "mean_paired_50bps": 0.009, "mean_paired_touch_fill_sensitivity": 0.009,
        "regimes": {"trending_up": 60, "risk_off": 40}, "ambiguous_share_of_fills": 0.05,
        "mean_per_signal_net_optimistic": 0.006, "mean_paired_50bps_optimistic": 0.011,
        "mean_paired_touch_fill_sensitivity_optimistic": 0.011}
OPT_OK = {"holm_opt": 0.008, "half_opt": [0.01, 0.01], "dev_mean_opt": 0.005}
FAIL = _primary(mean=-0.001, p=0.8, up=0.002)


@pytest.mark.parametrize(("primary", "holm", "desc", "dev", "half", "opt", "kw", "want"), [
    (_primary(), 0.004, GOOD, 0.004, [0.01, 0.01], None, {}, "RULE_VALIDATED_RESEARCH_OOS"),
    (_primary("INSUFFICIENT_CLUSTERS"), None, GOOD, 0.004, [0.01, 0.01], None, {}, "INCONCLUSIVE"),
    (FAIL, 1.0, GOOD, 0.004, [0.0, -0.002], None, {}, "FAILED_WITH_POWER"),
    (_primary(mean=-0.001, p=0.8, up=0.01), 1.0, GOOD, 0.004, [0.0, -0.002], None, {}, "INCONCLUSIVE"),
    (_primary(mean=0.004, p=0.08), 0.2, GOOD, 0.004, [0.01, 0.01], None, {}, "RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD"),
    (_primary(), 0.004, GOOD, -0.001, [0.01, 0.01], None, {}, "RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD"),
    (_primary(), 0.004, GOOD | {"ambiguous_share_of_fills": 0.4}, 0.004, [0.01, 0.01], None, {}, "UNCERTAINTY_TOO_HIGH"),
    # A1: the machine copy governs: > 25% ambiguous fills is UNCERTAINTY whatever the other gates say
    (FAIL, 1.0, GOOD | {"ambiguous_share_of_fills": 0.4}, 0.004, [0.0, -0.002], None, {}, "UNCERTAINTY_TOO_HIGH"),
    # A1: the optimistic leg must pass EVERY gate, with its own Holm p
    (_primary(mean=0.004, p=0.03), 0.12, GOOD, 0.004, [0.01, 0.01], _primary(mean=0.008, p=0.002), OPT_OK,
     "UNCERTAINTY_TOO_HIGH"),
    (_primary(mean=0.004, p=0.03), 0.12, GOOD, 0.004, [0.01, 0.01], _primary(mean=0.008, p=0.002),
     OPT_OK | {"holm_opt": 0.09}, "RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD"),
    (_primary(mean=0.004, p=0.03), 0.12, GOOD, 0.004, [0.01, 0.01], _primary(mean=0.008, p=0.002),
     OPT_OK | {"half_opt": [0.01, -0.001]}, "RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD"),
    (_primary(mean=0.004, p=0.03), 0.12, GOOD, 0.004, [0.01, 0.01], _primary(mean=0.004, p=0.03), OPT_OK,
     "RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD"),  # identical legs: no ambiguity to blame
    # A1: FAILED_WITH_POWER needs both legs to fail with power when they differ
    (FAIL, 1.0, GOOD, 0.004, [0.0, -0.002], _primary(mean=0.001, p=0.3, up=0.006), {}, "INCONCLUSIVE"),
    (FAIL, 1.0, GOOD, 0.004, [0.0, -0.002], _primary(mean=-0.0005, p=0.7, up=0.0025), {}, "FAILED_WITH_POWER"),
])
def test_v26_v41_v42_dispositions(primary, holm, desc, dev, half, opt, kw, want):
    assert SV.classify_v2(primary, holm, desc, dev, half, opt, **kw)[0] == want


S5_GOOD = {"mean_daily_net": 0.0004, "mean_daily_active_50bps": 0.0002,
           "invested_sessions_by_regime": {"trending_up": 300, "rebound": 80, "choppy_neutral": 60}}


@pytest.mark.parametrize(("desc", "want"), [
    (S5_GOOD, "RULE_VALIDATED_RESEARCH_OOS"),
    (S5_GOOD | {"mean_daily_active_50bps": -0.00001}, "RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD"),
    (S5_GOOD | {"invested_sessions_by_regime": {"trending_up": 400, "rebound": 20}}, "RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD"),
    (S5_GOOD | {"mean_daily_net": -0.0001}, "INCONCLUSIVE"),
])
def test_a1_s5_gates_cost_regimes_and_absolute_return(desc, want):
    """S5 has its own gates (A1): touch-fill and daily-bar ambiguity do not apply to market orders at the open."""
    assert SV.classify_v2(_primary(mean=0.0002), 0.01, desc, 0.0001, [0.0001, 0.0002], None, kind="s5")[0] == want


def test_a1_holm_counts_members_without_a_p_value_as_one():
    full, shown = SV._holm_full({"S1": 0.01, "S2": None, "S3": 0.02, "S5": 0.04})
    assert full == pytest.approx({"S1": 0.04, "S3": 0.06, "S5": 0.08, "S2": 1.0})  # A0 used m = 3: S1 0.03
    assert shown["S2"] is None and shown["S1"] == pytest.approx(0.04)


# ---- data layer -----------------------------------------------------------------------------------------------------------
def test_finality_lag_and_quarantine_dates():
    cal = default_calendar()
    now = datetime(2026, 10, 22, 22, 0, tzinfo=UTC)  # after Thursday's close
    assert data_end(cal, now) == date(2026, 10, 20)
    q = quarantine_dates([{"type": "spin_offs", "source_symbol": "GE", "new_symbol": "GEHC", "ex_date": "2023-01-04"},
                          {"type": "forward_splits", "symbol": "NVDA", "ex_date": "2024-06-10"}])
    assert q["GE"] == [pd.Timestamp("2023-01-04")] and "NVDA" not in q
    assert q["HON"] == [pd.Timestamp("2026-06-29")] and q["EXC"] == [pd.Timestamp("2022-02-02")]


@pytest.mark.asyncio
async def test_corporate_actions_cover_every_symbol_and_page():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        p = dict(req.url.params)
        seen.append((len(p["symbols"].split(",")), p.get("page_token")))
        if p.get("page_token") is None and p["symbols"].startswith("S000"):
            return httpx.Response(200, json={"corporate_actions": {"spin_offs": [{"source_symbol": "S001", "ex_date": "2020-01-02"}]},
                                             "next_page_token": "p2"})
        return httpx.Response(200, json={"corporate_actions": {"forward_splits": [{"symbol": "S150", "ex_date": "2021-01-04"}]},
                                         "next_page_token": None})

    async def no_sleep(_s):
        return None

    ad = AlpacaAdapter("PKTEST", "x", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                       bucket=TokenBucket(1e9, 1e9), sleep=no_sleep)
    out = await ad.get_corporate_actions_paged([f"S{i:03d}" for i in range(150)], date(2016, 1, 4), date(2026, 10, 1))
    assert seen == [(100, None), (100, "p2"), (50, None)]
    assert {a["type"] for a in out} == {"spin_offs", "forward_splits"} and len(out) == 3


@pytest.mark.asyncio
async def test_corporate_actions_fall_back_to_yearly_windows_when_the_range_is_rejected():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        p = dict(req.url.params)
        calls.append((p["start"], p["end"]))
        if p["start"][:4] != p["end"][:4]:
            return httpx.Response(422, json={"message": "date range too long"})
        return httpx.Response(200, json={"corporate_actions": {"spin_offs": [{"source_symbol": "AAA", "ex_date": p["start"]}]}})

    async def no_sleep(_s):
        return None

    ad = AlpacaAdapter("PKTEST", "x", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                       bucket=TokenBucket(1e9, 1e9), sleep=no_sleep)
    out = await ad.get_corporate_actions_paged(["AAA"], date(2016, 1, 4), date(2018, 6, 30))
    assert calls[0] == ("2016-01-04", "2018-06-30") and calls[1:] == [("2016-01-04", "2016-12-31"),
                                                                       ("2017-01-01", "2017-12-31"),
                                                                       ("2018-01-01", "2018-06-30")]
    assert len(out) == 3


# ---- study: quarantine, withheld arms, engine self-checks (V25, V42) ------------------------------------------------------
def test_quarantined_windows_are_excluded(small):
    _bars, b, pre = small
    sigs = [SV.Signal(pre.stocks[0], b.presample_range[0] + 40, None, 0.0)]
    SV.OPEN = pre.open_sessions
    base = SV.run_arm("t", "E0X1", sigs, pre, b, {}, stop_k=2.0, controls=False)
    day = pre.sessions[b.presample_range[0] + 45]
    q = SV.run_arm("t", "E0X1", sigs, pre, b, {pre.stocks[0]: [day]}, stop_k=2.0, controls=False)
    assert base.funnel["PRESAMPLE"]["kept"] == 1 and q.funnel["PRESAMPLE"]["quarantined"] == 1
    assert q.funnel["PRESAMPLE"]["kept"] == 0 and q.funnel["DEV"]["signals"] == 0


def test_a1_presample_trade_reaching_dev_is_excluded(small, monkeypatch):
    """A delayed time exit (missing horizon bar) can push a PRESAMPLE trade's exit into DEV: it is excluded (A1)."""
    from types import SimpleNamespace

    from apex_worker.research.controls import SimResult
    from apex_worker.research.execution_v2 import Leg, TradeV2

    _bars, b, pre = small
    t, dev0 = b.presample_range[1], b.dev_range[0]

    def fake(exit_index):
        leg = Leg("TIME", exit_index, 101.0, True)
        tr = TradeV2(True, "FILLED", t + 1, 100.0, "OPEN", leg, leg, mfe_bounds=(0.0, 0.01), mae_bounds=(-0.01, 0.0))
        return lambda *a, **k: SimResult(SimpleNamespace(mid=100.0, stop=98.0), tr)

    SV.OPEN = pre.open_sessions
    sig = [SV.Signal(pre.stocks[0], t, None, 0.0)]
    monkeypatch.setattr(SV, "simulate_signal", fake(dev0))
    r = SV.run_arm("t", "E0X1", sig, pre, b, {}, stop_k=2.0, controls=False)
    assert r.funnel["PRESAMPLE"]["label_crosses_dev"] == 1 and r.funnel["PRESAMPLE"]["kept"] == 0
    monkeypatch.setattr(SV, "simulate_signal", fake(dev0 - 1))
    r = SV.run_arm("t", "E0X1", sig, pre, b, {}, stop_k=2.0, controls=False)
    assert r.funnel["PRESAMPLE"]["label_crosses_dev"] == 0 and r.funnel["PRESAMPLE"]["kept"] == 1


def test_v25_lookahead_trap_detected_and_placebo_quiet():
    bars = factor_frames_v2(per_sector=10, seed=5)
    b = compute_boundaries_v2(bars["SPY"].index)
    pre = SV.prepare(bars)
    SV.OPEN = pre.open_sessions
    chk = SV.self_checks(pre, b, {})
    assert chk["lookahead_trap"]["detected"] is True and chk["lookahead_trap"]["test"]["mean"] > 0.05
    assert chk["placebo"]["test"]["status"] == "OK" and chk["placebo"]["test"]["p_one_sided_max"] > 0.01


def test_protocol2_runner_validates_inputs(tmp_path):
    base = {"source": "fixture", "experiment_id": "demo-p2", "out_dir": tmp_path / "o", "protocol": "realeq-protocol-2"}
    assert run(RunConfig(**base, universe="fixed78")).status == "INVALID_INPUT"
    assert run(RunConfig(**base, universe="u1-sp500", time_start=date(2018, 1, 2))).status == "INVALID_INPUT"
    assert run(RunConfig(**(base | {"protocol": "realeq-protocol-9"}), universe="u1-sp500")).status == "INVALID_INPUT"
    assert not (tmp_path / "o").exists()


def test_v42_protocol2_demo_end_to_end(tmp_path, monkeypatch):
    """Full DEMO pipeline on a small calm fixture (the 350-stock abort rule is relaxed for this test only)."""
    monkeypatch.setitem(PROTOCOL_V2["abort_rules"], "min_usable_stocks", 60)
    monkeypatch.setenv("APEX_DEMO_PER_SECTOR", "8")
    out = tmp_path / "demo"
    res = run(RunConfig(source="fixture", experiment_id="demo-p2-e2e", out_dir=out, universe="u1-sp500",
                        protocol="realeq-protocol-2", env={"APEX_DEMO_PER_SECTOR": "8"}))
    assert res.status == "COMPLETED", res.reason
    files = sorted(p.name for p in out.iterdir())
    assert files == sorted(["manifest.json", "data_quality.json", "strategy_metrics.json", "trades_summary.csv",
                            "comparisons.md", "recommendations.md", "execution_log.md"])
    m = json.loads((out / "strategy_metrics.json").read_text())
    assert m["data_class"] == "SYNTHETIC_FIXTURE_DEMO" and m["protocol_version"] == "realeq-protocol-2"
    assert set(m["aggregate"]["dispositions"]) == {"S1", "S2", "S3", "S4", "S5"}
    assert m["aggregate"]["dispositions"]["S4"] == "NOT_EVALUABLE_MISSING_INPUT"
    for sid in ("S1", "S2", "S3", "S5"):
        st = m["strategies"][sid]
        for arm, v in st["arms"].items():
            if arm != st["selection"]["chosen_arm"]:
                assert "withheld" in v["PRESAMPLE"], (sid, arm)
                if sid != "S5":
                    assert isinstance(st["funnel"][arm]["PRESAMPLE"], str), (sid, arm)  # counts withheld too
        assert st["primary"]["evidence_type"].startswith("PRESAMPLE_BACKWARD_TIME_HISTORICAL_ROBUSTNESS")
    presample_cfgs = {(c["name"], c["arm"]) for c in m["configurations_attempted"]
                      if c["segment"] == "PRESAMPLE" and c["name"] in ("S1", "S2", "S3", "S5")}
    assert presample_cfgs == {(sid, m["strategies"][sid]["selection"]["chosen_arm"]) for sid in ("S1", "S2", "S3", "S5")}
    assert set(m["strategies"]["S5"]["primary"]["describe"]["cost_grid"]) == {"0bps", "10bps", "25bps", "50bps"}
    assert m["multiplicity"]["family_size_used"] == 4
    assert "DEMO — SYNTHETIC FIXTURE" in (out / "comparisons.md").read_text()
    header = (out / "trades_summary.csv").read_text().splitlines()[0]
    assert "price" not in header and "open" not in header
    man = json.loads((out / "manifest.json").read_text())
    assert man["versions"]["protocol_sha256"] == protocol_v2_sha256() and man["status_at_freeze"] == "FROZEN_BEFORE_METRICS"
