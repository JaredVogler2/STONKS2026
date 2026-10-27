"""Exploratory lane v3x-live-picks-v1: the live engine's own picks, recorded prospectively (picks/README.md).

The decisions must be exactly the worker's group-mode picks (the same engine, configuration and caps), recorded without
prices, with controls fixed before any outcome exists; labels must follow the live lifecycle's own rules. Synthetic
fixture only (never market data); no test touches the network."""

from __future__ import annotations

import dataclasses
import functools
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd
import pytest

from apex_worker.catalysts.http import TokenBucket
from apex_worker.discovery.v3 import live_picks as LP
from apex_worker.market.calendar import default_calendar
from apex_worker.research import sec_history as SH
from apex_worker.research.forward import exploratory as X
from apex_worker.research.forward import picks_lane as PL
from apex_worker.research.forward import picks_view as PV
from apex_worker.research.forward.cli import PICKS_ISSUE_BODY, SimClock, _fixture_source, at_et
from apex_worker.research.forward.frozen import config_sha256
from apex_worker.research.forward.records import RAW_BAR_KEYS, publication_problems, verify_chain
from apex_worker.research.forward.source import SourceUnavailable
from apex_worker.research.forward.store import IssueLedgerStore, MemoryGitHubAPI
from apex_worker.research.realeq import universe_v2 as U
from apex_worker.runtime.engine import ApexEngine, EngineConfig
from apex_worker.runtime.worker import LIVE_BENCHMARK, LIVE_SECTOR_ETFS
from apex_worker.signals.policy import PolicyConfig
from apex_worker.timeutil import ReplayClock

CAL = default_calendar()
FIX = Path(__file__).resolve().parents[3] / "fixtures" / "catalysts"
UA = "APEX Research research@example.com"
# The series as this PR registers it: any later change needs a new series id (v2), never an edit of this one.
PICKS_CONFIG_SHA256 = "ed5b78fffbe99de13a099cb90c1cf8fe2dc22ad2807f211bb63ec27904a56305"


def golden() -> tuple[dict, ...]:
    return PL.golden_decisions()  # cached per process


@functools.cache
def frames() -> dict[str, pd.DataFrame]:
    return PL._golden_frames()


def _sessions() -> list[date]:
    return CAL.sessions_between(PL.GOLDEN["first"], PL.GOLDEN["last"])


# ---- registration ------------------------------------------------------------------------------------------------------
def test_the_series_is_registered_exploratory_research_only_and_separate():
    assert PL.SERIES_ID == "v3x-live-picks-v1" and X.EXPLORATORY_POLICIES[-1] is X.LIVE_PICKS
    cfg = PL.series_config(PL.PINNED_FINGERPRINT)
    assert cfg["lane"] == "EXPLORATORY" and cfg["evidence_tier"] == "PROSPECTIVE_EXPLORATORY"
    assert cfg["confirmatory"] is False and cfg["orders"] == "none" and cfg["alerts"] == "none"
    assert cfg["group_actionable"] is False and cfg["promotion"] == "none" and cfg["mode"] == "RESEARCH_ONLY"
    assert cfg["decisions_from"] == PL.DECISIONS_FROM.isoformat() and cfg["golden_fingerprint"] == PL.PINNED_FINGERPRINT
    assert cfg["earnings"]["required"] is True and cfg["view"]["branch"] == "picks-ledger"
    params = cfg["policy"]["parameters"]
    # the worker's defaults, unchanged: one research setup a session at quality >= 75, no model, no catalysts
    assert params["policy"]["research_max_per_session"] == 1 and params["policy"]["research_min_quality"] == 75.0
    assert params["policy"] == json.loads(json.dumps(dataclasses.asdict(EngineConfig().policy)))
    assert params["benchmark"] == LIVE_BENCHMARK and params["sector_etfs"] == LIVE_SECTOR_ETFS
    assert params["lookback_sessions"] == EngineConfig().lookback_sessions
    assert params["controls"]["k"] == 10 and params["controls"]["reserve"] == 30
    X.assert_separate([p.series_id for p in X.EXPLORATORY_POLICIES])


def test_the_registered_series_configuration_is_pinned():
    sha = config_sha256(PL.PicksLane().config(PL.PINNED_FINGERPRINT))
    assert sha == PICKS_CONFIG_SHA256, f"the series changed: {sha} (a changed implementation needs a new series id)"


