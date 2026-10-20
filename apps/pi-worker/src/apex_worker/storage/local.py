"""Local SQLite (WAL) store used for DEMO mode, offline operation and the durable write spool.

In connected mode the cloud Postgres database is canonical; this store then only keeps the
spool of not-yet-acknowledged writes. In DEMO / offline mode it plays the canonical role with the
same invariants: append-only events, transactional outbox, ownership-scoped position reads.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from apex_worker.timeutil import iso_utc, parse_utc

SCHEMA_VERSION = 3

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS app_users (
    id TEXT PRIMARY KEY,
    discord_user_id TEXT UNIQUE,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner','member')),
    dm_opt_in INTEGER NOT NULL DEFAULT 0,
    min_priority TEXT NOT NULL DEFAULT 'NORMAL',
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS signals (
    id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    state TEXT NOT NULL,
    is_demo INTEGER NOT NULL,
    body TEXT NOT NULL,
    immutable_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id, revision)
);
CREATE TABLE IF NOT EXISTS signal_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_at TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES app_users(id),
    symbol TEXT NOT NULL,
    signal_id TEXT,
    state TEXT NOT NULL,
    is_demo INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_positions_user ON positions(user_id);
CREATE TABLE IF NOT EXISTS position_events (
    id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL REFERENCES positions(id),
    user_id TEXT NOT NULL REFERENCES app_users(id),
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    client_idempotency_key TEXT NOT NULL,
    body TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (user_id, client_idempotency_key),
    UNIQUE (position_id, seq)
);
CREATE TABLE IF NOT EXISTS notification_outbox (
    id TEXT PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    user_id TEXT,
    route TEXT NOT NULL,
    kind TEXT NOT NULL,
    priority TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','sent','deferred','dead_letter','suppressed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    discord_message_id TEXT,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_due ON notification_outbox(state, next_attempt_at);
CREATE TABLE IF NOT EXISTS delivery_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    outbox_id TEXT NOT NULL REFERENCES notification_outbox(id),
    status TEXT NOT NULL,
    http_status INTEGER,
    rate_limit_until TEXT,
    occurred_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','acked','rejected')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_spool_due ON spool(state, next_attempt_at);
CREATE TABLE IF NOT EXISTS quotes (
    symbol TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    ts_received TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS position_assessments (
    position_id TEXT NOT NULL,
    assessment_key TEXT NOT NULL,
    body TEXT NOT NULL,
    notified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (position_id, assessment_key)
);
CREATE TABLE IF NOT EXISTS scan_runs (
    id TEXT PRIMARY KEY,
    session TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class OwnershipError(PermissionError):
    """Raised (as a generic not-found to callers) when an actor touches another member's data."""


@dataclass(frozen=True)
class OutboxRow:
    id: str
    event_key: str
    user_id: str | None
    route: dict
    kind: str
    priority: str
    state: str
    attempts: int
    payload: dict
    created_at: str


class LocalStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        # highest schema any code version has applied; the Pi's rollback guard refuses code older than this
        self._conn.execute(
            "INSERT INTO meta(key,value) VALUES ('schema_version', ?) ON CONFLICT(key) DO UPDATE SET "
            "value = CAST(MAX(CAST(value AS INTEGER), CAST(excluded.value AS INTEGER)) AS TEXT)",
            (str(SCHEMA_VERSION),),
        )
        if str(path) != ":memory:":
            with contextlib.suppress(OSError):
                self.path.chmod(0o600)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def schema_version(self) -> int:
        row = self.query("SELECT value FROM meta WHERE key='schema_version'")
        return int(row[0]["value"]) if row else 0

    # ---- members ---------------------------------------------------------
    def ensure_member(self, discord_user_id: str, now: datetime, role: str = "member") -> str:
        with self.tx() as c:
            row = c.execute(
                "SELECT id, revoked_at FROM app_users WHERE discord_user_id=?", (discord_user_id,)
            ).fetchone()
            if row:
                if row["revoked_at"]:
                    raise OwnershipError("membership revoked")
                return str(row["id"])
            uid = str(uuid.uuid4())
            c.execute(
                "INSERT INTO app_users(id, discord_user_id, role, created_at) VALUES (?,?,?,?)",
                (uid, discord_user_id, role, iso_utc(now)),
            )
            return uid

    def member_id(self, discord_user_id: str) -> str | None:
        rows = self.query("SELECT id FROM app_users WHERE discord_user_id=? AND revoked_at IS NULL", (discord_user_id,))
        return str(rows[0]["id"]) if rows else None

    def revoke_member(self, discord_user_id: str, now: datetime) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE app_users SET revoked_at=? WHERE discord_user_id=? AND revoked_at IS NULL",
                (iso_utc(now), discord_user_id),
            )

    def set_dm_opt_in(self, user_id: str, opt_in: bool) -> None:
        with self.tx() as c:
            c.execute("UPDATE app_users SET dm_opt_in=? WHERE id=?", (1 if opt_in else 0, user_id))

    def member_prefs(self, user_id: str) -> dict:
        rows = self.query(
            "SELECT discord_user_id, dm_opt_in, min_priority, revoked_at FROM app_users WHERE id=?", (user_id,)
        )
        if not rows:
            raise OwnershipError("unknown member")
        r = rows[0]
        return {
            "discord_user_id": r["discord_user_id"],
            "dm_opt_in": bool(r["dm_opt_in"]),
            "min_priority": r["min_priority"],
            "revoked": r["revoked_at"] is not None,
        }

    def delete_member_data(self, user_id: str) -> int:
        """Member-initiated deletion of their own positions; signals and aggregate stats are untouched."""
        with self.tx() as c:
            pos = [r["id"] for r in c.execute("SELECT id FROM positions WHERE user_id=?", (user_id,))]
            for pid in pos:
                c.execute("DELETE FROM position_assessments WHERE position_id=?", (pid,))
            c.execute("DELETE FROM position_events WHERE user_id=?", (user_id,))
            c.execute("DELETE FROM positions WHERE user_id=?", (user_id,))
            c.execute(
                "UPDATE notification_outbox SET state='suppressed', payload='{}' WHERE user_id=? AND state != 'sent'",
                (user_id,),
            )
            return len(pos)

    # ---- signals ---------------------------------------------------------
    def save_signal_revision(self, signal: dict, conn: sqlite3.Connection | None = None) -> bool:
        def _do(c: sqlite3.Connection) -> bool:
            cur = c.execute(
                "INSERT OR IGNORE INTO signals(id, revision, symbol, state, is_demo, body, immutable_hash, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    signal["id"],
                    signal["revision"],
                    signal["symbol"],
                    signal["state"],
                    1 if signal["is_demo"] else 0,
                    json.dumps(signal, sort_keys=True),
                    signal["immutable_hash"],
                    signal["created_at"],
                ),
            )
            return cur.rowcount == 1

        if conn is not None:
            return _do(conn)
        with self.tx() as c:
            return _do(c)

    def append_signal_event(self, event: dict, conn: sqlite3.Connection | None = None) -> bool:
        def _do(c: sqlite3.Connection) -> bool:
            cur = c.execute(
                "INSERT OR IGNORE INTO signal_events(signal_id, event_type, event_at, from_state, to_state, dedupe_key, body) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    event["signal_id"],
                    event["event_type"],
                    event["event_at"],
                    event["from_state"],
                    event["to_state"],
                    event["dedupe_key"],
                    json.dumps(event, sort_keys=True),
                ),
            )
            return cur.rowcount == 1

        if conn is not None:
            return _do(conn)
        with self.tx() as c:
            return _do(c)

    def signal_state(self, signal_id: str) -> str | None:
        rows = self.query(
            "SELECT to_state FROM signal_events WHERE signal_id=? ORDER BY seq DESC LIMIT 1", (signal_id,)
        )
        return rows[0]["to_state"] if rows else None

    def latest_signal(self, signal_id: str) -> dict | None:
        rows = self.query("SELECT body FROM signals WHERE id=? ORDER BY revision DESC LIMIT 1", (signal_id,))
        if not rows:
            return None
        body = json.loads(rows[0]["body"])
        body["state"] = self.signal_state(signal_id) or body["state"]
        return body

    def list_signals(self, states: set[str] | None = None, limit: int = 50) -> list[dict]:
        rows = self.query("SELECT DISTINCT id FROM signals ORDER BY created_at DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            sig = self.latest_signal(r["id"])
            if sig and (states is None or sig["state"] in states):
                out.append(sig)
        return out

    def signal_events(self, signal_id: str) -> list[dict]:
        return [
            json.loads(r["body"])
            for r in self.query("SELECT body FROM signal_events WHERE signal_id=? ORDER BY seq", (signal_id,))
        ]

    # ---- positions -------------------------------------------------------
    def create_position(
        self, c: sqlite3.Connection, user_id: str, symbol: str, signal_id: str | None, now: datetime, is_demo: bool
    ) -> str:
        pid = str(uuid.uuid4())
        c.execute(
            "INSERT INTO positions(id, user_id, symbol, signal_id, state, is_demo, created_at) VALUES (?,?,?,?,?,?,?)",
            (pid, user_id, symbol, signal_id, "WATCHING", 1 if is_demo else 0, iso_utc(now)),
        )
        return pid

    def owned_position(self, c: sqlite3.Connection | None, user_id: str, position_id: str) -> sqlite3.Row:
        conn = c or self._conn
        with self._lock:
            row = conn.execute("SELECT * FROM positions WHERE id=? AND user_id=?", (position_id, user_id)).fetchone()
        if row is None:
            raise OwnershipError("position not found")
        return row

    def position_event_rows(self, c: sqlite3.Connection | None, user_id: str, position_id: str) -> list[sqlite3.Row]:
        conn = c or self._conn
        with self._lock:
            return conn.execute(
                "SELECT * FROM position_events WHERE position_id=? AND user_id=? ORDER BY seq", (position_id, user_id)
            ).fetchall()

    def find_idempotent(self, c: sqlite3.Connection, user_id: str, key: str) -> sqlite3.Row | None:
        return c.execute(
            "SELECT * FROM position_events WHERE user_id=? AND client_idempotency_key=?", (user_id, key)
        ).fetchone()

    def insert_position_event(
        self,
        c: sqlite3.Connection,
        *,
        user_id: str,
        position_id: str,
        seq: int,
        event_type: str,
        key: str,
        body: dict,
        now: datetime,
    ) -> str:
        eid = body.get("event_id") or str(uuid.uuid4())
        c.execute(
            "INSERT INTO position_events(id, position_id, user_id, seq, event_type, client_idempotency_key, body, recorded_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (eid, position_id, user_id, seq, event_type, key, json.dumps(body, sort_keys=True), iso_utc(now)),
        )
        return eid

    def update_position_state(self, c: sqlite3.Connection, position_id: str, state: str) -> None:
        c.execute("UPDATE positions SET state=? WHERE id=?", (state, position_id))

    def list_positions(self, user_id: str, include_closed: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM positions WHERE user_id=?"
        if not include_closed:
            sql += " AND state != 'CLOSED'"
        return self.query(sql + " ORDER BY created_at DESC", (user_id,))

    def open_positions_for_symbol(self, symbol: str) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM positions WHERE symbol=? AND state IN ('OPEN','PARTIALLY_CLOSED','WATCHING')", (symbol,)
        )

    def all_open_positions(self) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM positions WHERE state IN ('OPEN','PARTIALLY_CLOSED','WATCHING')")

    def save_assessment(
        self,
        position_id: str,
        key: str,
        body: dict,
        now: datetime,
        conn: sqlite3.Connection | None = None,
        notified: bool = False,
    ) -> bool:
        def _do(c: sqlite3.Connection) -> bool:
            cur = c.execute(
                "INSERT OR IGNORE INTO position_assessments(position_id, assessment_key, body, notified, created_at) "
                "VALUES (?,?,?,?,?)",
                (position_id, key, json.dumps(body, sort_keys=True), 1 if notified else 0, iso_utc(now)),
            )
            return cur.rowcount == 1

        if conn is not None:
            return _do(conn)
        with self.tx() as c:
            return _do(c)

    def last_assessment(self, position_id: str, notified_only: bool = False) -> dict | None:
        where = " AND notified=1" if notified_only else ""
        rows = self.query(
            f"SELECT body FROM position_assessments WHERE position_id=?{where} "  # noqa: S608 - constant
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (position_id,),
        )
        return json.loads(rows[0]["body"]) if rows else None

    def save_scan(self, scan_id: str, session: str, body: dict, now: datetime) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO scan_runs(id, session, body, created_at) VALUES (?,?,?,?)",
                (scan_id, session, json.dumps(body, sort_keys=True, default=str), iso_utc(now)),
            )

    def scans(self, limit: int = 30) -> list[dict]:
        return [
            json.loads(r["body"])
            for r in self.query("SELECT body FROM scan_runs ORDER BY session DESC LIMIT ?", (limit,))
        ]

    # ---- outbox ------------------------------------------------------------
    def enqueue(self, intent: dict, now: datetime, conn: sqlite3.Connection | None = None) -> bool:
        """Insert a notification intent; duplicate event_key is a no-op (idempotent)."""
        route = intent["route"]
        user_id = route.get("user_id") if route.get("type") == "DM" else None

        def _do(c: sqlite3.Connection) -> bool:
            cur = c.execute(
                "INSERT OR IGNORE INTO notification_outbox(id, event_key, user_id, route, kind, priority, next_attempt_at, "
                "payload, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    intent["event_key"],
                    user_id,
                    json.dumps(route, sort_keys=True),
                    intent["kind"],
                    intent["priority"],
                    iso_utc(now),
                    json.dumps(intent, sort_keys=True),
                    iso_utc(now),
                ),
            )
            return cur.rowcount == 1

        if conn is not None:
            return _do(conn)
        with self.tx() as c:
            return _do(c)

    def due_outbox(self, now: datetime, limit: int = 20) -> list[OutboxRow]:
        rows = self.query(
            "SELECT * FROM notification_outbox WHERE state IN ('pending','deferred') AND next_attempt_at <= ? "
            "ORDER BY CASE priority WHEN 'URGENT' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'NORMAL' THEN 2 ELSE 3 END, created_at "
            "LIMIT ?",
            (iso_utc(now), limit),
        )
        return [self._outbox_row(r) for r in rows]

    def pending_dm_for_user(self, user_id: str) -> list[OutboxRow]:
        rows = self.query(
            "SELECT * FROM notification_outbox WHERE user_id=? AND state='deferred' ORDER BY created_at", (user_id,)
        )
        return [self._outbox_row(r) for r in rows]

    def outbox_rows(self) -> list[OutboxRow]:
        return [self._outbox_row(r) for r in self.query("SELECT * FROM notification_outbox ORDER BY created_at, rowid")]

    @staticmethod
    def _outbox_row(r: sqlite3.Row) -> OutboxRow:
        return OutboxRow(
            r["id"],
            r["event_key"],
            r["user_id"],
            json.loads(r["route"]),
            r["kind"],
            r["priority"],
            r["state"],
            r["attempts"],
            json.loads(r["payload"]),
            r["created_at"],
        )

    def ack_outbox(
        self,
        outbox_id: str,
        status: str,
        now: datetime,
        *,
        http_status: int | None = None,
        retry_after: float | None = None,
        discord_message_id: str | None = None,
        error: str | None = None,
        max_attempts: int = 8,
    ) -> str:
        """status: delivered | failed | dm_blocked. Returns the new outbox state."""
        with self.tx() as c:
            row = c.execute("SELECT state, attempts FROM notification_outbox WHERE id=?", (outbox_id,)).fetchone()
            if row is None:
                raise KeyError(outbox_id)
            if row["state"] == "sent":
                return "sent"  # duplicate ack is harmless
            attempts = row["attempts"] + 1
            rl_until = iso_utc(now + timedelta(seconds=retry_after)) if retry_after else None
            c.execute(
                "INSERT INTO delivery_attempts(outbox_id, status, http_status, rate_limit_until, occurred_at) "
                "VALUES (?,?,?,?,?)",
                (outbox_id, status, http_status, rl_until, iso_utc(now)),
            )
            if status == "delivered":
                c.execute(
                    "UPDATE notification_outbox SET state='sent', attempts=?, sent_at=?, discord_message_id=? "
                    "WHERE id=?",
                    (attempts, iso_utc(now), discord_message_id, outbox_id),
                )
                return "sent"
            if status == "dm_blocked":
                # never fall back to a public channel: hold privately until the member interacts again
                c.execute(
                    "UPDATE notification_outbox SET state='deferred', attempts=?, next_attempt_at=?, last_error=? "
                    "WHERE id=?",
                    (attempts, "9999-12-31T00:00:00Z", "DM_BLOCKED", outbox_id),
                )
                return "deferred"
            if attempts >= max_attempts:
                c.execute(
                    "UPDATE notification_outbox SET state='dead_letter', attempts=?, last_error=? WHERE id=?",
                    (attempts, (error or "failed")[:200], outbox_id),
                )
                return "dead_letter"
            backoff = retry_after if retry_after is not None else min(900.0, 2.0**attempts)
            c.execute(
                "UPDATE notification_outbox SET state='pending', attempts=?, next_attempt_at=?, last_error=? "
                "WHERE id=?",
                (attempts, iso_utc(now + timedelta(seconds=backoff)), (error or "failed")[:200], outbox_id),
            )
            return "pending"

    def release_deferred_for_user(self, user_id: str, now: datetime) -> list[OutboxRow]:
        """Return (and mark sent) privately held notifications to show in an ephemeral reply."""
        rows = self.pending_dm_for_user(user_id)
        with self.tx() as c:
            for r in rows:
                c.execute(
                    "UPDATE notification_outbox SET state='sent', sent_at=?, last_error='SHOWN_EPHEMERAL' WHERE id=?",
                    (iso_utc(now), r.id),
                )
        return rows

    # ---- spool (durable writes awaiting cloud ack) -----------------------------
    def spool_put(self, kind: str, key: str, payload: dict, now: datetime) -> bool:
        with self.tx() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO spool(kind, idempotency_key, payload, next_attempt_at, created_at) "
                "VALUES (?,?,?,?,?)",
                (kind, key, json.dumps(payload, sort_keys=True), iso_utc(now), iso_utc(now)),
            )
            return cur.rowcount == 1

    def spool_due(self, now: datetime, limit: int = 50) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM spool WHERE state='pending' AND next_attempt_at <= ? ORDER BY id LIMIT ?",
            (iso_utc(now), limit),
        )

    def spool_pending_count(self) -> int:
        return int(self.query("SELECT COUNT(*) AS n FROM spool WHERE state='pending'")[0]["n"])

    def spool_mark(
        self, spool_id: int, state: str, now: datetime, error: str | None = None, retry_in: float | None = None
    ) -> None:
        with self.tx() as c:
            if state == "retry":
                c.execute(
                    "UPDATE spool SET attempts=attempts+1, next_attempt_at=?, last_error=? WHERE id=?",
                    (iso_utc(now + timedelta(seconds=retry_in or 30)), (error or "")[:200], spool_id),
                )
            else:
                c.execute("UPDATE spool SET state=?, last_error=? WHERE id=?", (state, (error or None), spool_id))

    # ---- quotes ------------------------------------------------------------------
    def put_quote(self, observation: dict) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO quotes(symbol, body, ts_received) VALUES (?,?,?) ON CONFLICT(symbol) DO UPDATE SET "
                "body=excluded.body, ts_received=excluded.ts_received WHERE excluded.ts_received >= quotes.ts_received",
                (
                    observation["symbol"],
                    json.dumps(observation, sort_keys=True),
                    observation["provenance"]["ts_received"],
                ),
            )

    def get_quote(self, symbol: str) -> dict | None:
        rows = self.query("SELECT body FROM quotes WHERE symbol=?", (symbol,))
        return json.loads(rows[0]["body"]) if rows else None


def parse_row_time(value: str | None) -> datetime | None:
    return parse_utc(value) if value else None


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))
