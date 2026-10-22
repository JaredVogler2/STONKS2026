"""Capital-constrained equal-slot book for protocol-2 (amendment 06 section 4; test matrix V16).

Input: executed trades (one row per filled signal: symbol, sector, entry/exit session index, raw entry/exit price,
priority). Rules, chosen to be conservative and free of loop-order artefacts:
* sessions run in order. ENTRIES of session d are funded from the cash held at the START of d. Proceeds of exits on d
  (which may happen intraday, after the open) are credited after that session's entries, so no same-day cash is
  created by loop order;
* an entry needs a free slot, no open position in the same symbol, and sector exposure after entry <= `sector_cap` of
  equity, where open positions count at their previous-close MARK (not their entry cost, which understates a sector
  whose holdings rose); the size is min(cash, equity at the previous close / slots). Below half a slot it is skipped. Every skip is
  counted by reason. Same-session entries are taken in `priority` order (higher first; ties by a seeded shuffle, never
  alphabetical);
* proportional costs per side, once: units = alloc / (entry (1 + c)); proceeds = units x exit x (1 - c);
* positions are marked at the session close (a missing close carries the last valid close); no leverage, no margin;
* invariant, checked every session: equity = cash + sum(units x mark).
The exposure-matched benchmark holds SPY with the book's previous-session exposure and cash otherwise (cash yield 0).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class BookResult:
    sessions: int
    taken: int
    skipped: dict[str, int]
    equity: np.ndarray
    exposure: np.ndarray
    total_return: float
    max_drawdown: float
    avg_exposure: float
    max_positions: int
    turnover: float  # traded notional / mean equity
    bench_equity: np.ndarray | None = None
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        years = self.sessions / 252
        r = self.total_return
        cagr = (1 + r) ** (1 / years) - 1 if years >= 1 and r > -1 else None
        out = {
            "sessions": self.sessions, "trades_taken": self.taken, "trades_skipped": dict(self.skipped),
            "total_return": round(r, 6), "cagr": None if cagr is None else round(cagr, 6),
            "cagr_note": None if cagr is not None else "not annualised: window shorter than one year",
            "max_drawdown": round(self.max_drawdown, 6), "avg_exposure": round(self.avg_exposure, 4),
            "max_positions": self.max_positions, "turnover": round(self.turnover, 3),
        }
        if self.bench_equity is not None and len(self.bench_equity):
            b = self.bench_equity
            out["exposure_matched_spy_total_return"] = round(float(b[-1] - 1), 6)
            out["active_total_return"] = round(r - float(b[-1] - 1), 6)
            out["exposure_matched_spy_max_drawdown"] = round(float((b / np.maximum.accumulate(b) - 1).min()), 6)
        return out


def _mark(series: np.ndarray, d: int, fallback: float) -> float:
    v = series[d] if d >= 0 else math.nan
    return float(v) if math.isfinite(v) else fallback


def run_book(trades: pd.DataFrame, closes: dict[str, np.ndarray], n_sessions: int, cost_bps: float = 25.0,
             slots: int = 10, sector_cap: float = 0.30, spy_close: np.ndarray | None = None, start: int | None = None,
             end: int | None = None, seed: int = 7) -> BookResult:
    c = cost_bps / 1e4
    need = {"symbol", "sector", "entry_index", "exit_index", "entry_price", "exit_price"}
    if not need <= set(trades.columns) and len(trades):
        raise ValueError(f"trades need columns {sorted(need)}")
    t = trades.copy()
    rng = np.random.default_rng(seed)
    t["_tie"] = rng.permutation(len(t)) if len(t) else []
    if "priority" not in t:
        t["priority"] = 0.0
    lo = int(t["entry_index"].min()) if start is None and len(t) else (start or 0)
    hi = int(t["exit_index"].max()) if end is None and len(t) else (end if end is not None else n_sessions - 1)
    by_entry: dict[int, list[dict]] = {}
    for rec in t.sort_values(["entry_index", "priority", "_tie"], ascending=[True, False, True]).to_dict("records"):
        by_entry.setdefault(int(rec["entry_index"]), []).append(rec)
    marks = {s: pd.Series(v).ffill().to_numpy(float) for s, v in closes.items()}
    cash, prev_eq = 1.0, 1.0
    pos: dict[tuple[int, int], dict] = {}
    skipped = {"slots_full": 0, "duplicate_symbol": 0, "sector_cap": 0, "insufficient_cash": 0}
    eq_curve, expo, n_open, traded, taken = [], [], [], 0.0, 0
    for d in range(lo, hi + 1):
        start_cash = cash
        for i, tr in enumerate(by_entry.get(d, ())):
            if len(pos) >= slots:
                skipped["slots_full"] += 1
                continue
            if any(p["symbol"] == tr["symbol"] for p in pos.values()):
                skipped["duplicate_symbol"] += 1
                continue
            alloc = min(start_cash, prev_eq / slots)
            if alloc < 0.5 * prev_eq / slots:
                skipped["insufficient_cash"] += 1
                continue
            sec_val = sum(p["units"] * _mark(marks[p["symbol"]], d - 1, p["entry"]) for p in pos.values()
                          if p["sector"] == tr["sector"])
            if tr["sector"] is not None and (sec_val + alloc) > sector_cap * prev_eq + 1e-12:
                skipped["sector_cap"] += 1
                continue
            units = alloc / (float(tr["entry_price"]) * (1 + c))
            pos[(d, i)] = {"symbol": tr["symbol"], "sector": tr["sector"], "units": units, "entry": float(tr["entry_price"]),
                      "exit_index": int(tr["exit_index"]), "exit_price": float(tr["exit_price"])}
            start_cash -= alloc
            cash -= alloc
            traded += alloc
            taken += 1
        n_open.append(len(pos))
        for k, p in list(pos.items()):
            if p["exit_index"] == d:
                proceeds = p["units"] * p["exit_price"] * (1 - c)
                cash += proceeds
                traded += proceeds
                del pos[k]
        val = 0.0
        for p in pos.values():
            mk = marks[p["symbol"]][d]
            val += p["units"] * (mk if math.isfinite(mk) else p["entry"])
        eq = cash + val
        if cash < -1e-9:
            raise AssertionError("book over-allocated: negative cash")
        eq_curve.append(eq)
        expo.append(val / eq if eq > 0 else 0.0)
        prev_eq = eq
    eq_arr = np.array(eq_curve) if eq_curve else np.array([1.0])
    ex_arr = np.array(expo) if expo else np.array([0.0])
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq_arr]))[1:]
    bench = None
    if spy_close is not None and eq_curve:
        sp = pd.Series(spy_close).ffill().to_numpy(float)
        b, prev_x = [1.0], 0.0
        for j, d in enumerate(range(lo, hi + 1)):
            r = sp[d] / sp[d - 1] - 1 if d > 0 and math.isfinite(sp[d - 1]) and sp[d - 1] > 0 else 0.0
            b.append(b[-1] * (1 + prev_x * r))
            prev_x = ex_arr[j]
        bench = np.array(b[1:])
    return BookResult(
        sessions=len(eq_curve), taken=taken,
        skipped=skipped, equity=eq_arr, exposure=ex_arr, total_return=float(eq_arr[-1] - 1),
        max_drawdown=float((eq_arr / peak - 1).min()), avg_exposure=float(ex_arr.mean()),
        max_positions=int(max(n_open) if n_open else 0), turnover=float(traded / max(eq_arr.mean(), 1e-12)),
        bench_equity=bench,
    )
