"""Conservative daily-bar execution and barrier-exit simulator.

Entry (limit order over the entry zone, valid `entry_valid_sessions` sessions starting the session AFTER
the decision):
  * open inside [entry_min, entry_max]      -> fill at open + slippage
  * open above entry_max and low <= limit   -> fill at entry_max + slippage (limit touched intraday)
  * open below entry_min                    -> no fill that session (do not buy weakness / below plan)
  No "perfect low" fills, no fills at the decision bar's own close.

Exit after fill (target limit, stop, time stop at `horizon` sessions):
  * fill session: only the STOP can trigger (the high may have printed before the fill)
  * later sessions: gap through stop -> exit at the open (worse than stop); gap above target -> exit at open;
    if one bar touches both target and stop -> STOP FIRST (ambiguous, counted); time exit at the close.
  * delisting / end of data -> exit at last close, flagged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class ExecConfig:
    cost_bps_per_side: float = 10.0
    entry_valid_sessions: int = 2
    horizon_sessions: int = 20


@dataclass
class TradeOutcome:
    filled: bool
    outcome: str  # TARGET | STOP | TIME | NO_FILL | DATA_END
    entry_index: int | None = None
    exit_index: int | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    return_gross: float | None = None
    return_net: float | None = None
    r_multiple: float | None = None
    mfe: float | None = None
    mae: float | None = None
    sessions_held: int | None = None
    ambiguous_same_bar: bool = False
    gap_through_stop: bool = False
    flags: list[str] = field(default_factory=list)


def simulate(
    o: np.ndarray,
    h: np.ndarray,
    low: np.ndarray,
    c: np.ndarray,
    decision_index: int,
    entry_min: float,
    entry_max: float,
    stop: float,
    target: float,
    cfg: ExecConfig = ExecConfig(),
) -> TradeOutcome:
    n = len(c)
    slip = cfg.cost_bps_per_side / 1e4
    fill_i = None
    fill_px = None
    for d in range(decision_index + 1, min(n, decision_index + 1 + cfg.entry_valid_sessions)):
        if any(math.isnan(x) for x in (o[d], h[d], low[d], c[d])):
            continue
        if entry_min <= o[d] <= entry_max:
            fill_i, fill_px = d, o[d] * (1 + slip)
            break
        if o[d] > entry_max and low[d] <= entry_max:
            fill_i, fill_px = d, entry_max * (1 + slip)
            break
    if fill_i is None:
        return TradeOutcome(False, "NO_FILL" if decision_index + cfg.entry_valid_sessions < n else "DATA_END")

    risk = fill_px - stop
    mfe = 0.0
    mae = 0.0
    last = min(n - 1, fill_i + cfg.horizon_sessions)
    outcome, exit_i, exit_px = None, None, None
    ambiguous = gap_stop = False
    for d in range(fill_i, last + 1):
        if any(math.isnan(x) for x in (o[d], h[d], low[d], c[d])):
            continue
        mfe = max(mfe, h[d] / fill_px - 1)
        mae = min(mae, low[d] / fill_px - 1)
        if d == fill_i:
            if low[d] <= stop:
                outcome, exit_i, exit_px = "STOP", d, stop * (1 - slip)
                break
            continue
        if o[d] <= stop:
            outcome, exit_i, exit_px, gap_stop = "STOP", d, o[d] * (1 - slip), True
            break
        if o[d] >= target:
            outcome, exit_i, exit_px = "TARGET", d, o[d] * (1 - slip)
            break
        hit_stop = low[d] <= stop
        hit_target = h[d] >= target
        if hit_stop and hit_target:
            outcome, exit_i, exit_px, ambiguous = "STOP", d, stop * (1 - slip), True
            break
        if hit_stop:
            outcome, exit_i, exit_px = "STOP", d, stop * (1 - slip)
            break
        if hit_target:
            outcome, exit_i, exit_px = "TARGET", d, target * (1 - slip)
            break
    flags: list[str] = []
    if outcome is None:
        if fill_i + cfg.horizon_sessions <= n - 1:
            outcome, exit_i = "TIME", fill_i + cfg.horizon_sessions
        else:
            outcome, exit_i = "DATA_END", n - 1
            flags.append("ENDED_BEFORE_HORIZON")
        exit_px = c[exit_i] * (1 - slip)
    gross = (exit_px / (1 - slip)) / (fill_px / (1 + slip)) - 1  # raw prices, before modeled costs
    net = exit_px / fill_px - 1
    return TradeOutcome(
        True,
        outcome,
        fill_i,
        exit_i,
        round(fill_px, 6),
        round(exit_px, 6),
        round(gross, 6),
        round(net, 6),
        round((exit_px - fill_px) / risk, 4) if risk > 0 else None,
        round(mfe, 6),
        round(mae, 6),
        exit_i - fill_i,
        ambiguous,
        gap_stop,
        flags,
    )
