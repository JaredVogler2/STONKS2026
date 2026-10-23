"""Exploratory lane v3x-epa-tournament-v1: the prospective evidence tournament (docs/evidence/TOURNAMENT_LANE.md)."""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from apex_worker.discovery.v3 import tournament_books as TB
from apex_worker.evidence.tournament import CLAIM_RULE
from apex_worker.research.forward import exploratory as X
from apex_worker.research.forward import tournament_lane as T
from apex_worker.research.forward.records import canonical, clean, publication_problems, verify_chain
from apex_worker.research.forward.store import IssueLedgerStore, MemoryGitHubAPI

# Cohort 0's series as registered (PR #30, corrected before genesis by PR #31). Cohort 1 must leave it byte-identical.
COHORT_0_CONFIG_SHA256 = "888f3ade24cda6bd951624c87c04b1bd2da00d5135975c3249b3974ce336de32"
COHORT_0_POLICY_SHA256 = "dde6a10bbc3d46bd204cecd3a90e0466f326223fdcd8b26d9c763c9b42196762"


def test_the_policy_is_registered_exploratory_research_only_and_separate():
    assert T.SERIES_ID == "v3x-epa-tournament-v1" and (T.POLICY, T.COHORT_1.policy) == X.EXPLORATORY_POLICIES
    assert T.config_sha256(T.TournamentLane().config(T.PINNED_FINGERPRINT)) == COHORT_0_CONFIG_SHA256
    assert T.POLICY.config_sha256() == COHORT_0_POLICY_SHA256
    cfg = T.series_config(T.PINNED_FINGERPRINT)
    assert cfg["lane"] == "EXPLORATORY" and cfg["evidence_tier"] == "PROSPECTIVE_EXPLORATORY"
    assert cfg["confirmatory"] is False and cfg["orders"] == "none" and cfg["group_actionable"] is False
    assert cfg["decisions_from"] == T.DECISIONS_FROM.isoformat() and cfg["golden_fingerprint"] == T.PINNED_FINGERPRINT
    assert cfg["policy"]["parameters"]["tournament"]["claim_rule"] == CLAIM_RULE  # declared in the genesis record
    assert sum(c.prior_mass for c in T.CANDIDATES) == pytest.approx(0.45)  # 0.55 reserved for later admissions
    assert {c.book for c in T.CANDIDATES} | {c.twin for c in T.CANDIDATES if c.twin} <= set(TB.BOOKS)
    X.assert_separate([T.SERIES_ID])


def test_micro_weights_are_exact_integers():
    m = TB.to_micro({"A": 1, "B": 1, "C": 1})
    assert sum(m.values()) == TB.MICRO and sorted(m.values()) == [333333, 333333, 333334]
    assert TB.to_micro({}) == {TB.CASH: TB.MICRO}
    assert TB._equal(["A", "B"], 4) == {"A": 250000, "B": 250000, TB.CASH: 500000}  # G2's rule: the rest in cash


def test_the_buffer_keeps_held_names_and_fills_best_first():
    syms = list("ABCDEFGH")
    score = np.arange(8, 0, -1, dtype=float)
    mask = np.ones(8, bool)
    assert TB._buffered_pick(score, mask, syms, [], 3, 5) == ["A", "B", "C"]
    assert TB._buffered_pick(score, mask, syms, ["E", "H"], 3, 5) == ["A", "B", "E"]  # E ranks 4 < 5 and stays
    mask[0] = False
    assert TB._buffered_pick(score, mask, syms, [], 3, 5) == ["B", "C", "D"]


def test_tilts_are_long_only_bounded_and_neutral_without_a_feature():
    syms = [f"S{i}" for i in range(10)]
    score = np.arange(10, dtype=float)
    score[3] = np.nan
    mask = np.ones(10, bool)
    mask[9] = False
    w = TB._tilt(score, mask, syms, 0.5)
    assert sum(w.values()) == TB.MICRO and "S9" not in w and min(w.values()) > 0
    assert max(w.values()) / min(w.values()) == pytest.approx(3.0, rel=1e-3)  # (1 + 0.5) / (1 - 0.5)
    assert w["S8"] > w["S3"] > w["S0"]


def test_golden_decisions_follow_the_schedule_and_match_the_pinned_fingerprint():
    d = T.golden_decisions()
    for phase, books in enumerate(d):
        for b, spec in TB.BOOKS.items():
            assert (books[b] is not None) == (phase % spec["every"] == 0)
            if books[b] is not None:
                assert sum(books[b]["micro"].values()) == TB.MICRO and min(books[b]["micro"].values()) > 0
    assert len(d[0]["g2"]["micro"]) == 10 and len(d[0]["g2-twin"]["sub_books"]) == 20
    assert hashlib.sha256(canonical(clean(d))).hexdigest() == T.PINNED_FINGERPRINT


