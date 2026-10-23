"""Portfolio-first evaluation (05_PORTFOLIO_FIRST_OBJECTIVES.md): the unit of evaluation is a whole account.

`run_portfolio` is an equal-slot book in the conventions of research.book.run_book (entries funded from start-of-session
cash, exits credited after the session's entries, positions marked at the close, proportional costs once per side, no
leverage), with ONE added policy axis: what idle (un-deployed) equity holds.
* idle_capital = CASH: identical to run_book (asserted by tests; cash yield 0, a stated limitation);
* idle_capital = SPY: idle equity is held in SPY. An entry first spends cash, then sells SPY at that session's open; at
  the close all cash is swept into SPY. SPY trades cost `idle_cost_bps` per side.
Invariants checked every session (an AssertionError is a simulator defect, never a result): cash >= 0; positions <=
slots; stock + SPY value <= equity (no leverage); equity = cash + positions + SPY at the marks.

Benchmarks over the same sessions and clock (close of lo-1 to close of hi): CASH; SPY buy-and-hold (one cost each way);
exposure-matched SPY/cash (the previous close's equity exposure, stocks plus any SPY sleeve, held in SPY and the rest in
cash: book.py's comparator for a cash-idle book); and, for an idle-SPY policy, full SPY is the fair comparator. Daily
active returns are tested with protocol-2's dependent block bootstrap; results are DEV diagnostics on contaminated
history, never validation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from apex_worker.discovery.v3.contracts import AllocationSpec

DEFAULT_ALLOCATION = AllocationSpec()
ETF_COST_BPS = 5.0  # assumed SPY spread + impact per side for idle-capital sweeps (stated assumption, stressed in tests)
TRADING_DAYS = 252


@dataclass
class PortfolioResult:
    returns: np.ndarray  # daily net returns, sessions lo..hi (close-to-close; day lo is from the close of lo-1)
    equity: np.ndarray
    stock_exposure: np.ndarray  # stock value / equity at each close
    idle_exposure: np.ndarray  # SPY sleeve value / equity at each close
    taken: int
    skipped: dict[str, int]
    traded_notional: float
    costs_paid: float
    stock_pnl: float  # net P&L of closed stock trades in equity units
    deployed_capital_days: float  # sum over closes of stock value (equity units)
    max_positions: int
    lo: int
    hi: int
    notes: list[str] = field(default_factory=list)
    stock_traded_notional: float = 0.0  # stock entries plus exits only (no SPY sweeps): pilot-1 finding A-2


def _mark(series: np.ndarray, d: int, fallback: float) -> float:
    v = series[d] if 0 <= d < len(series) else math.nan
    return float(v) if math.isfinite(v) else fallback


def run_portfolio(trades: pd.DataFrame, closes: dict[str, np.ndarray], spy_open: np.ndarray, spy_close: np.ndarray,
                  lo: int, hi: int, alloc: AllocationSpec = DEFAULT_ALLOCATION, cost_bps: float = 25.0,
                  idle_cost_bps: float = ETF_COST_BPS, seed: int = 7, *, entry_at: str = "open",
                  entry_cost_bps: float | None = None, exit_cost_bps: float | None = None,
                  stop_exit_cost_bps: float | None = None) -> PortfolioResult:
    """Trades need symbol, sector, entry_index, exit_index, entry_price, exit_price (raw executable prices) and
    optionally priority and exit_kind (the leg outcome). Prices stay inside this function; results hold returns and
    ratios only.

    Pilot-2 options (defaults reproduce the pilot-1 book exactly): `entry_at="close"` funds entries at the entry
    session's CLOSE (SPY sold at that close; positions bought at the close carry no intraday P&L), after the session's
    exits, which still cannot fund same-session entries (conservative). Per-side costs can differ by side: entries
    (`entry_cost_bps`), time/data-end exits (`exit_cost_bps`) and STOP/TARGET exits inside the session
    (`stop_exit_cost_bps`); each defaults to `cost_bps`."""
    if entry_at not in ("open", "close"):
        raise ValueError("entry_at must be open or close")
    c_in = (cost_bps if entry_cost_bps is None else entry_cost_bps) / 1e4
    c_out = (cost_bps if exit_cost_bps is None else exit_cost_bps) / 1e4
    c_stop = (cost_bps if stop_exit_cost_bps is None else stop_exit_cost_bps) / 1e4
    ci = idle_cost_bps / 1e4
    idle_spy = alloc.idle_capital == "SPY"
    t = trades.copy() if len(trades) else pd.DataFrame(columns=["symbol", "sector", "entry_index", "exit_index",
                                                                "entry_price", "exit_price", "priority"])
    if "priority" not in t:
        t["priority"] = 0.0
    if "exit_kind" not in t:
        t["exit_kind"] = "TIME"
    rng = np.random.default_rng(seed)
    t["_tie"] = rng.permutation(len(t)) if len(t) else []
    by_entry: dict[int, list[dict]] = {}
    for rec in t.sort_values(["entry_index", "priority", "_tie"], ascending=[True, False, True]).to_dict("records"):
        by_entry.setdefault(int(rec["entry_index"]), []).append(rec)
    marks = {s: pd.Series(v).ffill().to_numpy(float) for s, v in closes.items()}
    so, sc = pd.Series(spy_open).to_numpy(float), pd.Series(spy_close).ffill().to_numpy(float)
    cash, spy_units, prev_eq = 1.0, 0.0, 1.0
    if idle_spy:
        px0 = sc[lo - 1] if lo > 0 and math.isfinite(sc[lo - 1]) else so[lo]
        spy_units = cash / (px0 * (1 + ci))  # start fully in SPY at the close of lo-1
        costs = cash - spy_units * px0
        cash = 0.0
    else:
        costs = 0.0
    pos: dict[tuple[int, int], dict] = {}
    skipped = {"slots_full": 0, "duplicate_symbol": 0, "sector_cap": 0, "insufficient_cash": 0}
    rets, eqs, sx, ix = [], [], [], []
    traded, stock_traded, taken, pnl, cap_days, max_pos = 0.0, 0.0, 0, 0.0, 0.0, 0
    for d in range(lo, hi + 1):
        spy_px_open = so[d] if math.isfinite(so[d]) else _mark(sc, d - 1, 1.0)
        fund_px = spy_px_open if entry_at == "open" else _mark(sc, d, spy_px_open)  # where SPY is sold to fund entries
        mark_day = d - 1 if entry_at == "open" else d  # sector-cap marks known when the entry order is placed
        start_cash = cash
        start_spy = spy_units * fund_px * (1 - ci) if idle_spy else 0.0

        # pilot-1 order: open entries, then the session's exits (a fill-bar stop included); close entries: the exits
        # first (a close fill cannot exit before the next session), and their proceeds stay out of this close's
        # entries because start_cash was fixed above (conservative)
        for phase in (("enter", "leave") if entry_at == "open" else ("leave", "enter")):
            if phase == "leave":
                for k, p in list(pos.items()):
                    if p["exit_index"] != d:
                        continue
                    c = c_stop if p["exit_kind"] in ("STOP", "TARGET") else c_out
                    gross = p["units"] * p["exit_price"]
                    proceeds = gross * (1 - c)
                    costs += gross * c
                    cash += proceeds
                    traded += proceeds
                    stock_traded += proceeds
                    pnl += proceeds - p["cost_in"]
                    del pos[k]
                continue
            for i, tr in enumerate(by_entry.get(d, ())):
                if len(pos) >= alloc.slots:
                    skipped["slots_full"] += 1
                    continue
                if any(p["symbol"] == tr["symbol"] for p in pos.values()):
                    skipped["duplicate_symbol"] += 1
                    continue
                avail = start_cash + start_spy
                want = prev_eq / alloc.slots * alloc.max_exposure
                amount = min(avail, want)
                if amount < 0.5 * want:
                    skipped["insufficient_cash"] += 1
                    continue
                sec_val = sum(p["units"] * _mark(marks[p["symbol"]], mark_day, p["entry"]) for p in pos.values()
                              if p["sector"] == tr["sector"])
                if tr["sector"] is not None and (sec_val + amount) > alloc.sector_cap * prev_eq + 1e-12:
                    skipped["sector_cap"] += 1
                    continue
                from_cash = min(start_cash, amount)
                from_spy = amount - from_cash
                if from_spy > 0:  # sell SPY (at this session's open, or its close for close entries) to fund the rest
                    sell_units = from_spy / (fund_px * (1 - ci))
                    spy_units -= sell_units
                    costs += sell_units * fund_px * ci
                    traded += sell_units * fund_px
                    start_spy -= from_spy
                start_cash -= from_cash
                cash -= from_cash
                units = amount / (float(tr["entry_price"]) * (1 + c_in))
                costs += amount - units * float(tr["entry_price"])
                pos[(d, i)] = {"symbol": tr["symbol"], "sector": tr["sector"], "units": units,
                               "entry": float(tr["entry_price"]), "cost_in": amount, "exit_index": int(tr["exit_index"]),
                               "exit_price": float(tr["exit_price"]), "exit_kind": str(tr["exit_kind"])}
                traded += amount
                stock_traded += amount
                taken += 1
            max_pos = max(max_pos, len(pos))  # measured right after the entries, as in pilot 1
            if len(pos) > alloc.slots:
                raise AssertionError("book holds more positions than slots")
        if idle_spy and cash > 0 and math.isfinite(sc[d]):  # sweep the close's cash into SPY
            buy_units = cash / (sc[d] * (1 + ci))
            costs += cash - buy_units * sc[d]
            traded += cash
            spy_units += buy_units
            cash = 0.0
        val = sum(p["units"] * _mark(marks[p["symbol"]], d, p["entry"]) for p in pos.values())
        spy_val = spy_units * _mark(sc, d, spy_px_open)
        eq = cash + val + spy_val
        if cash < -1e-9 or spy_units < -1e-12:
            raise AssertionError("book over-allocated: negative cash or SPY units")
        if val + spy_val > eq + 1e-9:
            raise AssertionError("leverage: invested value exceeds equity")
        rets.append(eq / prev_eq - 1)
        eqs.append(eq)
        sx.append(val / eq if eq > 0 else 0.0)
        ix.append(spy_val / eq if eq > 0 else 0.0)
        cap_days += val
        prev_eq = eq
    return PortfolioResult(np.array(rets), np.array(eqs), np.array(sx), np.array(ix), taken, skipped, traded, costs, pnl,
                           cap_days, max_pos, lo, hi, stock_traded_notional=stock_traded)


def close_rebalance_returns(weights: pd.DataFrame, closes: dict[str, pd.Series], cost_bps: float
                            ) -> tuple[pd.Series, pd.Series]:
    """(daily net return, end-of-day exposure) of a target-weight book whose decision row at t (data through the close
    of t) is traded at the CLOSE of t + 1: one session later than quant_v2.allocation_returns, which trades at the next
    open. The held weights drift with prices; costs are charged on the traded weight at that close."""
    cols, idx = list(weights.columns), weights.index
    c = pd.DataFrame({s: closes[s].reindex(idx) for s in cols}).astype(float).ffill().to_numpy(float)
    w = weights.to_numpy(float)
    has = ~np.all(np.isnan(w), axis=1)
    cst = cost_bps / 1e4
    cur = np.zeros(len(cols))
    rets, expo = np.zeros(len(idx)), np.zeros(len(idx))
    pending = None
    for i in range(1, len(idx)):
        day = np.nan_to_num(c[i] / c[i - 1] - 1)
        r = float(cur @ day)
        val = cur * (1 + day)
        cur = val / (1 + r) if (1 + r) > 0 else val
        traded = 0.0
        if pending is not None:  # the previous close's decision, executed at this close
            traded = float(np.abs(pending - cur).sum())
            cur, pending = pending.copy(), None
        rets[i] = (1 + r) * (1 - cst * traded) - 1
        expo[i] = float(cur.sum())
        if has[i]:
            pending = np.nan_to_num(w[i])
    return pd.Series(rets, index=idx), pd.Series(expo, index=idx)


# ---- benchmarks ---------------------------------------------------------------------------------------------------------
def spy_returns(spy_close: np.ndarray, lo: int, hi: int) -> np.ndarray:
    sc = pd.Series(spy_close).ffill().to_numpy(float)
    return np.array([sc[d] / sc[d - 1] - 1 if d > 0 else 0.0 for d in range(lo, hi + 1)])


def benchmark_returns(port: PortfolioResult, spy_close: np.ndarray, cost_bps: float = ETF_COST_BPS) -> dict[str, np.ndarray]:
    r_spy = spy_returns(spy_close, port.lo, port.hi)
    c = cost_bps / 1e4
    hold = r_spy.copy()
    if len(hold):
        hold[0] = (1 + hold[0]) / (1 + c) - 1  # buy at the close of lo-1
        hold[-1] = (1 + hold[-1]) * (1 - c) - 1  # sell at the close of hi
    total = port.stock_exposure + port.idle_exposure  # equity exposure of the whole account (stocks + SPY sleeve)
    prev_x = np.concatenate([[0.0], total[:-1]])
    return {"cash": np.zeros(len(r_spy)), "spy_hold": hold, "exposure_matched": prev_x * r_spy}


# ---- metrics ----------------------------------------------------------------------------------------------------------
def _r(x, nd: int = 6):
    return None if x is None or not math.isfinite(float(x)) else round(float(x), nd)


def metrics(r: np.ndarray) -> dict:
    r = np.asarray(r, float)
    if not len(r):
        return {"sessions": 0}
    eq = np.cumprod(1 + r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    lg = np.log1p(r)
    n = len(r)
    worst = np.sort(r)[: max(1, math.ceil(0.05 * n))]
    down = r[r < 0]
    years = n / TRADING_DAYS
    return {"sessions": n, "total_return": _r(eq[-1] - 1), "cagr": _r(eq[-1] ** (1 / years) - 1) if years >= 1 else None,
            "log_growth_annual": _r(lg.mean() * TRADING_DAYS), "vol_annual": _r(r.std(ddof=1) * math.sqrt(TRADING_DAYS))
            if n > 1 else None, "downside_vol_annual": _r(np.sqrt(np.mean(np.minimum(r, 0) ** 2)) * math.sqrt(TRADING_DAYS)),
            "max_drawdown": _r((eq / peak - 1).min()), "expected_shortfall_5": _r(worst.mean()),
            "worst_day": _r(r.min()), "share_down_days": _r(len(down) / n, 4)}


def describe_portfolio(port: PortfolioResult) -> dict:
    m = metrics(port.returns)
    eq_mean = float(np.mean(port.equity)) if len(port.equity) else 1.0
    return m | {"avg_stock_exposure": _r(port.stock_exposure.mean(), 4) if len(port.stock_exposure) else None,
                "avg_idle_spy_exposure": _r(port.idle_exposure.mean(), 4) if len(port.idle_exposure) else None,
                "cash_days_share": _r(float(np.mean(port.stock_exposure + port.idle_exposure < 0.01)), 4)
                if len(port.stock_exposure) else None,
                "trades_taken": port.taken, "trades_skipped": dict(port.skipped), "max_positions": port.max_positions,
                "turnover_per_year": _r(port.traded_notional / eq_mean / max(len(port.returns) / TRADING_DAYS, 1e-9), 3),
                "stock_turnover_per_year": _r(port.stock_traded_notional / eq_mean
                                              / max(len(port.returns) / TRADING_DAYS, 1e-9), 3),
                "costs_paid": _r(port.costs_paid), "stock_pnl": _r(port.stock_pnl),
                "net_return_per_capital_day": _r(port.stock_pnl / port.deployed_capital_days, 8)
                if port.deployed_capital_days > 0 else None}


def beta(r: np.ndarray, m: np.ndarray) -> float | None:
    ok = np.isfinite(r) & np.isfinite(m)
    if ok.sum() < 30 or np.var(m[ok]) <= 0:
        return None
    return float(np.cov(r[ok], m[ok])[0, 1] / np.var(m[ok], ddof=1))


def active_test(r: np.ndarray, b: np.ndarray, n_boot: int | None = 999) -> dict:
    """Dependent block bootstrap of the daily active return (protocol-2's conservative rule over L = 20 and 40)."""
    from apex_worker.research.realeq.study_v2 import block_decision

    a = np.asarray(r, float) - np.asarray(b, float)
    res = block_decision(a, np.arange(len(a)), len(a), n_boot=n_boot)
    s = res["summary"]
    return {"mean_daily": _r(a.mean(), 8) if len(a) else None,
            "active_log_growth_annual": _r((np.log1p(r) - np.log1p(b)).mean() * TRADING_DAYS) if len(a) else None,
            "p_one_sided": _r(s.get("p_one_sided_max"), 5), "lower_95": _r(s.get("lower_95_min"), 8),
            "upper_95": _r(s.get("upper_95_max"), 8), "status": s.get("status")}


def decompose(policy: np.ndarray, *, spy: np.ndarray, exposure_matched: np.ndarray, controls: np.ndarray | None,
              zero_cost: np.ndarray | None) -> dict:
    """Annualised log-growth attribution (each term a difference of two comparable books; nonlinear interactions are
    an explicit residual, never hidden):
      total            = policy
      market           = exposure-matched SPY/cash (what the same exposure in SPY earned)
      selection        = policy - matched-control books (same execution, allocation and dates; other stocks)
      friction         = policy - the same policy at zero cost (negative: the cost drag)
      cash_drag        = exposure-matched - full SPY (the opportunity cost of idle capital in this window)
      residual         = total - market - selection (timing/allocation/interaction not separately identified)."""
    def g(x):
        return None if x is None else float(np.log1p(np.asarray(x, float)).mean() * TRADING_DAYS)

    tot, mkt, ctl, zc, sp = g(policy), g(exposure_matched), g(controls), g(zero_cost), g(spy)
    sel = None if ctl is None else tot - ctl
    out = {"total": tot, "market_exposure_matched": mkt, "selection_vs_controls": sel,
           "friction": None if zc is None else tot - zc, "cash_drag_vs_full_spy": mkt - sp,
           "residual_timing_allocation_interaction": None if sel is None else tot - mkt - sel,
           "beta_to_spy": beta(np.asarray(policy, float), np.asarray(spy, float))}
    return {k: _r(v) for k, v in out.items()}
