"""Forward shadow (Discovery V3 PR-A): records, store, windows, recorder and lanes.

Fast tests: the recorder runs with a stub lane/source so ledger mechanics are tested without feature computation; the
frozen decision logic itself is tested in test_forward_shadow_parity.py. Test ids follow
amendments/discovery-v3-2026-10-22/10_EXECUTABLE_ACCEPTANCE_GATES.md (FWD01-FWD18).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time

import numpy as np
import pandas as pd
import pytest

from apex_worker.market.calendar import default_calendar
from apex_worker.research.forward import exploratory as X
from apex_worker.research.forward import frozen as F
from apex_worker.research.forward import windows as W
from apex_worker.research.forward.health import health
from apex_worker.research.forward.recorder import FrozenLane, Recorder
from apex_worker.research.forward.records import (
    DECISION,
    GENESIS,
    MISSED,
    OUTCOME,
    Ledger,
    canonical,
    digest_of,
    make_record,
    publication_problems,
    verify_chain,
)
from apex_worker.research.forward.source import DecisionData, MarketSource, OutcomeBars, SourceUnavailable
from apex_worker.research.forward.store import (
    IssueLedgerStore,
    MemoryGitHubAPI,
    StoreError,
    comment_bodies,
    decode_payload,
    encode_parts,
)
from apex_worker.research.realeq.data import STATUS_BLOCKED, RunStop
from apex_worker.timeutil import ET

CAL = default_calendar()
D0 = F.DECISIONS_FROM  # 2026-10-23, a Friday


def et(day: date, hh: int, mm: int) -> datetime:
    return datetime.combine(day, time(hh, mm), ET).astimezone(UTC)


@dataclass
class Clock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now


class StubLane(FrozenLane):
    """The real lane's identity/config with a cheap decision (no features): for ledger mechanics only."""

    tick: object = None

    def decide(self, dd, day, ledger, cal):
        if self.tick is not None:
            self.tick()
        arms = {f"{sid}:{arm}": {"arm": arm, "signals": [], "status": "NO_TRADE",
                                 "funnel": {"triggered": 0, "planned": 0}} for sid, arm, _ in F.ARMS}
        arms[f"S5:{F.S5_ARM}"] = {"arm": F.S5_ARM, "rebalance_session": False, "weights": None, "status": "HOLD"}
        return {"decision_session": day.isoformat(), "session_index": W.session_index(cal, day), "arms": arms,
                "decision": "NO_TRADE", "source": dd.meta, "summary": "stub"}


@dataclass
class StubSource:
    pending: set = field(default_factory=set)
    fail: SourceUnavailable | None = None
    requests_used: int = 0
    calls: int = 0

    def decision_data(self, day, now):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        if day in self.pending:
            raise SourceUnavailable("SOURCE_PENDING", f"the {day} SPY daily bar is not published yet")
        return DecisionData(day, {}, {"data_class": "SYNTHETIC_FIXTURE_DEMO"})

    def outcome_data(self, start, end, now, symbols=None):
        idx = pd.DatetimeIndex([pd.Timestamp(d) for d in CAL.sessions_between(start, end)])
        flat = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1e7}, index=idx)
        return OutcomeBars(idx, {s: flat for s in MarketSource.s5_symbols()}, {}, {"data_class": "SYNTHETIC"})


def make(clock, api=None, source=None, lane=None, outbox=None, fp=F.PINNED_FINGERPRINT):
    api = api or MemoryGitHubAPI(clock=clock)
    lane = lane or StubLane()
    store = IssueLedgerStore(api, lane.series_id)
    rec = Recorder(lane, store, source or StubSource(), CAL, clock=clock, producer={"test": True}, outbox=outbox,
                   fingerprint_fn=lambda: fp)
    return rec, api


def records(api, series=F.SERIES_ID):
    return IssueLedgerStore(api, series).read().records


