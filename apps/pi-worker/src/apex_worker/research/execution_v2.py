"""Quant V2 daily-bar execution with explicit path ambiguity (QV2-002/006/010; amendments 02 section B and 06).

`research.execution.simulate` is unchanged and remains the engine of realeq-protocol-1. This module is the engine for
protocol-2 and never guesses an intraday order of events that daily OHLC cannot determine. Where the bars leave it
open, a trade carries a PESSIMISTIC and an OPTIMISTIC leg and names the ambiguity; inference uses the pessimistic leg
and reports the optimistic one as a sensitivity.

Path model (the only assumption): within a session the price moves continuously from the open to the close and
touches the high and the low; gaps occur only between sessions. A resting limit fills at its price when first touched
("touch fill"; `trade_through_ticks=1` requires the price to trade one tick through a resting limit: the low below a
buy limit, the high above the target sell limit; an open beyond either is marketable). No partial fills, queue or
spread are modelled: costs are a proportional charge per side (`Costs`), applied once, in `TradeV2.net`.

Entry (buy limit, valid `valid_sessions` sessions from t+1; t is the decision session, after its close):
  * open < min_open                    -> no order that session (the plan's open guard: do not buy below the plan)
  * min_open <= open <= limit          -> fill at the open                                        (fill_kind OPEN)
  * open > limit and low <= limit      -> fill at the limit, reached from ABOVE                   (fill_kind LIMIT)
Exits: a protective stop (fixed, or trailing for X2), an optional target limit, and a time exit at the close of fill+H.
  * fill session, OPEN fill: the whole bar is after the fill, so the same-bar rules below apply.
  * fill session, LIMIT fill: the price came down to the limit, so the low (and a stop touch) is after the fill, but
    the high may have printed before it. A touched target is then UNRESOLVED: one branch sells at the target, the other
    holds; the legs take the worse and the better branch. A close at or above the target proves a post-fill touch.
  * later sessions: open <= stop -> exit at the (worse) open; open >= target -> exit at the open; one bar touching both
    -> UNRESOLVED (pessimistic STOP, optimistic TARGET); otherwise the touched barrier at its price.
  * a trailing stop for session d uses closes and ATR through d-1 only (known before d's open) and never loosens.
  * missing bars are skipped, never filled in; data ending before the horizon exits at the last close (DATA_END).
MFE/MAE are BOUNDS over the post-fill, pre-exit segment of the pessimistic leg; daily bars never observe that path.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from apex_worker.signals.levels import tick_round

EXECUTION_VERSION = "exec-v2.0"
TICK = 0.01
OPEN, LIMIT = "OPEN", "LIMIT"
AMBIGUITY = ("NONE", "SAME_BAR_STOP_TARGET", "FILL_DAY_TARGET_ORDER")


@dataclass(frozen=True)
class Costs:
    """Proportional modelled cost per side (commission, spread and impact together); never charged twice."""

    bps_per_side: float = 25.0

    def net(self, entry: float, exit_: float) -> float:
        c = self.bps_per_side / 1e4
        return exit_ * (1 - c) / (entry * (1 + c)) - 1


@dataclass(frozen=True)
class EntryPolicy:
    arm: str = "E0"  # E0: limit at entry_max, open guard entry_min | E1: limit below the decision close
    valid_sessions: int = 2
    e1_below_close_atr: float = 0.5
    trade_through_ticks: int = 0


@dataclass(frozen=True)
class ExitPolicy:
    arm: str = "X0"  # X0: stop + target + time | X1: stop + time | X2: trailing stop + time
    horizon_sessions: int = 20
    trail_atr: float = 3.0  # X2 only

    @property
    def uses_target(self) -> bool:
        return self.arm == "X0"


@dataclass(frozen=True)
class ExecutionPolicy:
    entry: EntryPolicy = EntryPolicy()
    exit: ExitPolicy = ExitPolicy()
    costs: Costs = Costs()
    version: str = EXECUTION_VERSION

    @property
    def arm(self) -> str:
        return f"{self.entry.arm}{self.exit.arm}"

    def to_dict(self) -> dict:
        return asdict(self) | {"arm": self.arm}


@dataclass(frozen=True)
class OrderPlan:
    """Prices fixed at the decision close (causal). `target` is ignored by exit arms without a target."""

    limit: float
    min_open: float
    stop: float
    target: float | None


def plan_from_levels(entry_min: float, entry_max: float, stop: float, target: float, close: float, atr: float,
                     entry: EntryPolicy) -> OrderPlan | None:
    """Same stop and target for every entry arm, so arms differ only in the entry price (factorial attribution)."""
    if entry.arm == "E0":
        return OrderPlan(entry_max, entry_min, stop, target)
    if entry.arm == "E1":
        limit = tick_round(close - entry.e1_below_close_atr * atr, down=True)
        if not limit > stop + TICK:
            return None
        return OrderPlan(min(limit, entry_max), stop + TICK, stop, target)
    raise ValueError(f"unknown entry arm {entry.arm}")


@dataclass(frozen=True)
class Leg:
    outcome: str  # TARGET | STOP | TIME | DATA_END
    exit_index: int
    exit_price: float
    gap: bool = False  # executed at the session's open (gap through a barrier, or a delayed time exit)


@dataclass(frozen=True)
class TradeV2:
    filled: bool
    status: str  # FILLED | NO_FILL | DATA_END
    entry_index: int | None = None
    entry_price: float | None = None  # raw executable price, before costs
    fill_kind: str | None = None
    pess: Leg | None = None
    opt: Leg | None = None
    ambiguity: str = "NONE"
    mfe_bounds: tuple[float, float] | None = None
    mae_bounds: tuple[float, float] | None = None
    observed_intraday_path: bool = False
    flags: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.ambiguity == "NONE"

    def leg(self, which: str = "pess") -> Leg | None:
        return self.pess if which == "pess" else self.opt

    def net(self, costs: Costs, which: str = "pess") -> float | None:
        lg = self.leg(which)
        return None if lg is None or self.entry_price is None else costs.net(self.entry_price, lg.exit_price)

    def r_multiple(self, costs: Costs, stop: float, which: str = "pess") -> float | None:
        lg, c = self.leg(which), costs.bps_per_side / 1e4
        if lg is None or self.entry_price is None:
            return None
        risk = self.entry_price * (1 + c) - stop
        return (lg.exit_price * (1 - c) - self.entry_price * (1 + c)) / risk if risk > 0 else None

    def sessions_held(self, which: str = "pess") -> int | None:
        lg = self.leg(which)
        return None if lg is None or self.entry_index is None else lg.exit_index - self.entry_index


def _fill(o, h, low, c, t: int, plan: OrderPlan, entry: EntryPolicy, ok: np.ndarray) -> tuple[int, float, str, list[str]] | None:
    flags: list[str] = []
    through = entry.trade_through_ticks * TICK
    for d in range(t + 1, min(len(c), t + 1 + entry.valid_sessions)):
        if not ok[d]:
            flags.append("MISSING_BAR_IN_ENTRY_WINDOW")
            continue
        if o[d] < plan.min_open:
            continue
        if o[d] <= plan.limit:
            return d, float(o[d]), OPEN, flags
        if low[d] <= plan.limit - through:
            return d, float(plan.limit), LIMIT, flags
    return None


def _segment(o, h, low, c, d: int, start: float, start_is_open: bool, low_after_start: bool, end_kind: str | None,
             end_px: float | None, stop_d: float, over: float = 0.0) -> tuple[float, float, float, float]:
    """(max_known, max_possible, min_known, min_possible) of the price over one session's held segment."""
    if end_kind == "GAP":
        return end_px, end_px, end_px, end_px
    end = c[d] if end_kind is None else end_px
    to_close = end_kind is None
    max_known = max(start, end, h[d] if start_is_open and to_close else -math.inf)
    max_possible = end_px + over if end_kind == "TARGET" else h[d]  # a trade-through target sells above its price
    min_known = min(start, end, low[d] if low_after_start and to_close else math.inf)
    min_possible = stop_d if end_kind == "STOP" else low[d]
    return max_known, max(max_possible, max_known), min_known, min(min_possible, min_known)


