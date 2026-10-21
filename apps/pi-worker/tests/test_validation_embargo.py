"""APEX-004: the walk-forward embargo is counted in NYSE sessions, not weekdays, so holidays never shorten it."""

import pandas as pd
import pytest

from apex_worker.market.calendar import default_calendar
from apex_worker.research.validation import _purge, embargo_limit


@pytest.mark.parametrize(
    ("test_start", "expected", "holiday"),
    [
        ("2025-12-01", "2025-11-21", "Thanksgiving 2025-11-27"),  # BDay(5) would give 11-24: only 4 sessions
        ("2026-04-08", "2026-03-31", "Good Friday 2026-04-03"),
        ("2026-06-24", "2026-06-16", "Juneteenth 2026-06-19"),
        ("2025-12-30", "2025-12-22", "Christmas 2025-12-25"),  # BDay(5) would give 12-23
        ("2026-07-08", "2026-06-30", "Independence Day observed 2026-07-03"),
        ("2026-01-21", "2026-01-13", "MLK Day 2026-01-19"),
        ("2026-03-11", "2026-03-04", "no holiday"),  # plain weeks: identical to five weekdays
    ],
)
def test_embargo_spans_exactly_five_sessions_across_holidays(test_start, expected, holiday):
    limit = embargo_limit(pd.Timestamp(test_start), 5)
    assert limit == pd.Timestamp(expected), holiday
    sessions = default_calendar().sessions_between(limit.date(), pd.Timestamp(test_start).date())
    assert len(sessions) == 6  # the limit session + 5 sessions of embargo before test_start (test_start itself excluded)
    assert sessions[-1] == pd.Timestamp(test_start).date()


def test_purge_drops_labels_that_end_inside_the_session_embargo():
    test_start = pd.Timestamp("2025-12-01")
    ev = pd.DataFrame({
        "decision_date": pd.to_datetime(["2025-11-03", "2025-11-05", "2025-11-07", "2025-11-10"]),
        "exit_date": pd.to_datetime(["2025-11-20", "2025-11-21", "2025-11-24", None]),
    })
    kept = _purge(ev, test_start, 5)
    # ends before 11-21 are kept; 11-21 and 11-24 fall inside the 5-session embargo (the weekday version kept 11-21);
    # an unfilled event ends at its decision date
    assert list(kept["exit_date"].fillna(kept["decision_date"]).dt.date.astype(str)) == ["2025-11-20", "2025-11-10"]
