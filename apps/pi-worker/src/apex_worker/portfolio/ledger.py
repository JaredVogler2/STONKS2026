"""Immutable, event-sourced FIFO position ledger for SELF-REPORTED member fills.

The ledger is a pure function of an ordered list of events. Nothing is ever rewritten:
corrections are new events that void or replace an earlier fill, and a correction can be
cancelled by a CANCEL_CORRECTION event. Projections (average cost, realized P&L) are always
re-derivable from the events.

The TypeScript server implements the same rules (apps/web/src/server/domain/ledger.ts) and both
implementations are pinned by the shared vectors in packages/quant/vectors/ledger.json.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext

LEDGER_VERSION = "ledger-fifo-1"
DEC_PRECISION = 34
MONEY_PLACES = Decimal("0.000001")
MAX_PRICE = Decimal("1000000")
MAX_QUANTITY = Decimal("100000000")
MAX_FEES = Decimal("100000")
PRICE_MAX_DP = 4
QTY_MAX_DP = 6
FEES_MAX_DP = 4
FUTURE_SKEW = timedelta(minutes=2)
EARLIEST_FILL = datetime(2000, 1, 1, tzinfo=UTC)


class EventType(enum.StrEnum):
    WATCH = "WATCH"
    UNWATCH = "UNWATCH"
    OPEN = "OPEN"
    ADD = "ADD"
    PARTIAL_EXIT = "PARTIAL_EXIT"
    CLOSE = "CLOSE"
    CORRECT = "CORRECT"
    CANCEL_CORRECTION = "CANCEL_CORRECTION"


FILL_EVENTS = {EventType.OPEN, EventType.ADD, EventType.PARTIAL_EXIT, EventType.CLOSE}
BUY_EVENTS = {EventType.OPEN, EventType.ADD}
SELL_EVENTS = {EventType.PARTIAL_EXIT, EventType.CLOSE}


class PositionState(enum.StrEnum):
    WATCHING = "WATCHING"
    OPEN = "OPEN"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    CLOSED = "CLOSED"


class LedgerError(ValueError):
    """A rejected event. `code` is stable and safe to show to the member."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Fill:
    price: Decimal
    quantity: Decimal
    fees: Decimal
    fill_at: datetime


@dataclass(frozen=True)
class Correction:
    target_event_id: str
    void: bool
    replacement: Fill | None = None


@dataclass(frozen=True)
class LedgerEvent:
    event_id: str
    seq: int
    event_type: EventType
    fill: Fill | None = None
    correction: Correction | None = None


@dataclass
class Lot:
    lot_id: str
    fill_at: datetime
    price: Decimal
    quantity_initial: Decimal
    quantity_remaining: Decimal
    fees: Decimal
    seq: int

    def cost_of(self, qty: Decimal) -> Decimal:
        """Cost basis (price plus pro-rata buy fees) of `qty` shares of this lot."""
        return qty * self.price + self.fees * qty / self.quantity_initial


@dataclass(frozen=True)
class SellAllocation:
    sell_event_id: str
    lot_id: str
    quantity: Decimal
    sell_price: Decimal
    realized: Decimal


@dataclass
class LedgerState:
    state: PositionState
    lots: list[Lot] = field(default_factory=list)
    total_quantity: Decimal = Decimal(0)
    cost_remaining: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)
    fees_total: Decimal = Decimal(0)
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    allocations: list[SellAllocation] = field(default_factory=list)
    effective_fill_count: int = 0
    had_sells: bool = False

    @property
    def avg_cost(self) -> Decimal:
        if self.total_quantity == 0:
            return Decimal(0)
        with localcontext(_ctx()):
            return (self.cost_remaining / self.total_quantity).quantize(MONEY_PLACES)

    def unrealized_pnl(self, price: Decimal) -> Decimal:
        with localcontext(_ctx()):
            return (self.total_quantity * price - self.cost_remaining).quantize(MONEY_PLACES)

    def to_summary(self) -> dict:
        return {
            "state": self.state.value,
            "total_quantity": fmt(self.total_quantity),
            "avg_cost": fmt(self.avg_cost),
            "realized_pnl": fmt(self.realized_pnl.quantize(MONEY_PLACES)),
            "fees_total": fmt(self.fees_total),
            "lots": [
                {
                    "lot_id": lot.lot_id,
                    "fill_price": fmt(lot.price),
                    "quantity_initial": fmt(lot.quantity_initial),
                    "quantity_remaining": fmt(lot.quantity_remaining),
                    "fees": fmt(lot.fees),
                }
                for lot in self.lots
            ],
        }


def _ctx() -> Context:
    return Context(prec=DEC_PRECISION, rounding=ROUND_HALF_EVEN)


