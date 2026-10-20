"""discord.py adapter tests without a network: command registration/permissions, views/modals, transport error
mapping, single-instance lock, legacy-overlap guard, token masking and the dry run."""

from __future__ import annotations

import io
import json
import logging
import subprocess
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import discord
import pytest

from apex_worker.discord_bot import app as bot_app
from apex_worker.discord_bot.backend import LocalMemberBackend, LocalPositionBackend
from apex_worker.discord_bot.delivery import TransportForbidden, TransportNotFound, TransportRateLimited
from apex_worker.discord_bot.gateway import (
    COMMAND_NAMES,
    INVITE_PERMISSIONS,
    DiscordPyTransport,
    build_modal,
    build_tree,
    build_view,
    make_intents,
    modal_values,
    reply_kwargs,
)
from apex_worker.discord_bot.service import BotService, Button, Reply
from apex_worker.discord_bot.settings import (
    AlreadyRunning,
    BotSettings,
    SecretMaskingFilter,
    acquire_instance_lock,
    legacy_service_active,
)
from apex_worker.storage.local import LocalStore

GUILD = 900000000000000001
SECRET = b"test-component-secret-0123456789abcdef"
REQUIRED = {"apex_help", "apex_health", "apex_signals", "apex_watch", "apex_unwatch", "apex_entry", "apex_positions",
            "apex_add", "apex_reduce", "apex_close", "apex_correct", "apex_remove", "apex_notifications", "apex_export",
            "apex_delete_my_data", "apex_admin_status"}
FAKE_TOKEN = "MTAwMDAwMDAwMDAwMDAwMDAx.GabcdE.abcdefghijklmnopqrstuvwxyz0123456789AB"


@pytest.fixture
def tree():
    store = LocalStore(":memory:")
    svc = BotService(positions=LocalPositionBackend(store), members=LocalMemberBackend(store), signals=store,
                     component_secret=SECRET, guild_id=GUILD)
    client = discord.Client(intents=make_intents())
    t, _bridge = build_tree(client, svc, GUILD)
    yield t
    store.close()


def payloads(tree) -> dict[str, dict]:
    return {c.name: c.to_dict(tree) for c in tree.get_commands(guild=discord.Object(id=GUILD))}


def test_all_commands_are_guild_scoped_and_least_privilege(tree):
    cmds = payloads(tree)
    assert set(cmds) >= REQUIRED and set(cmds) == set(COMMAND_NAMES)
    assert tree.get_commands() == []  # nothing global
    for name, c in cmds.items():
        assert c["dm_permission"] is False, name
        assert len(c["description"]) <= 100 and all(len(o["description"]) <= 100 for o in c.get("options", []))
        perms = c.get("default_member_permissions")
        if name.startswith("apex_admin"):
            assert int(perms) == discord.Permissions(manage_guild=True).value
        else:
            assert perms is None, name
        assert not perms or not (int(perms) & discord.Permissions(administrator=True).value)


def test_starter_commands_keep_their_options(tree):
    cmds = payloads(tree)
    entry = {o["name"]: o for o in cmds["apex_entry"]["options"]}
    assert [o["name"] for o in cmds["apex_entry"]["options"]][:3] == ["ticker", "price", "shares"]
    assert entry["ticker"]["required"] and entry["price"]["type"] == 10 and entry["shares"]["type"] == 10
    assert not entry["signal_id"]["required"] and {"fill_time", "fees", "notes"} <= set(entry)
    remove = cmds["apex_remove"]["options"]
    assert [o["name"] for o in remove] == ["position_id"] and remove[0]["required"]
    assert cmds["apex_health"].get("options", []) == [] and cmds["apex_positions"]["options"][0]["required"] is False
    correct = {o["name"]: o for o in cmds["apex_correct"]["options"]}
    assert {c["value"] for c in correct["action"]["choices"]} == {"void", "replace", "undo"}


