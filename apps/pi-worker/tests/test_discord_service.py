"""BotService (pure Python) tests: gating, identity, validation, idempotency, ownership, privacy, limits."""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime, timedelta

import pytest

from apex_worker.discord_bot.backend import (
    SPOOL_KIND,
    BackendUnavailable,
    LocalMemberBackend,
    LocalPositionBackend,
    replay_spool,
)
from apex_worker.discord_bot.delivery import ChannelMap, MemoryTransport, OutboxDelivery
from apex_worker.discord_bot.limits import message_within_limits
from apex_worker.discord_bot.samples import sample_assessment, sample_signal
from apex_worker.discord_bot.service import Actor, BotService
from apex_worker.notifications import components
from apex_worker.notifications import intents as intent_builders
from apex_worker.storage.local import LocalStore
from apex_worker.timeutil import iso_utc

GUILD = "900000000000000001"
OTHER_GUILD = "900000000000000099"
A, B, C, OWNER = "100000000000000001", "100000000000000002", "100000000000000003", "100000000000000009"
SECRET = b"test-component-secret-0123456789abcdef"
NOW = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)
_ids = itertools.count(1_300_000_000_000_000_001)


class Clock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t


def actor(user: str, *, guild: str | None = GUILD, iid: str | None = None) -> Actor:
    return Actor(discord_user_id=user, interaction_id=iid or str(next(_ids)), guild_id=guild)


def publish(store: LocalStore, sig: dict, at: datetime = NOW) -> dict:
    store.save_signal_revision(sig)
    store.append_signal_event({"signal_id": sig["id"], "event_type": "PUBLISHED", "event_at": iso_utc(at),
                               "from_state": None, "to_state": "PUBLISHED", "dedupe_key": f"{sig['id']}:pub"})
    return sig


def set_state(store: LocalStore, sig: dict, state: str) -> None:
    store.append_signal_event({"signal_id": sig["id"], "event_type": state, "event_at": iso_utc(NOW),
                               "from_state": "PUBLISHED", "to_state": state, "dedupe_key": f"{sig['id']}:{state}"})


@pytest.fixture
def env(tmp_path):
    store = LocalStore(tmp_path / "apex.sqlite3")
    clock = Clock(NOW)
    positions = LocalPositionBackend(store, is_demo=True)
    members = LocalMemberBackend(store)
    svc = BotService(positions=positions, members=members, signals=store, spool=store, component_secret=SECRET,
                     guild_id=GUILD, owner_ids=[OWNER], release_mode="DEMO", clock=clock)
    sig = publish(store, sample_signal(NOW - timedelta(minutes=30)))
    yield {"store": store, "svc": svc, "clock": clock, "sig": sig, "positions": positions, "members": members}
    store.close()


def text_of(reply) -> str:
    return json.dumps(reply.to_message(), sort_keys=True)


def entry_modal_submit(svc, sig, user, iid=None, **values):
    vals = {"price": "101.00", "quantity": "20", "fill_time": "", "fees": "", "notes": ""}
    vals.update(values)
    cid = components.make(SECRET, "entry", sig["id"])
    return svc.modal_submit(actor(user, iid=iid), cid, vals)


# ---------------------------------------------------------------- gating, identity, revocation
def test_cross_guild_interactions_are_refused_and_nothing_is_registered(env):
    svc, store = env["svc"], env["store"]
    r = svc.entry(actor(A, guild=OTHER_GUILD), ticker="NVDA", price=181.25, shares=20)
    assert r.code == "REFUSED_GUILD" and not r.ok
    assert "only serves its own private server" in r.content
    assert svc.gate(actor(A, guild=OTHER_GUILD)).code == "REFUSED_GUILD"
    assert store.member_id(A) is None
    r = svc.component(actor(A, guild=OTHER_GUILD), components.make(SECRET, "watch", env["sig"]["id"]))
    assert r.code == "REFUSED_GUILD"


def test_guild_commands_in_dms_are_refused_politely(env):
    r = env["svc"].positions_view(actor(A, guild=None))
    assert r.code == "REFUSED_DM"
    assert env["svc"].gate(actor(A, guild=None)).code == "REFUSED_DM"
    # an unknown user cannot use even DM-capable buttons
    cid = components.make(SECRET, "posupd", "00000000-0000-4000-8000-000000000000")
    assert env["svc"].component(actor(B, guild=None), cid).code == "REFUSED_DM"


