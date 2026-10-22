"""Quant V2 replacement hypotheses S1-S5 (amendment 05). Research-only: every plugin is disabled by default.

None of these is a claimed edge. Each is a falsifiable specification evaluated under realeq-protocol-2. `trigger_frame`
reads only the row at t and explicitly shifted earlier rows of a frame built by features.quant_v2.add_v2_features on the
benchmark session index. Live scanning, if a rule is ever validated, must call the same functions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from apex_worker.signals.strategies.library import Strategy, StrategySpec, _col

QV2_STRATEGY_VERSION = "0.1.0"


@dataclass(frozen=True)
class StrategySpecV2(StrategySpec):
    economic_mechanism: str = ""
    null_hypothesis: str = ""
    candidate_universe_at: str = "U1 members at t that pass the live gates"
    entry_trigger: str = ""
    planned_order: str = ""
    risk_stop_hypothesis: str = ""
    candidate_exit_policies: tuple[str, ...] = ()
    max_hold_sessions: int = 20
    disconfirming_evidence: str = ""
    event_blackouts: str = "earnings schedule unknown point-in-time: every signal carries EARNINGS_UNKNOWN"
    provenance: str = "amendments/quant-v2-2026-10-22/05_REPLACEMENT_STRATEGIES_S1_S5.md"
    release_profile: str = "RESEARCH_ONLY"
    requires_events: bool = False
    extra: dict = field(default_factory=dict, compare=False, hash=False)


def _spec(sid: str, title: str, **kw) -> StrategySpecV2:
    base = {"allowed_regimes": ("trending_up", "choppy_neutral", "rebound", "risk_off", "volatility_shock"),
            "default_enabled": False}
    return StrategySpecV2(sid, QV2_STRATEGY_VERSION, title, kw.pop("hypothesis"), tuple(kw.pop("deps")),
                          tuple(kw.pop("rules")), "setup_score = priority within a session (ranking only, never a "
                          "probability)", tuple(kw.pop("fails")), **(base | kw))


def _frame(trig: pd.Series, score: pd.Series, hint: pd.Series, f: pd.DataFrame, **extra) -> pd.DataFrame:
    return pd.DataFrame({"trigger": trig.fillna(False).astype(bool), "setup_score": score, "pivot": _col(f, "close"),
                         "invalidation_hint": hint, **extra}, index=f.index)


class LeaderPullback(Strategy):
    """S1: a sector-relative leader pulls back 0.5-1.5 ATR over 2-5 sessions above a rising SMA50, then closes above
    the prior session's high. Stop under the pullback trough."""

    def trigger_frame(self, f: pd.DataFrame) -> pd.DataFrame:
        trig = (
            _col(f, "v2_peak_age_10").between(2, 5)
            & (_col(f, "v2_leader_at_peak") == 1)
            & _col(f, "v2_pullback_atr").between(0.5, 1.5)
            & (_col(f, "dist_sma50_atr") > 0)
            & (_col(f, "sma50_slope_20") > 0)
            & (_col(f, "close") > _col(f, "v2_prev_high"))
        )
        hint = _col(f, "v2_trough_low") - 0.25 * _col(f, "atr_14")
        return _frame(trig, _col(f, "v2_sector_pct_ret_60"), hint, f)

    @staticmethod
    def mechanism_pool(f: pd.DataFrame) -> pd.Series:
        """Leaders at t in any pullback state: the S1 null is 'pullback + confirmation adds nothing to leadership'."""
        return ((_col(f, "v2_leader") == 1) & (_col(f, "dist_sma50_atr") > 0) & (_col(f, "sma50_slope_20") > 0)).fillna(False)