def fmt(value: Decimal) -> str:
    """Canonical decimal string: no exponent, no trailing zeros, '0' for zero."""
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _places(value: Decimal) -> int:
    exponent = value.normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def parse_decimal(
    raw: str | int | Decimal, field_name: str, *, max_dp: int, max_value: Decimal, allow_zero: bool = False
) -> Decimal:
    if isinstance(raw, float):
        raise LedgerError("INVALID_NUMBER", f"{field_name} must be an exact decimal string, not a float")
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError) as exc:
        raise LedgerError("INVALID_NUMBER", f"{field_name} is not a number") from exc
    if not value.is_finite():
        raise LedgerError("INVALID_NUMBER", f"{field_name} must be finite")
    if value < 0 or (value == 0 and not allow_zero):
        raise LedgerError("NON_POSITIVE", f"{field_name} must be {'non-negative' if allow_zero else 'positive'}")
    if value > max_value:
        raise LedgerError("TOO_LARGE", f"{field_name} exceeds the safety limit {fmt(max_value)}")
    if _places(value) > max_dp:
        raise LedgerError("TOO_PRECISE", f"{field_name} allows at most {max_dp} decimal places")
    return value


def parse_fill(
    price: str | Decimal, quantity: str | Decimal, fees: str | Decimal, fill_at: datetime, now: datetime
) -> Fill:
    if fill_at.tzinfo is None:
        raise LedgerError("INVALID_TIME", "fill time must be timezone-aware")
    fill_at = fill_at.astimezone(UTC)
    if fill_at > now + FUTURE_SKEW:
        raise LedgerError("FUTURE_FILL", "fill time cannot be in the future")
    if fill_at < EARLIEST_FILL:
        raise LedgerError("INVALID_TIME", "fill time is implausibly old")
    return Fill(
        price=parse_decimal(price, "price", max_dp=PRICE_MAX_DP, max_value=MAX_PRICE),
        quantity=parse_decimal(quantity, "quantity", max_dp=QTY_MAX_DP, max_value=MAX_QUANTITY),
        fees=parse_decimal(fees, "fees", max_dp=FEES_MAX_DP, max_value=MAX_FEES, allow_zero=True),
        fill_at=fill_at,
    )


def _effective_fills(events: list[LedgerEvent]) -> tuple[list[tuple[LedgerEvent, Fill]], set[str]]:
    by_id = {e.event_id: e for e in events}
    cancelled: set[str] = set()
    for e in events:
        if e.event_type is EventType.CANCEL_CORRECTION and e.correction:
            cancelled.add(e.correction.target_event_id)
    replaced: dict[str, Fill | None] = {}
    for e in events:  # seq order: latest live correction wins
        if e.event_type is EventType.CORRECT and e.correction and e.event_id not in cancelled:
            target = by_id[e.correction.target_event_id]
            replaced[target.event_id] = None if e.correction.void else e.correction.replacement
    out = []
    for e in events:
        if e.event_type in FILL_EVENTS:
            fill = replaced.get(e.event_id, e.fill)
            if fill is not None:
                out.append((e, fill))
    out.sort(key=lambda pair: (pair[1].fill_at, pair[0].seq))
    return out, cancelled


def _validate_structure(events: list[LedgerEvent]) -> None:
    """Record-order rules that do not depend on fill arithmetic."""
    seen: dict[str, LedgerEvent] = {}
    seqs = [e.seq for e in events]
    if seqs != sorted(seqs) or len(set(seqs)) != len(seqs):
        raise LedgerError("BAD_SEQUENCE", "events must have strictly increasing sequence numbers")
    if events and events[0].event_type not in {EventType.WATCH, EventType.OPEN}:
        raise LedgerError("NO_POSITION", "a position starts with OPEN or WATCH")
    for e in events:
        if e.event_id in seen:
            raise LedgerError("DUPLICATE_EVENT", "duplicate event id")
        if e.event_type in FILL_EVENTS and e.fill is None:
            raise LedgerError("MISSING_FILL", f"{e.event_type} requires a fill")
        if e.event_type in {EventType.CORRECT, EventType.CANCEL_CORRECTION}:
            if e.correction is None:
                raise LedgerError("MISSING_CORRECTION", "correction details are required")
            target = seen.get(e.correction.target_event_id)
            if target is None:
                raise LedgerError("UNKNOWN_TARGET", "correction target is not an earlier event of this position")
            if e.event_type is EventType.CORRECT:
                if target.event_type not in FILL_EVENTS:
                    raise LedgerError("BAD_TARGET", "only fills (OPEN/ADD/PARTIAL_EXIT/CLOSE) can be corrected")
                if not e.correction.void and e.correction.replacement is None:
                    raise LedgerError("MISSING_REPLACEMENT", "a non-void correction needs a replacement fill")
            elif target.event_type is not EventType.CORRECT:
                raise LedgerError("BAD_TARGET", "CANCEL_CORRECTION must target a CORRECT event")
        seen[e.event_id] = e


