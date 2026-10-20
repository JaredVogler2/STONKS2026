"""Backend ports used by the bot service, plus the local (SQLite) implementations.

Layering: DiscordCommands (gateway.py) -> BotService (service.py) -> these ports. Nothing above this module runs
SQL. `PositionBackend` mirrors `LocalPositionService` (record / positions / position) so a remote implementation
backed by the signed agent API can be swapped in later; it MUST raise `BackendUnavailable` (a ConnectionError)
when the canonical store cannot be reached, so the bot can answer "queued - pending sync" and never claim
success it does not have.

Bot-owned tables live in the same local SQLite file with a `discord_` prefix (created idempotently here):
`discord_member_prefs` (high-priority mention opt-in) and `discord_symbol_mutes` (Mute Symbol button).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Protocol, runtime_checkable

from apex_worker.contracts import ContractViolation
from apex_worker.portfolio.ledger import LedgerError
from apex_worker.services.positions import LocalPositionService, PositionNotFound, RecordResult, ServiceError
from apex_worker.storage.local import LocalStore, OutboxRow, OwnershipError
from apex_worker.timeutil import iso_utc

SPOOL_KIND = "discord_position_event"

BOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS discord_member_prefs (
    user_id TEXT PRIMARY KEY REFERENCES app_users(id),
    high_priority_mentions INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS discord_symbol_mutes (
    user_id TEXT NOT NULL REFERENCES app_users(id),
    symbol TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, symbol)
);
"""


class BackendUnavailable(ConnectionError):
    """The canonical position store could not be reached; nothing was persisted there."""


class MemberRevoked(PermissionError):
    pass


@runtime_checkable
class PositionBackend(Protocol):
    def record(self, discord_user_id: str, event: dict, now: datetime) -> RecordResult: ...

    def positions(self, discord_user_id: str, include_closed: bool = False) -> list[dict]: ...

    def position(self, discord_user_id: str, position_id: str) -> dict: ...


@runtime_checkable
class PositionHistory(Protocol):
    """Optional owner-scoped event history (needed for /apex_correct, /apex_remove and starter refs)."""

    def events(self, discord_user_id: str, position_id: str) -> list[dict]: ...

    def position_for_key(self, discord_user_id: str, idempotency_key: str) -> str | None: ...


class SignalSource(Protocol):
    def latest_signal(self, signal_id: str) -> dict | None: ...

    def list_signals(self, states: set[str] | None = None, limit: int = 50) -> list[dict]: ...

    def get_quote(self, symbol: str) -> dict | None: ...


class Spool(Protocol):
    def spool_put(self, kind: str, key: str, payload: dict, now: datetime) -> bool: ...


class MemberBackend(Protocol):
    def status(self, discord_user_id: str) -> str: ...  # active | revoked | unknown

    def ensure(self, discord_user_id: str, now: datetime) -> str: ...

    def user_id(self, discord_user_id: str) -> str | None: ...

    def revoke(self, discord_user_id: str, now: datetime) -> None: ...

    def restore(self, discord_user_id: str) -> bool: ...

    def prefs(self, user_id: str) -> dict: ...

    def set_dm_opt_in(self, user_id: str, opt_in: bool) -> None: ...

    def set_high_priority_mentions(self, user_id: str, opt_in: bool, now: datetime) -> None: ...

    def mute(self, user_id: str, symbol: str, now: datetime) -> bool: ...

    def unmute(self, user_id: str, symbol: str) -> bool: ...

    def mention_targets(self, symbol: str | None) -> list[str]: ...

    def pending_private(self, user_id: str) -> list[OutboxRow]: ...

    def release_private(self, user_id: str, now: datetime) -> list[OutboxRow]: ...

    def delete_data(self, user_id: str) -> int: ...

    def sync_status(self, discord_user_id: str) -> dict: ...

    def counts(self) -> dict: ...


# ---------------------------------------------------------------------------------------------------------------
class LocalPositionBackend(LocalPositionService):
    """LocalPositionService plus owner-scoped history. SQLite lock/IO errors surface as BackendUnavailable."""

    def record(self, discord_user_id: str, event: dict, now: datetime) -> RecordResult:
        try:
            return super().record(discord_user_id, event, now)
        except sqlite3.OperationalError as exc:  # locked / disk I/O: nothing was committed (transaction rolled back)
            raise BackendUnavailable(f"local store unavailable: {type(exc).__name__}") from exc

    def events(self, discord_user_id: str, position_id: str) -> list[dict]:
        uid = self.store.member_id(discord_user_id)
        if not uid:
            raise PositionNotFound("position not found")
        try:
            self.store.owned_position(None, uid, position_id)
        except OwnershipError as exc:
            raise PositionNotFound("position not found") from exc
        out = []
        for r in self.store.position_event_rows(None, uid, position_id):
            body = json.loads(r["body"])
            ev = {"event_id": r["id"], "seq": int(r["seq"]), "event_type": r["event_type"], "recorded_at": r["recorded_at"],
                  "source": body.get("source")}
            for k in ("fill", "correction", "notes"):
                if body.get(k):
                    ev[k] = body[k]
            out.append(ev)
        return out

    def position_for_key(self, discord_user_id: str, idempotency_key: str) -> str | None:
        uid = self.store.member_id(discord_user_id)
        if not uid:
            return None
        with self.store.tx() as c:
            row = self.store.find_idempotent(c, uid, idempotency_key)
        return str(row["position_id"]) if row is not None else None


