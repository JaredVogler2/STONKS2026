from __future__ import annotations

import json
import random
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from apex_worker.catalysts.http import HttpClientConfig, MemoryCache, PoliteHttpClient, TokenBucket
from apex_worker.catalysts.macro import (
    FredClient,
    SourceDisabledError,
    VintageSeries,
    load_release_calendar,
    release_to_catalyst,
)
from apex_worker.catalysts.models import ActionableAge, CatalystType, SourceGrade
from apex_worker.contracts import validate

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "catalysts"
NOW = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)
JUNE = date(2026, 6, 1)


@pytest.fixture(scope="module")
def series():
    return VintageSeries.from_alfred_json((FIX / "alfred_vintages_DMCPI.json").read_bytes(), "DMCPI")


@pytest.fixture(scope="module")
def series_0830():
    return VintageSeries.from_alfred_json(
        (FIX / "alfred_vintages_DMCPI.json").read_bytes(), "DMCPI", release_time_et=time(8, 30)
    )


def test_value_as_known_never_uses_later_revision(series):
    # June value first released 2026-07-15 (321.5), revised 2026-08-14 (322.1) and 2026-09-11 (322.0)
    first = series.value_as_of(JUNE, datetime(2026, 7, 20, 15, 0, tzinfo=UTC))
    assert first.value == Decimal("321.5") and first.reason == "FIRST_RELEASE"
    rev1 = series.value_as_of(JUNE, datetime(2026, 8, 20, 15, 0, tzinfo=UTC))
    assert rev1.value == Decimal("322.1") and rev1.reason == "REVISED_VINTAGE"
    latest = series.value_as_of(JUNE, NOW)
    assert latest.value == Decimal("322.0")


def test_not_yet_released_is_unavailable(series):
    out = series.value_as_of(JUNE, datetime(2026, 7, 10, tzinfo=UTC))
    assert out.value is None and not out.available and out.reason == "NOT_YET_RELEASED"


def test_release_day_without_time_is_conservative(series, series_0830):
    morning = datetime(2026, 7, 15, 13, 0, tzinfo=UTC)  # 09:00 ET on release day
    assert series.value_as_of(JUNE, morning).value is None  # time unknown -> only from next day
    assert series_0830.value_as_of(JUNE, morning).value == Decimal("321.5")
    pre = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)  # 08:00 ET, before the 08:30 release
    assert series_0830.value_as_of(JUNE, pre).value is None
    # before the 08:30 revision on 2026-08-14 the previous vintage still applies
    assert series_0830.value_as_of(JUNE, datetime(2026, 8, 14, 12, 0, tzinfo=UTC)).value == Decimal("321.5")


def test_missing_value_in_vintage_is_unavailable(series):
    out = series.value_as_of(date(2026, 7, 1), datetime(2026, 8, 20, tzinfo=UTC))
    assert out.value is None and out.reason == "VALUE_MISSING_IN_VINTAGE"
    assert series.value_as_of(date(2026, 7, 1), NOW).value == Decimal("323.4")


def test_latest_as_of(series):
    out = series.latest_as_of(datetime(2026, 8, 20, tzinfo=UTC))
    assert out.observation_date == date(2026, 7, 1) and out.value is None  # latest period exists but blank
    assert series.latest_as_of(NOW).observation_date == date(2026, 8, 1)


def test_revised_only_data_marks_feature_unavailable():
    current = VintageSeries.from_alfred_json((FIX / "fred_current_only_DMCPI.json").read_bytes(), "DMCPI")
    for when in (datetime(2026, 7, 20, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)):
        out = current.value_as_of(JUNE, when)
        assert out.value is None and out.reason == "VINTAGE_DATA_ABSENT"
    assert current.latest_as_of(datetime(2026, 8, 1, tzinfo=UTC)).reason == "VINTAGE_DATA_ABSENT"


def test_fred_disabled_without_key():
    client = FredClient(api_key=None)
    cap = client.capability()
    assert cap["status"] == "DISABLED" and "API key" in cap["reason"]
    with pytest.raises(SourceDisabledError):
        client.fetch_vintages("DMCPI")
    with pytest.raises(SourceDisabledError):
        client.observations_url("DMCPI")


def test_fred_from_env_without_key(monkeypatch):
    monkeypatch.delenv("APEX_FRED_API_KEY", raising=False)
    assert FredClient.from_env().capability()["status"] == "DISABLED"


def test_fred_client_with_key_uses_vintage_request_and_hides_key():
    payload = (FIX / "alfred_vintages_DMCPI.json").read_bytes()
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, content=payload, headers={"ETag": '"a1"'})

    class NoSleep:
        def monotonic(self):
            return 0.0

        def sleep(self, s):
            pass

        def now(self):
            return NOW

    cache = MemoryCache()
    http = PoliteHttpClient(
        HttpClientConfig.for_public("APEX macro (fixture) ops@example.com"),
        transport=httpx.MockTransport(handler),
        bucket=TokenBucket(1.0, time_source=NoSleep()),
        cache=cache,
        time_source=NoSleep(),
        rng=random.Random(2),
    )
    client = FredClient("TESTKEY123", http)
    assert client.capability()["status"] == "ENABLED" and "TESTKEY123" not in json.dumps(client.capability())
    assert "TESTKEY123" not in repr(client)
    s = client.fetch_vintages("DMCPI")
    assert s.value_as_of(JUNE, datetime(2026, 7, 20, 15, tzinfo=UTC)).value == Decimal("321.5")
    params = dict(seen[0].params)
    assert params["realtime_start"] == "1776-07-04" and params["realtime_end"] == "9999-12-31"
    assert all("TESTKEY123" not in e.url for e in cache._data.values())


def test_release_calendar_catalysts():
    entries = load_release_calendar((FIX / "macro_release_calendar.json").read_text())
    assert len(entries) == 3
    recs = [release_to_catalyst(e, first_seen_at=NOW, is_demo=True) for e in entries]
    for rec in recs:
        validate("Catalyst", rec.to_contract())
        assert rec.catalyst_type is CatalystType.MACRO_RELEASE and rec.symbol is None
        assert rec.actionable_age is ActionableAge.SCHEDULED_FUTURE
    cpi = recs[0]
    assert cpi.event_at == datetime(2026, 11, 12, 13, 30, tzinfo=UTC)  # 08:30 EST (after DST end)
    assert cpi.source_grade is SourceGrade.B_OFFICIAL_RELEASE
    rate = recs[2]
    assert (
        "TIME_UNKNOWN_DATE_ONLY" in rate.reason_codes and "EXPECTED_SCHEDULE_UNCONFIRMED" in rate.reason_codes
    )
