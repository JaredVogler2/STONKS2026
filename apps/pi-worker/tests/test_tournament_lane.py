"""Exploratory lane v3x-epa-tournament-v1: the prospective evidence tournament (docs/evidence/TOURNAMENT_LANE.md)."""

from __future__ import annotations

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


def test_the_policy_is_registered_exploratory_research_only_and_separate():
    assert T.SERIES_ID == "v3x-epa-tournament-v1" and X.EXPLORATORY_POLICIES == (T.POLICY,)
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
