"""Outbox bridge (connected mode): server notification_outbox -> bot's local delivery queue -> acks.

The server's `notification_outbox` is authoritative. The worker leases due rows from GET /work and inserts each
into the bot's LOCAL `notification_outbox` (same SQLite file the bot polls) under the SAME event_key, together with
a mapping row in `agent_outbox_bridge`, in ONE local transaction. The bot delivers exactly as in offline mode; the
bridge then reads the local row state and acks the server:
    local sent        -> ack delivered (with the Discord message id)
    local deferred    -> ack dm_blocked (held privately; later shown ephemerally -> local sent -> ack delivered)
    local dead_letter -> ack failed (re-acked on every re-lease so the server's attempt count reaches dead_letter)
Delivered CHANNEL acks carry `rendered` (the message as the bot renders it) for the dashboard; DM acks never do.
The server answers per item, so one unknown or foreign-leased id never blocks the rest of the batch.
Idempotent across restarts: the local event_key is UNIQUE (a re-leased row is never inserted twice), the mapping is
durable, and the server treats a duplicate `delivered` ack as a no-op.

Privacy: DM rows stay DMs. The server routes them by its member UUID; the bridge rewrites `route.user_id` to the
LOCAL member id of the same Discord user (created on first sight; DM opt-in seeded from the server's flag only for
a newly created local member) and never changes the route type. A DM row whose member the server reports as
disabled is acked dm_blocked without being queued.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime

from apex_worker.agent.client import AgentClient, AgentHTTPError, AgentUnavailable
from apex_worker.contracts import ContractViolation, validate
from apex_worker.discord_bot.limits import clamp_message
from apex_worker.notifications.render import render
from apex_worker.storage.local import LocalStore, OwnershipError
from apex_worker.timeutil import iso_utc

log = logging.getLogger("apex.runtime.bridge")

BRIDGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_outbox_bridge (
    event_key TEXT PRIMARY KEY,
    server_outbox_id TEXT NOT NULL,
    local_outbox_id TEXT,
    route_type TEXT NOT NULL,
    acked_state TEXT,
    error TEXT,
    leased_at TEXT NOT NULL,
    acked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_bridge_unacked ON agent_outbox_bridge(acked_state);
"""
ACK_BATCH = 100
RENDERED_MAX = 12_000  # server cap for the stored channel render (JSON chars)
# local state -> status acked to the server (None: still in flight locally)
DESIRED = {"sent": "delivered", "deferred": "dm_blocked", "dead_letter": "failed", "suppressed": "closed", "pending": None}


