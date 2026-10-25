from __future__ import annotations

import json
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from apex_worker.catalysts.http import HttpClientConfig, PoliteHttpClient, TokenBucket, UserAgentError
from apex_worker.catalysts.models import ActionableAge, CatalystType, Direction
from apex_worker.catalysts.sec import (
    FilingMeta,
    SecEdgarClient,
    SubmissionsParseError,
    classify_form,
    filing_to_catalyst,
    filing_url,
    parse_acceptance_datetime,
    parse_submissions,
    raw_document_url,
    submissions_to_catalysts,
)
from apex_worker.catalysts.tickers import TickerMap, TickerMapError, TickerSpan
from apex_worker.contracts import validate

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "catalysts"
NOW = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def doc():
    return parse_submissions((FIX / "edgar_submissions_CIK0009990001.json").read_bytes())


@pytest.fixture(scope="module")
def tmap():
    return TickerMap.load(
        (FIX / "company_tickers.json").read_bytes(), (FIX / "ticker_aliases.json").read_bytes()
    )


def _by_acc(doc, acc):
    return next(f for f in doc.filings if f.accession_number == acc)


# --- parsing ---------------------------------------------------------------------------------
def test_parse_submissions_fixture(doc):
    assert doc.cik == "0009990001" and doc.name == "Demo Semiconductor One Inc"
    assert doc.tickers == ("DMSEMI1",) and not doc.errors
    assert len(doc.filings) == 27
    k = _by_acc(doc, "0009990001-26-000041")
    assert k.form == "8-K" and k.items == ("2.02", "9.01") and k.filing_date == date(2026, 10, 14)


def test_acceptance_with_z_is_utc_and_the_raw_header_is_eastern():
    # the submissions API prints UTC: Apple's 8-K 0000320193-26-000018 is 20:30:28Z there and "Accepted
    # 2026-07-30 16:30:28" (US Eastern) on its EDGAR index page
    assert parse_acceptance_datetime("2026-07-30T20:30:28.000Z") == datetime(2026, 7, 30, 20, 30, 28, tzinfo=UTC)
    assert parse_acceptance_datetime("2026-07-30T20:30:28+00:00") == datetime(2026, 7, 30, 20, 30, 28, tzinfo=UTC)
    # the SGML header and a naive value are the Eastern wall clock: 16:05 ET in October is 20:05 UTC (EDT)
    assert parse_acceptance_datetime("20261014160531") == datetime(2026, 10, 14, 20, 5, 31, tzinfo=UTC)
    assert parse_acceptance_datetime("2026-10-14T16:05:31") == datetime(2026, 10, 14, 20, 5, 31, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "expected_utc"),
    [
        ("20260306164512", datetime(2026, 3, 6, 21, 45, 12, tzinfo=UTC)),  # Fri, EST (UTC-5)
        (
            "20260309164512",
            datetime(2026, 3, 9, 20, 45, 12, tzinfo=UTC),
        ),  # Mon after DST start, EDT
        ("20251031163000", datetime(2025, 10, 31, 20, 30, tzinfo=UTC)),  # Fri, EDT
        ("2025-11-03T16:30:00", datetime(2025, 11, 3, 21, 30, tzinfo=UTC)),  # Mon after DST end, EST (naive)
    ],
)
def test_acceptance_dst_boundaries(raw, expected_utc):  # the Eastern wall-clock formats
    assert parse_acceptance_datetime(raw) == expected_utc


def test_dst_boundary_filings_in_fixture(doc):
    tenk = _by_acc(doc, "0009990001-26-000006")
    eightk = _by_acc(doc, "0009990001-26-000007")
    # same 16:45:12 ET wall clock, one hour apart in UTC because DST began on 2026-03-08
    assert (eightk.acceptance_at - tenk.acceptance_at) == timedelta(days=3) - timedelta(hours=1)


def test_ambiguous_fall_back_hour_resolves_to_later_instant():
    # 01:30 ET on 2026-11-01 happens twice; the conservative choice is the later (EST) instant
    assert parse_acceptance_datetime("20261101013000") == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)