class ResidualMomentum(Strategy):
    """S2: top quintile of the 20-session residual score (out-of-window betas on SPY and sector-minus-SPY), with raw
    momentum > 0, sector breadth >= 0.5 and no large gap today; signals only on every 5th session of the index."""

    def _filters(self, f: pd.DataFrame) -> pd.Series:
        pos = pd.Series(np.arange(len(f)) % self.params.get("rebalance", 5) == 0, index=f.index)
        return pos & (_col(f, "ret_20") > 0) & (_col(f, "v2_sector_breadth") >= 0.5) & (_col(f, "gap_1").abs() <= 0.06)

    def trigger_frame(self, f: pd.DataFrame) -> pd.DataFrame:
        trig = self._filters(f) & (_col(f, "v2_xs_pct_resid") >= 0.8)
        return _frame(trig, _col(f, "v2_resid_score_20"), _col(f, "close") - 2.0 * _col(f, "atr_14"), f)

    def mechanism_pool(self, f: pd.DataFrame) -> pd.Series:
        """Raw 20-session momentum ranked the same way (the S2 null: residualisation adds nothing to the raw rank)."""
        return (self._filters(f) & (_col(f, "v2_xs_pct_ret_20") >= 0.8)).fillna(False)


def _qualified_breakout(f: pd.DataFrame) -> pd.Series:
    return (
        (_col(f, "breakout_today") == 1)
        & (_col(f, "squeeze_pct_120").shift(1) <= 0.35)
        & (_col(f, "base_length").shift(1) >= 10)
        & (_col(f, "base_depth_pct").shift(1) <= 0.15)
        & (_col(f, "rel_volume_1") >= 1.3)
        & (_col(f, "rs_bench_20") > 0)
        & (_col(f, "dist_sma50_atr") > 0)
        & (_col(f, "sma50_slope_20") > 0)
        & ~(_col(f, "rs_sector_20") <= 0)  # missing sector ETF history: the condition is not applied
    ).fillna(False)


class BreakoutImmediate(Strategy):
    """S3 arm A: buy the qualified volatility-contraction breakout itself; stop 0.5 ATR under the pivot."""

    def trigger_frame(self, f: pd.DataFrame) -> pd.DataFrame:
        q = _qualified_breakout(f)
        opp = pd.Series(np.where(q, np.arange(len(f)), -1), index=f.index)
        hint = _col(f, "pivot_high_20") - 0.5 * _col(f, "atr_14")
        return _frame(q, _col(f, "rel_volume_1"), hint, f, opportunity=opp)


class BreakoutRetest(Strategy):
    """S3 arm C: within 1-3 sessions after a qualified breakout b, the FIRST session whose low revisits pivot_b +
    0.5 ATR_b and closes back above pivot_b. Missed retests are recorded against the opportunity (value 0)."""

    def trigger_frame(self, f: pd.DataFrame) -> pd.DataFrame:
        q = _qualified_breakout(f).to_numpy(bool)
        piv, atr = _col(f, "pivot_high_20").to_numpy(float), _col(f, "atr_14").to_numpy(float)
        low, close = _col(f, "v2_low").to_numpy(float), _col(f, "close").to_numpy(float)
        n = len(f)
        trig, opp, hint = np.zeros(n, bool), np.full(n, -1), np.full(n, np.nan)
        done: set[int] = set()
        for t in range(1, n):
            b = next((b for b in (t - 1, t - 2, t - 3) if b >= 0 and q[b]), None)  # most recent qualified breakout
            if b is None or b in done or not (math.isfinite(piv[b]) and math.isfinite(atr[b])):
                continue
            if low[t] <= piv[b] + 0.5 * atr[b] and close[t] > piv[b]:
                trig[t], opp[t], hint[t] = True, b, min(low[t], piv[b]) - 0.5 * atr[t]
                done.add(b)
        return _frame(pd.Series(trig, index=f.index), _col(f, "rel_volume_1"), pd.Series(hint, index=f.index), f,
                      opportunity=pd.Series(opp, index=f.index))


