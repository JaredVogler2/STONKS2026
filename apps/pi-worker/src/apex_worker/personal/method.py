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

Two published single-asset rules (`personal-study-2`, docs/discovery-v3/PERSONAL_STUDY_2.md) share the same orders:
* TrendSpec (Gayed & Bilello 2016, "Leverage for the Long Run"): hold `asset` while `signal` closes above its 200-session
  simple moving average, otherwise `risk_off` (T-bills). The signal is read at every close; a switch trades at the next
  session; month-end decisions also put idle cash to work.
* DualMomentumSpec (Antonacci 2014, Global Equities Momentum): at each month-end, if `us` beat `bills` over 252 sessions,
  hold the better of `us` and `intl` over that span; otherwise `bonds`.
Single-asset sizing: EXIT whatever is not the target, BUY floor(cash / price) of the target, and at month-end ADD
floor(cash / price) when idle cash buys at least one share.

RuleSpec (`personal-study-3b`, docs/discovery-v3/PERSONAL_STUDY_3.md) expresses ten more published rules as target
weights: buy-and-hold, volatility targeting, Faber's GTAA-5, Keller's VAA-G4, Accelerating Dual Momentum, a fixed
leveraged stock/bond mix (HFEA), the Halloween season, the turn of the month and Connors' RSI(2). Whole-share orders move
the holdings to the weights at the rule's rebalance closes (month-end or quarter-end); a daily rule also trades when its
target set changes at any close.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date

import numpy as np
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
NAMES = ETF_NAMES | {
    "SPY": "SPDR S&P 500 ETF", "SSO": "ProShares Ultra S&P500 (2x daily)", "UPRO": "ProShares UltraPro S&P500 (3x daily)",
    "EFA": "iShares MSCI EAFE ETF", "AGG": "iShares Core US Aggregate Bond ETF",
    "BIL": "SPDR Bloomberg 1-3 Month T-Bill ETF", "SPYM": "SPDR Portfolio S&P 500 ETF (SPLG until 2025-10-31)",
    "TMF": "Direxion Daily 20+ Year Treasury Bull 3X", "EEM": "iShares MSCI Emerging Markets ETF",
    "LQD": "iShares iBoxx $ Investment Grade Corporate Bond ETF", "IEF": "iShares 7-10 Year Treasury Bond ETF",
    "SHY": "iShares 1-3 Year Treasury Bond ETF", "TLT": "iShares 20+ Year Treasury Bond ETF",
    "VNQ": "Vanguard Real Estate ETF", "DBC": "Invesco DB Commodity Index Tracking Fund",
    "SCZ": "iShares MSCI EAFE Small-Cap ETF", "MTUM": "iShares MSCI USA Momentum Factor ETF",
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

    daily = False  # decided at month-end only (class traits, not fields: personal-study-1's sha covers the fields)

    @property
    def data_symbols(self) -> tuple[str, ...]:
        return self.universe

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TrendSpec:
    """Gayed & Bilello (2016): `asset` while `signal` closes above its `sma`-session average, else `risk_off`."""

    method_id: str
    title: str
    asset: str
    risk_off: str = "BIL"
    signal: str = "SPY"
    sma: int = 200
    cadence: str = "every close (a switch trades at the next session); last session of each month (idle cash)"
    source: str = "Gayed & Bilello (2016), Leverage for the Long Run"

    daily = True
    slots = 1

    @property
    def universe(self) -> tuple[str, ...]:
        return (self.asset, self.risk_off)

    @property
    def data_symbols(self) -> tuple[str, ...]:
        return tuple(sorted({self.signal, self.asset, self.risk_off}))

    def to_dict(self) -> dict:
        return asdict(self) | {"kind": "trend"}


@dataclass(frozen=True)
class DualMomentumSpec:
    """Antonacci (2014), Global Equities Momentum: `us` or `intl` (the better) while `us` beats `bills`, else `bonds`."""

    method_id: str
    title: str
    us: str = "SPY"
    intl: str = "EFA"
    bonds: str = "AGG"
    bills: str = "BIL"
    lookback: int = 252
    cadence: str = "last session of each month"
    source: str = "Antonacci (2014), Dual Momentum Investing"

    daily = False
    slots = 1

    @property
    def universe(self) -> tuple[str, ...]:
        return (self.us, self.intl, self.bonds)

    @property
    def data_symbols(self) -> tuple[str, ...]:
        return tuple(sorted({self.us, self.intl, self.bonds, self.bills}))

    def to_dict(self) -> dict:
        return asdict(self) | {"kind": "dual-momentum"}


@dataclass(frozen=True)
class RuleSpec:
    """A published rule as target weights (personal-study-3). `params` are the publication's own numbers."""

    method_id: str
    title: str
    kind: str  # hold | vol-target | gtaa | vaa | adm | fixed-mix | seasonal | turn-of-month | rsi2
    assets: tuple[str, ...]  # what the rule can hold, in its own order
    cash: str = "BIL"  # T-bills: where a rule's idle sleeve waits
    signal: str = "SPY"  # the series a timing rule reads
    params: tuple[tuple[str, float], ...] = ()
    schedule: str = "monthly"  # rebalance closes: monthly | quarterly; daily rules also act on any close's switch
    daily_switch: bool = False
    source: str = ""

    @property
    def daily(self) -> bool:
        return self.daily_switch

    @property
    def slots(self) -> int:
        return max(1, len(self.assets)) if self.kind in ("gtaa", "fixed-mix") else 1

    @property
    def universe(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.assets, self.cash)))

    @property
    def data_symbols(self) -> tuple[str, ...]:
        return tuple(sorted({*self.universe, self.signal}))

    def p(self, name: str) -> float:
        return dict(self.params)[name]

    def to_dict(self) -> dict:
        return asdict(self) | {"kind": self.kind}


