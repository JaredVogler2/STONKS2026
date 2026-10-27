"""The personal method: a monthly sector-ETF momentum rotation sized in WHOLE shares for a small account.

One decision function serves the backtest (personal/backtest.py, discovery/v3/personal_study.py) and any live alert
that follows the method, so what the study measured is exactly what an alert would send.

* Universe: the 11 Select Sector SPDR ETFs. An ETF is eligible once it has `lookback` + 1 closes.
* Signal: the price return over the last `lookback` (126) sessions through the decision close t.
* Selection: the top `slots` (3) by the signal. A held ETF stays while it ranks within `keep` (5), a buffer that cuts
  turnover. In the `absolute` variant an ETF qualifies only with a positive signal; an empty slot stays in cash.
* Sizing in whole shares (Ally Invest has no fractional shares): the target per slot is equity / slots.
  - EXIT: a holding that leaves the selection is sold in full.
  - TRIM: a holding worth more than `trim_above` x target is sold down to the target.
  - BUY: a new pick gets floor(target / price) shares, in rank order, as far as cash allows.
  - ADD: a kept holding at least one share below its target is topped up, as far as cash allows (deposits flow here).
* Cadence: decided after the close of the last session of each month, traded during the next session. A first
  decision may also be taken on any session (an account that starts in cash).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date

import pandas as pd

from apex_worker.market.calendar import MarketCalendar
from apex_worker.research.training_data import SECTOR_ETFS

EXIT, TRIM, BUY, ADD = "EXIT", "TRIM", "BUY", "ADD"
SELLS, BUYS = (EXIT, TRIM), (BUY, ADD)
ETF_NAMES = {
    "XLK": "Technology Select Sector SPDR", "XLF": "Financial Select Sector SPDR",
    "XLE": "Energy Select Sector SPDR", "XLV": "Health Care Select Sector SPDR",
    "XLI": "Industrial Select Sector SPDR", "XLY": "Consumer Discretionary Select Sector SPDR",
    "XLP": "Consumer Staples Select Sector SPDR", "XLU": "Utilities Select Sector SPDR",
    "XLB": "Materials Select Sector SPDR", "XLRE": "Real Estate Select Sector SPDR",
    "XLC": "Communication Services Select Sector SPDR",
}


@dataclass(frozen=True)
class MethodSpec:
    method_id: str
    title: str
    universe: tuple[str, ...] = tuple(sorted(SECTOR_ETFS.values()))
    lookback: int = 126
    slots: int = 3
    keep: int = 5
    absolute: bool = False
    trim_above: float = 1.5
    cadence: str = "last session of each month"

    def to_dict(self) -> dict:
        return asdict(self)


SECTOR_MOM_3 = MethodSpec("sector-mom-3", "Top 3 sector ETFs by 6-month return, monthly, kept while in the top 5")
SECTOR_MOM_3_ABS = MethodSpec("sector-mom-3-abs", "The same, but only sectors with a positive 6-month return; the rest "
                              "in cash", absolute=True)
METHODS: dict[str, MethodSpec] = {m.method_id: m for m in (SECTOR_MOM_3, SECTOR_MOM_3_ABS)}


@dataclass(frozen=True)
class Order:
    action: str  # EXIT | TRIM | BUY | ADD
    symbol: str
    shares: int
    ref_price: float  # the decision close the shares were sized on
    reason: str


@dataclass
class Decision:
    day: date
    signals: dict[str, float]  # eligible ETF -> lookback return
    ranks: dict[str, int]
    target: list[str]  # the selection, best first
    orders: list[Order]
    equity: float  # cash + holdings at the decision closes
    cash_after: float  # estimated cash once every order fills at its reference price
    notes: list[str] = field(default_factory=list)


def is_decision_day(cal: MarketCalendar, day: date) -> bool:
    """The last session of a calendar month (the method's rebalance close)."""
    return cal.is_session(day) and cal.next_session(day).month != day.month


def signals(closes: dict[str, pd.Series], t: pd.Timestamp, spec: MethodSpec) -> dict[str, float]:
    """`lookback`-session price return through t for every ETF of the universe with enough history (bars after t are
    never read)."""
    out = {}
    for sym in spec.universe:
        s = closes.get(sym)
        if s is None:
            continue
        s = s[s.index <= t].dropna()
        if len(s) <= spec.lookback or s.index[-1] != t:
            continue
        a, b = float(s.iloc[-1 - spec.lookback]), float(s.iloc[-1])
        if a > 0 and math.isfinite(a) and math.isfinite(b):
            out[sym] = b / a - 1.0
    return out


def select(sig: dict[str, float], held: set[str], spec: MethodSpec) -> tuple[list[str], dict[str, int]]:
    """The target set (best first) and every eligible ETF's rank (1 = strongest; ties broken by symbol)."""
    ranked = sorted(sig, key=lambda s: (-sig[s], s))
    rank = {s: i + 1 for i, s in enumerate(ranked)}

    def ok(s: str) -> bool:
        return not spec.absolute or sig[s] > 0

    target = sorted((s for s in held if s in rank and rank[s] <= spec.keep and ok(s)), key=rank.get)[: spec.slots]
    for s in ranked:
        if len(target) >= spec.slots:
            break
        if s not in target and ok(s):
            target.append(s)
    return sorted(target, key=rank.get), rank


def orders(spec: MethodSpec, target: list[str], holdings: dict[str, int], cash: float,
           prices: dict[str, float], equity: float | None = None) -> tuple[list[Order], float]:
    """Whole-share orders that move `holdings` toward `target`, and the cash left if they fill at `prices`. `equity`
    (default: cash + holdings) sets the target per slot; it also counts holdings kept out of the decision."""
    held = {s: q for s, q in holdings.items() if q > 0}
    if equity is None:
        equity = cash + sum(q * prices[s] for s, q in held.items())
    per_slot = equity / spec.slots
    out: list[Order] = []
    for s in sorted(held):
        if s not in target:
            out.append(Order(EXIT, s, held[s], prices[s], "left the selection"))
            cash += held[s] * prices[s]
    for s in target:
        q = held.get(s, 0)
        if q and q * prices[s] > spec.trim_above * per_slot:
            n = int((q * prices[s] - per_slot) // prices[s])
            if n > 0:
                out.append(Order(TRIM, s, n, prices[s], f"above {spec.trim_above:g}x its target"))
                cash += n * prices[s]
    for s in target:
        if held.get(s, 0):
            continue
        n = int(min(per_slot, cash) // prices[s])
        if n > 0:
            out.append(Order(BUY, s, n, prices[s], "new pick"))
            cash -= n * prices[s]
    for s in target:
        q = held.get(s, 0)
        if not q:
            continue
        trimmed = sum(o.shares for o in out if o.symbol == s and o.action == TRIM)
        gap = per_slot - (q - trimmed) * prices[s]
        n = int(min(gap, cash) // prices[s])
        if n > 0:
            out.append(Order(ADD, s, n, prices[s], "below its target"))
            cash -= n * prices[s]
    return out, cash


def decide(spec: MethodSpec, closes: dict[str, pd.Series], day: date, holdings: dict[str, int],
           cash: float) -> Decision:
    """The method's decision at the close of `day`: signals, selection and whole-share orders sized on the closes."""
    t = pd.Timestamp(day)
    sig = signals(closes, t, spec)
    held = {s: q for s, q in holdings.items() if q > 0}
    last: dict[str, tuple[pd.Timestamp, float]] = {}
    for s in set(sig) | set(held):
        c = closes.get(s)
        c = c[c.index <= t].dropna() if c is not None else None
        if c is not None and len(c):
            last[s] = (c.index[-1], float(c.iloc[-1]))
    # a holding without a close at t cannot be ranked or resized: it keeps its shares and its slot, the rest is decided
    # around it (valued at its last close, if any)
    stale = sorted(s for s in held if s not in last or last[s][0] != t)
    live = {s: q for s, q in held.items() if s not in stale}
    target, rank = select(sig, set(live), spec)
    target = target[: max(0, spec.slots - len(stale))]
    prices = {s: v for s, (_d, v) in last.items()}
    equity = cash + sum(q * prices.get(s, 0.0) for s, q in held.items())
    notes = [f"no {day.isoformat()} close for {', '.join(stale)}: kept as is, not resized"] if stale else []
    ords, cash_after = orders(spec, target, live, cash, prices, equity=equity)
    return Decision(day, sig, rank, target, ords, equity, cash_after, notes)


__all__ = ["ADD", "BUY", "BUYS", "ETF_NAMES", "EXIT", "METHODS", "SECTOR_MOM_3", "SECTOR_MOM_3_ABS", "SELLS", "TRIM",
           "Decision", "MethodSpec", "Order", "decide", "is_decision_day", "orders", "select", "signals"]