def test_the_golden_fingerprint_is_pinned():
    assert PL.golden_fingerprint() == PL.PINNED_FINGERPRINT


# ---- decisions -------------------------------------------------------------------------------------------------------
def test_the_lanes_pick_is_the_workers_group_mode_pick():
    """The worker's own end-of-day path (ApexEngine, GROUP_POSITION_ALERTS, default configuration) posts exactly the
    lane's pick on every golden session where no earlier pick is open."""

    class Sink:
        def __init__(self):
            self.posted = []

        def publish_signal(self, signal, tier, intent, now):
            self.posted.append(signal["symbol"])
            return True

        def record_scan(self, scan, outcome, now):
            pass

        def open_signals(self, include_closed=False):
            return []

        def append_signal_events(self, *a):
            return 0

    meta = {s: m for s, m in LP.meta().items() if s in frames()}
    compared, picked = 0, 0
    for day, body in zip(_sessions(), golden(), strict=True):
        if any(o["open_at_scan"] for o in body["open_picks"]):
            continue  # the worker's sink would hold that open signal (the caps are tested below)
        t = pd.Timestamp(day)
        sink = Sink()
        eng = ApexEngine(None, sink, ReplayClock(CAL.session(day).close_utc + timedelta(minutes=20)),
                         EngineConfig(release_mode="GROUP_POSITION_ALERTS", benchmark=LIVE_BENCHMARK,
                                      sector_etfs=dict(LIVE_SECTOR_ETFS)), calendar=CAL, meta=meta)
        eng._bars = {s: df[df.index <= t] for s, df in frames().items()}
        eng.end_of_day(day)
        assert sink.posted == [e["symbol"] for e in body["plans"] if e["kind"] == LP.PICK], day
        compared += 1
        picked += bool(sink.posted)
    assert compared >= 3 and picked >= 1  # a session with a pick, not only agreement on "no pick"


def test_an_open_pick_counts_toward_the_caps_like_a_published_signal():
    """On some golden session the engine would pick a symbol again if its earlier pick were forgotten; the lane, like
    the worker holding a published signal, rejects it (OPEN_SIGNAL_EXISTS) instead."""
    days = _sessions()
    contrasts = 0
    for i, body in enumerate(golden()):
        held = {o["symbol"] for o in body["open_picks"] if o["open_at_scan"]}
        if not held:
            continue
        fresh = LP.decide(frames(), days[i], [], None, CAL)  # the same session with every earlier pick forgotten
        again = [e["symbol"] for e in fresh["plans"] if e["kind"] == LP.PICK and e["symbol"] in held]
        for sym in again:
            assert sym not in [e["symbol"] for e in body["plans"]]
            assert any("OPEN_SIGNAL_EXISTS" in c["reasons"] for c in body["candidates"] if c["symbol"] == sym)
            contrasts += 1
        if contrasts:
            break
    assert contrasts >= 1
    first = next(i for i, b in enumerate(golden()) if any(e["kind"] == LP.PICK for e in b["plans"]))
    pick = next(e for e in golden()[first]["plans"] if e["kind"] == LP.PICK)
    held = next(o for o in golden()[first + 1]["open_picks"] if o["symbol"] == pick["symbol"])
    assert held["open_at_scan"] and held["status_at_scan"] == "PUBLISHED"  # the worker evaluates after its scan


def test_decisions_hold_no_prices_and_plans_straddle_the_close():
    seen_pick = False
    for body in golden():
        assert publication_problems({"body": body}, env={}) == []
        assert not RAW_BAR_KEYS & {k for e in body["plans"] for k in e["plan"]}
        assert sum(e["kind"] == LP.PICK for e in body["plans"]) <= 1
        assert sum(e["kind"] == LP.QUALIFIED for e in body["plans"]) <= LP.QUALIFIED_MAX - 1
        for e in body["plans"]:
            seen_pick |= e["kind"] == LP.PICK
            p = e["plan"]
            assert p["stop"] < p["entry_min"] < 1 < p["entry_max"] < p["target"]
            assert e["confidence"] == "UNVALIDATED" and e["tier"] == "RESEARCH_SETUP" and e["quality"] >= 75
            res = e["controls"]["reserve"]
            syms = [c[0] for c in res]
            assert len(res) <= LP.RESERVE and len(set(syms)) == len(syms)
            assert not {x["symbol"] for x in body["plans"]} & set(syms)  # never a plan of the same session
            assert all(c[3] < c[1] < c[2] < c[4] for c in res)
    assert seen_pick


