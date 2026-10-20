"""Alpaca IEX WebSocket stream manager (bounded priority subscriptions, reconnect with backoff, backfill).

Data from this stream is IEX single-venue: it is labelled feed=iex / latency_class=live and NEVER
presented as consolidated NBBO. Isolated bad prints are quarantined by the shared QuoteCache validator.
After any disconnect the manager re-authenticates, re-subscribes and requests a REST backfill of latest
prints, and until fresh verified data arrives positions remain marked stale.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pandas as pd

from apex_worker.market.quote_cache import QuoteCache

IEX_URL = "wss://stream.data.alpaca.markets/v2/iex"
BASIC_SYMBOL_LIMIT = 30


class StreamAuthError(RuntimeError):
    pass


@dataclass
class StreamHealth:
    connected: bool = False
    authenticated: bool = False
    subscribed: list[str] = field(default_factory=list)
    last_message_at: datetime | None = None
    reconnects: int = 0
    last_error: str | None = None
    quarantined: int = 0
    messages: int = 0


def _ts(value: str) -> datetime:
    return pd.Timestamp(value).to_pydatetime().astimezone(UTC)


class IexStream:
    def __init__(self, key_id: str, secret_key: str, cache: QuoteCache, *, connect: Callable | None = None,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC), url: str = IEX_URL,
                 max_symbols: int = BASIC_SYMBOL_LIMIT, backfill: Callable[[list[str]], Awaitable[None]] | None = None,
                 sleep=asyncio.sleep):
        self._auth = {"action": "auth", "key": key_id, "secret": secret_key}
        self.cache = cache
        self._connect = connect
        self._now = now
        self.url = url
        self.max_symbols = max_symbols
        self._backfill = backfill
        self._sleep = sleep
        self.health = StreamHealth()
        self._desired: set[str] = set()
        self._ws = None

    def set_symbols(self, symbols: list[str]) -> None:
        if len(symbols) > self.max_symbols:
            raise ValueError(f"requested {len(symbols)} symbols exceeds plan limit {self.max_symbols}")
        self._desired = set(symbols)

    async def _send(self, msg: dict) -> None:
        await self._ws.send(json.dumps(msg))

    async def sync_subscriptions(self) -> None:
        if not self._ws or not self.health.authenticated:
            return
        current = set(self.health.subscribed)
        add, remove = sorted(self._desired - current), sorted(current - self._desired)
        if remove:
            await self._send({"action": "unsubscribe", "trades": remove, "quotes": remove})
        if add:
            await self._send({"action": "subscribe", "trades": add, "quotes": add})
        self.health.subscribed = sorted(self._desired)

    def handle(self, raw: str | bytes) -> None:
        now = self._now()
        msgs = json.loads(raw)
        if isinstance(msgs, dict):
            msgs = [msgs]
        for m in msgs:
            t = m.get("T")
            self.health.messages += 1
            self.health.last_message_at = now
            if t == "success" and m.get("msg") == "authenticated":
                self.health.authenticated = True
            elif t == "error":
                code = m.get("code")
                self.health.last_error = f"stream error {code}: {m.get('msg')}"
                if code in (401, 402, 404, 409):
                    raise StreamAuthError(self.health.last_error)
            elif t == "subscription":
                self.health.subscribed = sorted(set(m.get("trades", [])) | set(m.get("quotes", [])))
            elif t == "t":
                got = self.cache.ingest(symbol=m["S"], price=m.get("p"), bid=None, ask=None, provider="alpaca", feed="iex",
                                        latency_class="live", ts_exchange=_ts(m["t"]), ts_received=now, now=now,
                                        volume=m.get("s"), kind="trade")
                if got is None:
                    self.health.quarantined += 1
            elif t == "q":
                bid, ask = m.get("bp"), m.get("ap")
                mid = (bid + ask) / 2 if bid and ask else None
                got = self.cache.ingest(symbol=m["S"], price=mid, bid=bid, ask=ask, provider="alpaca", feed="iex",
                                        latency_class="live", ts_exchange=_ts(m["t"]), ts_received=now, now=now, kind="quote")
                if got is None:
                    self.health.quarantined += 1

    async def run(self, stop: asyncio.Event, max_backoff: float = 60.0) -> None:
        if self._connect is None:
            import websockets

            self._connect = websockets.connect
        attempt = 0
        while not stop.is_set():
            try:
                async with self._connect(self.url, max_size=2**20, ping_interval=20, ping_timeout=20) as ws:
                    self._ws = ws
                    self.health.connected = True
                    self.health.authenticated = False
                    self.health.subscribed = []
                    await self._send(self._auth)
                    async for raw in ws:
                        self.handle(raw)
                        if self.health.authenticated and set(self.health.subscribed) != self._desired:
                            await self.sync_subscriptions()
                            if attempt and self._backfill:
                                await self._backfill(sorted(self._desired))
                            attempt = 0
                        if stop.is_set():
                            break
            except StreamAuthError:
                self.health.connected = False
                raise
            except Exception as exc:  # noqa: BLE001 - network faults of any type trigger reconnect
                self.health.last_error = f"disconnected: {type(exc).__name__}"
            self.health.connected = False
            self.health.authenticated = False
            self._ws = None
            if stop.is_set():
                break
            attempt += 1
            self.health.reconnects += 1
            await self._sleep(min(max_backoff, 2 ** min(attempt, 6)) * (0.5 + random.random() / 2))  # noqa: S311
