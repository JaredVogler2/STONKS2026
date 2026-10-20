"""Member position application service over the local store (DEMO / offline canonical).

Identity is the authenticated Discord user id passed by the gateway handler; it is NEVER read from user
input. Reads and writes are scoped to that member; another member's position id behaves exactly like a
non-existent one. Ledger rules are validated by portfolio.ledger before anything is persisted.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from apex_worker.contracts import validate
from apex_worker.portfolio.assessment import off_plan_reasons
from apex_worker.portfolio.ledger import (
    Correction,
    EventType,
    Fill,
    LedgerError,
    LedgerEvent,
    LedgerState,
    apply,
    fmt,
    parse_fill,
    replay,
)
from apex_worker.storage.local import LocalStore, OwnershipError
from apex_worker.timeutil import iso_utc, parse_utc

PUBLISHED_STATES = {"PUBLISHED", "ACTIVE", "CLOSED", "EXPIRED", "INVALIDATED"}


class PositionNotFound(LookupError):
    pass


class ServiceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class RecordResult:
    status: str  # persisted | duplicate | queued_pending_sync
    position: dict | None
    event_id: str | None = None
    warnings: list[str] = field(default_factory=list)


def _fill_from(d: dict | None) -> Fill | None:
    if not d:
        return None
    return Fill(Decimal(d["price"]), Decimal(d["quantity"]), Decimal(d.get("fees", "0")), parse_utc(d["fill_at"]))


def ledger_events(rows) -> list[LedgerEvent]:
    out = []
    for r in rows:
        body = json.loads(r["body"])
        corr = None
        if body.get("correction"):
            c = body["correction"]
            corr = Correction(c["target_event_id"], bool(c["void"]), _fill_from(c.get("replacement")))
        out.append(LedgerEvent(r["id"], int(r["seq"]), EventType(r["event_type"]), _fill_from(body.get("fill")), corr))
    return out


def summary(position_row, state: LedgerState, signal: dict | None, event_count: int) -> dict:
    off = off_plan_reasons(state, signal)
    out = {
        "position_id": position_row["id"],
        "symbol": position_row["symbol"],
        "signal_id": position_row["signal_id"],
        "state": state.state.value,
        "total_quantity": fmt(state.total_quantity),
        "avg_cost": fmt(state.avg_cost),
        "realized_pnl": fmt(state.realized_pnl),
        "fees_total": fmt(state.fees_total),
        "cost_method": "FIFO",
        "opened_at": iso_utc(state.opened_at) if state.opened_at else position_row["created_at"],
        "off_plan": bool(off),
        "off_plan_reasons": off,
        "lots": [
            {
                "lot_id": lot.lot_id,
                "fill_at": iso_utc(lot.fill_at),
                "fill_price": fmt(lot.price),
                "quantity_initial": fmt(lot.quantity_initial),
                "quantity_remaining": fmt(lot.quantity_remaining),
                "fees": fmt(lot.fees),
            }
            for lot in state.lots
        ],
        "event_count": event_count,
    }
    if state.closed_at and state.state.value == "CLOSED":
        out["closed_at"] = iso_utc(state.closed_at)
    validate("PositionSummary", out)
    return out


class LocalPositionService:
    def __init__(self, store: LocalStore, *, is_demo: bool = True):
        self.store = store
        self.is_demo = is_demo

    def _signal(self, signal_id: str | None) -> dict | None:
        return self.store.latest_signal(signal_id) if signal_id else None

    def record(self, discord_user_id: str, event: dict, now: datetime) -> RecordResult:
        validate("PositionEvent", event)
        try:
            user_id = self.store.ensure_member(discord_user_id, now)
        except OwnershipError as exc:
            raise ServiceError("MEMBERSHIP_REVOKED", "your APEX access was revoked") from exc
        et = EventType(event["event_type"])
        key = event["client_idempotency_key"]
        with self.store.tx() as c:
            dup = self.store.find_idempotent(c, user_id, key)
            if dup is not None:
                pos = self.store.owned_position(c, user_id, dup["position_id"])
                rows = self.store.position_event_rows(c, user_id, pos["id"])
                st = replay(ledger_events(rows))
                return RecordResult("duplicate", summary(pos, st, self._signal(pos["signal_id"]), len(rows)), dup["id"])
            pid = event.get("position_id")
            warnings: list[str] = []
            if pid is None:
                if et not in (EventType.OPEN, EventType.WATCH):
                    raise ServiceError("POSITION_REQUIRED", "this action needs one of your existing positions")
                symbol = event.get("symbol")
                sig = self._signal(event.get("signal_id"))
                if event.get("signal_id"):
                    if sig is None or sig["state"] not in PUBLISHED_STATES:
                        raise ServiceError("UNKNOWN_SIGNAL", "that signal id is not a published APEX signal")
                    if symbol and symbol != sig["symbol"]:
                        raise ServiceError("SYMBOL_MISMATCH", f"signal is for {sig['symbol']}, not {symbol}")
                    symbol = sig["symbol"]
                if not symbol:
                    raise ServiceError("SYMBOL_REQUIRED", "a symbol is required")
                pid = self.store.create_position(c, user_id, symbol, event.get("signal_id"), now, self.is_demo)
            try:
                pos = self.store.owned_position(c, user_id, pid)
            except OwnershipError as exc:
                raise PositionNotFound("position not found") from exc
            rows = self.store.position_event_rows(c, user_id, pid)
            existing = ledger_events(rows)
            fill = None
            if "fill" in event:
                f = event["fill"]
                fill = parse_fill(f["price"], f["quantity"], f.get("fees", "0"), parse_utc(f["fill_at"]), now)
            corr = None
            if "correction" in event:
                cc = event["correction"]
                rep = cc.get("replacement")
                corr = Correction(
                    cc["target_event_id"],
                    cc["void"],
                    parse_fill(rep["price"], rep["quantity"], rep.get("fees", "0"), parse_utc(rep["fill_at"]), now)
                    if rep
                    else None,
                )
            eid = str(uuid.uuid4())
            if et is EventType.CLOSE and existing and fill is not None:
                cur = replay(existing)
                if fill.quantity != cur.total_quantity:
                    raise LedgerError(
                        "CLOSE_QUANTITY_MISMATCH",
                        f"close sells all {fmt(cur.total_quantity)} shares; use a partial exit for fewer",
                    )
            new = LedgerEvent(eid, (int(rows[-1]["seq"]) + 1) if rows else 1, et, fill, corr)
            st = apply(existing, new)
            body = {"event_id": eid, "event_type": et.value, "source": event["source"]}
            if fill:
                body["fill"] = {
                    "price": fmt(fill.price),
                    "quantity": fmt(fill.quantity),
                    "fees": fmt(fill.fees),
                    "fill_at": iso_utc(fill.fill_at),
                }
            if corr:
                body["correction"] = {"target_event_id": corr.target_event_id, "void": corr.void}
                if corr.replacement:
                    r = corr.replacement
                    body["correction"]["replacement"] = {
                        "price": fmt(r.price),
                        "quantity": fmt(r.quantity),
                        "fees": fmt(r.fees),
                        "fill_at": iso_utc(r.fill_at),
                    }
            if event.get("notes"):
                body["notes"] = event["notes"][:280]
            if event.get("discord_interaction_id"):
                body["discord_interaction_id"] = event["discord_interaction_id"]
            self.store.insert_position_event(
                c, user_id=user_id, position_id=pid, seq=new.seq, event_type=et.value, key=key, body=body, now=now
            )
            self.store.update_position_state(c, pid, st.state.value)
            sig = self._signal(pos["signal_id"])
            summ = summary(pos, st, sig, len(rows) + 1)
            if summ["off_plan"]:
                warnings.append("OFF_PLAN_ENTRY")
            if sig and sig["state"] in ("CLOSED", "EXPIRED", "INVALIDATED") and et in (EventType.OPEN, EventType.ADD):
                warnings.append(f"SIGNAL_ALREADY_{sig['state']}")
            return RecordResult("persisted", summ, eid, warnings)

    def positions(self, discord_user_id: str, include_closed: bool = False) -> list[dict]:
        user_id = self.store.member_id(discord_user_id)
        if not user_id:
            return []
        out = []
        for pos in self.store.list_positions(user_id, include_closed):
            rows = self.store.position_event_rows(None, user_id, pos["id"])
            out.append(summary(pos, replay(ledger_events(rows)), self._signal(pos["signal_id"]), len(rows)))
        return out

    def position(self, discord_user_id: str, position_id: str) -> dict:
        user_id = self.store.member_id(discord_user_id)
        if not user_id:
            raise PositionNotFound("position not found")
        try:
            pos = self.store.owned_position(None, user_id, position_id)
        except OwnershipError as exc:
            raise PositionNotFound("position not found") from exc
        rows = self.store.position_event_rows(None, user_id, position_id)
        return summary(pos, replay(ledger_events(rows)), self._signal(pos["signal_id"]), len(rows))

    def ledger_state(self, user_id: str, position_id: str) -> LedgerState:
        rows = self.store.position_event_rows(None, user_id, position_id)
        return replay(ledger_events(rows))