class OutboxBridge:
    def __init__(self, client: AgentClient, store: LocalStore, *, component_secret: bytes | None = None):
        self.client = client
        self.store = store
        self.component_secret = component_secret  # same secret the bot signs buttons with (<state_dir>/component_secret)
        with store.tx() as c:
            for stmt in BRIDGE_SCHEMA.split(";"):
                if stmt.strip():
                    c.execute(stmt)
        self.last_ingest_at: str | None = None
        self.last_ack_at: str | None = None
        self.last_error: str | None = None

    # ---- server -> local -------------------------------------------------------------------------------------
    def _local_dm(self, intent: dict, item: dict, now: datetime) -> dict | None:
        duid = item.get("discord_user_id")
        if not duid:
            return None  # member disabled server-side: never delivered, held privately on the server
        try:
            existing = self.store.member_id(duid)
            uid = existing or self.store.ensure_member(duid, now)
        except OwnershipError:
            return None  # locally revoked member
        if existing is None and item.get("dm_opt_in"):
            self.store.set_dm_opt_in(uid, True)
        local = {**intent, "route": {"type": "DM", "user_id": uid, "discord_user_id": str(duid)}}
        validate("NotificationIntent", local)
        return local

    def ingest(self, items: list[dict], now: datetime) -> dict[str, int]:
        counts = {"inserted": 0, "known": 0, "held": 0, "invalid": 0}
        for item in items:
            intent = item.get("intent") or {}
            key, oid = intent.get("event_key"), item.get("outbox_id")
            if not key or not oid:
                counts["invalid"] += 1
                continue
            known = self.store.query("SELECT acked_state FROM agent_outbox_bridge WHERE event_key=?", (key,))
            if known:
                counts["known"] += 1
                with self.store.tx() as c:  # re-leased: refresh the server id; re-ack terminal failures
                    c.execute("UPDATE agent_outbox_bridge SET server_outbox_id=?, acked_state=CASE WHEN acked_state="
                              "'failed' THEN NULL ELSE acked_state END WHERE event_key=?", (oid, key))
                continue
            try:
                local = self._local_dm(intent, item, now) if (intent.get("route") or {}).get("type") == "DM" else intent
                if local is not None:
                    validate("NotificationIntent", local)
            except ContractViolation:
                log.warning("leased outbox item has an invalid intent; acking failed")
                counts["invalid"] += 1
                self._map(key, oid, None, "INVALID", now, error="INVALID_INTENT")
                continue
            if local is None:
                counts["held"] += 1
                self._map(key, oid, None, "DM", now)
                continue
            with self.store.tx() as c:
                self.store.enqueue(local, now, conn=c)  # INSERT OR IGNORE on the unique event_key
                row = c.execute("SELECT id FROM notification_outbox WHERE event_key=?", (key,)).fetchone()
                self._map(key, oid, row["id"], local["route"]["type"], now, conn=c)
            counts["inserted"] += 1
        self.last_ingest_at = iso_utc(now)
        return counts

    def _map(self, key: str, oid: str, local_id: str | None, route_type: str, now: datetime, *,
             error: str | None = None, conn: sqlite3.Connection | None = None) -> None:
        sql = ("INSERT OR IGNORE INTO agent_outbox_bridge(event_key, server_outbox_id, local_outbox_id, route_type, error, "
               "leased_at) VALUES (?,?,?,?,?,?)")
        args = (key, oid, local_id, route_type, error, iso_utc(now))
        if conn is not None:
            conn.execute(sql, args)
            return
        with self.store.tx() as c:
            c.execute(sql, args)

    # ---- local -> server acks --------------------------------------------------------------------------------
    def _rendered(self, payload: str | None) -> dict | None:
        """The channel message exactly as the bot renders it (minus opt-in member mentions), for the dashboard's
        Alerts page. Only for CHANNEL rows; omitted when over the server's 12 KB cap or when rendering fails."""
        if not self.component_secret or not payload:
            return None
        try:
            msg = clamp_message(render(json.loads(payload), self.component_secret).to_discord())
        except Exception:
            return None
        return msg if len(json.dumps(msg, separators=(",", ":"))) <= RENDERED_MAX else None

    def _pending_acks(self) -> list[tuple[str, str, dict]]:
        rows = self.store.query(
            "SELECT b.event_key, b.server_outbox_id, b.local_outbox_id, b.acked_state, b.error, b.route_type, "
            "o.state AS local_state, o.discord_message_id, o.last_error, o.payload FROM agent_outbox_bridge b "
            "LEFT JOIN notification_outbox o ON o.id = b.local_outbox_id "
            "WHERE b.acked_state IS NULL OR b.acked_state = 'dm_blocked'")
        out = []
        for r in rows:
            if r["error"] == "INVALID_INTENT":
                desired = "failed"
            elif r["local_outbox_id"] is None:
                desired = "dm_blocked"
            else:
                desired = DESIRED.get(r["local_state"] or "suppressed")
            if desired is None or desired == r["acked_state"]:
                continue
            ack: dict = {"outbox_id": r["server_outbox_id"], "status": desired}
            if desired == "delivered" and r["discord_message_id"]:
                ack["discord_message_id"] = str(r["discord_message_id"])[:40]
            if desired == "delivered" and r["route_type"] == "CHANNEL":  # never for DMs
                rendered = self._rendered(r["payload"])
                if rendered is not None:
                    ack["rendered"] = rendered
            if desired == "failed":
                ack["error_code"] = (r["error"] or r["last_error"] or "DEAD_LETTER")[:48]
            out.append((r["event_key"], desired, ack))
        return out

    def _mark(self, key: str, state: str, now: datetime) -> None:
        with self.store.tx() as c:
            c.execute("UPDATE agent_outbox_bridge SET acked_state=?, acked_at=? WHERE event_key=?", (state, iso_utc(now), key))

    def ack_pass(self, now: datetime) -> dict[str, int]:
        """Batched acks; the server answers per item ({state} or {error: {code, status}})."""
        counts = {"acked": 0, "closed": 0, "deferred_to_next": 0}
        pending = self._pending_acks()
        for key, _desired, _ack in [p for p in pending if p[1] == "closed"]:
            self._mark(key, "closed", now)  # member deleted their data: server already suppressed its copy
            counts["closed"] += 1
        sendable = [p for p in pending if p[1] != "closed"]
        for i in range(0, len(sendable), ACK_BATCH):
            batch = sendable[i : i + ACK_BATCH]
            try:
                res = self.client.post("notifications/ack", {"acks": [a for _k, _d, a in batch]}).data or {}
            except (AgentUnavailable, AgentHTTPError) as exc:  # transport / whole-request failure: retry next pass
                self.last_error = getattr(exc, "code", None) or str(exc)
                counts["deferred_to_next"] += len(sendable) - i
                break
            by_id = {str(r.get("outbox_id")): r for r in res.get("results") or [] if isinstance(r, dict)}
            for key, desired, ack in batch:
                self._apply_result(key, desired, by_id.get(str(ack["outbox_id"])), now, counts)
        self.last_ack_at = iso_utc(now)
        return counts

    def _apply_result(self, key: str, desired: str, result: dict | None, now: datetime, counts: dict) -> None:
        if result is None:
            counts["deferred_to_next"] += 1  # not answered (should not happen): retried next pass
            return
        err = result.get("error")
        if isinstance(err, dict):
            if int(err.get("status") or 0) in (404, 409, 422):  # unknown / leased elsewhere / malformed: stop retrying
                self._mark(key, "closed", now)
                counts["closed"] += 1
            else:
                counts["deferred_to_next"] += 1
            return
        self._mark(key, desired, now)
        counts["acked"] += 1

    def unacked(self) -> int:
        return int(self.store.query(
            "SELECT COUNT(*) AS n FROM agent_outbox_bridge WHERE acked_state IS NULL")[0]["n"])
