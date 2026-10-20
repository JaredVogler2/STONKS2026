from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest

from apex_worker.catalysts.earnings import EarningsCalendar
from apex_worker.catalysts.entities import EntityResolver
from apex_worker.catalysts.http import HttpClientConfig, PoliteHttpClient
from apex_worker.catalysts.macro import FredClient, load_release_calendar
from apex_worker.catalysts.models import is_available
from apex_worker.catalysts.news import FeedAllowlist
from apex_worker.catalysts.pipeline import CatalystPipeline, point_in_time
from apex_worker.catalysts.sources import (
    Capability,
    CongressManualSource,
    EarningsCalendarSource,
    FredSeriesSource,
    MacroCalendarSource,
    NewsSource,
    SecFilingsSource,
    SourceBatch,
    SourceState,
)
from apex_worker.catalysts.tickers import TickerMap
from apex_worker.contracts import validate
from apex_worker.timeutil import ReplayClock, parse_utc

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "catalysts"
NOW = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)
RSS_URL = "https://feeds.demo-wire.example/markets.rss"
ATOM_URL = "https://newsroom.demo-regulator.example/atom.xml"
FORM4 = {
    "0009990101-26-000007": "form4_open_market_purchase.xml",
    "0009990102-26-000003": "form4_10b5_1_sale.xml",
    "0009990103-26-000002": "form4_award_code_a.xml",
    "0009990104-26-000005": "form4_exercise_m_withholding_f.xml",
    "0009990105-26-000001": "form4_multiple_transactions.xml",
}


def _read(name: str) -> bytes:
    return (FIX / name).read_bytes()


@pytest.fixture(scope="module")
def tmap():
    return TickerMap.load(_read("company_tickers.json"), _read("ticker_aliases.json"))


class _Exploding:
    name = "exploding"

    def capability(self):
        return Capability(self.name, SourceState.ENABLED, "test source", automatic=False)

    def collect(self, ctx) -> SourceBatch:
        raise RuntimeError("upstream changed its format")


def _sources(form4_docs=None):
    allowlist = FeedAllowlist.from_json(_read("news_allowlist.json"))
    resolver = EntityResolver.from_json(_read("entity_aliases.json"))
    return [
        SecFilingsSource(
            submissions=[
                _read("edgar_submissions_CIK0009990001.json"),
                _read("edgar_submissions_CIK0009990010.json"),
                _read("quarantine_weird_html_in_xml.xml"),
            ],
            form4_documents=form4_docs or {acc: _read(name) for acc, name in FORM4.items()},
        ),
        SecFilingsSource.live(lambda ua: PoliteHttpClient(HttpClientConfig.for_sec(ua)), None, ["9990001"]),
        NewsSource(
            allowlist,
            resolver,
            payloads={
                RSS_URL: _read("news_rss_syndicated.xml"),
                ATOM_URL: _read("news_atom.xml"),
                "https://unlisted-feed.example/rss": _read("news_rss_syndicated.xml"),
            },
        ),
        CongressManualSource([_read("congress_manual.csv").decode("utf-8")]),
        EarningsCalendarSource(
            EarningsCalendar.from_manual_json(_read("earnings_manual.json")), ["DMSEMI1", "DMBANK2", "DMBIO4"]
        ),
        EarningsCalendarSource(None),
        MacroCalendarSource(load_release_calendar(_read("macro_release_calendar.json"))),
        FredSeriesSource(FredClient(api_key=None)),
        _Exploding(),
    ]


@pytest.fixture(scope="module")
def result(tmap):
    pipe = CatalystPipeline(
        _sources(), mode="backfill", is_demo=True, ticker_map=tmap, clock=ReplayClock(NOW)
    )
    return pipe.run()


def test_every_emitted_catalyst_is_contract_valid(result):
    assert len(result.catalysts) > 40
    for payload in result.catalysts:
        validate("Catalyst", payload)
        assert payload["is_demo"] is True
    assert len({p["id"] for p in result.catalysts}) == len(result.catalysts)


def test_capability_gating_reports_disabled_states_honestly(result):
    caps = {(c["source"], c["status"]): c for c in result.capabilities}
    sec_disabled = caps[("sec_edgar", "DISABLED")]
    assert "SEC requires a descriptive User-Agent with contact email" in sec_disabled["reason"]
    assert caps[("fred", "DISABLED")]["reason"].startswith("FRED/ALFRED requires a free API key")
    congress = caps[("congress_manual", "ENABLED")]
    assert congress["automatic"] is False and "DISABLED" in congress["reason"]
    assert ("earnings_calendar", "DISABLED") in caps and ("earnings_calendar", "ENABLED") in caps
    assert ("sec_edgar", "ENABLED") in caps and ("news_rss", "ENABLED") in caps


def test_problems_are_quarantined_not_raised(result):
    reasons = Counter(q.reason_code for q in result.quarantined)
    assert reasons["PARSE_ERROR"] >= 1  # weird HTML in place of a submissions document
    assert reasons["NOT_ALLOWLISTED"] == 1
    assert reasons["SOURCE_FAILED"] == 1
    assert reasons["DISCLOSURE_BEFORE_TRANSACTION"] == 1