def test_revoked_member_is_refused_on_every_command_and_button(env):
    svc, store = env["svc"], env["store"]
    assert svc.entry(actor(A), ticker="NVDA", price=10, shares=1).code == "PERSISTED"
    svc.member_left(A)
    calls = [
        lambda: svc.help(actor(A)), lambda: svc.health(actor(A)), lambda: svc.positions_view(actor(A)),
        lambda: svc.entry(actor(A), ticker="NVDA", price=10, shares=1), lambda: svc.export(actor(A)),
        lambda: svc.notifications(actor(A), dms=True), lambda: svc.delete_my_data(actor(A)),
        lambda: svc.component(actor(A), components.make(SECRET, "watch", env["sig"]["id"])),
        lambda: entry_modal_submit(svc, env["sig"], A),
    ]
    for call in calls:
        assert call().code == "REVOKED"
    assert svc.gate(actor(A)).code == "REVOKED"
    assert svc.position_choices(actor(A), "") == []
    # owner can restore
    assert svc.admin_restore(actor(OWNER), target_discord_id=A).code == "RESTORED"
    assert svc.positions_view(actor(A)).code == "POSITIONS"
    assert store.member_id(A)


def test_identity_comes_from_the_actor_not_from_options(env):
    svc = env["svc"]
    svc.entry(actor(A), ticker="NVDA", price=180, shares=20)
    mine = env["positions"].positions(A)
    assert len(mine) == 1 and env["positions"].positions(B) == []


# ---------------------------------------------------------------- components: tampering, unknown/expired signals
def test_tampered_and_foreign_component_ids_are_rejected(env):
    svc, sig = env["svc"], env["sig"]
    good = components.make(SECRET, "entry", sig["id"])
    tampered_subject = good.replace(sig["id"], "sig_" + "0" * 24)
    tampered_mac = good[:-1] + ("0" if good[-1] != "0" else "1")
    foreign = components.make(b"another-secret-entirely-xxxxxxxxxx", "entry", sig["id"])
    for cid in (tampered_subject, tampered_mac, foreign, "apex:v1:entry:../../x:0000000000000000", "garbage", ""):
        r = svc.component(actor(A), cid)
        assert r.code == "INVALID_COMPONENT" and r.modal is None
        assert svc.modal_submit(actor(A), cid, {"price": "1", "quantity": "1"}).code == "INVALID_COMPONENT"
    assert env["positions"].positions(A) == []


def test_entry_button_opens_modal_bound_to_the_verified_signal(env):
    r = env["svc"].component(actor(A), components.make(SECRET, "entry", env["sig"]["id"]))
    assert r.code == "MODAL" and r.modal is not None
    assert components.parse(SECRET, r.modal.custom_id) == ("entry", env["sig"]["id"])
    d = r.modal.to_dict()
    assert len(d["components"]) == 5 and len(d["title"]) <= 45
    assert [c["components"][0]["custom_id"] for c in d["components"]] == ["price", "quantity", "fill_time", "fees", "notes"]
    assert all(len(c["components"][0]["label"]) <= 45 for c in d["components"])
    assert env["svc"].component_kind(r.modal.custom_id) == "modal"


def test_unknown_and_expired_signals_are_rejected(env):
    svc, store = env["svc"], env["store"]
    unknown = components.make(SECRET, "entry", "sig_" + "a" * 24)
    assert svc.component(actor(A), unknown).code == "UNKNOWN_SIGNAL"
    assert svc.modal_submit(actor(A), unknown, {"price": "1", "quantity": "1"}).code == "UNKNOWN_SIGNAL"
    assert svc.component(actor(A), components.make(SECRET, "watch", "sig_" + "a" * 24)).code == "UNKNOWN_SIGNAL"
    assert svc.component(actor(A), components.make(SECRET, "evidence", "sig_" + "a" * 24)).code == "UNKNOWN_SIGNAL"
    old = publish(store, sample_signal(NOW - timedelta(days=3), symbol="DMOLD1", seed="old"))
    set_state(store, old, "EXPIRED")
    r = svc.component(actor(A), components.make(SECRET, "entry", old["id"]))
    assert r.code == "SIGNAL_NOT_OPEN" and r.modal is None and "EXPIRED" in r.content
    assert svc.component(actor(A), components.make(SECRET, "watch", old["id"])).code == "SIGNAL_NOT_OPEN"
    assert env["positions"].positions(A) == []


# ---------------------------------------------------------------- modal entry and confirmation contents
def test_modal_entry_confirmation_is_private_and_complete(env):
    r = entry_modal_submit(env["svc"], env["sig"], A, price="101.25", quantity="20", fees="1")
    assert r.code == "PERSISTED", r.content
    msg = r.to_message()
    assert msg["flags"] == 64 and msg["allowed_mentions"] == {"parse": []}
    blob = text_of(r)
    for needle in ("BUY 20 DMSEMI1", "$101.25", "Estimated notional", "$2,025.00", "Average cost", "96.50", "110.00",
                   "stop / invalidation", "Market data", "verify at your broker", "Self-reported - not broker proof",
                   "Private: only you can see this", "[DEMO]"):
        assert needle.lower() in blob.lower(), needle
    assert message_within_limits(msg)
    pos = env["positions"].positions(A)[0]
    assert pos["signal_id"] == env["sig"]["id"] and pos["total_quantity"] == "20"