def test_a_decision_reads_bars_through_its_session_only():
    day = _sessions()[3]
    truncated = {s: df[df.index <= pd.Timestamp(day)] for s, df in frames().items()}
    assert LP.decide(frames(), day, [], None, CAL) == LP.decide(truncated, day, [], None, CAL)


def test_the_control_reserve_is_stratified_seeded_and_never_includes_the_plan():
    def row(sector, atrp, close=100.0):
        return {"status": "PASS", "sector": sector, "close": close, "atr": close * atrp, "atr_pct": atrp,
                "resistance": float("nan")}

    table = {f"T{i}": row("technology", 0.02 + i * 1e-4) for i in range(8)}
    table |= {f"E{i}": row("energy", 0.03 + i * 1e-4) for i in range(8)}
    table |= {f"H{i}": row("health_care", 0.05 + i * 1e-4) for i in range(8)}
    table["X"] = row("technology", 0.021) | {"status": "VETO"}
    terc = LP._terciles(table)
    a = LP.control_reserve(table, terc, "T0", "technology", 1.5, {"T0", "T1"}, "2026-09-08")
    assert a == LP.control_reserve(table, terc, "T0", "technology", 1.5, {"T0", "T1"}, "2026-09-08")
    syms = [c[0] for c in a["reserve"]]
    assert a["stratum"] == "sector_vol" and set(syms) <= {f"T{i}" for i in range(2, 8)} and "X" not in syms
    b = LP.control_reserve(table, terc, "T0", "technology", 1.5, {"T0", "T1"}, "2026-09-09")
    assert sorted(c[0] for c in b["reserve"]) == sorted(syms)  # the same stratum, another seeded order
    lone = LP.control_reserve(table, terc, "Z", "materials", 1.5, set(), "2026-09-08")
    assert lone["stratum"] == "pool"  # no materials stock and Z has no tercile: the whole pool
    for c in a["reserve"]:  # each control carries the plan's stop distance (1.5 ATR, inside the live clamp)
        mid = (c[1] + c[2]) / 2
        assert (mid - c[3]) / table[c[0]]["atr_pct"] == pytest.approx(1.5, abs=0.01)


# ---- labels ----------------------------------------------------------------------------------------------------------
T0 = date(2026, 9, 1)
PLAN = {"entry_min": 0.99, "entry_max": 1.01, "stop": 0.95, "target": 1.10}


def _frame(rows: list[tuple[float, float, float, float]], scale: float = 1.0) -> pd.DataFrame:
    rows = rows + [rows[-1]] * (LP.LIFETIME_SESSIONS + 1 - len(rows))
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in CAL.sessions_between(T0, CAL.add_sessions(T0, len(rows) - 1))])
    return pd.DataFrame([[x * scale for x in r] for r in rows], index=idx,
                        columns=["open", "high", "low", "close"]).assign(volume=1e6)


RISE = [(100, 101, 99, 100), (100, 101, 99, 100.5), (101, 103, 100.5, 102), (102, 105, 101, 104), (104, 111, 103, 110)]
FLAT = [(100, 100.5, 99.5, 100)]
GAP_DOWN = [(100, 101, 99, 100), (100, 101, 99.5, 100), (93, 94, 92, 93)]
ABOVE = [(100, 101, 99, 100), (103, 104, 102, 103), (104, 105, 103, 104)]


def _market(**extra) -> tuple[pd.DatetimeIndex, dict[str, pd.DataFrame]]:
    bars = {"SPY": _frame(FLAT), "XLK": _frame(FLAT)} | {k: _frame(v) for k, v in extra.items()}
    return bars["SPY"].index, bars


