"""RemoteSink: the engine's connected-mode sink over the signed agent API (same interface as LocalSink).

The cloud Postgres is canonical. Writes go to the agent API with spool keys, so a network outage spools them
locally (FIFO) and replays them without duplicates: the server dedupes by signal id+revision, event dedupe_key,
scan id, assessment key and outbox event_key. Notification intents travel WITH the write that caused them (the
server commits both in one transaction), so the server's notification_outbox stays the single source of delivery.

Reads the engine needs (open signals, monitored member positions) come from the server and are cached in
AgentState so a restart while offline still knows what it is monitoring. Pi-originated SYSTEM notices (model
rejected, registry unreachable, missed training) go through `enqueue` (the worker routes them to /notices).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal

from apex_worker.agent.client import AgentClient, AgentHTTPError, AgentUnavailable, dedupe_key
from apex_worker.portfolio.ledger import LedgerState, Lot, PositionState
from apex_worker.runtime.agent_state import AgentState
from apex_worker.runtime.engine import MonitoredPosition
from apex_worker.signals.candidates import ScanResult, feature_snapshots
from apex_worker.signals.lifecycle import SignalState, transition
from apex_worker.signals.policy import PolicyOutcome
from apex_worker.storage.local import LocalStore
from apex_worker.timeutil import iso_utc, parse_utc

log = logging.getLogger("apex.runtime.remote_sink")

OPEN_STATES = {"PUBLISHED", "ACTIVE"}
SCAN_CANDIDATES_MAX = 12
EVENTS_PER_POST = 50
ASSESSMENTS_PER_POST = 200
OBS_PER_POST = 500
BARS_PER_POST = 2000  # keeps a bar batch well under the 2 MB observations body cap


def ledger_state_from_summary(summary: dict) -> LedgerState:
    """Rebuild the projection the assessor needs from a server PositionSummary (lots are FIFO remainders)."""
    lots = []
    for i, lot in enumerate(summary.get("lots") or []):
        lots.append(Lot(lot["lot_id"], parse_utc(lot["fill_at"]), Decimal(lot["fill_price"]),
                        Decimal(lot["quantity_initial"]), Decimal(lot["quantity_remaining"]), Decimal(lot["fees"]), i + 1))
    open_lots = [lot for lot in lots if lot.quantity_remaining > 0]
    total = sum((lot.quantity_remaining for lot in open_lots), Decimal(0))
    cost = sum((lot.cost_of(lot.quantity_remaining) for lot in open_lots), Decimal(0))
    return LedgerState(
        state=PositionState(summary["state"]),
        lots=open_lots,
        total_quantity=total,
        cost_remaining=cost,
        realized_pnl=Decimal(summary.get("realized_pnl") or "0"),
        fees_total=Decimal(summary.get("fees_total") or "0"),
        opened_at=parse_utc(summary["opened_at"]) if summary.get("opened_at") else None,
        closed_at=parse_utc(summary["closed_at"]) if summary.get("closed_at") else None,
    )


def _chunks(items: list, n: int) -> list[list]:
    return [items[i : i + n] for i in range(0, len(items), n)]


def _body_hash(*parts: object) -> str:
    return hashlib.sha256(repr(parts).encode()).hexdigest()[:32]


class RemoteSink:
    def __init__(
        self,
        client: AgentClient,
        state: AgentState,
        *,
        release_mode: str,
        provider: str,
        feed: str,
        local_enqueue: Callable[[dict, datetime], bool] | None = None,
        mirror: LocalStore | None = None,
    ):
        self.client = client
        self.state = state
        self.release_mode = release_mode
        self.is_demo = release_mode == "DEMO"
        self.provider = provider
        self.feed = feed
        self.local_enqueue = local_enqueue
        self.mirror = mirror  # the bot's SQLite: read-only signal/quote view for slash commands (never an outbox)
        self._open: dict[str, dict] = {s["id"]: s for s in state.get("open_signals", [])}
        self._work_positions: list[dict] = state.get("monitored_positions", [])
        self._assess_buffer: list[dict] = []
        self.scan_started_at: datetime | None = None
        self.missing_user_ids = 0

    # ---- open-signal view --------------------------------------------------------------------------------------
    def _remember(self, signal: dict, state: str) -> None:
        self._open[signal["id"]] = {**signal, "state": state}
        self.state.put("open_signals", list(self._open.values()))

    def _mirror_events(self, signal: dict, events: list[dict]) -> None:
        if self.mirror is None:
            return
        with self.mirror.tx() as c:
            self.mirror.save_signal_revision(signal, conn=c)
            for ev in events:
                self.mirror.append_signal_event(ev, conn=c)

    def _mirror_state(self, signal: dict, state: str) -> None:
        """Align the bot's local view with the server state (local-only marker event, never sent anywhere)."""
        if self.mirror is None:
            return
        self.mirror.save_signal_revision(signal)
        cur = self.mirror.signal_state(signal["id"])
        if cur != state:
            self.mirror.append_signal_event({
                "signal_id": signal["id"], "event_type": "MIRROR_SYNC", "event_at": signal.get("published_at")
                or signal["created_at"], "from_state": cur, "to_state": state,
                "dedupe_key": f"mirror:{signal['id']}:{cur}:{state}"})

    def mirror_quotes(self, observations: list[dict]) -> None:
        if self.mirror is None:
            return
        for o in observations:
            self.mirror.put_quote(o)

    def refresh_open_signals(self) -> bool:
        """Replace the cached view with the server's (skipped while older writes are still spooled)."""
        if self.client.spool is not None and self.client.spool.pending_count():
            return False
        try:
            rows = self.client.get("signals/open").get("signals") or []
        except (AgentUnavailable, AgentHTTPError):
            return False
        self._open = {r["signal"]["id"]: {**r["signal"], "state": r["state"]} for r in rows if r.get("signal")}
        self.state.put("open_signals", list(self._open.values()))
        for sig in self._open.values():
            self._mirror_state({k: v for k, v in sig.items() if k != "tier"}, sig["state"])
        return True

    def open_signals(self, include_closed: bool = False) -> list[tuple[dict, str]]:
        sigs = sorted(self._open.values(), key=lambda s: s.get("created_at", ""), reverse=True)
        return [(s, s["state"]) for s in sigs if include_closed or s["state"] in OPEN_STATES]

    # ---- Sink protocol -------------------------------------------------------------------------------------------
    def publish_signal(self, signal: dict, tier: str, intent: dict, now: datetime) -> bool:
        at = parse_utc(signal["published_at"])
        events, cur = [], None
        for target, cause in (
            (SignalState.DISCOVERED, "candidate discovered"),
            (SignalState.EVALUATING, "candidate evaluated against gates"),
            (SignalState.QUALIFIED, f"passed policy as {tier}"),
            (SignalState.PUBLISHED, "published to Discord outbox"),
        ):
            events.append(transition(signal["id"], cur, target, at=at, cause=cause, payload={"tier": tier}).to_contract())
            cur = target
        body = {"signal": signal, "tier": tier, "events": events, "intent": intent}
        res = self.client.post("signals", body, spool_key=dedupe_key("signal", signal["id"], f"r{signal['revision']}"))
        created = bool((res.data or {}).get("created", True)) if res.sent else res.spooled
        if res.sent or res.spooled:
            self._remember(signal, "PUBLISHED")
            self._mirror_events(signal, events)
        return created

    def append_signal_events(self, signal: dict, events: list[dict], intents_: list[dict], now: datetime) -> int:
        n = 0
        by_key = {i["event_key"].removeprefix("SIGEVT:"): i for i in intents_}
        for batch in _chunks(events, EVENTS_PER_POST):
            intents = [by_key[e["dedupe_key"]] for e in batch if e["dedupe_key"] in by_key]
            key = dedupe_key("sigevt", signal["id"], _body_hash([e["dedupe_key"] for e in batch]))
            try:
                res = self.client.post("signals/events", {"events": batch, "intents": intents}, spool_key=key)
            except AgentHTTPError as exc:
                log.warning("signal events for %s rejected: %s", signal["id"], exc.code)
                continue
            n += int((res.data or {}).get("applied", len(batch))) if res.sent else (len(batch) if res.spooled else 0)
            state = self._open.get(signal["id"], {**signal}).get("state", "PUBLISHED")
            for e in batch:
                if e.get("from_state") != e["to_state"]:
                    state = e["to_state"]
            self._remember(self._open.get(signal["id"], signal), state)
            if res.sent or res.spooled:
                self._mirror_events(signal, batch)
        return n

    def record_scan(self, scan: ScanResult, outcome: PolicyOutcome, now: datetime) -> None:
        sid = "scan_" + hashlib.sha256(f"{scan.session}|{scan.asof_at}".encode()).hexdigest()[:16]
        started = self.scan_started_at or now - timedelta(seconds=1)
        body = {
            "scan": {
                "id": sid, "started_at": iso_utc(started), "finished_at": iso_utc(now), "asof_at": scan.asof_at,
                "universe_size": scan.stats.universe_size, "eligible_count": scan.stats.eligible,
                "scanned_count": scan.stats.scanned, "published_count": len(outcome.signals),
                "no_trade_reason": outcome.no_trade_reason, "is_demo": self.is_demo,
                "metrics": {
                    "vetoes": dict(scan.stats.vetoes), "triggered": scan.stats.triggered, "sectors": scan.sectors,
                    "decisions": [{"symbol": d.symbol, "outcome": d.outcome, "reasons": d.reasons[:4]}
                                  for d in outcome.decisions[:20]],
                },
            },
            "candidates": [{k: v for k, v in c.items() if k != "_internal"} for c in scan.candidates[:SCAN_CANDIDATES_MAX]],
            "regime": {**scan.regime, "version": "regime-rules-1"},
            "feature_snapshots": feature_snapshots(scan, self.provider, self.feed),
        }
        try:
            self.client.post("scan-runs", body, spool_key=dedupe_key("scan", sid))
        except AgentHTTPError as exc:
            log.warning("scan %s rejected by server: %s", sid, exc.code)

    def set_work(self, work: dict) -> None:
        self._work_positions = list(work.get("monitored_positions") or [])
        self.state.put("monitored_positions", self._work_positions)

    def _summaries(self, discord_user_id: str) -> dict[str, dict]:
        cache_key = f"positions:{discord_user_id}"
        try:
            rows = self.client.get("positions", {"discord_user_id": discord_user_id}).get("positions") or []
            self.state.put(cache_key, rows)
        except (AgentUnavailable, AgentHTTPError):
            rows = self.state.get(cache_key, [])  # last known ledger; the assessor still marks quotes stale
        return {r["position_id"]: r for r in rows}

    def monitored_positions(self) -> list[MonitoredPosition]:
        out: list[MonitoredPosition] = []
        by_member: dict[str, dict[str, dict]] = {}
        self.missing_user_ids = 0
        for p in self._work_positions:
            duid = p.get("discord_user_id")
            if not duid:
                continue
            if not p.get("user_id"):
                self.missing_user_ids += 1  # defensive: an older server without user_id cannot route private updates
                continue
            if duid not in by_member:
                by_member[duid] = self._summaries(duid)
            summ = by_member[duid].get(p["position_id"])
            if summ is None:
                continue
            out.append(MonitoredPosition(p["user_id"], duid, p["position_id"], p["symbol"], p.get("signal_id"),
                                         ledger_state_from_summary(summ), self.state.last_assessment(p["position_id"])))
        return out

    def save_assessment(self, pos: MonitoredPosition, assessment: dict, intent: dict | None, now: datetime) -> None:
        item = {
            "discord_user_id": pos.discord_user_id,
            "assessment": {k: v for k, v in assessment.items() if not k.startswith("_")},
            "assessment_key": assessment["_key"] + ":" + assessment["asof_at"],
        }
        if intent is not None:
            item["intent"] = intent
            self.state.set_last_assessment(pos.position_id, item["assessment"])
        self._assess_buffer.append(item)

    def flush_assessments(self) -> int:
        items, self._assess_buffer = self._assess_buffer, []
        sent = 0
        for batch in _chunks(items, ASSESSMENTS_PER_POST):
            key = dedupe_key("assess", _body_hash([(i["assessment"]["position_id"], i["assessment_key"]) for i in batch]))
            try:
                res = self.client.post("positions/assessments", {"items": batch}, spool_key=key)
            except AgentHTTPError as exc:
                log.warning("assessment batch rejected: %s", exc.code)
                continue
            sent += len(batch) if (res.sent or res.spooled) else 0
        return sent

    def enqueue(self, intent: dict, now: datetime) -> bool:
        """Pi-originated SYSTEM_ALERT notices: routed by the worker's NoticeRouter (server /notices, local fallback)."""
        return bool(self.local_enqueue and self.local_enqueue(intent, now))

    # ---- extra writes ----------------------------------------------------------------------------------------------
    def post_observations(self, observations: list[dict], bars: list[dict] | None = None) -> int:
        n = 0
        for batch in _chunks(observations, OBS_PER_POST):
            body = {"observations": batch, "bars": []}
            key = dedupe_key("obs", _body_hash([(o["symbol"], o["provenance"]["ts_received"]) for o in batch]))
            n += self._post_droppable("observations", body, key)
        for batch in _chunks(bars or [], BARS_PER_POST):
            body = {"observations": [], "bars": batch}
            key = dedupe_key("bars", _body_hash([(b["symbol"], b["session_date"]) for b in batch]))
            n += self._post_droppable("observations", body, key)
        return n

    def _post_droppable(self, route: str, body: dict, key: str) -> int:
        try:
            res = self.client.post(route, body, spool_key=key, droppable=True)
        except AgentHTTPError as exc:
            log.warning("%s batch rejected: %s", route, exc.code)
            return 0
        return 1 if (res.sent or res.spooled) else 0

    def post_catalysts(self, items: list[dict]) -> int:
        n = 0
        for batch in _chunks(items, 500):
            key = dedupe_key("cat", _body_hash([c["id"] for c in batch]))
            try:
                res = self.client.post("catalysts", {"items": batch}, spool_key=key)
            except AgentHTTPError as exc:
                log.warning("catalyst batch rejected: %s", exc.code)
                continue
            n += len(batch) if (res.sent or res.spooled) else 0
        return n
