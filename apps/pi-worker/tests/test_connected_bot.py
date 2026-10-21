"""Connected-mode wiring to the server additions: /notices, per-item acks with `rendered`, secret_versions rotation,
positions/history (corrections, removes, starter refs) and member prefs/revoke/delete sync."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from agent_helpers import AGENT, BASE, DUID_A, DUID_B, SECRET, T0, Clock, client, fill, server
from apex_worker.agent.client import AgentClient
from apex_worker.agent.crypto import DeviceKeys
from apex_worker.agent.secrets import SecretManager
from apex_worker.contracts import validate
from apex_worker.discord_bot.backend import MEMBER_SPOOL_KIND, BackendUnavailable, PositionHistory, replay_spool
from apex_worker.discord_bot.delivery import CHANNEL_KEYS, ChannelMap, MemoryTransport, OutboxDelivery
from apex_worker.discord_bot.limits import clamp_message
from apex_worker.discord_bot.remote_backend import RemoteMemberBackend, RemotePositionBackend, event_from_server
from apex_worker.discord_bot.samples import sample_assessment, sample_signal
from apex_worker.discord_bot.service import Actor, BotService
from apex_worker.notifications import intents
from apex_worker.notifications.render import render
from apex_worker.runtime.notices import NoticeRouter
from apex_worker.runtime.outbox_bridge import OutboxBridge
from apex_worker.services.positions import PositionNotFound
from apex_worker.storage.local import LocalStore

COMPONENT_SECRET = b"test-component-secret-0123456789abcdef"
CHANNELS = ChannelMap(dict.fromkeys(CHANNEL_KEYS, 100000000000000555), "test")


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    srv = server(clock)
    acks: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/notifications/ack"):
            acks.append(json.loads(req.content))
        return srv.handle(req)

    c = AgentClient(BASE, AGENT, SECRET, transport=httpx.MockTransport(handler), clock=clock, sleep=lambda _s: None,
                    rng=lambda: 0.0)
    local = LocalStore(tmp_path / "apex_local.sqlite3")
    return srv, c, local, clock, acks


def _deliver(local, transport, now):
    from apex_worker.discord_bot.backend import LocalMemberBackend

    return asyncio.run(OutboxDelivery(local, transport, COMPONENT_SECRET, CHANNELS, LocalMemberBackend(local),
                                      clock=lambda: now).deliver_due(now))


def _notice(now, bucket="b1"):
    return intents.system_alert("MODEL_REJECTED", "Pi refused model", "d", "a", now, True, bucket)


# ---- /notices routing ----------------------------------------------------------------------------------------------
def test_notice_goes_to_server_and_reaches_bot_once_via_bridge(env):
    _srv, c, local, clock, _acks = env
    router = NoticeRouter(c, local.enqueue)
    now = clock.now()
    assert router(_notice(now), now) is True and router.last_route == "server"
    assert local.outbox_rows() == []  # not delivered locally as well
    assert router(_notice(now), now) is False  # idempotent by event_key on the server
    OutboxBridge(c, local).ingest(c.get("work")["outbox"], now)
    (row,) = local.outbox_rows()
    assert row.event_key == "SYS:MODEL_REJECTED:b1" and row.route == {"type": "CHANNEL", "channel_key": "system_status"}


def test_notice_falls_back_locally_and_never_duplicates(env):
    srv, c, local, clock, _acks = env
    router = NoticeRouter(c, local.enqueue)
    now = clock.now()
    srv.down = True
    assert router(_notice(now), now) is True and router.last_route == "local"
    transport = MemoryTransport()
    _deliver(local, transport, now)
    # the server comes back and the same notice (same day bucket) is raised again: the dashboard records it ...
    srv.down = False
    c.offline_until = 0
    assert router(_notice(now), now) is True and router.last_route == "server"
    bridge = OutboxBridge(c, local)
    bridge.ingest(c.get("work")["outbox"], now)
    _deliver(local, transport, now)
    bridge.ack_pass(now)
    # ... but Discord got it exactly once, and the server copy is marked delivered
    assert len(transport.sent) == 1 and next(iter(srv.outbox.values()))["state"] == "sent"


def test_notice_pending_device_goes_local_and_non_notice_is_refused(env):
    srv, c, local, clock, _acks = env
    router = NoticeRouter(c, local.enqueue)
    now = clock.now()
    srv.devices[AGENT]["status"] = "pending_approval"
    assert router(_notice(now, "p1"), now) is True and router.last_route == "local"
    srv.devices[AGENT]["status"] = "active"
    bad = intents.new_signal(sample_signal(now), "RESEARCH_SETUP", now)
    assert router(bad, now) is False and not srv.outbox and len(local.outbox_rows()) == 1


# ---- per-item acks and the rendered channel message ----------------------------------------------------------------
def test_channel_ack_carries_rendered_message_dm_ack_never(env):
    srv, c, local, clock, acks = env
    now = clock.now()
    sig = sample_signal(now)
    srv.heads[sig["id"]] = {"state": "PUBLISHED", "events": 4, "symbol": sig["symbol"]}
    channel_intent = intents.new_signal(sig, "RESEARCH_SETUP", now)
    srv._enqueue(channel_intent)
    srv.set_dm_opt_in(DUID_A, True)
    pos = srv.positions.record(DUID_A, {"client_idempotency_key": "test-key-dm-open", "event_type": "OPEN",
                                        "symbol": "DMSEMI1", "source": "discord",
                                        "fill": fill("101", "5", now - timedelta(hours=1))}, now).position
    a = sample_assessment(pos["position_id"], "DMSEMI1", now)
    validate("PositionAssessment", a)  # the bot sample is contract-valid now
    srv._enqueue(intents.position_update(srv.member_id(DUID_A), DUID_A, a, now, True))
    bridge = OutboxBridge(c, local, component_secret=COMPONENT_SECRET)
    bridge.ingest(c.get("work")["outbox"], now)
    transport = MemoryTransport()
    assert _deliver(local, transport, now)["delivered"] == 2
    bridge.ack_pass(now)
    sent = [a for body in acks for a in body["acks"]]
    by_status = {(a["status"], "rendered" in a) for a in sent}
    assert by_status == {("delivered", True), ("delivered", False)}
    chan_row = next(r for r in srv.outbox.values() if r["intent"]["route"]["type"] == "CHANNEL")
    dm_row = next(r for r in srv.outbox.values() if r["intent"]["route"]["type"] == "DM")
    assert chan_row["rendered"] == clamp_message(render(channel_intent, COMPONENT_SECRET).to_discord())
    assert "rendered" not in dm_row and chan_row["state"] == dm_row["state"] == "sent"
    channel_msg = next(t["message"] for t in transport.sent if t["target"].startswith("channel:"))
    assert chan_row["rendered"]["embeds"] == channel_msg["embeds"]  # exactly what the bot posted


def test_per_item_ack_errors_close_only_the_bad_item_in_one_request(env):
    srv, c, local, clock, acks = env
    now = clock.now()
    for seed in ("a", "b"):
        sig = sample_signal(now, seed=seed)
        srv.heads[sig["id"]] = {"state": "PUBLISHED", "events": 4, "symbol": sig["symbol"]}
        srv._enqueue(intents.new_signal(sig, "RESEARCH_SETUP", now))
    bridge = OutboxBridge(c, local)
    bridge.ingest(c.get("work")["outbox"], now)
    _deliver(local, MemoryTransport(), now)
    srv.outbox[next(iter(srv.outbox))]["leased_by"] = "agt_someoneelse1"  # 409 LEASE_CONFLICT for that one
    counts = bridge.ack_pass(now)
    assert counts == {"acked": 1, "closed": 1, "deferred_to_next": 0} and len(acks) == 1
    assert bridge.ack_pass(now) == {"acked": 0, "closed": 0, "deferred_to_next": 0}


# ---- secret rotation via secret_versions ---------------------------------------------------------------------------
def test_secret_versions_from_config_and_heartbeat_drive_rotation(tmp_path):
    clock = Clock()
    keys = DeviceKeys.generate()
    srv = server(clock, x25519=keys.x25519_public)
    srv.set_integration("alpaca", {"key_id": "TESTKEYID000000000000", "secret_key": "TEST-ONLY-a"}, 1)
    c = client(srv, clock)
    mgr = SecretManager(c, keys, clock=clock.now)
    cfg = c.get("config")
    assert cfg["secret_versions"] == {"alpaca": 1}
    mgr.maybe_refresh(cfg)
    assert mgr.version == 1
    srv.set_integration("alpaca", {"key_id": "TESTKEYID000000000001", "secret_key": "TEST-ONLY-b"}, 2)
    hb = {"agent_id": AGENT, "sent_at": "2026-10-20T20:15:00Z", "worker_version": "0.1.0", "release_mode": "DEMO",
          "components": {}, "resources": {}, "secret_version_ack": mgr.version}
    res = c.post("heartbeat", hb).data
    assert res["secret_versions"] == {"alpaca": 2}
    assert mgr.maybe_refresh(res) is True and mgr.credentials.key_id.endswith("1")
    srv.devices[AGENT]["status"] = "pending_approval"
    assert c.post("heartbeat", hb).data["secret_versions"] == {}  # nothing announced to an unapproved device


# ---- positions/history -------------------------------------------------------------------------------------------
def test_history_maps_server_rows_to_bot_shape(env):
    _srv, c, *_ = env
    b = RemotePositionBackend(c)
    assert isinstance(b, PositionHistory)
    pid = b.record(DUID_A, {"client_idempotency_key": "test-key-h1", "event_type": "OPEN", "symbol": "DMSEMI1",
                            "source": "discord", "fill": fill("101.5", "10", T0 - timedelta(hours=3))}, T0).position["position_id"]
    first = b.events(DUID_A, pid)[0]
    b.record(DUID_A, {"client_idempotency_key": "test-key-h2", "event_type": "CORRECT", "position_id": pid,
                      "source": "discord", "correction": {"target_event_id": first["event_id"], "void": False,
                                                          "replacement": fill("101", "10", T0 - timedelta(hours=3))}}, T0)
    evs = b.events(DUID_A, pid)
    assert [e["event_type"] for e in evs] == ["OPEN", "CORRECT"]
    assert evs[0]["fill"] == {"price": "101.5", "quantity": "10", "fees": "0", "fill_at": "2026-10-20T17:15:00Z"}
    assert evs[1]["correction"]["target_event_id"] == first["event_id"] and evs[1]["correction"]["void"] is False
    assert evs[1]["correction"]["replacement"]["price"] == "101"
    assert b.position_for_key(DUID_A, "test-key-h1") == pid
    assert b.position_for_key(DUID_A, "test-key-none") is None
    with pytest.raises(PositionNotFound):
        b.events(DUID_B, pid)  # another member's position id
    assert event_from_server({"id": "e", "seq": 1, "event_type": "WATCH", "price": None, "quantity": None, "fees": None,
                              "event_at": None, "correction_json": None, "notes": None, "source": "discord",
                              "recorded_at": "2026-10-20T17:15:00.123Z"}) == {
        "event_id": "e", "seq": 1, "event_type": "WATCH", "recorded_at": "2026-10-20T17:15:00Z", "source": "discord"}


GUILD = "900000000000000001"


def _actor(user, iid=[1_300_000_000_000_000_001]):  # noqa: B006 - deliberate shared counter
    iid[0] += 1
    return Actor(discord_user_id=user, interaction_id=str(iid[0]), guild_id=GUILD)


@pytest.fixture
def bot(env):
    srv, c, local, _clock, _acks = env
    positions = RemotePositionBackend(c)
    members = RemoteMemberBackend(local, c, positions=positions)
    svc = BotService(positions=positions, members=members, signals=local, spool=local, component_secret=COMPONENT_SECRET,
                     guild_id=GUILD, owner_ids=[], release_mode="DEMO", clock=lambda: T0)
    return svc, positions, members, srv, c, local


def test_connected_correct_and_remove_flows_use_cloud_history(bot):
    svc, positions, _m, _srv, _c, _local = bot
    assert svc.entry(_actor(DUID_A), ticker="NVDA", price=180, shares=20).ok
    svc.add(_actor(DUID_A), position="NVDA", price=170, shares=10)
    pos = positions.positions(DUID_A)[0]
    prompt = svc.correct(_actor(DUID_A), position=pos["position_id"], fill=pos["lots"][1]["lot_id"][:8], action="replace",
                         price=171)
    assert prompt.code == "CONFIRM_REQUIRED"  # no longer UNSUPPORTED in connected mode
    assert svc.component(_actor(DUID_A), prompt.rows[0][0].custom_id).code == "PERSISTED"
    after = positions.positions(DUID_A)[0]
    assert after["lots"][1]["fill_price"] == "171" and after["total_quantity"] == "30"
    assert [e["event_type"] for e in positions.events(DUID_A, pos["position_id"])] == ["OPEN", "ADD", "CORRECT"]
    svc.entry(_actor(DUID_A), ticker="AMD", price=100, shares=2)
    rm = svc.remove(_actor(DUID_A), position_id="AMD")
    assert rm.code == "CONFIRM_REQUIRED" and svc.component(_actor(DUID_A), rm.rows[0][0].custom_id).code == "PERSISTED"
    assert [p["symbol"] for p in positions.positions(DUID_A)] == ["NVDA"]


def test_connected_starter_number_reference(bot):
    svc, positions, _m, srv, _c, _local = bot
    srv.positions.record(DUID_A, {"client_idempotency_key": "starter:7", "event_type": "OPEN", "symbol": "DMSEMI1",
                                  "source": "starter_import", "fill": fill("50", "4", T0 - timedelta(days=2))}, T0)
    rm = svc.remove(_actor(DUID_A), position_id=7)
    assert rm.code == "CONFIRM_REQUIRED", rm.content
    assert svc.component(_actor(DUID_A), rm.rows[0][0].custom_id).code == "PERSISTED"
    assert positions.positions(DUID_A) == []


# ---- member actions ----------------------------------------------------------------------------------------------
def test_member_prefs_and_revoke_sync_or_spool_then_replay(bot):
    svc, positions, members, srv, c, local = bot
    uid = members.ensure(DUID_A, T0)
    members.set_dm_opt_in(uid, True)
    members.set_high_priority_mentions(uid, True, T0)
    srv_uid = srv.ledger_store.member_id(DUID_A)
    assert srv.ledger_store.member_prefs(srv_uid)["dm_opt_in"] is True and srv.high_priority[srv_uid] is True
    srv.down = True
    members.set_dm_opt_in(uid, False)  # applied locally at once, cloud sync queued
    members.set_high_priority_mentions(uid, False, T0 + timedelta(seconds=1))
    members.revoke(DUID_B, T0 + timedelta(seconds=2))
    queued = local.query("SELECT kind FROM spool WHERE state='pending'")
    assert [r["kind"] for r in queued] == [MEMBER_SPOOL_KIND] * 3 and members.prefs(uid)["dm_opt_in"] is False
    srv.down = False
    c.offline_until = 0
    later = datetime.now(UTC) + timedelta(minutes=5)  # set_dm_opt_in spools at wall-clock time
    assert replay_spool(positions, local, later, members=members) == {"acked": 3, "rejected": 0, "retry": 0}
    assert [a["action"] for a in srv.member_actions[-3:]] == ["prefs", "prefs", "revoke"]
    assert srv.ledger_store.member_prefs(srv_uid)["dm_opt_in"] is False and srv.high_priority[srv_uid] is False
    assert replay_spool(positions, local, later, members=members)["acked"] == 0  # replayed exactly once
    assert svc.members.sync_status(DUID_A)["pending"] == []  # member actions are not shown as pending fills


def test_delete_my_data_is_cloud_first_and_never_half_done(bot):
    svc, positions, members, srv, c, _local = bot
    svc.entry(_actor(DUID_A), ticker="NVDA", price=180, shares=20)
    uid = members.ensure(DUID_A, T0)
    srv.down = True
    with pytest.raises(BackendUnavailable):
        members.delete_data(uid)
    srv.down = False
    c.offline_until = 0
    assert len(positions.positions(DUID_A)) == 1  # nothing was deleted anywhere
    step1 = svc.delete_my_data(_actor(DUID_A))
    step2 = svc.component(_actor(DUID_A), step1.rows[0][0].custom_id)
    done = svc.component(_actor(DUID_A), step2.rows[0][0].custom_id)
    assert done.code == "DELETED" and "Deleted: 1 position" in done.content
    assert positions.positions(DUID_A, include_closed=True) == []
    assert srv.member_actions[-2]["action"] == "delete_my_data" and srv.member_actions[-1]["dm_opt_in"] is False