class LocalMemberBackend:
    """Member directory, preferences, mutes and private-notification hold over the LocalStore."""

    def __init__(self, store: LocalStore):
        self.store = store
        with store.tx() as c:
            for stmt in BOT_SCHEMA.split(";"):
                if stmt.strip():
                    c.execute(stmt)

    # identity --------------------------------------------------------------------------------------------------
    def status(self, discord_user_id: str) -> str:
        rows = self.store.query("SELECT revoked_at FROM app_users WHERE discord_user_id=?", (discord_user_id,))
        if not rows:
            return "unknown"
        return "revoked" if rows[0]["revoked_at"] else "active"

    def ensure(self, discord_user_id: str, now: datetime) -> str:
        try:
            return self.store.ensure_member(discord_user_id, now)
        except OwnershipError as exc:
            raise MemberRevoked("membership revoked") from exc

    def user_id(self, discord_user_id: str) -> str | None:
        return self.store.member_id(discord_user_id)

    def revoke(self, discord_user_id: str, now: datetime) -> None:
        self.store.revoke_member(discord_user_id, now)

    def restore(self, discord_user_id: str) -> bool:
        with self.store.tx() as c:
            cur = c.execute("UPDATE app_users SET revoked_at=NULL WHERE discord_user_id=? AND revoked_at IS NOT NULL",
                            (discord_user_id,))
            return cur.rowcount == 1

    # preferences -----------------------------------------------------------------------------------------------
    def prefs(self, user_id: str) -> dict:
        base = self.store.member_prefs(user_id)
        row = self.store.query("SELECT high_priority_mentions FROM discord_member_prefs WHERE user_id=?", (user_id,))
        base["high_priority_mentions"] = bool(row and row[0]["high_priority_mentions"])
        base["muted_symbols"] = [r["symbol"] for r in self.store.query(
            "SELECT symbol FROM discord_symbol_mutes WHERE user_id=? ORDER BY symbol", (user_id,))]
        return base

    def set_dm_opt_in(self, user_id: str, opt_in: bool) -> None:
        self.store.set_dm_opt_in(user_id, opt_in)

    def set_high_priority_mentions(self, user_id: str, opt_in: bool, now: datetime) -> None:
        with self.store.tx() as c:
            c.execute("INSERT INTO discord_member_prefs(user_id, high_priority_mentions, updated_at) VALUES (?,?,?) "
                      "ON CONFLICT(user_id) DO UPDATE SET high_priority_mentions=excluded.high_priority_mentions, "
                      "updated_at=excluded.updated_at", (user_id, 1 if opt_in else 0, iso_utc(now)))

    def mute(self, user_id: str, symbol: str, now: datetime) -> bool:
        with self.store.tx() as c:
            cur = c.execute("INSERT OR IGNORE INTO discord_symbol_mutes(user_id, symbol, created_at) VALUES (?,?,?)",
                            (user_id, symbol, iso_utc(now)))
            return cur.rowcount == 1

    def unmute(self, user_id: str, symbol: str) -> bool:
        with self.store.tx() as c:
            cur = c.execute("DELETE FROM discord_symbol_mutes WHERE user_id=? AND symbol=?", (user_id, symbol))
            return cur.rowcount == 1

    def is_muted(self, user_id: str, symbol: str | None) -> bool:
        if not symbol:
            return False
        return bool(self.store.query("SELECT 1 FROM discord_symbol_mutes WHERE user_id=? AND symbol=?", (user_id, symbol)))

    def mention_targets(self, symbol: str | None) -> list[str]:
        """Discord ids of active members who opted into high-priority mentions and did not mute `symbol`."""
        rows = self.store.query(
            "SELECT u.id, u.discord_user_id FROM app_users u JOIN discord_member_prefs p ON p.user_id=u.id "
            "WHERE p.high_priority_mentions=1 AND u.revoked_at IS NULL AND u.discord_user_id IS NOT NULL "
            "ORDER BY u.created_at")
        return [r["discord_user_id"] for r in rows if not self.is_muted(r["id"], symbol)]

    # private notifications held because DMs are off / blocked ---------------------------------------------------
    def pending_private(self, user_id: str) -> list[OutboxRow]:
        return self.store.pending_dm_for_user(user_id)

    def release_private(self, user_id: str, now: datetime) -> list[OutboxRow]:
        return self.store.release_deferred_for_user(user_id, now)

    # data rights -----------------------------------------------------------------------------------------------
    def delete_data(self, user_id: str) -> int:
        n = self.store.delete_member_data(user_id)
        self.store.set_dm_opt_in(user_id, False)
        with self.store.tx() as c:
            c.execute("DELETE FROM discord_symbol_mutes WHERE user_id=?", (user_id,))
            c.execute("DELETE FROM discord_member_prefs WHERE user_id=?", (user_id,))
        return n

    def sync_status(self, discord_user_id: str) -> dict:
        """Spooled (queued, not yet persisted) and recently rejected events of THIS member only."""
        pending: list[dict] = []
        rejected: list[str] = []
        rows = self.store.query("SELECT payload, state, last_error, created_at FROM spool WHERE kind=? AND state IN "
                                "('pending','rejected') ORDER BY id", (SPOOL_KIND,))
        for r in rows:
            try:
                p = json.loads(r["payload"])
            except ValueError:
                continue
            if p.get("discord_user_id") != discord_user_id:
                continue
            ev = p.get("event", {})
            item = {"event_type": ev.get("event_type"), "symbol": ev.get("symbol"), "queued_at": r["created_at"],
                    "fill": ev.get("fill")}
            if r["state"] == "pending":
                pending.append(item)
            else:
                rejected.append(r["last_error"] or "REJECTED")
        return {"pending": pending, "rejected": rejected[-5:]}

    def counts(self) -> dict:
        """Aggregate counts for the owner. Never returns any member's fills or identifiers."""
        m = self.store.query("SELECT COUNT(*) AS total, SUM(revoked_at IS NOT NULL) AS revoked, "
                             "SUM(dm_opt_in=1 AND revoked_at IS NULL) AS dm FROM app_users")[0]
        mentions = self.store.query("SELECT COUNT(*) AS n FROM discord_member_prefs WHERE high_priority_mentions=1")[0]["n"]
        pos = {r["state"]: r["n"] for r in self.store.query("SELECT state, COUNT(*) AS n FROM positions GROUP BY state")}
        outbox = {r["state"]: r["n"] for r in self.store.query(
            "SELECT state, COUNT(*) AS n FROM notification_outbox GROUP BY state")}
        last_sent = self.store.query("SELECT MAX(sent_at) AS t FROM notification_outbox WHERE state='sent'")[0]["t"]
        last_quote = self.store.query("SELECT MAX(ts_received) AS t FROM quotes")[0]["t"]
        return {
            "members_total": int(m["total"] or 0), "members_revoked": int(m["revoked"] or 0),
            "members_dm_opt_in": int(m["dm"] or 0), "members_high_priority_mentions": int(mentions or 0),
            "positions_by_state": pos, "outbox_by_state": outbox, "outbox_last_sent_at": last_sent,
            "spool_pending": self.store.spool_pending_count(), "latest_quote_received_at": last_quote,
            "schema_version": self.store.schema_version(),
        }


