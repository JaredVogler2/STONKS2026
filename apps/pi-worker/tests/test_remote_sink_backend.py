from datetime import timedelta
from decimal import Decimal

import pytest

from agent_helpers import AGENT, DUID_A, DUID_B, SECRET, T0, Clock, client, fill, server
from apex_worker.agent.client import PostSpool
from apex_worker.config import WorkerConfig
from apex_worker.contracts import ContractViolation
from apex_worker.discord_bot.app import position_backend
from apex_worker.discord_bot.backend import BackendUnavailable, LocalPositionBackend, replay_spool
from apex_worker.discord_bot.remote_backend import RemotePositionBackend
from apex_worker.discord_bot.samples import sample_assessment, sample_signal
from apex_worker.notifications import intents
from apex_worker.portfolio.ledger import LedgerError
from apex_worker.runtime.agent_state import AgentState
from apex_worker.runtime.engine import MonitoredPosition
from apex_worker.runtime.remote_sink import RemoteSink, ledger_state_from_summary
from apex_worker.services.positions import PositionNotFound, ServiceError
from apex_worker.signals.lifecycle import SignalState, transition
from apex_worker.storage.local import LocalStore
from apex_worker.timeutil import iso_utc


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    srv = server(clock)
    spool = PostSpool(tmp_path / "agent.sqlite3")
    c = client(srv, clock, spool=spool)
    local = LocalStore(tmp_path / "bot.sqlite3")
    sink = RemoteSink(c, AgentState(tmp_path / "agent.sqlite3"), release_mode="DEMO", provider="replay", feed="demo",
                      local_enqueue=local.enqueue, mirror=local)
    return srv, c, sink, local, clock


def _publish(sink, now, seed="s1"):
    sig = sample_signal(now, seed=seed)
    sink.publish_signal(sig, "RESEARCH_SETUP", intents.new_signal(sig, "RESEARCH_SETUP", now), now)
    return sig


def _activate(sig, now):
    ev = transition(sig["id"], SignalState.PUBLISHED, SignalState.ACTIVE, at=now, cause="entry",
                    discriminator="ENTRY").to_contract()
    return ev, [i for i in [intents.signal_event(sig, ev, now)] if i]


# ---- RemoteSink ---------------------------------------------------------------------------------------------------
def test_publish_and_events_reach_server_with_intents_and_local_mirror(env):
    srv, _c, sink, local, clock = env
    sig = _publish(sink, clock.now())
    assert srv.heads[sig["id"]]["state"] == "PUBLISHED" and srv.heads[sig["id"]]["events"] == 4
    assert [r["intent"]["kind"] for r in srv.outbox.values()] == ["NEW_SIGNAL"]
    assert local.latest_signal(sig["id"])["state"] == "PUBLISHED"  # bot can resolve the signal for buttons
    assert local.outbox_rows() == []  # the server outbox is the delivery source, never a local copy
    ev, notes = _activate(sig, clock.now())
    assert sink.append_signal_events(sig, [ev], notes, clock.now()) == 1
    assert srv.heads[sig["id"]]["state"] == "ACTIVE" and len(srv.outbox) == 2
    assert [s for s, st in sink.open_signals()] and sink.open_signals()[0][1] == "ACTIVE"
    assert local.latest_signal(sig["id"])["state"] == "ACTIVE"
    # an engine restart re-publishing the same revision is a no-op
    assert sink.publish_signal(sig, "RESEARCH_SETUP", intents.new_signal(sig, "RESEARCH_SETUP", clock.now()),
                               clock.now()) is False
    assert srv.duplicates["signal"] == 1 and len(srv.outbox) == 2


def test_record_scan_includes_feature_snapshots_and_sectors(env):
    from apex_worker.demo.replay import synthetic_scan_inputs
    from apex_worker.signals.candidates import CandidateEngine
    from apex_worker.signals.policy import PolicyOutcome

    srv, _c, sink, _local, clock = env
    panel, day = synthetic_scan_inputs()
    scan = CandidateEngine().scan(panel, day)
    sink.scan_started_at = clock.now() - timedelta(seconds=30)
    sink.record_scan(scan, PolicyOutcome(scan.session), clock.now())
    (run,) = srv.scans.values()
    assert run["metrics"]["sectors"] == scan.sectors and scan.sectors
    assert run["is_demo"] is True and len(srv.candidates) == min(12, len(scan.candidates))
    assert srv.feature_snapshots and all(f["is_demo"] for f in srv.feature_snapshots.values())
    assert run["started_at"] < run["finished_at"]


