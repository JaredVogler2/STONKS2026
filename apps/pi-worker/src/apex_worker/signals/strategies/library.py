"""Hypothesis plugins H1-H8 for 5-20 trading-session LONG swings.

Each plugin is a *hypothesis to test*, not a claimed edge. `trigger_frame` is vectorized and causal
(it only reads the feature row at t and explicitly shifted earlier rows), so the same code drives live
scanning (one session) and walk-forward research (all sessions). Every candidate must carry positive
and negative evidence; plugins also declare failure modes so explanations can cite them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    version: str
    title: str
    hypothesis: str
    feature_dependencies: tuple[str, ...]
    eligibility_rules: tuple[str, ...]
    scoring_contract: str
    failure_modes: tuple[str, ...]
    citations: tuple[str, ...] = ()
    allowed_regimes: tuple[str, ...] = ("trending_up", "choppy_neutral", "rebound")
    requires_catalysts: bool = False
    default_enabled: bool = True
    weak_prior: bool = False


@dataclass
class Strategy:
    spec: StrategySpec
    params: dict[str, float] = field(default_factory=dict)

    @property
    def strategy_id(self) -> str:
        return self.spec.strategy_id

    def trigger_frame(self, f: pd.DataFrame) -> pd.DataFrame:  # pragma: no cover - interface
        raise NotImplementedError


def _col(f: pd.DataFrame, name: str) -> pd.Series:
    if name in f:
        return f[name].astype(float)
    return pd.Series(np.nan, index=f.index)


def _tanh(x: pd.Series, scale: float) -> pd.Series:
    return np.tanh(x / scale)


def _regime_ok(f: pd.DataFrame, allowed: tuple[str, ...]) -> pd.Series:
    if "regime_label" not in f:
        return pd.Series(False, index=f.index)
    return f["regime_label"].isin(list(allowed))


class VolatilityContractionBreakout(Strategy):
    """H1: base with contracting volatility, then a breakout confirmed by participation and RS."""

    def trigger_frame(self, f: pd.DataFrame) -> pd.DataFrame:
        p = {"max_squeeze": 0.35, "min_base": 10, "max_depth": 0.15, "min_relvol": 1.3, **self.params}
        squeeze_prev = _col(f, "squeeze_pct_120").shift(1)
        base_prev = _col(f, "base_length").shift(1)
        depth_prev = _col(f, "base_depth_pct").shift(1)
        trig = (
            (_col(f, "breakout_today") == 1)
            & (squeeze_prev <= p["max_squeeze"])
            & (base_prev >= p["min_base"])
            & (depth_prev <= p["max_depth"])
            & (_col(f, "rel_volume_1") >= p["min_relvol"])
            & (_col(f, "rs_bench_20") > 0)
            & (_col(f, "dist_sma50_atr") > 0)
        )
        score = (
            0.35 * _tanh(0.5 - squeeze_prev, 0.25)
            + 0.25 * _tanh(_col(f, "rel_volume_1") - 1, 0.8)
            + 0.2 * _tanh(base_prev - 10, 10)
            + 0.2 * _tanh(_col(f, "clv_1"), 0.6)
        )
        return pd.DataFrame(
            {
                "trigger": trig.fillna(False),
                "setup_score": score.clip(-1, 1),
                "pivot": _col(f, "pivot_high_20"),
                "invalidation_hint": _col(f, "close") - 1.6 * _col(f, "atr_14"),
            }
        )


class RelativeStrengthPullback(Strategy):
    """H2: multi-timeframe relative-strength leader in a shallow pullback toward rising support."""

    def trigger_frame(self, f: pd.DataFrame) -> pd.DataFrame:
        rank60 = _col(f, "xs_rank_ret_60")
        pull = _col(f, "pct_from_high_60")
        trig = (
            (rank60 >= 0.75)
            & (_col(f, "above_sma200") == 1)
            & (pull <= -0.03)
            & (pull >= -0.11)
            & (_col(f, "dist_sma20_atr").between(-1.2, 0.3))
            & (_col(f, "sma50_slope_20") > 0)
            & (_col(f, "clv_1") > 0.25)
            & (_col(f, "ret_5") < 0)
        )
        score = (
            0.4 * _tanh(rank60 - 0.5, 0.25)
            + 0.3 * _tanh(_col(f, "sma50_slope_20"), 0.04)
            + 0.3 * _tanh(-(pull + 0.06).abs() + 0.03, 0.03)
        )
        return pd.DataFrame(
            {
                "trigger": trig.fillna(False),
                "setup_score": score.clip(-1, 1),
                "pivot": _col(f, "close").rolling(60, min_periods=60).max(),
                "invalidation_hint": _col(f, "close") - 1.4 * _col(f, "atr_14"),
            }
        )


class PostEventDrift(Strategy):
    """H3: drift after a POSITIVE, point-in-time-available event; entry the next session, never at the release print."""

    def trigger_frame(self, f: pd.DataFrame) -> pd.DataFrame:
        cat_pos = _col(f, "cat_positive_recent_2d")
        gap_prev = _col(f, "gap_1").shift(1)
        held = _col(f, "close") >= (_col(f, "close").shift(1) * 0.99)
        trig = (
            (cat_pos >= 1) & (gap_prev >= 0.03) & (gap_prev <= 0.12) & held & (_col(f, "rel_volume_1").shift(1) >= 2.0)
        )
        score = 0.5 * _tanh(gap_prev, 0.05) + 0.5 * _tanh(_col(f, "cat_impact_max_5d") - 50, 25)
        return pd.DataFrame(
            {
                "trigger": trig.fillna(False),
                "setup_score": score.clip(-1, 1),
                "pivot": _col(f, "close").shift(1),
                "invalidation_hint": _col(f, "close").shift(1) / np.exp(gap_prev) * 0.995,
            }
        )


class OversoldRecovery(Strategy):
    """H4: short-term oversold bounce ONLY inside a long-term uptrend; avoid collapsing-momentum traps."""

    def trigger_frame(self, f: pd.DataFrame) -> pd.DataFrame:
        trig = (
            (_col(f, "above_sma200") == 1)
            & (_col(f, "ret_5") <= -0.06)
            & (_col(f, "dist_sma20_atr") <= -1.5)
            & (_col(f, "clv_1") >= 0.3)
            & (_col(f, "ret_60") > -0.05)
            & (_col(f, "failed_breakouts_60") <= 2)
        )
        score = 0.5 * _tanh(-_col(f, "dist_sma20_atr") - 1.5, 1.0) + 0.5 * _tanh(_col(f, "clv_1"), 0.5)
        return pd.DataFrame(
            {
                "trigger": trig.fillna(False),
                "setup_score": score.clip(-1, 1),
                "pivot": _col(f, "close").rolling(20, min_periods=20).mean(),
                "invalidation_hint": _col(f, "close") - 1.2 * _col(f, "atr_14"),
            }
        )


class SectorRotationLeadership(Strategy):
    """H5: improving sector leadership + stock cross-sectional strength + supportive breadth."""

    def trigger_frame(self, f: pd.DataFrame) -> pd.DataFrame:
        trig = (
            (_col(f, "sector_rank_change_20") >= 0.3)
            & (_col(f, "sector_rank_20") >= 0.7)
            & (_col(f, "xs_rank_ret_20") >= 0.65)
            & (_col(f, "regime_breadth") >= 0.5)
            & (_col(f, "dist_sma20_atr").between(-0.5, 2.0))
        )
        score = (
            0.4 * _tanh(_col(f, "sector_rank_change_20"), 0.3)
            + 0.3 * _tanh(_col(f, "xs_rank_ret_20") - 0.5, 0.25)
            + 0.3 * _tanh(_col(f, "regime_breadth") - 0.5, 0.2)
        )
        return pd.DataFrame(
            {
                "trigger": trig.fillna(False),
                "setup_score": score.clip(-1, 1),
                "pivot": _col(f, "pivot_high_20"),
                "invalidation_hint": _col(f, "close") - 1.5 * _col(f, "atr_14"),
            }
        )


class GapAndHold(Strategy):
    """H6: gap up that holds intraday on heavy participation; entry next session inside a zone, not the open print."""

    def trigger_frame(self, f: pd.DataFrame) -> pd.DataFrame:
        trig = (
            (_col(f, "gap_1") >= 0.025)
            & (_col(f, "gap_1") <= 0.10)
            & (_col(f, "clv_1") >= 0.5)
            & (_col(f, "rel_volume_1") >= 1.8)
            & (_col(f, "dist_sma50_atr") > 0)
        )
        score = (
            0.4 * _tanh(_col(f, "clv_1"), 0.5)
            + 0.3 * _tanh(_col(f, "rel_volume_1") - 1.5, 1.0)
            + 0.3 * _tanh(_col(f, "rs_bench_20"), 0.05)
        )
        return pd.DataFrame(
            {
                "trigger": trig.fillna(False),
                "setup_score": score.clip(-1, 1),
                "pivot": _col(f, "close"),
                "invalidation_hint": _col(f, "close") / np.exp(_col(f, "gap_1")) * 0.995,
            }
        )


class CatalystCompression(Strategy):
    """H7: compression base + fresh positive catalyst. Must beat H1 alone AND catalyst alone out of sample."""

    def trigger_frame(self, f: pd.DataFrame) -> pd.DataFrame:
        squeeze_prev = _col(f, "squeeze_pct_120").shift(1)
        trig = (
            (squeeze_prev <= 0.4)
            & (_col(f, "base_length").shift(1) >= 8)
            & (_col(f, "cat_positive_recent_5d") >= 1)
            & (_col(f, "dist_pivot_atr") >= -0.5)
            & (_col(f, "rs_bench_20") > 0)
        )
        score = 0.5 * _tanh(0.5 - squeeze_prev, 0.25) + 0.5 * _tanh(_col(f, "cat_impact_max_5d") - 50, 25)
        return pd.DataFrame(
            {
                "trigger": trig.fillna(False),
                "setup_score": score.clip(-1, 1),
                "pivot": _col(f, "pivot_high_20"),
                "invalidation_hint": _col(f, "close") - 1.5 * _col(f, "atr_14"),
            }
        )


class TurnOfMonthSeasonality(Strategy):
    """H8: turn-of-month seasonality. Weak prior with multiple-testing penalty; disabled by default."""

    def trigger_frame(self, f: pd.DataFrame) -> pd.DataFrame:
        from apex_worker.market.calendar import default_calendar

        cal = default_calendar()
        # the exchange calendar is published in advance (not price data), so this is causal
        last_session_of_month = pd.Series(
            [cal.next_session(d.date()).month != d.month for d in pd.DatetimeIndex(f.index)], index=f.index
        )
        trig = last_session_of_month & (_col(f, "above_sma200") == 1) & (_col(f, "dist_sma20_atr").between(-1, 1.5))
        score = pd.Series(0.1, index=f.index)
        return pd.DataFrame(
            {
                "trigger": trig.fillna(False),
                "setup_score": score,
                "pivot": _col(f, "close"),
                "invalidation_hint": _col(f, "close") - 1.5 * _col(f, "atr_14"),
            }
        )


def _spec(sid, title, hyp, deps, rules, fails, **kw) -> StrategySpec:
    return StrategySpec(
        sid,
        "1.0.0",
        title,
        hyp,
        tuple(deps),
        tuple(rules),
        "setup_score in [-1,1] from monotone transforms of listed features; blended into Quality 0-100 "
        "(ranking only, never a probability)",
        tuple(fails),
        **kw,
    )


STRATEGIES: dict[str, Strategy] = {
    s.strategy_id: s
    for s in [
        VolatilityContractionBreakout(
            _spec(
                "h1_vcp_breakout",
                "Volatility contraction breakout",
                "After a tight, shallow base with compressed volatility, an expansion through the pivot on above-normal "
                "participation and positive relative strength continues over 5-20 sessions more often than unconditional entries.",
                [
                    "squeeze_pct_120",
                    "base_length",
                    "base_depth_pct",
                    "breakout_today",
                    "rel_volume_1",
                    "rs_bench_20",
                    "clv_1",
                ],
                ["price >= $10", "median $vol >= $30M", "regime not risk_off/volatility_shock", "close > SMA50"],
                [
                    "false breakout back into the base",
                    "breakout on low market breadth",
                    "earnings gap inside horizon",
                    "IEX-only volume understates or overstates participation",
                ],
                citations=(
                    "Minervini-style VCP heuristics are practitioner lore, NOT evidence; tested here out of sample",
                ),
            )
        ),
        RelativeStrengthPullback(
            _spec(
                "h2_rs_pullback",
                "Relative-strength pullback",
                "Cross-sectional 60-session leaders that pull back 3-11% toward a rising SMA50 with a bullish reversal bar "
                "resume their trend.",
                ["xs_rank_ret_60", "pct_from_high_60", "dist_sma20_atr", "sma50_slope_20", "clv_1", "ret_5"],
                ["above SMA200", "rising SMA50", "not extended"],
                ["momentum crash in regime flips", "sector-wide distribution", "pullback deepens into breakdown"],
                citations=(
                    "Jegadeesh & Titman (1993) cross-sectional momentum; short-horizon applicability unproven here",
                ),
            )
        ),
        PostEventDrift(
            _spec(
                "h3_post_event_drift",
                "Post-event drift",
                "A positive, point-in-time-available catalyst followed by a held gap continues to drift; entry only on the "
                "next session within a zone (no fill at the release print); compared against a placebo event set.",
                ["cat_positive_recent_2d", "cat_impact_max_5d", "gap_1", "rel_volume_1"],
                ["catalyst available_at <= decision time", "gap 3-12% (not exhausted)"],
                ["upside consumed by the gap", "event misclassified", "post-event fade"],
                requires_catalysts=True,
            )
        ),
        OversoldRecovery(
            _spec(
                "h4_oversold_recovery",
                "Oversold recovery in uptrend",
                "Short-term oversold names inside a long-term uptrend with a reversal bar mean-revert toward SMA20.",
                ["above_sma200", "ret_5", "dist_sma20_atr", "clv_1", "ret_60", "failed_breakouts_60"],
                ["regime trending_up or choppy_neutral only"],
                ["falling knife: trend change", "news-driven breakdown"],
                allowed_regimes=("trending_up", "choppy_neutral"),
            )
        ),
        SectorRotationLeadership(
            _spec(
                "h5_sector_rotation",
                "Sector rotation leadership",
                "Stocks ranking highly within a sector whose cross-sector rank has improved sharply outperform while breadth "
                "is supportive.",
                ["sector_rank_change_20", "sector_rank_20", "xs_rank_ret_20", "regime_breadth"],
                ["breadth above 50% of names over SMA50"],
                ["rotation reverses", "crowding into one theme"],
            )
        ),
        GapAndHold(
            _spec(
                "h6_gap_and_hold",
                "Gap and hold",
                "Gap-ups of 2.5-10% that close in the top half of the range on heavy volume continue; executable entry is "
                "the next session's zone, never the official open print.",
                ["gap_1", "clv_1", "rel_volume_1", "dist_sma50_atr", "rs_bench_20"],
                ["gap not exhausted (>10% vetoed)"],
                ["gap fill", "news reversal", "spread widening at open"],
            )
        ),
        CatalystCompression(
            _spec(
                "h7_catalyst_compression",
                "Catalyst + compression concurrence",
                "The intersection of a compression base and a fresh positive catalyst beats either component alone.",
                ["squeeze_pct_120", "base_length", "cat_positive_recent_5d", "cat_impact_max_5d", "dist_pivot_atr"],
                ["catalyst available_at <= decision time"],
                ["intersection is too rare for reliable statistics", "catalyst classification noise"],
                requires_catalysts=True,
            )
        ),
        TurnOfMonthSeasonality(
            _spec(
                "h8_turn_of_month",
                "Turn-of-month seasonality (weak prior)",
                "Entries on the last session of a month in uptrending names capture a turn-of-month effect; treated as a weak "
                "prior subject to multiple-testing penalty and strict holdout.",
                ["above_sma200", "dist_sma20_atr"],
                ["disabled by default"],
                ["data-mined calendar effect", "effect decayed after publication"],
                default_enabled=False,
                weak_prior=True,
                citations=("Ariel (1987); McConnell & Xu (2008) - documented effects may not persist",),
            )
        ),
    ]
}


def enabled_strategies(overrides: dict[str, bool] | None = None) -> list[Strategy]:
    overrides = overrides or {}
    return [s for sid, s in STRATEGIES.items() if overrides.get(sid, s.spec.default_enabled)]