class EarningsContinuation(Strategy):
    """S4: continuation after a first-publication earnings/guidance reaction. Needs `ev_reaction_session` columns from
    `earnings_event_frame` (point-in-time events). Without them it never triggers: NOT_EVALUABLE, never a false 0."""

    def trigger_frame(self, f: pd.DataFrame) -> pd.DataFrame:
        if "ev_reaction_session" not in f:
            return _frame(pd.Series(False, index=f.index), pd.Series(np.nan, index=f.index),
                          pd.Series(np.nan, index=f.index), f)
        react = f["ev_reaction_session"].fillna(0).astype(bool)
        c = _col(f, "close")
        jump = c / c.shift(1) - 1  # reaction-session move from the prior close
        ok_e = react & jump.between(0.02, 0.12) & (_col(f, "rel_volume_1") >= 2.0) & (_col(f, "clv_1") >= 0.5)
        # signal at t = e + 2: both following closes keep >= 75% of the reaction move
        base, move = c.shift(3), jump.shift(2)
        level = base * (1 + 0.75 * move)
        trig = ok_e.shift(2, fill_value=False) & (c.shift(1) >= level) & (c >= level)
        return _frame(trig, move, base * (1 + 0.5 * move), f)


def earnings_event_frame(events: list[dict], index: pd.DatetimeIndex, close_utc: dict) -> pd.Series:
    """Session of the FIRST tradable reaction to each point-in-time event, from `available_at` (= max(first_public_at,
    provider_observed_at), amendment 03 section 4): before the open -> that session; during the session -> that
    session; at or after the close -> the next session. Events without available_at are dropped, never guessed."""
    flag = pd.Series(False, index=index)
    closes = np.array([pd.Timestamp(close_utc[d]).value for d in index])
    for ev in events:
        at = ev.get("available_at")
        if not at:
            continue
        ts = pd.Timestamp(at)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        k = int(np.searchsorted(closes, ts.value, side="right"))  # first session whose close is after availability
        if k < len(index):
            flag.iloc[k] = True
    return flag


S1 = LeaderPullback(_spec(
    "s1_leader_pullback", "Leadership continuation after a controlled pullback",
    hypothesis="buying sector-relative leaders after a shallow, confirmed pullback beats buying comparable leaders",
    deps=["v2_sector_pct_ret_20", "v2_sector_pct_ret_60", "rs_bench_60", "v2_peak_age_10", "v2_pullback_atr",
          "dist_sma50_atr", "sma50_slope_20", "v2_prev_high", "v2_trough_low", "atr_14"],
    rules=["leader on the 10-session peak: sector percentile of ret_20 and ret_60 >= 0.75, rs_bench_60 > 0, ret_20 <= ln 1.25",
           "peak 2-5 sessions ago, pullback 0.5-1.5 ATR, close > SMA50, SMA50 rising", "close > prior session high"],
    fails=["strong leaders never pull back (missed winners)", "factor momentum, not selection skill"],
    economic_mechanism="avoiding chase entries on leaders while keeping their trend persistence",
    null_hypothesis="paired per-signal net vs same-session matched control <= 0",
    entry_trigger="close of t after the confirmation bar", planned_order="E0 zone limit or E1 0.5 ATR below the close",
    risk_stop_hypothesis="pullback trough - 0.25 ATR, clamped to 1-3 ATR", candidate_exit_policies=("X0", "X1"),
    disconfirming_evidence="pullback+confirmation does not beat leaders-only under the same execution"))
S2 = ResidualMomentum(_spec(
    "s2_residual_momentum", "Market/sector residual momentum",
    hypothesis="stock-level residual strength after removing SPY and sector exposure persists over 10-20 sessions",
    deps=["v2_resid_score_20", "v2_xs_pct_resid", "ret_20", "v2_sector_breadth", "gap_1", "atr_14"],
    rules=["every 5th session", "residual score percentile >= 0.8 among eligible", "ret_20 > 0, sector breadth >= 0.5, |gap| <= 6%"],
    fails=["inherits sector or beta bets", "beta instability in short windows"],
    economic_mechanism="residual momentum is less exposed to factor crashes than raw momentum",
    null_hypothesis="paired per-signal net vs same-session matched control <= 0",
    entry_trigger="close of a rebalance session", planned_order="E0 zone limit", risk_stop_hypothesis="close - 2 ATR",
    candidate_exit_policies=("X1", "X0"),
    disconfirming_evidence="does not beat raw-momentum names chosen by the same filters"), {"rebalance": 5})
