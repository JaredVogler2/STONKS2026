"""Shared quote cache: ONE validated observation per symbol feeds every member's evaluation.

Fan-out is O(members) in memory with zero extra provider calls. Each entry carries provider, feed,
exchange timestamp, ingest timestamp, delay and validation level. Stale data is kept (last-good) but is
always marked stale; it is never presented as current.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from apex_worker.market.validation import ValidationResult, validate_price
from apex_worker.timeutil import iso_utc


@dataclass
class CachedQuote:
    symbol: str
    price: float
    bid: float | None
    ask: float | None
    provider: str
    feed: str
    latency_class: str
    ts_exchange: datetime
    ts_received: datetime
    delayed_by_seconds: int
    validation: ValidationResult
    volume: int | None = None
    kind: str = "trade"

    def age_seconds(self, now: datetime) -> int:
        return max(0, int((now - self.ts_exchange).total_seconds()))

    def to_contract(self, now: datetime, is_demo: bool) -> dict:
        obs = {
            "symbol": self.symbol,
            "kind": self.kind,
            "price": f"{self.price:.4f}".rstrip("0").rstrip("."),
            "provenance": {
                "provider": self.provider,
                "feed": self.feed,
                "latency_class": self.latency_class,
                "ts_exchange": iso_utc(self.ts_exchange),
                "ts_received": iso_utc(self.ts_received),
                "delayed_by_seconds": self.delayed_by_seconds,
                "is_demo": is_demo,
            },
            "validation_level": self.validation.level,
            "quality_flags": list(self.validation.flags),
            "staleness_seconds": self.age_seconds(now),
        }
        if self.bid is not None:
            obs["bid"] = f"{self.bid:.4f}".rstrip("0").rstrip(".")
        if self.ask is not None:
            obs["ask"] = f"{self.ask:.4f}".rstrip("0").rstrip(".")
        if self.volume is not None:
            obs["volume"] = int(self.volume)
        return obs


@dataclass
class QuoteCache:
    reference_prices: dict[str, float] = field(default_factory=dict)
    _quotes: dict[str, CachedQuote] = field(default_factory=dict)
    _quarantine: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    _listeners: list[Callable[[CachedQuote], None]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def subscribe(self, fn: Callable[[CachedQuote], None]) -> None:
        self._listeners.append(fn)

    def ingest(
        self,
        *,
        symbol: str,
        price: float | None,
        bid: float | None,
        ask: float | None,
        provider: str,
        feed: str,
        latency_class: str,
        ts_exchange: datetime | None,
        ts_received: datetime,
        now: datetime,
        delayed_by_seconds: int = 0,
        volume: int | None = None,
        kind: str = "trade",
        consolidated_reference: float | None = None,
        halted: bool = False,
    ) -> CachedQuote | None:
        with self._lock:
            last = self._quotes.get(symbol)
            res = validate_price(
                price=price,
                bid=bid,
                ask=ask,
                ts_exchange=ts_exchange,
                ts_received=ts_received,
                now=now,
                last_accepted_price=last.price if last else None,
                last_accepted_ts=last.ts_exchange if last else None,
                reference_price=self.reference_prices.get(symbol),
                consolidated_reference=consolidated_reference,
                halted=halted,
                volume=volume,
            )
            if res.quarantined or res.level == 0:
                self._quarantine.append((symbol, res.flags))
                self._quarantine = self._quarantine[-500:]
                return None
            q = CachedQuote(
                symbol,
                float(price),
                bid,
                ask,
                provider,
                feed,
                latency_class,
                ts_exchange,
                ts_received,  # type: ignore[arg-type]
                delayed_by_seconds,
                res,
                volume,
                kind,
            )
            self._quotes[symbol] = q
        for fn in self._listeners:
            fn(q)
        return q

    def get(self, symbol: str) -> CachedQuote | None:
        return self._quotes.get(symbol)

    def all(self) -> dict[str, CachedQuote]:
        return dict(self._quotes)

    def quarantined(self) -> list[tuple[str, tuple[str, ...]]]:
        return list(self._quarantine)

    def stale_symbols(self, now: datetime, sla_seconds: int) -> list[str]:
        return sorted(s for s, q in self._quotes.items() if q.age_seconds(now) > sla_seconds)
