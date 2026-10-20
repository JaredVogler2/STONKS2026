from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from apex_worker.catalysts.form4 import Form4ParseError, classify_form4, form4_to_catalyst, parse_form4
from apex_worker.catalysts.http import QuarantineRecord, safe_parse
from apex_worker.catalysts.models import CatalystType, Direction
from apex_worker.catalysts.sec import parse_submissions
from apex_worker.catalysts.tickers import TickerMap
from apex_worker.catalysts.xmlsafe import UnsafeXmlError, parse_xml
from apex_worker.contracts import validate

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "catalysts"
NOW = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)
ACCESSIONS = {
    "form4_open_market_purchase.xml": "0009990101-26-000007",
    "form4_10b5_1_sale.xml": "0009990102-26-000003",
    "form4_award_code_a.xml": "0009990103-26-000002",
    "form4_exercise_m_withholding_f.xml": "0009990104-26-000005",
    "form4_multiple_transactions.xml": "0009990105-26-000001",
}


@pytest.fixture(scope="module")
def filings():
    doc = parse_submissions((FIX / "edgar_submissions_CIK0009990001.json").read_bytes())
    return {f.accession_number: f for f in doc.filings}


def _catalyst(name, filings, **kw):
    raw = (FIX / name).read_bytes()
    doc = parse_form4(raw)
    rec = form4_to_catalyst(
        doc, raw=raw, filing=filings[ACCESSIONS[name]], first_seen_at=NOW, is_demo=True, **kw
    )
    validate("Catalyst", rec.to_contract())
    return doc, rec


def test_open_market_purchase_is_potentially_positive(filings):
    doc, rec = _catalyst("form4_open_market_purchase.xml", filings)
    assert doc.issuer_cik == "0009990001" and doc.issuer_symbol == "DMSEMI1"
    owner = doc.owners[0]
    assert owner.is_director and owner.is_officer and owner.officer_title == "Chief Executive Officer"
    tx = doc.transactions[0]
    assert tx.code == "P" and tx.acquired_disposed == "A" and tx.notional == Decimal("251000.00")
    assert rec.catalyst_type is CatalystType.INSIDER_OPEN_MARKET_PURCHASE
    assert rec.direction is Direction.POTENTIALLY_POSITIVE
    assert {"NOTIONAL_GE_100K", "ROLE_CEO", "ROLE_DIRECTOR"} <= set(rec.reason_codes)
    assert rec.parsed["materiality"] == "medium"


def test_transaction_date_and_filing_acceptance_stay_distinct(filings):
    _, rec = _catalyst("form4_open_market_purchase.xml", filings)
    assert rec.event_at == datetime(2026, 10, 7, 4, 0, tzinfo=UTC)  # 2026-10-07 00:00 ET
    assert rec.published_at == datetime(2026, 10, 9, 22, 12, 44, tzinfo=UTC)  # acceptance 18:12:44 ET
    assert rec.available_at == rec.published_at + timedelta(minutes=15)
    assert rec.event_at < rec.published_at
    assert "TRANSACTION_TO_FILING_LAG_2D" in rec.reason_codes
    with pytest.raises(Form4ParseError, match="published_at"):
        raw = (FIX / "form4_open_market_purchase.xml").read_bytes()
        form4_to_catalyst(
            parse_form4(raw), raw=raw, accession_number="0009990101-26-000007", first_seen_at=NOW
        )


def test_planned_10b5_1_sale_is_unknown(filings):
    doc, rec = _catalyst("form4_10b5_1_sale.xml", filings)
    assert doc.aff10b5one is True and doc.transactions[0].planned_10b5_1
    assert rec.catalyst_type is CatalystType.INSIDER_SALE
    assert rec.direction is Direction.UNKNOWN
    assert "PLANNED_10B5_1" in rec.reason_codes and "ROLE_CFO" in rec.reason_codes


def test_award_code_a_is_never_bullish(filings):
    doc, rec = _catalyst("form4_award_code_a.xml", filings)
    assert doc.transactions[0].code == "A" and doc.transactions[0].acquired_disposed == "A"
    assert rec.catalyst_type is CatalystType.INSIDER_AWARD_OR_EXERCISE
    assert rec.catalyst_type is not CatalystType.INSIDER_OPEN_MARKET_PURCHASE
    assert rec.direction is Direction.UNKNOWN
    assert {"AWARD_CODE_A", "NOT_OPEN_MARKET", "NON_DISCRETIONARY_COMPENSATION"} <= set(rec.reason_codes)


def test_exercise_and_tax_withholding_not_a_sale_signal(filings):
    doc, rec = _catalyst("form4_exercise_m_withholding_f.xml", filings)
    codes = sorted(t.code for t in doc.transactions)
    assert codes == ["F", "M", "M"]
    assert any(t.table == "derivative" for t in doc.transactions)
    assert rec.catalyst_type is CatalystType.INSIDER_AWARD_OR_EXERCISE
    assert rec.direction is Direction.UNKNOWN
    assert {"OPTION_EXERCISE_CODE_M", "TAX_WITHHOLDING_CODE_F"} <= set(rec.reason_codes)


