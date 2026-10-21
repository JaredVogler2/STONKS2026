"""RemotePositionBackend: the bot's PositionBackend over the signed agent API (connected mode).

The cloud ledger is canonical; this class never keeps a second copy of a member's fills. Error mapping:
  network failure / 5xx / 429 after bounded retries, or a device/auth problem (pending approval, revoked device,
  bad clock, bad HMAC)                                -> BackendUnavailable  (the bot spools: "queued - pending sync")
  404 NOT_FOUND (incl. another member's position id)  -> PositionNotFound
  422 ledger code (OVERSELL, CLOSE_QUANTITY_MISMATCH…) -> LedgerError(code)
  422 CONTRACT_VIOLATION                               -> ContractViolation
  403 MEMBERSHIP_REVOKED / GUILD_NOT_ALLOWED, other 4xx -> ServiceError(code)
Every call is scoped by the Discord user id the gateway authenticated; idempotency keys make spool replays safe.

History (`events`, `position_for_key`) comes from GET positions/history, so /apex_correct, /apex_remove and starter
`#n` references (idempotency key `starter:<id>`) work against the cloud ledger. RemoteMemberBackend keeps the bot's
local member mirror (delivery decisions, mutes, held DMs) and syncs prefs / revocation / deletion to POST members.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from apex_worker.agent.client import AgentClient, AgentHTTPError, AgentUnavailable, PayloadTooLarge
from apex_worker.contracts import ContractViolation, validate
from apex_worker.discord_bot.backend import MEMBER_SPOOL_KIND, BackendUnavailable, LocalMemberBackend
from apex_worker.portfolio.ledger import LedgerError, fmt
from apex_worker.services.positions import PositionNotFound, RecordResult, ServiceError
from apex_worker.storage.local import LocalStore
from apex_worker.timeutil import iso_utc

LEDGER_CODES = {
    "ALREADY_CANCELLED", "ALREADY_EXISTS", "ALREADY_OPEN", "BAD_SEQUENCE", "BAD_TARGET", "CLOSE_QUANTITY_MISMATCH",
    "DUPLICATE_EVENT", "FUTURE_FILL", "INVALID_NUMBER", "INVALID_TIME", "MISSING_CORRECTION", "MISSING_FILL",
    "MISSING_REPLACEMENT", "NON_POSITIVE", "NOTHING_TO_SELL", "NOT_OPEN", "NOT_WATCHING", "NO_POSITION", "OVERSELL",
    "TOO_LARGE", "TOO_PRECISE", "UNKNOWN_TARGET",
}
# the device cannot write right now; the member's event is fine and must be kept for later sync
UNAVAILABLE_CODES = {"UNAUTHENTICATED", "CLOCK_SKEW", "DEVICE_PENDING_APPROVAL", "DEVICE_REVOKED", "DEVICE_NOT_PAIRED",
                     "AGENT_AUTH_NOT_CONFIGURED", "DATABASE_NOT_READY", "NON_JSON_RESPONSE", "RESPONSE_TOO_LARGE"}


def _dec(value) -> str:
    """Postgres numeric text ('101.000000') -> the ledger's canonical decimal string ('101')."""
    try:
        return fmt(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return str(value)


def _iso(value: str) -> str:
    return iso_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(microsecond=0))


def _fill(price, quantity, fees, at) -> dict:
    return {"price": _dec(price), "quantity": _dec(quantity), "fees": _dec(fees or "0"), "fill_at": _iso(at)}


def event_from_server(row: dict) -> dict:
    """positions/history event row -> the bot's local history shape (same keys LocalPositionBackend.events returns)."""
    ev = {"event_id": str(row["id"]), "seq": int(row["seq"]), "event_type": row["event_type"],
          "recorded_at": _iso(row["recorded_at"]), "source": row.get("source")}
    if row.get("price") is not None and row.get("quantity") is not None and row.get("event_at"):
        ev["fill"] = _fill(row["price"], row["quantity"], row.get("fees"), row["event_at"])
    corr = row.get("correction_json")
    if isinstance(corr, dict):
        c = {"target_event_id": str(corr["target_event_id"]), "void": bool(corr.get("void"))}
        rep = corr.get("replacement")
        if isinstance(rep, dict):
            c["replacement"] = _fill(rep["price"], rep["quantity"], rep.get("fees"), rep["fill_at"])
        ev["correction"] = c
    if row.get("notes"):
        ev["notes"] = row["notes"]
    return ev


