"""Worker-local durable state for connected mode (`<state_dir>/agent_state.sqlite3`, WAL, 0600).

Small key/value records that must survive restarts: the cached open-signal view, last notified assessment per
position (materiality hysteresis), last scan session, once-per-day notice markers, rejected model ids. The POST spool
(agent.client.PostSpool) lives in the same file under its own table. No secrets are stored here.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS agent_kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_last_assessment (position_id TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at TEXT NOT NULL);
"""

STATE_FILE = "agent_state.sqlite3"


class AgentState:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._lock = threading.RLock()
        if str(path) != ":memory:":
            with suppress(OSError):
                Path(path).chmod(0o600)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT value FROM agent_kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO agent_kv(key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value, updated_at=excluded.updated_at",
                (key, json.dumps(value, sort_keys=True, default=str), datetime.now(UTC).isoformat()),
            )

    def once(self, key: str) -> bool:
        """True exactly once per key (atomic). Used for once-per-day notices."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO agent_kv(key, value, updated_at) VALUES (?,?,?)",
                (key, "true", datetime.now(UTC).isoformat()),
            )
            return cur.rowcount == 1

    def last_assessment(self, position_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT body FROM agent_last_assessment WHERE position_id=?", (position_id,)).fetchone()
        return json.loads(row["body"]) if row else None

    def set_last_assessment(self, position_id: str, body: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO agent_last_assessment(position_id, body, updated_at) VALUES (?,?,?) ON CONFLICT(position_id) "
                "DO UPDATE SET body=excluded.body, updated_at=excluded.updated_at",
                (position_id, json.dumps(body, sort_keys=True), datetime.now(UTC).isoformat()),
            )