SECTOR_MOM_3 = MethodSpec("sector-mom-3", "Top 3 sector ETFs by 6-month return, monthly, kept while in the top 5")
SECTOR_MOM_3_ABS = MethodSpec("sector-mom-3-abs", "The same, but only sectors with a positive 6-month return; the rest "
                              "in cash", absolute=True)
SPY_TREND = TrendSpec("spy-trend200", "SPY while SPY is above its 200-day average, else T-bills (BIL)", asset="SPY")
SSO_TREND = TrendSpec("sso-trend200", "SSO (2x S&P 500) while SPY is above its 200-day average, else T-bills (BIL)",
                      asset="SSO")
UPRO_TREND = TrendSpec("upro-trend200", "UPRO (3x S&P 500) while SPY is above its 200-day average, else T-bills (BIL)",
                       asset="UPRO")
GEM = DualMomentumSpec("gem", "Dual Momentum: SPY or EFA (the stronger) while SPY beats T-bills over 12 months, else "
                       "bonds (AGG)")
RULES = (
    RuleSpec("sso-hold", "SSO (2x S&P 500) held throughout", "hold", ("SSO",),
             source="the leveraged buy-and-hold baseline of Gayed & Bilello (2016)"),
    RuleSpec("voltarget-sso", "Exposure 15% / last month's SPY volatility (0-2x), held as SSO plus T-bills, monthly",
             "vol-target", ("SSO",), params=(("target_vol", 0.15), ("lookback", 21.0), ("max_exposure", 2.0)),
             source="Harvey, Hoyle, Korgaonkar, Rattray, Sargaison & Van Hemert (2018), The Impact of Volatility "
             "Targeting; after Moreira & Muir (2017), Volatility-Managed Portfolios"),
    RuleSpec("gtaa5", "GTAA-5: US stocks, foreign stocks, bonds, REITs, commodities, a fifth each, each only above its "
             "10-month average (else T-bills), monthly", "gtaa", ("SPYM", "EFA", "IEF", "VNQ", "DBC"),
             params=(("sma_months", 10.0),), source="Faber (2007), A Quantitative Approach to Tactical Asset Allocation"),
    RuleSpec("vaa-g4", "VAA-G4: the best of US, foreign, emerging stocks and bonds while all four have positive 13612W "
             "momentum, else the best of corporate, 7-10y and 1-3y Treasuries, monthly", "vaa",
             ("SPYM", "EFA", "EEM", "AGG", "LQD", "IEF", "SHY"), source="Keller & Keuning (2017), Breadth Momentum and "
             "Vigilant Asset Allocation"),
    RuleSpec("adm", "Accelerating Dual Momentum: S&P 500 or foreign small caps (1+3+6-month return, the stronger) while "
             "positive, else long Treasuries, monthly", "adm", ("SPYM", "SCZ", "TLT"),
             source="EngineeredPortfolio (2018), Accelerating Dual Momentum"),
    RuleSpec("hfea", "55% UPRO (3x S&P 500) and 45% TMF (3x long Treasuries), rebalanced each quarter", "fixed-mix",
             ("UPRO", "TMF"), params=(("UPRO", 0.55), ("TMF", 0.45)), schedule="quarterly",
             source="Hedgefundie's Excellent Adventure, Bogleheads forum (2019)"),
    RuleSpec("halloween-sso", "SSO (2x S&P 500) from November through April, T-bills from May through October",
             "seasonal", ("SSO",), params=(("first_month", 11.0), ("last_month", 4.0)),
             source="Bouman & Jacobsen (2002), The Halloween Indicator"),
    RuleSpec("tom-sso", "SSO (2x S&P 500) over the last session of each month and the first three of the next, "
             "T-bills otherwise", "turn-of-month", ("SSO",), params=(("days_before", 1.0), ("days_after", 3.0)),
             daily_switch=True, source="Lakonishok & Smidt (1988); McConnell & Xu (2008), Equity Returns at the Turn "
             "of the Month"),
    RuleSpec("rsi2-sso", "SSO (2x S&P 500) after SPY's 2-day RSI closes below 10 above its 200-day average, until SPY "
             "closes above its 5-day average; T-bills otherwise", "rsi2", ("SSO",),
             params=(("rsi_days", 2.0), ("entry", 10.0), ("trend_days", 200.0), ("exit_days", 5.0)), daily_switch=True,
             source="Connors & Alvarez (2008), Short Term Trading Strategies That Work"),
    RuleSpec("mtum-hold", "MTUM (US momentum factor) held throughout", "hold", ("MTUM",),
             source="Jegadeesh & Titman (1993) momentum; the iShares MSCI USA Momentum Factor ETF"),
)
Spec = MethodSpec | TrendSpec | DualMomentumSpec | RuleSpec
METHODS: dict[str, Spec] = {m.method_id: m for m in (SECTOR_MOM_3, SECTOR_MOM_3_ABS, SPY_TREND, SSO_TREND,
                                                       UPRO_TREND, GEM, *RULES)}


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
    explain: str = ""  # the signal in words (single-asset methods)
    weights: dict[str, float] = field(default_factory=dict)  # a RuleSpec's target weights


