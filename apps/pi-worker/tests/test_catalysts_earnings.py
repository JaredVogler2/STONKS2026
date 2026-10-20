from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from apex_worker.catalysts.earnings import (
    EarningsCalendar,
    EarningsEntry,
    EntryStatus,
    ReleaseTiming,
    earnings_status,
    entry_to_catalyst,
    estimate_next_from_8k,
    explain_earnings_status,
    first_usable_session,
    reported_from_8k,
    usable_at_close,
)
from apex_worker.catalysts.models import ActionableAge, CatalystType, is_available
from apex_worker.catalysts.sec import filing_to_catalyst, parse_submissions
from apex_worker.contracts import validate
from apex_worker.market.calendar import default_calendar
from apex_worker.timeutil import et_datetime

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "catalysts"
NOW = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)
START = date(2026, 10, 20)


@pytest.fixture(scope="module")
def cal():
    return default_calendar()


@pytest.fixture(scope="module")
def filings():
    return parse_submissions((FIX / "edgar_submissions_CIK0009990001.json").read_bytes()).filings


@pytest.fixture
def manual():
    return EarningsCalendar.from_manual_json((FIX / "earnings_manual.json").read_text())


def _with_history(manual, filings, cal):
    for entry in reported_from_8k("DMSEMI1", filings, cal):
        manual.add(entry)
    return manual


# --- missing data is UNKNOWN, never "no earnings" -------------------------------------------------
def test_missing_schedule_is_unknown(manual, cal):
    assert earnings_status("DMLOG3", START, 20, manual, market_calendar=cal) == "UNKNOWN"
    assert earnings_status("DMLOG3", START, 20, None, market_calendar=cal) == "UNKNOWN"
    assert earnings_status("DMSEMI1", START, 20, EarningsCalendar(), market_calendar=cal) == "UNKNOWN"
    detail = explain_earnings_status("DMLOG3", START, 20, manual, market_calendar=cal)
    assert detail.reason_codes == ("NO_SCHEDULE_DATA_FOR_SYMBOL",)


def test_confirmed_release_within_horizon(manual, cal):
    assert earnings_status("DMBANK2", START, 20, manual, market_calendar=cal) == "WITHIN_HORIZON"
    # 2 sessions from Tue Oct 20 ends Thu Oct 22; the Fri Oct 23 BEFORE-OPEN release still gaps the position
    detail = explain_earnings_status("DMBANK2", START, 2, manual, market_calendar=cal)
    assert (
        detail.status.value == "WITHIN_HORIZON"
        and "PRE_OPEN_RELEASE_AFTER_HORIZON_GAP_RISK" in detail.reason_codes
    )
    # 1 session (ends Wed Oct 21): confirmed next release is after the horizon and last release is known
    assert (
        earnings_status("DMBANK2", START, 1, manual, market_calendar=cal) == "NONE_WITHIN_HORIZON_CONFIRMED"
    )


def test_after_close_release_on_day_after_horizon_is_outside(cal):
    c = EarningsCalendar(
        [
            EarningsEntry(
                "DMX", date(2026, 7, 22), EntryStatus.REPORTED, "manual", known_since=NOW - timedelta(days=90)
            ),
            EarningsEntry(
                "DMX", date(2026, 10, 23), EntryStatus.CONFIRMED, "manual", ReleaseTiming.AMC, known_since=NOW
            ),
        ]
    )
    assert earnings_status("DMX", START, 2, c, market_calendar=cal) == "NONE_WITHIN_HORIZON_CONFIRMED"
    assert earnings_status("DMX", START, 3, c, market_calendar=cal) == "WITHIN_HORIZON"


def test_expected_estimate_within_horizon_counts_as_risk(manual, cal):
    assert earnings_status("DMBIO4", START, 20, manual, market_calendar=cal) == "WITHIN_HORIZON"
    # short horizon ends before the +-7 day window: still not "none" because nothing is confirmed
    assert earnings_status("DMBIO4", START, 5, manual, market_calendar=cal) == "UNKNOWN"


def test_none_within_horizon_requires_confirmed_next_and_known_last_release(manual, filings, cal):
    assert (
        earnings_status("DMSEMI1", START, 20, manual, market_calendar=cal) == "UNKNOWN"
    )  # last release unknown
    full = _with_history(manual, filings, cal)
    detail = explain_earnings_status("DMSEMI1", START, 20, full, market_calendar=cal)
    assert detail.status.value == "NONE_WITHIN_HORIZON_CONFIRMED"
    assert detail.entries[0].day == date(2027, 2, 3)


def test_point_in_time_schedule_knowledge(manual, filings, cal):
    full = _with_history(manual, filings, cal)
    before_confirmation = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)  # confirmation known only from Oct 15
    assert (
        earnings_status("DMSEMI1", START, 20, full, market_calendar=cal, as_of=before_confirmation)
        == "UNKNOWN"
    )
    assert (
        earnings_status("DMSEMI1", START, 20, full, market_calendar=cal, as_of=NOW)
        == "NONE_WITHIN_HORIZON_CONFIRMED"
    )


