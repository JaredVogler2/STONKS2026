"""Phase 09 interaction demo as a test: one shared alert, three members with private outcomes, one member's DMs
blocked (held privately, never leaked), then a 'Pi restart' with no duplicate alert or fill."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from apex_worker.discord_bot.backend import LocalMemberBackend, LocalPositionBackend
from apex_worker.discord_bot.delivery import ChannelMap, MemoryTransport, OutboxDelivery
from apex_worker.discord_bot.limits import message_within_limits
from apex_worker.discord_bot.samples import sample_assessment, sample_signal
from apex_worker.discord_bot.service import Actor, BotService
from apex_worker.notifications import components
from apex_worker.notifications import intents as intent_builders
from apex_worker.storage.local import LocalStore

GUILD = "900000000000000001"
HC = 900000000000000101
SECRET = b"scenario-component-secret-0123456789"
A, B, C = "100000000000000001", "100000000000000002", "100000000000000003"
T0 = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)


class World:
    def __init__(self, path, now):
        self.now = now
        self.store = LocalStore(path)
        self.positions = LocalPositionBackend(self.store, is_demo=True)
        self.members = LocalMemberBackend(self.store)
        self.svc = BotService(positions=self.positions, members=self.members, signals=self.store, spool=self.store,
                              component_secret=SECRET, guild_id=GUILD, clock=lambda: self.now)
        self.transport = MemoryTransport(dm_forbidden={B})
        self.loop = OutboxDelivery(self.store, self.transport, SECRET, ChannelMap({"high_conviction_alerts": HC}),
                                   self.members, clock=lambda: self.now)


def act(user, iid):
    return Actor(user, str(iid), GUILD)


async def test_one_alert_three_private_outcomes_and_restart(tmp_path):
    w = World(tmp_path / "apex.sqlite3", T0)
    sig = sample_signal(T0 - timedelta(minutes=5), symbol="DMNVDA", seed="scenario")
    w.store.save_signal_revision(sig)
    w.store.enqueue(intent_builders.new_signal(sig, "HIGH_CONVICTION", T0), T0)
    assert (await w.loop.deliver_due(T0))["delivered"] == 1
    alert = w.transport.sent[0]
    assert alert["target"] == f"channel:{HC}"
    buttons = {b["label"]: b["custom_id"] for b in alert["message"]["components"][0]["components"]}
    assert set(buttons) == {"Record My Entry", "Watch", "View Evidence", "Mute Symbol"}

    # A opens via the button + modal, then adds via its private update button
    w.now = T0 + timedelta(minutes=10)
    modal = w.svc.component(act(A, 1300000000000000011), buttons["Record My Entry"]).modal
    r = w.svc.modal_submit(act(A, 1300000000000000012), modal.custom_id, {"price": "101", "quantity": "20"})
    assert r.code == "PERSISTED"
    a_pid = w.positions.positions(A)[0]["position_id"]
    add_modal = w.svc.component(act(A, 1300000000000000013), components.make(SECRET, "posupd", f"add.{a_pid}")).modal
    assert w.svc.modal_submit(act(A, 1300000000000000014), add_modal.custom_id,
                              {"price": "100.5", "quantity": "10"}).code == "PERSISTED"
    # B watches, buys later; C only watches
    assert w.svc.component(act(B, 1300000000000000021), buttons["Watch"]).code == "PERSISTED"
    assert w.svc.component(act(C, 1300000000000000031), buttons["Watch"]).code == "PERSISTED"
    w.now = T0 + timedelta(hours=3)
    modal_b = w.svc.component(act(B, 1300000000000000022), buttons["Record My Entry"]).modal
    assert w.svc.modal_submit(act(B, 1300000000000000023), modal_b.custom_id,
                              {"price": "102.75", "quantity": "5"}).code == "PERSISTED"
    # DM preferences: A and B opted in (B's DMs are blocked by Discord), C did not opt in
    w.svc.notifications(act(A, 1300000000000000015), dms=True)
    w.svc.notifications(act(B, 1300000000000000024), dms=True)

    pos = {u: w.positions.positions(u) for u in (A, B, C)}
    assert (pos[A][0]["total_quantity"], pos[A][0]["avg_cost"], len(pos[A][0]["lots"])) == ("30", "100.833333", 2)
    assert (pos[B][0]["total_quantity"], pos[B][0]["avg_cost"]) == ("5", "102.75")
    assert pos[C][0]["state"] == "WATCHING"

    # one shared market move -> three independent private updates
    for u in (A, B, C):
        uid = w.store.member_id(u)
        p = pos[u][0]
        w.store.enqueue(intent_builders.position_update(uid, u, sample_assessment(p["position_id"], "DMNVDA", w.now),
                                                        w.now, True), w.now)
    counts = await w.loop.deliver_due(w.now)
    assert counts == {"delivered": 1, "deferred": 2, "failed": 0, "dead_letter": 0, "rate_limited": 0}
    dms = [s for s in w.transport.sent if s["target"].startswith("dm:")]
    assert [d["target"] for d in dms] == [f"dm:{A}"]
    channel_text = json.dumps([s["message"] for s in w.transport.sent if s["target"].startswith("channel:")])
    for secret_bit in ("102.75", "100.833333", "30 sh", A, B, C):
        assert secret_bit not in channel_text  # no member data ever reaches the shared channel

    # B sees the held update privately on the next command; C's stays held; A sees nothing extra
    rb = w.svc.positions_view(act(B, 1300000000000000025))
    assert rb.private_outbox_ids and "could not be delivered by DM" in rb.content
    assert "refused the DM" in rb.content and rb.to_message()["flags"] == 64 and message_within_limits(rb.to_message())
    w.svc.after_reply(rb)
    assert not w.svc.positions_view(act(A, 1300000000000000016)).private_outbox_ids
    held_c = w.members.pending_private(w.store.member_id(C))
    assert len(held_c) == 1

    # ---- Pi restart: new process objects on the same SQLite file
    alerts_before = len(w.transport.sent)
    events_before = w.store.query("SELECT COUNT(*) FROM position_events")[0][0]
    w.store.close()
    w2 = World(tmp_path / "apex.sqlite3", w.now + timedelta(minutes=30))
    for _ in range(3):
        assert (await w2.loop.deliver_due(w2.now))["delivered"] == 0
    assert w2.transport.sent == []
    # Discord re-delivering the same interactions after reconnect cannot create fills
    assert w2.svc.modal_submit(act(A, 1300000000000000012), modal.custom_id,
                               {"price": "101", "quantity": "20"}).code == "DUPLICATE"
    assert w2.svc.modal_submit(act(B, 1300000000000000023), modal_b.custom_id,
                               {"price": "102.75", "quantity": "5"}).code == "DUPLICATE"
    assert w2.store.query("SELECT COUNT(*) FROM position_events")[0][0] == events_before
    assert alerts_before == 2
    # and the old alert's buttons still work after the restart (verified signed ids, no in-memory views)
    assert w2.svc.component(act(C, 1300000000000000032), buttons["View Evidence"]).code == "EVIDENCE"
    w2.store.close()