def is_decision_day(cal: MarketCalendar, day: date) -> bool:
    """The last session of a calendar month (the method's rebalance close)."""
    return cal.is_session(day) and cal.next_session(day).month != day.month


def rebalance_day(spec: Spec, cal: MarketCalendar, day: date) -> bool:
    """The method's scheduled rebalance close: the month's last session, or the quarter's for a quarterly rule."""
    if not is_decision_day(cal, day):
        return False
    return not (isinstance(spec, RuleSpec) and spec.schedule == "quarterly") or day.month in (3, 6, 9, 12)


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


def decide(spec: Spec, closes: dict[str, pd.Series], day: date, holdings: dict[str, int], cash: float, *,
           rebalance: bool = True) -> Decision:
    """The method's decision at the close of `day`: signals, selection and whole-share orders sized on the closes.
    `rebalance` (single-asset methods): also put idle cash to work; otherwise only a switch produces orders."""
    if isinstance(spec, TrendSpec | DualMomentumSpec):
        return _decide_single(spec, closes, day, holdings, cash, rebalance)
    if isinstance(spec, RuleSpec):
        return _decide_rule(spec, closes, day, holdings, cash, rebalance)
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


def _upto(closes: dict[str, pd.Series], sym: str, t: pd.Timestamp) -> pd.Series:
    s = closes.get(sym)
    return s[s.index <= t].dropna() if s is not None else pd.Series(dtype=float)


