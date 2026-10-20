"""LocalSink: the engine's DEMO/offline sink over the SQLite LocalStore (transactional outbox semantics)."""

from __future__ import annotations

import hashlib
from datetime import datetime

from apex_worker.portfolio.ledger import replay
from apex_worker.runtime.engine import MonitoredPosition
from apex_worker.services.positions import ledger_events
from apex_worker.signals.candidates import ScanResult
from apex_worker.signals.lifecycle import SignalState, transition
from apex_worker.signals.policy import PolicyOutcome
from apex_worker.storage.local import LocalStore
from apex_worker.timeutil import parse_utc

OPEN_STATES = {"PUBLISHED", "ACTIVE"}


class LocalSink:
    def __init__(self, store: LocalStore):
        self.store = store

    def publish_signal(self, signal: dict, tier: str, intent: dict, now: datetime) -> bool:
        with self.store.tx() as c:
            fresh = self.store.save_signal_revision(signal, conn=c)
            if not fresh:
                return False
            cur = None
            at = parse_utc(signal["published_at"])
            for target, cause in (
                (SignalState.DISCOVERED, "candidate discovered"),
                (SignalState.EVALUATING, "candidate evaluated against gates"),
                (SignalState.QUALIFIED, f"passed policy as {tier}"),
                (SignalState.PUBLISHED, "published to Discord outbox"),
            ):
                ev = transition(signal["id"], cur, target, at=at, cause=cause, payload={"tier": tier})
                self.store.append_signal_event(ev.to_contract(), conn=c)
                cur = target
            self.store.enqueue(intent, now, conn=c)
            return True

    def append_signal_events(self, signal: dict, events: list[dict], intents_: list[dict], now: datetime) -> int:
        by_key = {i["event_key"].removeprefix("SIGEVT:"): i for i in intents_}
        n = 0
        with self.store.tx() as c:
            for ev in events:
                if self.store.append_signal_event(ev, conn=c):
                    n += 1
                    intent = by_key.get(ev["dedupe_key"])
                    if intent:
                        self.store.enqueue(intent, now, conn=c)
        return n

    def record_scan(self, scan: ScanResult, outcome: PolicyOutcome, now: datetime) -> None:
        sid = "scan_" + hashlib.sha256(f"{scan.session}|{scan.asof_at}".encode()).hexdigest()[:16]
        body = {
            "id": sid,
            "session": scan.session,
            "asof_at": scan.asof_at,
            "stats": scan.stats.to_dict(),
            "regime": scan.regime,
            "no_trade_reason": outcome.no_trade_reason,
            "candidates": [{k: v for k, v in c.items() if k != "_internal"} for c in scan.candidates],
            "decisions": [
                {
                    "candidate_id": d.candidate_id,
                    "symbol": d.symbol,
                    "outcome": d.outcome,
                    "reasons": d.reasons,
                    "signal_id": d.signal["id"] if d.signal else None,
                }
                for d in outcome.decisions
            ],
        }
        self.store.save_scan(sid, scan.session, body, now)

    def open_signals(self, include_closed: bool = False) -> list[tuple[dict, str]]:
        sigs = self.store.list_signals(None if include_closed else OPEN_STATES, limit=500)
        return [(s, s["state"]) for s in sigs]

    def monitored_positions(self) -> list[MonitoredPosition]:
        out = []
        for pos in self.store.all_open_positions():
            rows = self.store.position_event_rows(None, pos["user_id"], pos["id"])
            prefs = self.store.member_prefs(pos["user_id"])
            if prefs["revoked"]:
                continue
            out.append(
                MonitoredPosition(
                    pos["user_id"],
                    prefs["discord_user_id"],
                    pos["id"],
                    pos["symbol"],
                    pos["signal_id"],
                    replay(ledger_events(rows)),
                    self.store.last_assessment(pos["id"], notified_only=True),
                )
            )
        return out

    def save_assessment(self, pos: MonitoredPosition, assessment: dict, intent: dict | None, now: datetime) -> None:
        with self.store.tx() as c:
            self.store.save_assessment(
                pos.position_id,
                assessment["_key"] + ":" + assessment["asof_at"],
                assessment,
                now,
                conn=c,
                notified=intent is not None,
            )
            if intent is not None:
                self.store.enqueue(intent, now, conn=c)

    def enqueue(self, intent: dict, now: datetime) -> bool:
        return self.store.enqueue(intent, now)