def test_syndicated_story_counted_once(result):
    guidance = [
        c for c in result.catalysts if c["catalyst_type"] == "guidance_raise" and c.get("symbol") == "DMSEMI1"
    ]
    assert len(guidance) == 1
    assert "MULTI_SOURCE_CONFIRMED" in guidance[0]["reason_codes"]
    cluster = next(cl for cl in result.clusters if cl.representative.id == guidance[0]["id"])
    assert len(cluster.members) == 5


def test_type_mix(result):
    types = Counter(c["catalyst_type"] for c in result.catalysts)
    assert types["insider_open_market_purchase"] == 2
    assert types["insider_award_or_exercise"] == 2
    assert types["insider_sale"] == 1
    assert types["political_disclosure"] == 4
    assert types["macro_release"] == 3
    assert types["earnings_scheduled"] == 3


def test_point_in_time_after_hours_earnings(result):
    close_oct14 = datetime(2026, 10, 14, 20, 0, tzinfo=UTC)  # 16:00 EDT
    before = point_in_time(result.catalysts, close_oct14, "DMSEMI1")
    assert not any(c.get("accession_number") == "0009990001-26-000041" for c in before)
    after = point_in_time(result.catalysts, datetime(2026, 10, 15, 20, 0, tzinfo=UTC), "DMSEMI1")
    assert any(c.get("accession_number") == "0009990001-26-000041" for c in after)
    assert all(is_available(c, close_oct14) for c in before)


def test_point_in_time_political_disclosure(result):
    def politics(t):
        return [
            c
            for c in point_in_time(result.catalysts, t, "DMSEMI1")
            if c["catalyst_type"] == "political_disclosure"
        ]

    assert politics(datetime(2026, 8, 1, 20, 0, tzinfo=UTC)) == []
    assert politics(datetime(2026, 9, 9, 20, 0, tzinfo=UTC)) == []
    assert len(politics(datetime(2026, 9, 12, 14, 0, tzinfo=UTC))) == 1


def test_point_in_time_ticker_reuse_no_entity_mixing(result, tmap):
    t = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)
    naive = point_in_time(result.catalysts, t, "DMRX")
    assert {c.get("cik") for c in naive} == {
        "0009990010",
        "0009990011",
    }  # symbol-only matching mixes entities
    strict = point_in_time(result.catalysts, t, "DMRX", ticker_map=tmap)
    assert strict and {c.get("cik") for c in strict} == {"0009990011"}
    as_of_2021 = point_in_time(result.catalysts, datetime(2021, 6, 28, tzinfo=UTC), "DMRX", ticker_map=tmap)
    assert as_of_2021 and {c["cik"] for c in as_of_2021} == {"0009990010"}
    gap = point_in_time(result.catalysts, datetime(2022, 6, 1, tzinfo=UTC), "DMRX", ticker_map=tmap)
    assert gap == []


def test_point_in_time_market_wide_and_ordering(result):
    t = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)
    rows = point_in_time(result.catalysts, t, "DMBANK2", include_market_wide=True)
    assert any(c["catalyst_type"] == "macro_release" for c in rows)
    stamps = [parse_utc(c["available_at"]) for c in rows]
    assert stamps == sorted(stamps)
    assert not any(
        c["catalyst_type"] == "macro_release" for c in point_in_time(result.catalysts, t, "DMBANK2")
    )


def test_form4_parse_failure_quarantined_and_rest_continues(tmap):
    docs = {acc: _read(name) for acc, name in FORM4.items()}
    docs["0009990103-26-000002"] = _read("quarantine_weird_html_in_xml.xml")
    res = CatalystPipeline(
        _sources(docs), mode="backfill", is_demo=True, ticker_map=tmap, clock=ReplayClock(NOW)
    ).run()
    types = Counter(c["catalyst_type"] for c in res.catalysts)
    assert types["insider_award_or_exercise"] == 1
    form4_q = [q for q in res.quarantined if q.url.endswith("/000999010326000002/wk-form4_1748896200.xml")]
    assert len(form4_q) == 1 and form4_q[0].reason_code == "PARSE_ERROR"
    assert form4_q[0].parser_version == "form4/1.0.0"


def test_run_is_deterministic_and_scoring_optional(tmap):
    a = CatalystPipeline(
        _sources(), mode="backfill", is_demo=True, ticker_map=tmap, clock=ReplayClock(NOW)
    ).run()
    b = CatalystPipeline(
        _sources(), mode="backfill", is_demo=True, ticker_map=tmap, clock=ReplayClock(NOW)
    ).run(score_at=NOW)
    assert [c["id"] for c in a.catalysts] == [c["id"] for c in b.catalysts]
    assert all("impact_score" not in c for c in a.catalysts)
    scored = [c for c in b.catalysts if "impact_score" in c]
    assert scored and all(0 <= c["impact_score"] <= 100 for c in scored)
