"""Entry zone, stop/invalidation and target levels. ONE implementation used by live signals AND backtests.

Levels come from structure and volatility (ATR), never from a fixed +/-1%:
  entry_max  = close + min(0.5 ATR, 2% of close)   (do not chase beyond half a daily range)
  entry_min  = close - 0.5 ATR
  stop       = strategy invalidation hint, clamped to [mid - 3 ATR, mid - 1 ATR]
  target     = mid + k x (mid - stop), k = 2.0 by default (or a calibrated multiple), capped below overhead
               supply (prior 120-session high) when that resistance sits between entry and target.
Reward/risk is reported net of modeled round-trip costs from the zone midpoint.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

LEVELS_VERSION = "levels-1"


@dataclass(frozen=True)
class LevelConfig:
    entry_above_atr: float = 0.5
    entry_above_cap_pct: float = 0.02
    entry_below_atr: float = 0.5
    stop_min_atr: float = 1.0
    stop_max_atr: float = 3.0
    target_r_multiple: float = 2.0
    resistance_buffer_atr: float = 0.25
    min_net_rr: float = 1.5
    cost_bps_per_side: float = 10.0
    entry_valid_sessions: int = 2
    horizon_max_sessions: int = 20


@dataclass(frozen=True)
class Levels:
    entry_min: float
    entry_max: float
    stop: float
    target: float
    target_secondary: float | None
    reward_to_risk_net: float
    basis: tuple[str, ...]

    @property
    def mid(self) -> float:
        return (self.entry_min + self.entry_max) / 2


@dataclass(frozen=True)
class LevelRejection:
    code: str
    detail: str


def tick_round(price: float, down: bool = False) -> float:
    """Round to a valid US equity tick ($0.01 at/above $1)."""
    q = Decimal("0.01")
    d = Decimal(str(price))
    if down:
        return float(d.quantize(q, rounding=ROUND_FLOOR))
    return float(d.quantize(q, rounding=ROUND_HALF_UP))


def compute_levels(
    close: float,
    atr: float,
    invalidation_hint: float | None,
    resistance: float | None,
    cfg: LevelConfig = LevelConfig(),
    r_multiple: float | None = None,
    stop_distance_atr: float | None = None,
) -> Levels | LevelRejection:
    """`stop_distance_atr` (research controls only) replaces the hint by mid - k ATR, so a matched control reproduces a
    signal's stop geometry in ATR units; the [stop_min_atr, stop_max_atr] clamp still applies."""
    if not (close and atr) or any(math.isnan(x) for x in (close, atr)) or close <= 0 or atr <= 0:
        return LevelRejection("LEVELS_INPUT_MISSING", "close/ATR unavailable")
    above = min(cfg.entry_above_atr * atr, cfg.entry_above_cap_pct * close)
    entry_max = tick_round(close + above)
    entry_min = tick_round(close - cfg.entry_below_atr * atr, down=True)
    mid = (entry_min + entry_max) / 2
    hint = invalidation_hint if invalidation_hint is not None and not math.isnan(invalidation_hint) else mid - 1.5 * atr
    if stop_distance_atr is not None:
        hint = mid - stop_distance_atr * atr
    stop = min(max(hint, mid - cfg.stop_max_atr * atr), mid - cfg.stop_min_atr * atr)
    stop = tick_round(stop, down=True)
    if stop <= 0 or stop >= entry_min:
        return LevelRejection("STOP_INVALID", "stop not below entry zone")
    k = r_multiple or cfg.target_r_multiple
    risk = mid - stop
    target = mid + k * risk
    basis = [
        f"entry zone: close {close:.2f} -{cfg.entry_below_atr:g} ATR / +min({cfg.entry_above_atr:g} ATR, "
        f"{cfg.entry_above_cap_pct:.0%})",
        f"stop: structure/ATR clamp [{cfg.stop_min_atr:g},{cfg.stop_max_atr:g}] ATR",
        f"target: {k:g}R from zone midpoint",
    ]
    secondary = None
    if resistance is not None and not math.isnan(resistance) and entry_max < resistance < target:
        capped = resistance - cfg.resistance_buffer_atr * atr
        secondary = tick_round(target)
        target = capped
        basis.append(f"target capped below overhead supply {resistance:.2f}")
    target = tick_round(target)
    cost = 2 * cfg.cost_bps_per_side / 1e4 * mid
    rr = (target - mid - cost) / (mid - stop + cost)
    if target <= entry_max:
        return LevelRejection("TARGET_NOT_ABOVE_ENTRY", "no room above the entry zone")
    if rr < cfg.min_net_rr:
        return LevelRejection("REWARD_RISK_TOO_LOW", f"net R/R {rr:.2f} < {cfg.min_net_rr}")
    return Levels(entry_min, entry_max, stop, target, secondary, round(rr, 2), tuple(basis))


def dec_str(x: float, places: int = 2) -> str:
    return str(Decimal(str(x)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))
