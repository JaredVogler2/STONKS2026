"""DiscordCommands: discord.py adapter (slash commands, buttons, modals, transport). No business logic, no SQL.

Every handler turns the interaction into an `Actor` (authenticated user id, interaction id, guild id), acknowledges
within Discord's 3-second window (deferred, ephemeral, "thinking") and runs the BotService call in a worker thread,
then sends the ephemeral follow-up. Interaction tokens stay valid for 15 minutes after the initial response, which
bounds how long a follow-up may take. Component and modal interactions are routed globally by their SIGNED custom
ids (not by in-memory views), so buttons on old alerts keep working after a restart and are re-verified each time.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
from collections.abc import Callable
from typing import Literal

import discord
from discord import app_commands

from .delivery import OutboxDelivery, TransportError, TransportForbidden, TransportNotFound, TransportRateLimited
from .service import ACTIVE_POSITION_STATES, HOLDING_STATES, Actor, BotService, ModalSpec, Reply

log = logging.getLogger("apex.discord.gateway")

# Minimal bot permissions for the invite URL: View Channels, Send Messages, Embed Links (no Administrator).
INVITE_PERMISSIONS = discord.Permissions(view_channel=True, send_messages=True, embed_links=True)


def make_intents(members_intent: bool = False) -> discord.Intents:
    """Only the non-privileged GUILDS intent. Message Content and Presence are never requested."""
    intents = discord.Intents.none()
    intents.guilds = True
    intents.members = bool(members_intent)  # privileged; opt-in via APEX_DISCORD_MEMBERS_INTENT=1
    return intents


def actor_from(interaction: discord.Interaction) -> Actor:
    return Actor(discord_user_id=str(interaction.user.id), interaction_id=str(interaction.id),
                 guild_id=str(interaction.guild_id) if interaction.guild_id else None)


class StaticView(discord.ui.View):
    """Buttons rendered from component JSON. Not dispatchable: clicks are routed by signed custom id instead."""

    def is_dispatchable(self) -> bool:
        return False


def build_view(rows: list[dict] | None) -> StaticView | None:
    if not rows:
        return None
    view = StaticView(timeout=None)
    for r, row in enumerate(rows[:5]):
        for c in row.get("components", [])[:5]:
            if c.get("type") == 2 and c.get("custom_id"):
                view.add_item(discord.ui.Button(style=discord.ButtonStyle(int(c.get("style", 2))),
                                                label=c.get("label"), custom_id=c["custom_id"], row=r))
    return view if view.children else None


def build_modal(spec: ModalSpec) -> discord.ui.Modal:
    d = spec.to_dict()
    modal = discord.ui.Modal(title=d["title"], custom_id=d["custom_id"], timeout=None)
    for row in d["components"]:
        f = row["components"][0]
        modal.add_item(discord.ui.TextInput(
            label=f["label"], custom_id=f["custom_id"], placeholder=f.get("placeholder") or None,
            required=f["required"], max_length=f["max_length"],
            style=discord.TextStyle.paragraph if f["style"] == 2 else discord.TextStyle.short))
    modal.stop()  # finished -> discord.py does not store it; the submit is routed by its signed custom id
    return modal


def modal_values(data: dict | None) -> dict[str, str]:
    """Extract {custom_id: value} from a modal-submit payload (action rows or labels)."""
    out: dict[str, str] = {}

    def walk(items: list) -> None:
        for c in items or []:
            if not isinstance(c, dict):
                continue
            if "custom_id" in c and "value" in c:
                out[str(c["custom_id"])] = str(c.get("value") or "")
            if c.get("components"):
                walk(c["components"])
            if isinstance(c.get("component"), dict):
                walk([c["component"]])

    walk((data or {}).get("components", []))
    return out


def _embeds(msg: dict) -> list[discord.Embed]:
    return [discord.Embed.from_dict(e) for e in msg.get("embeds", [])]


def _allowed(msg: dict) -> discord.AllowedMentions:
    users = (msg.get("allowed_mentions") or {}).get("users") or []
    return discord.AllowedMentions(everyone=False, roles=False, replied_user=False,
                                   users=[discord.Object(id=int(u)) for u in users] if users else False)


def reply_kwargs(reply: Reply) -> dict:
    msg = reply.to_message()
    kw: dict = {"content": msg["content"] or None, "embeds": _embeds(msg), "allowed_mentions":
                discord.AllowedMentions.none()}
    view = build_view(msg.get("components"))
    if view is not None:
        kw["view"] = view
    if reply.files:
        kw["files"] = [discord.File(io.BytesIO(data), filename=name) for name, data in reply.files]
    if not kw["content"] and not kw["embeds"] and not reply.files:
        kw["content"] = "Done."
    return kw


# ---- transport over discord.py -----------------------------------------------------------------------------------
class DiscordPyTransport:
    def __init__(self, client: discord.Client, guild_id: int):
        self.client = client
        self.guild_id = guild_id

    @staticmethod
    def _kwargs(message: dict, nonce: str) -> dict:
        kw: dict = {"content": message.get("content") or None, "embeds": _embeds(message),
                    "allowed_mentions": _allowed(message), "nonce": nonce}
        view = build_view(message.get("components"))
        if view is not None:
            kw["view"] = view
        return kw

    @staticmethod
    def _map(exc: Exception) -> Exception:
        if isinstance(exc, discord.RateLimited):
            return TransportRateLimited(exc.retry_after)
        if isinstance(exc, discord.Forbidden):
            return TransportForbidden(f"code {getattr(exc, 'code', 0)}")
        if isinstance(exc, discord.NotFound):
            return TransportNotFound(f"code {getattr(exc, 'code', 0)}")
        if isinstance(exc, discord.HTTPException):
            if exc.status == 429:
                retry = 5.0
                with contextlib.suppress(Exception):
                    retry = float(exc.response.headers.get("Retry-After", retry))
                return TransportRateLimited(retry)
            return TransportError(exc.status, f"code {getattr(exc, 'code', 0)}")
        return exc

    async def send_channel(self, channel_id: int, message: dict, *, nonce: str) -> str:
        try:
            channel = self.client.get_channel(channel_id) or await self.client.fetch_channel(channel_id)
            if getattr(channel, "guild", None) is not None and channel.guild.id != self.guild_id:
                raise TransportError(None, "channel is outside the allowlisted guild")
            sent = await channel.send(**self._kwargs(message, nonce))
        except (discord.DiscordException, discord.RateLimited) as exc:
            raise self._map(exc) from exc
        return str(sent.id)

    async def send_dm(self, discord_user_id: str, message: dict, *, nonce: str) -> str:
        try:
            user = self.client.get_user(int(discord_user_id)) or await self.client.fetch_user(int(discord_user_id))
            sent = await user.send(**self._kwargs(message, nonce))
        except (discord.DiscordException, discord.RateLimited) as exc:
            raise self._map(exc) from exc
        return str(sent.id)

    async def is_member(self, discord_user_id: str) -> bool | None:
        guild = self.client.get_guild(self.guild_id)
        if guild is None:
            return None
        try:
            await guild.fetch_member(int(discord_user_id))  # REST; works without the privileged members intent
        except discord.NotFound:
            return False
        except discord.DiscordException:
            return None
        return True


# ---- interaction bridge --------------------------------------------------------------------------------------------
class Bridge:
    def __init__(self, service: BotService):
        self.service = service

    async def _send(self, interaction: discord.Interaction, reply: Reply) -> None:
        await interaction.followup.send(**reply_kwargs(reply), ephemeral=True)

    async def _after(self, interaction: discord.Interaction, reply: Reply) -> None:
        extra = await asyncio.to_thread(self.service.after_reply, reply)
        if extra is not None:
            with contextlib.suppress(discord.DiscordException):
                await self._send(interaction, extra)
        if reply.test_dm:
            try:
                await interaction.user.send(reply.test_dm, allowed_mentions=discord.AllowedMentions.none())
            except discord.Forbidden:
                with contextlib.suppress(discord.DiscordException):
                    await interaction.followup.send(
                        "Discord blocked a test DM from APEX. Enable *Allow direct messages from server members* "
                        "(Server menu > Privacy Settings) or private updates will keep being held for your next "
                        "APEX reply.", ephemeral=True)
            except discord.DiscordException:
                pass

    async def _failed(self, interaction: discord.Interaction, name: str, exc: Exception) -> None:
        log.error("interaction %s failed: %s", name, type(exc).__name__)  # never log option values (fills)
        text = "Something went wrong and nothing was changed. Please try again."
        with contextlib.suppress(discord.DiscordException):
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)

    async def slash(self, interaction: discord.Interaction, name: str, fn: Callable[[Actor], Reply]) -> None:
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            reply = await asyncio.to_thread(fn, actor_from(interaction))
            await self._send(interaction, reply)
            log.info("command %s -> %s", name, reply.code)
            await self._after(interaction, reply)
        except Exception as exc:
            await self._failed(interaction, name, exc)

    async def component(self, interaction: discord.Interaction) -> None:
        cid = str((interaction.data or {}).get("custom_id", ""))
        if not cid.startswith("apex:"):
            return
        actor = actor_from(interaction)
        try:
            kind = self.service.component_kind(cid)
            if kind == "modal":  # a modal must be the FIRST response; the lookup is local and fast
                reply = await asyncio.to_thread(self.service.component, actor, cid)
                if reply.modal is not None:
                    await interaction.response.send_modal(build_modal(reply.modal))
                    return
                await interaction.response.send_message(**reply_kwargs(reply), ephemeral=True)
            elif kind == "update":
                await interaction.response.defer()
                reply = await asyncio.to_thread(self.service.component, actor, cid)
                if reply.replace_original:
                    kw = reply_kwargs(reply)
                    await interaction.edit_original_response(content=kw.get("content"), embeds=kw["embeds"],
                                                             view=kw.get("view"), allowed_mentions=kw["allowed_mentions"])
                else:
                    await self._send(interaction, reply)
            else:
                await interaction.response.defer(ephemeral=True, thinking=True)
                reply = await asyncio.to_thread(self.service.component, actor, cid)
                await self._send(interaction, reply)
            log.info("component %s -> %s", kind, reply.code)
            await self._after(interaction, reply)
        except Exception as exc:
            await self._failed(interaction, "component", exc)

    async def modal(self, interaction: discord.Interaction) -> None:
        data = interaction.data or {}
        cid = str(data.get("custom_id", ""))
        if not cid.startswith("apex:"):
            return
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            reply = await asyncio.to_thread(self.service.modal_submit, actor_from(interaction), cid, modal_values(data))
            await self._send(interaction, reply)
            log.info("modal -> %s", reply.code)
            await self._after(interaction, reply)
        except Exception as exc:
            await self._failed(interaction, "modal", exc)


class ApexTree(app_commands.CommandTree):
    """Global check: allowlisted guild only, revoked members refused - before any command body runs."""

    service: BotService | None = None

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if self.service is None:
            return False
        refusal = await asyncio.to_thread(self.service.gate, actor_from(interaction))
        if refusal is None:
            return True
        with contextlib.suppress(discord.DiscordException):
            if interaction.type == discord.InteractionType.autocomplete:
                await interaction.response.autocomplete([])
            else:
                await interaction.response.send_message(**reply_kwargs(refusal), ephemeral=True)
        log.info("interaction refused: %s", refusal.code)
        return False

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError, /) -> None:
        log.error("app command error: %s", type(error).__name__)
        with contextlib.suppress(discord.DiscordException):
            if not interaction.response.is_done() and interaction.type != discord.InteractionType.autocomplete:
                await interaction.response.send_message("That command is not available here.", ephemeral=True)


# ---- command registration ---------------------------------------------------------------------------------------
def _choices(pairs: list[tuple[str, str]]) -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=n, value=v) for n, v in pairs]


def register_commands(tree: app_commands.CommandTree, bridge: Bridge, guild: discord.abc.Snowflake) -> None:
    svc = bridge.service

    async def ac_active(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return _choices(await asyncio.to_thread(svc.position_choices, actor_from(interaction), current,
                                                states=ACTIVE_POSITION_STATES))

    async def ac_holding(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return _choices(await asyncio.to_thread(svc.position_choices, actor_from(interaction), current,
                                                states=HOLDING_STATES))

    async def ac_watching(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return _choices(await asyncio.to_thread(svc.position_choices, actor_from(interaction), current,
                                                states={"WATCHING"}))

    async def ac_any(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return _choices(await asyncio.to_thread(svc.position_choices, actor_from(interaction), current,
                                                include_closed=True))

    async def ac_signal(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return _choices(await asyncio.to_thread(svc.signal_choices, actor_from(interaction), current))

    async def ac_fill(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        pos = str(getattr(interaction.namespace, "position", "") or "")
        undo = str(getattr(interaction.namespace, "action", "") or "") == "undo"
        return _choices(await asyncio.to_thread(svc.fill_choices, actor_from(interaction), pos, current,
                                                corrections=undo))

    @tree.command(name="apex_help", description="How to use APEX privately (commands, privacy, buttons)", guild=guild)
    @app_commands.guild_only()
    async def apex_help(interaction: discord.Interaction) -> None:
        await bridge.slash(interaction, "apex_help", svc.help)

    @tree.command(name="apex_health", description="Check that the APEX bot is online and how fresh its data is",
                  guild=guild)
    @app_commands.guild_only()
    async def apex_health(interaction: discord.Interaction) -> None:
        await bridge.slash(interaction, "apex_health", svc.health)

    @tree.command(name="apex_signals", description="List open APEX group signals (private reply)", guild=guild)
    @app_commands.guild_only()
    async def apex_signals(interaction: discord.Interaction) -> None:
        await bridge.slash(interaction, "apex_signals", svc.signals_list)

    @tree.command(name="apex_watch", description="Privately watch an APEX signal or a ticker", guild=guild)
    @app_commands.guild_only()
    @app_commands.describe(signal_id="APEX signal (start typing)", ticker="Or a ticker to watch manually, e.g. NVDA")
    @app_commands.autocomplete(signal_id=ac_signal)
    async def apex_watch(interaction: discord.Interaction, signal_id: str = "", ticker: str = "") -> None:
        await bridge.slash(interaction, "apex_watch", lambda a: svc.watch(a, signal_id=signal_id, ticker=ticker))

    @tree.command(name="apex_unwatch", description="Stop watching one of YOUR watch-only positions", guild=guild)
    @app_commands.guild_only()
    @app_commands.describe(position="Your watch (start typing)")
    @app_commands.autocomplete(position=ac_watching)
    async def apex_unwatch(interaction: discord.Interaction, position: str) -> None:
        await bridge.slash(interaction, "apex_unwatch", lambda a: svc.unwatch(a, position=position))

    @tree.command(name="apex_entry", description="Privately save YOUR own entry (self-reported, not a broker record)",
                  guild=guild)
    @app_commands.guild_only()
    @app_commands.describe(ticker="Stock ticker such as NVDA", price="Your actual entry price in USD (positive)",
                           shares="Number of shares (positive; fractions allowed)",
                           signal_id="Optional APEX signal (start typing); starter refs like DEMO-001 become a note",
                           fill_time="now (default), HH:MM or YYYY-MM-DD HH:MM New York time, or ISO with zone",
                           fees="Fees paid (optional)", notes="Private notes, max 280 characters")
    @app_commands.autocomplete(signal_id=ac_signal)
    async def apex_entry(interaction: discord.Interaction, ticker: str, price: float, shares: float,
                         signal_id: str = "", fill_time: str = "now", fees: float = 0.0, notes: str = "") -> None:
        await bridge.slash(interaction, "apex_entry", lambda a: svc.entry(
            a, ticker=ticker, price=price, shares=shares, signal_id=signal_id, fill_time=fill_time, fees=fees,
            notes=notes))

    @tree.command(name="apex_positions", description="Privately list only YOUR positions and watches", guild=guild)
    @app_commands.guild_only()
    @app_commands.describe(position="Show one position in detail (optional)", include_closed="Include closed positions")
    @app_commands.autocomplete(position=ac_any)
    async def apex_positions(interaction: discord.Interaction, position: str = "", include_closed: bool = False) -> None:
        await bridge.slash(interaction, "apex_positions",
                           lambda a: svc.positions_view(a, position=position, include_closed=include_closed))

    @tree.command(name="apex_add", description="Record another buy on one of YOUR positions", guild=guild)
    @app_commands.guild_only()
    @app_commands.describe(position="Your position (start typing)", price="Fill price in USD", shares="Shares bought",
                           fill_time="now, HH:MM or YYYY-MM-DD HH:MM (New York)", fees="Fees (optional)",
                           notes="Private notes")
    @app_commands.autocomplete(position=ac_active)
    async def apex_add(interaction: discord.Interaction, position: str, price: float, shares: float,
                       fill_time: str = "now", fees: float = 0.0, notes: str = "") -> None:
        await bridge.slash(interaction, "apex_add", lambda a: svc.add(
            a, position=position, price=price, shares=shares, fill_time=fill_time, fees=fees, notes=notes))

    @tree.command(name="apex_reduce", description="Record a partial sell on one of YOUR positions", guild=guild)
    @app_commands.guild_only()
    @app_commands.describe(position="Your position (start typing)", price="Sell price in USD", shares="Shares sold",
                           fill_time="now, HH:MM or YYYY-MM-DD HH:MM (New York)", fees="Fees (optional)",
                           notes="Private notes")
    @app_commands.autocomplete(position=ac_holding)
    async def apex_reduce(interaction: discord.Interaction, position: str, price: float, shares: float,
                          fill_time: str = "now", fees: float = 0.0, notes: str = "") -> None:
        await bridge.slash(interaction, "apex_reduce", lambda a: svc.reduce(
            a, position=position, price=price, shares=shares, fill_time=fill_time, fees=fees, notes=notes))

    @tree.command(name="apex_close", description="Record selling ALL remaining shares of one of YOUR positions",
                  guild=guild)
    @app_commands.guild_only()
    @app_commands.describe(position="Your position (start typing)", price="Sell price in USD",
                           fill_time="now, HH:MM or YYYY-MM-DD HH:MM (New York)", fees="Fees (optional)",
                           notes="Private notes")
    @app_commands.autocomplete(position=ac_holding)
    async def apex_close(interaction: discord.Interaction, position: str, price: float, fill_time: str = "now",
                         fees: float = 0.0, notes: str = "") -> None:
        await bridge.slash(interaction, "apex_close", lambda a: svc.close(
            a, position=position, price=price, fill_time=fill_time, fees=fees, notes=notes))

    @tree.command(name="apex_correct", description="Correct one of YOUR recorded fills (asks to confirm first)",
                  guild=guild)
    @app_commands.guild_only()
    @app_commands.describe(position="Your position (start typing)", fill="The fill to correct (start typing)",
                           action="void = remove its effect, replace = new values, undo = cancel a correction",
                           price="Corrected price (replace)", shares="Corrected shares (replace)",
                           fill_time="Corrected time (replace; New York unless a zone is given)",
                           fees="Corrected fees (replace)", reason="Why (private, optional)")
    @app_commands.autocomplete(position=ac_any, fill=ac_fill)
    async def apex_correct(interaction: discord.Interaction, position: str, fill: str = "",
                           action: Literal["void", "replace", "undo"] = "void", price: float | None = None,
                           shares: float | None = None, fill_time: str = "", fees: float | None = None,
                           reason: str = "") -> None:
        await bridge.slash(interaction, "apex_correct", lambda a: svc.correct(
            a, position=position, fill=fill, action=action, price=price, shares=shares, fill_time=fill_time,
            fees=fees, reason=reason))

    @tree.command(name="apex_remove", description="Remove one of YOUR entries (voided, kept in your audit history)",
                  guild=guild)
    @app_commands.guild_only()
    @app_commands.describe(position_id="Position from /apex_positions (or the old starter entry number)")
    @app_commands.autocomplete(position_id=ac_active)
    async def apex_remove(interaction: discord.Interaction, position_id: str) -> None:
        await bridge.slash(interaction, "apex_remove", lambda a: svc.remove(a, position_id=position_id))

    @tree.command(name="apex_notifications", description="Private DMs, high-priority mentions and symbol mutes",
                  guild=guild)
    @app_commands.guild_only()
    @app_commands.describe(dms="Receive private position updates by DM", high_priority_mentions="Be @mentioned on "
                           "high-conviction alerts", mute="Mute a ticker (no DMs/mentions about it)",
                           unmute="Unmute a ticker")
    async def apex_notifications(interaction: discord.Interaction, dms: bool | None = None,
                                 high_priority_mentions: bool | None = None, mute: str = "", unmute: str = "") -> None:
        await bridge.slash(interaction, "apex_notifications", lambda a: svc.notifications(
            a, dms=dms, high_priority_mentions=high_priority_mentions, mute=mute, unmute=unmute))

    @tree.command(name="apex_export", description="Get a private JSON copy of YOUR APEX data", guild=guild)
    @app_commands.guild_only()
    async def apex_export(interaction: discord.Interaction) -> None:
        await bridge.slash(interaction, "apex_export", svc.export)

    @tree.command(name="apex_delete_my_data", description="Permanently delete YOUR APEX data (two confirmations)",
                  guild=guild)
    @app_commands.guild_only()
    async def apex_delete_my_data(interaction: discord.Interaction) -> None:
        await bridge.slash(interaction, "apex_delete_my_data", svc.delete_my_data)

    @tree.command(name="apex_admin_status", description="Owner only: APEX health and aggregate counts", guild=guild)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def apex_admin_status(interaction: discord.Interaction) -> None:
        await bridge.slash(interaction, "apex_admin_status", svc.admin_status)

    @tree.command(name="apex_admin_restore", description="Owner only: restore a revoked member's access", guild=guild)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(member="The member to restore")
    async def apex_admin_restore(interaction: discord.Interaction, member: discord.User) -> None:
        await bridge.slash(interaction, "apex_admin_restore",
                           lambda a: svc.admin_restore(a, target_discord_id=str(member.id)))


COMMAND_NAMES = (
    "apex_help", "apex_health", "apex_signals", "apex_watch", "apex_unwatch", "apex_entry", "apex_positions",
    "apex_add", "apex_reduce", "apex_close", "apex_correct", "apex_remove", "apex_notifications", "apex_export",
    "apex_delete_my_data", "apex_admin_status", "apex_admin_restore",
)


def build_tree(client: discord.Client, service: BotService, guild_id: int) -> tuple[ApexTree, Bridge]:
    tree = ApexTree(client)
    tree.service = service
    bridge = Bridge(service)
    register_commands(tree, bridge, discord.Object(id=guild_id))
    return tree, bridge


# ---- the client ---------------------------------------------------------------------------------------------------
class ApexBot(discord.Client):
    def __init__(self, *, service: BotService, guild_id: int, members_intent: bool = False,
                 delivery_factory: Callable[[discord.Client], OutboxDelivery] | None = None,
                 poll_seconds: float = 5.0, background: list[Callable[[asyncio.Event], object]] | None = None):
        super().__init__(intents=make_intents(members_intent), max_ratelimit_timeout=10.0,
                         allowed_mentions=discord.AllowedMentions.none())
        self.service = service
        self.allowed_guild_id = guild_id
        self.apex_tree, self.bridge = build_tree(self, service, guild_id)
        self.delivery_factory = delivery_factory
        self.delivery: OutboxDelivery | None = None
        self.poll_seconds = poll_seconds
        self.background = background or []
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    async def setup_hook(self) -> None:
        synced = await self.apex_tree.sync(guild=discord.Object(id=self.allowed_guild_id))
        log.info("registered %d guild slash commands", len(synced))
        if self.delivery_factory is not None:
            self.delivery = self.delivery_factory(self)
            self._tasks.append(asyncio.create_task(self._delivery_main(self.delivery), name="apex-outbox-delivery"))
        for fn in self.background:
            self._tasks.append(asyncio.create_task(fn(self._stop), name="apex-background"))  # type: ignore[arg-type]

    async def _delivery_main(self, delivery: OutboxDelivery) -> None:
        await self.wait_until_ready()  # channel/guild caches populated before the first pass
        await delivery.run(self._stop, self.poll_seconds)

    async def _enforce_allowlist(self, guild: discord.Guild) -> None:
        if guild.id != self.allowed_guild_id:
            log.warning("leaving non-allowlisted guild")
            with contextlib.suppress(discord.DiscordException):
                await guild.leave()

    async def on_ready(self) -> None:
        log.info("APEX bot connected; %d guild(s)", len(self.guilds))
        for g in list(self.guilds):
            await self._enforce_allowlist(g)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._enforce_allowlist(guild)

    async def on_member_remove(self, member: discord.Member) -> None:  # requires the optional members intent
        if member.guild.id == self.allowed_guild_id:
            await asyncio.to_thread(self.service.member_left, str(member.id))
            log.info("member left: access revoked")

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type == discord.InteractionType.component:
            await self.bridge.component(interaction)
        elif interaction.type == discord.InteractionType.modal_submit:
            await self.bridge.modal(interaction)

    async def close(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        await super().close()
