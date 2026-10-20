"""Expected GitHub Actions schedules for the APEX model workflows, and missed-run (SLA) arithmetic.

The workflows declare `schedule: - cron: ... timezone: "America/New_York"` (documented by GitHub; see
docs/AUTONOMOUS_TRAINING.md), so the wall-clock time in New York is fixed across DST and the UTC instant moves by one
hour twice a year. GitHub cron is best-effort: runs can be delayed at busy times or dropped, so the dashboard / Pi must
alert when a run has not been *reported* within the grace window after its expected time. These pure functions are
the single source for "next expected execution", "missed" and the monthly run counts used by the budget forecast.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Schedule:
    workflow: str  # training_runs.workflow value
    weekdays: frozenset[int]  # Monday=0 ... Sunday=6 (New York local date)
    at: time  # New York wall-clock time
    grace: timedelta  # how long after the expected time before the run counts as missed

    def _local_dates(self, start: date, end: date) -> list[date]:
        out, d = [], start
        while d <= end:
            if d.weekday() in self.weekdays:
                out.append(d)
            d += timedelta(days=1)
        return out

    def occurrences(self, start: datetime, end: datetime) -> list[datetime]:
        """Expected fire instants (UTC) with start <= t < end."""
        _aware(start, end)
        lo = start.astimezone(ET).date() - timedelta(days=1)
        hi = end.astimezone(ET).date() + timedelta(days=1)
        out = []
        for d in self._local_dates(lo, hi):
            t = datetime.combine(d, self.at, tzinfo=ET).astimezone(UTC)
            if start <= t < end:
                out.append(t)
        return out

    def previous(self, now: datetime) -> datetime:
        _aware(now)
        hits = self.occurrences(now - timedelta(days=8), now + timedelta(microseconds=1))
        return hits[-1]

    def next(self, now: datetime) -> datetime:
        _aware(now)
        return self.occurrences(now + timedelta(microseconds=1), now + timedelta(days=8))[0]


# Keep in sync with .github/workflows/*.yml (tests/test_release_workflows.py asserts it).
WEEKLY_TRAIN = Schedule("train_weekly", frozenset({0}), time(19, 17), timedelta(hours=24))
WEEKDAY_DRIFT = Schedule("drift", frozenset({0, 1, 2, 3, 4}), time(20, 23), timedelta(hours=6))
SCHEDULES = {s.workflow: s for s in (WEEKLY_TRAIN, WEEKDAY_DRIFT)}


def month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Calendar month in UTC (GitHub's included minutes reset per billing cycle; calendar month is the approximation)."""
    _aware(now)
    u = now.astimezone(UTC)
    start = datetime(u.year, u.month, 1, tzinfo=UTC)
    end = datetime(u.year + (u.month == 12), u.month % 12 + 1, 1, tzinfo=UTC)
    return start, end


def runs_in_month(schedule: Schedule, now: datetime) -> int:
    start, end = month_bounds(now)
    return len(schedule.occurrences(start, end))


def remaining_runs_in_month(schedule: Schedule, now: datetime) -> int:
    """Scheduled runs strictly after `now` and before the month ends."""
    _, end = month_bounds(now)
    return len(schedule.occurrences(now + timedelta(microseconds=1), end))


def sla_status(schedule: Schedule, last_reported_at: datetime | None, now: datetime) -> dict:
    """Missed when the most recent expected run (older than the grace window) has no report at or after it.

    A run "reported" includes skipped_budget / skipped_holiday reports: those are deliberate and visible, not missed.
    GitHub may start a scheduled run late, so a report up to the grace window after the expected time still counts.
    """
    _aware(now)
    expected = schedule.previous(now)
    due = expected + schedule.grace
    if now < due:  # still inside the grace window for the latest slot: judge the slot before it
        expected = schedule.previous(expected - timedelta(microseconds=1))
        due = expected + schedule.grace
    # small tolerance: a manual run shortly before the slot also satisfies it
    ok = last_reported_at is not None and last_reported_at >= expected - timedelta(hours=1)
    return {
        "workflow": schedule.workflow,
        "expected_at": _iso(expected),
        "due_by": _iso(due),
        "next_expected_at": _iso(schedule.next(now)),
        "last_reported_at": _iso(last_reported_at) if last_reported_at else None,
        "missed": not ok,
        "overdue_minutes": round((now - due).total_seconds() / 60) if not ok else 0,
    }


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _aware(*ts: datetime) -> None:
    for t in ts:
        if t.tzinfo is None:
            raise ValueError("timezone-aware datetimes required")