def test_intents_and_invite_permissions_are_minimal():
    i = make_intents()
    assert i.guilds and not i.members and not i.message_content and not i.presences
    assert i.value == discord.Intents(guilds=True).value
    assert make_intents(True).members  # only when the owner explicitly opts in
    assert not INVITE_PERMISSIONS.administrator and not INVITE_PERMISSIONS.manage_roles
    assert INVITE_PERMISSIONS.value == 19456


async def test_views_are_not_stored_and_modals_are_finished():
    view = build_view([{"type": 1, "components": [{"type": 2, "style": 1, "label": "Go", "custom_id": "apex:v1:x"}]}])
    assert view is not None and not view.is_dispatchable() and view.to_components()[0]["components"][0]["custom_id"]
    assert build_view([]) is None
    svc_reply = Reply(rows=[[Button("A", "apex:v1:a"), Button("B", "apex:v1:b", 4)]], content="hi")
    kw = reply_kwargs(svc_reply)
    assert kw["allowed_mentions"].everyone is False and kw["allowed_mentions"].users is False
    assert len(kw["view"].children) == 2
    from apex_worker.discord_bot.service import ModalField, ModalSpec

    modal = build_modal(ModalSpec("apex:v1:entry:sig_x:0", "T" * 60, tuple(
        ModalField(f"f{i}", "L" * 60) for i in range(7))))
    assert modal.is_finished() and len(modal.children) == 5 and len(modal.title) <= 45
    files = reply_kwargs(Reply(content="x", files=[("a.json", b"{}")]))
    assert files["files"][0].filename == "a.json"
    assert reply_kwargs(Reply())["content"] == "Done."


def test_modal_values_handles_action_rows_and_labels():
    legacy = {"components": [{"type": 1, "components": [{"type": 4, "custom_id": "price", "value": "101"}]},
                             {"type": 1, "components": [{"type": 4, "custom_id": "quantity", "value": "2"}]}]}
    labelled = {"components": [{"type": 18, "component": {"type": 4, "custom_id": "notes", "value": "hi"}}]}
    assert modal_values(legacy) == {"price": "101", "quantity": "2"}
    assert modal_values(labelled) == {"notes": "hi"}
    assert modal_values(None) == {}


def _http(cls, status, headers=None):
    resp = SimpleNamespace(status=status, reason="x", headers=headers or {})
    return cls(resp, {"code": 50007, "message": "nope"})


def test_transport_error_mapping():
    m = DiscordPyTransport._map
    rl = m(discord.RateLimited(3.25))
    assert isinstance(rl, TransportRateLimited) and rl.retry_after == 3.25
    assert isinstance(m(_http(discord.Forbidden, 403)), TransportForbidden)
    assert isinstance(m(_http(discord.NotFound, 404)), TransportNotFound)
    r = m(_http(discord.HTTPException, 429, {"Retry-After": "12"}))
    assert isinstance(r, TransportRateLimited) and r.retry_after == 12.0
    e = m(_http(discord.HTTPException, 502))
    assert e.status == 502 and not isinstance(e, TransportRateLimited)


def test_single_instance_lock(tmp_path):
    lock = acquire_instance_lock(tmp_path)
    assert (lock.path.stat().st_mode & 0o777) == 0o600
    with pytest.raises(AlreadyRunning):
        acquire_instance_lock(tmp_path)
    code = ("import sys; from pathlib import Path; from apex_worker.discord_bot.settings import "
            "acquire_instance_lock, AlreadyRunning\n"
            "try:\n    acquire_instance_lock(Path(sys.argv[1]))\nexcept AlreadyRunning:\n    sys.exit(7)\n")
    proc = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, timeout=60, check=False)
    assert proc.returncode == 7, proc.stderr
    lock.release()
    with acquire_instance_lock(tmp_path):
        pass
    proc = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr


