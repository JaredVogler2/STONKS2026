"""Discovery V3 execution-cost study (PR-D): the registered plan, quote filtering, cost arithmetic, instants on
half days, the seeded stratified sample, aggregates-only outputs, and the blocked / budget paths."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from apex_worker.discovery.v3 import costs as C
from apex_worker.discovery.v3.fixture_micro import FakeAlpacaMicro, fixture_half_spread_bps
from apex_worker.market.calendar import default_calendar
from apex_worker.research.realeq.fixture import FIXTURE_NOW

COST_STUDY_1_SHA256 = "94477312f4dc1b86689b4869a1d659c1ecb88dfa818795e2c0e6dbf81b2ca576"  # EXECUTION_COST_STUDY.md
SMALL = replace(C.COST_STUDY_1, plan_id="cost-study-test", years=(2020, 2021), per_cell=2, s1_events=4)


def test_plan_is_the_registered_one():
    """If this fails the plan changed after registration: register a NEW plan id instead of editing this one."""
    assert C.COST_STUDY_1.sha256() == COST_STUDY_1_SHA256
    doc = (Path(__file__).resolve().parents[3] / "docs" / "discovery-v3" / "EXECUTION_COST_STUDY.md").read_text()
    assert COST_STUDY_1_SHA256 in doc


def test_valid_quote_skips_crossed_locked_and_empty_quotes():
    qs = [{"bp": 10.02, "ap": 10.0, "bs": 1, "as": 1}, {"bp": 10.0, "ap": 10.0, "bs": 1, "as": 1},
          {"bp": 10.0, "ap": 10.02, "bs": 0, "as": 1}, {"bp": 9.99, "ap": 10.01, "bs": 3, "as": 2}]
    assert C.valid_quote(qs) == qs[3] and C.valid_quote(qs[:3]) is None and C.valid_quote([]) is None


def test_event_metrics_arithmetic_and_signs():
    q = {"open+60": {"bp": 99.95, "ap": 100.05}, "open+300": None, "12:00": {"bp": 100.0, "ap": 100.1},
         "close-60": {"bp": 101.9, "ap": 102.1}}
    m = C.event_metrics(100.0, 102.0, q, {"open": 100.0, "vwap": 100.02}, {"vwap": 101.98})
    assert m["half_spread_open+60"] == pytest.approx(5.0, abs=1e-3)  # 0.05 / 100
    assert m["buy_cost_open+60"] == pytest.approx(5.0, abs=1e-3)  # ask 100.05 vs open 100
    assert m["sell_cost_close-60"] == pytest.approx(0.1 / 102 * 1e4, abs=1e-3)  # bid 101.9 vs close 102: a cost
    assert m["per_side_marketable"] == pytest.approx((5.0 + 0.1 / 102 * 1e4) / 2, abs=1e-3)
    assert m["mid_drift_open+60"] == 0.0 and m["minute_open_vs_daily_open"] == 0.0
    assert m["first_minute_vwap_vs_open"] == pytest.approx(2.0, abs=1e-3)
    assert m["last_minute_vwap_vs_close"] == pytest.approx(0.02 / 102 * 1e4, abs=1e-3)
    assert m["buy_cost_open+300"] is None and m["half_spread_open+300"] is None


def test_instants_respect_half_days_and_daylight_saving():
    cal = default_calendar()
    assert C.instant_utc(cal, date(2024, 11, 29), "close-60") == datetime(2024, 11, 29, 17, 59, tzinfo=UTC)  # 13:00
    assert C.instant_utc(cal, date(2024, 11, 29), "open+60") == datetime(2024, 11, 29, 14, 31, tzinfo=UTC)
    assert C.instant_utc(cal, date(2024, 7, 3), "12:00") == datetime(2024, 7, 3, 16, 0, tzinfo=UTC)  # EDT
    assert C.instant_utc(cal, date(2025, 1, 15), "12:00") == datetime(2025, 1, 15, 17, 0, tzinfo=UTC)  # EST


def test_adapter_get_quotes_is_one_bounded_newest_first_request():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(dict(req.url.params))
        return httpx.Response(200, json={"quotes": {"AAA": [{"t": "x", "bp": 1, "ap": 2}]}, "next_page_token": "more"})

    from apex_worker.market.adapters.alpaca import AlpacaAdapter
    from apex_worker.market.ratelimit import TokenBucket

    now = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    a = AlpacaAdapter("k", "s", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                      bucket=TokenBucket(1e9, 1e9), now=lambda: now)
    out = asyncio.run(a.get_quotes("AAA", datetime(2026, 1, 5, 14, 50, tzinfo=UTC), now))
    assert out == [{"t": "x", "bp": 1, "ap": 2}] and len(seen) == 1  # no paging even with a next token
    p = seen[0]
    assert p["sort"] == "desc" and p["feed"] == "sip" and p["limit"] == "50"
    assert p["end"] == "2026-01-05T14:44:00Z"  # clamped behind the SIP delay


@pytest.fixture(scope="module")
def data():
    from apex_worker.discovery.v3.pipeline import load_data

    return load_data("fixture", per_sector=4)


def _factory(**kw):
    from apex_worker.research.realeq.fixture_v2 import factor_frames_v2

    fake = FakeAlpacaMicro(factor_frames_v2(per_sector=4), corporate_actions=[], **kw)
    return fake, (lambda: fake.adapter(FIXTURE_NOW))


def test_sample_is_seeded_stratified_and_causal_in_its_strata(data):
    ev1 = C.draw_sample(data.pre, data.b, data.quarantine, SMALL)
    ev2 = C.draw_sample(data.pre, data.b, data.quarantine, SMALL)
    assert ev1 == ev2 and C.sample_sha256(ev1) == C.sample_sha256(ev2)
    rnd = [e for e in ev1 if e.source == "random"]
    cells = {(e.year, e.tercile) for e in rnd}
    assert cells == {(y, k) for y in SMALL.years for k in (1, 2, 3)}
    assert all(sum(1 for e in rnd if (e.year, e.tercile) == c) == SMALL.per_cell for c in cells)
    assert len([e for e in ev1 if e.source == "S1"]) == SMALL.s1_events
    lo = data.b.dev_range[0]
    for e in ev1:
        assert lo < e.t and data.pre.sessions[e.t].date() == e.day
        if e.source == "random":
            assert data.pre.eligible[e.symbol][e.t - 1]  # eligible at the DECISION close, not the entry session
    other = C.draw_sample(data.pre, data.b, data.quarantine, replace(SMALL, seed=SMALL.seed + 1))
    assert C.sample_sha256(other) != C.sample_sha256(ev1)


def test_end_to_end_on_the_fixture_writes_aggregates_only(data, tmp_path):
    fake, factory = _factory()
    s = C.run_cost_study(SMALL, data, factory, tmp_path, experiment_id="demo-cost-test", progress=lambda _m: None)
    assert s["status"] == "COMPLETED" and s["measured"] == s["events"] == 3 * 2 * 2 + 4
    body = json.loads((tmp_path / "cost_study.json").read_text())
    hs = body["groups"]["all"]["half_spread_open+60"]
    syms = list(data.pre.stocks)
    assert min(fixture_half_spread_bps(x) for x in syms) - 1e-6 <= hs["p50"] <= max(fixture_half_spread_bps(x) for x in syms)
    assert body["groups"]["year=2020"]["per_side_marketable"]["n"] == 6
    assert body["groups"]["gap=>=3%"]["per_side_marketable"].get("suppressed")  # cells under min_cell are suppressed
    assert body["cost_model_proposal"]["overall_bps"] <= 10 and body["assumption_check"]["share_of_events_above_25bps"] == 0
    raw = (tmp_path / "cost_study.json").read_text()
    assert not any(f'"{x}"' in raw for x in syms)  # no per-symbol (per-event) data is published
    from apex_worker.research.forward.records import publication_problems

    assert publication_problems(body, {}) == []
    assert "COMPLETED" in (tmp_path / "COST_STUDY.md").read_text()
    kinds = {p.get("timeframe", "quotes") for p in fake.micro_requests}
    assert kinds == {"1Day", "1Min", "quotes"}


def test_unentitled_quotes_are_source_blocked_never_estimated(data, tmp_path):
    _fake, factory = _factory(deny_quotes=True)
    s = C.run_cost_study(SMALL, data, factory, tmp_path, experiment_id="demo-cost-blocked", progress=lambda _m: None)
    body = json.loads((tmp_path / "cost_study.json").read_text())
    assert s["status"] == body["status"] == "SOURCE_BLOCKED" and "403" in body["reason"]
    assert "groups" not in body and "cost_model_proposal" not in body


def test_request_cap_stops_the_study(data, tmp_path):
    _fake, factory = _factory()
    s = C.run_cost_study(replace(SMALL, max_requests=10), data, factory, tmp_path, experiment_id="demo-cost-cap",
                         progress=lambda _m: None)
    assert s["status"] == "BUDGET_EXHAUSTED" and s["requests"] <= 10
    assert "groups" not in json.loads((tmp_path / "cost_study.json").read_text())


def test_cli_guards_and_plan_listing(tmp_path, capsys):
    from apex_worker.cli import main

    assert main(["research", "discovery", "costs", "--plan", "nope", "--source", "fixture", "--experiment-id",
                 "demo-x", "--out", str(tmp_path)]) == 5
    assert main(["research", "discovery", "costs", "--plan", "cost-study-1", "--source", "fixture",
                 "--experiment-id", "real-looking", "--out", str(tmp_path)]) == 5
    capsys.readouterr()
    assert main(["research", "discovery", "plans"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["cost-study-1"]["sha256"] == COST_STUDY_1_SHA256 and listed["cost-study-1"]["kind"] == "measurement"