def test_a_plan_is_labelled_by_the_live_rules_scaled_by_the_final_close():
    sessions, bars = _market(PICK=RISE, C1=ABOVE, C2=GAP_DOWN, C3=GAP_DOWN)
    e = {"kind": "pick", "symbol": "PICK", "strategy_id": "h1_vcp_breakout", "quality": 80.0, "sector": "technology",
         "plan": PLAN, "controls": {"stratum": "sector", "reserve": [[s, *PLAN.values()] for s in ("C1", "C2", "C3")]}}
    q = {"C3": [sessions[2]]}  # C3's holding window has a corporate-action quarantine: replaced, never counted
    out = LP.label_plan(e, T0.isoformat(), sessions, bars, q)
    fill, exit_ = 100 * 1.001, 110 * 0.999
    assert out["outcome"] == "TARGET" and out["filled"] and out["complete"]
    assert out["net"] == pytest.approx(exit_ / fill - 1, abs=1e-6) and out["sessions_held"] == 3
    assert (out["entry_session"], out["exit_session"]) == (sessions[1].date().isoformat(), sessions[4].date().isoformat())
    assert out["spy"] == 0.0 and out["sector_etf"] == "XLK" and out["vs_spy"] == pytest.approx(out["net"], abs=1e-6)
    ctrl = out["controls"]
    stop_net = (93 * 0.999) / (100 * 1.001) - 1  # C2 gaps through its stop: exit at the open
    assert ctrl["symbols"] == ["C1", "C2"] and ctrl["n"] == 2 and ctrl["fills"] == 1 and ctrl["skipped"] == 1
    assert ctrl["mean_net"] == pytest.approx((0.0 + stop_net) / 2, abs=1e-6)  # C1 never fills: 0, not dropped
    assert out["vs_controls"] == pytest.approx(out["net"] - ctrl["mean_net"], abs=1e-6)
    # a split after the decision rescales the history; the plan is in units of the close, so nothing changes
    _, split = _market(PICK=RISE, C1=ABOVE, C2=GAP_DOWN, C3=GAP_DOWN)
    split["PICK"] = _frame(RISE, 0.5)
    assert LP.label_plan(e, T0.isoformat(), sessions, split, q)["net"] == pytest.approx(out["net"], abs=1e-6)


def test_no_fill_counts_as_zero_and_missing_data_is_never_labelled():
    sessions, bars = _market(PICK=ABOVE)
    e = {"kind": "pick", "symbol": "PICK", "strategy_id": "h2_rs_pullback", "quality": 76.0, "sector": "technology",
         "plan": PLAN, "controls": {"stratum": "none", "reserve": []}}
    out = LP.label_plan(e, T0.isoformat(), sessions, bars, {})
    assert out["outcome"] == "NO_FILL" and out["net"] == 0.0 and out["complete"] and out["controls"]["n"] == 0
    assert out["vs_controls"] is None  # no controls, no comparison
    gone = LP.label_plan(e | {"symbol": "DELISTED"}, T0.isoformat(), sessions, bars, {})
    assert gone["outcome"] == "NO_DATA" and gone["complete"] is False
    body = LP.label({"plans": [e]}, T0.isoformat(), sessions, bars, {})
    assert body["spy_window"] == 0.0 and "NO_FILL" in body["summary"] and "Research only" in body["summary"]


def test_an_outcome_waits_for_missing_data_then_labels_the_gap():
    """A pick without final bars (a halt, a late provider) is not labelled NO_DATA at once: its outcome, and the ones
    after it, wait up to five more sessions; only then is the gap recorded as such."""
    from apex_worker.research.forward.recorder import RunReport
    from apex_worker.research.forward.records import DECISION, GENESIS, Ledger
    from apex_worker.research.forward.source import OutcomeBars

    sessions, bars = _market()  # SPY and XLK only: the pick's own bars never arrive

    class Source:
        requests_used = 0

        def outcome_data(self, start, end, now, symbols=None):
            return OutcomeBars(sessions, bars, {}, {"data_class": "SYNTHETIC_FIXTURE_DEMO"})

    clock = SimClock(at_et(CAL.add_sessions(T0, 24), 19, 23))  # data end t + 22: mature, waited 22 of 27
    lane = PL.PicksLane.fixture(T0)
    rec = PL.PicksRecorder(lane, IssueLedgerStore(MemoryGitHubAPI(clock=clock), lane.series_id), Source(), CAL,
                           clock=clock, producer={}, fingerprint_fn=lambda: "fp", expected_fingerprint="fp")
    led, anchors, rep = Ledger(lane.series_id, lane.lane), {}, RunReport(lane.series_id, lane.lane, "t")
    entry = {"kind": "pick", "symbol": "HALTED", "strategy_id": "h1_vcp_breakout", "quality": 80.0,
             "sector": "technology", "plan": PLAN, "controls": {"stratum": "none", "reserve": []}}
    rec._append(led, rep, anchors, GENESIS, None, {"config": {}})
    rec._append(led, rep, anchors, DECISION, T0.isoformat(), {"plans": [entry]})
    rec._outcomes(led, rep, anchors)
    assert led.of_type("OUTCOME") == [] and any("waits" in m and "HALTED" in m for m in rep.messages)
    clock.now = at_et(CAL.add_sessions(T0, 29), 19, 23)  # waited 27: the gap is labelled, never left open forever
    rec._outcomes(led, rep, anchors)
    (out,) = led.of_type("OUTCOME")
    assert out["body"]["plans"][0]["outcome"] == "NO_DATA" and out["body"]["sessions_after_decision"] == 27


