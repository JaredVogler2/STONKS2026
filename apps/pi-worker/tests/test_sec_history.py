"""research.sec_history (docs/evidence/DESIGN_STUDY_2.md): point-in-time earnings releases (8-K item 2.02) and insider
purchases (Form 4 code P) from SEC data, the fail-closed contact rule and the aggregates-only summary. Every issuer,
ticker, CIK and person here is fictional (fixtures/catalysts/README.md conventions); no test touches the network."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, date, datetime

import httpx
import pytest

from apex_worker.catalysts.http import TokenBucket
from apex_worker.market.calendar import default_calendar
from apex_worker.research import sec_history as SH
from apex_worker.research.realeq.data import RunStop
from apex_worker.timeutil import ET

UA = "APEX Research Demo ops@example.com"
CAL = default_calendar()
SUB_COLS = ("ACCESSION_NUMBER", "FILING_DATE", "PERIOD_OF_REPORT", "DOCUMENT_TYPE", "ISSUERCIK", "ISSUERNAME",
            "ISSUERTRADINGSYMBOL", "REMARKS")
TRANS_COLS = ("ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "SECURITY_TITLE", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES",
              "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD", "DIRECT_INDIRECT_OWNERSHIP")
OWNER_COLS = ("ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE")


def _recent(rows: list[dict]) -> dict:
    keys = ("accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "form", "items", "primaryDocument",
            "primaryDocDescription", "size")
    return {k: [r.get(k, "") for r in rows] for k in keys}


def _filing(acc: int, form: str, accepted_et: str, items: str = "", filed: str | None = None) -> dict:
    """A submissions row accepted at `accepted_et` (US Eastern wall clock), printed in UTC as the API prints it."""
    utc = datetime.fromisoformat(accepted_et).replace(tzinfo=ET).astimezone(UTC)
    return {"accessionNumber": f"0009990001-26-{acc:06d}", "filingDate": filed or accepted_et[:10], "form": form,
            "acceptanceDateTime": utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "items": items,
            "primaryDocument": "doc.htm", "size": 100}


def _tsv(cols: tuple[str, ...], rows: list[tuple]) -> str:
    return "\t".join(cols) + "\n" + "".join("\t".join(str(x) for x in r) + "\n" for r in rows)


def _zip(subs: list[tuple], trans: list[tuple], owners: list[tuple], *, extra: dict[str, str] | None = None,
         drop: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        tables = {"SUBMISSION.tsv": _tsv(SUB_COLS, subs), "NONDERIV_TRANS.tsv": _tsv(TRANS_COLS, trans),
                  "REPORTINGOWNER.tsv": _tsv(OWNER_COLS, owners)} | (extra or {})
        for name, text in tables.items():
            if name != drop:
                zf.writestr(f"2026q1_form345/{name}", text)
    return buf.getvalue()


# ---- the contact ----------------------------------------------------------------------------------------------------
def test_the_contact_is_required_and_never_echoed():
    with pytest.raises(RunStop) as missing:
        SH.user_agent_from_env({})
    assert missing.value.status == SH.STATUS_SEC_BLOCKED and "BLOCKED" in missing.value.status
    with pytest.raises(RunStop) as generic:
        SH.user_agent_from_env({"APEX_SEC_USER_AGENT": "curl/8.0 someone@example.com"})
    assert "someone@example.com" not in generic.value.reason and generic.value.__cause__ is None
    assert SH.user_agent_from_env({"APEX_SEC_USER_AGENT": f"  {UA} "}) == UA


# ---- earnings releases ----------------------------------------------------------------------------------------------
def test_releases_are_8k_item_202_once_a_day_and_usable_from_the_right_session():
    from apex_worker.catalysts.sec import parse_submissions

    rows = [
        _filing(1, "8-K", "2026-01-29T16:05:00", "2.02,9.01"),  # Thursday after the close -> Friday
        _filing(2, "8-K", "2026-01-29T16:40:00", "2.02"),  # the same day again: the earliest counts
        _filing(3, "8-K/A", "2026-02-03T08:00:00", "2.02"),  # an amendment: not a release
        _filing(4, "8-K", "2026-02-04T08:00:00", "5.02"),  # no item 2.02
        _filing(5, "8-K", "2026-04-30T07:30:00", "2.02,9.01"),  # Thursday before the open -> that session
        _filing(6, "8-K", "2026-07-25T10:00:00", "2.02"),  # a Saturday -> Monday
        _filing(7, "8-K", "2026-10-29T11:15:00", "2.02"),  # during the session -> that session
    ]
    doc = parse_submissions({"cik": "9990001", "name": "DM Demo Inc", "filings": {"recent": _recent(rows)}})
    rel = SH.releases_from_filings(doc.filings, CAL)
    assert [(r.day, r.session) for r in rel] == [
        (date(2026, 1, 29), date(2026, 1, 30)), (date(2026, 4, 30), date(2026, 4, 30)),
        (date(2026, 7, 25), date(2026, 7, 27)), (date(2026, 10, 29), date(2026, 10, 29))]
    assert rel[0].accepted_at == datetime(2026, 1, 29, 21, 5, tzinfo=UTC)  # 16:05 Eastern (EST)
    assert all(r.cik == "0009990001" for r in rel)


def _transport(routes: dict[str, bytes | int], seen: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        assert request.headers["User-Agent"] == UA
        body = routes.get(url)
        if body is None:
            return httpx.Response(404)
        if isinstance(body, int):
            return httpx.Response(body)
        return httpx.Response(200, content=body)
    return httpx.MockTransport(handler)


class FakeTime:
    """The rate limiter's clock: waiting advances virtual time, so tests run at the SEC's 10 a second instantly."""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(seconds, 1e-6)  # a real sleep always advances the clock; a 1e-17 s one would be absorbed

    def now(self) -> datetime:
        return datetime(2026, 10, 25, tzinfo=UTC)


def _http(routes, seen):
    return SH.sec_http(UA, transport=_transport(routes, seen), bucket=TokenBucket(10.0, time_source=FakeTime()))


def test_company_filings_reads_only_the_pages_that_overlap_the_window():
    recent = _recent([_filing(10, "8-K", "2026-04-30T07:30:00", "2.02")])
    older = _recent([_filing(11, "8-K", "2025-01-30T16:10:00", "2.02"), _filing(12, "4", "2024-12-02T17:00:00")])
    doc = {"cik": "9990001", "name": "DM Demo Inc", "filings": {"recent": recent, "files": [
        {"name": "CIK0009990001-submissions-001.json", "filingCount": 2, "filingFrom": "2024-12-02",
         "filingTo": "2025-01-30"},
        {"name": "CIK0009990001-submissions-002.json", "filingCount": 9, "filingFrom": "2010-01-04",
         "filingTo": "2012-06-29"}]}}
    seen: list[str] = []
    routes = {"https://data.sec.gov/submissions/CIK0009990001.json": json.dumps(doc).encode(),
              "https://data.sec.gov/submissions/CIK0009990001-submissions-001.json": json.dumps(older).encode()}
    filings, n = SH.company_filings(_http(routes, seen), "9990001", date(2025, 1, 1), date(2026, 9, 30))
    assert n == 2 and len(seen) == 2 and not any("submissions-002" in u for u in seen)
    assert sorted(f.accession_number for f in filings) == ["0009990001-26-000010", "0009990001-26-000011"]
    bad = doc | {"filings": doc["filings"] | {"files": [{"name": "../../etc/passwd", "filingFrom": "2025-01-01",
                                                        "filingTo": "2025-12-31"}]}}
    routes["https://data.sec.gov/submissions/CIK0009990001.json"] = json.dumps(bad).encode()
    with pytest.raises(RunStop) as stop:
        SH.company_filings(_http(routes, []), "9990001", date(2025, 1, 1), date(2026, 9, 30))
    assert stop.value.status == SH.STATUS_SEC_SCHEMA


# ---- insider purchases ----------------------------------------------------------------------------------------------
IN, OUT = "9990001", "9990099"


def _sub(acc: str, filed: str, form: str = "4", issuer: str = IN) -> tuple:
    return (acc, filed, filed, form, issuer, "DM Demo Inc", "DMDEMO", "")


def _buy(acc: str, code: str = "P", shares: str = "100", price: str = "10.5", ad: str = "A", sk: int = 1) -> tuple:
    return (acc, sk, "Common Stock", "02-JAN-2026", code, shares, price, ad, "D")


def _owner(acc: str, cik: str, rel: str) -> tuple:
    return (acc, cik, "Fictional Person", rel, "")


def test_form345_keeps_only_director_or_officer_open_market_purchases():
    subs = [_sub("A1", "06-FEB-2026"), _sub("A2", "06-FEB-2026", form="4/A"), _sub("A3", "06-FEB-2026"),
            _sub("A4", "06-FEB-2026"), _sub("A5", "06-FEB-2026"), _sub("A6", "06-FEB-2026", issuer=OUT),
            _sub("A7", "2026-02-09"), _sub("A8", "09-FEB-2026", form="3")]
    trans = [_buy("A1"), _buy("A1", shares="50", price="", sk=2), _buy("A2"), _buy("A3", code="S"),
             _buy("A4", ad="D"), _buy("A5"), _buy("A6"), _buy("A7", shares="1,000"), _buy("A8"),
             _buy("A1", shares="0", sk=3)]
    owners = [_owner("A1", "9991001", "Director"), _owner("A2", "9991002", "Officer"),
              _owner("A3", "9991003", "Officer"), _owner("A4", "9991004", "Officer"),
              _owner("A5", "9991005", "TenPercentOwner"), _owner("A6", "9991006", "Director"),
              _owner("A7", "9991007", "Officer"), _owner("A7", "9991008", "Director,Officer"),
              _owner("A7", "9991009", "Other")]
    stats: dict = {}
    out = SH.parse_form345(_zip(subs, trans, owners), [IN], CAL, stats)
    got = sorted((p.owner, p.filing_date, p.session, p.shares, p.value) for p in out)
    assert got == [
        ("0009991001", date(2026, 2, 6), date(2026, 2, 9), 150.0, None),  # Friday filing -> Monday; one line unpriced
        ("0009991007", date(2026, 2, 9), date(2026, 2, 10), 1000.0, 10500.0),  # a joint filing: one event per owner
        ("0009991008", date(2026, 2, 9), date(2026, 2, 10), 1000.0, 10500.0)]
    assert all(p.cik == "0009990001" for p in out)
    assert stats["coded_p_filings"] == 3 and stats["coded_p_filings_without_director_or_officer"] == 1


def test_form345_tables_match_by_name_whatever_the_extension():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SUBMISSION.txt", _tsv(SUB_COLS, [_sub("A1", "06-FEB-2026")]))
        zf.writestr("x/NONDERIV_TRANS.TSV", _tsv(TRANS_COLS, [_buy("A1")]))
        zf.writestr("ReportingOwner.tsv", _tsv(OWNER_COLS, [_owner("A1", "9991001", "OFFICER")]))
    assert len(SH.parse_form345(buf.getvalue(), [IN], CAL)) == 1  # the readme writes relationships in upper case


def test_form345_counts_malformed_lines_and_stops_on_a_changed_schema():
    subs = [_sub("A1", "06-FEB-2026")]
    trans = [_buy("A1")]
    owners = [_owner("A1", "9991001", "Officer")]
    text = _tsv(SUB_COLS, subs) + "A9\tonly-two-fields\n"
    stats: dict = {}
    out = SH.parse_form345(_zip(subs, trans, owners, extra={"SUBMISSION.tsv": text}), [IN], CAL, stats)
    assert len(out) == 1 and stats["malformed_lines"] == 1
    with pytest.raises(RunStop) as lost:
        SH.parse_form345(_zip(subs, trans, owners, drop="REPORTINGOWNER.tsv"), [IN], CAL)
    assert lost.value.status == SH.STATUS_SEC_SCHEMA
    renamed = _tsv(tuple(c.replace("TRANS_CODE", "TRANSACTION_CODE") for c in TRANS_COLS), trans)
    with pytest.raises(RunStop) as changed:
        SH.parse_form345(_zip(subs, trans, owners, extra={"NONDERIV_TRANS.tsv": renamed}), [IN], CAL)
    assert "TRANS_CODE" in changed.value.reason
    with pytest.raises(RunStop):
        SH.parse_form345(b"not a zip", [IN], CAL)


def test_quarters_cover_the_window():
    assert SH.quarters(date(2018, 1, 1), date(2018, 4, 1)) == [(2018, 1), (2018, 2)]
    assert len(SH.quarters(SH.HISTORY_START, date(2026, 9, 18))) == 35


# ---- the whole history ----------------------------------------------------------------------------------------------
def _world(missing_quarter: bool = False) -> dict[str, bytes]:
    tickers = {"0": {"cik_str": 9990001, "ticker": "DMSEMI1", "title": "DM Semi"},
               "1": {"cik_str": 9990002, "ticker": "DMBANK-B", "title": "DM Bank"}}
    sub1 = {"cik": "9990001", "name": "DM Semi", "filings": {"recent": _recent([
        _filing(1, "8-K", "2026-01-29T16:05:00", "2.02"), _filing(2, "8-K", "2026-04-30T07:30:00", "2.02")])}}
    sub2 = {"cik": "9990002", "name": "DM Bank", "filings": {"recent": _recent([
        _filing(3, "8-K", "2026-02-12T08:00:00", "2.02")])}}
    z1 = _zip([_sub("A1", "06-FEB-2026")], [_buy("A1")], [_owner("A1", "9991001", "Director")])
    z2 = _zip([_sub("B1", "15-MAY-2026", issuer="9990002")], [_buy("B1")], [_owner("B1", "9991002", "Officer")])
    routes = {"https://www.sec.gov/files/company_tickers.json": json.dumps(tickers).encode(),
              "https://data.sec.gov/submissions/CIK0009990001.json": json.dumps(sub1).encode(),
              "https://data.sec.gov/submissions/CIK0009990002.json": json.dumps(sub2).encode(),
              SH.FORM345_URLS[1].format(year=2026, quarter=1): z1}  # an older quarter: the original path
    if not missing_quarter:
        routes[SH.FORM345_URLS[0].format(year=2026, quarter=2)] = z2  # a newer quarter: the moved path
    return routes


def test_the_history_joins_both_sources_by_cik_and_publishes_only_counts():
    seen: list[str] = []
    h = SH.load_sec_history(["DMSEMI1", "DMBANK.B", "DMNOCIK"], start=date(2026, 1, 1), end=date(2026, 6, 30),
                            cal=CAL, http=_http(_world(), seen), progress=lambda _m: None)
    assert [r.session for r in h.releases["DMSEMI1"]] == [date(2026, 1, 30), date(2026, 4, 30)]
    assert [r.session for r in h.releases["DMBANK.B"]] == [date(2026, 2, 12)]
    assert [p.session for p in h.purchases["DMSEMI1"]] == [date(2026, 2, 9)]
    assert [p.session for p in h.purchases["DMBANK.B"]] == [date(2026, 5, 18)]
    assert h.releases["DMNOCIK"] == [] and h.purchases["DMNOCIK"] == []
    s = h.summary()
    assert (s["symbols"], s["symbols_with_cik"], s["earnings_releases"], s["insider_purchases"]) == (3, 2, 3, 2)
    assert s["requests"] == len(seen) == 6  # tickers, 2 submissions, 2026q1 at its second path, 2026q2 at its first
    text = json.dumps(s)
    assert not any(x in text for x in ("DMSEMI1", "DMBANK", "9990001", "9991001"))


def test_a_missing_quarter_stops_the_run_rather_than_reading_as_no_purchase():
    with pytest.raises(RunStop) as stop:
        SH.load_sec_history(["DMSEMI1"], start=date(2026, 1, 1), end=date(2026, 6, 30), cal=CAL,
                            http=_http(_world(missing_quarter=True), []), progress=lambda _m: None)
    assert stop.value.status == SH.STATUS_SEC_INCOMPLETE and "2026q2" in stop.value.reason


def test_a_refused_request_stops_the_run():
    routes = _world()
    routes["https://data.sec.gov/submissions/CIK0009990001.json"] = 400  # non-retryable
    with pytest.raises(RunStop) as stop:
        SH.load_sec_history(["DMSEMI1"], start=date(2026, 1, 1), end=date(2026, 6, 30), cal=CAL,
                            http=_http(routes, []), progress=lambda _m: None)
    assert stop.value.status == SH.STATUS_SEC_INCOMPLETE


def test_synthetic_history_is_deterministic_and_marked():
    days = CAL.sessions_between(date(2024, 1, 2), date(2024, 12, 31))
    a, b = SH.synthetic_history(["DMX", "DMY"], days), SH.synthetic_history(["DMY", "DMX"], days)
    assert a.releases == b.releases and a.purchases == b.purchases and a.stats == {"synthetic": True}
    assert all(r.session >= r.day for rs in a.releases.values() for r in rs)
