"""Rate limiting and free-tier quota governance.

TokenBucket enforces provider request ceilings (Alpaca Basic: 200 historical calls/minute; SEC fair access:
<=10 req/s, APEX default 2 req/s). QuotaTracker applies 70/85/95% budget thresholds: at 70% warn, at 85%
pause non-essential collectors, at 95% only position-protecting work proceeds. Nothing auto-upgrades a plan.
"""

from __future__ import annotations

import enum
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


class Priority(enum.IntEnum):
    ESSENTIAL = 0  # open-position monitoring, invalidations
    NORMAL = 1  # candidate refresh
    OPTIONAL = 2  # broad discovery, research extras


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class TokenBucket:
    rate_per_sec: float
    capacity: float
    clock: Callable[[], float] = time.monotonic
    _tokens: float = field(init=False)
    _last: float = field(init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._last = self.clock()

    def _refill(self) -> None:
        now = self.clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate_per_sec)
        self._last = now

    def try_acquire(self, n: float = 1.0) -> float:
        """Take n tokens if available and return 0, else return seconds to wait (nothing taken)."""
        with self._lock:
            self._refill()
            if self._tokens >= n:
                self._tokens -= n
                return 0.0
            return (n - self._tokens) / self.rate_per_sec

    def penalize(self, seconds: float) -> None:
        """Honour a provider Retry-After by draining the bucket for that long."""
        with self._lock:
            self._refill()
            self._tokens = min(self._tokens, -seconds * self.rate_per_sec)


@dataclass
class QuotaTracker:
    provider: str
    daily_limit: int
    used: int = 0
    day: str = ""
    thresholds: tuple[float, float, float] = (0.70, 0.85, 0.95)

    def roll(self, day: str) -> None:
        if day != self.day:
            self.day, self.used = day, 0

    @property
    def fraction(self) -> float:
        return self.used / self.daily_limit if self.daily_limit else 0.0

    def level(self) -> str:
        f = self.fraction
        if f >= self.thresholds[2]:
            return "CRITICAL"
        if f >= self.thresholds[1]:
            return "RESTRICTED"
        if f >= self.thresholds[0]:
            return "WARN"
        return "OK"

    def allow(self, priority: Priority) -> bool:
        lvl = self.level()
        if lvl == "CRITICAL":
            return priority is Priority.ESSENTIAL
        if lvl == "RESTRICTED":
            return priority <= Priority.NORMAL
        return True

    def record(self, day: str, n: int = 1, priority: Priority = Priority.NORMAL) -> None:
        self.roll(day)
        if not self.allow(priority):
            raise BudgetExceeded(
                f"{self.provider} budget {self.level()} ({self.used}/{self.daily_limit}); {priority.name} work paused"
            )
        self.used += n

    def snapshot(self) -> dict:
        return {
            "provider": self.provider,
            "day": self.day,
            "used": self.used,
            "limit": self.daily_limit,
            "fraction": round(self.fraction, 4),
            "level": self.level(),
        }