# ---------------------------------------------------------------------------------------------------------------
def replay_spool(backend: PositionBackend, store: LocalStore, now: datetime, limit: int = 50) -> dict[str, int]:
    """Push queued (pending-sync) member events to the backend. Idempotency keys make retries safe."""
    counts = {"acked": 0, "rejected": 0, "retry": 0}
    for row in store.spool_due(now, limit):
        if row["kind"] != SPOOL_KIND:
            continue
        payload = json.loads(row["payload"])
        try:
            backend.record(payload["discord_user_id"], payload["event"], now)
        except BackendUnavailable as exc:
            store.spool_mark(row["id"], "retry", now, error=str(exc), retry_in=min(900, 30 * 2 ** min(int(row["attempts"]), 5)))
            counts["retry"] += 1
            continue
        except (ServiceError, LedgerError) as exc:
            store.spool_mark(row["id"], "rejected", now, error=exc.code)
            counts["rejected"] += 1
            continue
        except PositionNotFound:
            store.spool_mark(row["id"], "rejected", now, error="POSITION_NOT_FOUND")
            counts["rejected"] += 1
            continue
        except ContractViolation:
            store.spool_mark(row["id"], "rejected", now, error="INVALID_EVENT")
            counts["rejected"] += 1
            continue
        store.spool_mark(row["id"], "acked", now)
        counts["acked"] += 1
    return counts

