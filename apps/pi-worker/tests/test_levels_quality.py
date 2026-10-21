"""Characterization tests for the price-level builder (levels-1) and the Quality ranking (quality-1).

They pin today's exact behaviour so that any refinement of entry/stop/target or of the evidence weights is a deliberate,
reviewed change: update the expected numbers here together with LEVELS_VERSION / QUALITY_VERSION, never silently.
Expected values are derived by hand from the formulas in the module docstrings.
"""

import math

import pytest

from apex_worker.signals.levels import LEVELS_VERSION, LevelConfig, LevelRejection, Levels, compute_levels, tick_round
from apex_worker.signals.quality import GROUP_WEIGHTS, MIN_COVERAGE, QUALITY_VERSION, SETUP_WEIGHT, quality


def test_versions_are_pinned():
    # bump these (and the expectations below) when the methodology changes
    assert LEVELS_VERSION == "levels-1"
    assert QUALITY_VERSION == "quality-1"
    assert LevelConfig() == LevelConfig(0.5, 0.02, 0.5, 1.0, 3.0, 2.0, 0.25, 1.5, 10.0, 2, 20)
    assert GROUP_WEIGHTS == {"price": 1.0, "sector": 0.8, "volume": 0.8, "volatility": 0.6, "regime": 1.0, "catalyst": 0.6}
    assert (SETUP_WEIGHT, MIN_COVERAGE) == (0.4, 0.6)


# ---- levels ----------------------------------------------------------------------------------------------------------
# close 50, ATR 1.5: entry 49.25..50.75 (mid 50.00), stop clamp [45.50, 48.50], cost 0.10 round trip at the mid


def _lv(**kw) -> Levels:
    out = compute_levels(**{"close": 50.0, "atr": 1.5, "invalidation_hint": 47.0, "resistance": None, **kw})
    assert isinstance(out, Levels), out
    return out


def test_structural_stop_inside_the_clamp_is_kept():
    lv = _lv()
    assert (lv.entry_min, lv.entry_max, lv.mid) == (49.25, 50.75, 50.0)
    assert (lv.stop, lv.target, lv.target_secondary) == (47.0, 56.0, None)  # 2R from the zone midpoint
    assert lv.reward_to_risk_net == 1.9  # (56 - 50 - 0.10) / (50 - 47 + 0.10)


@pytest.mark.parametrize(
    ("hint", "stop", "target", "rr"),
    [
        (49.9, 48.5, 53.0, 1.81),  # too tight -> pushed out to mid - 1 ATR
        (40.0, 45.5, 59.0, 1.93),  # too wide -> pulled in to mid - 3 ATR
        (None, 47.75, 54.5, 1.87),  # no structural level -> mid - 1.5 ATR
        (float("nan"), 47.75, 54.5, 1.87),
    ],
)
def test_stop_is_clamped_between_one_and_three_atr(hint, stop, target, rr):
    lv = _lv(invalidation_hint=hint)
    assert (lv.stop, lv.target, lv.reward_to_risk_net) == (stop, target, rr)


def test_entry_above_close_is_capped_at_two_percent():
    lv = compute_levels(close=10.0, atr=1.0, invalidation_hint=None, resistance=None)
    assert isinstance(lv, Levels)
    assert (lv.entry_min, lv.entry_max) == (9.5, 10.2)  # +min(0.5 ATR, 2%) = +0.20, -0.5 ATR
    assert lv.stop == 8.35 and lv.target == 12.85
    assert lv.reward_to_risk_net == 1.96


def test_overhead_supply_caps_the_target_and_keeps_the_uncapped_one_as_secondary():
    lv = _lv(resistance=55.5)  # between entry and the 2R target
    assert lv.target == 55.13  # 55.50 - 0.25 ATR, rounded half up
    assert lv.target_secondary == 56.0
    assert lv.reward_to_risk_net == 1.62
    assert any("overhead supply" in b for b in lv.basis)


def test_resistance_above_the_target_or_below_the_zone_is_ignored():
    assert _lv(resistance=60.0).target == 56.0
    assert _lv(resistance=50.5).target == 56.0  # inside the zone: not "overhead"


@pytest.mark.parametrize("resistance", [52.0, 55.0])
def test_a_cap_that_leaves_too_little_reward_rejects_the_setup(resistance):
    out = compute_levels(close=50.0, atr=1.5, invalidation_hint=47.0, resistance=resistance)
    assert isinstance(out, LevelRejection) and out.code == "REWARD_RISK_TOO_LOW"


def test_calibrated_r_multiple_overrides_the_default():
    lv = _lv(r_multiple=3.0)
    assert (lv.target, lv.reward_to_risk_net) == (59.0, 2.87)


def test_costs_are_charged_on_both_sides_of_reward_to_risk():
    free = compute_levels(50.0, 1.5, 47.0, None, LevelConfig(cost_bps_per_side=0.0))
    dear = compute_levels(50.0, 1.5, 47.0, None, LevelConfig(cost_bps_per_side=50.0))
    assert isinstance(free, Levels) and free.reward_to_risk_net == 2.0
    assert isinstance(dear, Levels) and dear.reward_to_risk_net == 1.57  # (6 - 0.5) / (3 + 0.5)


@pytest.mark.parametrize(("close", "atr"), [(0.0, 1.0), (50.0, 0.0), (float("nan"), 1.0), (50.0, float("nan")), (-5.0, 1.0)])
def test_missing_inputs_are_rejected(close, atr):
    out = compute_levels(close=close, atr=atr, invalidation_hint=None, resistance=None)
    assert isinstance(out, LevelRejection) and out.code == "LEVELS_INPUT_MISSING"