def _target(spec: TrendSpec | DualMomentumSpec, closes: dict[str, pd.Series],
            t: pd.Timestamp) -> tuple[str | None, dict[str, float], str]:
    """(the asset to hold after the close t, the signals, the reason in words); no target without full data at t."""
    if isinstance(spec, TrendSpec):
        s = _upto(closes, spec.signal, t)
        if len(s) < spec.sma or s.index[-1] != t:
            return None, {}, f"not enough {spec.signal} history for its {spec.sma}-day average"
        close, avg = float(s.iloc[-1]), float(s.iloc[-spec.sma:].mean())
        on = close > avg
        target = spec.asset if on else spec.risk_off
        gap = close / avg - 1
        return target, {spec.signal: gap}, (f"{spec.signal} {close:,.2f} is {100 * abs(gap):.1f}% "
                                            f"{'above' if on else 'below'} its {spec.sma}-day average ({avg:,.2f}) → "
                                            f"{target}")
    rets = {}
    for sym in (spec.us, spec.intl, spec.bills):
        s = _upto(closes, sym, t)
        if len(s) <= spec.lookback or s.index[-1] != t or float(s.iloc[-1 - spec.lookback]) <= 0:
            return None, {}, f"not enough {sym} history for its {spec.lookback}-session return"
        rets[sym] = float(s.iloc[-1]) / float(s.iloc[-1 - spec.lookback]) - 1
    if rets[spec.us] > rets[spec.bills]:
        target = spec.us if rets[spec.us] >= rets[spec.intl] else spec.intl
    else:
        target = spec.bonds
    words = ", ".join(f"{k} {100 * v:+.1f}%" for k, v in rets.items())
    return target, rets, f"12-month returns: {words} → {target}"


