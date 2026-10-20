"""Durable outbox -> Discord delivery loop (runs as its own asyncio task inside the bot process).

State lives in SQLite (`notification_outbox` + `delivery_attempts`), so a restart never resends an item that was
acknowledged as delivered. Channel messages also carry a deterministic nonce with Discord's `enforce_nonce`, which
closes the remaining "sent but crashed before the ack" window for a few minutes.

Delivery rules (same semantics as dryrun.DryRunDiscord):
- DM routes go ONLY to members who opted in and are not revoked/muted for that symbol; otherwise, or when Discord
  refuses the DM (403/404), the row is acked `dm_blocked` -> held privately (deferred) and shown in the member's
  next ephemeral reply. A DM is never redirected to a channel.
- Channel routes resolve `channel_key` through the operator's channel map. Private kinds on a channel route are
  dead-lettered, never sent.
- 429: acked `failed` with Discord's retry_after and the whole loop pauses until then. Other errors: `failed`
  with exponential backoff (LocalStore.ack_outbox); after max_attempts -> dead_letter.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from apex_worker.notifications.render import render
from apex_worker.storage.local import LocalStore, OutboxRow, OwnershipError
from apex_worker.timeutil import iso_utc

from .limits import CONTENT_MAX, clamp_message, clip

log = logging.getLogger("apex.discord.delivery")

CHANNEL_KEYS = ("high_conviction_alerts", "active_trades", "exit_alerts", "market_intelligence",
                "performance_reports", "system_status")
PRIVATE_KINDS = {"POSITION_UPDATE", "POSITION_CONFIRMATION"}
MENTION_KINDS = {"NEW_SIGNAL", "SIGNAL_CLOSED"}
MENTION_CHANNELS = {"high_conviction_alerts", "exit_alerts"}
UNCONFIGURED_RETRY_SECONDS = 600.0


class TransportError(Exception):
    def __init__(self, status: int | None = None, detail: str = ""):
        super().__init__(f"discord HTTP {status}: {detail}"[:200])
        self.status = status


class TransportForbidden(TransportError):
    def __init__(self, detail: str = "forbidden"):
        super().__init__(403, detail)


class TransportNotFound(TransportError):
    def __init__(self, detail: str = "not found"):
        super().__init__(404, detail)


class TransportRateLimited(TransportError):
    def __init__(self, retry_after: float, detail: str = "rate limited"):
        super().__init__(429, detail)
        self.retry_after = max(0.5, float(retry_after))


class DiscordTransport(Protocol):
    async def send_channel(self, channel_id: int, message: dict, *, nonce: str) -> str: ...

    async def send_dm(self, discord_user_id: str, message: dict, *, nonce: str) -> str: ...

    async def is_member(self, discord_user_id: str) -> bool | None: ...


class MemberDirectory(Protocol):  # subset of LocalMemberBackend used here
    def prefs(self, user_id: str) -> dict: ...

    def is_muted(self, user_id: str, symbol: str | None) -> bool: ...

    def mention_targets(self, symbol: str | None) -> list[str]: ...

    def revoke(self, discord_user_id: str, now: datetime) -> None: ...


# ---- channel map -------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ChannelMap:
    channels: dict[str, int] = field(default_factory=dict)
    source: str = "unset"

    @classmethod
    def parse(cls, raw: str | dict, source: str) -> ChannelMap:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(data, dict):
            raise ValueError("channel map must be a JSON object {channel_key: channel_id}")
        out: dict[str, int] = {}
        for k, v in data.items():
            if k not in CHANNEL_KEYS:
                raise ValueError(f"unknown channel key {k!r}; expected one of {', '.join(CHANNEL_KEYS)}")
            s = str(v).strip()
            if not (s.isdigit() and 15 <= len(s) <= 21):
                raise ValueError(f"channel id for {k} must be a numeric Discord channel id")
            out[k] = int(s)
        return cls(out, source)

    @classmethod
    def from_file(cls, path: Path) -> ChannelMap:
        return cls.parse(path.read_text(), str(path))

    def get(self, key: str) -> int | None:
        return self.channels.get(key)

    def missing(self) -> list[str]:
        return [k for k in CHANNEL_KEYS if k not in self.channels]


# ---- stats -----------------------------------------------------------------------------------------------------
@dataclass
class DeliveryStats:
    delivery_last_run_at: str | None = None
    delivery_last_delivered_at: str | None = None
    delivery_last_error: str | None = None
    rate_limited_until: str | None = None
    delivered: int = 0
    deferred: int = 0
    failed: int = 0
    dead_letter: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def nonce_for(outbox_id: str) -> str:
    """Deterministic <=25-char nonce: Discord dedupes repeated sends with the same nonce (enforce_nonce)."""
    return "apx" + hashlib.sha256(outbox_id.encode()).hexdigest()[:22]


def intent_symbol(intent: dict) -> str | None:
    p = intent.get("payload") or {}
    if isinstance(p.get("signal"), dict):
        return p["signal"].get("symbol")
    if isinstance(p.get("assessment"), dict):
        return p["assessment"].get("symbol")
    return p.get("symbol")


class OutboxDelivery:
    def __init__(self, store: LocalStore, transport: DiscordTransport, component_secret: bytes, channels: ChannelMap,
                 members: MemberDirectory | None = None, *, clock: Callable[[], datetime] | None = None,
                 max_attempts: int = 8, batch: int = 20, max_mentions: int = 50, membership_ttl_seconds: int = 600):
        self.store = store
        self.transport = transport
        self.secret = component_secret
        self.channels = channels
        self.members = members
        self.clock = clock or (lambda: datetime.now(UTC))
        self.max_attempts = max_attempts
        self.batch = batch
        self.max_mentions = max_mentions
        self.membership_ttl = timedelta(seconds=membership_ttl_seconds)
        self.stats = DeliveryStats()
        self._paused_until: datetime | None = None
        self._membership: dict[str, tuple[bool | None, datetime]] = {}

    # ------------------------------------------------------------------ loop
    async def run(self, stop: asyncio.Event, interval: float = 5.0) -> None:
        while not stop.is_set():
            try:
                await self.deliver_due()
            except Exception as exc:
                self.stats.delivery_last_error = type(exc).__name__
                log.warning("delivery pass failed: %s", type(exc).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval)

    async def deliver_due(self, now: datetime | None = None) -> dict[str, int]:
        now = now or self.clock()
        counts = {"delivered": 0, "deferred": 0, "failed": 0, "dead_letter": 0, "rate_limited": 0}
        self.stats.delivery_last_run_at = iso_utc(now)
        if self._paused_until and now < self._paused_until:
            return counts
        self._paused_until = None
        self.stats.rate_limited_until = None
        for row in self.store.due_outbox(now, self.batch):
            outcome = await self._deliver_one(row, now)
            counts[outcome] += 1
            if outcome == "rate_limited":
                break
        self.stats.delivered += counts["delivered"]
        self.stats.deferred += counts["deferred"]
        self.stats.failed += counts["failed"] + counts["rate_limited"]
        self.stats.dead_letter += counts["dead_letter"]
        if counts["delivered"]:
            self.stats.delivery_last_delivered_at = iso_utc(now)
        return counts

    # ------------------------------------------------------------------ one row
    def _ack(self, row: OutboxRow, status: str, now: datetime, **kw) -> str:
        state = self.store.ack_outbox(row.id, status, now, max_attempts=kw.pop("max_attempts", self.max_attempts), **kw)
        if status == "delivered":
            return "delivered"
        if state == "deferred":
            return "deferred"
        if state == "dead_letter":
            log.warning("outbox item dead-lettered kind=%s attempts>=%d", row.kind, self.max_attempts)
            return "dead_letter"
        return "failed"

    async def _deliver_one(self, row: OutboxRow, now: datetime) -> str:
        intent = row.payload
        try:
            msg = clamp_message(render(intent, self.secret).to_discord())
        except Exception as exc:
            return self._ack(row, "failed", now, error=f"RENDER_ERROR:{type(exc).__name__}")
        nonce = nonce_for(row.id)
        if row.route.get("type") == "DM":
            return await self._dm(row, intent, msg, nonce, now)
        return await self._channel(row, intent, msg, nonce, now)

    async def _dm(self, row: OutboxRow, intent: dict, msg: dict, nonce: str, now: datetime) -> str:
        uid = row.route.get("user_id") or row.user_id or ""
        try:
            prefs = self.members.prefs(uid) if self.members else self.store.member_prefs(uid)
        except OwnershipError:
            return self._ack(row, "dm_blocked", now, error="UNKNOWN_MEMBER")
        if prefs["revoked"] or not prefs["dm_opt_in"] or not prefs.get("discord_user_id"):
            return self._ack(row, "dm_blocked", now)  # held privately; shown in the member's next ephemeral reply
        sym = intent_symbol(intent)
        if self.members and self.members.is_muted(uid, sym):
            return self._ack(row, "dm_blocked", now)
        duid = str(prefs["discord_user_id"])
        if await self._is_member(duid, now) is False:
            if self.members:
                self.members.revoke(duid, now)
            else:
                self.store.revoke_member(duid, now)
            return self._ack(row, "dm_blocked", now, http_status=404)
        msg = {**msg, "allowed_mentions": {"parse": []}}
        try:
            mid = await self.transport.send_dm(duid, msg, nonce=nonce)
        except (TransportForbidden, TransportNotFound) as exc:
            return self._ack(row, "dm_blocked", now, http_status=exc.status)
        except TransportRateLimited as exc:
            return self._rate_limited(row, exc, now)
        except TransportError as exc:
            return self._ack(row, "failed", now, http_status=exc.status, error=f"HTTP_{exc.status}")
        except Exception as exc:
            return self._ack(row, "failed", now, error=type(exc).__name__)
        return self._ack(row, "delivered", now, discord_message_id=str(mid))

    async def _channel(self, row: OutboxRow, intent: dict, msg: dict, nonce: str, now: datetime) -> str:
        if row.kind in PRIVATE_KINDS:  # defensive: personal payloads never go to a shared channel
            return self._ack(row, "failed", now, error="PRIVATE_KIND_ON_CHANNEL", max_attempts=1)
        key = row.route.get("channel_key", "")
        cid = self.channels.get(key)
        if cid is None:
            return self._ack(row, "failed", now, retry_after=UNCONFIGURED_RETRY_SECONDS,
                             error=f"CHANNEL_NOT_CONFIGURED:{key}")
        msg = self._with_mentions(row, intent, msg)
        try:
            mid = await self.transport.send_channel(cid, msg, nonce=nonce)
        except TransportRateLimited as exc:
            return self._rate_limited(row, exc, now)
        except TransportError as exc:
            return self._ack(row, "failed", now, http_status=exc.status, error=f"HTTP_{exc.status}")
        except Exception as exc:
            return self._ack(row, "failed", now, error=type(exc).__name__)
        return self._ack(row, "delivered", now, discord_message_id=str(mid))

    def _rate_limited(self, row: OutboxRow, exc: TransportRateLimited, now: datetime) -> str:
        self._ack(row, "failed", now, http_status=429, retry_after=exc.retry_after, error="HTTP_429")
        self._paused_until = now + timedelta(seconds=exc.retry_after)
        self.stats.rate_limited_until = iso_utc(self._paused_until)
        log.info("discord rate limit: pausing delivery for %.1fs", exc.retry_after)
        return "rate_limited"

    def _with_mentions(self, row: OutboxRow, intent: dict, msg: dict) -> dict:
        """@mention ONLY members who explicitly opted in (never @everyone/@here/roles)."""
        if (self.members is None or row.kind not in MENTION_KINDS or row.priority not in ("HIGH", "URGENT")
                or row.route.get("channel_key") not in MENTION_CHANNELS):
            return msg
        ids = [i for i in self.members.mention_targets(intent_symbol(intent)) if str(i).isdigit()][: self.max_mentions]
        if not ids:
            return msg
        head = "High-priority alert for members who opted in: " + " ".join(f"<@{i}>" for i in ids)
        return {**msg, "content": clip(head + "\n" + (msg.get("content") or ""), CONTENT_MAX),
                "allowed_mentions": {"parse": [], "users": [str(i) for i in ids]}}

    async def _is_member(self, duid: str, now: datetime) -> bool | None:
        cached = self._membership.get(duid)
        if cached and cached[1] > now:
            return cached[0]
        try:
            res = await self.transport.is_member(duid)
        except Exception:
            res = None
        self._membership[duid] = (res, now + self.membership_ttl)
        return res


# ---- in-memory transport (dry run and tests; also models Discord's nonce dedupe) ------------------------------
@dataclass
class MemoryTransport:
    sent: list[dict] = field(default_factory=list)
    dm_forbidden: set[str] = field(default_factory=set)
    members: set[str] | None = None
    script: list[Exception] = field(default_factory=list)  # raised (in order) by the next sends
    _by_nonce: dict[str, str] = field(default_factory=dict)
    _seq: int = 0
    calls: int = 0

    def _next(self, target: str, message: dict, nonce: str) -> str:
        self.calls += 1
        if self.script:
            raise self.script.pop(0)
        if nonce in self._by_nonce:
            return self._by_nonce[nonce]
        self._seq += 1
        mid = str(1_400_000_000_000_000_000 + self._seq)
        self._by_nonce[nonce] = mid
        self.sent.append({"target": target, "message": message, "nonce": nonce, "message_id": mid})
        return mid

    async def send_channel(self, channel_id: int, message: dict, *, nonce: str) -> str:
        return self._next(f"channel:{channel_id}", message, nonce)

    async def send_dm(self, discord_user_id: str, message: dict, *, nonce: str) -> str:
        if discord_user_id in self.dm_forbidden:
            self.calls += 1
            raise TransportForbidden("Cannot send messages to this user")
        return self._next(f"dm:{discord_user_id}", message, nonce)

    async def is_member(self, discord_user_id: str) -> bool | None:
        return None if self.members is None else discord_user_id in self.members
