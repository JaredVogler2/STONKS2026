"""Quality 0-100: an internal RANKING built from independent evidence groups. Never a probability.

Correlated measurements are collapsed inside one group (e.g. 20/60-session momentum, SMA distance and
trend slope form a single `price` group) so they cannot masquerade as independent confirmations.
Missing inputs reduce coverage (and therefore the score) instead of being treated as neutral zeros.
Weights are a documented starting hypothesis (version quality-1) evaluated in Phase 06.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

QUALITY_VERSION = "quality-1"

GROUP_WEIGHTS = {
    "price": 1.0,
    "sector": 0.8,
    "volume": 0.8,
    "volatility": 0.6,
    "regime": 1.0,
    "catalyst": 0.6,
}
SETUP_WEIGHT = 0.4
MIN_COVERAGE = 0.6


def _v(row: Mapping, key: str) -> float | None:
    x = row.get(key)
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _mean(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _t(x: float | None, scale: float, shift: float = 0.0) -> float | None:
    return None if x is None else math.tanh((x - shift) / scale)


@dataclass(frozen=True)
class QualityResult:
    score: float | None
    groups: dict[str, float | None]
    coverage: float
    penalties: dict[str, float]


def group_scores(row: Mapping) -> dict[str, float | None]:
    price = _mean(
        [
            _t(_v(row, "trend_slope_50"), 0.15),
            _t(_v(row, "dist_sma50_atr"), 2.0),
            _t(_v(row, "xs_rank_ret_60"), 0.25, 0.5),
            None if _v(row, "above_sma200") is None else (2 * _v(row, "above_sma200") - 1) * 0.6,  # type: ignore[operator]
        ]
    )
    sector = _mean(
        [
            _t(_v(row, "rs_bench_20"), 0.06),
            _t(_v(row, "rs_sector_20"), 0.05),
            _t(_v(row, "sector_rank_20"), 0.3, 0.5),
            _t(_v(row, "sector_rank_change_20"), 0.3),
        ]
    )
    volume = _mean(
        [
            _t(_v(row, "rel_volume_1"), 0.8, 1.0),
            _t(_v(row, "volume_z_1"), 2.0),
            None if not _v(row, "up_down_vol_ratio_20") else math.tanh(math.log(_v(row, "up_down_vol_ratio_20")) / 0.4),  # type: ignore[arg-type]
        ]
    )
    sq = _v(row, "squeeze_prev")
    vol = _mean(
        [
            None if sq is None else math.tanh((0.5 - sq) / 0.25),
            _t(_v(row, "vol_expansion"), 0.5, 1.0),
        ]
    )
    p_up, p_reb = _v(row, "regime_p_trending_up"), _v(row, "regime_p_rebound")
    p_off, p_shock = _v(row, "regime_p_risk_off"), _v(row, "regime_p_volatility_shock")
    regime = None
    if None not in (p_up, p_reb, p_off, p_shock):
        regime = max(-1.0, min(1.0, p_up + 0.5 * p_reb - p_off - 1.5 * p_shock))  # type: ignore[operator]
    cat = None
    if _v(row, "cat_count_5d"):
        pos = _v(row, "cat_positive_recent_5d") or 0
        neg = _v(row, "cat_negative_recent_5d") or 0
        impact = _v(row, "cat_impact_max_5d") or 0
        cat = max(-1.0, min(1.0, 0.6 * math.tanh(pos - neg) + 0.4 * math.tanh((impact - 50) / 25)))
    return {"price": price, "sector": sector, "volume": volume, "volatility": vol, "regime": regime, "catalyst": cat}


def penalties(row: Mapping, earnings_status: str) -> dict[str, float]:
    out: dict[str, float] = {}
    dv = _v(row, "dollar_vol_med_20")
    if dv is not None and dv < 5e7:
        out["THIN_LIQUIDITY"] = 6.0 * (1 - dv / 5e7)
    ext = _v(row, "dist_sma20_atr")
    if ext is not None and ext > 2.5:
        out["EXTENDED_FROM_SMA20"] = min(10.0, 4.0 * (ext - 2.5))
    fb = _v(row, "failed_breakouts_60")
    if fb is not None and fb >= 2:
        out["REPEATED_FAILED_BREAKOUTS"] = 3.0 * (fb - 1)
    if earnings_status == "UNKNOWN":
        out["EARNINGS_DATE_UNKNOWN"] = 5.0
    if (_v(row, "cat_negative_recent_5d") or 0) > 0:
        out["NEGATIVE_CATALYST"] = 8.0
    return out


def quality(row: Mapping, setup_score: float | None, earnings_status: str = "UNKNOWN") -> QualityResult:
    groups = group_scores(row)
    avail = {g: s for g, s in groups.items() if s is not None}
    total_w = sum(GROUP_WEIGHTS.values()) - (GROUP_WEIGHTS["catalyst"] if groups["catalyst"] is None else 0.0)
    w = sum(GROUP_WEIGHTS[g] for g in avail)
    coverage = w / total_w if total_w else 0.0
    pens = penalties(row, earnings_status)
    if not avail or setup_score is None or coverage < MIN_COVERAGE:
        return QualityResult(None, groups, round(coverage, 3), pens)
    composite = sum(GROUP_WEIGHTS[g] * s for g, s in avail.items()) / w
    blended = SETUP_WEIGHT * setup_score + (1 - SETUP_WEIGHT) * composite
    raw = 50.0 + 50.0 * blended
    raw *= 0.85 + 0.15 * coverage  # incomplete evidence is penalized, never assumed neutral
    raw -= sum(pens.values())
    return QualityResult(
        round(max(0.0, min(100.0, raw)), 2),
        {k: (None if v is None else round(v, 4)) for k, v in groups.items()},
        round(coverage, 3),
        {k: round(v, 2) for k, v in pens.items()},
    )