@pytest.mark.parametrize(("values", "code"), [
    ({"price": "abc"}, "INVALID_NUMBER"), ({"price": "-1"}, "NON_POSITIVE"), ({"price": "0"}, "NON_POSITIVE"),
    ({"price": "nan"}, "INVALID_NUMBER"), ({"price": "Infinity"}, "INVALID_NUMBER"), ({"price": "1.23456"}, "TOO_PRECISE"),
    ({"price": "1,000"}, "INVALID_NUMBER"), ({"price": "2000000"}, "TOO_LARGE"), ({"quantity": ""}, "MISSING_VALUE"),
    ({"quantity": "0.0000001"}, "TOO_PRECISE"), ({"fees": "-2"}, "NON_POSITIVE"),
    ({"fill_time": "2026-10-21 10:00"}, "FUTURE_FILL"), ({"fill_time": "2026-10-20"}, "INVALID_TIME"),
    ({"fill_time": "yesterday"}, "INVALID_TIME"), ({"fill_time": "25:61"}, "INVALID_TIME"),
    ({"notes": "x" * 281}, "NOTES_TOO_LONG"),
])
def test_modal_validation_errors_are_friendly_and_save_nothing(env, values, code):
    r = entry_modal_submit(env["svc"], env["sig"], A, **values)
    assert r.code == code and not r.ok
    assert r.content and "Traceback" not in r.content
    assert env["positions"].positions(A) == []


def test_fill_time_formats(env):
    svc = env["svc"]
    r = svc.entry(actor(A), ticker="AAA", price=10, shares=1, fill_time="10:30")  # 10:30 New York = 14:30 UTC (EDT)
    assert r.code == "PERSISTED"
    assert env["positions"].positions(A)[0]["lots"][0]["fill_at"] == "2026-10-20T14:30:00Z"
    r = svc.entry(actor(A), ticker="BBB", price=10, shares=1, fill_time="2026-10-19T19:59:00Z")
    assert r.code == "PERSISTED"
    lots = {p["symbol"]: p["lots"][0]["fill_at"] for p in env["positions"].positions(A)}
    assert lots["BBB"] == "2026-10-19T19:59:00Z"


def test_slash_floats_are_converted_through_str_and_validated(env):
    svc = env["svc"]
    assert svc.entry(actor(A), ticker=" nvda ", price=181.25, shares=0.5).code == "PERSISTED"
    p = env["positions"].positions(A)[0]
    assert (p["symbol"], p["lots"][0]["fill_price"], p["total_quantity"]) == ("NVDA", "181.25", "0.5")
    assert svc.entry(actor(A), ticker="NVDA", price=float("nan"), shares=1).code == "INVALID_NUMBER"
    assert svc.entry(actor(A), ticker="NVDA", price=float("inf"), shares=1).code == "INVALID_NUMBER"
    assert svc.entry(actor(A), ticker="NVDA", price=1e-07, shares=1).code == "TOO_PRECISE"
    assert svc.entry(actor(A), ticker="NVDA", price=0.1 + 0.2, shares=1).code == "TOO_PRECISE"
    assert svc.entry(actor(A), ticker="NVDA", price=-5.0, shares=1).code == "NON_POSITIVE"
    assert svc.entry(actor(A), ticker="NVDA", price=10.0, shares=0.0).code == "NON_POSITIVE"
    for bad in ("1ABC", "TOOLONGTICKER", "NV DA", "", "ab$"):
        assert svc.entry(actor(A), ticker=bad, price=10, shares=1).code == "INVALID_SYMBOL"
    assert len(env["positions"].positions(A)) == 1


def test_starter_style_signal_refs_become_notes(env):
    svc = env["svc"]
    r = svc.entry(actor(A), ticker="NVDA", price=181.25, shares=20, signal_id="DEMO-001")
    assert r.code == "PERSISTED"
    p = env["positions"].positions(A)[0]
    assert p["signal_id"] is None
    ev = env["positions"].events(A, p["position_id"])[0]
    assert "DEMO-001" in ev["notes"]
    assert svc.entry(actor(A), ticker="NVDA", price=1, shares=1, signal_id="sig_" + "f" * 24).code == "UNKNOWN_SIGNAL"
    assert svc.entry(actor(A), ticker="NVDA", price=1, shares=1, signal_id="sig_bad").code == "UNKNOWN_SIGNAL"
    assert svc.entry(actor(A), ticker="DMXX", price=1, shares=1, signal_id=env["sig"]["id"]).code == "SYMBOL_MISMATCH"


