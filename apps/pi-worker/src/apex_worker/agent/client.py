"""Signed HTTP client for the Vercel agent API (Pi -> server, outbound HTTPS only).

* Every attempt is HMAC-signed (APEX-HMAC-SHA256-V1) with a fresh 144-bit nonce.
* Bounded retries with full-jitter exponential backoff, ONLY for network errors, 5xx, 429 (Retry-After honoured)
  and 409 NONCE_REPLAYED (re-signed with a new nonce). Every other 4xx is final and raised as AgentHTTPError.
* Request bodies are capped per route (mirrors the server's readBodyCapped limits); responses are capped too.
* POSTs that still fail with a network error / 5xx / 429 can be written to a durable SQLite spool and replayed in
  FIFO order on reconnect. Replays cannot duplicate: the spool is unique per dedupe key and the server dedupes by
  natural ids / dedupe keys (signal id+revision, event dedupe_key, scan id, assessment key, outbox event_key).
* Clock skew is measured from the server's `server_time` (or the HTTP Date header) and warned about above 30 s.
* Logging: method, route path (never the query string, which can carry Discord ids), status and attempt only.
  Request/response bodies are never logged (they can hold member fills); secrets never leave this object.
"""

from __future__ import annotations

import email.utils
import hashlib
import json
import logging
import random
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx

from apex_worker.agent.signing import sign

log = logging.getLogger("apex.agent.client")

API_PREFIX = "/api/v1/agent"
DEFAULT_BODY_CAP = 256 * 1024
BODY_CAPS = {
    "observations": 2 * 1024 * 1024,
    "catalysts": 1024 * 1024,
    "scan-runs": 1024 * 1024,
    "positions/assessments": 1024 * 1024,
}
RESPONSE_CAP = 4 * 1024 * 1024  # a 2 MB model artifact is ~2.7 MB as base64
SKEW_WARN_SECONDS = 30
MAX_RETRY_AFTER = 60.0
BYPASS_HEADER = "x-vercel-protection-bypass"


class AgentError(RuntimeError):
    pass


class AgentUnavailable(AgentError):
    """Network failure, 5xx or rate limit after the bounded retries: nothing is known to be persisted."""


class PayloadTooLarge(AgentError):
    pass


class AgentHTTPError(AgentError):
    """A final (non-retryable) server answer. `code` is the server's stable error code."""

    def __init__(self, status: int, code: str, message: str = "", extra: dict | None = None):
        super().__init__(f"HTTP {status} {code}: {message}"[:300])
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}


@dataclass(frozen=True)
class PostResult:
    sent: bool
    spooled: bool = False
    dropped: bool = False
    data: dict | None = None


@dataclass(frozen=True)
class _Attempt:
    status: int | None
    data: dict
    retry_after: float | None = None
    error: str | None = None


def body_cap(route: str) -> int:
    return BODY_CAPS.get(route.split("?", 1)[0].strip("/"), DEFAULT_BODY_CAP)


def encode_body(body: dict | None) -> bytes:
    if body is None:
        return b""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()


def _server_epoch(data: dict, headers: httpx.Headers) -> float | None:
    st = data.get("server_time") if isinstance(data, dict) else None
    if st is None and isinstance(data.get("error"), dict):
        st = data["error"].get("server_time")
    if isinstance(st, int | float) and not isinstance(st, bool):
        return float(st)
    if isinstance(st, str):
        with suppress(ValueError):
            return datetime.fromisoformat(st.replace("Z", "+00:00")).timestamp()
    date_hdr = headers.get("date")
    if date_hdr:
        with suppress(TypeError, ValueError):
            return email.utils.parsedate_to_datetime(date_hdr).timestamp()
    return None