# ---- the recorder, the view and the earnings view ----------------------------------------------------------------------
def test_the_recorder_decides_tracks_open_picks_and_labels_on_the_fixture(tmp_path):
    days = _sessions()[:4]
    clock = SimClock(at_et(days[0], 19, 23))
    api = MemoryGitHubAPI(clock=clock)
    lane = PL.PicksLane.fixture(days[0])
    reports = []
    for when in [at_et(d, 19, 23) for d in days] + [at_et(date(2026, 10, 16), 19, 23)]:
        clock.now = when
        store = IssueLedgerStore(api, lane.series_id, issue_body=PICKS_ISSUE_BODY)
        reports.append(PL.PicksRecorder(lane, store, _fixture_source(frames(), CAL, clock), CAL, clock=clock,
                                        producer={"note": "test"}, outbox=tmp_path / "outbox.json",
                                        fingerprint_fn=lambda: "fp", expected_fingerprint="fp").run())
    rr = IssueLedgerStore(api, lane.series_id).read()
    last = max(r["session"] for r in rr.records if r["record_type"] in ("DECISION", "MISSED_OBSERVATION"))
    exp = [d.isoformat() for d in CAL.sessions_between(days[0], date.fromisoformat(last))]
    assert rr.problems == [] and verify_chain(rr.records, series_id=lane.series_id, lane=lane.lane,
                                              expected_sessions=exp) == []
    decisions = {r["session"]: r for r in rr.records if r["record_type"] == "DECISION"}
    for d, body in zip(days, golden()[:4], strict=True):  # the recorded decisions are the golden ones (same bars)
        rec = decisions[d.isoformat()]["body"]
        assert [e["symbol"] for e in rec["plans"]] == [e["symbol"] for e in body["plans"]]
        assert rec["evidence_tier"] == "PROSPECTIVE_EXPLORATORY" and rec["earnings"] == PL.FIXTURE_EARNINGS
    outs = [r for r in rr.records if r["record_type"] == "OUTCOME"]
    assert {o["session"] for o in outs} == {d.isoformat() for d in days}  # mature by 2026-10-16 (data end 10-14)
    for o in outs:
        d = decisions[o["session"]]
        assert o["body"]["decision"] == {"record_id": d["record_id"], "digest": d["digest"], "session": d["session"]}
        assert [p["symbol"] for p in o["body"]["plans"]] == [e["symbol"] for e in d["body"]["plans"]]
    assert reports[-1].status == "MISSED"  # the sessions between were never run: recorded as missed, never back-filled
    files = PV.render(rr.records, rr.anchors, series_id=lane.series_id, ledger_url="memory://issues/1")
    assert files == PV.render(rr.records, rr.anchors, series_id=lane.series_id, ledger_url="memory://issues/1")
    assert set(files) == {"picks/README.md", "picks/picks.csv", "picks/sessions.csv", "picks/ledger.jsonl",
                          "picks/anchors.json"}
    assert PV.DISCLAIMER in files["picks/README.md"] and "## Track record: picks" in files["picks/README.md"]
    header = files["picks/picks.csv"].splitlines()[0].split(",")
    assert header == PV.PLAN_COLUMNS and not RAW_BAR_KEYS & set(header)
    assert len(files["picks/ledger.jsonl"].splitlines()) == len(rr.records)