def test_legacy_service_check_is_best_effort():
    def active(*_a, **_k):
        return subprocess.CompletedProcess([], 0, stdout="active\n", stderr="")

    def inactive(*_a, **_k):
        return subprocess.CompletedProcess([], 3, stdout="inactive\n", stderr="")

    def boom(*_a, **_k):
        raise subprocess.TimeoutExpired("systemctl", 5)

    which = lambda _n: "/usr/bin/systemctl"  # noqa: E731
    assert legacy_service_active(active, which) is True
    assert legacy_service_active(inactive, which) is False
    assert legacy_service_active(boom, which) is None
    assert legacy_service_active(active, lambda _n: None) is None


@pytest.fixture
def bot_env(tmp_path, monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith(("APEX_", "DISCORD_")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("APEX_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("APEX_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("DISCORD_GUILD_ID", str(GUILD))
    return tmp_path


def test_run_bot_refuses_when_legacy_starter_is_active(bot_env, monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(bot_app, "legacy_service_active", lambda: True)
    monkeypatch.setattr(bot_app, "_run_gateway", lambda *a: ran.append(1) or 0)
    assert bot_app.run_bot(dry_run=False) == 4 and ran == []
    err = capsys.readouterr().err
    assert "apex-pi-bot" in err and "migrate-starter" in err and FAKE_TOKEN not in err
    monkeypatch.setenv("APEX_ALLOW_LEGACY_OVERLAP", "1")
    assert bot_app.run_bot(dry_run=False) == 0 and ran == [1]


def test_run_bot_refuses_second_instance_and_bad_config(bot_env, monkeypatch, capsys):
    monkeypatch.setattr(bot_app, "legacy_service_active", lambda: False)
    monkeypatch.setattr(bot_app, "_run_gateway", lambda *a: 0)
    held = acquire_instance_lock(bot_env / "state")
    try:
        assert bot_app.run_bot(dry_run=False) == 3
    finally:
        held.release()
    assert bot_app.run_bot(dry_run=False) == 0
    monkeypatch.setenv("DISCORD_GUILD_ID", "not-a-number")
    assert bot_app.run_bot(dry_run=False) == 2
    monkeypatch.setenv("DISCORD_GUILD_ID", str(GUILD))
    monkeypatch.setenv("APEX_CHANNEL_MAP", '{"nope": "1"}')
    assert bot_app.run_bot(dry_run=False) == 2
    assert FAKE_TOKEN not in capsys.readouterr().err


def test_dry_run_prints_registration_and_sample_without_connecting(bot_env, monkeypatch, capsys):
    monkeypatch.setattr(bot_app, "legacy_service_active", lambda: None)

    def no_connect(*_a, **_k):
        raise AssertionError("dry run must not connect")

    monkeypatch.setattr(discord.Client, "run", no_connect)
    monkeypatch.setenv("APEX_CHANNEL_MAP", json.dumps({"market_intelligence": "900000000000000104"}))
    assert bot_app.run_bot(dry_run=True) == 0
    out = capsys.readouterr().out
    assert FAKE_TOKEN not in out
    d = json.loads(out)
    assert d["dry_run"] and not d["connects_to_discord"] and d["token"] == "configured"
    assert {c["name"] for c in d["commands"]} >= REQUIRED
    assert d["sample_new_signal"]["within_discord_limits"]
    assert d["sample_new_signal"]["target"] == "channel:900000000000000104"
    assert d["sample_new_signal"]["message"]["content"].startswith("[DEMO]")
    assert d["intents"]["message_content (privileged)"] is False and d["invite"]["administrator"] is False


def test_settings_channel_map_sources(tmp_path, monkeypatch):
    from apex_worker.config import WorkerConfig

    cfg = WorkerConfig(state_dir=tmp_path)
    s = BotSettings.load(cfg, environ={"APEX_CHANNEL_MAP": '{"system_status": "900000000000000106"}'})
    assert s.channel_map.get("system_status") == 900000000000000106 and "exit_alerts" in s.channel_map.missing()
    bot_app.write_channel_map(tmp_path, {"exit_alerts": "900000000000000102"})
    s = BotSettings.load(cfg, environ={})
    assert s.channel_map.get("exit_alerts") == 900000000000000102 and s.local_db == tmp_path / "apex_local.sqlite3"
    assert ((tmp_path / "discord_channels.json").stat().st_mode & 0o777) == 0o600
    bad = BotSettings.load(cfg, environ={"APEX_CHANNEL_MAP": '{"exit_alerts": "12"}'})
    assert bad.channel_map_error and bad.problems(cfg)
    flags = BotSettings.load(cfg, environ={"APEX_ALLOW_LEGACY_OVERLAP": "1", "APEX_DISCORD_MEMBERS_INTENT": "true",
                                           "APEX_LOCAL_DB": str(tmp_path / "x.db")})
    assert flags.allow_legacy_overlap and flags.members_intent and flags.local_db == tmp_path / "x.db"


def test_token_is_masked_in_logs():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(SecretMaskingFilter([FAKE_TOKEN]))
    logger = logging.getLogger("apex.test.mask")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("connecting with %s", FAKE_TOKEN)
        logger.info("Authorization: Bot %s", "OTk5OTk5OTk5OTk5OTk5OTk5.Xyz12A.zyxwvutsrqponmlkjihgfedcba98765432")
        try:
            raise RuntimeError(f"bad token {FAKE_TOKEN}")
        except RuntimeError:
            logger.exception("failure")
    finally:
        logger.removeHandler(handler)
    out = stream.getvalue()
    assert FAKE_TOKEN not in out and "OTk5OTk5" not in out and out.count("***") >= 3


def test_actor_comes_from_interaction_user():
    from apex_worker.discord_bot.gateway import actor_from

    inter = SimpleNamespace(user=SimpleNamespace(id=100000000000000001), id=1300000000000000001, guild_id=GUILD)
    a = actor_from(inter)
    assert (a.discord_user_id, a.interaction_id, a.guild_id) == ("100000000000000001", "1300000000000000001", str(GUILD))
    assert actor_from(SimpleNamespace(user=SimpleNamespace(id=5), id=6, guild_id=None)).guild_id is None


def test_component_secret_signs_ids_used_on_buttons(tree):
    # sanity: the tree's service uses the configured secret for every button it emits
    svc = tree.service
    now = datetime(2026, 10, 20, tzinfo=UTC)
    from apex_worker.discord_bot.samples import sample_signal

    sig = sample_signal(now)
    svc.signals.save_signal_revision(sig)
    r = svc.signals_list(SimpleNamespace(discord_user_id="100000000000000001", interaction_id="1300000000000000001",
                                         guild_id=str(GUILD)))
    from apex_worker.notifications import components

    assert all(components.parse(SECRET, b.custom_id) for row in r.rows for b in row)


# ---------------------------------------------------------------- Bridge driven by fake interactions (no network)
class FakeUser:
    def __init__(self, uid: int, forbid: bool = False):
        self.id = uid
        self.forbid = forbid
        self.dms: list[str] = []

    async def send(self, content, **_kw):
        if self.forbid:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), {"code": 50007, "message": "x"})
        self.dms.append(content)


class FakeResponse:
    def __init__(self):
        self.calls: list[tuple] = []

    async def defer(self, **kw):
        self.calls.append(("defer", kw))

    async def send_message(self, *a, **kw):
        self.calls.append(("send_message", kw))

    async def send_modal(self, modal):
        self.calls.append(("send_modal", modal))

    async def autocomplete(self, choices):
        self.calls.append(("autocomplete", choices))

    def is_done(self):
        return bool(self.calls)


class FakeFollowup:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, *a, **kw):
        if a:
            kw["content"] = a[0]
        self.sent.append(kw)


_fake_ids = iter(range(1_300_000_000_000_100_000, 1_300_000_000_000_200_000))


class FakeInteraction:
    def __init__(self, user: FakeUser, guild_id=GUILD, *, data=None, type_=discord.InteractionType.application_command):
        self.user = user
        self.id = next(_fake_ids)
        self.guild_id = guild_id
        self.data = data or {}
        self.type = type_
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.edits: list[dict] = []

    async def edit_original_response(self, **kw):
        self.edits.append(kw)


@pytest.fixture
def bridge_env():
    from apex_worker.discord_bot.samples import sample_signal

    store = LocalStore(":memory:")
    now = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)
    svc = BotService(positions=LocalPositionBackend(store), members=LocalMemberBackend(store), signals=store,
                     spool=store, component_secret=SECRET, guild_id=GUILD, clock=lambda: now)
    sig = sample_signal(now)
    store.save_signal_revision(sig)
    client = discord.Client(intents=make_intents())
    t, bridge = build_tree(client, svc, GUILD)
    yield {"svc": svc, "tree": t, "bridge": bridge, "sig": sig, "store": store}
    store.close()