# ---- records and chain ------------------------------------------------------------------------------------------------
def _chain(n=4):
    led = Ledger("s", "FIXTURE_DEMO")
    led.commit(led.build(GENESIS, None, {"x": 1}, "2026-10-23T22:00:00Z", {}))
    for i, day in enumerate(["2026-10-23", "2026-10-26", "2026-10-27"][: n - 1]):
        led.commit(led.build(DECISION, day, {"i": i}, f"2026-10-2{3 + i}T22:00:00Z", {}))
    return led


def test_digest_is_canonical_and_chain_verifies():
    led = _chain()
    assert verify_chain(led.records) == []
    r = led.records[1]
    assert r["digest"] == digest_of(r) and r["previous_digest"] == led.records[0]["digest"]
    assert canonical({"b": 1, "a": [1.5, None]}) == b'{"a":[1.5,null],"b":1}'
    with pytest.raises(ValueError):
        canonical({"bad": float("nan")})  # the encoding never carries NaN/inf
    r0 = make_record(series_id="s", lane="FIXTURE_DEMO", seq=0, record_type=GENESIS, session=None,
                     previous_digest="0" * 64, observed_at="x", producer={}, body={"v": np.float64("nan"), "i": np.int64(3)})
    assert r0["body"] == {"v": None, "i": 3}  # non-finite numbers become null before hashing
    with pytest.raises(ValueError):
        make_record(series_id="s", lane="NOPE", seq=0, record_type=GENESIS, session=None, previous_digest="0" * 64,
                    observed_at="x", producer={}, body={})


def test_fwd06_edit_insert_delete_reorder_all_fail_verification():
    base = _chain().records
    edited = [dict(r) for r in base]
    edited[2] = dict(edited[2], body={"i": 99})
    assert any("digest does not match" in p for p in verify_chain(edited))
    deleted = base[:2] + base[3:]
    assert any("seq" in p or "previous_digest" in p for p in verify_chain(deleted))
    reordered = [base[0], base[2], base[1], base[3]]
    assert verify_chain(reordered)
    forged = dict(base[2])
    forged["body"] = {"i": 7}
    forged["digest"] = digest_of(forged)  # re-hashed forgery: the successor no longer links to it
    assert any("previous_digest" in p for p in verify_chain([base[0], base[1], forged, base[3]]))


def test_terminal_uniqueness_order_and_coverage():
    led = _chain(3)
    with pytest.raises(ValueError):
        led.build(MISSED, "2026-10-23", {}, "2026-10-26T22:00:00Z", {})
    gap = verify_chain(led.records, expected_sessions=["2026-10-23", "2026-10-26"])
    assert gap == []
    missing = verify_chain(led.records[:2], expected_sessions=["2026-10-23", "2026-10-26"])
    assert missing == []  # sessions after the last terminal record are not yet due
    led2 = _chain(4)
    skipped = dict(led2.records[3], seq=2, previous_digest=led2.records[1]["digest"])  # 10-26 dropped, relinked
    assert any("without a DECISION" in p for p in verify_chain(
        [led2.records[0], led2.records[1], skipped], expected_sessions=["2026-10-23", "2026-10-26", "2026-10-27"]))


def test_outcome_must_name_its_decision():
    led = _chain(2)
    d = led.records[1]
    bad = led.build(OUTCOME, d["session"], {"decision": {"record_id": d["record_id"], "digest": "f" * 64}},
                    "2026-11-30T22:00:00Z", {})
    assert any("does not name its DECISION" in p for p in verify_chain([*led.records, bad]))


