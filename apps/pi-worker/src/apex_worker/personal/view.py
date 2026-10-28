"""What the bot writes in the private channel: account valuation and the message texts (no I/O)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from apex_worker.personal.book import ZERO, Book, Record, spy_dividend_cash
from apex_worker.personal.method import NAMES, SELLS, Order, Spec
from apex_worker.timeutil import ET

CENT = Decimal("0.01")
LABEL = "UNVALIDATED method: its live record is the test"


def usd(x: Decimal | float) -> str:
    x = Decimal(str(x)).quantize(CENT)
    return f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}"


def susd(x: Decimal | float) -> str:
    x = Decimal(str(x)).quantize(CENT)
    return f"-${-x:,.2f}" if x < 0 else f"+${x:,.2f}"


def spct(x: Decimal | float | None) -> str:
    return "—" if x is None else f"{100 * float(x):+.1f}%"


def pfmt(x: Decimal | float) -> str:
    d = Decimal(str(x))
    return f"{d:,.2f}" if d == d.quantize(CENT) else f"{d.normalize():,f}"


def day(d: date) -> str:
    return f"{d:%a} {d.month}/{d.day}"


def when(at: datetime) -> str:
    t = at.astimezone(ET)
    return f"{t:%a} {t.month}/{t.day} {t:%-I:%M %p} ET"


@dataclass(frozen=True)
class Line:
    symbol: str
    shares: int
    avg_cost: Decimal
    price: Decimal | None
    realized: Decimal
    dividends: Decimal

    @property
    def value(self) -> Decimal:
        return self.shares * (self.price if self.price is not None else self.avg_cost)

    @property
    def unrealized(self) -> Decimal:
        return self.value - self.shares * self.avg_cost


@dataclass(frozen=True)
class Valuation:
    as_of: str
    lines: list[Line]
    cash: Decimal
    net_in: Decimal
    realized: Decimal
    dividends: Decimal
    spy_value: Decimal | None
    spy_dividends_included: bool

    @property
    def equity(self) -> Decimal:
        return self.cash + sum((ln.value for ln in self.lines), ZERO)

    @property
    def pnl(self) -> Decimal:
        return self.equity - self.net_in

    @property
    def missing(self) -> list[str]:
        return [ln.symbol for ln in self.lines if ln.price is None]


def valuation(book: Book, prices: dict[str, float], as_of: str, spy_price: float | None,
              spy_dividends: list | None) -> Valuation:
    lines = [Line(s, h.shares, h.avg_cost, Decimal(str(prices[s])) if prices.get(s) else None, h.realized,
                  h.dividends) for s, h in sorted(book.holdings.items()) if h.shares > 0]
    spy = None
    if spy_price and book.flows:
        spy = book.spy_units() * Decimal(str(spy_price)) + spy_dividend_cash(book, spy_dividends)
    return Valuation(as_of, lines, book.cash, book.net_in, book.realized, book.dividends, spy,
                     spy_dividends is not None)


def _ratio(x: Decimal, base: Decimal) -> Decimal | None:
    return x / base if base else None


def account_text(v: Valuation, *, title: str) -> str:
    out = [f"{title} · account **{usd(v.equity)}** (P/L {susd(v.pnl)}, {spct(_ratio(v.pnl, v.net_in))} on "
           f"{usd(v.net_in)} in)"]
    if v.spy_value is not None:
        gap = v.equity - v.spy_value
        out.append(f"SPY with the same cash: {usd(v.spy_value)} ({spct(_ratio(v.spy_value - v.net_in, v.net_in))}) → "
                   f"{'ahead by' if gap >= 0 else 'behind by'} {usd(abs(gap))}")
    for ln in v.lines:
        tail = (f"{usd(ln.value)} · {susd(ln.unrealized)} ({spct(_ratio(ln.unrealized, ln.shares * ln.avg_cost))})"
                if ln.price is not None else "no price yet")
        out.append(f"- **{ln.symbol}** {ln.shares} sh · avg {pfmt(ln.avg_cost.quantize(CENT))} → "
                   f"{pfmt(ln.price.quantize(CENT)) if ln.price is not None else '?'} · {tail}")
    out.append(f"Cash {usd(v.cash)} · realized {susd(v.realized)} · dividends {usd(v.dividends)} · prices: {v.as_of}"
               + ("" if v.spy_dividends_included else " · SPY dividends unavailable"))
    return "\n".join(out)


def order_line(o: dict, spec: Spec, per_slot: Decimal | None = None, ranks: dict | None = None) -> str:
    n = o.get("left", o["n"])
    what = f"BUY {n} {o['s']}" if o["a"] == "BUY" else f"{o['a']}: {'sell' if o['a'] in SELLS else 'buy'} {n} {o['s']}"
    trim = getattr(spec, "trim_above", 1.5)
    why = {"EXIT": "left the selection", "TRIM": f"above {trim:g}x its target", "BUY": "new pick",
           "ADD": "below its target"}[o["a"]]
    if spec.slots == 1:  # single-asset methods: the signal decides, not a rank
        why = {"EXIT": "the signal moved away", "BUY": "the signal's asset", "ADD": "idle cash", "TRIM": why}[o["a"]]
    elif ranks and o["s"] in ranks:
        why += f", rank {ranks[o['s']]}"
    if per_slot is not None and o["a"] in ("ADD", "TRIM"):
        why += f" ({usd(per_slot)} a slot)"
    return f"**{what}** ≈ ${pfmt(o['px'])} · {why}"


def orders_payload(orders: list[Order]) -> list[dict]:
    return [{"a": o.action, "s": o.symbol, "n": o.shares, "px": f"{o.ref_price:.2f}"} for o in orders]


def alert_text(*, spec: Spec, head: str, status: str, decided: date, trade_when: str, orders: list[dict],
               target: list[str], held: set[str], basis: str, equity: float, cash_after: float,
               closes: dict[str, float], ranks: dict[str, int], late: bool, notes: list[str]) -> str:
    out = [f"{head} · decided on the close of {day(decided)} · `{spec.method_id}` ({status})"]
    if late:
        out.append(f"⚠️ Posted late: these orders were sized on the {day(decided)} closes.")
    per_slot = Decimal(str(equity)) / spec.slots
    bought = {o["s"] for o in orders if o["a"] in ("BUY", "ADD")}
    too_dear = [s for s in target if s not in held and s not in bought and s in closes]
    if orders:
        out.append(f"{trade_when}, whole shares (a limit order near the price is fine):")
        out += [f"- {order_line(o, spec, per_slot if spec.slots > 1 else None, ranks)}" for o in orders]
    else:
        out.append("No trades." + (f" Keep {', '.join(s for s in target if s in held)}."
                                   if any(s in held for s in target) else ""))
    out.append(basis)
    out += [f"- {s}: one share (≈ ${pfmt(round(closes[s], 2))}) costs more than this account can put in it now "
            f"(≈ {usd(per_slot)}), so that money stays in cash." for s in too_dear]
    if orders:
        out.append(f"Account at the close: {usd(equity)} · cash after these ≈ {usd(cash_after)}")
        example = orders[0]["s"]
        out.append(f"Reply when done: `{'sold' if orders[0]['a'] in SELLS else 'bought'} {orders[0]['n']} {example} at "
                   f"{pfmt(orders[0]['px'])}` (your fill price) · or `skip {example}`")
    out += [f"Note: {n}" for n in notes]
    return "\n".join(out)


def reminder_text(alert: Record, left: list[dict], spec: Spec, trade_day: date) -> str:
    items = " · ".join(order_line(o, spec) for o in left)
    return (f"⏰ {day(trade_day)}: still to do from the {day(date.fromisoformat(alert.data['day']))} alert: {items}. "
            "Reply `bought …` / `sold …` when done, or `skip XLK`.")


def pending_text(alert: Record, left: list[dict], spec: Spec) -> str:
    return (f"⏳ Not reported yet from the {day(date.fromisoformat(alert.data['day']))} alert: "
            + " · ".join(order_line(o, spec) for o in left))


def help_text(spec: Spec, status: str) -> str:
    names = ", ".join(f"{s} ({NAMES.get(s, s)})" for s in spec.universe) if spec.slots == 1 else \
        ", ".join(sorted(spec.universe))
    when = ("I check every close and alert you when the signal flips, and after the last close of each month I size "
            "any idle cash" if spec.daily else "I check after the last close of each month")
    return "\n".join([
        f"👋 I'm your personal alert bot for `{spec.method_id}`: {spec.title} ({names}).",
        f"{when}, and suggest whole-share trades sized on what you hold; you place them yourself at Ally and tell me "
        "what you did. I never place orders.",
        "**Start:** `start 1000` once the cash is in the account.",
        "**Trades:** `bought 2 XLK at 245.10` · `sold 3 XLE at 90.20` (add `on 10/30 9:45` if it was earlier)",
        "**Cash:** `deposit 500` · `withdraw 200` · `dividend XLU 3.12` · a broker split: `split XLK 2:1`",
        "**Alerts:** `skip XLK` (you won't take it) · `skip all`",
        "**See:** `status` · `plan` (what the method would do today) · `trades` · `undo` (removes your last record)",
        f"Method status: {status}. {LABEL}, compared with SPY bought with the same cash. Not investment advice.",
    ])


def describe(r: Record) -> str:
    d = r.data
    at = f" ({when(r.at)})" if r.at else ""
    match r.kind:
        case "buy" | "sell":
            verb = "bought" if r.kind == "buy" else "sold"
            return f"{verb} {d['n']} {d['sym']} at ${pfmt(d['px'])}{at}"
        case "start" | "deposit" | "withdraw":
            return f"{r.kind} {usd(Decimal(d['amt']))}{at}"
        case "dividend":
            return f"dividend {d['sym']} {usd(Decimal(d['amt']))}{at}"
        case "split":
            return f"split {d['sym']} {d['new']}:{d['old']}{at}"
        case "skip":
            return f"skip {d.get('sym') or 'all'} (alert {d.get('alert')})"
    return r.kind


def trades_text(book: Book, limit: int = 15) -> str:
    recs = book.of("start", "deposit", "withdraw", "buy", "sell", "dividend", "split")
    if not recs:
        return "No records yet. Start with `start 1000`."
    shown = recs[-limit:]
    head = f"🧾 Your records ({len(shown)} of {len(recs)}, oldest first):"
    return "\n".join([head, *(f"- {describe(r)}" for r in shown)])


__all__ = ["LABEL", "Line", "Valuation", "account_text", "alert_text", "day", "describe", "help_text",
           "order_line", "orders_payload", "pending_text", "pfmt", "reminder_text", "spct", "susd", "trades_text",
           "usd", "valuation", "when"]