def test_a_late_first_decision_invests_every_book_then_follows_the_schedule():
    from apex_worker.research.realeq.fixture_v2 import factor_frames_v2

    pre = TB.light_panel(factor_frames_v2(per_sector=2, seed=4, start=pd.Timestamp("2023-01-03").date(),
                                          end=pd.Timestamp("2024-03-28").date()))
    t = len(pre.sessions) - 1
    first = TB.decide(pre, t, t, 7, dict.fromkeys(TB.BOOKS))  # phase 7: no book's scheduled rebalance
    assert all(first[b] is not None for b in TB.BOOKS)
    nxt = TB.decide(pre, t, t, 8, first)
    assert all(nxt[b] is None for b in TB.BOOKS)
    assert TB.decide(pre, t, t, 10, first)["rev-tilt"] is not None  # every 5: phase 10 rebalances


def _closes(days, **cols):
    return pd.DataFrame(cols, index=pd.DatetimeIndex([pd.Timestamp(x) for x in days]))


def _decision(day, **books):
    return {"session": day, "body": {"books": dict.fromkeys(TB.BOOKS) | books}}


def test_replay_trades_at_the_next_close_charges_costs_and_holds_after_a_missed_session():
    days = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29"]
    closes = _closes(days, SPY=[100.0, 101.0, 102.01, 100.0], AAA=[50.0, 50.0, 55.0, 55.0])
    out = T.replay([_decision(days[0], ew={"micro": {"AAA": TB.MICRO}}), _decision(days[1])], closes)
    assert list(out) == days[1:]
    assert out[days[1]]["books"]["ew"] == pytest.approx(1.01 * (1 - 0.0007) - 1)  # SPY day, then sell 5 + buy 2 bps
    assert out[days[1]]["books"]["g2"] == pytest.approx(0.01)  # no target yet: still SPY
    assert out[days[2]]["books"]["ew"] == pytest.approx(0.10) and out[days[2]]["spy"] == pytest.approx(0.01)
    assert out[days[3]]["books"]["ew"] == pytest.approx(0.0)  # day 2's decision is missing: nothing trades


def test_replay_quarantined_and_missing_bars_earn_nothing():
    days = ["2026-10-26", "2026-10-27", "2026-10-28"]
    closes = _closes(days, SPY=[100.0, 100.0, 100.0], AAA=[50.0, 50.0, 100.0], BBB=[10.0, 10.0, np.nan])
    out = T.replay([_decision(days[0], ew={"micro": {"AAA": 500000, "BBB": 500000}})], closes,
                   {"AAA": [pd.Timestamp(days[2])]})
    assert out[days[2]]["books"]["ew"] == pytest.approx(0.0)


def test_the_tournament_state_is_a_pure_function_of_the_posted_returns():
    rng = np.random.default_rng(3)
    sessions = {}
    for i in range(60):
        spy = float(rng.normal(0.0004, 0.01))
        sessions[(pd.Timestamp("2026-11-02") + pd.Timedelta(days=i)).date().isoformat()] = {
            "spy": spy, "books": {b: spy + float(rng.normal(0, 0.003)) for b in TB.BOOKS}}
    a = T.tournament_state(sessions)
    assert a == T.tournament_state(dict(reversed(list(sessions.items()))))
    assert a["sessions"] == 60 and a["orders"] == "none" and set(a["candidates"]) == {c.candidate_id for c in T.CANDIDATES}
    assert sum(c["allocation"] for c in a["candidates"].values()) + a["allocation_spy"] == pytest.approx(1.0)
    assert a["candidates"]["ew"]["vs_twin"] is None and a["candidates"]["g2"]["vs_twin_bounded_loss"] is not None
    # each candidate's mass is split across its claim tests: every cohort-0 claim needs evidence 800, and the whole
    # family (every test of every candidate) spends alpha * 0.45 of the error budget
    assert a["claim_rule"] == CLAIM_RULE
    assert {k: len(c["claim_tests"]) for k, c in a["candidates"].items()} == {"g2": 4, "mom-tilt": 4, "rev-tilt": 4,
                                                                              "hi52-tilt": 4, "ew": 2}
    assert all(c["threshold"] == pytest.approx(800.0) for c in a["candidates"].values())
    assert sum(len(c["claim_tests"]) / c["threshold"] for c in a["candidates"].values()) == pytest.approx(0.05 * 0.45)
    json.dumps(a)