def test_fwd16_publication_guard_blocks_raw_bars_and_secrets():
    rec = _chain(2).records[1]
    assert publication_problems(rec, env={}) == []
    raw = dict(rec, body={"bar": {"open": 1.0, "close": 2.0}})
    assert any("raw bar field" in p for p in publication_problems(raw, env={}))
    leak = dict(rec, body={"note": "key PKABCDEFGHIJKLMNOPQR"})
    assert publication_problems(leak, env={})
    env_leak = dict(rec, body={"note": "x s3cr3t-value-123 y"})
    assert publication_problems(env_leak, env={"ALPACA_SECRET_KEY": "s3cr3t-value-123"})
    assert publication_problems(dict(rec, body={"by_symbol": {"LOW": "PASS"}}), env={}) == []  # Lowe's is a ticker
    api = MemoryGitHubAPI()
    with pytest.raises(StoreError) as exc:
        IssueLedgerStore(api, "s").append(dict(raw, series_id="s"))
    assert exc.value.kind == "INTEGRITY" and api.comments == []


# ---- store ------------------------------------------------------------------------------------------------------------
def test_store_round_trip_multipart_and_server_timestamps():
    clock = Clock(et(D0, 18, 30))
    api = MemoryGitHubAPI(clock=clock)
    store = IssueLedgerStore(api, "s")
    led = _chain()
    noise = np.random.default_rng(0).integers(0, 2**63, 9000).astype(str).tolist()  # incompressible
    big = led.build(DECISION, "2026-10-28", {"blob": noise}, "2026-10-28T22:00:00Z", {})
    assert len(encode_parts(big)) > 1
    for r in [*led.records, big]:
        store.append(r)
    rr = IssueLedgerStore(api, "s").read()
    assert rr.problems == [] and [r["digest"] for r in rr.records] == [r["digest"] for r in [*led.records, big]]
    assert decode_payload(encode_parts(led.records[1])[0]) == led.records[1]
    assert all(a.created_at == "2026-10-23T22:30:00Z" for a in rr.anchors.values())
    assert len(rr.volumes) == 1 and rr.volumes[0]["comments"] == len(api.comments)


def test_store_rollover_reads_across_volumes():
    api = MemoryGitHubAPI()
    store = IssueLedgerStore(api, "s", rollover=2)
    for r in _chain().records:
        store.append(r)
    rr = IssueLedgerStore(api, "s").read()
    assert [v["vol"] for v in rr.volumes] == [1, 2] and len(rr.records) == 4 and rr.problems == []


def test_store_rejects_foreign_or_conflicting_record_comments():
    api = MemoryGitHubAPI()
    store = IssueLedgerStore(api, "s")
    recs = _chain().records
    for r in recs[:3]:
        store.append(r)
    issue = api.issues[0]["number"]
    api.create_comment(issue, comment_bodies(recs[3])[0], login="someone")
    assert any("not the recorder" in p for p in IssueLedgerStore(api, "s").read().problems)
    api.comments.pop()
    api.create_comment(issue, "ordinary discussion is ignored")
    assert IssueLedgerStore(api, "s").read().problems == []
    other = dict(recs[2], body={"i": 5})
    other["digest"] = digest_of(other)
    api.create_comment(issue, comment_bodies(other)[0])
    assert any("two different records" in p for p in IssueLedgerStore(api, "s").read().problems)


def test_fwd06_edited_comment_is_detected():
    api = MemoryGitHubAPI()
    store = IssueLedgerStore(api, "s")
    recs = _chain().records
    for r in recs:
        store.append(r)
    c = api.comments[2]
    forged = dict(recs[2], body={"i": 42})
    api.edit_comment(c["id"], comment_bodies(forged)[0].replace(forged["digest"], recs[2]["digest"]))
    assert any("digest" in p for p in IssueLedgerStore(api, "s").read().problems)


def test_store_permission_error_is_typed():
    api = MemoryGitHubAPI(deny=True)
    with pytest.raises(StoreError) as exc:
        IssueLedgerStore(api, "s").read()
    assert exc.value.kind == "PERMISSION"