def test_offline_writes_spool_and_replay_in_order_without_duplicates(env):
    srv, c, sink, _local, clock = env
    srv.down = True
    sig = _publish(sink, clock.now())
    ev, notes = _activate(sig, clock.now())
    sink.append_signal_events(sig, [ev], notes, clock.now())
    assert c.spool.pending_count() == 2 and sink.open_signals()[0][1] == "ACTIVE"
    assert sink.refresh_open_signals() is False  # local view wins while writes are still spooled
    srv.down = False
    clock.advance(600)
    assert c.flush_spool()["sent"] == 2
    assert srv.heads[sig["id"]]["state"] == "ACTIVE" and len(srv.outbox) == 2 and not srv.duplicates
    assert sink.refresh_open_signals() is True and sink.open_signals()[0][0]["id"] == sig["id"]


def test_ledger_state_from_server_summary_matches_local_fifo(env, tmp_path):
    _srv, c, _sink, _local, clock = env
    events = [
        {"client_idempotency_key": "test-key-k1", "event_type": "OPEN", "symbol": "DMSEMI1", "source": "discord",
         "fill": fill("100", "10", T0 - timedelta(hours=5))},
        {"client_idempotency_key": "test-key-k2", "event_type": "ADD", "source": "discord",
         "fill": fill("110", "10", T0 - timedelta(hours=4))},
        {"client_idempotency_key": "test-key-k3", "event_type": "PARTIAL_EXIT", "source": "discord",
         "fill": fill("120", "5", T0 - timedelta(hours=3))},
    ]
    local = LocalPositionBackend(LocalStore(tmp_path / "l.sqlite3"))
    remote = RemotePositionBackend(c)
    for backend in (local, remote):
        pid = None
        for e in events:
            res = backend.record(DUID_A, {**e, **({"position_id": pid} if pid else {})}, clock.now())
            pid = res.position["position_id"]
    st = ledger_state_from_summary(remote.positions(DUID_A)[0])
    ref = local.ledger_state(local.store.member_id(DUID_A), local.positions(DUID_A)[0]["position_id"])
    assert st.total_quantity == ref.total_quantity == Decimal(15)
    assert st.avg_cost == ref.avg_cost and st.realized_pnl == ref.realized_pnl and st.state is ref.state


def test_monitored_positions_and_private_assessment(env):
    srv, c, sink, _local, clock = env
    remote = RemotePositionBackend(c)
    pos = remote.record(DUID_A, {"client_idempotency_key": "test-key-o1", "event_type": "OPEN", "symbol": "DMSEMI1",
                                 "source": "discord", "fill": fill("100", "10", T0 - timedelta(hours=2))},
                        clock.now()).position
    sink.set_work(c.get("work"))
    (mp,) = sink.monitored_positions()
    assert isinstance(mp, MonitoredPosition) and mp.position_id == pos["position_id"]
    assert mp.user_id == srv.member_id(DUID_A) and mp.ledger_state.total_quantity == Decimal(10)
    a = {**sample_assessment(pos["position_id"], "DMSEMI1", clock.now()), "price_status": "FRESH_VERIFIED"}
    intent = intents.position_update(mp.user_id, DUID_A, a, clock.now(), True)
    sink.save_assessment(mp, {**a, "_key": "k"}, intent, clock.now())
    assert sink.flush_assessments() == 1
    assert len(srv.assessments) == 1 and [r["intent"]["route"]["type"] for r in srv.outbox.values()] == ["DM"]
    assert sink.monitored_positions()[0].last_sent["status"] == a["status"]  # hysteresis survives restarts
    # a server that does not expose user_id cannot route private updates: the position is skipped, not misrouted
    srv.monitored_include_user_id = False
    sink.set_work(c.get("work"))
    assert sink.monitored_positions() == [] and sink.missing_user_ids == 1


def test_pi_system_notices_go_to_the_local_outbox(env):
    _srv, _c, sink, local, clock = env
    note = intents.system_alert("MODEL_REJECTED", "t", "d", "a", clock.now(), True, "b1")
    assert sink.enqueue(note, clock.now()) is True
    assert [r.kind for r in local.outbox_rows()] == ["SYSTEM_ALERT"]


# ---- RemotePositionBackend -----------------------------------------------------------------------------------------
def _open(backend, duid, key, qty="10", now=T0):
    return backend.record(duid, {"client_idempotency_key": f"test-key-{key}", "event_type": "OPEN", "symbol": "DMSEMI1",
                                 "source": "discord", "fill": fill("100", qty, now - timedelta(hours=1))}, now)