def map_error(exc: AgentHTTPError) -> Exception:
    if exc.code in UNAVAILABLE_CODES or exc.status in (401, 0):
        return BackendUnavailable(f"position store unavailable ({exc.code})")
    if exc.status == 404:
        return PositionNotFound("position not found")
    if exc.code == "CONTRACT_VIOLATION":
        return ContractViolation("PositionEvent", [str(e) for e in exc.extra.get("errors", [])][:5] or ["rejected"])
    if exc.code in LEDGER_CODES:
        return LedgerError(exc.code, exc.message or exc.code)
    return ServiceError(exc.code, exc.message or exc.code)


class RemotePositionBackend:
    def __init__(self, client: AgentClient, *, guild_id: str | None = None, cache_seconds: float = 20.0,
                 clock: Callable[[], float] = time.monotonic):
        self.client = client
        self.guild_id = guild_id
        self.cache_seconds = cache_seconds
        self._clock = clock
        self._cache: dict[tuple[str, bool], tuple[float, list[dict]]] = {}
        self._lock = threading.Lock()

    def _call(self, fn: Callable[[], dict]) -> dict:
        try:
            return fn()
        except AgentUnavailable as exc:
            raise BackendUnavailable("position store unreachable") from exc
        except PayloadTooLarge as exc:
            raise ServiceError("PAYLOAD_TOO_LARGE", "request too large") from exc
        except AgentHTTPError as exc:
            raise map_error(exc) from None

    def invalidate(self, discord_user_id: str) -> None:
        with self._lock:
            for k in [k for k in self._cache if k[0] == discord_user_id]:
                del self._cache[k]

    def record(self, discord_user_id: str, event: dict, now: datetime) -> RecordResult:
        validate("PositionEvent", event)
        body = {"discord_user_id": discord_user_id, "event": event}
        if self.guild_id:
            body["guild_id"] = self.guild_id
        data = self._call(lambda: self.client.post("positions/events", body).data or {})
        self.invalidate(discord_user_id)
        status = data.get("status")
        if status not in ("persisted", "duplicate") or not isinstance(data.get("position"), dict):
            raise BackendUnavailable("unexpected response from the position store")
        return RecordResult(status, data["position"], data.get("event_id"), list(data.get("warnings") or []))

    def positions(self, discord_user_id: str, include_closed: bool = False) -> list[dict]:
        key = (discord_user_id, include_closed)
        with self._lock:
            hit = self._cache.get(key)
        if hit and self._clock() - hit[0] < self.cache_seconds:
            return [dict(p) for p in hit[1]]
        params = {"discord_user_id": discord_user_id, "include_closed": "1" if include_closed else None}
        if self.guild_id:
            params["guild_id"] = self.guild_id
        rows = self._call(lambda: self.client.get("positions", params)).get("positions") or []
        for r in rows:
            validate("PositionSummary", r)
        with self._lock:
            self._cache[key] = (self._clock(), rows)
        return [dict(p) for p in rows]

    def position(self, discord_user_id: str, position_id: str) -> dict:
        for p in self.positions(discord_user_id, include_closed=True):
            if p["position_id"] == position_id:
                return p
        raise PositionNotFound("position not found")

    # ---- PositionHistory (owner-scoped; another member's id is a 404 -> PositionNotFound) -------------------------
    def _history(self, discord_user_id: str, **query: str) -> dict:
        params = {"discord_user_id": discord_user_id, **query}
        if self.guild_id:
            params["guild_id"] = self.guild_id
        return self._call(lambda: self.client.get("positions/history", params))

    def events(self, discord_user_id: str, position_id: str) -> list[dict]:
        rows = self._history(discord_user_id, position_id=position_id).get("events") or []
        return [event_from_server(r) for r in sorted(rows, key=lambda r: int(r["seq"]))]

    def position_for_key(self, discord_user_id: str, idempotency_key: str) -> str | None:
        try:
            data = self._history(discord_user_id, client_idempotency_key=idempotency_key)
        except PositionNotFound:
            return None
        summary = data.get("summary") or {}
        return str(summary["position_id"]) if summary.get("position_id") else None