# ---------------------------------------------------------------- idempotency
def test_double_submit_of_the_same_interaction_never_creates_a_second_fill(env):
    svc, sig = env["svc"], env["sig"]
    iid = str(next(_ids))
    first = entry_modal_submit(svc, sig, A, iid=iid)
    again = entry_modal_submit(svc, sig, A, iid=iid)
    assert first.code == "PERSISTED" and again.code == "DUPLICATE"
    assert "no second fill" in text_of(again)
    pos = env["positions"].positions(A)
    assert len(pos) == 1 and len(pos[0]["lots"]) == 1 and pos[0]["total_quantity"] == "20"
    # slash command resend (same interaction id) is also deduplicated
    iid2 = str(next(_ids))
    assert svc.entry(actor(B, iid=iid2), ticker="NVDA", price=10, shares=2).code == "PERSISTED"
    assert svc.entry(actor(B, iid=iid2), ticker="NVDA", price=10, shares=2).code == "DUPLICATE"
    assert len(env["positions"].positions(B)) == 1
    key_rows = env["store"].query("SELECT client_idempotency_key FROM position_events")
    assert {r["client_idempotency_key"] for r in key_rows} == {f"discord:{iid}", f"discord:{iid2}"}


def test_second_entry_on_same_signal_is_an_add_and_watch_converts_to_open(env):
    svc, sig = env["svc"], env["sig"]
    assert svc.component(actor(C), components.make(SECRET, "watch", sig["id"])).code == "PERSISTED"
    assert svc.component(actor(C), components.make(SECRET, "watch", sig["id"])).code == "ALREADY_WATCHING"
    r = entry_modal_submit(svc, sig, C, price="100.50", quantity="10")
    assert r.code == "PERSISTED" and "now an open position" in text_of(r)
    r = entry_modal_submit(svc, sig, C, price="102", quantity="10")
    assert "additional buy (ADD)" in text_of(r)
    pos = env["positions"].positions(C)
    assert len(pos) == 1 and pos[0]["total_quantity"] == "20" and len(pos[0]["lots"]) == 2
    assert pos[0]["avg_cost"] == "101.25"


# ---------------------------------------------------------------- ownership
def test_member_b_cannot_touch_member_a_positions(env):
    svc, sig = env["svc"], env["sig"]
    entry_modal_submit(svc, sig, A, price="101", quantity="20")
    a_pos = env["positions"].positions(A)[0]
    pid = a_pos["position_id"]
    fill_id = a_pos["lots"][0]["lot_id"]
    entry_modal_submit(svc, sig, B, price="101", quantity="5")
    attempts = [
        svc.add(actor(B), position=pid, price=100, shares=1),
        svc.reduce(actor(B), position=pid, price=100, shares=1),
        svc.close(actor(B), position=pid, price=100),
        svc.correct(actor(B), position=pid, fill=fill_id, action="void"),
        svc.remove(actor(B), position_id=pid),
        svc.positions_view(actor(B), position=pid),
        svc.positions_view(actor(B), position=pid[:8]),
        svc.component(actor(B), components.make(SECRET, "posupd", pid)),
        svc.component(actor(B), components.make(SECRET, "posupd", f"add.{pid}")),
        svc.modal_submit(actor(B), components.make(SECRET, "posupd", f"close.{pid}"), {"price": "1"}),
    ]
    for r in attempts:
        assert r.code == "POSITION_NOT_FOUND", r.code
        assert "20" not in (r.content or "")
    assert env["positions"].positions(A)[0] == a_pos
    assert all(v != pid for _, v in svc.position_choices(actor(B), ""))
    assert svc.fill_choices(actor(B), pid, "") == []
    assert {v for _, v in svc.position_choices(actor(A), "")} == {pid}


def test_posupd_flow_for_own_position(env):
    svc, sig = env["svc"], env["sig"]
    entry_modal_submit(svc, sig, A, price="101", quantity="20")
    pid = env["positions"].positions(A)[0]["position_id"]
    menu = svc.component(actor(A, guild=None), components.make(SECRET, "posupd", pid))  # from a DM
    assert menu.code == "POSUPD_MENU"
    labels = [b.label for b in menu.rows[0]]
    assert labels == ["Add shares", "Sell some", "Close all", "Details"]
    reduce_id = menu.rows[0][1].custom_id
    assert svc.component_kind(reduce_id) == "modal"
    modal = svc.component(actor(A, guild=None), reduce_id)
    assert modal.code == "MODAL"
    r = svc.modal_submit(actor(A, guild=None), modal.modal.custom_id, {"price": "105", "quantity": "5"})
    assert r.code == "PERSISTED" and "SELL 5" in text_of(r)
    close_modal = svc.component(actor(A), menu.rows[0][2].custom_id)
    assert next(c["components"][0]["custom_id"] for c in close_modal.modal.to_dict()["components"]) == "price"
    r = svc.modal_submit(actor(A), close_modal.modal.custom_id, {"price": "106"})
    assert r.code == "PERSISTED"
    p = env["positions"].positions(A, include_closed=True)[0]
    assert p["state"] == "CLOSED" and p["realized_pnl"] == "95"
    # entry/watch buttons are guild-only
    assert svc.component(actor(A, guild=None), components.make(SECRET, "watch", sig["id"])).code == "REFUSED_DM"