# ---- windows ----------------------------------------------------------------------------------------------------------
def test_fwd04_windows_follow_dst_and_half_days():
    edt = W.window(CAL, date(2026, 10, 30))
    est = W.window(CAL, date(2026, 11, 2))
    assert edt.close_utc.hour == 20 and est.close_utc.hour == 21  # 16:00 ET either side of the 1 Nov change
    assert edt.deadline_utc == CAL.session(date(2026, 11, 2)).open_utc  # Friday -> Monday open
    half = W.window(CAL, date(2026, 11, 27))
    assert half.early_close and half.close_utc == et(date(2026, 11, 27), 13, 0)
    assert half.state(et(date(2026, 11, 27), 14, 0)) == "NOT_YET" and half.state(et(date(2026, 11, 27), 14, 45)) == "OPEN"
    assert W.session_index(CAL, date(2026, 10, 20)) == 2714  # the official run's last session (2,715 sessions)
    assert W.session_index(CAL, F.WINDOW_ANCHOR) == F.WINDOW_ANCHOR_INDEX and F.WINDOW_ANCHOR_INDEX % 10 == 0


def test_fwd03_weekend_and_holiday_runs_create_no_session():
    assert [w.session for w in W.due_windows(CAL, date(2026, 11, 26), et(date(2026, 11, 26), 20, 0))] == []
    rec, api = make(Clock(et(date(2026, 10, 24), 18, 12)))  # Saturday after D0
    rec.lane.decisions_from = date(2026, 10, 26)
    rep = rec.run()
    assert [r["record_type"] for r in records(api)] == [GENESIS] and rep.exit_code == 0


def test_maturity_counts_trading_sessions_and_finality_lag():
    assert W.maturity_session(CAL, D0) == CAL.add_sessions(D0, 22)
    assert not W.is_mature(CAL, D0, CAL.add_sessions(D0, 21)) and W.is_mature(CAL, D0, CAL.add_sessions(D0, 22))
    assert W.outcome_data_end(CAL, et(date(2026, 11, 30), 18, 0)) == date(2026, 11, 25)


# ---- recorder -----------------------------------------------------------------------------------------------------
def test_fwd01_fwd12_decision_in_window_is_recorded_and_anchored_on_time():
    clock = Clock(et(D0, 18, 12))
    rec, api = make(clock)
    rep = rec.run()
    rs = records(api)
    assert [r["record_type"] for r in rs] == [GENESIS, DECISION] and rep.exit_code == 0
    d = rs[1]
    assert d["session"] == "2026-10-23" and d["body"]["decision"] == "NO_TRADE"  # FWD12: NO_TRADE still recorded
    assert d["observed_at"] == "2026-10-23T22:12:00Z" and d["lane"] == "FROZEN_CONFIRMATORY"
    h = rep.health
    assert h["decisions_on_time"] == 1 and h["status"] == "RUNNING"
    g = rs[0]["body"]["config"]
    assert g["selection_lock"]["sha256"] == F.LOCK_SHA256 and g["protocol"]["sha256"] == F.PINNED_PROTOCOL_SHA256
    assert g["arms"] == ["S1:E0X1", "S2:E0X1", "S3:C_X0", "S5:R10"] and g["group_actionable"] is False


def test_before_the_window_opens_nothing_is_decided():
    rec, api = make(Clock(et(D0, 17, 0)))  # 16:00 close + 90 min buffer not reached
    rec.run()
    assert [r["record_type"] for r in records(api)] == [GENESIS]


def test_fwd05_duplicate_dispatch_and_retry_are_idempotent():
    clock = Clock(et(D0, 18, 12))
    api = MemoryGitHubAPI(clock=clock)
    make(clock, api)[0].run()
    clock.now = et(D0, 21, 52)
    rep = make(clock, api)[0].run()
    assert rep.actions == [] and [r["record_type"] for r in records(api)] == [GENESIS, DECISION]