def test_bad_documents_raise_parse_errors():
    with pytest.raises(SubmissionsParseError):
        parse_submissions(b"<html>not json</html>")
    with pytest.raises(SubmissionsParseError):
        parse_submissions(json.dumps({"cik": "1", "filings": {"recent": {}}}))
    bad = json.loads((FIX / "edgar_submissions_CIK0009990001.json").read_text())
    bad["filings"]["recent"]["form"] = bad["filings"]["recent"]["form"][:-1]
    with pytest.raises(SubmissionsParseError, match="length mismatch"):
        parse_submissions(bad)


def test_bad_row_is_skipped_not_fatal():
    raw = json.loads((FIX / "edgar_submissions_CIK0009990010.json").read_text())
    raw["filings"]["recent"]["acceptanceDateTime"][0] = "yesterday-ish"
    parsed = parse_submissions(raw)
    assert len(parsed.filings) == 1 and len(parsed.errors) == 1


def test_canonical_urls():
    assert (
        filing_url("0009990001", "0009990001-26-000041", "dmsemi1-20261014.htm")
        == "https://www.sec.gov/Archives/edgar/data/9990001/000999000126000041/dmsemi1-20261014.htm"
    )
    assert filing_url("9990001", "000999000126000041", None).endswith(
        "/000999000126000041/0009990001-26-000041-index.htm"
    )
    assert raw_document_url("9990001", "0009990101-26-000007", "xslF345X05/wk-form4_1.xml").endswith(
        "/000999010126000007/wk-form4_1.xml"
    )
    with pytest.raises(SubmissionsParseError):
        filing_url("9990001", "0009990001-26-000041", "../../etc/passwd")


# --- form mapping ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("form", "items", "ctype", "direction", "code"),
    [
        (
            "8-K",
            ["2.02", "9.01"],
            CatalystType.EARNINGS_RELEASE,
            Direction.UNKNOWN,
            "ITEM_2_02_RESULTS_OF_OPERATIONS",
        ),
        (
            "8-K",
            ["1.01", "9.01"],
            CatalystType.CURRENT_REPORT_OTHER,
            Direction.UNKNOWN,
            "ITEM_1_01_MATERIAL_AGREEMENT",
        ),
        (
            "8-K",
            ["5.02"],
            CatalystType.MANAGEMENT_CHANGE,
            Direction.UNKNOWN,
            "ITEM_5_02_OFFICER_DIRECTOR_CHANGE",
        ),
        ("8-K", ["8.01"], CatalystType.CURRENT_REPORT_OTHER, Direction.UNKNOWN, "ITEM_8_01_OTHER_EVENTS"),
        (
            "8-K",
            ["3.02"],
            CatalystType.DILUTION_OFFERING,
            Direction.POTENTIALLY_NEGATIVE,
            "ITEM_3_02_UNREGISTERED_EQUITY_SALE",
        ),
        (
            "8-K",
            ["7.01", "9.01"],
            CatalystType.CURRENT_REPORT_OTHER,
            Direction.UNKNOWN,
            "ITEM_7_01_REG_FD_DISCLOSURE",
        ),
        ("8-K/A", ["5.02"], CatalystType.MANAGEMENT_CHANGE, Direction.UNKNOWN, "AMENDED_FILING"),
        ("10-Q", [], CatalystType.PERIODIC_REPORT, Direction.UNKNOWN, "PERIODIC_REPORT_10Q"),
        ("10-K", [], CatalystType.PERIODIC_REPORT, Direction.UNKNOWN, "PERIODIC_REPORT_10K"),
        ("S-3", [], CatalystType.SHELF_REGISTRATION, Direction.UNKNOWN, "SHELF_CAPACITY_NOT_AN_OFFERING"),
        ("S-3ASR", [], CatalystType.SHELF_REGISTRATION, Direction.UNKNOWN, "AUTOMATIC_SHELF_WKSI"),
        (
            "424B5",
            [],
            CatalystType.DILUTION_OFFERING,
            Direction.UNKNOWN,
            "PROSPECTUS_SECURITY_TYPE_UNVERIFIED",
        ),
        ("424B2", [], CatalystType.DILUTION_OFFERING, Direction.UNKNOWN, "FORM_424B2"),
        ("SC 13D", [], CatalystType.OWNERSHIP_13D, Direction.UNKNOWN, "ACTIVE_INTENT_POSSIBLE"),
        ("SC 13G", [], CatalystType.OWNERSHIP_13G, Direction.UNKNOWN, "PASSIVE_HOLDER"),
        ("SCHEDULE 13G", [], CatalystType.OWNERSHIP_13G, Direction.UNKNOWN, "PASSIVE_HOLDER"),
    ],
)
def test_form_mapping(form, items, ctype, direction, code):
    cls = classify_form(form, items)
    assert cls.catalyst_type is ctype and cls.direction is direction and code in cls.reason_codes