def test_the_recorder_records_decisions_and_outcomes_that_replay_exactly():
    from apex_worker.market.calendar import default_calendar
    from apex_worker.research.forward import cli as C

    cal = default_calendar()
    start = T.DECISIONS_FROM
    days = cal.sessions_between(start, cal.add_sessions(start, 4))
    frames = C._fixture_frames(cal.add_sessions(days[-1], 3))
    clock = C.SimClock(C.at_et(days[0], 18, 12))
    api = MemoryGitHubAPI(clock=clock)
    lane = T.TournamentLane.fixture(start)
    fp = functools.cache(T.golden_fingerprint)
    for d in days:
        clock.now = C.at_et(d, 18, 12)
        rep = T.TournamentRecorder(lane, IssueLedgerStore(api, lane.series_id), C._fixture_source(frames, cal, clock),
                                   cal, clock=clock, producer={"test": True}, fingerprint_fn=fp).run()
        assert rep.exit_code == 0, rep.messages
    rr = IssueLedgerStore(api, lane.series_id).read()
    types = [r["record_type"] for r in rr.records]
    assert types[0] == "SERIES_GENESIS" and types.count("DECISION") == 5 and types.count("OUTCOME") == 3
    assert not verify_chain(rr.records, series_id=lane.series_id, lane=lane.lane,
                            expected_sessions=[d.isoformat() for d in days])
    assert rr.records[0]["body"]["config_sha256"] == T.config_sha256(lane.config(T.PINNED_FINGERPRINT))
    outs = [r for r in rr.records if r["record_type"] == "OUTCOME"]
    posted = {s: v for r in outs for s, v in r["body"]["sessions"].items()}
    assert outs[-1]["body"]["tournament"] == T.tournament_state(posted)
    assert all(not publication_problems(r) for r in rr.records)
    assert all(r["body"]["evidence_tier"] == "PROSPECTIVE_EXPLORATORY" for r in rr.records if r["record_type"] != "SERIES_GENESIS")
    assert "Tournament after" in T.books_summary_md(rr.records)


def test_a_dry_run_posts_nothing(tmp_path):
    from apex_worker.cli import main

    rc = main(["research", "forward-shadow", "run", "--lane", "tournament", "--store", "dry", "--source", "fixture",
               "--now", "2026-10-26T22:30:00Z", "--summary-md", str(tmp_path / "s.md"),
               "--store-file", str(tmp_path / "ledger.json"), "--outbox", str(tmp_path / "outbox.json")])
    assert rc == 0
    assert not (tmp_path / "ledger.json").exists() and not (tmp_path / "outbox.json").exists()
    md = (tmp_path / "s.md").read_text()
    assert "DRY RUN: nothing was posted" in md
    assert "| Reading | anytime-valid evidence" in md and "first powered look" not in md  # not the frozen lane's look


# ---- cohort 1: v3x-epa-tournament-c1-v1 (docs/evidence/TOURNAMENT_COHORT_1.md) -------------------------------------------
def test_cohort_1_is_its_own_series_with_twin_only_claims_inside_one_mass_budget():
    from apex_worker.discovery.v3 import tournament_books_c1 as TB1
    from apex_worker.evidence.tournament import SELECTION, SELECTION_BOUNDED

    c1 = T.COHORT_1
    assert c1.series_id == "v3x-epa-tournament-c1-v1" and c1.series_id != T.SERIES_ID
    assert list(c1.books) == ["ew", "mom-full", "rev-full", "hi52-full", "composite-full", "lowvol-full"]
    assert [c.candidate_id for c in c1.candidates] == list(c1.books)[1:]
    assert all(c.twin == "ew" and c.prior_mass == 0.04 and c.loss_bound == 0.075 for c in c1.candidates)
    assert {c.candidate_id: c.twin_loss_bound for c in c1.candidates} == {
        "mom-full": 0.02, "rev-full": 0.015, "hi52-full": 0.02, "composite-full": 0.015, "lowvol-full": 0.0075}
    assert all(c.claims == (SELECTION, SELECTION_BOUNDED) for c in c1.candidates)
    assert T.assert_mass_budget() == pytest.approx(0.65)  # 0.45 + 0.20 of the budget of 1; 0.35 stays in reserve
    cfg = T.TournamentLane.for_cohort(c1).config(T.PINNED_FINGERPRINT_C1)
    assert cfg["series_id"] == c1.series_id and cfg["lane"] == "EXPLORATORY" and cfg["confirmatory"] is False
    assert cfg["cohort"]["earlier_cohorts"] == [{"series_id": T.SERIES_ID, "prior_mass": pytest.approx(0.45)}]
    assert cfg["cohort"]["prior_mass"] == pytest.approx(0.2) and cfg["cohort"]["mass_used_after"] == pytest.approx(0.65)
    assert cfg["policy"]["parameters"]["tournament"]["claim_rule"] == CLAIM_RULE
    assert cfg["cohort"]["designs_measured_by"]["plan_sha256"] == TB1.NEUTRALIZATION["measured_by"]["plan_sha256"]
    with pytest.raises(ValueError):
        T.assert_mass_budget((T.COHORT_0, T.COHORT_1, T.COHORT_1))  # a reused series id
    big = dataclasses.replace(T.COHORT_1, policy=dataclasses.replace(T.COHORT_1.policy, policy_id="other"),
                              candidates=tuple(dataclasses.replace(c, prior_mass=0.2) for c in T.COHORT_1.candidates))
    with pytest.raises(ValueError):
        T.assert_mass_budget((T.COHORT_0, big))  # 0.45 + 1.0 > 1