# ---- durable POST spool ------------------------------------------------------------------------------------------
SPOOL_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS agent_spool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    route TEXT NOT NULL,
    body BLOB NOT NULL,
    droppable INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','rejected')),
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_spool_state ON agent_spool(state, id);
"""


class PostSpool:
    """FIFO spool of signed-later POSTs. Rows are deleted once the server acknowledged them."""

    def __init__(self, path: Path | str, *, max_rows: int = 20_000, max_bytes: int = 64 * 1024 * 1024):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SPOOL_SCHEMA)
        self._lock = threading.RLock()
        self.max_rows = max_rows
        self.max_bytes = max_bytes
        if str(path) != ":memory:":
            with suppress(OSError):
                Path(path).chmod(0o600)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def put(self, dedupe_key: str, route: str, body: bytes, *, droppable: bool = False) -> bool:
        """Insert unless the key is already pending. Returns False if a droppable row could not be kept."""
        with self._lock:
            self._make_room(len(body))
            if droppable and self._over(len(body)):
                return False
            self._conn.execute(
                "INSERT OR IGNORE INTO agent_spool(dedupe_key, route, body, droppable, created_at) VALUES (?,?,?,?,?)",
                (dedupe_key, route, body, 1 if droppable else 0, datetime.now(UTC).isoformat()),
            )
            return True

    def _over(self, extra: int) -> bool:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(body)),0) AS b FROM agent_spool WHERE state='pending'"
        ).fetchone()
        return row["n"] + 1 > self.max_rows or row["b"] + extra > self.max_bytes

    def _make_room(self, extra: int) -> None:
        """Shed the OLDEST droppable rows (quotes) first; signals/events/assessments are never dropped."""
        while self._over(extra):
            cur = self._conn.execute(
                "DELETE FROM agent_spool WHERE id = (SELECT id FROM agent_spool WHERE state='pending' AND droppable=1 "
                "ORDER BY id LIMIT 1)"
            )
            if cur.rowcount == 0:
                return

    def pending(self, limit: int = 100) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM agent_spool WHERE state='pending' ORDER BY id LIMIT ?", (limit,)
            ).fetchall()

    def pending_count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM agent_spool WHERE state='pending'").fetchone()[0])

    def is_pending(self, dedupe_key: str) -> bool:
        with self._lock:
            return bool(self._conn.execute(
                "SELECT 1 FROM agent_spool WHERE dedupe_key=? AND state='pending'", (dedupe_key,)).fetchone())

    def rejected_count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM agent_spool WHERE state='rejected'").fetchone()[0])

    def done(self, row_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM agent_spool WHERE id=?", (row_id,))

    def reject(self, row_id: int, code: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE agent_spool SET state='rejected', attempts=attempts+1, last_error=? WHERE id=?", (code[:120], row_id)
            )
            # keep only the most recent rejected rows for diagnosis
            self._conn.execute(
                "DELETE FROM agent_spool WHERE state='rejected' AND id NOT IN "
                "(SELECT id FROM agent_spool WHERE state='rejected' ORDER BY id DESC LIMIT 200)"
            )

    def bump(self, row_id: int, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE agent_spool SET attempts=attempts+1, last_error=? WHERE id=?", (error[:120], row_id)
            )


# ---- client --------------------------------------------------------------------------------------------------------
class AgentClient:
    def __init__(
        self,
        base_url: str,
        agent_id: str,
        secret: bytes,
        *,
        spool: PostSpool | None = None,
        transport: httpx.BaseTransport | None = None,
        bypass_secret: str | None = None,
        max_attempts: int = 4,
        backoff_base: float = 0.5,
        backoff_cap: float = 8.0,
        timeout: float = 15.0,
        connect_timeout: float = 5.0,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
    ):
        parts = urlsplit(base_url.rstrip("/"))
        if parts.scheme != "https" and parts.hostname not in ("localhost", "127.0.0.1", "testserver"):
            raise ValueError("APEX_API_BASE_URL must use https (plain http only for localhost testing)")
        if len(secret) < 32:
            raise ValueError("agent HMAC secret must be at least 32 bytes")
        self.base_url = f"{parts.scheme}://{parts.netloc}"
        self._prefix = parts.path.rstrip("/") + API_PREFIX
        self.agent_id = agent_id
        self._secret = secret
        self._bypass = bypass_secret
        self.spool = spool
        self.max_attempts = max(1, max_attempts)
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self._clock = clock
        self._sleep = sleep
        self._rng = rng
        self._http = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
            follow_redirects=False,  # a redirect must never carry a signed request elsewhere
            headers={"User-Agent": "apex-worker/0.1 (private research agent)", "Accept": "application/json"},
        )
        self._lock = threading.Lock()
        self.skew_seconds: float | None = None
        self.last_ok_at: float | None = None
        self.last_error: str | None = None
        self.offline_until: float = 0.0
        self._offline_backoff = 15.0
        self._skew_warned_at = 0.0

    def __repr__(self) -> str:  # never leak the secret
        return f"AgentClient(base={self.base_url}, agent={self.agent_id})"

    def close(self) -> None:
        self._http.close()

    # ---- state ------------------------------------------------------------------------------------------------
    @property
    def offline(self) -> bool:
        return self._clock() < self.offline_until

    @property
    def clock_synced(self) -> bool | None:
        return None if self.skew_seconds is None else abs(self.skew_seconds) <= SKEW_WARN_SECONDS

    def _mark_ok(self) -> None:
        self.last_ok_at = self._clock()
        self.offline_until = 0.0
        self._offline_backoff = 15.0

    def _mark_down(self, error: str) -> None:
        self.last_error = error
        self.offline_until = self._clock() + self._offline_backoff
        self._offline_backoff = min(300.0, self._offline_backoff * 2)

    def _observe_skew(self, data: dict, headers: httpx.Headers, local: float) -> None:
        server = _server_epoch(data, headers)
        if server is None:
            return
        self.skew_seconds = round(server - local, 3)
        if abs(self.skew_seconds) > SKEW_WARN_SECONDS and local - self._skew_warned_at > 600:
            self._skew_warned_at = local
            log.warning("clock skew vs APEX server is %.0fs (limit 60s): check NTP (timedatectl)", self.skew_seconds)

    # ---- low level ----------------------------------------------------------------------------------------------
    def _url(self, route: str, params: dict | None) -> httpx.URL:
        query = ("?" + urlencode([(k, str(v)) for k, v in params.items() if v is not None])) if params else ""
        return httpx.URL(self.base_url + self._prefix + "/" + route.strip("/") + query)

    def _attempt(self, method: str, url: httpx.URL, body: bytes) -> _Attempt:
        signed_path = url.raw_path.decode("ascii")  # exactly what the server reads as pathname + search
        headers = sign(self._secret, self.agent_id, method, signed_path, body, timestamp=int(self._clock())).as_http()
        if body:
            headers["Content-Type"] = "application/json"
        if self._bypass:
            headers[BYPASS_HEADER] = self._bypass
        try:
            with self._http.stream(method, url, content=body or None, headers=headers) as resp:
                raw = bytearray()
                for chunk in resp.iter_bytes():
                    raw += chunk
                    if len(raw) > RESPONSE_CAP:
                        return _Attempt(None, {}, error="RESPONSE_TOO_LARGE")
                status, resp_headers = resp.status_code, resp.headers
        except httpx.HTTPError as exc:
            return _Attempt(None, {}, error=f"network:{type(exc).__name__}")
        local = self._clock()
        try:
            data = json.loads(bytes(raw)) if raw else {}
            if not isinstance(data, dict):
                data = {"_non_object": True}
        except ValueError:
            data = {"error": {"code": "NON_JSON_RESPONSE", "message": "server returned a non-JSON body "
                                                                      "(Vercel Deployment Protection? set APEX_VERCEL_BYPASS_SECRET)"}}
        self._observe_skew(data, resp_headers, local)
        retry_after = None
        if status == 429:
            with suppress(TypeError, ValueError):
                retry_after = float(resp_headers.get("retry-after") or "")
        return _Attempt(status, data, retry_after)

    def _backoff(self, attempt: int) -> float:
        return self._rng() * min(self.backoff_cap, self.backoff_base * 2**attempt)  # full jitter

    def request(self, method: str, route: str, *, params: dict | None = None, body: dict | None = None) -> dict:
        """Signed request with bounded retries. Raises AgentHTTPError (final 4xx) or AgentUnavailable."""
        raw = encode_body(body)
        cap = body_cap(route)
        if len(raw) > cap:
            raise PayloadTooLarge(f"{route} body {len(raw)} bytes exceeds cap {cap}")
        url = self._url(route, params)
        attempts = 1 if self.offline else self.max_attempts
        last = "unknown"
        for i in range(attempts):
            res = self._attempt(method, url, raw)
            status = res.status
            if status is not None and 200 <= status < 300:
                self._mark_ok()
                log.debug("%s %s -> %s (attempt %d)", method, url.path, status, i + 1)
                return res.data
            err = res.data.get("error") if isinstance(res.data.get("error"), dict) else {}
            code = str(err.get("code") or f"HTTP_{status}")
            if status is None and res.error == "RESPONSE_TOO_LARGE":
                raise AgentHTTPError(0, "RESPONSE_TOO_LARGE", f"response exceeded {RESPONSE_CAP} bytes")
            if status is None:
                last = res.error or "network"
            elif status == 409 and code == "NONCE_REPLAYED":
                last = code  # re-signed with a new nonce on the next attempt, no wait needed
                log.info("%s %s nonce replay reported; re-signing", method, url.path)
                continue
            elif status == 429:
                last = "RATE_LIMITED"
                wait = res.retry_after if res.retry_after is not None else self._backoff(i + 1)
                if wait > MAX_RETRY_AFTER:
                    break
                if i + 1 < attempts:
                    self._sleep(wait)
                continue
            elif status >= 500:
                last = f"HTTP_{status}:{code}"
            else:
                log.info("%s %s -> %s %s", method, url.path, status, code)
                raise AgentHTTPError(status, code, str(err.get("message") or "")[:200],
                                     {k: v for k, v in err.items() if k not in ("code", "message")})
            log.info("%s %s failed (%s), attempt %d/%d", method, url.path, last, i + 1, attempts)
            if i + 1 < attempts:
                self._sleep(self._backoff(i))
        self._mark_down(last)
        raise AgentUnavailable(f"{method} {url.path}: {last}")

    def get(self, route: str, params: dict | None = None) -> dict:
        return self.request("GET", route, params=params)

    # ---- POST with optional spool ----------------------------------------------------------------------------------
    def post(self, route: str, body: dict, *, spool_key: str | None = None, droppable: bool = False) -> PostResult:
        """POST now; with `spool_key`, a transient failure is spooled (and later replayed in FIFO order).

        While older spooled rows are pending, new spoolable POSTs queue behind them so the server sees writes in
        their original order (a signal before its events)."""
        if spool_key is None or self.spool is None:
            return PostResult(True, data=self.request("POST", route, body=body))
        raw = encode_body(body)
        if len(raw) > body_cap(route):
            raise PayloadTooLarge(f"{route} body {len(raw)} bytes exceeds cap {body_cap(route)}")
        if self.offline or self.spool.pending_count():
            kept = self.spool.put(spool_key, route, raw, droppable=droppable)
            if not self.offline:
                self.flush_spool()
            sent = kept and not self.spool.is_pending(spool_key)
            return PostResult(sent, spooled=kept and not sent, dropped=not kept)
        try:
            return PostResult(True, data=self.request("POST", route, body=body))
        except AgentUnavailable:
            kept = self.spool.put(spool_key, route, raw, droppable=droppable)
            log.info("POST %s spooled for replay (key %s)", route, spool_key.split(":", 1)[0])
            return PostResult(False, spooled=kept, dropped=not kept)

    def flush_spool(self, limit: int = 200) -> dict[str, int]:
        """Replay spooled POSTs oldest-first; stop at the first transient failure to preserve ordering."""
        counts = {"sent": 0, "rejected": 0, "remaining": 0}
        if self.spool is None:
            return counts
        for row in self.spool.pending(limit):
            try:
                self.request("POST", row["route"], body=json.loads(row["body"]))
            except AgentUnavailable as exc:
                self.spool.bump(row["id"], str(exc))
                break
            except AgentHTTPError as exc:
                # final rejection (e.g. contract violation): keep for diagnosis, never retried, never blocks the queue
                self.spool.reject(row["id"], exc.code)
                counts["rejected"] += 1
                log.warning("spooled POST %s rejected by server: %s", row["route"], exc.code)
                continue
            except PayloadTooLarge:
                self.spool.reject(row["id"], "PAYLOAD_TOO_LARGE")
                counts["rejected"] += 1
                continue
            self.spool.done(row["id"])
            counts["sent"] += 1
        counts["remaining"] = self.spool.pending_count()
        return counts


def dedupe_key(kind: str, *parts: str) -> str:
    """Stable spool key; long natural keys are hashed so the key stays short."""
    joined = ":".join(parts)
    if len(joined) > 120:
        joined = hashlib.sha256(joined.encode()).hexdigest()
    return f"{kind}:{joined}"