def test_fwd02_fwd15_late_run_records_missed_and_catches_up_without_backfill():
    clock = Clock(et(D0, 18, 12))
    api = MemoryGitHubAPI(clock=clock)
    make(clock, api)[0].run()
    clock.now = et(date(2026, 10, 29), 18, 12)  # 26, 27 and 28 had no run at all
    rep = make(clock, api)[0].run()
    rs = records(api)
    assert [(r["record_type"], r["session"]) for r in rs[2:]] == [
        (MISSED, "2026-10-26"), (MISSED, "2026-10-27"), (MISSED, "2026-10-28"), (DECISION, "2026-10-29")]
    assert all(r["body"]["reason"] == "NO_RUN_IN_WINDOW" for r in rs[2:5])
    assert rep.exit_code == 4 and rep.health["missed"] == 3  # the missed-run alarm fails the job
    assert verify_chain(rs, expected_sessions=[d.isoformat() for d in CAL.sessions_between(D0, date(2026, 10, 29))]) == []


def test_sessions_before_genesis_are_missed_without_alarm():
    clock = Clock(et(date(2026, 10, 27), 18, 12))
    rec, api = make(clock)
    rep = rec.run()
    rs = records(api)
    assert [r["body"].get("reason") for r in rs if r["record_type"] == MISSED] == ["BEFORE_SERIES_GENESIS"] * 2
    assert rs[-1]["record_type"] == DECISION and rep.exit_code == 0


def test_source_pending_is_recorded_once_and_a_later_run_can_still_decide():
    clock = Clock(et(D0, 18, 12))
    api = MemoryGitHubAPI(clock=clock)
    src = StubSource(pending={D0})
    r1 = make(clock, api, src)[0].run()
    clock.now = et(D0, 19, 0)
    make(clock, api, src)[0].run()
    assert r1.exit_code == 3 and [r["record_type"] for r in records(api)] == [GENESIS, "OBSERVATION_ATTEMPT_FAILED"]
    src.pending.clear()
    clock.now = et(D0, 21, 52)
    make(clock, api, src)[0].run()
    assert [r["record_type"] for r in records(api)][-1] == DECISION


def test_pending_until_deadline_becomes_missed_with_the_attempt_reason():
    clock = Clock(et(D0, 18, 12))
    api = MemoryGitHubAPI(clock=clock)
    src = StubSource(pending={D0})
    make(clock, api, src)[0].run()
    clock.now = et(date(2026, 10, 26), 18, 12)
    src.pending.clear()
    make(clock, api, src)[0].run()
    rs = records(api)
    assert [r["record_type"] for r in rs] == [GENESIS, "OBSERVATION_ATTEMPT_FAILED", MISSED, DECISION]
    assert rs[2]["body"]["reason"] == "ATTEMPT_FAILED:SOURCE_PENDING" and rs[2]["body"]["attempt_seq"] == 1
    assert rs[3]["session"] == "2026-10-26"


def test_fwd11_missing_credentials_fail_with_owner_setup_and_no_fallback():
    clock = Clock(et(D0, 18, 12))

    def factory():
        raise RunStop(STATUS_BLOCKED, "ALPACA_KEY_ID not set")

    src = MarketSource(factory, CAL)
    rec, api = make(clock, source=src, lane=FrozenLane())
    rep = rec.run()
    rs = records(api)
    assert rep.exit_code == 5 and rs[-1]["record_type"] == "OBSERVATION_ATTEMPT_FAILED"
    assert rs[-1]["body"]["status"] == "CREDENTIALS_MISSING" and any("ALPACA_API_KEY_ID" in s for s in rep.owner_setup)
    with pytest.raises(ValueError):
        MarketSource(factory, CAL, min_usable_stocks=10)  # a real source can never lower the protocol minimum


