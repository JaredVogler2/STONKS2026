"""Signal status machine (append-only).

DISCOVERED -> EVALUATING -> QUALIFIED -> PUBLISHED -> ACTIVE -> CLOSED
rejections: DISCARDED / NO_TRADE; unfilled entry window: EXPIRED; thesis broken: INVALIDATED.

Every transition is a SignalEvent with a deterministic dedupe key, so replaying the same
market evolution twice cannot create duplicate transitions or duplicate notifications.
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from apex_worker.timeutil import iso_utc


class SignalState(enum.StrEnum):
    DISCOVERED = "DISCOVERED"
    EVALUATING = "EVALUATING"
    QUALIFIED = "QUALIFIED"
    PUBLISHED = "PUBLISHED"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    DISCARDED = "DISCARDED"
    NO_TRADE = "NO_TRADE"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


TERMINAL = {SignalState.CLOSED, SignalState.DISCARDED, SignalState.NO_TRADE, SignalState.EXPIRED,
            SignalState.INVALIDATED}

ALLOWED: dict[SignalState | None, set[SignalState]] = {
    None: {SignalState.DISCOVERED},
    SignalState.DISCOVERED: {SignalState.EVALUATING, SignalState.DISCARDED},
    SignalState.EVALUATING: {SignalState.QUALIFIED, SignalState.DISCARDED, SignalState.NO_TRADE},
    SignalState.QUALIFIED: {SignalState.PUBLISHED, SignalState.DISCARDED, SignalState.NO_TRADE, SignalState.EXPIRED},
    SignalState.PUBLISHED: {SignalState.ACTIVE, SignalState.EXPIRED, SignalState.INVALIDATED},
    SignalState.ACTIVE: {SignalState.CLOSED, SignalState.INVALIDATED},
}

# event types that annotate without changing state
ANNOTATIONS = {"WATCH", "UPGRADED", "DOWNGRADED", "TIME_WARNING", "STALE_DATA", "ADMIN_REVIEW"}

EVENT_FOR_TARGET = {
    SignalState.DISCOVERED: "CREATED",
    SignalState.EVALUATING: "CREATED",
    SignalState.QUALIFIED: "QUALIFIED",
    SignalState.PUBLISHED: "PUBLISHED",
    SignalState.ACTIVE: "ACTIVATED",
    SignalState.CLOSED: "CLOSED",
    SignalState.DISCARDED: "DISCARDED",
    SignalState.NO_TRADE: "NO_TRADE",
    SignalState.EXPIRED: "EXPIRED",
    SignalState.INVALIDATED: "INVALIDATED",
}


class TransitionError(ValueError):
    pass


@dataclass(frozen=True)
class SignalEventRecord:
    signal_id: str
    event_type: str
    event_at: datetime
    from_state: SignalState | None
    to_state: SignalState
    cause: str
    dedupe_key: str
    evidence_asof_at: datetime | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_contract(self) -> dict:
        out = {
            "signal_id": self.signal_id,
            "event_type": self.event_type,
            "event_at": iso_utc(self.event_at),
            "dedupe_key": self.dedupe_key,
            "from_state": self.from_state.value if self.from_state else None,
            "to_state": self.to_state.value,
            "cause": self.cause[:240],
            "payload": self.payload,
        }
        if self.evidence_asof_at:
            out["evidence_asof_at"] = iso_utc(self.evidence_asof_at)
        return out


def dedupe_key(signal_id: str, event_type: str, discriminator: str) -> str:
    digest = hashlib.sha256(f"{signal_id}|{event_type}|{discriminator}".encode()).hexdigest()[:24]
    return f"{signal_id}:{event_type}:{digest}"


def transition(signal_id: str, current: SignalState | None, target: SignalState, *, at: datetime, cause: str,
               discriminator: str = "", event_type: str | None = None, evidence_asof_at: datetime | None = None,
               payload: dict | None = None) -> SignalEventRecord:
    if target not in ALLOWED.get(current, set()):
        raise TransitionError(f"illegal signal transition {current} -> {target}")
    et = event_type or EVENT_FOR_TARGET[target]
    return SignalEventRecord(signal_id, et, at, current, target, cause,
                             dedupe_key(signal_id, et, discriminator or target.value), evidence_asof_at, payload or {})


def annotate(signal_id: str, current: SignalState, event_type: str, *, at: datetime, cause: str,
             discriminator: str, evidence_asof_at: datetime | None = None,
             payload: dict | None = None) -> SignalEventRecord:
    if event_type not in ANNOTATIONS:
        raise TransitionError(f"{event_type} is not an annotation event")
    if current in TERMINAL:
        raise TransitionError("terminal signals are immutable")
    return SignalEventRecord(signal_id, event_type, at, current, current, cause,
                             dedupe_key(signal_id, event_type, discriminator), evidence_asof_at, payload or {})


def outcome_transition(signal_id: str, current: SignalState, outcome: str, *, at: datetime, cause: str,
                       evidence_asof_at: datetime | None = None, payload: dict | None = None) -> SignalEventRecord:
    """Map a market outcome to the correct terminal transition.

    outcome: TARGET | STOP | TIME | ENTRY_EXPIRED | THESIS_INVALIDATED
    """
    mapping = {
        "TARGET": (SignalState.CLOSED, "TARGET_REACHED"),
        "STOP": (SignalState.CLOSED, "STOP_HIT"),
        "TIME": (SignalState.CLOSED, "CLOSED"),
        "ENTRY_EXPIRED": (SignalState.EXPIRED, "EXPIRED"),
        "THESIS_INVALIDATED": (SignalState.INVALIDATED, "INVALIDATED"),
    }
    target, et = mapping[outcome]
    data = {"outcome": outcome, **(payload or {})}
    return transition(signal_id, current, target, at=at, cause=cause, discriminator=outcome, event_type=et,
                      evidence_asof_at=evidence_asof_at, payload=data)


def signal_id_for(symbol: str, strategy_id: str, asof_session: str, policy_version: str) -> str:
    """Deterministic, unique signal identity for a (symbol, strategy, decision session, policy)."""
    digest = hashlib.sha256(f"{symbol}|{strategy_id}|{asof_session}|{policy_version}".encode()).hexdigest()
    return "sig_" + digest[:24]
