"""Observation windows on the XNYS calendar (DST, holidays and half days come from `exchange_calendars`).

For session t:
* earliest observation = the regular close of t + FINALITY_BUFFER (90 minutes; 13:00 ET closes on half days move
  it to 14:30 ET). Earlier runs see a daily bar that may still be forming.
* deadline = the regular OPEN of the next session. A decision observed (and anchored) after it could already know
  the next session's open, which is the planned entry: it is MISSED, never back-filled.
* a decision is MATURE when the data end after the protocol's finality lag (2 sessions) has reached t + 22 sessions
  (2-session entry window + 20-session horizon); a trade still unresolved then waits up to EXTRA_SESSIONS more.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from apex_worker.market.calendar import MarketCalendar, OutOfCalendarRange
from apex_worker.research.realeq.protocol_v2 import PROTOCOL_V2

FINALITY_BUFFER = timedelta(minutes=90)
LABEL_SESSIONS = PROTOCOL_V2["chronology"]["label_sessions"]  # 22 = entry window 2 + horizon 20
FINALITY_LAG = PROTOCOL_V2["data"]["finality_lag_sessions"]  # 2
EXTRA_SESSIONS = 5  # an outcome with an unresolved (data-ended) trade waits at most this many more sessions
FIRST_SESSION = date.fromisoformat(PROTOCOL_V2["data"]["first_session"])  # session index 0 of the protocol axis


@dataclass(frozen=True)
class SessionWindow:
    session: date
    close_utc: datetime
    earliest_utc: datetime
    deadline_utc: datetime
    next_session: date
    early_close: bool

    def state(self, now: datetime) -> str:
        if now < self.earliest_utc:
            return "NOT_YET"
        return "OPEN" if now < self.deadline_utc else "CLOSED"


def window(cal: MarketCalendar, day: date) -> SessionWindow:
    s = cal.session(day)
    nxt = cal.next_session(day)
    return SessionWindow(day, s.close_utc, s.close_utc + FINALITY_BUFFER, cal.session(nxt).open_utc, nxt, s.early_close)


def due_windows(cal: MarketCalendar, start: date, now: datetime) -> list[SessionWindow]:
    """Sessions on/after `start` whose observation window has opened by `now` (ascending)."""
    now = now.astimezone(UTC)
    end = min(cal.session_date_for(now), cal.last_day)
    out = []
    for d in cal.sessions_between(start, end):
        try:
            w = window(cal, d)
        except OutOfCalendarRange:
            break
        if w.earliest_utc <= now:
            out.append(w)
    return out


def session_index(cal: MarketCalendar, day: date, first: date = FIRST_SESSION) -> int:
    """Position of `day` on the protocol's session axis (0 = 2016-01-04): the `t` of the historical study."""
    if not cal.is_session(day):
        raise ValueError(f"{day} is not an XNYS session")
    return len(cal.sessions_between(first, day)) - 1


def outcome_data_end(cal: MarketCalendar, now: datetime) -> date:
    """Latest session whose bars count as final: the latest completed session minus the protocol's finality lag."""
    end = cal.latest_completed_session(now)
    for _ in range(FINALITY_LAG):
        end = cal.previous_session(end)
    return end


def sessions_after(cal: MarketCalendar, start: date, end: date) -> int:
    return cal.count_sessions_after(start, end)


def is_mature(cal: MarketCalendar, decision_session: date, data_end: date) -> bool:
    return sessions_after(cal, decision_session, data_end) >= LABEL_SESSIONS


def maturity_session(cal: MarketCalendar, decision_session: date) -> date:
    """First data end at which the decision's labels may be computed."""
    return cal.add_sessions(decision_session, LABEL_SESSIONS)


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
