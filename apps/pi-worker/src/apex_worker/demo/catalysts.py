"""DEMO catalysts: the real Phase 05 parsers run over the fictional fixtures in fixtures/catalysts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from apex_worker.catalysts.earnings import EarningsCalendar
from apex_worker.catalysts.entities import EntityResolver
from apex_worker.catalysts.macro import FredClient, load_release_calendar
from apex_worker.catalysts.news import FeedAllowlist
from apex_worker.catalysts.pipeline import CatalystPipeline
from apex_worker.catalysts.sources import (
    CongressManualSource,
    EarningsCalendarSource,
    FredSeriesSource,
    MacroCalendarSource,
    NewsSource,
    SecFilingsSource,
)
from apex_worker.catalysts.tickers import TickerMap
from apex_worker.timeutil import ReplayClock

FIX = Path(__file__).resolve().parents[5] / "fixtures" / "catalysts"
FORM4 = {
    "0009990101-26-000007": "form4_open_market_purchase.xml",
    "0009990102-26-000003": "form4_10b5_1_sale.xml",
    "0009990103-26-000002": "form4_award_code_a.xml",
    "0009990104-26-000005": "form4_exercise_m_withholding_f.xml",
    "0009990105-26-000001": "form4_multiple_transactions.xml",
}
FEEDS = {"https://feeds.demo-wire.example/markets.rss": "news_rss_syndicated.xml",
         "https://newsroom.demo-regulator.example/atom.xml": "news_atom.xml"}


def _read(name: str) -> bytes:
    return (FIX / name).read_bytes()


def demo_pipeline(now: datetime | None = None) -> CatalystPipeline:
    tmap = TickerMap.load(_read("company_tickers.json"), _read("ticker_aliases.json"))
    sources = [
        SecFilingsSource(submissions=[_read("edgar_submissions_CIK0009990001.json"), _read("edgar_submissions_CIK0009990010.json")],
                         form4_documents={acc: _read(n) for acc, n in FORM4.items()}),
        NewsSource(FeedAllowlist.from_json(_read("news_allowlist.json")), EntityResolver.from_json(_read("entity_aliases.json")),
                   payloads={url: _read(n) for url, n in FEEDS.items()}),
        CongressManualSource([_read("congress_manual.csv").decode("utf-8")]),
        EarningsCalendarSource(EarningsCalendar.from_manual_json(_read("earnings_manual.json")), ["DMSEMI1", "DMBANK2", "DMBIO4"]),
        MacroCalendarSource(load_release_calendar(_read("macro_release_calendar.json"))),
        FredSeriesSource(FredClient(api_key=None)),  # reports DISABLED honestly without a key
    ]
    return CatalystPipeline(sources, mode="backfill", is_demo=True, ticker_map=tmap,
                            clock=ReplayClock(now or datetime(2026, 10, 20, 14, 0, tzinfo=UTC)))


def demo_catalysts() -> list[dict]:
    return demo_pipeline().run().catalysts


def demo_capabilities() -> list[dict]:
    return demo_pipeline().capabilities()