def replay(events: list[LedgerEvent]) -> LedgerState:
    """Derive the position projection. Raises LedgerError if any invariant is violated."""
    events = sorted(events, key=lambda e: e.seq)
    _validate_structure(events)
    cancel_targets = [
        e.correction.target_event_id for e in events if e.event_type is EventType.CANCEL_CORRECTION and e.correction
    ]
    if len(cancel_targets) != len(set(cancel_targets)):
        raise LedgerError("ALREADY_CANCELLED", "that correction was already cancelled")
    effective, _cancelled = _effective_fills(events)

    st = LedgerState(state=PositionState.WATCHING)
    with localcontext(_ctx()):
        for ev, fill in effective:
            st.fees_total += fill.fees
            st.effective_fill_count += 1
            if ev.event_type in BUY_EVENTS:
                lot = Lot(ev.event_id, fill.fill_at, fill.price, fill.quantity, fill.quantity, fill.fees, ev.seq)
                st.lots.append(lot)
                st.total_quantity += fill.quantity
                st.cost_remaining += lot.cost_of(fill.quantity)
                if st.opened_at is None:
                    st.opened_at = fill.fill_at
                continue
            # sells consume FIFO
            qty = st.total_quantity if ev.event_type is EventType.CLOSE else fill.quantity
            if qty <= 0:
                raise LedgerError("NOTHING_TO_SELL", "no shares remain at that fill time")
            if qty > st.total_quantity:
                raise LedgerError(
                    "OVERSELL",
                    f"cannot sell {fmt(qty)} shares; only {fmt(st.total_quantity)} remain at that fill time",
                )
            st.had_sells = True
            remaining = qty
            cost = Decimal(0)
            for lot in st.lots:
                if remaining == 0:
                    break
                if lot.quantity_remaining == 0:
                    continue
                take = min(lot.quantity_remaining, remaining)
                lot_cost = lot.cost_of(take)
                cost += lot_cost
                lot.quantity_remaining -= take
                remaining -= take
                st.allocations.append(
                    SellAllocation(
                        ev.event_id, lot.lot_id, take, fill.price, take * fill.price - fill.fees * take / qty - lot_cost
                    )
                )
            st.total_quantity -= qty
            st.cost_remaining -= cost
            st.realized_pnl += qty * fill.price - fill.fees - cost
            if st.total_quantity == 0:
                st.closed_at = fill.fill_at
                st.cost_remaining = Decimal(0)
            else:
                st.closed_at = None

    if st.effective_fill_count == 0 or not st.lots:
        unwatched = any(e.event_type is EventType.UNWATCH for e in events)
        had_fill_events = any(e.event_type in FILL_EVENTS for e in events)
        st.state = PositionState.CLOSED if (unwatched or had_fill_events) else PositionState.WATCHING
    elif st.total_quantity > 0:
        st.state = PositionState.PARTIALLY_CLOSED if st.had_sells else PositionState.OPEN
    else:
        st.state = PositionState.CLOSED
    st.realized_pnl = st.realized_pnl.quantize(MONEY_PLACES)
    st.cost_remaining = st.cost_remaining.quantize(Decimal("1e-12"))
    return st


def apply(existing: list[LedgerEvent], new: LedgerEvent) -> LedgerState:
    """Validate `new` against the record-order state machine and the full replay."""
    current = replay(existing) if existing else None
    state = current.state if current else None
    et = new.event_type
    if et is EventType.WATCH and existing:
        raise LedgerError("ALREADY_EXISTS", "position already exists; WATCH starts a new position only")
    if et is EventType.OPEN and state not in {None, PositionState.WATCHING}:
        raise LedgerError("ALREADY_OPEN", "this position already has an entry; use ADD, or record a new position")
    if et in {EventType.ADD, EventType.PARTIAL_EXIT, EventType.CLOSE} and state not in {
        PositionState.OPEN,
        PositionState.PARTIALLY_CLOSED,
    }:
        raise LedgerError("NOT_OPEN", "position is not open")
    if et is EventType.UNWATCH and state is not PositionState.WATCHING:
        raise LedgerError("NOT_WATCHING", "only a watch-only position can be unwatched")
    if et in {EventType.CORRECT, EventType.CANCEL_CORRECTION} and not existing:
        raise LedgerError("NO_POSITION", "nothing to correct")
    return replay([*existing, new])


__all__ = [
    "LEDGER_VERSION",
    "Correction",
    "EventType",
    "Fill",
    "LedgerError",
    "LedgerEvent",
    "LedgerState",
    "Lot",
    "PositionState",
    "apply",
    "fmt",
    "parse_decimal",
    "parse_fill",
    "replay",
]