def test_tick_rounding_is_half_up_and_stops_round_down():
    assert tick_round(10.005) == 10.01
    assert tick_round(10.004) == 10.0
    assert tick_round(10.009, down=True) == 10.0


# ---- quality ---------------------------------------------------------------------------------------------------------
# A row where every transform sits at its neutral point (tanh(0) = 0): the composite is 0, so the score is driven only by
# the setup score, the coverage factor and the penalties.

NEUTRAL = {
    "trend_slope_50": 0.0, "dist_sma50_atr": 0.0, "xs_rank_ret_60": 0.5, "above_sma200": 0.5,
    "rs_bench_20": 0.0, "rs_sector_20": 0.0, "sector_rank_20": 0.5, "sector_rank_change_20": 0.0,
    "rel_volume_1": 1.0, "volume_z_1": 0.0, "up_down_vol_ratio_20": 1.0,
    "squeeze_prev": 0.5, "vol_expansion": 1.0,
    "regime_p_trending_up": 0.0, "regime_p_rebound": 0.0, "regime_p_risk_off": 0.0, "regime_p_volatility_shock": 0.0,
    "cat_count_5d": 0, "dollar_vol_med_20": 1e8, "dist_sma20_atr": 0.0, "failed_breakouts_60": 0,
}


def test_neutral_evidence_scores_from_the_setup_alone():
    q = quality(NEUTRAL, setup_score=0.5, earnings_status="CLEAR")
    assert q.groups == {"price": 0.0, "sector": 0.0, "volume": 0.0, "volatility": 0.0, "regime": 0.0, "catalyst": None}
    assert q.coverage == 1.0  # no catalyst data: catalyst leaves the denominator instead of counting against coverage
    assert q.score == 60.0  # 50 + 50 * (0.4 * 0.5)
    assert q.penalties == {}


@pytest.mark.parametrize(
    ("change", "earnings", "penalty", "score"),
    [
        ({}, "UNKNOWN", {"EARNINGS_DATE_UNKNOWN": 5.0}, 55.0),
        ({"dollar_vol_med_20": 2.5e7}, "CLEAR", {"THIN_LIQUIDITY": 3.0}, 57.0),  # 6 x (1 - 25M/50M)
        ({"dist_sma20_atr": 3.5}, "CLEAR", {"EXTENDED_FROM_SMA20": 4.0}, 56.0),  # 4 x (3.5 - 2.5)
        ({"dist_sma20_atr": 9.0}, "CLEAR", {"EXTENDED_FROM_SMA20": 10.0}, 50.0),  # capped at 10
        ({"failed_breakouts_60": 3}, "CLEAR", {"REPEATED_FAILED_BREAKOUTS": 6.0}, 54.0),  # 3 x (3 - 1)
    ],
)
def test_penalties_are_subtracted_in_points(change, earnings, penalty, score):
    q = quality({**NEUTRAL, **change}, setup_score=0.5, earnings_status=earnings)
    assert q.penalties == penalty and q.score == score


def test_a_negative_catalyst_counts_as_evidence_and_as_a_penalty():
    q = quality({**NEUTRAL, "cat_count_5d": 1, "cat_negative_recent_5d": 1, "cat_impact_max_5d": 50}, 0.5, "CLEAR")
    cat = 0.6 * math.tanh(-1.0)  # impact 50 is neutral
    assert q.groups["catalyst"] == pytest.approx(cat, abs=1e-4)
    composite = GROUP_WEIGHTS["catalyst"] * cat / sum(GROUP_WEIGHTS.values())
    expected = 50 + 50 * (0.4 * 0.5 + 0.6 * composite) - 8.0
    assert q.penalties == {"NEGATIVE_CATALYST": 8.0}
    assert q.score == pytest.approx(round(expected, 2), abs=0.01)


def test_missing_evidence_lowers_the_score_and_below_sixty_percent_gives_none():
    partial = {k: v for k, v in NEUTRAL.items() if k not in ("squeeze_prev", "vol_expansion")}  # volatility missing
    q = quality(partial, setup_score=0.5, earnings_status="CLEAR")
    assert q.coverage == round(3.6 / 4.2, 3)
    assert q.score == round(60.0 * (0.85 + 0.15 * (3.6 / 4.2)), 2)
    thin = {k: v for k, v in partial.items() if not k.startswith(("rs_", "sector_", "rel_volume", "volume_z", "up_down"))}
    q2 = quality(thin, setup_score=0.5, earnings_status="CLEAR")  # price + regime only: 2.0 / 4.2 < 0.6
    assert q2.score is None and q2.coverage == round(2.0 / 4.2, 3)


def test_no_setup_score_means_no_quality():
    assert quality(NEUTRAL, setup_score=None).score is None


def test_score_is_monotone_in_trend_strength_and_clamped():
    scores = [quality({**NEUTRAL, "trend_slope_50": s}, 0.5, "CLEAR").score for s in (-0.3, 0.0, 0.3)]
    assert scores[0] < scores[1] < scores[2]
    best = {**NEUTRAL, "trend_slope_50": 5, "dist_sma50_atr": 50, "xs_rank_ret_60": 5, "above_sma200": 1,
            "rs_bench_20": 5, "rs_sector_20": 5, "sector_rank_20": 5, "sector_rank_change_20": 5, "rel_volume_1": 50,
            "volume_z_1": 50, "up_down_vol_ratio_20": 50, "squeeze_prev": -5, "vol_expansion": 50,
            "regime_p_trending_up": 1.0}
    # price group tops out at 0.9 (above_sma200 contributes 0.6): 50 + 50 * (0.4 + 0.6 * 4.1 / 4.2)
    assert quality(best, 1.0, "CLEAR").score == 99.29
    assert quality(best, 1.0, "CLEAR").score <= 100.0
