"""Live picking path guards (docs/TESTING_AND_METHODOLOGY_REPORT.md APEX-011, -013, -019):

* signals reach the Discord group channels only in DEMO and GROUP_POSITION_ALERTS; PAPER_LIVE_DATA and
  PERSONAL_RESEARCH record the would-be setups for the owner and publish nothing;
* the end-of-day scan waits until the delayed SIP bars cover the closing auction;
* the nightly SEC earnings calendar feeds the earnings gate (WITHIN_HORIZON vetoes; nothing is ever inferred from
  missing data);
* the S&P 500 snapshot gives the live names their sectors.

Issuers, tickers and CIKs other than the pinned S&P 500 snapshot are fictional; no test touches the network."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from apex_worker.catalysts.earnings import estimate_next_from_8k
from apex_worker.catalysts.http import TokenBucket
from apex_worker.catalysts.sec import parse_submissions
from apex_worker.demo.synthetic import generate
from apex_worker.market.adapters.alpaca import SIP_DELAY
from apex_worker.market.calendar import default_calendar
from apex_worker.research import sec_history as SH
from apex_worker.runtime import earnings_job as EJ
from apex_worker.runtime.universe_job import asset_meta, snapshot_sectors
from apex_worker.runtime.worker import LIVE_BENCHMARK, LIVE_SECTOR_ETFS, Schedule, sec_contact
from apex_worker.signals.policy import (
    GROUP_OUTPUT_MODES,
    GROUP_OUTPUT_WITHHELD,
    PolicyDecision,
    PolicyOutcome,
    withhold_signals,
)
from apex_worker.timeutil import ReplayClock
from test_worker import DAY, make

CAL = default_calendar()
FIX = Path(__file__).resolve().parents[3] / "fixtures" / "catalysts"
UA = "APEX Research Demo ops@example.com"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"


@pytest.fixture(scope="module")
def market():
    return generate()


# ---- group output ---------------------------------------------------------------------------------------------------
def test_withheld_signals_keep_their_decision_and_tier_but_publish_nothing():
    sig = {"id": "s1", "symbol": "DMA"}
    out = PolicyOutcome("2026-08-05", [PolicyDecision("c1", "DMA", "RESEARCH_SETUP", ["MODEL_UNVALIDATED"], sig),
                                       PolicyDecision("c2", "DMB", "REJECTED", ["SECTOR_DAILY_CAP"])])
    held = withhold_signals(out)
    assert held.signals == [] and held.no_trade_reason == GROUP_OUTPUT_WITHHELD
    assert held.decisions[0].outcome == "REJECTED"
    assert held.decisions[0].reasons[:3] == [GROUP_OUTPUT_WITHHELD, "RESEARCH_SETUP", "MODEL_UNVALIDATED"]
    assert held.decisions[1] == out.decisions[1]
    quiet = PolicyOutcome("2026-08-05", [out.decisions[1]], "NO_CANDIDATE_PASSED_POLICY")
    assert withhold_signals(quiet).no_trade_reason == "NO_CANDIDATE_PASSED_POLICY"
    assert {"DEMO", "GROUP_POSITION_ALERTS"} == set(GROUP_OUTPUT_MODES)


@pytest.mark.parametrize("mode", ["PAPER_LIVE_DATA", "PERSONAL_RESEARCH"])
def test_private_modes_publish_nothing_and_show_the_owner_the_would_be_setups(tmp_path, market, mode):
    clock = ReplayClock(DAY.close_utc + timedelta(minutes=25))
    w, srv, local = make(tmp_path, market, clock, mode=mode)
    out = asyncio.run(w.run_once())
    assert out["end_of_day"]["published"] == 0
    assert not srv.signals and not any(r["kind"] == "NEW_SIGNAL" for r in srv.outbox.values())
    assert not any(r.kind == "NEW_SIGNAL" for r in local.outbox_rows())
    (scan,) = srv.scans.values()
    held = [d for d in scan["metrics"]["decisions"] if d["reasons"][:1] == [GROUP_OUTPUT_WITHHELD]]
    assert held, "the same session publishes a research setup in DEMO; here it is withheld"
    assert all(d["outcome"] == "REJECTED" and d["reasons"][1] in ("RESEARCH_SETUP", "HIGH_CONVICTION") for d in held)
    assert scan["no_trade_reason"] == GROUP_OUTPUT_WITHHELD


def test_group_mode_still_publishes(tmp_path, market):
    clock = ReplayClock(DAY.close_utc + timedelta(minutes=25))
    w, srv, _local = make(tmp_path, market, clock, mode="GROUP_POSITION_ALERTS")
    asyncio.run(w.run_once())
    assert srv.signals and {s["tier"] for s in srv.signals.values()} == {"RESEARCH_SETUP"}


# ---- end-of-day timing ----------------------------------------------------------------------------------------------
def test_the_end_of_day_scan_waits_until_the_delayed_bars_cover_the_close():
    # SIP bars are requested up to now - SIP_DELAY; at close + 15 min that ended a minute before the closing auction
    assert timedelta(minutes=Schedule().eod_delay_minutes) >= SIP_DELAY + timedelta(minutes=2)


# ---- sectors --------------------------------------------------------------------------------------------------------
def test_the_snapshot_gives_live_names_their_sectors():
    sectors = snapshot_sectors()
    assert len(sectors) > 490 and set(sectors.values()) == set(LIVE_SECTOR_ETFS)
    assert sectors["MMM"] == "industrials" and sectors["GOOG"] == sectors["GOOGL"]  # every share class
    uni = {"meta": {"MMM": {"instrument": "common", "sector": None}, "DMZZZ": {"instrument": "common"}}}
    meta = asset_meta(uni, sectors, LIVE_SECTOR_ETFS, LIVE_BENCHMARK)
    assert meta["MMM"].sector == "industrials" and meta["DMZZZ"].sector is None


# ---- SEC contact ----------------------------------------------------------------------------------------------------
def test_the_sec_contact_is_normalized_and_never_logged(caplog):
    class Cfg:
        sec_user_agent = None

    assert sec_contact(Cfg) is None
    Cfg.sec_user_agent = "ops@example.com"
    assert sec_contact(Cfg) == "APEX Research ops@example.com"  # a bare email is accepted, as in the research lanes
    Cfg.sec_user_agent = "curl/8 private@example.com"
    with caplog.at_level(logging.WARNING):
        assert sec_contact(Cfg) is None
    assert "private@example.com" not in caplog.text and "not a usable contact" in caplog.text


# ---- earnings calendar ----------------------------------------------------------------------------------------------
class FakeTime:
    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(seconds, 1e-6)

    def now(self) -> datetime:
        return datetime(2026, 10, 27, tzinfo=UTC)


def _http(routes: dict[str, bytes | int], seen: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert request.headers["User-Agent"] == UA
        body = routes.get(str(request.url))
        if body is None or isinstance(body, int):
            return httpx.Response(body or 404)
        return httpx.Response(200, content=body)

    return SH.sec_http(UA, transport=httpx.MockTransport(handler), bucket=TokenBucket(10.0, time_source=FakeTime()))


def _fixture_doc() -> bytes:
    return (FIX / "edgar_submissions_CIK0009990001.json").read_bytes()


NOW = datetime(2026, 10, 27, 23, 30, tzinfo=UTC)


def _expected_next() -> date:
    filings = parse_submissions(json.loads(_fixture_doc())).filings
    est = estimate_next_from_8k("DMSEMI1", filings, cal=CAL)
    assert est is not None
    return est.day


def test_refresh_gives_a_next_release_window_the_gate_vetoes_and_never_infers_none(tmp_path):
    seen: list[str] = []
    http = _http({SUBMISSIONS.format(cik="0009990001"): _fixture_doc(),
                  SUBMISSIONS.format(cik="0009990002"): 404}, seen)  # (a 5xx is retried, then fails the same way)
    ciks = {"DMSEMI1": "0009990001", "DMBANK2": "0009990002"}
    data = EJ.refresh_earnings(["DMSEMI1", "DMBANK2", "DMNOCIK"], ciks, http, CAL, NOW)
    assert data["failed"] == ["DMBANK2"] and data["no_cik"] == ["DMNOCIK"] and len(seen) == 2
    row = data["symbols"]["DMSEMI1"]
    # the five releases of the last 400 days (the sixth fixture release is older); the estimate is labelled as such
    assert row["reported"][0]["day"] == "2025-10-29" and len(row["reported"]) == 5
    assert row["estimate"]["status"] == "expected" and "NOT a confirmed date" in row["estimate"]["note"]
    book = EJ.EarningsBook(CAL, data, clock=lambda: NOW)
    nxt = _expected_next()
    before = CAL.add_sessions(nxt - timedelta(days=14), 0)  # a 20-session hold from here spans the estimate
    assert book.status("DMSEMI1", before.isoformat(), 20) == "WITHIN_HORIZON"
    far = CAL.add_sessions(nxt - timedelta(days=90), 0)
    assert book.status("DMSEMI1", far.isoformat(), 20) == "UNKNOWN"  # an estimate never confirms "no earnings"
    assert book.status("DMBANK2", before.isoformat(), 20) == "UNKNOWN"
    assert book.status("DMNOCIK", before.isoformat(), 20) == "UNKNOWN"
    cov = book.coverage()
    assert cov["symbols"] == 1 and cov["with_estimate"] == 1 and cov["failed"] == 1 and cov["no_cik"] == 1
    # a calendar saved tonight answers the same tomorrow after a restart
    EJ.save_earnings(tmp_path, data)
    again = EJ.EarningsBook(CAL, EJ.load_earnings(tmp_path), clock=lambda: NOW)
    assert again.status("DMSEMI1", before.isoformat(), 20) == "WITHIN_HORIZON"
    assert EJ.load_earnings(tmp_path / "missing") == {}


def test_a_failed_request_keeps_the_previous_entries():
    ok = EJ.refresh_earnings(["DMSEMI1"], {"DMSEMI1": "0009990001"},
                             _http({SUBMISSIONS.format(cik="0009990001"): _fixture_doc()}, []), CAL, NOW)
    down = EJ.refresh_earnings(["DMSEMI1"], {"DMSEMI1": "0009990001"},
                               _http({SUBMISSIONS.format(cik="0009990001"): 503}, []), CAL, NOW + timedelta(days=1),
                               previous=ok)
    assert down["failed"] == ["DMSEMI1"] and down["symbols"]["DMSEMI1"] == ok["symbols"]["DMSEMI1"]


def test_ciks_come_from_the_snapshot_first_then_one_ticker_file_request():
    seen: list[str] = []
    tickers = (FIX / "company_tickers.json").read_bytes()
    http = _http({"https://www.sec.gov/files/company_tickers.json": tickers}, seen)
    ciks = EJ.resolve_ciks(["MMM", "DMSEMI1", "DMUNKNOWN"], http)
    assert ciks == {"MMM": "0000066740", "DMSEMI1": "0009990001"} and len(seen) == 1
    seen.clear()
    assert EJ.resolve_ciks(["MMM"], http) == {"MMM": "0000066740"} and seen == []  # nothing to look up
    assert EJ.resolve_ciks(["MMM", "DMSEMI1"], _http({}, [])) == {"MMM": "0000066740"}  # ticker file down


# ---- worker wiring --------------------------------------------------------------------------------------------------
def test_worker_reports_earnings_coverage_and_refreshes_before_its_first_scan(tmp_path, market):
    clock = ReplayClock(DAY.close_utc + timedelta(minutes=25))
    w, _srv, _local = make(tmp_path, market, clock)
    w.start()
    assert w.status["earnings"]["status"] == "UNCONFIGURED"
    calls: list[int] = []

    async def refresh(now, symbols):
        calls.append(len(symbols))
        return {"generated_at": None, "symbols": len(symbols), "with_estimate": 3, "failed": 0, "no_cik": 1}

    w2, _srv2, _local2 = make(tmp_path / "w2", market, clock)
    w2.earnings_refresh = refresh
    asyncio.run(w2.run_once())
    assert calls, "the first end-of-day scan after a start refreshes the calendar"
    assert w2.status["earnings"]["status"] == "OK" and w2.status["earnings"]["metrics"]["with_estimate"] == 3

    async def broken(now, symbols):
        raise httpx.ConnectError("down")

    w2.earnings_refresh = broken
    asyncio.run(w2._refresh_earnings(clock.now()))
    assert w2.status["earnings"]["status"] == "DEGRADED" and "previous calendar" in w2.status["earnings"]["detail"]