def test_multiple_transactions_aggregate_into_one_catalyst(filings):
    doc, rec = _catalyst("form4_multiple_transactions.xml", filings)
    assert len(doc.transactions) == 3
    totals = rec.parsed["totals"]
    assert totals["purchase_shares"] == Decimal(25000)
    assert totals["purchase_notional"] == Decimal("563625.00")
    assert rec.parsed["transaction_dates"] == [date(2026, 3, 4), date(2026, 3, 5), date(2026, 3, 6)]
    assert rec.direction is Direction.POTENTIALLY_POSITIVE
    assert {"ROLE_TEN_PERCENT_OWNER", "INDIRECT_OWNERSHIP_PRESENT", "LATE_FILING_GT_2_BUSINESS_DAYS"} <= set(
        rec.reason_codes
    )
    payload = rec.to_contract()
    assert payload["parsed"]["totals"]["purchase_notional"] == "563625.00"  # exact decimal string
    assert len(payload["parsed"]["transactions"]) == 3
    assert payload["event_at"] == "2026-03-04T05:00:00Z"  # EST before the DST change
    assert payload["published_at"] == "2026-03-09T20:45:00Z"  # EDT after it


def test_purchase_under_10b5_1_plan_is_not_positive():
    raw = (
        (FIX / "form4_open_market_purchase.xml")
        .read_bytes()
        .replace(b"<aff10b5One>0</aff10b5One>", b"<aff10b5One>1</aff10b5One>")
    )
    cls = classify_form4(parse_form4(raw))
    assert cls.catalyst_type is CatalystType.INSIDER_OPEN_MARKET_PURCHASE
    assert cls.direction is Direction.UNKNOWN and "PLANNED_10B5_1" in cls.reason_codes


def test_unplanned_sale_is_mixed_not_bearish_by_default():
    raw = (FIX / "form4_10b5_1_sale.xml").read_bytes()
    raw = raw.replace(b"<aff10b5One>1</aff10b5One>", b"<aff10b5One>0</aff10b5One>").replace(
        b"Rule 10b5-1 trading plan", b"open-market"
    )
    cls = classify_form4(parse_form4(raw))
    assert cls.catalyst_type is CatalystType.INSIDER_SALE
    assert (
        cls.direction is Direction.MIXED and "DISCRETION_UNCLEAR" in cls.reason_codes
    )  # $528k, ~12.5% of holdings


def test_clearly_discretionary_large_sale_is_potentially_negative():
    raw = (FIX / "form4_10b5_1_sale.xml").read_bytes()
    raw = (
        raw.replace(b"<aff10b5One>1</aff10b5One>", b"<aff10b5One>0</aff10b5One>")
        .replace(b"Rule 10b5-1 trading plan", b"open-market")
        .replace(b"<value>20000</value>", b"<value>60000</value>")
    )
    cls = classify_form4(parse_form4(raw))
    assert cls.direction is Direction.POTENTIALLY_NEGATIVE
    assert {"DISCRETIONARY_SALE", "SOLD_GE_25PCT_OF_HOLDINGS", "NOTIONAL_GE_1M"} <= set(cls.reason_codes)


def test_symbol_cross_checked_against_ticker_map(filings):
    tm = TickerMap.load({"0": {"cik_str": 9990001, "ticker": "DMSEMI1", "title": "x"}})
    _, rec = _catalyst("form4_open_market_purchase.xml", filings, ticker_map=tm)
    assert rec.symbol == "DMSEMI1" and rec.entity_confidence == 0.99
    wrong = TickerMap.load({"0": {"cik_str": 9990001, "ticker": "DMOTHER", "title": "x"}})
    _, rec2 = _catalyst("form4_open_market_purchase.xml", filings, ticker_map=wrong)
    assert "SYMBOL_CIK_MISMATCH" in rec2.reason_codes and rec2.entity_confidence <= 0.6


def test_weird_html_document_is_quarantined():
    raw = (FIX / "quarantine_weird_html_in_xml.xml").read_bytes()
    with pytest.raises(Form4ParseError):
        parse_form4(raw)
    out = safe_parse(
        raw,
        parse_form4,
        source="sec_edgar",
        url="https://www.sec.gov/x.xml",
        now=NOW,
        parser_version="form4/1.0.0",
    )
    assert isinstance(out, QuarantineRecord) and out.reason_code == "PARSE_ERROR"


@pytest.mark.parametrize(
    "xml",
    [
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]>'
        b"<ownershipDocument>&lol;</ownershipDocument>",
        b"<ownershipDocument><documentType>3</documentType></ownershipDocument>",
        b"<notOwnership/>",
        b"<ownershipDocument><documentType>4</documentType><issuer/></ownershipDocument>",
    ],
)
def test_hostile_or_wrong_documents_rejected(xml):
    with pytest.raises(ValueError):
        parse_form4(xml)


def test_dtd_refused_before_parsing():
    with pytest.raises(UnsafeXmlError):
        parse_xml(b'<!DOCTYPE x SYSTEM "file:///etc/passwd"><x/>')


def test_bad_amount_is_parse_error():
    raw = (
        (FIX / "form4_open_market_purchase.xml")
        .read_bytes()
        .replace(b"<value>25.10</value>", b"<value>NaN</value>")
    )
    with pytest.raises(Form4ParseError):
        parse_form4(raw)