async def test_slash_defers_then_sends_ephemeral_followup(bridge_env):
    svc, bridge = bridge_env["svc"], bridge_env["bridge"]
    inter = FakeInteraction(FakeUser(100000000000000001))
    await bridge.slash(inter, "apex_entry", lambda a: svc.entry(a, ticker="NVDA", price=181.25, shares=20))
    assert inter.response.calls == [("defer", {"ephemeral": True, "thinking": True})]
    sent = inter.followup.sent[0]
    assert sent["ephemeral"] is True and sent["allowed_mentions"].everyone is False
    assert "BUY 20 NVDA" in sent["embeds"][0].title
    assert len(LocalPositionBackend(bridge_env["store"]).positions("100000000000000001")) == 1


async def test_tree_check_refuses_other_guilds_and_dms_politely(bridge_env):
    tree = bridge_env["tree"]
    foreign = FakeInteraction(FakeUser(100000000000000001), guild_id=900000000000000099)
    assert await tree.interaction_check(foreign) is False
    kind, kw = foreign.response.calls[0]
    assert kind == "send_message" and kw["ephemeral"] is True and "private server" in kw["content"]
    dm = FakeInteraction(FakeUser(100000000000000001), guild_id=None)
    assert await tree.interaction_check(dm) is False
    auto = FakeInteraction(FakeUser(100000000000000001), guild_id=None, type_=discord.InteractionType.autocomplete)
    assert await tree.interaction_check(auto) is False and auto.response.calls == [("autocomplete", [])]
    assert await tree.interaction_check(FakeInteraction(FakeUser(100000000000000001))) is True


