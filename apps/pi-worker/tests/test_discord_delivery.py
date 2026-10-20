"""Outbox delivery loop tests with a fake transport (no network): routing, privacy, 429, retries, restarts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from apex_worker.discord_bot.backend import LocalMemberBackend, LocalPositionBackend
from apex_worker.discord_bot.delivery import (
    ChannelMap,
    MemoryTransport,
    OutboxDelivery,
    TransportError,
    TransportRateLimited,
    nonce_for,
)
from apex_worker.discord_bot.limits import embed_length, message_within_limits
from apex_worker.discord_bot.samples import sample_assessment, sample_signal
from apex_worker.notifications import intents as intent_builders
from apex_worker.notifications.render import render
from apex_worker.storage.local import LocalStore
from apex_worker.timeutil import iso_utc

SECRET = b"test-component-secret-0123456789abcdef"
NOW = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)
HC, EXIT, SYS = 900000000000000101, 900000000000000102, 900000000000000106
CHANNELS = ChannelMap({"high_conviction_alerts": HC, "exit_alerts": EXIT, "market_intelligence": 900000000000000104,
                       "system_status": SYS}, "test")
A, B, C = "100000000000000001", "100000000000000002", "100000000000000003"


@pytest.fixture
def env(tmp_path):
    path = tmp_path / "apex.sqlite3"
    store = LocalStore(path)
    members = LocalMemberBackend(store)
    positions = LocalPositionBackend(store, is_demo=True)
    yield {"store": store, "members": members, "positions": positions, "path": path}
    store.close()


def loop(env, transport, **kw) -> OutboxDelivery:
    return OutboxDelivery(env["store"], transport, SECRET, kw.pop("channels", CHANNELS), env["members"],
                          clock=lambda: NOW, **kw)


def new_signal_intent(store, tier="HIGH_CONVICTION", symbol="DMSEMI1", seed="a", long_text=False):
    sig = sample_signal(NOW, symbol=symbol, seed=seed, long_text=long_text)
    store.save_signal_revision(sig)
    intent = intent_builders.new_signal(sig, tier, NOW)
    store.enqueue(intent, NOW)
    return sig, intent


def dm_intent(env, discord_id, *, opt_in=True, symbol="DMSEMI1", version=1):
    store = env["store"]
    uid = store.ensure_member(discord_id, NOW)
    store.set_dm_opt_in(uid, opt_in)
    pid = "11111111-1111-4111-8111-" + discord_id[-12:]
    intent = intent_builders.position_update(uid, discord_id, sample_assessment(pid, symbol, NOW, version=version), NOW,
                                             True)
    store.enqueue(intent, NOW)
    return uid, intent


def states(store):
    return {r.kind + ":" + r.event_key[-6:]: r.state for r in store.outbox_rows()}


async def test_channel_alert_delivered_once_and_never_resent_after_restart(env):
    store = env["store"]
    _sig, _intent = new_signal_intent(store)
    t = MemoryTransport()
    counts = await loop(env, t).deliver_due(NOW)
    assert counts["delivered"] == 1 and len(t.sent) == 1
    sent = t.sent[0]
    assert sent["target"] == f"channel:{HC}" and message_within_limits(sent["message"])
    assert sent["message"]["allowed_mentions"] == {"parse": []}
    row = store.outbox_rows()[0]
    assert row.state == "sent" and sent["nonce"] == nonce_for(row.id) and len(sent["nonce"]) <= 25
    assert "Record My Entry" in str(sent["message"]["components"])
    # "restart": brand-new store connection and loop instance on the same SQLite file
    store.close()
    store2 = LocalStore(env["path"])
    env2 = {**env, "store": store2, "members": LocalMemberBackend(store2)}
    t2 = MemoryTransport()
    for minutes in (0, 5, 60 * 24):
        assert (await loop(env2, t2).deliver_due(NOW + timedelta(minutes=minutes)))["delivered"] == 0
    assert t2.sent == [] and t2.calls == 0
    env["store"] = store2  # fixture closes it


async def test_crash_between_send_and_ack_is_deduplicated_by_nonce(env, monkeypatch):
    store = env["store"]
    new_signal_intent(store)
    t = MemoryTransport()
    real_ack = store.ack_outbox
    calls = {"n": 0}

    def crashing_ack(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("power loss before ack")
        return real_ack(*a, **kw)

    monkeypatch.setattr(store, "ack_outbox", crashing_ack)
    with pytest.raises(RuntimeError):
        await loop(env, t).deliver_due(NOW)
    assert store.outbox_rows()[0].state == "pending"
    await loop(env, t).deliver_due(NOW)
    assert len(t.sent) == 1 and t.calls == 2  # second send reused the nonce -> same Discord message
    assert store.outbox_rows()[0].state == "sent"


async def test_dm_blocked_or_not_opted_in_is_deferred_and_never_posted_to_a_channel(env):
    store = env["store"]
    dm_intent(env, A, opt_in=True)
    dm_intent(env, B, opt_in=False)
    uid_c, _ = dm_intent(env, C, opt_in=True, symbol="DMMUTE")
    env["members"].mute(uid_c, "DMMUTE", NOW)
    t = MemoryTransport(dm_forbidden={A})
    counts = await loop(env, t).deliver_due(NOW)
    assert counts["deferred"] == 3 and counts["delivered"] == 0
    assert t.sent == []  # nothing anywhere: no DM, and above all no channel fallback
    assert t.calls == 1  # only A was attempted (B not opted in, C muted)
    assert {r.state for r in store.outbox_rows()} == {"deferred"}
    assert all(not r["target"].startswith("channel:") for r in t.sent)
    # deferred items are not retried by the loop; they wait for the member's next ephemeral reply
    later = await loop(env, MemoryTransport()).deliver_due(NOW + timedelta(days=30))
    assert later["delivered"] == 0
    held = env["members"].pending_private(store.member_id(A))
    assert len(held) == 1


async def test_opted_in_dm_is_delivered_privately(env):
    dm_intent(env, A, opt_in=True)
    t = MemoryTransport()
    assert (await loop(env, t).deliver_due(NOW))["delivered"] == 1
    assert t.sent[0]["target"] == f"dm:{A}"
    assert "Update My Position" in str(t.sent[0]["message"]["components"])
    assert message_within_limits(t.sent[0]["message"])


async def test_member_who_left_is_revoked_and_dm_held(env):
    store = env["store"]
    dm_intent(env, A, opt_in=True)
    t = MemoryTransport(members={B})  # A is no longer in the guild
    assert (await loop(env, t).deliver_due(NOW))["deferred"] == 1
    assert t.sent == [] and store.member_id(A) is None
    assert env["members"].status(A) == "revoked"


async def test_429_uses_retry_after_and_pauses_the_whole_loop(env):
    store = env["store"]
    new_signal_intent(store, seed="one")
    new_signal_intent(store, seed="two", symbol="DMTWO")
    t = MemoryTransport(script=[TransportRateLimited(7.5)])
    d = loop(env, t)
    counts = await d.deliver_due(NOW)
    assert counts["rate_limited"] == 1 and counts["delivered"] == 0 and t.calls == 1  # second row not attempted
    attempt = store.query("SELECT status, http_status, rate_limit_until FROM delivery_attempts")[0]
    assert attempt["http_status"] == 429 and attempt["rate_limit_until"] == iso_utc(NOW + timedelta(seconds=7.5))
    assert d.stats.rate_limited_until == iso_utc(NOW + timedelta(seconds=7.5))
    assert (await d.deliver_due(NOW + timedelta(seconds=3)))["delivered"] == 0 and t.calls == 1  # still paused
    counts = await d.deliver_due(NOW + timedelta(seconds=8))
    assert counts["delivered"] == 2 and len(t.sent) == 2
    assert {r.state for r in store.outbox_rows()} == {"sent"}


async def test_5xx_backoff_then_dead_letter(env):
    store = env["store"]
    new_signal_intent(store)
    t = MemoryTransport(script=[TransportError(503, "unavailable") for _ in range(20)])
    d = loop(env, t, max_attempts=4)
    when = NOW
    outcomes = []
    for _ in range(10):
        c = await d.deliver_due(when)
        outcomes.append(c)
        when += timedelta(minutes=20)
    row = store.outbox_rows()[0]
    assert row.state == "dead_letter" and row.attempts == 4 and t.sent == []
    assert sum(c["dead_letter"] for c in outcomes) == 1
    nexts = [r["http_status"] for r in store.query("SELECT http_status FROM delivery_attempts")]
    assert nexts == [503, 503, 503, 503]


async def test_backoff_is_respected_between_attempts(env):
    store = env["store"]
    new_signal_intent(store)
    t = MemoryTransport(script=[TransportError(500, "boom")])
    d = loop(env, t)
    await d.deliver_due(NOW)
    assert (await d.deliver_due(NOW + timedelta(seconds=1)))["delivered"] == 0  # backoff 2 s after attempt 1
    assert (await d.deliver_due(NOW + timedelta(seconds=3)))["delivered"] == 1


async def test_unmapped_channel_stays_queued_and_private_kind_on_channel_is_dead_lettered(env):
    store = env["store"]
    new_signal_intent(store, tier="RESEARCH_SETUP")  # market_intelligence
    t = MemoryTransport()
    only_hc = ChannelMap({"high_conviction_alerts": HC}, "test")
    c = await loop(env, t, channels=only_hc).deliver_due(NOW)
    assert c["failed"] == 1 and t.sent == []
    row = store.outbox_rows()[0]
    assert row.state == "pending"
    assert "CHANNEL_NOT_CONFIGURED" in store.query("SELECT last_error FROM notification_outbox")[0][0]
    # misrouted personal payload on a channel route is never sent
    uid = store.ensure_member(A, NOW)
    bad = intent_builders.position_update(uid, A, sample_assessment("22222222-2222-4222-8222-222222222222", "DMX", NOW),
                                          NOW, True)
    bad["event_key"] = "POS:misrouted:test"
    bad["route"] = {"type": "CHANNEL", "channel_key": "high_conviction_alerts"}
    store.enqueue(bad, NOW)
    await loop(env, t).deliver_due(NOW)
    assert t.sent == []
    assert [r.state for r in store.outbox_rows() if r.kind == "POSITION_UPDATE"] == ["dead_letter"]


async def test_high_priority_mentions_only_opted_in_members(env):
    store, members = env["store"], env["members"]
    for duid, mention, mute in ((A, True, False), (B, True, True), (C, False, False)):
        uid = store.ensure_member(duid, NOW)
        members.set_high_priority_mentions(uid, mention, NOW)
        if mute:
            members.mute(uid, "DMSEMI1", NOW)
    revoked = "100000000000000004"
    members.set_high_priority_mentions(store.ensure_member(revoked, NOW), True, NOW)
    store.revoke_member(revoked, NOW)
    new_signal_intent(store, tier="HIGH_CONVICTION")
    new_signal_intent(store, tier="RESEARCH_SETUP", seed="b", symbol="DMNORM")
    t = MemoryTransport()
    await loop(env, t).deliver_due(NOW)
    hc = next(s for s in t.sent if s["target"] == f"channel:{HC}")["message"]
    assert f"<@{A}>" in hc["content"] and f"<@{B}>" not in hc["content"] and f"<@{C}>" not in hc["content"]
    assert f"<@{revoked}>" not in hc["content"]
    assert hc["allowed_mentions"] == {"parse": [], "users": [A]}
    assert "@everyone" not in hc["content"] and "@here" not in hc["content"]
    normal = next(s for s in t.sent if s["target"] != f"channel:{HC}")["message"]
    assert normal["allowed_mentions"] == {"parse": []} and "<@" not in normal["content"]


async def test_signal_closed_goes_to_exit_alerts_without_member_data(env):
    store = env["store"]
    sig, _ = new_signal_intent(store)
    uid = store.ensure_member(A, NOW)
    store.set_dm_opt_in(uid, True)
    ev = {"signal_id": sig["id"], "event_type": "STOP_HIT", "event_at": iso_utc(NOW), "from_state": "ACTIVE",
          "to_state": "CLOSED", "dedupe_key": f"{sig['id']}:stop", "cause": "close below stop",
          "payload": {"return_net_modeled": -0.04, "r_multiple_modeled": -1.0, "sessions_held": 3}}
    store.enqueue(intent_builders.signal_event(sig, ev, NOW), NOW)
    t = MemoryTransport()
    await loop(env, t).deliver_due(NOW)
    exit_msg = next(s for s in t.sent if s["target"] == f"channel:{EXIT}")["message"]
    assert "your own position is tracked privately" in str(exit_msg)
    assert message_within_limits(exit_msg)


def test_rendered_payloads_respect_discord_limits(env):
    sig = sample_signal(NOW, long_text=True)
    for tier in ("HIGH_CONVICTION", "RESEARCH_SETUP"):
        msg = render(intent_builders.new_signal(sig, tier, NOW), SECRET).to_discord()
        assert len(msg["content"]) <= 2000 and sum(embed_length(e) for e in msg["embeds"]) <= 6000
        assert message_within_limits(msg)
        assert all(len(b["custom_id"]) <= 100 for row in msg["components"] for b in row["components"])
    uid = env["store"].ensure_member(A, NOW)
    pos = render(intent_builders.position_update(uid, A, sample_assessment("33333333-3333-4333-8333-333333333333",
                                                                           "DMSEMI1", NOW), NOW, True), SECRET)
    assert message_within_limits(pos.to_discord())


async def test_run_loop_stops_cleanly(env):
    import asyncio

    new_signal_intent(env["store"])
    t = MemoryTransport()
    d = loop(env, t)
    stop = asyncio.Event()
    task = asyncio.create_task(d.run(stop, interval=0.01))
    for _ in range(100):
        if t.sent:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, 2)
    assert len(t.sent) == 1 and d.stats.delivered == 1 and d.stats.delivery_last_run_at