def test_cohort_1_books_are_the_measured_design_study_books():
    from apex_worker.discovery.v3 import design as D
    from apex_worker.discovery.v3 import tournament_books_c1 as TB1

    pre = _panel_c1()
    t = len(pre.sessions) - 1
    lane_books = TB1.decide(pre, t, t, 0, dict.fromkeys(TB1.BOOKS))
    study, _ = D.decide(D.inputs(pre), t, 0, D.DESIGN_STUDY_1, True)  # the registered study's own plan
    assert all(lane_books[b] is not None for b in TB1.BOOKS)
    for b in TB1.BOOKS:
        assert lane_books[b]["micro"] == study[b]
    nxt = TB1.decide(pre, t, t, 1, lane_books)
    assert all(v is None for v in nxt.values())  # phase 1: nothing scheduled
    assert {b for b, v in TB1.decide(pre, t, t, 5, lane_books).items() if v} == {"rev-full", "composite-full"}


def test_cohort_1_golden_fingerprint_is_pinned_and_cohort_0s_is_unchanged():
    assert T.golden_fingerprint(T.COHORT_1) == T.PINNED_FINGERPRINT_C1
    assert T.golden_fingerprint() == T.PINNED_FINGERPRINT


def test_cohort_1_state_claims_only_against_the_twin():
    from apex_worker.evidence.tournament import EDGE, SELECTION, SELECTION_BOUNDED

    rng = np.random.default_rng(5)
    sessions = {}
    for i in range(250):
        spy = float(rng.normal(0.0004, 0.01))
        ew = spy + float(rng.normal(0.004, 0.002))  # equal weight beats SPY hugely: an edge, but not the mechanism
        books = {"ew": ew} | {b: ew + float(rng.normal(0, 0.0005)) for b in T.COHORT_1.books if b != "ew"}
        sessions[(pd.Timestamp("2026-11-02") + pd.Timedelta(days=i)).date().isoformat()] = {"spy": spy, "books": books}
    a = T.tournament_state(sessions, T.COHORT_1)
    assert set(a["candidates"]) == {c.candidate_id for c in T.COHORT_1.candidates}
    for c in a["candidates"].values():
        assert c["claim_tests"] == [SELECTION, SELECTION_BOUNDED] and c["threshold"] == pytest.approx(1000.0)
        assert c["vs_spy_bounded_loss"]["e_max"] > 1000 and EDGE not in c["claims"]
        assert not c["claims"]  # far past 1,000 against SPY, but no claim: those tests only drive the allocation
    assert a["allocation_spy"] < 0.5  # the evidence against SPY moved the paper book
    assert T.tournament_state(dict(reversed(list(sessions.items()))), T.COHORT_1) == a