def test_fwd07_unconfirmed_post_goes_to_outbox_and_is_reposted_with_the_same_digest(tmp_path):
    clock = Clock(et(D0, 18, 12))
    api = MemoryGitHubAPI(clock=clock)
    box = tmp_path / "outbox.json"
    make(clock, api, outbox=box)[0].run()  # genesis + decision
    clock.now = et(date(2026, 10, 26), 18, 12)
    api.fail_creates = 1
    rep = make(clock, api, outbox=box)[0].run()
    pending = json.loads(box.read_text())
    assert rep.exit_code == 7 and "ANCHOR_PENDING" in rep.messages[-1] and rep.actions == []  # never claimed posted
    assert [r["session"] for r in pending] == ["2026-10-26"]
    clock.now = et(date(2026, 10, 27), 18, 12)
    rep2 = make(clock, api, outbox=box)[0].run()
    rs = records(api)
    assert rs[2]["digest"] == pending[0]["digest"] and rs[2]["observed_at"] == "2026-10-26T22:12:00Z"
    assert json.loads(box.read_text()) == [] and rep2.health["decisions_anchored_late"] == 1  # anchored after the open


def test_fwd05_lost_response_does_not_duplicate(tmp_path):
    clock = Clock(et(D0, 18, 12))
    api = MemoryGitHubAPI(clock=clock, lose_create_responses=0)
    box = tmp_path / "outbox.json"
    make(clock, api, outbox=box)[0].run()
    clock.now = et(date(2026, 10, 26), 18, 12)
    api.lose_create_responses = 1  # GitHub stored the comment but the response never arrived
    make(clock, api, outbox=box)[0].run()
    clock.now = et(date(2026, 10, 26), 21, 52)
    make(clock, api, outbox=box)[0].run()
    rs = records(api)
    assert [r["session"] for r in rs if r["record_type"] == DECISION] == ["2026-10-23", "2026-10-26"]
    assert verify_chain(rs) == []


def test_decision_computed_after_the_deadline_is_missed_not_decided():
    clock = Clock(et(date(2026, 10, 26), 9, 0))  # 30 minutes before the next open closes the 10-23 window
    lane = StubLane()

    def slow():
        clock.now = et(date(2026, 10, 26), 9, 45)

    lane.tick = slow
    rec, api = make(clock, lane=lane)
    rec.run()
    rs = records(api)
    assert rs[1]["record_type"] == MISSED and rs[1]["body"]["reason"] == "DECISION_COMPUTED_AFTER_DEADLINE"


def test_fwd06_tampered_ledger_stops_recording():
    clock = Clock(et(D0, 18, 12))
    api = MemoryGitHubAPI(clock=clock)
    make(clock, api)[0].run()
    clock.now = et(date(2026, 10, 26), 18, 12)
    make(clock, api)[0].run()
    api.delete_comment(api.comments[1]["id"])  # delete the 10-23 decision
    clock.now = et(date(2026, 10, 27), 18, 12)
    rep = make(clock, api)[0].run()
    assert rep.exit_code == 6 and "CHAIN_VIOLATION" in rep.messages[0] and rep.actions == []


def test_behaviour_fingerprint_or_config_change_fails_closed(monkeypatch):
    clock = Clock(et(D0, 18, 12))
    rec, api = make(clock, fp="f" * 64)
    rep = rec.run()
    assert rep.exit_code == 6 and "SERIES_BEHAVIOUR_CHANGED" in rep.messages[0] and api.comments == []
    rec2, api2 = make(clock)
    rec2.run()
    monkeypatch.setattr(F.U, "snapshot_sha256", lambda: "0" * 64)  # FWD14: a re-ranked/changed survivor snapshot
    clock.now = et(date(2026, 10, 26), 18, 12)
    rep2 = make(clock, api2)[0].run()
    assert rep2.exit_code == 6 and "SERIES_CONFIG_CHANGED" in rep2.messages[0]


def test_frozen_inputs_are_verified_against_the_committed_lock():
    got = F.check_frozen_inputs()
    assert got["lock_verified_against_committed_artifact"] and got["lock_sha256"] == F.LOCK_SHA256


def test_frozen_inputs_fail_closed_when_the_protocol_changes(monkeypatch):
    monkeypatch.setattr(F, "protocol_v2_sha256", lambda: "1" * 64)
    with pytest.raises(F.FrozenLaneInvalid):
        F.check_frozen_inputs()