def _sec(routes: dict[str, bytes | int]):
    class FakeTime:
        t = 0.0

        def monotonic(self):
            return self.t

        def sleep(self, s):
            self.t += max(s, 1e-6)

    def handler(request: httpx.Request) -> httpx.Response:
        body = routes.get(str(request.url), routes.get("*"))
        return httpx.Response(body) if isinstance(body, int) or body is None else httpx.Response(200, content=body)

    return SH.sec_http(UA, transport=httpx.MockTransport(handler), bucket=TokenBucket(10.0, time_source=FakeTime()))


def test_the_sec_view_is_as_of_the_decision_instant_and_waits_on_a_mass_failure(monkeypatch):
    doc = (FIX / "edgar_submissions_CIK0009990001.json").read_bytes()
    monkeypatch.setattr(U, "u1_stocks", lambda: ("MMM", "AOS", "ABT"))
    day = date(2026, 10, 26)
    status, meta = PL.SecEarnings(_sec({"*": doc}), CAL).view(day, datetime(2026, 10, 27, 1, 0, tzinfo=UTC))
    assert meta["symbols"] == 3 and meta["failed"] == 0 and meta["as_of"] == "2026-10-26T20:15:00Z"
    assert status("MMM", day.isoformat(), 20) in ("WITHIN_HORIZON", "UNKNOWN", "NONE_WITHIN_HORIZON_CONFIRMED")
    with pytest.raises(SourceUnavailable) as exc:
        PL.SecEarnings(_sec({"*": 404}), CAL).view(day, datetime(2026, 10, 27, 1, 0, tzinfo=UTC))
    assert exc.value.status == "SOURCE_PENDING"


def test_the_sec_view_keeps_the_estimate_known_at_the_decision_instant():
    """A release filed after the instant must not erase what was known before it: the earlier estimate still vetoes,
    as in the worker's calendar refreshed the night before (the fixture's last release: 2026-10-14 16:05 ET)."""
    from apex_worker.runtime import earnings_job as EJ

    http = _sec({"*": (FIX / "edgar_submissions_CIK0009990001.json").read_bytes()})
    instant = datetime(2026, 10, 14, 20, 0, tzinfo=UTC)  # 16:00 ET: five minutes before that filing
    later = instant + timedelta(hours=6)
    data = EJ.refresh_earnings(["MMM"], {"MMM": "0000066740"}, http, CAL, later, as_of=instant)
    row = data["symbols"]["MMM"]
    assert row["reported"][-1]["day"] == "2026-07-29" and row["estimate"]["day"] == "2026-10-28"
    assert EJ.EarningsBook(CAL, data, clock=lambda: instant).status("MMM", "2026-10-15", 20) == "WITHIN_HORIZON"
    # filtering only the finished calendar would drop the new estimate AND the old one: the veto would be lost
    full = EJ.refresh_earnings(["MMM"], {"MMM": "0000066740"}, http, CAL, later)
    assert EJ.EarningsBook(CAL, full, clock=lambda: instant).status("MMM", "2026-10-15", 20) != "WITHIN_HORIZON"


def test_the_real_lane_never_decides_without_the_sec_contact():
    assert PL.SecEarnings.from_env(CAL, {}) is None
    assert PL.SecEarnings.from_env(CAL, {"APEX_SEC_USER_AGENT": "APEX Research research@example.com"}) is not None
    with pytest.raises(SourceUnavailable) as exc:
        PL.PicksLane().decide(None, PL.DECISIONS_FROM, None, CAL)
    assert exc.value.status == "SOURCE_INVALID" and "APEX_SEC_USER_AGENT" in exc.value.reason


def test_the_policy_defaults_are_the_workers():
    assert LP.POLICY == PolicyConfig() == EngineConfig().policy and LP.GateConfig() == LP.GATES
    assert LP.EXEC.cost_bps_per_side == LP.LEVELS.cost_bps_per_side == 10.0
    assert (LP.EXEC.entry_valid_sessions, LP.EXEC.horizon_sessions) == (2, 20) and LP.LIFETIME_SESSIONS == 22