def simulate_v2(
    o: np.ndarray,
    h: np.ndarray,
    low: np.ndarray,
    c: np.ndarray,
    decision_index: int,
    plan: OrderPlan,
    policy: ExecutionPolicy = ExecutionPolicy(),
    atr: np.ndarray | None = None,
    valid: np.ndarray | None = None,
) -> TradeV2:
    """`valid` (optional, precomputed by callers that simulate many trades): True where all four prices are finite."""
    n = len(c)
    ok = valid if valid is not None else (np.isfinite(o) & np.isfinite(h) & np.isfinite(low) & np.isfinite(c))

    got = _fill(o, h, low, c, decision_index, plan, policy.entry, ok)
    if got is None:
        status = "NO_FILL" if decision_index + policy.entry.valid_sessions < n else "DATA_END"
        return TradeV2(False, status)
    fi, px, kind, flags = got
    xp = policy.exit
    tgt = plan.target if xp.uses_target else None
    tt = policy.entry.trade_through_ticks * TICK  # touch-fill sensitivity: resting buy AND sell limits need a trade-through
    if xp.arm == "X2" and atr is None:
        raise ValueError("X2 trailing exit needs the causal ATR series")
    last = min(n - 1, fi + xp.horizon_sessions)
    stops: dict[int, float] = {fi: plan.stop}

    def stop_on(d: int) -> float:  # trailing stop for session d from closes/ATR through d-1 only
        if d in stops:
            return stops[d]
        prev = stop_on(d - 1)
        if xp.arm != "X2" or not math.isfinite(c[d - 1]) or atr is None or not math.isfinite(atr[d - 1]):
            stops[d] = prev
        else:
            hi_close = np.nanmax(c[fi : d])
            stops[d] = max(prev, float(hi_close - xp.trail_atr * atr[d - 1]))
        return stops[d]

    def walk(start_day: int) -> tuple[Leg, Leg, str]:
        """Legs from `start_day` (> fill day) to the exit; terminal ambiguities only."""
        for d in range(start_day, last + 1):
            if not ok[d]:
                flags.append("MISSING_BAR_WHILE_HELD")
                continue
            s = stop_on(d)
            if o[d] <= s:
                leg = Leg("STOP", d, float(o[d]), True)
                return leg, leg, "NONE"
            if tgt is not None and o[d] >= tgt:
                leg = Leg("TARGET", d, float(o[d]), True)
                return leg, leg, "NONE"
            hit_s, hit_t = low[d] <= s, tgt is not None and h[d] >= tgt + tt
            if hit_s and hit_t:
                return Leg("STOP", d, s), Leg("TARGET", d, tgt), "SAME_BAR_STOP_TARGET"
            if hit_s:
                leg = Leg("STOP", d, s)
                return leg, leg, "NONE"
            if hit_t:
                leg = Leg("TARGET", d, tgt)
                return leg, leg, "NONE"
            if d == fi + xp.horizon_sessions:
                leg = Leg("TIME", d, float(c[d]))
                return leg, leg, "NONE"
        horizon_day = fi + xp.horizon_sessions
        if horizon_day <= n - 1:  # the horizon bar is missing: sell at the next session's open
            k = next((k for k in range(horizon_day + 1, n) if ok[k]), None)
            if k is not None:
                flags.append("TIME_EXIT_DELAYED_MISSING_BAR")
                leg = Leg("TIME", k, float(o[k]), True)
                return leg, leg, "NONE"
        j = max((k for k in range(fi, n) if math.isfinite(c[k])), default=fi)
        flags.append("ENDED_BEFORE_HORIZON")
        leg = Leg("DATA_END", j, float(c[j]))
        return leg, leg, "NONE"

    # ---- fill session ------------------------------------------------------------------------------------------
    s0 = plan.stop
    hit_s = low[fi] <= s0
    hit_t = tgt is not None and h[fi] >= tgt + tt
    if kind == OPEN:
        if hit_s and hit_t:
            pess, opt, amb = Leg("STOP", fi, s0), Leg("TARGET", fi, tgt), "SAME_BAR_STOP_TARGET"
        elif hit_s:
            pess = opt = Leg("STOP", fi, s0)
            amb = "NONE"
        elif hit_t:
            pess = opt = Leg("TARGET", fi, tgt)
            amb = "NONE"
        else:
            pess, opt, amb = walk(fi + 1)
    else:  # LIMIT: approached from above; the low and any stop touch are after the fill, the high may be before it
        post_target = tgt is not None and c[fi] >= tgt + tt
        if hit_s and hit_t:
            pess, opt, amb = Leg("STOP", fi, s0), Leg("TARGET", fi, tgt), "SAME_BAR_STOP_TARGET"
        elif hit_s:
            pess = opt = Leg("STOP", fi, s0)
            amb = "NONE"
        elif post_target:
            pess = opt = Leg("TARGET", fi, tgt)
            amb = "NONE"
        elif hit_t:
            sell = Leg("TARGET", fi, tgt)
            cp, co, _ = walk(fi + 1)
            pess = min(sell, cp, key=lambda lg: lg.exit_price)
            opt = max(sell, co, key=lambda lg: lg.exit_price)
            amb = "FILL_DAY_TARGET_ORDER"
        else:
            pess, opt, amb = walk(fi + 1)

    # ---- excursion bounds over the pessimistic leg (interior sessions vectorised; fill and exit sessions exact) ---
    ex = pess.exit_index
    mx_k, mx_p, mn_k, mn_p = -math.inf, -math.inf, math.inf, math.inf
    if ex - fi > 1:
        inner = slice(fi + 1, ex)
        m_ok = ok[inner]
        if m_ok.any():
            mx_k = mx_p = float(np.max(h[inner][m_ok]))
            mn_k = mn_p = float(np.min(low[inner][m_ok]))
    for d in sorted({fi, ex}):
        if not ok[d]:
            continue
        end_kind, end_px = None, None
        if d == ex and (pess.gap or pess.outcome in ("STOP", "TARGET")):
            end_kind, end_px = ("GAP" if pess.gap else pess.outcome), pess.exit_price
        if d == fi:
            seg = _segment(o, h, low, c, d, px, kind == OPEN, True, end_kind, end_px, s0, tt)
        else:
            seg = _segment(o, h, low, c, d, float(o[d]), True, True, end_kind, end_px, stop_on(d), tt)
        mx_k, mx_p, mn_k, mn_p = max(mx_k, seg[0]), max(mx_p, seg[1]), min(mn_k, seg[2]), min(mn_p, seg[3])
    mfe = (round(mx_k / px - 1, 6), round(mx_p / px - 1, 6))
    mae = (round(mn_p / px - 1, 6), round(mn_k / px - 1, 6))
    return TradeV2(True, "FILLED", fi, px, kind, pess, opt, amb, mfe, mae, False, tuple(dict.fromkeys(flags)))
