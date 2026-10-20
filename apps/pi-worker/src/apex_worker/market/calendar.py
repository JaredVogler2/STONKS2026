"""NYSE (XNYS) exchange session calendar.

Backed by the pinned `exchange_calendars` library: holidays, observed holidays and early
closes (half days) come from its rule set rather than "every weekday 09:30-16:00" guesses.
"""

from __future__ import annotations

import bisect
import enum
import functools
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from apex_worker.timeutil import ET, ensure_utc, iso_utc

CALENDAR_START = date(2016, 1, 1)
CALENDAR_END = date(2028, 12, 31)
PREMARKET_START_ET = time(4, 0)
AFTER_HOURS_END_ET = time(20, 0)
AFTER_HOURS_END_EARLY_CLOSE_ET = time(17, 0)


class MarketPhase(enum.StrEnum):
    PRE = "PRE"
    OPEN = "OPEN"
    AFTER = "AFTER"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class Session:
    day: date
    open_utc: datetime
    close_utc: datetime
    early_close: bool


@dataclass(frozen=True)
class PhaseInfo:
    phase: MarketPhase
    session: Session | None
    next_open_utc: datetime | None
    next_close_utc: datetime | None


class OutOfCalendarRange(ValueError):
    pass


class MarketCalendar:
    """Immutable, bisect-indexed view of XNYS sessions."""

    def __init__(self, sessions: list[Session]):
        if not sessions:
            raise ValueError("calendar requires sessions")
        self._sessions = sessions
        self._days = [s.day for s in sessions]
        self._by_day = {s.day: s for s in sessions}

    # ---- construction -------------------------------------------------
    @classmethod
    def from_exchange_calendars(cls, start: date = CALENDAR_START, end: date = CALENDAR_END) -> MarketCalendar:
        import exchange_calendars as xc  # heavy import kept local

        cal = xc.get_calendar("XNYS", start=start.isoformat(), end=end.isoformat())
        sched = cal.schedule
        early = {ts.date() for ts in cal.early_closes}
        sessions: list[Session] = []
        for label, row in sched.iterrows():
            day = label.date()
            sessions.append(
                Session(
                    day=day,
                    open_utc=row["open"].to_pydatetime().astimezone(UTC),
                    close_utc=row["close"].to_pydatetime().astimezone(UTC),
                    early_close=day in early,
                )
            )
        return cls(sessions)

    # ---- range ---------------------------------------------------------
    @property
    def first_day(self) -> date:
        return self._days[0]

    @property
    def last_day(self) -> date:
        return self._days[-1]

    def _check(self, day: date) -> None:
        if day < self._days[0] or day > self._days[-1]:
            raise OutOfCalendarRange(f"{day} outside calendar range {self._days[0]}..{self._days[-1]}")

    # ---- session queries -------------------------------------------------
    def is_session(self, day: date) -> bool:
        self._check(day)
        return day in self._by_day

    def session(self, day: date) -> Session:
        self._check(day)
        try:
            return self._by_day[day]
        except KeyError as exc:
            raise KeyError(f"{day} is not an XNYS session") from exc

    def sessions(self) -> list[Session]:
        return list(self._sessions)

    def next_session(self, day: date) -> date:
        self._check(day)
        idx = bisect.bisect_right(self._days, day)
        if idx >= len(self._days):
            raise OutOfCalendarRange(f"no session after {day}")
        return self._days[idx]

    def previous_session(self, day: date) -> date:
        self._check(day)
        idx = bisect.bisect_left(self._days, day) - 1
        if idx < 0:
            raise OutOfCalendarRange(f"no session before {day}")
        return self._days[idx]

    def sessions_between(self, start: date, end: date) -> list[date]:
        """Sessions with start <= day <= end."""
        lo = bisect.bisect_left(self._days, start)
        hi = bisect.bisect_right(self._days, end)
        return self._days[lo:hi]

    def count_sessions_after(self, start: date, end: date) -> int:
        """Number of sessions in (start, end]; horizon arithmetic is in trading sessions."""
        if end <= start:
            return 0
        return bisect.bisect_right(self._days, end) - bisect.bisect_right(self._days, start)

    def add_sessions(self, day: date, n: int) -> date:
        """The n-th session after `day` (n >= 1), or the session on/after day when n == 0."""
        self._check(day)
        if n < 0:
            raise ValueError("n must be >= 0")
        if n == 0:
            idx = bisect.bisect_left(self._days, day)
        else:
            idx = bisect.bisect_right(self._days, day) + n - 1
        if idx >= len(self._days):
            raise OutOfCalendarRange(f"{n} sessions after {day} exceeds calendar range")
        return self._days[idx]

    # ---- instants ---------------------------------------------------------
    def session_date_for(self, instant: datetime) -> date:
        """Exchange-local calendar date of an instant (not necessarily a session)."""
        return ensure_utc(instant).astimezone(ET).date()

    def latest_completed_session(self, instant: datetime) -> date:
        """Most recent session whose regular close is at or before `instant`."""
        instant = ensure_utc(instant)
        day = self.session_date_for(instant)
        if day > self._days[-1]:
            raise OutOfCalendarRange(f"{instant} beyond calendar")
        if day in self._by_day and self._by_day[day].close_utc <= instant:
            return day
        return self.previous_session(day)

    def phase(self, instant: datetime) -> PhaseInfo:
        instant = ensure_utc(instant)
        day = self.session_date_for(instant)
        self._check(day)
        sess = self._by_day.get(day)
        nxt_day = self.next_session(day) if day < self._days[-1] else None
        nxt = self._by_day.get(nxt_day) if nxt_day else None
        if sess is not None:
            pre_start = datetime.combine(day, PREMARKET_START_ET, ET).astimezone(UTC)
            after_end_t = AFTER_HOURS_END_EARLY_CLOSE_ET if sess.early_close else AFTER_HOURS_END_ET
            after_end = datetime.combine(day, after_end_t, ET).astimezone(UTC)
            if instant < sess.open_utc:
                ph = MarketPhase.PRE if instant >= pre_start else MarketPhase.CLOSED
                return PhaseInfo(ph, sess, sess.open_utc, sess.close_utc)
            if instant < sess.close_utc:
                return PhaseInfo(MarketPhase.OPEN, sess, sess.open_utc, sess.close_utc)
            ph = MarketPhase.AFTER if instant < after_end else MarketPhase.CLOSED
            return PhaseInfo(ph, sess, nxt.open_utc if nxt else None, nxt.close_utc if nxt else None)
        return PhaseInfo(MarketPhase.CLOSED, None, nxt.open_utc if nxt else None, nxt.close_utc if nxt else None)

    def is_open(self, instant: datetime) -> bool:
        return self.phase(instant).phase is MarketPhase.OPEN

    def session_close_after_sessions(self, day: date, n: int) -> datetime:
        return self.session(self.add_sessions(day, n)).close_utc

    # ---- export -------------------------------------------------------------
    def export(self) -> dict:
        """Serialized calendar consumed by the web dashboard (single source of truth)."""
        return {
            "calendar": "XNYS",
            "source": "exchange_calendars",
            "first_session": self._days[0].isoformat(),
            "last_session": self._days[-1].isoformat(),
            "premarket_start_et": PREMARKET_START_ET.strftime("%H:%M"),
            "after_hours_end_et": AFTER_HOURS_END_ET.strftime("%H:%M"),
            "after_hours_end_early_close_et": AFTER_HOURS_END_EARLY_CLOSE_ET.strftime("%H:%M"),
            "sessions": [
                [s.day.isoformat(), iso_utc(s.open_utc), iso_utc(s.close_utc), 1 if s.early_close else 0]
                for s in self._sessions
            ],
        }

    @classmethod
    def from_export(cls, data: dict) -> MarketCalendar:
        from apex_worker.timeutil import parse_utc

        return cls(
            [
                Session(date.fromisoformat(d), parse_utc(o), parse_utc(c), bool(e))
                for d, o, c, e in data["sessions"]
            ]
        )


@functools.cache
def default_calendar() -> MarketCalendar:
    return MarketCalendar.from_exchange_calendars()


def sessions_until_expiry(cal: MarketCalendar, start: date, n: int) -> tuple[date, datetime]:
    """Session date and UTC close instant `n` trading sessions after `start`."""
    day = cal.add_sessions(start, n)
    return day, cal.session(day).close_utc


__all__ = [
    "MarketCalendar",
    "MarketPhase",
    "OutOfCalendarRange",
    "PhaseInfo",
    "Session",
    "default_calendar",
    "sessions_until_expiry",
]