def _decide_single(spec: TrendSpec | DualMomentumSpec, closes: dict[str, pd.Series], day: date,
                   holdings: dict[str, int], cash: float, rebalance: bool) -> Decision:
    t = pd.Timestamp(day)
    target, sig, explain = _target(spec, closes, t)
    held = {s: q for s, q in holdings.items() if q > 0}
    last = {s: c for s in {*held, *([target] if target else [])} if len(c := _upto(closes, s, t))}
    prices = {s: float(c.iloc[-1]) for s, c in last.items()}
    equity = cash + sum(q * prices.get(s, 0.0) for s, q in held.items())
    if target is None:
        return Decision(day, sig, {}, [], [], equity, cash, [explain], explain)
    stale = sorted(s for s in held if s not in last or last[s].index[-1] != t)
    notes = [f"no {day.isoformat()} close for {', '.join(stale)}: kept as is"] if stale else []
    out: list[Order] = []
    for s in sorted(held):
        if s != target and s not in stale:
            out.append(Order(EXIT, s, held[s], prices[s], f"the signal says {target}"))
            cash += held[s] * prices[s]
    if target not in last or last[target].index[-1] != t:
        return Decision(day, sig, {target: 1}, [target], out, equity, cash, [*notes, f"no {target} close"], explain)
    px = prices[target]
    if held.get(target, 0) == 0 or rebalance:
        n = int(cash // px)
        if n > 0:
            out.append(Order(BUY if held.get(target, 0) == 0 else ADD, target, n, px,
                             "the signal's asset" if held.get(target, 0) == 0 else "idle cash"))
            cash -= n * px
    return Decision(day, sig, {target: 1}, [target], out, equity, cash, notes, explain)


def _ret(closes: dict[str, pd.Series], sym: str, t: pd.Timestamp, sessions: int) -> float | None:
    s = _upto(closes, sym, t)
    if len(s) <= sessions or s.index[-1] != t or float(s.iloc[-1 - sessions]) <= 0:
        return None
    return float(s.iloc[-1]) / float(s.iloc[-1 - sessions]) - 1


def _rsi(s: pd.Series, days: int) -> float | None:
    """Wilder's RSI over `days` sessions (smoothing 1/days) at the series' last close."""
    if len(s) < days + 2:
        return None
    d = s.diff().dropna()
    up = d.clip(lower=0).ewm(alpha=1 / days, adjust=False).mean().iloc[-1]
    down = (-d.clip(upper=0)).ewm(alpha=1 / days, adjust=False).mean().iloc[-1]
    return 100.0 if down == 0 else float(100 - 100 / (1 + up / down))


def _month_end_closes(s: pd.Series) -> pd.Series:
    return s.groupby([s.index.year, s.index.month]).last()


def rule_weights(spec: RuleSpec, closes: dict[str, pd.Series], t: pd.Timestamp,
                 holdings: dict[str, int]) -> tuple[dict[str, float] | None, dict[str, float], str]:
    """(target weights summing to 1, signals, the reason in words) after the close t; None without full data at t."""
    from apex_worker.market.calendar import default_calendar

    k, a, cash = spec.kind, spec.assets, spec.cash
    if k == "hold":
        return {a[0]: 1.0}, {}, f"hold {a[0]}"
    if k == "fixed-mix":
        return {s: spec.p(s) for s in a}, {}, " + ".join(f"{100 * spec.p(s):.0f}% {s}" for s in a)
    if k == "vol-target":
        s = _upto(closes, spec.signal, t)
        n = int(spec.p("lookback"))
        if len(s) <= n or s.index[-1] != t:
            return None, {}, f"not enough {spec.signal} history for its {n}-session volatility"
        vol = float(np.log(s / s.shift(1)).dropna().iloc[-n:].std(ddof=1) * np.sqrt(252))
        e = min(spec.p("max_exposure"), spec.p("target_vol") / vol) if vol > 0 else spec.p("max_exposure")
        w = e / 2  # SSO is 2x
        return {a[0]: w, cash: 1 - w}, {"vol": vol, "exposure": e}, (
            f"{spec.signal}'s last-month volatility {100 * vol:.1f}% → exposure {e:.2f}x: {100 * w:.0f}% {a[0]}, "
            f"{100 * (1 - w):.0f}% {cash}")
    if k == "gtaa":
        n = int(spec.p("sma_months"))
        w: dict[str, float] = {}
        sig, words = {}, []
        for sym in a:
            s = _upto(closes, sym, t)
            if not len(s) or s.index[-1] != t:
                return None, {}, f"no {t.date()} close for {sym}"
            m = _month_end_closes(s)
            if len(m) < n:
                return None, {}, f"not enough {sym} history for its {n}-month average"
            gap = float(s.iloc[-1]) / float(m.iloc[-n:].mean()) - 1
            sig[sym] = gap
            keep = gap > 0
            w[sym if keep else cash] = w.get(sym if keep else cash, 0.0) + 1 / len(a)
            words.append(f"{sym} {'above' if keep else 'below'} ({100 * gap:+.1f}%)")
        return w, sig, "10-month averages: " + ", ".join(words)
    if k == "vaa":
        risky, safe = a[:4], a[4:]
        mom = {}
        for sym in a:
            r = [_ret(closes, sym, t, n) for n in (21, 63, 126, 252)]
            if any(x is None for x in r):
                return None, {}, f"not enough {sym} history for 13612W momentum"
            mom[sym] = 12 * r[0] + 4 * r[1] + 2 * r[2] + r[3]
        pick = max(risky, key=mom.get) if all(mom[x] > 0 for x in risky) else max(safe, key=mom.get)
        return {pick: 1.0}, mom, ("13612W momentum: " + ", ".join(f"{x} {100 * mom[x]:+.0f}" for x in a) +
                                  f" → {pick}")
    if k == "adm":
        score = {}
        for sym in a[:2]:
            r = [_ret(closes, sym, t, n) for n in (21, 63, 126)]
            if any(x is None for x in r):
                return None, {}, f"not enough {sym} history for 1/3/6-month returns"
            score[sym] = sum(r)  # type: ignore[arg-type]
        best = max(a[:2], key=score.get)
        pick = best if score[best] > 0 else a[2]
        return {pick: 1.0}, score, ("1+3+6-month returns: " + ", ".join(f"{x} {100 * v:+.1f}%" for x, v in
                                                                         score.items()) + f" → {pick}")
    cal = default_calendar()
    if k == "seasonal":
        nxt = cal.next_session(t.date())
        first, last = int(spec.p("first_month")), int(spec.p("last_month"))
        on = nxt.month >= first or nxt.month <= last
        pick = a[0] if on else cash
        return {pick: 1.0}, {}, f"{nxt:%B} is {'in' if on else 'outside'} the November-April season → {pick}"
    if k == "turn-of-month":
        nxt = cal.next_session(t.date())
        before, after = int(spec.p("days_before")), int(spec.p("days_after"))
        first_of_month = cal.sessions_between(nxt.replace(day=1), nxt)
        nth = len(first_of_month)  # nxt is the nth session of its month
        later = cal.add_sessions(nxt, before) if before else nxt
        on = nth <= after or later.month != nxt.month
        pick = a[0] if on else cash
        return {pick: 1.0}, {}, (f"the next session ({nxt:%a %m/%d}) is {'inside' if on else 'outside'} the turn "
                                 f"of the month → {pick}")
    if k == "rsi2":
        s = _upto(closes, spec.signal, t)
        n_trend, n_exit = int(spec.p("trend_days")), int(spec.p("exit_days"))
        if len(s) < n_trend or s.index[-1] != t:
            return None, {}, f"not enough {spec.signal} history for its {n_trend}-day average"
        close = float(s.iloc[-1])
        rsi = _rsi(s.iloc[-60:], int(spec.p("rsi_days")))
        trend, exit_avg = float(s.iloc[-n_trend:].mean()), float(s.iloc[-n_exit:].mean())
        holding = holdings.get(a[0], 0) > 0
        if holding:
            stay = close <= exit_avg
            pick = a[0] if stay else cash
            why = f"{spec.signal} {'at or below' if stay else 'above'} its {n_exit}-day average"
        else:
            enter = rsi is not None and rsi < spec.p("entry") and close > trend
            pick = a[0] if enter else cash
            why = f"{spec.signal} RSI(2) {rsi:.0f}, {'above' if close > trend else 'below'} its {n_trend}-day average"
        return {pick: 1.0}, {"rsi2": rsi or 0.0, "trend_gap": close / trend - 1}, f"{why} → {pick}"
    raise ValueError(f"unknown rule kind {k}")


def weight_orders(weights: dict[str, float], holdings: dict[str, int], cash: float,
                  prices: dict[str, float]) -> tuple[list[Order], float]:
    """Whole-share orders that move `holdings` toward `weights` of today's equity: sells first, then buys from the
    largest weight down, as far as cash allows."""
    held = {s: q for s, q in holdings.items() if q > 0}
    equity = cash + sum(q * prices[s] for s, q in held.items())
    target = {s: int(w * equity // prices[s]) for s, w in weights.items() if w > 0}
    out: list[Order] = []
    for s in sorted(held):
        keep = target.get(s, 0)
        if held[s] > keep:
            n = held[s] - keep
            out.append(Order(EXIT if keep == 0 else TRIM, s, n, prices[s],
                             "the rule moved away" if keep == 0 else "back to its weight"))
            cash += n * prices[s]
    for s in sorted(target, key=lambda x: -weights[x]):
        need = target[s] - held.get(s, 0)
        n = min(need, int(cash // prices[s]))
        if n > 0:
            out.append(Order(ADD if held.get(s, 0) else BUY, s, n, prices[s],
                             "up to its weight" if held.get(s, 0) else "the rule's asset"))
            cash -= n * prices[s]
    return out, cash


def _decide_rule(spec: RuleSpec, closes: dict[str, pd.Series], day: date, holdings: dict[str, int], cash: float,
                 rebalance: bool) -> Decision:
    t = pd.Timestamp(day)
    weights, sig, explain = rule_weights(spec, closes, t, holdings)
    held = {s: q for s, q in holdings.items() if q > 0}
    names = {*held, *(weights or {})}
    last = {s: c for s in names if len(c := _upto(closes, s, t))}
    prices = {s: float(c.iloc[-1]) for s, c in last.items()}
    equity = cash + sum(q * prices.get(s, 0.0) for s, q in held.items())
    if weights is None:
        return Decision(day, sig, {}, [], [], equity, cash, [explain], explain)
    target = sorted(s for s, w in weights.items() if w > 0)
    missing = [s for s in names if s not in last or last[s].index[-1] != t]
    if missing:  # never resize on a partial picture: no target, so the decision waits for complete closes
        return Decision(day, sig, {}, [], [], equity, cash, [f"no {day.isoformat()} close for "
                                                              f"{', '.join(sorted(missing))}"], explain)
    live = {s: w for s, w in weights.items() if w > 0}
    if not (rebalance or set(target) != set(held)):
        return Decision(day, sig, {}, target, [], equity, cash, [], explain, live)
    ords, cash_after = weight_orders(weights, held, cash, prices)
    return Decision(day, sig, {}, target, ords, equity, cash_after, [], explain, live)


__all__ = ["ADD", "BUY", "BUYS", "ETF_NAMES", "EXIT", "GEM", "METHODS", "NAMES", "RULES", "SECTOR_MOM_3",
           "SECTOR_MOM_3_ABS", "SELLS", "SPY_TREND", "SSO_TREND", "TRIM", "UPRO_TREND", "Decision",
           "DualMomentumSpec", "MethodSpec", "Order", "RuleSpec", "Spec", "TrendSpec", "decide", "is_decision_day",
           "orders", "rebalance_day", "rule_weights", "select", "signals", "weight_orders"]