async def test_record_my_entry_button_opens_modal_and_submit_records(bridge_env):
    from apex_worker.notifications import components

    bridge, sig = bridge_env["bridge"], bridge_env["sig"]
    user = FakeUser(100000000000000002)
    cid = components.make(SECRET, "entry", sig["id"])
    click = FakeInteraction(user, data={"custom_id": cid, "component_type": 2}, type_=discord.InteractionType.component)
    await bridge.component(click)
    kind, modal = click.response.calls[0]
    assert kind == "send_modal" and modal.custom_id == cid and modal.is_finished()
    submit = FakeInteraction(user, type_=discord.InteractionType.modal_submit, data={"custom_id": cid, "components": [
        {"type": 1, "components": [{"type": 4, "custom_id": "price", "value": "101"}]},
        {"type": 1, "components": [{"type": 4, "custom_id": "quantity", "value": "3"}]}]})
    await bridge.modal(submit)
    assert submit.response.calls[0] == ("defer", {"ephemeral": True, "thinking": True})
    assert "BUY 3 DMSEMI1" in submit.followup.sent[0]["embeds"][0].title
    # a tampered button gets an ephemeral refusal, never a modal
    bad = FakeInteraction(user, data={"custom_id": cid[:-2] + "00"}, type_=discord.InteractionType.component)
    await bridge.component(bad)
    assert bad.response.calls[0][0] == "defer" and "invalid" in bad.followup.sent[0]["content"]
    # foreign custom ids (not ours) are ignored entirely
    other = FakeInteraction(user, data={"custom_id": "someone-elses-button"}, type_=discord.InteractionType.component)
    await bridge.component(other)
    assert other.response.calls == [] and other.followup.sent == []


