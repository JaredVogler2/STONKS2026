from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apex_worker.catalysts.congress import (
    CAPABILITY,
    REQUIRED_COLUMNS,
    CongressImportError,
    import_csv,
    parse_amount_range,
)
from apex_worker.catalysts.models import ActionableAge, CatalystType, Direction, Source, is_available
from apex_worker.contracts import validate
from apex_worker.timeutil import ET

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "catalysts"
NOW = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def imported():
    return import_csv(
        (FIX / "congress_manual.csv").read_text(encoding="utf-8"), first_seen_at=NOW, is_demo=True
    )


def _alpha(imported):
    return next(r for r in imported.records if r.parsed["member_name"].startswith("Senator Example Alpha"))


def test_automatic_collection_is_disabled_with_explanation():
    assert CAPABILITY["automatic"] is False
    assert CAPABILITY["status"] == "DISABLED"
    assert "manual" in CAPABILITY["reason"].lower() and "45 days" in CAPABILITY["reason"]
    assert "never real-time" in CAPABILITY["reason"]


def test_politician_buy_aug1_disclosed_sep10_not_usable_before_disclosure(imported):
    rec = _alpha(imported)
    assert rec.catalyst_type is CatalystType.POLITICAL_DISCLOSURE
    assert rec.event_at.astimezone(ET).date().isoformat() == "2026-08-01"
    assert rec.published_at.astimezone(ET).date().isoformat() == "2026-09-10"
    # Not on the trade date, not at any instant before the disclosure date...
    probes = [
        datetime(2026, 8, 1, 4, 0, tzinfo=UTC),
        datetime(2026, 8, 1, 20, 0, tzinfo=UTC),
        datetime(2026, 8, 31, 12, 0, tzinfo=UTC),
        datetime(2026, 9, 10, 3, 59, 59, tzinfo=UTC),  # 23:59:59 ET on Sep 9
    ]
    for t in probes:
        assert not is_available(rec, t), t
    # ...and in backfill not even during the disclosure day (time of day unknown): from 00:00 ET Sep 11
    assert not is_available(rec, datetime(2026, 9, 10, 20, 0, tzinfo=UTC))
    assert rec.available_at == datetime(2026, 9, 11, 4, 0, tzinfo=UTC)
    assert is_available(rec, rec.available_at)
    assert rec.available_at >= rec.published_at > rec.event_at


def test_disclosure_is_lagged_context_only(imported):
    rec = _alpha(imported)
    assert rec.actionable_age is ActionableAge.LAGGED_DISCLOSURE
    assert rec.direction in (Direction.UNKNOWN, Direction.MIXED)
    assert "CONTEXT_ONLY_NOT_A_TRADE_TRIGGER" in rec.reason_codes
    assert "DISCLOSURE_LAG_40D" in rec.reason_codes
    assert rec.source is Source.SENATE_EFD and rec.legal_classification.value == "manual_entry"
    payload = rec.to_contract()
    validate("Catalyst", payload)
    assert payload["parsed"]["amount_min_usd"] == 15001 and payload["parsed"]["amount_max_usd"] == 50000


def test_all_rows_contract_valid_and_bad_rows_reported(imported):
    assert len(imported.records) == 4
    for rec in imported.records:
        validate("Catalyst", rec.to_contract())
        assert rec.is_demo
    codes = sorted(e.reason_code for e in imported.errors)
    assert codes == ["DISCLOSURE_BEFORE_TRANSACTION", "SOURCE_URL_INVALID", "TRANSACTION_TYPE_INVALID"]


def test_late_and_non_official_rows_flagged(imported):
    late = next(r for r in imported.records if "Gamma" in r.parsed["member_name"])
    assert "LATE_DISCLOSURE_GT_45D" in late.reason_codes and late.symbol is None
    assert "NO_TICKER_ON_REPORT" in late.reason_codes
    provider = next(r for r in imported.records if "Epsilon" in r.parsed["member_name"])
    assert provider.source is Source.MANUAL and provider.source_grade.value == "D_other"
    assert "SOURCE_NOT_OFFICIAL_HOST" in provider.reason_codes


def test_live_import_uses_first_seen_and_rejects_future_disclosures():
    header = ",".join(REQUIRED_COLUMNS)
    row = 'senate,Senator Fictional,2026-08-01,2026-09-10,DMSEMI1,Demo,purchase,"$1,001 - $15,000",https://efdsearch.senate.gov/x/'
    seen = datetime(2026, 9, 10, 18, 30, tzinfo=UTC)
    rec = import_csv(f"{header}\n{row}\n", first_seen_at=seen, mode="live").records[0]
    assert rec.available_at == seen
    early = import_csv(f"{header}\n{row}\n", first_seen_at=seen - timedelta(days=2), mode="live")
    assert not early.records and early.errors[0].reason_code == "DISCLOSURE_DATE_IN_FUTURE"


def test_missing_columns_rejected():
    with pytest.raises(CongressImportError, match="missing required columns"):
        import_csv("chamber,member_name\nsenate,X\n", first_seen_at=NOW)


def test_duplicate_rows_collapse():
    text = (FIX / "congress_manual.csv").read_text(encoding="utf-8")
    lines = text.splitlines()
    out = import_csv("\n".join([*lines, lines[1]]) + "\n", first_seen_at=NOW)
    assert [e.reason_code for e in out.errors].count("DUPLICATE_ROW") == 1


def test_amount_ranges():
    assert parse_amount_range("$1,001 - $15,000") == (1001, 15000)
    assert parse_amount_range("Over $50,000,000") == (50_000_000, None)
    assert parse_amount_range("n/a") == (None, None)