def test_reduce_close_and_oversell_messages(env):
    svc = env["svc"]
    svc.entry(actor(A), ticker="NVDA", price=180, shares=20)
    pid = env["positions"].positions(A)[0]["position_id"]
    r = svc.reduce(actor(A), position="NVDA", price=190, shares=25)
    assert r.code == "OVERSELL" and "cannot sell more" in r.content.lower()
    assert svc.reduce(actor(A), position=pid[:8], price=190, shares=5).code == "PERSISTED"
    r = svc.close(actor(A), position=pid, price=200)
    assert r.code == "PERSISTED" and "Realized P&L" in text_of(r)
    assert svc.close(actor(A), position=pid, price=200).code == "NOT_OPEN"
    assert svc.add(actor(A), position="ZZZZ", price=1, shares=1).code == "POSITION_NOT_FOUND"


# ---------------------------------------------------------------- confirmations: correct / remove / delete
def test_correct_requires_confirmation_and_confirm_is_single_use(env):
    svc = env["svc"]
    svc.entry(actor(A), ticker="NVDA", price=180, shares=20)
    svc.add(actor(A), position="NVDA", price=170, shares=10)
    pos = env["positions"].positions(A)[0]
    add_lot = pos["lots"][1]["lot_id"]
    prompt = svc.correct(actor(A), position=pos["position_id"], fill=add_lot[:8], action="replace", price=171)
    assert prompt.code == "CONFIRM_REQUIRED"
    assert env["positions"].positions(A)[0] == pos  # nothing changed yet
    confirm, _cancel = prompt.rows[0]
    assert svc.component_kind(confirm.custom_id) == "update"
    assert svc.component(actor(B), confirm.custom_id).code == "CONFIRM_EXPIRED"  # B cannot use A's button
    done = svc.component(actor(A), confirm.custom_id)
    assert done.code == "PERSISTED" and done.replace_original
    after = env["positions"].positions(A)[0]
    assert after["lots"][1]["fill_price"] == "171" and after["total_quantity"] == "30"
    assert svc.component(actor(A), confirm.custom_id).code == "CONFIRM_EXPIRED"
    events = env["positions"].events(A, pos["position_id"])
    assert [e["event_type"] for e in events] == ["OPEN", "ADD", "CORRECT"]
    # undo the correction
    undo = svc.correct(actor(A), position=pos["position_id"], action="undo")
    assert svc.component(actor(A), undo.rows[0][0].custom_id).code == "PERSISTED"
    assert env["positions"].positions(A)[0]["lots"][1]["fill_price"] == "170"
    # cancel path changes nothing
    p2 = svc.correct(actor(A), position=pos["position_id"], fill=pos["lots"][0]["lot_id"], action="void")
    assert svc.component(actor(A), p2.rows[0][1].custom_id).code == "CANCELLED"
    assert svc.component(actor(A), p2.rows[0][0].custom_id).code == "CONFIRM_EXPIRED"
    assert len(env["positions"].events(A, pos["position_id"])) == 4


def test_confirmation_expires(env):
    svc, clock = env["svc"], env["clock"]
    svc.entry(actor(A), ticker="NVDA", price=180, shares=20)
    prompt = svc.remove(actor(A), position_id="NVDA")
    clock.t = NOW + timedelta(minutes=11)
    assert svc.component(actor(A), prompt.rows[0][0].custom_id).code == "CONFIRM_EXPIRED"
    assert env["positions"].positions(A)[0]["state"] == "OPEN"


def test_remove_voids_single_entry_and_refuses_multi_fill(env):
    svc = env["svc"]
    svc.entry(actor(A), ticker="NVDA", price=180, shares=20)
    prompt = svc.remove(actor(A), position_id="NVDA")
    assert prompt.code == "CONFIRM_REQUIRED"
    assert svc.component(actor(A), prompt.rows[0][0].custom_id).code == "PERSISTED"
    assert env["positions"].positions(A) == []
    closed = env["positions"].positions(A, include_closed=True)[0]
    assert closed["state"] == "CLOSED" and closed["total_quantity"] == "0"
    svc.entry(actor(A), ticker="AMD", price=100, shares=2)
    svc.add(actor(A), position="AMD", price=101, shares=2)
    assert svc.remove(actor(A), position_id="AMD").code == "NOT_REMOVABLE"


