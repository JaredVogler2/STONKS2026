"""Time helpers. All instants are timezone-aware UTC internally; ET is display/market-local only."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass
class ReplayClock:
    """Deterministic, manually advanced clock used by replay and tests. Never sleeps."""

    current: datetime = field(default_factory=lambda: datetime(2026, 1, 2, 14, 30, tzinfo=UTC))

    def __post_init__(self) -> None:
        self.current = ensure_utc(self.current)

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> datetime:
        if delta < timedelta(0):
            raise ValueError("replay clock cannot move backwards")
        self.current = self.current + delta
        return self.current

    def set(self, instant: datetime) -> datetime:
        instant = ensure_utc(instant)
        if instant < self.current:
            raise ValueError("replay clock cannot move backwards")
        self.current = instant
        return self.current


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetimes are not allowed; supply a timezone-aware instant")
    return dt.astimezone(UTC)


def iso_utc(dt: datetime) -> str:
    """Canonical contract format: YYYY-MM-DDTHH:MM:SS[.ffffff]Z."""
    dt = ensure_utc(dt)
    if dt.microsecond:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"expected UTC timestamp ending in Z, got {value!r}")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    return parsed.astimezone(UTC)


def to_et(dt: datetime) -> datetime:
    return ensure_utc(dt).astimezone(ET)


def et_label(dt: datetime) -> str:
    """Human label with explicit zone abbreviation (EDT/EST), e.g. '2026-10-20 09:31 EDT'."""
    local = to_et(dt)
    return local.strftime("%Y-%m-%d %H:%M ") + (local.tzname() or "ET")


def et_datetime(day: date, hour: int, minute: int = 0) -> datetime:
    """Construct an instant from an exchange-local wall time, DST-aware."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET).astimezone(UTC)


def age_seconds(then: datetime | None, now: datetime) -> int | None:
    if then is None:
        return None
    return max(0, int((ensure_utc(now) - ensure_utc(then)).total_seconds()))
