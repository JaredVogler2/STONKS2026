from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from apex_worker.catalysts.models import (
    NEWS_PUBLICATION_LAG,
    SEC_ACCEPTANCE_LAG,
    ActionableAge,
    AvailabilityMode,
    CatalystRecord,
    CatalystType,
    Direction,
    LegalClassification,
    Source,
    SourceGrade,
    classify_actionable_age,
    is_available,
    make_catalyst_id,
    metadata_hash,
    reason_code,
    resolve_available_at,
    sha256_hex,
)
from apex_worker.contracts import validate

T0 = datetime(2026, 10, 14, 20, 5, 31, tzinfo=UTC)


def _record(**kw):
    base = dict(
        id=make_catalyst_id(Source.SEC_EDGAR, "0009990001-26-000041", CatalystType.EARNINGS_RELEASE),
        catalyst_type=CatalystType.EARNINGS_RELEASE,
        source=Source.SEC_EDGAR,
        headline="Demo Semiconductor One Inc filed 8-K: Item 2.02",
        source_grade=SourceGrade.A_OFFICIAL_FILING,
        direction=Direction.UNKNOWN,
        entity_confidence=0.99,
        extraction_confidence=0.95,
        first_seen_at=T0 + timedelta(minutes=2),
        available_at=T0 + timedelta(minutes=2),
        source_hash=sha256_hex(b"raw"),
        parser_version="test/1",
        legal_classification=LegalClassification.PUBLIC_METADATA_LINK_ONLY,
        is_demo=True,
        published_at=T0,
        symbol="DMSEMI1",
        cik="0009990001",
        canonical_url="https://www.sec.gov/Archives/edgar/data/9990001/000999000126000041/x.htm",
        accession_number="0009990001-26-000041",
    )
    base.update(kw)
    return CatalystRecord(**base)


def test_id_is_stable_and_contract_shaped():
    a = make_catalyst_id("sec_edgar", "0009990001-26-000041", "earnings_release")
    b = make_catalyst_id(Source.SEC_EDGAR, "0009990001-26-000041", CatalystType.EARNINGS_RELEASE)
    assert a == b and a.startswith("cat_") and len(a) == 28
    assert a != make_catalyst_id("sec_edgar", "0009990001-26-000041", "current_report_other")
    assert a != make_catalyst_id("news_rss", "0009990001-26-000041", "earnings_release")


def test_metadata_hash_is_canonical():
    assert metadata_hash({"b": 1, "a": [1, 2]}) == metadata_hash({"a": [1, 2], "b": 1})
    assert len(metadata_hash({"x": 1})) == 64


def test_to_contract_validates_and_roundtrips():
    rec = _record(parsed={"items": ["2.02", "9.01"]}, reason_codes=("ITEM_2_02_RESULTS",))
    payload = rec.to_contract()
    validate("Catalyst", payload)
    assert payload["published_at"] == "2026-10-14T20:05:31Z"
    again = CatalystRecord.from_contract(payload)
    assert again.to_contract() == payload


def test_four_instants_are_distinct_fields():
    rec = _record(event_at=T0 - timedelta(days=2))
    p = rec.to_contract()
    assert {p["event_at"], p["published_at"], p["first_seen_at"]} == {
        "2026-10-12T20:05:31Z",
        "2026-10-14T20:05:31Z",
        "2026-10-14T20:07:31Z",
    }
    assert p["available_at"] == p["first_seen_at"]


def test_available_before_published_is_rejected():
    with pytest.raises(ValueError, match="point-in-time"):
        _record(available_at=T0 - timedelta(seconds=1))


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError, match="naive"):
        _record(first_seen_at=datetime(2026, 10, 14, 20, 0))


def test_headline_is_cleaned_and_truncated():
    rec = _record(headline="x" * 500 + "\n\t tail")
    assert len(rec.headline) == 200
    assert rec.headline.endswith("…")
    validate("Catalyst", rec.to_contract())


def test_reason_codes_normalized_deduped_and_capped():
    codes = (*(f"code {i}" for i in range(30)), "code 1")
    rec = _record(reason_codes=codes)
    assert len(rec.reason_codes) == 16
    assert rec.reason_codes[-1] == "REASON_CODES_TRUNCATED"
    assert rec.reason_codes[0] == "CODE_0"
    assert reason_code("Disclosure lag", 40, "d") == "DISCLOSURE_LAG_40_D"


def test_resolve_available_at_live_and_backfill():
    seen = T0 + timedelta(minutes=3)
    assert resolve_available_at("live", first_seen_at=seen, published_at=T0, lag=SEC_ACCEPTANCE_LAG) == seen
    assert resolve_available_at(
        AvailabilityMode.BACKFILL,
        first_seen_at=seen + timedelta(days=400),
        published_at=T0,
        lag=SEC_ACCEPTANCE_LAG,
    ) == T0 + timedelta(minutes=15)
    assert resolve_available_at(
        "backfill", first_seen_at=seen, published_at=T0, lag=NEWS_PUBLICATION_LAG
    ) == T0 + timedelta(minutes=60)
    # live: clock skew can never make something available before its publication
    early = T0 - timedelta(minutes=5)
    assert resolve_available_at("live", first_seen_at=early, published_at=T0, lag=timedelta(0)) == T0
    # no publication time: only usable from when we actually saw it
    assert (
        resolve_available_at("backfill", first_seen_at=seen, published_at=None, lag=SEC_ACCEPTANCE_LAG)
        == seen
    )


def test_is_available_for_records_and_payloads():
    rec = _record()
    assert not is_available(rec, rec.available_at - timedelta(seconds=1))
    assert is_available(rec, rec.available_at)
    assert is_available(rec.to_contract(), rec.available_at + timedelta(hours=1))
    assert not rec.is_available(T0)


def test_actionable_age_categories():
    assert (
        classify_actionable_age(available_at=T0, published_at=T0 - timedelta(hours=2), event_at=None)
        is ActionableAge.FRESH
    )
    assert (
        classify_actionable_age(available_at=T0, published_at=T0 - timedelta(days=3), event_at=None)
        is ActionableAge.RECENT
    )
    assert (
        classify_actionable_age(available_at=T0, published_at=T0 - timedelta(days=30), event_at=None)
        is ActionableAge.STALE
    )
    assert (
        classify_actionable_age(available_at=T0, published_at=None, event_at=T0 + timedelta(days=5))
        is ActionableAge.SCHEDULED_FUTURE
    )
    assert (
        classify_actionable_age(available_at=T0, published_at=T0, event_at=None, lagged_disclosure=True)
        is ActionableAge.LAGGED_DISCLOSURE
    )


def test_confidence_and_impact_bounds():
    with pytest.raises(ValueError):
        _record(entity_confidence=1.2)
    with pytest.raises(ValueError, match="NOT a probability"):
        _record(impact_score=140)