def test_remote_backend_scoped_to_member_and_idempotent(env):
    _srv, c, *_ = env
    b = RemotePositionBackend(c)
    first = _open(b, DUID_A, "dup")
    second = _open(b, DUID_A, "dup")
    assert (first.status, second.status) == ("persisted", "duplicate") and second.position["event_count"] == 1
    pid = first.position["position_id"]
    assert b.position(DUID_A, pid)["total_quantity"] == "10"
    with pytest.raises(PositionNotFound):
        b.position(DUID_B, pid)  # another member's id behaves like a missing one
    assert b.positions(DUID_B) == []


def test_remote_backend_maps_ledger_and_service_errors(env):
    srv, c, *_ = env
    b = RemotePositionBackend(c)
    pid = _open(b, DUID_A, "o").position["position_id"]
    with pytest.raises(LedgerError) as e:
        b.record(DUID_A, {"client_idempotency_key": "test-key-sell", "event_type": "PARTIAL_EXIT", "position_id": pid,
                          "source": "discord", "fill": fill("101", "15", T0 - timedelta(minutes=5))}, T0)
    assert e.value.code == "OVERSELL"
    with pytest.raises(LedgerError) as e:
        b.record(DUID_A, {"client_idempotency_key": "test-key-close", "event_type": "CLOSE", "position_id": pid,
                          "source": "discord", "fill": fill("101", "3", T0 - timedelta(minutes=5))}, T0)
    assert e.value.code == "CLOSE_QUANTITY_MISMATCH"
    with pytest.raises(ServiceError) as e:
        b.record(DUID_A, {"client_idempotency_key": "test-key-x", "event_type": "ADD", "source": "discord",
                          "fill": fill("101", "1", T0 - timedelta(minutes=5))}, T0)
    assert e.value.code == "POSITION_REQUIRED"
    srv.ledger_store.revoke_member(DUID_A, T0)
    with pytest.raises(ServiceError) as e:
        _open(b, DUID_A, "after-revoke")
    assert e.value.code == "MEMBERSHIP_REVOKED"
    with pytest.raises(ContractViolation):
        b.record(DUID_A, {"event_type": "OPEN"}, T0)  # validated locally, never sent


def test_outage_and_device_problems_raise_backend_unavailable_then_spool_replays_once(env, tmp_path):
    srv, c, *_ = env
    b = RemotePositionBackend(c)
    srv.down = True
    with pytest.raises(BackendUnavailable):
        _open(b, DUID_A, "queued")
    srv.down = False
    c.offline_until = 0
    srv.devices[AGENT]["status"] = "pending_approval"
    with pytest.raises(BackendUnavailable):
        _open(b, DUID_A, "queued")
    srv.devices[AGENT]["status"] = "active"
    # the bot spooled the event when it saw BackendUnavailable; its replay loop pushes it through the remote backend
    bot = LocalStore(tmp_path / "bot.sqlite3")
    ev = {"client_idempotency_key": "test-key-queued", "event_type": "OPEN", "symbol": "DMSEMI1", "source": "discord",
          "fill": fill("100", "10", T0 - timedelta(hours=1))}
    bot.spool_put("discord_position_event", "test-key-queued", {"discord_user_id": DUID_A, "event": ev}, T0)
    assert replay_spool(b, bot, T0) == {"acked": 1, "rejected": 0, "retry": 0}
    assert replay_spool(b, bot, T0)["acked"] == 0 and len(b.positions(DUID_A)) == 1


def test_positions_cache_is_short_and_invalidated_by_writes(env):
    srv, c, *_ = env
    t = {"now": 0.0}
    b = RemotePositionBackend(c, clock=lambda: t["now"])
    assert b.positions(DUID_A) == []
    n = len(srv.log)
    b.positions(DUID_A)
    assert len(srv.log) == n  # served from the 20 s cache (Discord autocomplete has 3 s)
    _open(b, DUID_A, "c1")
    assert len(b.positions(DUID_A)) == 1


def test_bot_selects_remote_backend_only_when_paired(tmp_path, monkeypatch):
    store = LocalStore(":memory:")
    offline = WorkerConfig(state_dir=tmp_path)
    assert isinstance(position_backend(offline, store, 1)[0], LocalPositionBackend)
    paired = WorkerConfig(state_dir=tmp_path, api_base_url="https://apex.test", agent_id=AGENT, agent_hmac_secret=SECRET)
    backend, label = position_backend(paired, store, 100000000000000099)
    assert isinstance(backend, RemotePositionBackend) and "cloud" in label and backend.guild_id == "100000000000000099"
    monkeypatch.setenv("APEX_BOT_POSITIONS", "local")
    assert isinstance(position_backend(paired, store, 1)[0], LocalPositionBackend)


def test_iso_helper_sanity():
    assert iso_utc(T0).endswith("Z")
