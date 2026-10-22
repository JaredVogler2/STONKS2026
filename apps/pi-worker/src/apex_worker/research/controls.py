"""Shared signal simulation and same-date matched executable controls for protocol-2 (QV2-007; amendment 04 section 5).

`Market` aligns every symbol on ONE session index (the benchmark's), so a strategy signal and its controls are
simulated by the same `simulate_signal` call: the same `compute_levels`, the same cost-aware screen and the same
`ExecutionPolicy`. Nothing here is a separate re-implementation of the live levels or execution rules.

Matched controls (per signal: symbol s, decision session t, stop distance k ATR):
* candidates are the stocks gate-eligible on session t inside the control POOL (all eligible stocks, or a
  strategy-specific mechanism pool such as "leaders"), excluding s and any symbol with its own signal that day;
* the stratum is the first with at least `min_stratum` candidates in the hierarchy
  sector x volatility tercile -> sector -> volatility tercile -> pool. Terciles of atr_pct_14 are formed on session t
  among eligible stocks, so they use no later data;
* each control gets the signal's stop distance (k ATR, clamped as live), the same target rule and resistance cap, and
  the same execution policy. Draws are without replacement; a control whose levels are rejected is replaced by the
  next candidate;
* the signal's control value is the mean over its K controls of the PER-SIGNAL net return: a no-fill counts as 0,
  because no capital was deployed. The same convention applies to the strategy value, so a no-fill is never dropped.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from apex_worker.research.execution_v2 import ExecutionPolicy, TradeV2, plan_from_levels, simulate_v2
from apex_worker.signals.levels import LevelConfig, Levels, compute_levels

MATCH_LEVELS = ("sector_vol", "sector", "vol", "pool")


@dataclass
class Market:
    sessions: pd.DatetimeIndex
    symbols: list[str]
    sector: dict[str, str | None]
    o: dict[str, np.ndarray]
    h: dict[str, np.ndarray]
    low: dict[str, np.ndarray]
    c: dict[str, np.ndarray]
    atr: dict[str, np.ndarray]
    resistance: dict[str, np.ndarray]  # highest close of the 120 sessions BEFORE t (min 60), as in build_events
    eligible: np.ndarray  # bool [symbol, session]: live gate mask (price, liquidity, history, extension, breaker)
    vol_tercile: np.ndarray  # int8 [symbol, session]: 0/1/2 among eligible stocks that session, -1 otherwise

    def __post_init__(self) -> None:
        self._rows = {s: i for i, s in enumerate(self.symbols)}
        self.valid = {s: np.isfinite(self.o[s]) & np.isfinite(self.h[s]) & np.isfinite(self.low[s]) & np.isfinite(self.c[s])
                      for s in self.symbols}
        sec_codes = {v: i for i, v in enumerate(sorted({x for x in self.sector.values() if x}))}
        self.sector_code = np.array([sec_codes.get(self.sector.get(s), -1) for s in self.symbols])

    def row(self, symbol: str) -> int:
        return self._rows[symbol]

    def has(self, symbol: str) -> bool:
        return symbol in self._rows

    @classmethod
    def from_frames(cls, sessions: pd.DatetimeIndex, bars: dict[str, pd.DataFrame], ctx: dict[str, pd.DataFrame],
                    eligible: dict[str, pd.Series], sector: dict[str, str | None]) -> Market:
        syms = sorted(ctx)
        al = {s: bars[s].reindex(sessions) for s in syms}
        cx = {s: ctx[s].reindex(sessions) for s in syms}
        elig = np.vstack([eligible[s].reindex(sessions).fillna(False).to_numpy(bool) for s in syms]) if syms else \
            np.zeros((0, len(sessions)), bool)
        atrp = np.vstack([cx[s]["atr_pct_14"].to_numpy(float) for s in syms]) if syms else np.zeros((0, len(sessions)))
        terc = np.full(elig.shape, -1, np.int8)
        for j in range(len(sessions)):
            m = elig[:, j] & np.isfinite(atrp[:, j])
            if m.sum() >= 3:
                r = pd.Series(atrp[m, j]).rank(method="first", pct=True).to_numpy()
                terc[m, j] = np.minimum((r * 3 - 1e-9).astype(int), 2)
        return cls(
            sessions, syms, {s: sector.get(s) for s in syms},
            {s: al[s]["open"].to_numpy(float) for s in syms}, {s: al[s]["high"].to_numpy(float) for s in syms},
            {s: al[s]["low"].to_numpy(float) for s in syms}, {s: al[s]["close"].to_numpy(float) for s in syms},
            {s: cx[s]["atr_14"].to_numpy(float) for s in syms},
            {s: cx[s]["close"].rolling(120, min_periods=60).max().shift(1).to_numpy(float) for s in syms},
            elig, terc,
        )


@dataclass(frozen=True)
class SimResult:
    levels: Levels
    trade: TradeV2


def simulate_signal(m: Market, symbol: str, t: int, policy: ExecutionPolicy, level_cfg: LevelConfig,
                    invalidation_hint: float | None = None, stop_distance_atr: float | None = None) -> SimResult | None:
    """Levels at the close of session t (causal) -> order plan -> v2 simulation on sessions after t. None = rejected.

    Exit arms without a target (X1, X2) never use the target, so the target-based screens (resistance cap, minimum net
    reward/risk) do not apply to them; the stop must still be valid. Signals and controls get the same treatment."""
    close, atr = float(m.c[symbol][t]), float(m.atr[symbol][t])
    if not (np.isfinite(close) and np.isfinite(atr)):
        return None
    if policy.exit.uses_target:
        lv = compute_levels(close, atr, invalidation_hint, float(m.resistance[symbol][t]), level_cfg,
                            stop_distance_atr=stop_distance_atr)
    else:
        lv = compute_levels(close, atr, invalidation_hint, None, replace(level_cfg, min_net_rr=-math.inf),
                            stop_distance_atr=stop_distance_atr)
    if not isinstance(lv, Levels):
        return None
    plan = plan_from_levels(lv.entry_min, lv.entry_max, lv.stop, lv.target, close, atr, policy.entry)
    if plan is None:
        return None
    tr = simulate_v2(m.o[symbol], m.h[symbol], m.low[symbol], m.c[symbol], t, plan, policy, m.atr[symbol], m.valid[symbol])
    return SimResult(lv, tr)


def per_signal_net(tr: TradeV2, policy: ExecutionPolicy, which: str = "pess") -> float | None:
    """Per-signal net return: no fill = 0 (no capital deployed); an incomplete label (DATA_END before entry) = None."""
    if not tr.filled:
        return 0.0 if tr.status == "NO_FILL" else None
    return tr.net(policy.costs, which)


def stop_distance_atr(lv: Levels, atr: float) -> float:
    return (lv.mid - lv.stop) / atr


@dataclass(frozen=True)
class ControlSet:
    value_pess: float | None
    value_opt: float | None
    n: int
    fills: int
    match: str
    symbols: tuple[str, ...]
    trades: tuple[TradeV2, ...]


def matched_controls(m: Market, symbol: str, t: int, k_atr: float, policy: ExecutionPolicy, level_cfg: LevelConfig,
                     rng: np.random.Generator, k: int = 20, pool: np.ndarray | None = None,
                     exclude: frozenset[str] = frozenset(), min_stratum: int = 5,
                     accept: Callable[[str, TradeV2], bool] | None = None) -> ControlSet:
    """K same-session controls for one signal; see the module docstring for the matching hierarchy. `accept` may reject
    a simulated candidate (e.g. a quarantined corporate-action window); it is then replaced by the next candidate."""
    elig = m.eligible[:, t].copy()
    if pool is not None:
        elig &= pool[:, t]
    for s in exclude | {symbol}:
        if m.has(s):
            elig[m.row(s)] = False
    cand = np.flatnonzero(elig)
    sec, terc = m.sector.get(symbol), int(m.vol_tercile[m.row(symbol), t]) if m.has(symbol) else -1
    code = m.sector_code[m.row(symbol)] if m.has(symbol) else -1
    same_sec = (m.sector_code[cand] == code) if sec is not None and code >= 0 else np.zeros(len(cand), bool)
    same_vol = m.vol_tercile[cand, t] == terc if terc >= 0 else np.zeros(len(cand), bool)
    match, chosen = "none", np.array([], int)
    for name, mask in zip(MATCH_LEVELS, (same_sec & same_vol, same_sec, same_vol, np.ones(len(cand), bool)), strict=True):
        if mask.sum() >= min_stratum:
            match, chosen = name, cand[mask]
            break
    vals_p, vals_o, syms, trades, fills = [], [], [], [], 0
    for j in rng.permutation(len(chosen)):  # without replacement: at most K distinct controls
        if len(vals_p) >= k:
            break
        s = m.symbols[chosen[j]]
        res = simulate_signal(m, s, t, policy, level_cfg, stop_distance_atr=k_atr)
        if res is None or (accept is not None and not accept(s, res.trade)):
            continue
        vp = per_signal_net(res.trade, policy, "pess")
        if vp is None:
            continue
        vals_p.append(vp)
        vals_o.append(per_signal_net(res.trade, policy, "opt"))
        syms.append(s)
        trades.append(res.trade)
        fills += res.trade.filled
    if not vals_p:
        return ControlSet(None, None, 0, 0, match, (), ())
    return ControlSet(float(np.mean(vals_p)), float(np.mean(vals_o)), len(vals_p), fills, match, tuple(syms), tuple(trades))