def test_delete_my_data_needs_two_confirmations(env):
    svc, sig = env["svc"], env["sig"]
    entry_modal_submit(svc, sig, A)
    entry_modal_submit(svc, sig, B)
    step1 = svc.delete_my_data(actor(A))
    assert step1.code == "CONFIRM_REQUIRED" and "Step 1 of 2" in text_of(step1)
    step2 = svc.component(actor(A), step1.rows[0][0].custom_id)
    assert step2.code == "CONFIRM_REQUIRED" and step2.replace_original and "step 2 of 2" in text_of(step2).lower()
    assert len(env["positions"].positions(A)) == 1  # still there after step 1
    done = svc.component(actor(A), step2.rows[0][0].custom_id)
    assert done.code == "DELETED"
    assert env["positions"].positions(A, include_closed=True) == []
    assert len(env["positions"].positions(B)) == 1  # other members untouched
    assert env["members"].prefs(env["store"].member_id(A))["dm_opt_in"] is False
    # cancel at step 1 keeps data
    s = svc.delete_my_data(actor(B))
    assert svc.component(actor(B), s.rows[0][1].custom_id).code == "CANCELLED"
    assert len(env["positions"].positions(B)) == 1


# ---------------------------------------------------------------- queued / pending sync
class DownBackend(LocalPositionBackend):
    def record(self, discord_user_id, event, now):
        raise BackendUnavailable("cloud unreachable")


def test_backend_unavailable_answers_queued_and_never_claims_saved(env, tmp_path):
    store = env["store"]
    down = DownBackend(store, is_demo=True)
    svc = BotService(positions=down, members=env["members"], signals=store, spool=store, component_secret=SECRET,
                     guild_id=GUILD, clock=env["clock"])
    iid = str(next(_ids))
    r = svc.entry(actor(A, iid=iid), ticker="NVDA", price=181.25, shares=20)
    assert r.code == "QUEUED" and "NOT saved yet" in r.content and "pending sync" in r.content.lower()
    assert "Saved privately" not in text_of(r)
    assert down.positions(A) == []
    assert store.spool_pending_count() == 1
    view = svc.positions_view(actor(A))
    assert "Pending sync (NOT saved yet)" in text_of(view)
    # the same interaction re-queued is a no-op
    svc.entry(actor(A, iid=iid), ticker="NVDA", price=181.25, shares=20)
    assert store.spool_pending_count() == 1
    # sync later through a working backend: exactly one fill, idempotent on replay
    counts = replay_spool(env["positions"], store, NOW)
    assert counts["acked"] == 1 and store.spool_pending_count() == 0
    assert len(env["positions"].positions(A)) == 1
    assert env["positions"].record(A, json.loads(store.query("SELECT payload FROM spool")[0]["payload"])["event"],
                                   NOW).status == "duplicate"
    # without a spool the bot says NOT saved
    nospool = BotService(positions=down, members=env["members"], signals=store, spool=None, component_secret=SECRET,
                         guild_id=GUILD, clock=env["clock"])
    r = nospool.entry(actor(B), ticker="NVDA", price=1, shares=1)
    assert r.code == "NOT_SAVED" and "Nothing was recorded" in r.content


def test_spool_rejections_are_reported_to_the_member(env):
    store = env["store"]
    bad = {"client_idempotency_key": "discord:1300000000000000777", "event_type": "ADD",
           "position_id": "00000000-0000-4000-8000-000000000000", "source": "discord",
           "fill": {"price": "1", "quantity": "1", "fees": "0", "fill_at": iso_utc(NOW)}}
    store.spool_put(SPOOL_KIND, bad["client_idempotency_key"], {"discord_user_id": A, "event": bad}, NOW)
    assert replay_spool(env["positions"], store, NOW)["rejected"] == 1
    assert "rejected on sync" in text_of(env["svc"].positions_view(actor(A)))


# ---------------------------------------------------------------- private notifications held for DM-blocked members
def test_deferred_private_updates_are_shown_in_next_ephemeral_reply_never_in_a_channel(env):
    svc, store, sig = env["svc"], env["store"], env["sig"]
    entry_modal_submit(svc, sig, B, price="101", quantity="5")
    b_uid = store.member_id(B)
    pid = env["positions"].positions(B)[0]["position_id"]
    store.enqueue(intent_builders.position_update(b_uid, B, sample_assessment(pid, sig["symbol"], NOW), NOW, True), NOW)
    transport = MemoryTransport()
    loop = OutboxDelivery(store, transport, SECRET, ChannelMap({"high_conviction_alerts": 900000000000000100}),
                          env["members"], clock=env["clock"])
    import asyncio

    counts = asyncio.run(loop.deliver_due(NOW))
    assert counts["deferred"] == 1 and transport.sent == [] and transport.calls == 0
    reply = svc.positions_view(actor(B))
    blob = text_of(reply)
    assert "could not be delivered by DM" in blob and "/apex_notifications dms:True" in blob
    assert "Your DMSEMI1 position" in blob and reply.private_outbox_ids
    assert reply.to_message()["flags"] == 64 and message_within_limits(reply.to_message())
    assert svc.after_reply(reply) is None
    row = next(r for r in store.outbox_rows() if r.kind == "POSITION_UPDATE")
    assert row.state == "sent"
    assert store.query("SELECT last_error FROM notification_outbox WHERE id=?", (row.id,))[0][0] == "SHOWN_EPHEMERAL"
    assert not svc.positions_view(actor(B)).private_outbox_ids
    # member A never sees B's private update
    assert "Your DMSEMI1 position" not in text_of(svc.positions_view(actor(A)))


