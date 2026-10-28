"""Whole-share simulation of a personal method (personal/method.py) on daily bars, and its comparison with SPY.

* The first decision is taken at the close of `first_decision` (the account starts in cash), later ones at the close of
  each month's last session, and for a daily method (TrendSpec) at every close too, where only a switch makes orders.
  Every decision's orders fill at the next session's OPEN: sells first, then buys in the method's order. `cost_bps` per
  side is charged on the traded value (spread and slippage; Ally charges no commission). A buy that no longer fits the
  cash at the open is cut to the shares that fit; a symbol without an open that session is retried at the next open.
* Equity is marked at every close (a missing close carries the last one). Cash earns nothing.
* The benchmark is SPY bought with the whole starting cash at the same first open, fractional and frictionless: the
  index return a $1000 account would have earned without any method.
Returns follow the bars' adjustment for the method and SPY alike: price returns on split-adjusted bars
(personal-study-1), total returns on split- and dividend-adjusted bars (personal-study-2).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from apex_worker.market.calendar import MarketCalendar
from apex_worker.personal import method as M
from apex_worker.research.training_data import BENCHMARK

YEAR = 252


@dataclass
class SimResult:
    equity: pd.Series  # marked at each close from the first fill session
    cash: pd.Series
    positions: pd.Series  # held ETFs at each close
    trades: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    costs_paid: float = 0.0


def _closes(bars: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
    return {s: df["close"].astype(float) for s, df in bars.items()}


def simulate(spec: M.Spec, bars: dict[str, pd.DataFrame], first_decision: date, end: date,
             cal: MarketCalendar, *, initial_cash: float = 1000.0, cost_bps: float = 5.0) -> SimResult:
    sessions = [d for d in cal.sessions_between(first_decision, end)]
    closes = _closes(bars)
    opens = {s: df["open"].astype(float) for s, df in bars.items()}
    cost = cost_bps / 1e4
    holdings: dict[str, int] = {}
    cash = float(initial_cash)
    pending: list[M.Order] = []
    eq, ca, npos = {}, {}, {}
    last_close: dict[str, float] = {}
    trades: list[dict] = []
    decisions: list[dict] = []
    paid = 0.0
    for i, d in enumerate(sessions):
        ts = pd.Timestamp(d)
        if pending:
            retry: list[M.Order] = []
            for o in sorted(pending, key=lambda o: o.action not in M.SELLS):  # sells first, then buys in order
                px = opens.get(o.symbol, pd.Series(dtype=float)).get(ts)
                if px is None or not math.isfinite(px) or px <= 0:
                    retry.append(o)
                    continue
                if o.action in M.SELLS:
                    n = min(o.shares, holdings.get(o.symbol, 0))
                    proceeds = n * px * (1 - cost)
                    cash += proceeds
                    paid += n * px * cost
                    holdings[o.symbol] = holdings.get(o.symbol, 0) - n
                else:
                    n = min(o.shares, int(cash // (px * (1 + cost))))
                    if n <= 0:
                        continue
                    cash -= n * px * (1 + cost)
                    paid += n * px * cost
                    holdings[o.symbol] = holdings.get(o.symbol, 0) + n
                if n > 0:
                    trades.append({"day": d.isoformat(), "action": o.action, "symbol": o.symbol, "shares": n,
                                   "fill": "open", "reason": o.reason})
            holdings = {s: q for s, q in holdings.items() if q > 0}
            pending = retry
        for s in set(holdings) | {BENCHMARK}:
            c = closes.get(s, pd.Series(dtype=float)).get(ts)
            if c is not None and math.isfinite(c):
                last_close[s] = float(c)
        value = cash + sum(q * last_close.get(s, 0.0) for s, q in holdings.items())
        eq[ts], ca[ts], npos[ts] = value, cash, len(holdings)
        last = i == len(sessions) - 1
        scheduled = i == 0 or M.is_decision_day(cal, d)
        if not last and (scheduled or spec.daily):
            dec = M.decide(spec, closes, d, holdings, cash, rebalance=scheduled)
            pending = [*pending, *dec.orders]
            if scheduled or dec.orders:  # a daily check without a switch is not a decision
                decisions.append({"day": d.isoformat(), "target": dec.target, "orders": len(dec.orders)})
    return SimResult(pd.Series(eq), pd.Series(ca), pd.Series(npos), trades, decisions, paid)


def spy_benchmark(bars: dict[str, pd.DataFrame], equity_index: pd.DatetimeIndex, initial_cash: float) -> pd.Series:
    """The same cash at the first decision close, then SPY bought at the next open (where the method's first orders
    fill), fractional and frictionless."""
    spy = bars[BENCHMARK]
    shares = initial_cash / float(spy["open"].astype(float).get(equity_index[1]))
    out = shares * spy["close"].astype(float).reindex(equity_index).ffill()
    out.iloc[0] = initial_cash
    return out


def _drawdown(e: pd.Series) -> float:
    return float((e / e.cummax() - 1).min())


def _hac_t(x: np.ndarray, lags: int = 3) -> float | None:
    """Newey-West t statistic of the mean of `x`."""
    n = len(x)
    if n < 6:
        return None
    u = x - x.mean()
    s = float(u @ u) / n
    for k in range(1, min(lags, n - 1) + 1):
        s += 2 * (1 - k / (lags + 1)) * float(u[k:] @ u[:-k]) / n
    return float(x.mean() / math.sqrt(s / n)) if s > 0 else None


def _p_one_sided(t: float | None) -> float | None:
    return None if t is None else 0.5 * math.erfc(t / math.sqrt(2))


def metrics(equity: pd.Series, bench: pd.Series) -> dict:
    """Growth, risk and the comparison with SPY on the same sessions."""
    years = (len(equity) - 1) / YEAR
    g = math.log(equity.iloc[-1] / equity.iloc[0]) / years
    gb = math.log(bench.iloc[-1] / bench.iloc[0]) / years
    lr = np.log(equity / equity.shift(1)).dropna().to_numpy()
    vol = float(lr.std(ddof=1) * math.sqrt(YEAR)) if len(lr) > 1 else float("nan")
    me, mb = equity.resample("ME").last(), bench.resample("ME").last()
    me = pd.concat([equity.iloc[:1], me]).groupby(level=0).last()
    mb = pd.concat([bench.iloc[:1], mb]).groupby(level=0).last()
    active = (me.pct_change() - mb.pct_change()).dropna().to_numpy()
    t = _hac_t(active)
    roll = (me / me.shift(12) - mb / mb.shift(12)).dropna()
    ye, yb = equity.resample("YE").last(), bench.resample("YE").last()
    starts_e = pd.concat([equity.iloc[:1], ye.iloc[:-1]])
    starts_b = pd.concat([bench.iloc[:1], yb.iloc[:-1]])
    yearly = {str(y.year): {"method": float(ye.iloc[i] / starts_e.iloc[i] - 1),
                            "spy": float(yb.iloc[i] / starts_b.iloc[i] - 1)} for i, y in enumerate(ye.index)}
    return {"years": years, "start_value": float(equity.iloc[0]), "end_value": float(equity.iloc[-1]),
            "end_value_spy": float(bench.iloc[-1]), "log_growth_per_year": g, "spy_log_growth_per_year": gb,
            "excess_log_growth_per_year": g - gb, "cagr": math.exp(g) - 1, "spy_cagr": math.exp(gb) - 1,
            "max_drawdown": _drawdown(equity), "spy_max_drawdown": _drawdown(bench), "volatility": vol,
            "sharpe_log": g / vol if vol and math.isfinite(vol) and vol > 0 else None,
            "monthly_active": {"months": len(active), "mean": float(active.mean()) if len(active) else None,
                               "hac_t": t, "one_sided_p": _p_one_sided(t)},
            "rolling_12m_beat_share": float((roll > 0).mean()) if len(roll) else None, "calendar_years": yearly}


__all__ = ["SimResult", "metrics", "simulate", "spy_benchmark"]