def test_form4_metadata_needs_xml_detail_and_is_never_typed_from_metadata():
    cls = classify_form("4")
    assert cls.needs_detail and cls.catalyst_type is None


def test_unmapped_forms_produce_no_catalyst(doc):
    proxy = _by_acc(doc, "0009990001-26-000009")
    assert proxy.form == "DEF 14A"
    assert filing_to_catalyst(proxy, first_seen_at=NOW) is None


def test_8k_items_fixture_rows_classify():
    rows = json.loads((FIX / "edgar_8k_items.json").read_text())["filings"]
    types = [classify_form(r["form"], r["items"].split(",")).catalyst_type for r in rows]
    assert types.count(CatalystType.EARNINGS_RELEASE) == 1
    assert CatalystType.MERGER_ACQUISITION in types  # 2.01 completion
    cyber = classify_form("8-K", ["1.05", "8.01"])
    assert cyber.direction is Direction.POTENTIALLY_NEGATIVE


# --- catalysts -------------------------------------------------------------------------------
def test_submissions_to_contract_valid_catalysts(doc, tmap):
    records, pending = submissions_to_catalysts(doc, first_seen_at=NOW, ticker_map=tmap, is_demo=True)
    assert len(records) == 21  # 27 filings - 5 Form 4 (need XML) - 1 DEF 14A (unmapped)
    assert len(pending) == 5 and all(p.base_form == "4" for p in pending)
    for rec in records:
        payload = rec.to_contract()
        validate("Catalyst", payload)
        assert payload["source_grade"] == "A_official_filing"
        assert payload["legal_classification"] == "public_metadata_link_only"
        assert payload["canonical_url"].startswith("https://www.sec.gov/Archives/edgar/data/9990001/")
        assert payload["symbol"] == "DMSEMI1" and payload["cik"] == "0009990001"
        assert payload["is_demo"] is True


def test_backfill_availability_uses_acceptance_plus_lag(doc):
    meta = _by_acc(doc, "0009990001-26-000041")
    rec = filing_to_catalyst(meta, first_seen_at=NOW, mode="backfill")
    assert rec.published_at == datetime(2026, 10, 14, 20, 5, 31, tzinfo=UTC)
    assert rec.available_at == rec.published_at + timedelta(minutes=15)
    live = filing_to_catalyst(meta, first_seen_at=rec.published_at + timedelta(seconds=40), mode="live")
    assert live.available_at == rec.published_at + timedelta(seconds=40)
    assert live.id == rec.id  # identity does not depend on when we saw it


def test_stale_filing_flagged_in_live_mode(doc):
    meta = _by_acc(doc, "0009990001-26-000006")  # March 10-K discovered in October
    rec = filing_to_catalyst(meta, first_seen_at=NOW, mode="live")
    assert rec.actionable_age is ActionableAge.STALE
    assert "STALE_ON_DISCOVERY" in rec.reason_codes
    assert rec.available_at == NOW


def test_missing_acceptance_time_is_conservative():
    meta = FilingMeta(
        cik="0009990001",
        company_name="Demo Semiconductor One Inc",
        accession_number="0009990001-26-000099",
        form="8-K",
        filing_date=date(2026, 10, 1),
        acceptance_at=None,
        items=("8.01",),
    )
    rec = filing_to_catalyst(meta, first_seen_at=NOW)
    assert rec.published_at == datetime(
        2026, 10, 2, 2, 0, tzinfo=UTC
    )  # 22:00 ET, the latest EDGAR acceptance
    assert "ACCEPTANCE_TIME_MISSING_CONSERVATIVE" in rec.reason_codes