S3A = BreakoutImmediate(_spec(
    "s3a_breakout_immediate", "Volatility-contraction breakout, immediate entry (S3 arm A)",
    hypothesis="a qualified contraction breakout continues", deps=["breakout_today", "squeeze_pct_120", "base_length",
    "base_depth_pct", "rel_volume_1", "rs_bench_20", "rs_sector_20", "dist_sma50_atr", "sma50_slope_20", "pivot_high_20"],
    rules=["H1 breakout conditions plus a rising SMA50 and sector RS > 0 when available"],
    fails=["whipsaw immediately after the breakout"], economic_mechanism="supply absorbed in a tight base",
    null_hypothesis="paired per-signal net vs same-session matched control <= 0", entry_trigger="breakout close",
    planned_order="E0 zone limit", risk_stop_hypothesis="pivot - 0.5 ATR", candidate_exit_policies=("X0", "X2"),
    disconfirming_evidence="breakouts do not beat matched controls"))
S3C = BreakoutRetest(_spec(
    "s3c_breakout_retest", "Volatility-contraction breakout, retest entry (S3 arm C)",
    hypothesis="waiting for a retest of the pivot within 3 sessions improves net outcomes per opportunity",
    deps=["S3 arm A conditions", "v2_low", "pivot_high_20", "atr_14"],
    rules=["first session in b+1..b+3 with low <= pivot + 0.5 ATR_b and close > pivot"],
    fails=["runaway breakouts never retest (missed winners)"], economic_mechanism="acceptance above the pivot",
    null_hypothesis="per opportunity, retest arm minus immediate arm <= 0", entry_trigger="retest close",
    planned_order="E0 zone limit", risk_stop_hypothesis="min(retest low, pivot) - 0.5 ATR",
    candidate_exit_policies=("X0", "X2"), disconfirming_evidence="waiting loses more in missed moves than it saves"))
S4 = EarningsContinuation(_spec(
    "s4_earnings_continuation", "First-publication earnings/guidance continuation",
    hypothesis="a held earnings reaction continues for 5-15 sessions",
    deps=["ev_reaction_session (point-in-time events)", "close", "rel_volume_1", "clv_1"],
    rules=["reaction 2-12%, relative volume >= 2, close in the upper half", "next two sessions keep >= 75% of the move"],
    fails=["no point-in-time source: NOT_EVALUABLE"], economic_mechanism="post-announcement drift",
    null_hypothesis="paired net vs matched non-event gaps <= 0", entry_trigger="second completed session after reaction",
    planned_order="E0 zone limit", risk_stop_hypothesis="midpoint of the reaction", candidate_exit_policies=("X1",),
    requires_events=True, requires_catalysts=True,
    disconfirming_evidence="drift absent after timing and matching to non-event gaps"))

QV2_STRATEGIES: dict[str, Strategy] = {s.strategy_id: s for s in (S1, S2, S3A, S3C, S4)}


# ---- S5: sector-ETF trend allocation (portfolio rule, not a per-signal plugin) ---------------------------------------
@dataclass(frozen=True)
class S5Config:
    rebalance: int = 10
    top_n: int = 3
    lookback: int = 60
    abstain_regimes: tuple[str, ...] = ("risk_off", "volatility_shock")


S5_SPEC = _spec(
    "s5_sector_trend", "Sector-ETF trend allocation with cash abstention",
    hypothesis="holding the strongest risk-adjusted sector ETFs in an uptrend beats exposure-matched SPY/cash",
    deps=["ret_60", "rv_20", "dist_sma50_atr", "regime_label"],
    rules=["every R sessions: top 3 ETFs by ret_60 / (rv_20 sqrt(60/252)) among those above SMA50", "cash in risk-off"],
    fails=["wins only through lower beta in a crash"], economic_mechanism="sector trend persistence with low turnover",
    null_hypothesis="daily active return vs exposure-matched SPY/cash <= 0", entry_trigger="rebalance close",
    planned_order="market at the next open", risk_stop_hypothesis="none (rebalance exits)",
    candidate_exit_policies=("rebalance",), disconfirming_evidence="no improvement over SPY/cash or equal-weight sectors")


