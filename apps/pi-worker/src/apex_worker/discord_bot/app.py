"""`apex bot` entry point: wires config -> local store -> BotService -> discord.py gateway + outbox delivery loop.

`run_bot(dry_run=True)` prints the slash-command registration payloads, intents, invite permissions, channel-map
status and a sample DEMO NEW_SIGNAL message exactly as it would be posted - without a token and without connecting.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from apex_worker.config import WorkerConfig
from apex_worker.notifications import intents as intent_builders
from apex_worker.storage.local import LocalStore

from .backend import LocalMemberBackend, LocalPositionBackend, replay_spool
from .delivery import CHANNEL_KEYS, ChannelMap, MemoryTransport, OutboxDelivery
from .limits import message_within_limits
from .samples import sample_signal
from .service import BotService
from .settings import (
    LEGACY_MESSAGE,
    AlreadyRunning,
    BotSettings,
    InstanceLock,
    acquire_instance_lock,
    legacy_service_active,
    masked_handler,
)

log = logging.getLogger("apex.discord")
PLACEHOLDER_GUILD = 100000000000000000
PLACEHOLDER_CHANNEL = 100000000000000001


def build_service(cfg: WorkerConfig, store: LocalStore, *, guild_id: int, runtime_status=None) -> tuple[
        BotService, LocalPositionBackend, LocalMemberBackend]:
    positions = LocalPositionBackend(store, is_demo=cfg.release_mode == "DEMO")
    members = LocalMemberBackend(store)
    svc = BotService(positions=positions, members=members, signals=store, spool=store,
                     component_secret=cfg.component_secret or b"", guild_id=guild_id, owner_ids=cfg.owner_discord_ids,
                     release_mode=cfg.release_mode, runtime_status=runtime_status)
    return svc, positions, members


def _dry_run(cfg: WorkerConfig, settings: BotSettings) -> int:
    import discord

    from .gateway import INVITE_PERMISSIONS, build_tree, make_intents

    guild = int(cfg.discord_guild_id) if (cfg.discord_guild_id or "").isdigit() else PLACEHOLDER_GUILD
    now = datetime.now(UTC).replace(microsecond=0)
    store = LocalStore(":memory:")
    secret = cfg.component_secret or b"dry-run-component-secret-not-for-production"
    cfg.component_secret = secret
    svc, _p, members = build_service(cfg, store, guild_id=guild)
    client = discord.Client(intents=make_intents(settings.members_intent))
    tree, _bridge = build_tree(client, svc, guild)
    commands = [c.to_dict(tree) for c in tree.get_commands(guild=discord.Object(id=guild))]

    sig = sample_signal(now)
    intent = intent_builders.new_signal(sig, "RESEARCH_SETUP", now)
    store.enqueue(intent, now)
    channels = settings.channel_map if settings.channel_map.channels else ChannelMap(
        dict.fromkeys(CHANNEL_KEYS, PLACEHOLDER_CHANNEL), "placeholder (dry run)")
    transport = MemoryTransport()
    delivery = OutboxDelivery(store, transport, secret, channels, members, clock=lambda: now)
    asyncio.run(delivery.deliver_due(now))
    sample = transport.sent[0] if transport.sent else None
    intents = make_intents(settings.members_intent)
    out = {
        "dry_run": True, "connects_to_discord": False, "release_mode": cfg.release_mode,
        "token": "configured" if cfg.discord_token else "MISSING (DISCORD_BOT_TOKEN)",
        "guild_id": str(guild) + ("" if guild != PLACEHOLDER_GUILD else " (placeholder - DISCORD_GUILD_ID not set)"),
        "owner_ids_configured": len(cfg.owner_discord_ids),
        "intents": {"guilds": intents.guilds, "members (privileged, optional)": intents.members,
                    "message_content (privileged)": intents.message_content, "presences (privileged)": intents.presences},
        "invite": {"scopes": ["bot", "applications.commands"], "permissions": INVITE_PERMISSIONS.value,
                   "permission_names": ["View Channels", "Send Messages", "Embed Links"], "administrator": False},
        "channel_map": {"source": settings.channel_map.source, "configured": sorted(settings.channel_map.channels),
                        "missing": settings.channel_map.missing(), "error": settings.channel_map_error},
        "local_db": str(settings.local_db),
        "legacy_service_apex_pi_bot_active": legacy_service_active(),
        "commands": commands,
        "sample_new_signal": {"target": sample["target"] if sample else None,
                              "within_discord_limits": bool(sample and message_within_limits(sample["message"])),
                              "message": sample["message"] if sample else None},
    }
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    store.close()
    return 0


def run_bot(dry_run: bool = False) -> int:
    cfg = WorkerConfig.load()
    settings = BotSettings.load(cfg)
    if dry_run:
        return _dry_run(cfg, settings)
    problems = settings.problems(cfg)
    if problems:
        for p in problems:
            print(f"apex bot: {p}", file=sys.stderr)
        return 2
    try:
        lock: InstanceLock = acquire_instance_lock(cfg.state_dir)
    except AlreadyRunning as exc:
        print(f"apex bot: {exc}", file=sys.stderr)
        return 3
    try:
        if legacy_service_active() and not settings.allow_legacy_overlap:
            print("apex bot: " + LEGACY_MESSAGE, file=sys.stderr)
            return 4
        return _run_gateway(cfg, settings, lock)
    finally:
        lock.release()


def _run_gateway(cfg: WorkerConfig, settings: BotSettings, lock: InstanceLock) -> int:
    from .gateway import ApexBot, DiscordPyTransport

    guild = int(cfg.discord_guild_id or 0)
    store = LocalStore(settings.local_db)
    holder: dict = {}

    def runtime_status() -> dict:
        d = holder["bot"].delivery
        st = d.stats.to_dict() if d else {}
        st.update({"channels_configured": sorted(settings.channel_map.channels),
                   "channels_missing": settings.channel_map.missing(), "instance_lock": str(lock.path)})
        return st

    svc, positions, members = build_service(cfg, store, guild_id=guild, runtime_status=runtime_status)

    def delivery_factory(client) -> OutboxDelivery:
        return OutboxDelivery(store, DiscordPyTransport(client, guild), cfg.component_secret or b"",
                              settings.channel_map, members)

    async def spool_loop(stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.to_thread(replay_spool, positions, store, datetime.now(UTC))
            except Exception as exc:
                log.warning("spool replay failed: %s", type(exc).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=60)

    bot = ApexBot(service=svc, guild_id=guild, members_intent=settings.members_intent,
                  delivery_factory=delivery_factory, poll_seconds=settings.poll_seconds, background=[spool_loop])
    holder["bot"] = bot
    if settings.channel_map.missing():
        log.warning("channel map missing keys: %s (those alerts stay queued)", ", ".join(settings.channel_map.missing()))
    try:
        bot.run(cfg.discord_token or "", log_handler=masked_handler([cfg.discord_token]), log_level=logging.INFO,
                root_logger=True)
    finally:
        store.close()
    return 0


def write_channel_map(state_dir: Path, mapping: dict[str, str]) -> Path:
    """Helper for operators/tests: validate and persist <state_dir>/discord_channels.json (0600)."""
    ChannelMap.parse(mapping, "write_channel_map")
    path = state_dir / "discord_channels.json"
    state_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)
    return path
