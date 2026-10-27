"""The owner's account, rebuilt from the bot's own records on every run (personal/records.py).

* Money: cash = the start + deposits - withdrawals - buys + sells + dividends, at the prices the owner reported.
  Positions are FIFO lots (portfolio/ledger.py: one ledger episode per holding period of a symbol), so realized P/L
  follows the first-in-first-out lot order a broker uses by default.
* Order: money records apply in the order of their time (`at`; the message order breaks ties). `undo` voids the
  latest record the owner made, so every state the book can return to was validated when it was recorded.
* Benchmark: SPY bought with the same cash flows (the start, deposits, withdrawals) at the SPY price written into each
  flow's record, fractional and frictionless, plus SPY's cash dividends when the market data has them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from apex_worker.personal.method import SELLS
from apex_worker.portfolio.ledger import EventType, Fill, LedgerError, LedgerEvent, replay
from apex_worker.timeutil import ET

ZERO = Decimal(0)
MONEY = ("start", "deposit", "withdraw", "buy", "sell", "dividend", "split")
OWNER_KINDS = (*MONEY, "skip")  # what `undo` can remove


class BookError(ValueError):
    """A record that would make the account inconsistent. `str(err)` is safe to show the owner (never logged)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def parse_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def iso(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Record:
    msg_id: str  # the bot message that carries it (a Discord snowflake)
    data: dict

    @property
    def kind(self) -> str:
        return self.data["k"]

    @property
    def seq(self) -> int:
        return int(self.msg_id)

    @property
    def at(self) -> datetime | None:
        return parse_at(self.data["at"]) if self.data.get("at") else None

    @property
    def origin(self) -> tuple[int, int]:
        """(the owner message it answers, the line in it), else its own message: `undo` and `skip` act on what came
        before them."""
        return int(self.data.get("ref") or self.msg_id), int(self.data.get("line") or 0)


@dataclass
class Holding:
    symbol: str
    shares: int = 0
    cost: Decimal = ZERO  # remaining FIFO cost basis
    realized: Decimal = ZERO  # every holding period of the symbol
    dividends: Decimal = ZERO
    since: datetime | None = None  # the open holding period's first buy

    @property
    def avg_cost(self) -> Decimal:
        return self.cost / self.shares if self.shares else ZERO


@dataclass(frozen=True)
class Flow:
    at: datetime
    amount: Decimal  # positive in, negative out
    spy: Decimal  # the SPY price the benchmark traded at

    @property
    def units(self) -> Decimal:
        return self.amount / self.spy


@dataclass
class Book:
    records: list[Record] = field(default_factory=list)  # live records (voided ones dropped), message order
    voided: set[str] = field(default_factory=set)
    started_at: datetime | None = None
    cash: Decimal = ZERO
    deposits: Decimal = ZERO  # the start included
    withdrawals: Decimal = ZERO
    dividends: Decimal = ZERO
    holdings: dict[str, Holding] = field(default_factory=dict)
    flows: list[Flow] = field(default_factory=list)

    @property
    def net_in(self) -> Decimal:
        return self.deposits - self.withdrawals

    @property
    def realized(self) -> Decimal:
        return sum((h.realized for h in self.holdings.values()), ZERO)

    def held(self) -> dict[str, int]:
        return {s: h.shares for s, h in sorted(self.holdings.items()) if h.shares > 0}

    def of(self, *kinds: str) -> list[Record]:
        return [r for r in self.records if r.kind in kinds]

    def latest(self, kind: str) -> Record | None:
        found = self.of(kind)
        return found[-1] if found else None

    def has(self, kind: str, day: str) -> bool:
        return any(r.data.get("day") == day for r in self.of(kind))

    def last_undoable(self, before: tuple[int, int]) -> Record | None:
        """The owner's latest record made before (message, line) `before`: a re-read `undo` finds the same one."""
        found = [r for r in self.of(*OWNER_KINDS) if r.origin < before]
        return max(found, key=lambda r: r.origin) if found else None

    def spy_units(self) -> Decimal:
        return sum((f.units for f in self.flows), ZERO)


def shell(records: list[Record]) -> Book:
    """The live records only, without the money (enough for `undo` when the records do not add up)."""
    recs = sorted(records, key=lambda r: r.seq)
    voided = {str(r.data.get("target")) for r in recs if r.kind == "void"}
    return Book([r for r in recs if r.kind != "void" and r.msg_id not in voided], voided)


def build(records: list[Record]) -> Book:
    """Rebuild the account. Raises BookError when the records are inconsistent (the runner validates every new record
    this way before writing it)."""
    book = shell(records)
    starts = book.of("start")
    if len(starts) > 1:
        raise BookError("STARTED", "the account has already started; record more cash with `deposit`")
    money = sorted((r for r in book.records if r.kind in MONEY), key=lambda r: (r.at, r.seq))
    if not money:
        return book
    if money[0].kind != "start":
        raise BookError("NOT_STARTED", "start the account first (`start 1000`), and date nothing before the start")
    open_eps: dict[str, list[LedgerEvent]] = {}
    seq = 0
    for r in money:
        d, at = r.data, r.at
        assert at is not None
        if r.kind in ("start", "deposit", "withdraw"):
            amt = Decimal(d["amt"])
            sign = -1 if r.kind == "withdraw" else 1
            book.cash += sign * amt
            if sign > 0:
                book.deposits += amt
            else:
                book.withdrawals += amt
            book.flows.append(Flow(at, sign * amt, Decimal(d["spy"])))
            if r.kind == "start":
                book.started_at = at
            continue
        sym = d["sym"]
        h = book.holdings.setdefault(sym, Holding(sym))
        ep = open_eps.get(sym)
        if r.kind == "dividend":
            amt = Decimal(d["amt"])
            book.cash += amt
            book.dividends += amt
            h.dividends += amt
            continue
        if r.kind == "split":
            if not ep:
                raise BookError("NOT_HELD", f"no {sym} shares are recorded at that time")
            open_eps[sym] = _split(ep, int(d["new"]), int(d["old"]), sym)
            continue
        n, px = int(d["n"]), Decimal(d["px"])
        seq += 1
        if r.kind == "buy":
            ev = EventType.ADD if ep else EventType.OPEN
            open_eps.setdefault(sym, []).append(LedgerEvent(r.msg_id, seq, ev, Fill(px, Decimal(n), ZERO, at)))
            book.cash -= n * px
            continue
        held = replay(ep).total_quantity if ep else ZERO
        if n > held:
            raise BookError("OVERSELL", f"only {held:f} {sym} shares are recorded at that time" if held else
                            f"no {sym} shares are recorded at that time")
        ev = EventType.CLOSE if n == held else EventType.PARTIAL_EXIT
        ep.append(LedgerEvent(r.msg_id, seq, ev, Fill(px, Decimal(n), ZERO, at)))  # type: ignore[union-attr]
        book.cash += n * px
        if ev is EventType.CLOSE:
            h.realized += _replay(ep).realized_pnl  # type: ignore[arg-type]
            del open_eps[sym]
    for sym, ep in open_eps.items():
        st = _replay(ep)
        h = book.holdings[sym]
        h.shares, h.cost, h.since = int(st.total_quantity), st.cost_remaining, st.opened_at
        h.realized += st.realized_pnl
    return book


def _replay(ep: list[LedgerEvent]):
    try:
        return replay(ep)
    except LedgerError as exc:  # cannot happen for records built above; surfaced, never silently skipped
        raise BookError(exc.code, "the recorded trades do not add up; `undo` the last one") from None


def _split(ep: list[LedgerEvent], new: int, old: int, sym: str) -> list[LedgerEvent]:
    out = []
    for e in ep:
        f = e.fill
        assert f is not None
        q = f.quantity * new / old
        if q != q.to_integral_value():
            raise BookError("SPLIT_FRACTION", f"a {new}:{old} split leaves a fraction of a {sym} share: record the "
                                              "broker's cash-in-lieu sale first")
        out.append(LedgerEvent(e.event_id, e.seq, e.event_type, Fill(f.price * old / new, q, f.fees, f.fill_at)))
    return out


def pending(book: Book, alert: Record, decided_close: datetime) -> list[dict]:
    """The alert's orders not yet reported in full nor skipped, each with `left` shares: buys and sells recorded at
    or after the decision close count toward the order of the same side and symbol."""
    day = alert.data["day"]
    skips = {r.data.get("sym") for r in book.of("skip") if r.data.get("alert") == day}
    done: dict[tuple[str, str], int] = {}
    for r in book.of("buy", "sell"):
        if r.at and r.at >= decided_close:
            key = (r.kind, r.data["sym"])
            done[key] = done.get(key, 0) + int(r.data["n"])
    out = []
    for o in alert.data.get("orders") or []:
        if None in skips or o["s"] in skips:
            continue
        key = ("sell" if o["a"] in SELLS else "buy", o["s"])
        got = min(done.get(key, 0), int(o["n"]))
        done[key] = done.get(key, 0) - got
        if int(o["n"]) > got:
            out.append(o | {"left": int(o["n"]) - got})
    return out


def spy_dividend_cash(book: Book, dividends: list[tuple[date, Decimal]] | None) -> Decimal:
    """Cash the benchmark's SPY units earned: each ex-date pays the rate on the units bought before that date."""
    cash = ZERO
    for ex, rate in dividends or []:
        units = sum((f.units for f in book.flows if f.at.astimezone(ET).date() < ex), ZERO)
        cash += units * rate
    return cash


__all__ = ["MONEY", "OWNER_KINDS", "Book", "BookError", "Flow", "Holding", "Record", "build", "iso", "parse_at",
           "pending", "shell", "spy_dividend_cash"]