def test_fwd08_outcome_waits_for_maturity_and_is_appended_once():
    clock = Clock(et(D0, 18, 12))
    api = MemoryGitHubAPI(clock=clock)
    make(clock, api)[0].run()
    early = CAL.add_sessions(D0, 22)  # data end lags 2 sessions behind: not yet mature at this close
    clock.now = et(early, 18, 12)
    make(clock, api)[0].run()
    assert not [r for r in records(api) if r["record_type"] == OUTCOME]
    clock.now = et(CAL.add_sessions(D0, 24), 18, 12)
    make(clock, api)[0].run()
    clock.now = et(CAL.add_sessions(D0, 24), 21, 52)
    make(clock, api)[0].run()
    outs = [r for r in records(api) if r["record_type"] == OUTCOME]
    assert [o["session"] for o in outs] == ["2026-10-23"]
    d = next(r for r in records(api) if r["record_type"] == DECISION and r["session"] == "2026-10-23")
    assert outs[0]["body"]["decision"] == {"record_id": d["record_id"], "digest": d["digest"], "session": d["session"]}


def test_fwd17_health_states():
    t = et(D0, 18, 12)
    assert health([], {}, CAL, t, D0)["status"] == "SETUP_REQUIRED"
    clock = Clock(t)
    api = MemoryGitHubAPI(clock=clock)
    make(clock, api)[0].run()
    rr = IssueLedgerStore(api, F.SERIES_ID).read()
    later = et(date(2026, 11, 3), 18, 0)
    assert health(rr.records, rr.anchors, CAL, later, D0)["status"] == "STALE"  # ran once, then stopped capturing
    assert health(rr.records, rr.anchors, CAL, et(D0, 22, 0), D0)["status"] == "RUNNING"


def test_fwd18_export_restore_keeps_hash_verification():
    clock = Clock(et(D0, 18, 12))
    api = MemoryGitHubAPI(clock=clock)
    make(clock, api)[0].run()
    exported = [json.loads(json.dumps(r)) for r in records(api)]
    restored = MemoryGitHubAPI(clock=clock)
    store = IssueLedgerStore(restored, F.SERIES_ID)
    for r in exported:
        store.append(r)
    back = IssueLedgerStore(restored, F.SERIES_ID).read()
    assert back.problems == [] and verify_chain(back.records) == [] and back.records == exported


def test_fwd10_exploratory_lane_is_separate_and_never_confirmatory():
    pol = X.ExploratoryPolicy("f2-dispersion-gate", 1, "H-F2-001", "HYPOTHESIS_UNREGISTERED", "dispersion gates capital",
                              "apex_worker.discovery.v3.policies.f2", "a" * 64)
    assert pol.series_id == "v3x-f2-dispersion-gate-v1" and pol.config()["confirmatory"] is False
    before = F.config_sha256(FrozenLane().config(F.PINNED_FINGERPRINT))
    changed = X.ExploratoryPolicy("f2-dispersion-gate", 2, "H-F2-001", "EXPLORATORY_REGISTERED", "v2 schema",
                                  "apex_worker.discovery.v3.policies.f2", "b" * 64)
    assert changed.series_id != pol.series_id and changed.config_sha256() != pol.config_sha256()
    assert F.config_sha256(FrozenLane().config(F.PINNED_FINGERPRINT)) == before  # frozen series untouched
    with pytest.raises(ValueError):
        X.ExploratoryPolicy("x", 1, "h", "EXPLORATORY_REGISTERED", "m", "apex_worker.research.forward.frozen", "c")
    with pytest.raises(ValueError):
        X.label_body({"confirmatory": True})
    assert X.label_body({})["evidence_tier"] == "PROSPECTIVE_EXPLORATORY"
    with pytest.raises(ValueError):
        X.assert_separate([F.SERIES_ID])
    assert [q.series_id for q in X.EXPLORATORY_POLICIES] == ["v3x-epa-tournament-v1"]  # docs/evidence/TOURNAMENT_LANE.md
    led = Ledger(pol.series_id, X.LANE)
    rec = led.build(GENESIS, None, X.label_body({"config": pol.config()}), "2026-10-23T22:00:00Z", {})
    with pytest.raises(StoreError):
        IssueLedgerStore(MemoryGitHubAPI(), F.SERIES_ID).append(rec)  # an exploratory record never enters the frozen chain