def test_many_deferred_updates_fit_limits_and_late_arrivals_are_followed_up(env):
    svc, store, sig = env["svc"], env["store"], env["sig"]
    entry_modal_submit(svc, sig, B)
    b_uid = store.member_id(B)
    pid = env["positions"].positions(B)[0]["position_id"]
    for i in range(12):
        intent = intent_builders.position_update(b_uid, B, sample_assessment(pid, sig["symbol"], NOW + timedelta(minutes=i),
                                                                             version=i + 1), NOW, True)
        store.enqueue(intent, NOW + timedelta(seconds=i))
    for r in store.due_outbox(NOW + timedelta(hours=1), 50):
        store.ack_outbox(r.id, "dm_blocked", NOW)
    reply = svc.positions_view(actor(B))
    assert message_within_limits(reply.to_message())
    assert len(reply.private_outbox_ids) == 12 and "Older private updates" in text_of(reply)
    late = intent_builders.position_update(b_uid, B, sample_assessment(pid, sig["symbol"], NOW, version=99), NOW, True)
    store.enqueue(late, NOW)
    store.ack_outbox(store.due_outbox(NOW + timedelta(hours=1), 50)[0].id, "dm_blocked", NOW)
    extra = svc.after_reply(reply)
    assert extra is not None and "1 more private update" in extra.content
    assert all(r.state == "sent" for r in store.outbox_rows())


# ---------------------------------------------------------------- export, admin, notifications, evidence, limits
def test_export_inline_then_file_and_only_own_data(env):
    svc = env["svc"]
    svc.entry(actor(A), ticker="NVDA", price=180, shares=20)
    svc.entry(actor(B), ticker="SECRETB", price=55.55, shares=7)
    small = svc.export(actor(A))
    assert small.code == "EXPORT" and not small.files and "```json" in small.content
    assert len(small.to_message()["content"]) <= 2000 and "SECRETB" not in text_of(small)
    for i in range(15):
        svc.entry(actor(A), ticker=f"T{i}", price=10 + i, shares=1, notes="n" * 200)
    big = svc.export(actor(A))
    assert big.files and big.content and len(big.content) < 2000
    name, data = big.files[0]
    doc = json.loads(data)
    assert name.endswith(".json") and doc["discord_user_id"] == A and len(doc["positions"]) == 16
    assert "SECRETB" not in data.decode() and B not in data.decode()


def test_admin_status_is_owner_only_and_counts_only(env):
    svc = env["svc"]
    svc.entry(actor(A), ticker="NVDA", price=181.37, shares=23)
    assert svc.admin_status(actor(A)).code == "NOT_OWNER"
    r = svc.admin_status(actor(OWNER))
    assert r.code == "ADMIN_STATUS"
    blob = text_of(r)
    assert "OPEN 1" in blob and "181.37" not in blob and A not in blob and "NVDA" not in blob
    nobody = BotService(positions=env["positions"], members=env["members"], signals=env["store"],
                        component_secret=SECRET, guild_id=GUILD, clock=env["clock"])
    assert nobody.admin_status(actor(OWNER)).code == "NOT_OWNER"


def test_notifications_and_mute_button(env):
    svc, store, sig = env["svc"], env["store"], env["sig"]
    r = svc.notifications(actor(A), dms=True, high_priority_mentions=True, mute="dmxx")
    assert r.code == "NOTIFICATIONS" and r.test_dm and "DMXX" in r.content
    prefs = env["members"].prefs(store.member_id(A))
    assert prefs["dm_opt_in"] and prefs["high_priority_mentions"] and prefs["muted_symbols"] == ["DMXX"]
    r = svc.component(actor(A), components.make(SECRET, "mute", sig["symbol"]))
    assert r.code == "MUTED" and "Muted DMSEMI1" in r.content
    assert svc.component(actor(A), components.make(SECRET, "mute", sig["symbol"])).content.startswith("DMSEMI1 was")
    assert env["members"].mention_targets(sig["symbol"]) == []
    assert env["members"].mention_targets("NVDA") == [A]
    svc.notifications(actor(A), unmute=sig["symbol"], dms=False)
    assert env["members"].mention_targets(sig["symbol"]) == [A]
    assert svc.notifications(actor(A), mute="bad ticker").code == "INVALID_SYMBOL"