def test_confirmed_date_too_far_from_last_release_is_unknown(cal):
    c = EarningsCalendar(
        [
            EarningsEntry(
                "DMX",
                date(2026, 4, 20),
                EntryStatus.REPORTED,
                "manual",
                known_since=NOW - timedelta(days=200),
            ),
            EarningsEntry(
                "DMX", date(2027, 1, 20), EntryStatus.CONFIRMED, "manual", known_since=NOW - timedelta(days=1)
            ),
        ]
    )
    assert earnings_status("DMX", START, 20, c, market_calendar=cal) == "UNKNOWN"


def test_estimates_alone_never_confirm_absence(filings, cal):
    est = estimate_next_from_8k("DMSEMI1", filings, cal=cal)
    c = EarningsCalendar([est, *reported_from_8k("DMSEMI1", filings, cal)])
    assert earnings_status("DMSEMI1", START, 20, c, market_calendar=cal) == "UNKNOWN"


# --- cadence estimate ---------------------------------------------------------------------------
def test_cadence_estimate_from_item_202_history(filings, cal):
    reported = reported_from_8k("DMSEMI1", filings, cal)
    assert len(reported) == 8
    assert all(e.timing is ReleaseTiming.AMC for e in reported)
    est = estimate_next_from_8k("DMSEMI1", filings, cal=cal)
    assert est is not None and est.status is EntryStatus.EXPECTED
    assert est.day == date(2027, 2, 3) and est.uncertainty_days >= 7
    assert "NOT a confirmed date" in (est.note or "")
    assert est.known_since == reported[-1].known_since


def test_cadence_estimate_needs_history(filings, cal):
    assert (
        estimate_next_from_8k("DMSEMI1", [f for f in filings if f.filing_date > date(2026, 7, 1)], cal=cal)
        is None
    )


# --- after-hours point in time ------------------------------------------------------------------
def test_after_hours_release_available_next_session_not_prior_close(filings, cal):
    meta = next(f for f in filings if f.accession_number == "0009990001-26-000041")
    assert meta.acceptance_at == et_datetime(date(2026, 10, 14), 16, 5) + timedelta(seconds=31)
    for mode, seen in (("backfill", NOW), ("live", meta.acceptance_at + timedelta(seconds=30))):
        rec = filing_to_catalyst(meta, first_seen_at=seen, mode=mode)
        assert rec.catalyst_type is CatalystType.EARNINGS_RELEASE
        close_oct14 = cal.session(date(2026, 10, 14)).close_utc
        assert not is_available(rec, close_oct14)
        assert not usable_at_close(rec.available_at, date(2026, 10, 14), cal)
        assert first_usable_session(rec.available_at, cal) == date(2026, 10, 15)
        assert usable_at_close(rec.available_at, date(2026, 10, 15), cal)


def test_backfill_lag_pushes_late_session_release_past_close(cal):
    accepted = et_datetime(date(2026, 10, 14), 15, 50)
    available = accepted + timedelta(minutes=15)  # SEC backfill lag
    assert first_usable_session(available, cal) == date(2026, 10, 15)
    assert first_usable_session(et_datetime(date(2026, 9, 28), 8, 1), cal) == date(2026, 9, 28)  # pre-market


def test_early_close_and_weekend(cal):
    # 2026-11-27 (day after Thanksgiving) closes 13:00 ET
    assert first_usable_session(et_datetime(date(2026, 11, 27), 13, 30), cal) == date(2026, 11, 30)
    assert first_usable_session(et_datetime(date(2026, 11, 27), 12, 30), cal) == date(2026, 11, 27)
    assert first_usable_session(et_datetime(date(2026, 10, 17), 10, 0), cal) == date(2026, 10, 19)  # Saturday


# --- catalysts ------------------------------------------------------------------------------------
def test_manual_entries_to_scheduled_catalysts(manual):
    for sym in ("DMSEMI1", "DMBANK2", "DMBIO4"):
        for entry in manual.entries_for(sym):
            if entry.status is EntryStatus.REPORTED:
                with pytest.raises(ValueError):
                    entry_to_catalyst(entry, first_seen_at=NOW)
                continue
            rec = entry_to_catalyst(entry, first_seen_at=NOW, is_demo=True)
            validate("Catalyst", rec.to_contract())
            assert rec.catalyst_type is CatalystType.EARNINGS_SCHEDULED
            assert rec.direction.value == "unknown"
            if entry.day > date(2026, 10, 20):
                assert rec.actionable_age is ActionableAge.SCHEDULED_FUTURE
    bio = entry_to_catalyst(manual.entries_for("DMBIO4")[0], first_seen_at=NOW)
    assert "EXPECTED_DATE_ESTIMATE" in bio.reason_codes and bio.source_grade.value == "E_unverified"


def test_manual_json_rejects_non_https_source():
    with pytest.raises(ValueError, match="https"):
        EarningsCalendar.from_manual_json(
            {
                "entries": [
                    {
                        "symbol": "DMX",
                        "date": "2026-11-01",
                        "status": "confirmed",
                        "source_url": "http://x.example",
                    }
                ]
            }
        )
