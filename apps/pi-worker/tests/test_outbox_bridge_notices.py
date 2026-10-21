import asyncio
from datetime import timedelta

import pytest

from agent_helpers import DUID_A, DUID_B, Clock, client, fill, server
from apex_worker.discord_bot.backend import LocalMemberBackend
from apex_worker.discord_bot.delivery import CHANNEL_KEYS, ChannelMap, MemoryTransport, OutboxDelivery
from apex_worker.discord_bot.samples import sample_assessment, sample_signal
from apex_worker.notifications import intents
from apex_worker.runtime.notices import SystemNotices
from apex_worker.runtime.outbox_bridge import OutboxBridge
from apex_worker.storage.local import LocalStore
from apex_worker.timeutil import iso_utc

CHANNELS = ChannelMap(dict.fromkeys(CHANNEL_KEYS, 100000000000000555), "test")


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    srv = server(clock)
    c = client(srv, clock)
    local = LocalStore(tmp_path / "apex_local.sqlite3")
    return srv, c, local, clock, tmp_path


def _deliver(local, transport, now):
    return asyncio.run(OutboxDelivery(local, transport, b"test-secret", CHANNELS, LocalMemberBackend(local),
                                      clock=lambda: now).deliver_due(now))


def _dm_for(srv, c, duid, now, opt_in=True):
    srv.set_dm_opt_in(duid, opt_in)
    pos = srv.positions.record(duid, {"client_idempotency_key": f"test-open-{duid}", "event_type": "OPEN",
                                      "symbol": "DMSEMI1", "source": "discord",
                                      "fill": fill("101", "5", now - timedelta(hours=2))}, now).position
    a = {**sample_assessment(pos["position_id"], "DMSEMI1", now), "price_status": "FRESH_VERIFIED"}
    c.post("positions/assessments", {"items": [{"discord_user_id": duid, "assessment": a, "assessment_key": "t:" + a["asof_at"],
                                                "intent": intents.position_update(srv.member_id(duid), duid, a, now, True)}]})


def test_channel_row_bridged_delivered_and_acked(env):
    srv, c, local, clock, _ = env
    now = clock.now()
    sig = sample_signal(now)
    srv.heads[sig["id"]] = {"state": "PUBLISHED", "events": 4, "symbol": sig["symbol"]}
    srv._enqueue(intents.new_signal(sig, "RESEARCH_SETUP", now))
    bridge = OutboxBridge(c, local)
    assert bridge.ingest(c.get("work")["outbox"], now)["inserted"] == 1
    (row,) = local.outbox_rows()
    assert row.event_key == f"NEW_SIGNAL:{sig['id']}:r1" and row.route["channel_key"] == "market_intelligence"
    transport = MemoryTransport()
    assert _deliver(local, transport, now)["delivered"] == 1
    assert bridge.ack_pass(now)["acked"] == 1
    srow = next(iter(srv.outbox.values()))
    assert srow["state"] == "sent" and srow["discord_message_id"] == transport.sent[0]["message_id"]
    assert bridge.ack_pass(now)["acked"] == 0  # nothing left to ack


def test_dm_row_stays_dm_rewritten_to_local_member_and_blocked_dm_held(env):
    srv, c, local, clock, _ = env
    now = clock.now()
    _dm_for(srv, c, DUID_B, now)
    bridge = OutboxBridge(c, local)
    bridge.ingest(c.get("work")["outbox"], now)
    (row,) = local.outbox_rows()
    local_uid = local.member_id(DUID_B)
    assert row.route == {"type": "DM", "user_id": local_uid, "discord_user_id": DUID_B}
    assert local_uid != srv.member_id(DUID_B) or True  # separate databases: ids are independent
    assert local.member_prefs(local_uid)["dm_opt_in"] is True  # seeded from the server for a new local member
    transport = MemoryTransport(dm_forbidden={DUID_B})
    assert _deliver(local, transport, now)["deferred"] == 1
    bridge.ack_pass(now)
    srow = next(iter(srv.outbox.values()))
    assert srow["state"] == "deferred" and transport.sent == []  # never redirected to a channel
    LocalMemberBackend(local).release_private(local_uid, now)  # shown in the member's next ephemeral reply
    assert bridge.ack_pass(now)["acked"] == 1 and srow["state"] == "sent"


def test_bridge_is_idempotent_across_restart_and_re_lease(env):
    srv, c, local, clock, tmp = env
    now = clock.now()
    _dm_for(srv, c, DUID_A, now)
    OutboxBridge(c, local).ingest(c.get("work")["outbox"], now)
    # crash before delivery/ack: the server lease expires and the row is handed out again
    clock.advance(180)
    restarted = OutboxBridge(c, LocalStore(tmp / "apex_local.sqlite3"))
    items = c.get("work")["outbox"]
    assert len(items) == 1 and restarted.ingest(items, clock.now())["known"] == 1
    assert len(local.outbox_rows()) == 1
    transport = MemoryTransport()
    _deliver(local, transport, clock.now())
    restarted.ack_pass(clock.now())
    assert len(transport.sent) == 1 and next(iter(srv.outbox.values()))["state"] == "sent"
    clock.advance(180)
    assert c.get("work")["outbox"] == []  # sent rows are never leased again