def test_evidence_and_signals_replies_are_private_and_bounded(env):
    svc, store = env["svc"], env["store"]
    for i in range(7):
        publish(store, sample_signal(NOW, symbol=f"DMLONG{i}", seed=f"s{i}", long_text=True))
    r = svc.component(actor(A), components.make(SECRET, "evidence", env["sig"]["id"]))
    assert r.code == "EVIDENCE"
    blob = text_of(r)
    assert "Why selected" in blob and "Counter-evidence" in blob and "UNVALIDATED" in blob and "[DEMO]" in blob
    assert message_within_limits(r.to_message())
    lst = svc.signals_list(actor(A))
    msg = lst.to_message()
    assert lst.code == "SIGNALS" and message_within_limits(msg) and len(msg["components"]) <= 5
    for row in msg["components"]:
        for b in row["components"]:
            assert components.parse(SECRET, b["custom_id"]) is not None


def test_positions_listing_and_detail_stay_within_limits(env):
    svc = env["svc"]
    for i in range(30):
        svc.entry(actor(A), ticker=f"S{i}", price=100 + i, shares=3, notes="private note")
    r = svc.positions_view(actor(A))
    assert r.code == "POSITIONS" and message_within_limits(r.to_message()) and "and 5 more" in text_of(r)
    pid = env["positions"].positions(A)[0]["position_id"]
    d = svc.positions_view(actor(A), position=pid)
    assert d.code == "POSITION_DETAIL" and message_within_limits(d.to_message())
    assert "History (refs for /apex_correct)" in text_of(d)


def test_help_health_and_autocomplete(env):
    svc = env["svc"]
    assert svc.help(actor(A)).code == "HELP" and "/apex_entry" in svc.help(actor(A)).content
    h = svc.health(actor(A))
    assert h.code == "HEALTH" and "online" in h.content and "DEMO" in h.content and "no APEX quotes" in h.content
    assert len(svc.health(actor(A)).to_message()["content"]) <= 2000
    svc.entry(actor(A), ticker="NVDA", price=10, shares=1)
    choices = svc.position_choices(actor(A), "nv")
    assert len(choices) == 1 and len(choices[0][0]) <= 100
    sc = svc.signal_choices(actor(A), "")
    assert sc and sc[0][1] == env["sig"]["id"]
    assert svc.signal_choices(actor(A, guild=OTHER_GUILD), "") == []


def test_every_backend_error_code_has_a_friendly_message():
    import re
    from pathlib import Path

    import apex_worker.portfolio.ledger as ledger_mod
    import apex_worker.services.positions as positions_mod
    from apex_worker.discord_bot.validation import FRIENDLY

    codes = set()
    for mod, exc in ((ledger_mod, "LedgerError"), (positions_mod, "ServiceError"), (positions_mod, "LedgerError")):
        codes |= set(re.findall(rf'{exc}\(\s*"([A-Z_]+)"', Path(mod.__file__).read_text()))
    assert {"OVERSELL", "FUTURE_FILL", "UNKNOWN_SIGNAL", "CLOSE_QUANTITY_MISMATCH"} <= codes
    assert codes - set(FRIENDLY) == set()


def test_payload_structure_snapshot(env):
    """Structural snapshot of what members see: alert fields/buttons and the private confirmation fields."""
    from apex_worker.notifications.render import render

    alert = render(intent_builders.new_signal(env["sig"], "HIGH_CONVICTION", NOW), SECRET).to_discord()
    assert [f["name"] for f in alert["embeds"][0]["fields"]] == [
        "Quality rank (NOT a probability)", "Calibrated probability", "Entry zone", "Stop / invalidation", "Target(s)",
        "Net reward/risk (after modeled costs)", "Horizon", "Entry valid until", "Decision as-of", "Quote",
        "Why selected", "Counter-evidence / risks", "Account eligibility"]
    assert [b["label"] for b in alert["components"][0]["components"]] == [
        "Record My Entry", "Watch", "View Evidence", "Mute Symbol"]
    assert alert["allowed_mentions"] == {"parse": []} and "flags" not in alert  # shared: not ephemeral, no pings
    r = entry_modal_submit(env["svc"], env["sig"], A).to_message()
    assert r["flags"] == 64 and r["allowed_mentions"] == {"parse": []} and r["content"] == ""
    assert [f["name"] for f in r["embeds"][0]["fields"]] == [
        "Estimated notional", "Fill time", "Your position", "Signal plan / invalidation", "Market data"]
    assert r["embeds"][0]["footer"]["text"].startswith("DEMO MODE")