# --- ticker <-> CIK ----------------------------------------------------------------------------
def test_ticker_reuse_never_mixes_entities(tmap):
    assert tmap.cik_for("DMRX", date(2020, 5, 6)) == "0009990010"
    assert tmap.cik_for("DMRX", date(2022, 6, 1)) is None  # gap: ticker unassigned, not today's owner
    assert tmap.cik_for("DMRX", date(2024, 6, 1)) == "0009990011"
    assert tmap.symbol_for("0009990010", date(2020, 5, 6)) == "DMRX"
    assert tmap.symbol_for("0009990010", date(2024, 6, 1)) is None
    assert tmap.symbol_for("0009990011", date(2020, 5, 6)) is None
    assert tmap.resolve("DMRX", date(2022, 6, 1)).reason == "TICKER_UNASSIGNED_ON_DATE"


def test_old_company_filings_keep_their_own_entity(tmap):
    old = parse_submissions((FIX / "edgar_submissions_CIK0009990010.json").read_bytes())
    records, _ = submissions_to_catalysts(old, first_seen_at=NOW, ticker_map=tmap)
    assert {r.cik for r in records} == {"0009990010"}
    assert {r.symbol for r in records} == {"DMRX"}  # DMRX as of 2020/2021 belonged to the legacy CIK
    without_map, _ = submissions_to_catalysts(old, first_seen_at=NOW)
    assert all(r.symbol is None for r in without_map)  # no current ticker -> no guessed symbol


def test_ticker_change_follows_entity(tmap):
    assert tmap.symbol_for("0009990005", date(2025, 1, 15)) == "DMOLDNM"
    assert tmap.symbol_for("0009990005", date(2025, 6, 1)) == "DMNEWNM"
    assert tmap.cik_for("DMOLDNM", date(2025, 1, 15)) == tmap.cik_for("DMNEWNM", date(2025, 6, 1))
    assert tmap.cik_for("DMOLDNM", date(2025, 6, 1)) is None


def test_overlapping_spans_for_different_ciks_rejected():
    with pytest.raises(TickerMapError, match="overlapping"):
        TickerMap(
            {},
            [
                TickerSpan("DMX", "1", date(2020, 1, 1), date(2022, 1, 1)),
                TickerSpan("DMX", "2", date(2021, 6, 1), None),
            ],
        )


def test_company_tickers_class_separator_normalized():
    tm = TickerMap.load({"0": {"cik_str": 9990099, "ticker": "DMCL-B", "title": "Demo Class B"}})
    assert tm.cik_for("DMCL.B") == "0009990099"


# --- live client (MockTransport only) -----------------------------------------------------------
class _NoSleep:
    def monotonic(self):
        return 0.0

    def sleep(self, s):
        pass

    def now(self):
        return NOW


def test_sec_client_fetches_submissions_with_mock_transport():
    payload = (FIX / "edgar_submissions_CIK0009990001.json").read_bytes()
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=payload, headers={"Content-Type": "application/json"})

    cfg = HttpClientConfig.for_sec("APEX Research Demo ops@example.com")
    http = PoliteHttpClient(
        cfg,
        transport=httpx.MockTransport(handler),
        bucket=TokenBucket(2.0, time_source=_NoSleep()),
        rng=random.Random(1),
    )
    client = SecEdgarClient(http)
    result = client.submissions("9990001")
    assert seen == ["https://data.sec.gov/submissions/CIK0009990001.json"]
    assert parse_submissions(result.content).cik == "0009990001"


def test_sec_client_refuses_non_sec_config():
    http = PoliteHttpClient(
        HttpClientConfig.for_public("APEX reader"),
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )
    with pytest.raises(UserAgentError, match="SEC requires a descriptive User-Agent with contact email"):
        SecEdgarClient(http)