def s5_weights(etf: dict[str, pd.DataFrame], regime_label: pd.Series, index: pd.DatetimeIndex, cfg: S5Config,
               equal_weight: bool = False) -> pd.DataFrame:
    """Target weights decided at the CLOSE of each rebalance session (rows), applied from the next session's open."""
    cols = sorted(etf)
    r60 = np.column_stack([etf[s]["ret_60"].reindex(index).to_numpy(float) for s in cols])
    rv = np.column_stack([etf[s]["rv_20"].reindex(index).to_numpy(float) for s in cols])
    up = np.column_stack([(etf[s]["dist_sma50_atr"].reindex(index) > 0).to_numpy(bool) for s in cols])
    reg = regime_label.reindex(index).astype(str).to_numpy()
    w = np.full((len(index), len(cols)), np.nan)
    for i in np.flatnonzero(np.arange(len(index)) % cfg.rebalance == 0):
        live = np.isfinite(r60[i]) & np.isfinite(rv[i]) & (rv[i] > 0)
        row = np.zeros(len(cols))
        if equal_weight:
            if live.any():
                row[live] = 1.0 / live.sum()
        elif reg[i] not in cfg.abstain_regimes:
            ok = live & up[i]
            score = np.where(ok, r60[i] / (np.where(rv[i] > 0, rv[i], np.nan) * math.sqrt(cfg.lookback / 252)), -np.inf)
            order = sorted((j for j in range(len(cols)) if ok[j]), key=lambda j: (-score[j], cols[j]))
            for j in order[: cfg.top_n]:
                row[j] = 1.0 / cfg.top_n  # fewer qualifiers -> the rest stays in cash
        w[i] = row
    return pd.DataFrame(w, index=index, columns=cols)


def allocation_returns(weights: pd.DataFrame, opens: dict[str, pd.Series], closes: dict[str, pd.Series],
                       cost_bps: float) -> tuple[pd.Series, pd.Series]:
    """(daily net return, end-of-day exposure). Rebalance at the open after a decision row; costs on traded weight.

    Day d return = sum_i w_prev,i (open_d / close_{d-1} - 1) (overnight on the old book) then re-weight at the open
    (cost x |traded weight|) and sum_i w_new,i (close_d / open_d - 1). Weights drift with prices between rebalances."""
    cols = list(weights.columns)
    idx = weights.index
    c = pd.DataFrame({s: closes[s].reindex(idx) for s in cols}).astype(float).ffill()
    o = pd.DataFrame({s: opens[s].reindex(idx) for s in cols}).astype(float).fillna(c.shift(1)).to_numpy(float)
    c = c.to_numpy(float)
    w = weights.to_numpy(float)
    has = ~np.all(np.isnan(w), axis=1)
    cst = cost_bps / 1e4
    cur = np.zeros(len(cols))
    rets, expo = np.zeros(len(idx)), np.zeros(len(idx))
    pending = np.nan_to_num(w[0]) if has[0] else None
    for i in range(1, len(idx)):
        gap = np.nan_to_num(o[i] / c[i - 1] - 1)
        r_night = float(cur @ gap)
        val = cur * (1 + gap)
        nav = 1 + r_night
        cur = val / nav if nav > 0 else val
        traded = 0.0
        if pending is not None:
            traded = float(np.abs(pending - cur).sum())
            cur, pending = pending.copy(), None
        day = np.nan_to_num(c[i] / o[i] - 1)
        r_day = float(cur @ day)
        rets[i] = (1 + r_night) * (1 - cst * traded) * (1 + r_day) - 1
        val = cur * (1 + day)
        cur = val / (1 + r_day) if (1 + r_day) > 0 else val
        expo[i] = float(cur.sum())
        if has[i]:
            pending = np.nan_to_num(w[i])
    return pd.Series(rets, index=idx), pd.Series(expo, index=idx)