async def test_confirm_button_edits_the_ephemeral_prompt(bridge_env):
    svc, bridge = bridge_env["svc"], bridge_env["bridge"]
    user = FakeUser(100000000000000003)
    await bridge.slash(FakeInteraction(user), "apex_entry", lambda a: svc.entry(a, ticker="AMD", price=10, shares=1))
    prompt_inter = FakeInteraction(user)
    await bridge.slash(prompt_inter, "apex_remove", lambda a: svc.remove(a, position_id="AMD"))
    view = prompt_inter.followup.sent[0]["view"]
    confirm_id = view.children[0].custom_id
    click = FakeInteraction(user, data={"custom_id": confirm_id}, type_=discord.InteractionType.component)
    await bridge.component(click)
    assert click.response.calls == [("defer", {})]
    assert click.edits and click.edits[0]["view"] is None
    assert LocalPositionBackend(bridge_env["store"]).positions(str(user.id)) == []


async def test_notifications_test_dm_reports_blocked_dms(bridge_env):
    svc, bridge = bridge_env["svc"], bridge_env["bridge"]
    blocked = FakeUser(100000000000000004, forbid=True)
    inter = FakeInteraction(blocked)
    await bridge.slash(inter, "apex_notifications", lambda a: svc.notifications(a, dms=True))
    assert any("blocked a test DM" in (m.get("content") or "") for m in inter.followup.sent)
    ok = FakeUser(100000000000000005)
    await bridge.slash(FakeInteraction(ok), "apex_notifications", lambda a: svc.notifications(a, dms=True))
    assert ok.dms and "private DMs are on" in ok.dms[0]


async def test_unexpected_errors_reply_generically_without_details(bridge_env, caplog):
    bridge = bridge_env["bridge"]
    inter = FakeInteraction(FakeUser(100000000000000006))

    def boom(_a):
        raise RuntimeError("secret detail 181.25")

    with caplog.at_level(logging.INFO):
        await bridge.slash(inter, "apex_entry", boom)
    assert "nothing was changed" in inter.followup.sent[0]["content"]
    assert "181.25" not in caplog.text and "RuntimeError" in caplog.text


async def test_apexbot_hooks_without_connecting(bridge_env):
    from apex_worker.discord_bot.gateway import ApexBot
    from apex_worker.notifications import components

    svc = bridge_env["svc"]
    bot = ApexBot(service=svc, guild_id=GUILD)
    assert not bot.intents.message_content and not bot.intents.members
    leaver = "100000000000000007"
    svc.members.ensure(leaver, datetime(2026, 10, 20, tzinfo=UTC))
    await bot.on_member_remove(SimpleNamespace(id=int(leaver), guild=SimpleNamespace(id=900000000000000099)))
    assert svc.members.status(leaver) == "active"  # other guilds are ignored
    await bot.on_member_remove(SimpleNamespace(id=int(leaver), guild=SimpleNamespace(id=GUILD)))
    assert svc.members.status(leaver) == "revoked"
    left: list[int] = []

    def guild(gid):
        async def leave():
            left.append(gid)
        return SimpleNamespace(id=gid, leave=leave)

    await bot._enforce_allowlist(guild(900000000000000099))
    await bot._enforce_allowlist(guild(GUILD))
    assert left == [900000000000000099]
    cid = components.make(SECRET, "evidence", bridge_env["sig"]["id"])
    inter = FakeInteraction(FakeUser(100000000000000008), data={"custom_id": cid},
                            type_=discord.InteractionType.component)
    await bot.on_interaction(inter)
    assert inter.response.calls[0][0] == "defer" and "Evidence" in inter.followup.sent[0]["embeds"][0].title
    slash = FakeInteraction(FakeUser(100000000000000008))
    await bot.on_interaction(slash)  # application commands are handled by the tree, not here
    assert slash.response.calls == []