def test_fixture_lane_is_labelled_and_separate():
    fx = FrozenLane.fixture()
    assert fx.series_id != F.SERIES_ID and fx.lane == "FIXTURE_DEMO" and fx.config("x")["data_class"] == "SYNTHETIC_FIXTURE_DEMO"


def test_cli_refuses_fixture_on_the_github_ledger(capsys):
    from apex_worker.cli import main

    assert main(["research", "forward-shadow", "run", "--store", "github", "--source", "fixture"]) == 5
    assert main(["research", "forward-shadow", "run", "--store", "file", "--source", "alpaca",
                 "--now", "2026-10-23T22:00:00Z"]) == 5  # the wall clock only, for real data


def test_anchor_timestamps_come_from_the_server_not_the_record():
    clock = Clock(et(D0, 18, 12))
    api = MemoryGitHubAPI(clock=lambda: et(date(2026, 10, 26), 10, 0))  # GitHub's clock says after the open
    rec, _ = make(clock, api)
    rep = rec.run()
    assert rep.health["decisions_anchored_late"] == 1 and rep.health["decisions_on_time"] == 0


def test_rest_client_maps_permission_and_retries_rate_limits():
    import httpx

    from apex_worker.research.forward.store import RestGitHubAPI

    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if req.url.path.endswith("/issues") and req.method == "GET":
            if calls["n"] == 1:
                return httpx.Response(429, headers={"retry-after": "0"}, json={"message": "slow down"})
            return httpx.Response(200, json=[])
        return httpx.Response(403, json={"message": "Resource not accessible by integration"})

    api = RestGitHubAPI("t0ken", "o/r", client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _s: None)
    assert api.list_issues() == [] and calls["n"] == 2
    with pytest.raises(StoreError) as exc:
        api.create_issue("x", "y")
    assert exc.value.kind == "PERMISSION"
    with pytest.raises(StoreError):
        RestGitHubAPI("", "o/r")


def test_s5_outcomes_hold_cash_until_the_first_forward_rebalance():
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in CAL.sessions_between(D0, CAL.add_sessions(D0, 15))])
    bars = {s: pd.DataFrame({"open": 100.0 + np.arange(len(idx)), "high": 200.0, "low": 50.0,
                             "close": 100.5 + np.arange(len(idx)), "volume": 1e7}, index=idx)
            for s in MarketSource.s5_symbols()}
    decs = []
    for i, d in enumerate(idx[:12]):
        ti = W.session_index(CAL, d.date())
        reb = ti % 10 == 0
        decs.append({"body": {"decision_session": d.date().isoformat(), "session_index": ti, "arms": {
            "S5:R10": {"rebalance_session": reb, "weights": {"XLK": 1 / 3, "XLF": 1 / 3, "XLE": 1 / 3} if reb else None}}}})
        assert i >= 0
    od5 = F.OutcomeData(idx, bars, {}, idx[-1].date())
    out = F.s5_outcomes(decs, od5, W.session_index(CAL, idx[0].date()), [d.date() for d in idx[:12]])
    first_reb = next(d["body"]["decision_session"] for d in decs if d["body"]["arms"]["S5:R10"]["rebalance_session"])
    assert out[D0.isoformat()]["book_started"] is False
    started = out[first_reb]
    assert started["book_started"] and started["exposure_open"] == pytest.approx(1.0, abs=1e-6)