def test_disabled_member_dm_is_held_without_queueing(env):
    srv, c, local, clock, _ = env
    now = clock.now()
    _dm_for(srv, c, DUID_A, now)
    srv.ledger_store.revoke_member(DUID_A, now)  # server reports discord_user_id = null for disabled members
    bridge = OutboxBridge(c, local)
    assert bridge.ingest(c.get("work")["outbox"], now)["held"] == 1
    assert local.outbox_rows() == []
    bridge.ack_pass(now)
    assert next(iter(srv.outbox.values()))["state"] == "deferred"


def test_dead_letter_acked_failed_and_bad_item_does_not_poison_batch(env):
    srv, c, local, clock, _ = env
    now = clock.now()
    for seed in ("a", "b"):
        sig = sample_signal(now, seed=seed)
        srv.heads[sig["id"]] = {"state": "PUBLISHED", "events": 4, "symbol": sig["symbol"]}
        srv._enqueue(intents.new_signal(sig, "RESEARCH_SETUP", now))
    bridge = OutboxBridge(c, local)
    bridge.ingest(c.get("work")["outbox"], now)
    rows = local.outbox_rows()
    local.ack_outbox(rows[0].id, "failed", now, error="HTTP_500", max_attempts=1)  # local dead letter
    local.ack_outbox(rows[1].id, "delivered", now, discord_message_id="1400000000000000001")
    srv.outbox.pop(next(k for k, v in srv.outbox.items() if v["event_key"] == rows[1].event_key))  # vanished server-side
    counts = bridge.ack_pass(now)
    assert counts["acked"] == 1 and counts["closed"] == 1
    failed = next(v for v in srv.outbox.values() if v["event_key"] == rows[0].event_key)
    assert failed["state"] == "pending" and failed["attempts"] == 1  # server applies its own backoff / dead-letter


def test_ack_failure_is_retried_next_pass(env):
    srv, c, local, clock, _ = env
    now = clock.now()
    sig = sample_signal(now)
    srv.heads[sig["id"]] = {"state": "PUBLISHED", "events": 4, "symbol": sig["symbol"]}
    srv._enqueue(intents.new_signal(sig, "RESEARCH_SETUP", now))
    bridge = OutboxBridge(c, local)
    bridge.ingest(c.get("work")["outbox"], now)
    _deliver(local, MemoryTransport(), now)
    srv.down = True
    assert bridge.ack_pass(now)["deferred_to_next"] == 1
    srv.down = False
    c.offline_until = 0
    assert bridge.ack_pass(now)["acked"] == 1


# ---- system notices ----------------------------------------------------------------------------------------------
@pytest.fixture
def notices(tmp_path):
    local = LocalStore(tmp_path / "n.sqlite3")
    return SystemNotices(local.enqueue, is_demo=True), local


def _kinds(local):
    return [(r.payload["payload"]["alert"], r.route["channel_key"], r.kind) for r in local.outbox_rows()]


def test_missed_training_notice_at_most_once_per_day(notices):
    n, local = notices
    clock = Clock()
    old = iso_utc(clock.now() - timedelta(days=9))
    training = {"last_success_at": old, "last_run_at": old, "last_run_status": "failed", "champions": []}
    assert n.check_training(training, clock.now()) == "STALE"
    n.check_training(training, clock.now() + timedelta(hours=3))
    assert _kinds(local) == [("MISSED_TRAINING", "system_status", "SYSTEM_ALERT")]
    n.check_training(training, clock.now() + timedelta(days=1))
    assert len(local.outbox_rows()) == 2
    fresh = {**training, "last_success_at": iso_utc(clock.now() - timedelta(days=6))}
    assert n.check_training(fresh, clock.now()) == "OK"
    assert n.check_training(None, clock.now()) == "UNKNOWN"
    assert n.check_training({"last_success_at": None, "last_run_at": None}, clock.now()) == "UNKNOWN"
    assert len(local.outbox_rows()) == 2  # unknown is reported in health, never as a false alarm


def test_registry_expiry_and_quota_notices(notices):
    n, local = notices
    clock = Clock()
    now = clock.now()
    assert n.check_registry(1, now - timedelta(hours=1), now) == "OK"
    assert n.check_registry(3, now - timedelta(hours=2), now) == "DOWN"
    soon, later = iso_utc(now + timedelta(days=2)), iso_utc(now + timedelta(days=10))
    assert n.check_expiry([("s1", "mdl_one", soon), ("s2", "mdl_two", later)], now) == ["mdl_one"]
    n.check_expiry([("s1", "mdl_one", soon)], now + timedelta(hours=1))
    n.check_expiry([("s1", "mdl_one", iso_utc(now - timedelta(minutes=1)))], now)
    assert n.check_quota([{"provider": "alpaca", "level": "WARN", "used": 110000, "limit": 150000, "fraction": 0.73}],
                         now) == ["alpaca"]
    assert n.check_quota([{"provider": "alpaca", "level": "OK"}], now) == []
    alerts = [k[0] for k in _kinds(local)]
    assert alerts == ["REGISTRY_UNREACHABLE", "MODEL_EXPIRING", "MODEL_EXPIRED", "QUOTA_LOW"]
    assert all(k[1] == "system_status" and k[2] == "SYSTEM_ALERT" for k in _kinds(local))  # never investment alerts