def test_cohort_1_recorder_records_and_replays_exactly():
    from apex_worker.market.calendar import default_calendar
    from apex_worker.research.forward import cli as C

    cal = default_calendar()
    start = T.COHORT_1.decisions_from
    days = cal.sessions_between(start, cal.add_sessions(start, 4))
    frames = C._fixture_frames(cal.add_sessions(days[-1], 3))
    clock = C.SimClock(C.at_et(days[0], 18, 12))
    api = MemoryGitHubAPI(clock=clock)
    lane = T.TournamentLane.fixture(start, T.COHORT_1)
    fp = functools.cache(functools.partial(T.golden_fingerprint, T.COHORT_1))
    for d in days:
        clock.now = C.at_et(d, 18, 12)
        rep = T.TournamentRecorder(lane, IssueLedgerStore(api, lane.series_id), C._fixture_source(frames, cal, clock),
                                   cal, clock=clock, producer={"test": True}, fingerprint_fn=fp).run()
        assert rep.exit_code == 0, rep.messages
    rr = IssueLedgerStore(api, lane.series_id).read()
    types = [r["record_type"] for r in rr.records]
    assert types[0] == "SERIES_GENESIS" and types.count("DECISION") == 5 and types.count("OUTCOME") == 3
    assert not verify_chain(rr.records, series_id=lane.series_id, lane=lane.lane,
                            expected_sessions=[d.isoformat() for d in days])
    assert rr.records[0]["body"]["config_sha256"] == T.config_sha256(lane.config(T.PINNED_FINGERPRINT_C1))
    first = next(r for r in rr.records if r["record_type"] == "DECISION")["body"]["books"]
    assert set(first) == set(T.COHORT_1.books) and all(v is not None for v in first.values())
    outs = [r for r in rr.records if r["record_type"] == "OUTCOME"]
    posted = {s: v for r in outs for s, v in r["body"]["sessions"].items()}
    assert outs[-1]["body"]["tournament"] == T.tournament_state(posted, T.COHORT_1)
    assert set(next(iter(posted.values()))["books"]) == set(T.COHORT_1.books)
    assert all(not publication_problems(r) for r in rr.records)


def test_one_pass_records_every_cohort_from_one_fetch(tmp_path):
    from apex_worker.cli import main
    from apex_worker.research.forward import source as S

    calls = []
    real = S.MarketSource.decision_data

    def counting(self, day, now):
        calls.append(day)
        return real(self, day, now)

    S.MarketSource.decision_data = counting
    try:
        rc = main(["research", "forward-shadow", "run-tournament", "--store", "dry", "--source", "fixture",
                   "--now", "2026-10-26T22:30:00Z", "--out-dir", str(tmp_path)])
    finally:
        S.MarketSource.decision_data = real
    assert rc == 0 and len(calls) == 1  # cohort 1 reused cohort 0's fetch
    md = (tmp_path / "summary.md").read_text()
    assert md.count("DRY RUN: nothing was posted") == 2
    assert "`fixture-demo-epa-tournament`" in md and "`fixture-demo-epa-tournament-c1`" in md
    reports = [json.loads((tmp_path / f).read_text()) for f in ("report.json", "report-c1.json")]
    assert [r["series_id"] for r in reports] == ["fixture-demo-epa-tournament", "fixture-demo-epa-tournament-c1"]
    assert all([a["record_type"] for a in r["actions"]] == ["SERIES_GENESIS", "DECISION"] for r in reports)
    assert not (tmp_path / "outbox.json").exists() and not (tmp_path / "outbox-c1.json").exists()  # dry: no outbox


def test_shared_source_memoises_successes_only():
    class Flaky:
        requests_used = 7

        def __init__(self):
            self.n = 0

        def decision_data(self, day, now):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("pending")
            return ("bars", day)

        def outcome_data(self, start, end, now, symbols=None):
            self.n += 1
            return ("outcome", start, end, symbols)

    inner = Flaky()
    s = T.SharedSource(inner)
    with pytest.raises(RuntimeError):
        s.decision_data("d", None)
    assert s.decision_data("d", None) == s.decision_data("d", None) == ("bars", "d") and inner.n == 2
    assert s.outcome_data("a", "b", None) is s.outcome_data("a", "b", None) and inner.n == 3
    assert s.outcome_data("a", "b", None, ("SPY",)) != s.outcome_data("a", "b", None) and inner.n == 4
    assert s.requests_used == 7  # delegated


@functools.cache
def _panel_c1():
    from apex_worker.research.realeq.fixture_v2 import factor_frames_v2

    return TB.light_panel(factor_frames_v2(per_sector=4, seed=9, start=pd.Timestamp("2022-06-01").date(),
                                           end=pd.Timestamp("2024-03-28").date()))


def test_one_cohorts_crash_does_not_stop_the_others(tmp_path, monkeypatch):
    from apex_worker.cli import main
    from apex_worker.discovery.v3 import tournament_books_c1 as TB1

    def broken(*_a, **_k):
        raise ArithmeticError("simulated defect")

    monkeypatch.setattr(TB1.D, "decide", broken)  # cohort 1's decisions (and fingerprint) now raise
    rc = main(["research", "forward-shadow", "run-tournament", "--store", "dry", "--source", "fixture",
               "--now", "2026-10-26T22:30:00Z", "--out-dir", str(tmp_path)])
    md = (tmp_path / "summary.md").read_text()
    assert rc == 1 and "RUN ERROR** (ArithmeticError)" in md  # the pass fails visibly
    assert json.loads((tmp_path / "report.json").read_text())["exit_code"] == 0  # cohort 0 recorded normally