class RemoteMemberBackend(LocalMemberBackend):
    """Local member mirror (the bot delivers from it) + cloud sync through POST members.

    prefs / revoke: applied locally at once; the cloud copy is pushed now or spooled (MEMBER_SPOOL_KIND, ordered, last
    write wins) and replayed by the bot's spool loop. delete_my_data: cloud FIRST - if the server cannot be reached
    the member is told nothing was deleted (BackendUnavailable), so a deletion is never half-done.
    Restoring a revoked member is local only: the server has no un-revoke action."""

    def __init__(self, store: LocalStore, client: AgentClient, *, guild_id: str | None = None,
                 positions: RemotePositionBackend | None = None):
        super().__init__(store)
        self.client = client
        self.guild_id = guild_id
        self.positions = positions  # its short read cache must not outlive a deletion

    def _body(self, action: str, discord_user_id: str, **extra) -> dict:
        body = {"action": action, "discord_user_id": discord_user_id, **extra}
        if self.guild_id:
            body["guild_id"] = self.guild_id
        return body

    def _push(self, body: dict) -> dict:
        try:
            return self.client.post("members", body).data or {}
        except AgentUnavailable as exc:
            raise BackendUnavailable("member store unreachable") from exc
        except AgentHTTPError as exc:
            raise map_error(exc) from None

    def replay(self, payload: dict) -> None:
        self._push(payload["body"])

    def _sync(self, body: dict, now: datetime) -> None:
        try:
            self._push(body)
        except BackendUnavailable:
            key = f"member:{body['action']}:{body['discord_user_id']}:{int(now.timestamp() * 1000)}"
            self.store.spool_put(MEMBER_SPOOL_KIND, key, {"body": body}, now)
        except (ServiceError, PositionNotFound, LedgerError, ContractViolation):
            pass  # e.g. a member the cloud never saw: the local change still applies

    def _sync_prefs(self, user_id: str, now: datetime) -> None:
        p = self.prefs(user_id)
        if p.get("discord_user_id"):
            self._sync(self._body("prefs", str(p["discord_user_id"]), dm_opt_in=bool(p["dm_opt_in"]),
                                  high_priority_mentions=bool(p["high_priority_mentions"])), now)

    def set_dm_opt_in(self, user_id: str, opt_in: bool) -> None:
        super().set_dm_opt_in(user_id, opt_in)
        self._sync_prefs(user_id, datetime.now(UTC))

    def set_high_priority_mentions(self, user_id: str, opt_in: bool, now: datetime) -> None:
        super().set_high_priority_mentions(user_id, opt_in, now)
        self._sync_prefs(user_id, now)

    def revoke(self, discord_user_id: str, now: datetime) -> None:
        super().revoke(discord_user_id, now)
        self._sync(self._body("revoke", discord_user_id), now)

    def delete_data(self, user_id: str) -> int:
        duid = self.store.member_prefs(user_id).get("discord_user_id")
        cloud = 0
        if duid:
            try:
                cloud = int(self._push(self._body("delete_my_data", str(duid))).get("positions_deleted") or 0)
            except PositionNotFound:
                cloud = 0  # the cloud never had this member
            if self.positions is not None:
                self.positions.invalidate(str(duid))
        local = super().delete_data(user_id)
        self._sync_prefs(user_id, datetime.now(UTC))  # DMs and mentions are now off in the cloud copy too
        return cloud + local
