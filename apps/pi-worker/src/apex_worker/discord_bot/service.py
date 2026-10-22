"""BotService: everything the Discord bot decides, as pure Python (unit-testable without Discord).

DiscordCommands (gateway.py) translate interactions into `Actor` + plain arguments, call one method here and send
the returned `Reply`. This layer owns: guild/DM/revocation gating, identity binding (the actor is ALWAYS the
interaction's authenticated user, never an option value), input validation, idempotency-key derivation from the
interaction id, ownership-scoped lookups through the backend ports, confirmation steps for destructive actions,
and message composition within Discord's limits. Every reply is ephemeral; nothing here ever posts to a channel.
"""

from __future__ import annotations

import contextlib
import json
import logging
import secrets
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from apex_worker.contracts import ContractViolation
from apex_worker.notifications import components
from apex_worker.notifications.render import (
    AMBER,
    CORAL,
    DEMO_FOOTER,
    DISCLAIMER,
    GREEN,
    SLATE,
    STATUS_TEXT,
    VIOLET,
    confidence_text,
    feed_label,
    render,
)
from apex_worker.portfolio.ledger import LedgerError, fmt
from apex_worker.services.positions import PUBLISHED_STATES, PositionNotFound, RecordResult, ServiceError
from apex_worker.storage.local import OutboxRow
from apex_worker.timeutil import et_label, iso_utc, parse_utc

from .backend import (
    SPOOL_KIND,
    BackendUnavailable,
    MemberBackend,
    MemberRevoked,
    PositionBackend,
    PositionHistory,
    SignalSource,
    Spool,
)
from .limits import (
    BUTTONS_PER_ROW,
    CHOICE_NAME_MAX,
    CHOICES_MAX,
    CONTENT_MAX,
    EMBED_TOTAL_MAX,
    EMBEDS_MAX,
    EPHEMERAL,
    ROWS_MAX,
    chunk_text,
    clamp_embed,
    clamp_message,
    clip,
    embed_length,
)
from .validation import (
    SIGNAL_ID_RE,
    SYMBOL_RE,
    UUID_RE,
    InputError,
    classify_signal_ref,
    friendly,
    parse_fees,
    parse_fill_time,
    parse_notes,
    parse_price,
    parse_quantity,
    parse_symbol,
)

log = logging.getLogger("apex.discord.service")

OPEN_SIGNAL_STATES = {"PUBLISHED", "ACTIVE"}
ACTIVE_POSITION_STATES = {"WATCHING", "OPEN", "PARTIALLY_CLOSED"}
HOLDING_STATES = {"OPEN", "PARTIALLY_CLOSED"}
FILL_TYPES = {"OPEN", "ADD", "PARTIAL_EXIT", "CLOSE"}
MODAL_OPS = {"add", "reduce", "close", "open"}
CONFIRM_TTL = timedelta(minutes=10)
MAX_PRIVATE_EMBEDS = 3
SNOWFLAKE_MAX_LEN = 21
SELF_REPORTED = "Self-reported - not broker proof. APEX places no orders."
PRIVACY = "Private: only you can see this. Your fills are never posted to shared channels."
DATA_CAVEAT = "Quotes can be delayed or single-venue (IEX is not the NBBO); verify at your broker."
OFF_PLAN_TEXT = {"ENTRY_OUTSIDE_SIGNAL_RANGE": "outside the published entry zone",
                 "ENTRY_AFTER_SIGNAL_EXPIRY": "after the entry window closed",
                 "ENTRY_BEFORE_SIGNAL_PUBLISHED": "before the signal was published"}


# ---- value objects returned to the Discord layer ----------------------------------------------------------------
@dataclass(frozen=True)
class Actor:
    """The authenticated interaction user. Built ONLY from the Discord event, never from option values."""

    discord_user_id: str
    interaction_id: str
    guild_id: str | None = None


@dataclass(frozen=True)
class Button:
    label: str
    custom_id: str
    style: int = 2  # 1 primary, 2 secondary, 3 success, 4 danger

    def to_component(self) -> dict:
        return {"type": 2, "style": self.style, "label": clip(self.label, 80), "custom_id": self.custom_id}


@dataclass(frozen=True)
class ModalField:
    custom_id: str
    label: str
    placeholder: str = ""
    required: bool = True
    max_length: int = 32
    paragraph: bool = False


@dataclass(frozen=True)
class ModalSpec:
    custom_id: str
    title: str
    fields: tuple[ModalField, ...]

    def to_dict(self) -> dict:
        return {"custom_id": self.custom_id, "title": clip(self.title, 45), "components": [
            {"type": 1, "components": [{"type": 4, "custom_id": f.custom_id, "style": 2 if f.paragraph else 1,
                                        "label": clip(f.label, 45), "placeholder": clip(f.placeholder, 100),
                                        "required": f.required, "max_length": f.max_length}]}
            for f in self.fields[:5]]}


@dataclass
class Reply:
    content: str = ""
    embeds: list[dict] = field(default_factory=list)
    rows: list[list[Button]] = field(default_factory=list)
    files: list[tuple[str, bytes]] = field(default_factory=list)
    modal: ModalSpec | None = None
    replace_original: bool = False  # confirm/cancel: edit the ephemeral prompt instead of sending a new message
    test_dm: str | None = None  # gateway sends this as a DM probe after the reply (notifications opt-in)
    code: str = "OK"
    ok: bool = True
    private_outbox_ids: list[str] = field(default_factory=list)
    private_user_id: str | None = None

    def to_message(self) -> dict:
        msg = {"content": self.content, "embeds": self.embeds,
               "components": [{"type": 1, "components": [b.to_component() for b in row[:BUTTONS_PER_ROW]]}
                              for row in self.rows[:ROWS_MAX] if row],
               "allowed_mentions": {"parse": []}, "flags": EPHEMERAL}
        return clamp_message(msg)


@dataclass
class PendingAction:
    op: str
    discord_user_id: str
    base_interaction_id: str
    expires_at: datetime
    payload: dict


class _Refusal(Exception):
    def __init__(self, reply: Reply):
        super().__init__(reply.code)
        self.reply = reply


@dataclass
class _Ctx:
    actor: Actor
    user_id: str | None
    now: datetime

    @property
    def duid(self) -> str:
        return self.actor.discord_user_id


class PendingStore:
    """In-memory confirmations (expire in 10 minutes; a restart simply asks the member to run the command again)."""

    def __init__(self) -> None:
        self._items: dict[str, PendingAction] = {}
        self._lock = threading.Lock()

    def put(self, action: PendingAction) -> str:
        nonce = secrets.token_hex(8)
        with self._lock:
            self._items[nonce] = action
        return nonce

    def take(self, nonce: str, discord_user_id: str, now: datetime) -> PendingAction | None:
        with self._lock:
            for k in [k for k, v in self._items.items() if v.expires_at < now]:
                del self._items[k]
            pa = self._items.get(nonce)
            if pa is None or pa.discord_user_id != discord_user_id:
                return None
            del self._items[nonce]
            return pa


# ---- formatting helpers ------------------------------------------------------------------------------------------
def _when(iso: str | None) -> str:
    if not iso:
        return "n/a"
    t = parse_utc(iso)
    return f"{et_label(t)} / {t.strftime('%H:%M')} UTC"


def _age(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 120:
        return f"{s} s"
    if s < 7200:
        return f"{s // 60} min"
    if s < 172800:
        return f"{s // 3600} h"
    return f"{s // 86400} days"


def _px(value: str | Decimal | None) -> str:
    if value in (None, ""):
        return "n/a"
    return "$" + fmt(Decimal(str(value)).quantize(Decimal("0.0001")))


def _money(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _field(name: str, value: str, inline: bool = False) -> dict:
    return {"name": clip(name, 256), "value": clip(value or "-", 1024), "inline": inline}


def _embed(title: str, description: str = "", fields: list[dict] | None = None, color: int = SLATE,
           footer: str = "") -> dict:
    e: dict = {"title": clip(title, 256), "color": color}
    if description:
        e["description"] = clip(description, 4096)
    if fields:
        e["fields"] = fields[:25]
    if footer:
        e["footer"] = {"text": clip(footer, 2048)}
    return clamp_embed(e)


def _short(position_id: str) -> str:
    return position_id[:8]


def _qty(p: dict) -> str:
    return f"{p['total_quantity']} sh"


def _snowflake(value: str) -> bool:
    return value.isdigit() and not value.startswith("0") and 15 <= len(value) <= SNOWFLAKE_MAX_LEN


# ---- the service --------------------------------------------------------------------------------------------------
class BotService:
    def __init__(self, *, positions: PositionBackend, members: MemberBackend, signals: SignalSource,
                 component_secret: bytes, guild_id: str | int, owner_ids: Iterable[str] = (),
                 release_mode: str = "DEMO", spool: Spool | None = None,
                 clock: Callable[[], datetime] | None = None, backend_label: str = "local SQLite (offline canonical)",
                 runtime_status: Callable[[], dict] | None = None):
        self.positions = positions
        self.members = members
        self.signals = signals
        self.secret = component_secret
        self.guild_id = str(guild_id)
        self.owner_ids = {str(x) for x in owner_ids}
        self.release_mode = release_mode
        self.spool = spool
        self.clock = clock or (lambda: datetime.now(UTC))
        self.backend_label = backend_label
        self.runtime_status = runtime_status or dict
        self.pending = PendingStore()

    # ------------------------------------------------------------------ plumbing
    @property
    def demo(self) -> bool:
        return self.release_mode == "DEMO"

    def _footer(self, text: str = "") -> str:
        base = text or f"{SELF_REPORTED} {PRIVACY}"
        return (DEMO_FOOTER + " | " + base) if self.demo else base

    def _history(self) -> PositionHistory:
        if not isinstance(self.positions, PositionHistory):
            raise InputError("UNSUPPORTED", "This APEX backend does not expose fill history yet.")
        return self.positions

    @staticmethod
    def _key(ctx: _Ctx, suffix: str | None = None) -> str:
        return f"discord:{ctx.actor.interaction_id}" + (f":{suffix}" if suffix else "")

    @staticmethod
    def _stamp(event: dict, interaction_id: str) -> dict:
        if _snowflake(interaction_id):
            event["discord_interaction_id"] = interaction_id
        return event

    def _error(self, code: str, detail: str | None = None) -> Reply:
        return Reply(content=friendly(code, detail), code=code, ok=False)

    def _refuse(self, code: str, text: str) -> _Refusal:
        return _Refusal(Reply(content=text, code=code, ok=False))

    def gate(self, actor: Actor, *, allow_dm: bool = False) -> Reply | None:
        """Cheap check used by the command tree before any command runs. None means allowed."""
        try:
            self._check(actor, allow_dm=allow_dm)
        except _Refusal as r:
            return r.reply
        return None

    def _check(self, actor: Actor, *, allow_dm: bool) -> str:
        if actor.guild_id is None:
            if not allow_dm:
                raise self._refuse("REFUSED_DM", "APEX commands only work inside the private APEX server, so your "
                                                 "data stays scoped to it. Please use them there.")
        elif str(actor.guild_id) != self.guild_id:
            raise self._refuse("REFUSED_GUILD", "This APEX bot only serves its own private server. Nothing was "
                                                "recorded.")
        status = self.members.status(actor.discord_user_id)
        if status == "revoked":
            raise self._refuse("REVOKED", friendly("MEMBERSHIP_REVOKED"))
        if actor.guild_id is None and status != "active":
            raise self._refuse("REFUSED_DM", "Use APEX from the private server first.")
        return status

    def _run(self, actor: Actor, fn: Callable[[_Ctx], Reply], *, allow_dm: bool = False,
             attach_private: bool = True) -> Reply:
        ctx: _Ctx | None = None
        try:
            status = self._check(actor, allow_dm=allow_dm)
            now = self.clock()
            # read-only lookup for known members (no write lock on the hot path, e.g. opening a modal within 3 s)
            uid = self.members.user_id(actor.discord_user_id) if status == "active" else None
            ctx = _Ctx(actor, uid or self.members.ensure(actor.discord_user_id, now), now)
            reply = fn(ctx)
        except _Refusal as r:
            return r.reply
        except MemberRevoked:
            return Reply(content=friendly("MEMBERSHIP_REVOKED"), code="REVOKED", ok=False)
        except InputError as exc:
            reply = self._error(exc.code, str(exc))
        except (ServiceError, LedgerError) as exc:
            reply = self._error(exc.code, str(exc))
        except PositionNotFound:
            reply = self._error("POSITION_NOT_FOUND")
        except ContractViolation:
            log.warning("contract violation building a member event (values not logged)")
            reply = Reply(content="That request was malformed, so nothing was saved.", code="INVALID_EVENT", ok=False)
        except BackendUnavailable:
            reply = Reply(content="The APEX position store is unreachable right now, so this could not be shown. "
                                  "Nothing was changed. Please try again shortly.", code="BACKEND_UNAVAILABLE", ok=False)
        if attach_private and ctx is not None and ctx.user_id and reply.modal is None:
            self._attach_private(ctx, reply)
        return reply

    # ------------------------------------------------------------------ backend calls
    def _record(self, ctx: _Ctx, event: dict) -> tuple[RecordResult | None, Reply | None]:
        try:
            res = self.positions.record(ctx.duid, event, ctx.now)
        except BackendUnavailable:
            return None, self._queued(ctx, event)
        if res.status == "queued_pending_sync":
            return None, self._queued_reply(spooled=True)
        return res, None

    def _queued(self, ctx: _Ctx, event: dict) -> Reply:
        if self.spool is None:
            return self._queued_reply(spooled=False)
        try:
            self.spool.spool_put(SPOOL_KIND, event["client_idempotency_key"],
                                 {"discord_user_id": ctx.duid, "event": event, "queued_at": iso_utc(ctx.now)}, ctx.now)
        except Exception:
            log.warning("local spool write failed")
            return self._queued_reply(spooled=False)
        return self._queued_reply(spooled=True)

    @staticmethod
    def _queued_reply(*, spooled: bool) -> Reply:
        if spooled:
            return Reply(content="**Queued - pending sync.** APEX could not reach its position store, so this is "
                                 "**NOT saved yet**. It is held privately on the APEX Pi and will sync automatically; "
                                 "/apex_positions lists it under *Pending sync*. Please do not re-enter it.",
                         code="QUEUED", ok=True)
        return Reply(content="**Not saved.** The APEX position store is unavailable and the local queue could not "
                             "hold your entry. Nothing was recorded - please try again in a few minutes.",
                     code="NOT_SAVED", ok=False)

    def _active_for_signal(self, ctx: _Ctx, signal_id: str) -> dict | None:
        try:
            mine = self.positions.positions(ctx.duid, include_closed=False)
        except BackendUnavailable:
            return None
        hits = [p for p in mine if p.get("signal_id") == signal_id and p["state"] in ACTIVE_POSITION_STATES]
        return hits[0] if hits else None

    def _resolve(self, ctx: _Ctx, ref: str | None, *, states: set[str] | None = None) -> dict:
        """Resolve a member-supplied position reference to ONE of the member's own positions."""
        r = (ref or "").strip().strip("#`").strip()
        pos: dict | None = None
        if not r:
            cands = [p for p in self.positions.positions(ctx.duid, include_closed=False)
                     if states is None or p["state"] in states]
            if len(cands) != 1:
                raise InputError("POSITION_REQUIRED", "Pick the position (start typing to see your list).")
            pos = cands[0]
        elif UUID_RE.fullmatch(r.lower()):
            pos = self.positions.position(ctx.duid, r.lower())
        elif r.isdigit() and (pid := self._history().position_for_key(ctx.duid, f"starter:{int(r)}")) is not None:
            pos = self.positions.position(ctx.duid, pid)  # starter-bot entry number (idempotency key starter:<id>)
        else:  # an all-digit reference with no starter entry is still a position-id prefix (2.3% of UUIDs)
            mine = self.positions.positions(ctx.duid, include_closed=True)
            low = r.lower()
            hits = [p for p in mine if len(low) >= 4 and p["position_id"].startswith(low)]
            if not hits and SYMBOL_RE.fullmatch(r.upper()):
                hits = [p for p in mine if p["symbol"] == r.upper() and p["state"] != "CLOSED"
                        and (states is None or p["state"] in states)]
            if not hits:
                raise PositionNotFound("position not found")
            if len(hits) > 1:
                raise InputError("AMBIGUOUS_POSITION", "Several of your positions match; pick one from the list.")
            pos = hits[0]
        if states and pos["state"] not in states:
            if states == {"WATCHING"}:
                raise InputError("NOT_WATCHING", f"That {pos['symbol']} position is {pos['state']}, not watch-only.")
            raise InputError("NOT_OPEN", f"That {pos['symbol']} position is {pos['state']}.")
        return pos

    # ------------------------------------------------------------------ message composition
    def _plan_text(self, sig: dict) -> str:
        lv, hz = sig["levels"], sig["horizon"]
        targets = lv["target_primary"] + (f" / {lv['target_secondary']}" if lv.get("target_secondary") else "")
        return (f"Entry zone {lv['entry_min']}-{lv['entry_max']} | stop / invalidation {lv['stop_level']} | target "
                f"{targets} | horizon {hz['min_sessions']}-{hz['max_sessions']} sessions | entry valid until "
                f"{_when(hz['entry_expires_at'])} | group signal {sig['state']}"
                + (" (DEMO)" if sig.get("is_demo") else ""))

    def _market_text(self, symbol: str | None, sig: dict | None, now: datetime) -> str:
        q = self.signals.get_quote(symbol) if symbol else None
        if q:
            prov = q["provenance"]
            age = _age((now - parse_utc(prov["ts_received"])).total_seconds())
            show = self.demo or prov.get("feed") == "demo" or prov.get("is_demo")
            head = (f"Last APEX quote {q['price']} ({feed_label(prov)}), received {age} ago." if show else
                    f"APEX last received a {feed_label(prov)} quote {age} ago; check your broker for the current price.")
            return f"{head} {DATA_CAVEAT}"
        if sig:
            prov = sig["quote"]["provenance"]
            return (f"No fresh APEX quote for {symbol}; the signal's quote was {sig['quote']['price']} "
                    f"({feed_label(prov)}) as of {_when(prov['ts_received'])}. Market data may be offline - your "
                    f"record is still saved. {DATA_CAVEAT}")
        return (f"No recent APEX quote for {symbol} (market data may be offline). Your record is still saved; risk "
                f"updates resume when data is fresh. {DATA_CAVEAT}")

    def _warnings_text(self, warnings: list[str], pos: dict) -> str:
        out = []
        if "OFF_PLAN_ENTRY" in warnings or pos.get("off_plan"):
            why = ", ".join(OFF_PLAN_TEXT.get(r, r.lower()) for r in pos.get("off_plan_reasons", []))
            out.append(f"OFF-PLAN ENTRY: reassessment required ({why}). The signal's original probability label "
                       "does not apply to your entry.")
        for w in warnings:
            if w.startswith("SIGNAL_ALREADY_"):
                out.append(f"The group signal is already {w.removeprefix('SIGNAL_ALREADY_')}; APEX treats your "
                           "position as a manual watch with reassessment.")
        return "\n".join(out)

    def _position_reply(self, ctx: _Ctx, event: dict, res: RecordResult, *, verb: str,
                        notice: str | None = None) -> Reply:
        pos = res.position or {}
        sym = pos.get("symbol") or event.get("symbol") or "?"
        fill = event.get("fill")
        dup = res.status == "duplicate"
        if dup:
            title = f"Already recorded - duplicate ignored ({sym})"
            desc = "This exact submission was saved earlier, so no second fill was created."
        elif fill:
            title = f"Saved privately: {verb} {fill['quantity']} {sym} @ {_px(fill['price'])}"
            desc = notice or ""
        else:
            title = f"Saved privately: {verb} {sym}"
            desc = notice or ""
        if self.demo:
            title = "[DEMO] " + title
        fields = []
        if fill and not dup:
            notional = Decimal(fill["price"]) * Decimal(fill["quantity"])
            fees = Decimal(fill["fees"])
            fields.append(_field("Estimated notional", _money(notional) + (f" + {_money(fees)} fees" if fees else ""),
                                 True))
            fields.append(_field("Fill time", _when(fill["fill_at"]), True))
        if pos:
            state_line = (f"{_qty(pos)}, average cost {_px(pos['avg_cost'])}, {pos['state']}"
                          if pos["state"] != "WATCHING" else "watch-only (no shares)")
            fields.append(_field("Your position", f"{state_line} | ref `{_short(pos['position_id'])}`"))
            if Decimal(pos.get("realized_pnl") or "0") != 0 or verb.startswith("SELL"):
                fields.append(_field("Realized P&L (estimated, after fees)", _money(Decimal(pos["realized_pnl"])), True))
        sig = self.signals.latest_signal(pos["signal_id"]) if pos.get("signal_id") else None
        fields.append(_field("Signal plan / invalidation", self._plan_text(sig) if sig else
                             "Manual position - no APEX signal linked, so there is no published stop or target."))
        fields.append(_field("Market data", self._market_text(sym, sig, ctx.now)))
        warn = self._warnings_text(res.warnings, pos) if pos else ""
        if warn:
            fields.append(_field("Review", warn))
        color = AMBER if warn else (SLATE if dup else GREEN)
        return Reply(embeds=[_embed(title, desc, fields, color, self._footer())],
                     code="DUPLICATE" if dup else "PERSISTED")

    def _attach_private(self, ctx: _Ctx, reply: Reply) -> None:
        """Show notifications that were held because DMs are off/blocked (never in a shared channel)."""
        try:
            rows = self.members.pending_private(ctx.user_id or "")
        except Exception:
            return
        if not rows:
            return
        rows = sorted(rows, key=lambda r: r.created_at, reverse=True)
        budget = EMBED_TOTAL_MAX - sum(embed_length(e) for e in reply.embeds)
        slots = EMBEDS_MAX - len(reply.embeds) - 1
        older: list[str] = []
        shown = 0
        for row in rows:
            msg = None
            try:
                msg = render(row.payload, self.secret).to_discord()
            except Exception:
                msg = None
            embeds = [clamp_embed(e) for e in (msg or {}).get("embeds", [])]
            need = sum(embed_length(e) for e in embeds)
            if embeds and shown < MAX_PRIVATE_EMBEDS and len(embeds) <= slots and need <= budget - 400:
                reply.embeds.extend(embeds)
                budget -= need
                slots -= len(embeds)
                shown += 1
                for comp_row in (msg or {}).get("components", []):
                    if len(reply.rows) < ROWS_MAX:
                        reply.rows.append([Button(c.get("label", "Open"), c["custom_id"], c.get("style", 1))
                                           for c in comp_row.get("components", []) if c.get("custom_id")])
            else:
                older.append(self._summary_line(row))
        try:
            dm_on = bool(self.members.prefs(ctx.user_id or "").get("dm_opt_in"))
        except Exception:
            dm_on = False
        tip = ("Discord refused the DM (or you muted that symbol): enable *Allow direct messages from server members* "
               "for this server to receive them directly." if dm_on else
               "Your APEX DMs are off: turn them on with /apex_notifications dms:True (and allow DMs from this "
               "server's members) to receive them directly.")
        header = (f"You have {len(rows)} private update(s) that could not be delivered by DM, shown here privately. "
                  f"Tip: {tip}")
        if older:
            reply.embeds.append(_embed("Older private updates", "\n".join(older), color=SLATE,
                                       footer="Private to you. Self-reported entries are not broker records."))
        reply.content = clip((reply.content + "\n\n" if reply.content else "") + header, CONTENT_MAX)
        reply.private_outbox_ids = [r.id for r in rows]
        reply.private_user_id = ctx.user_id

    @staticmethod
    def _summary_line(row: OutboxRow) -> str:
        p = row.payload.get("payload", {})
        a = p.get("assessment")
        if row.kind == "POSITION_UPDATE" and a:
            gated = a.get("compliance_gated", True)
            text = STATUS_TEXT.get(a.get("status"), (a.get("status"), a.get("status")))[0 if gated else 1]
            return f"- {a.get('symbol')}: {text} (as of {_when(a.get('asof_at'))})"
        return f"- {row.kind.replace('_', ' ').title()} ({_when(row.created_at)})"

    def after_reply(self, reply: Reply) -> Reply | None:
        """Call after `reply` was delivered: marks its held notifications shown (release_deferred_for_user).

        Anything released that was not part of `reply` (it arrived in between) is returned as one more private
        follow-up so nothing is marked shown without being shown.
        """
        if not reply.private_outbox_ids or not reply.private_user_id:
            return None
        released = self.members.release_private(reply.private_user_id, self.clock())
        extra = [r for r in released if r.id not in set(reply.private_outbox_ids)]
        if not extra:
            return None
        out = Reply(content=f"{len(extra)} more private update(s) arrived just now:", code="PRIVATE_UPDATES")
        for r in extra[:MAX_PRIVATE_EMBEDS]:
            try:
                out.embeds.extend(render(r.payload, self.secret).to_discord().get("embeds", []))
            except Exception:
                out.content += "\n" + self._summary_line(r)
        for r in extra[MAX_PRIVATE_EMBEDS:]:
            out.content += "\n" + self._summary_line(r)
        return out

    # ------------------------------------------------------------------ informational commands
    def help(self, actor: Actor) -> Reply:
        def go(ctx: _Ctx) -> Reply:
            lines = [
                "**APEX bot - every reply is private to you.** Research only; APEX never places orders.",
                "",
                "**Signals**: /apex_signals (open group signals) - alert buttons: *Record My Entry*, *Watch*, "
                "*View Evidence*, *Mute Symbol*",
                "**Your positions**: /apex_entry - /apex_add - /apex_reduce - /apex_close - /apex_positions - "
                "/apex_correct - /apex_remove - /apex_watch - /apex_unwatch",
                "**Notifications**: /apex_notifications (DMs on/off, high-priority @mentions, mute/unmute symbols)",
                "**Your data**: /apex_export (private JSON copy) - /apex_delete_my_data (two-step, permanent)",
                "**Status**: /apex_health",
                "",
                "Prices: plain numbers with a dot (181.25). Fill time: blank/'now', HH:MM or YYYY-MM-DD HH:MM "
                "(New York time) or ISO with zone.",
                "Your entries are **self-reported**, not broker records. Shared alerts never contain anyone's fills; "
                "private updates come by DM only if you opt in, otherwise they are held and shown in your next "
                "APEX reply.",
            ]
            if self.demo:
                lines.append("\n**DEMO MODE** - fictional tickers and synthetic prices, not market data.")
            if ctx.duid in self.owner_ids:
                lines.append("Owner: /apex_admin_status (counts only), /apex_admin_restore.")
            return Reply(content="\n".join(lines), code="HELP")
        return self._run(actor, go)

    def health(self, actor: Actor) -> Reply:
        def go(ctx: _Ctx) -> Reply:
            rt = self.runtime_status() or {}
            try:
                open_sigs = len(self.signals.list_signals(OPEN_SIGNAL_STATES, limit=100))
            except Exception:
                open_sigs = None
            counts = {}
            try:
                counts = self.members.counts()
            except Exception:
                counts = {}
            lq = counts.get("latest_quote_received_at")
            data = (f"latest APEX quote received {_age((ctx.now - parse_utc(lq)).total_seconds())} ago" if lq else
                    "no APEX quotes received yet (market data offline or not configured) - you can still record "
                    "and edit your positions")
            mode = self.release_mode + (" - synthetic data, NOT market quotes" if self.demo else "")
            lines = [
                "**APEX bot is online.**",
                f"Mode: {mode}",
                f"Position store: {self.backend_label}",
                f"Open group signals: {open_sigs if open_sigs is not None else 'unknown'}",
                f"Market data: {data}",
            ]
            if rt.get("delivery_last_run_at"):
                lines.append(f"Alert delivery: last run {_when(rt['delivery_last_run_at'])}"
                             + (f", paused by Discord rate limit until {_when(rt['rate_limited_until'])}"
                                if rt.get("rate_limited_until") else ""))
            lines.append("APEX does not place orders. This status is informational.")
            return Reply(content="\n".join(lines), code="HEALTH")
        return self._run(actor, go)

    def signals_list(self, actor: Actor) -> Reply:
        def go(ctx: _Ctx) -> Reply:
            sigs = self.signals.list_signals(OPEN_SIGNAL_STATES, limit=10)
            if not sigs:
                return Reply(content="No open APEX signals right now. APEX is designed to say NO TRADE when the "
                                     "evidence is not there.", code="NO_SIGNALS")
            muted = set(self.members.prefs(ctx.user_id or "").get("muted_symbols", [])) if ctx.user_id else set()
            lines = []
            rows: list[list[Button]] = []
            for s in sigs:
                lv, hz = s["levels"], s["horizon"]
                demo = "[DEMO] " if s.get("is_demo") else ""
                lines.append(
                    f"{demo}**LONG {s['symbol']}** ({s['state']}) entry {lv['entry_min']}-{lv['entry_max']}, stop "
                    f"{lv['stop_level']}, target {lv['target_primary']}, {lv['reward_to_risk']}R net, "
                    f"{hz['min_sessions']}-{hz['max_sessions']} sessions, entry valid until {_when(hz['entry_expires_at'])}"
                    f"\n  quality {s['quality_score']:.0f}/100 (rank, not a probability) | "
                    f"{confidence_text(s['confidence'])} | `{s['id']}`" + (" | muted" if s["symbol"] in muted else ""))
                if len(rows) < ROWS_MAX:
                    rows.append([Button(f"Record entry {s['symbol']}", components.make(self.secret, "entry", s["id"]), 1),
                                 Button(f"Watch {s['symbol']}", components.make(self.secret, "watch", s["id"])),
                                 Button(f"Evidence {s['symbol']}", components.make(self.secret, "evidence", s["id"]))])
            e = _embed("Open APEX signals", "\n".join(lines), color=VIOLET,
                       footer=self._footer(f"Group research, not personal advice. {DISCLAIMER}"))
            return Reply(embeds=[e], rows=rows, code="SIGNALS")
        return self._run(actor, go)

    def positions_view(self, actor: Actor, *, position: str = "", include_closed: bool = False) -> Reply:
        def go(ctx: _Ctx) -> Reply:
            if position.strip():
                return self._position_detail(ctx, self._resolve(ctx, position))
            mine = self.positions.positions(ctx.duid, include_closed=include_closed)
            sync = self.members.sync_status(ctx.duid)
            lines = []
            for p in mine[:25]:
                sig = f"signal `{p['signal_id']}`" if p.get("signal_id") else "manual"
                if p["state"] == "WATCHING":
                    body = "watching"
                else:
                    body = f"{_qty(p)} @ avg {_px(p['avg_cost'])}"
                    if Decimal(p["realized_pnl"]) != 0:
                        body += f", realized {_money(Decimal(p['realized_pnl']))}"
                flag = " | OFF-PLAN" if p.get("off_plan") else ""
                lines.append(f"`{_short(p['position_id'])}` **{p['symbol']}** {p['state']} - {body} ({sig}){flag}")
            if len(mine) > 25:
                lines.append(f"... and {len(mine) - 25} more (use /apex_export for everything)")
            for q in sync["pending"]:
                f = q.get("fill") or {}
                lines.append(f"Pending sync (NOT saved yet): {q.get('event_type')} {f.get('quantity', '')} "
                             f"{q.get('symbol') or ''} @ {f.get('price', '')} - queued {_when(q.get('queued_at'))}")
            for code in sync["rejected"]:
                lines.append(f"A queued entry was rejected on sync: {friendly(code)}")
            if not lines:
                return Reply(content="You have no saved APEX positions or watches. Use /apex_entry or the *Record My "
                                     "Entry* button on an alert.", code="NO_POSITIONS")
            title = ("[DEMO] " if self.demo else "") + "Your positions (private)"
            e = _embed(title, "\n".join(lines), color=SLATE,
                       footer=self._footer("Refs work in /apex_add, /apex_reduce, /apex_close, /apex_correct. "
                                           f"{SELF_REPORTED}"))
            return Reply(embeds=[e], code="POSITIONS")
        return self._run(actor, go)

    def _position_detail(self, ctx: _Ctx, pos: dict) -> Reply:
        fields = [_field("State", pos["state"], True), _field("Quantity", pos["total_quantity"], True),
                  _field("Average cost (FIFO)", _px(pos["avg_cost"]), True),
                  _field("Realized P&L (est.)", _money(Decimal(pos["realized_pnl"])), True),
                  _field("Fees", _money(Decimal(pos["fees_total"])), True),
                  _field("Opened", _when(pos.get("opened_at")), True)]
        if pos.get("closed_at"):
            fields.append(_field("Closed", _when(pos["closed_at"]), True))
        lots = [f"`{lot['lot_id'][:8]}` {lot['quantity_remaining']}/{lot['quantity_initial']} sh @ "
                f"{_px(lot['fill_price'])} ({_when(lot['fill_at'])})" for lot in pos.get("lots", [])[:12]]
        if lots:
            fields.append(_field("Open lots (remaining/initial)", "\n".join(lots)))
        try:
            events = self._history().events(ctx.duid, pos["position_id"])
        except InputError:
            events = []
        hist = []
        for ev in events[-15:]:
            f = ev.get("fill")
            if f:
                hist.append(f"`{ev['event_id'][:8]}` {ev['event_type']} {f['quantity']} @ {_px(f['price'])} "
                            f"({_when(f['fill_at'])})")
            elif ev.get("correction"):
                c = ev["correction"]
                what = "void" if c.get("void") else "replace"
                hist.append(f"`{ev['event_id'][:8]}` {ev['event_type']} ({what}) -> `{c['target_event_id'][:8]}`")
            else:
                hist.append(f"`{ev['event_id'][:8]}` {ev['event_type']}")
        if hist:
            fields.append(_field("History (refs for /apex_correct)", "\n".join(hist)))
        sig = self.signals.latest_signal(pos["signal_id"]) if pos.get("signal_id") else None
        fields.append(_field("Signal plan / invalidation", self._plan_text(sig) if sig else "Manual position"))
        fields.append(_field("Market data", self._market_text(pos["symbol"], sig, ctx.now)))
        warn = self._warnings_text([], pos)
        if warn:
            fields.append(_field("Review", warn))
        title = ("[DEMO] " if self.demo else "") + f"{pos['symbol']} position `{_short(pos['position_id'])}`"
        rows = []
        if pos["state"] in ACTIVE_POSITION_STATES:
            rows.append([Button("Update this position", components.make(self.secret, "posupd", pos["position_id"]), 1)])
        return Reply(embeds=[_embed(title, "", fields, AMBER if warn else SLATE, self._footer())], rows=rows,
                     code="POSITION_DETAIL")

    # ------------------------------------------------------------------ position-affecting commands
    def _fill(self, ctx: _Ctx, price: object, shares: object, fill_time: str | None, fees: object) -> dict:
        return {"price": parse_price(price), "quantity": parse_quantity(shares), "fees": parse_fees(fees),
                "fill_at": iso_utc(parse_fill_time(fill_time, ctx.now))}

    def _buy(self, ctx: _Ctx, *, symbol: str, signal_id: str | None, fill: dict, notes: str, key: str) -> Reply:
        existing = self._active_for_signal(ctx, signal_id) if signal_id else None
        event: dict = self._stamp({"client_idempotency_key": key, "source": "discord", "fill": fill}, ctx.actor.interaction_id)
        notice = None
        if existing is None:
            event.update({"event_type": "OPEN", "symbol": symbol})
            if signal_id:
                event["signal_id"] = signal_id
        else:
            if existing["symbol"] != symbol:
                raise ServiceError("SYMBOL_MISMATCH", f"signal is for {existing['symbol']}, not {symbol}")
            event["position_id"] = existing["position_id"]
            if existing["state"] == "WATCHING":
                event["event_type"] = "OPEN"
                notice = "Your watch on this signal is now an open position."
            else:
                event["event_type"] = "ADD"
                notice = ("You already hold a position from this signal, so this was recorded as an additional buy "
                          "(ADD). Your earlier fills and cost basis are kept.")
        if notes:
            event["notes"] = notes
        res, queued = self._record(ctx, event)
        if queued:
            return queued
        assert res is not None
        return self._position_reply(ctx, event, res, verb="BUY", notice=notice)

    def entry(self, actor: Actor, *, ticker: str, price: object, shares: object, signal_id: str = "",
              fill_time: str = "now", fees: object = 0, notes: str = "") -> Reply:
        def go(ctx: _Ctx) -> Reply:
            symbol = parse_symbol(ticker)
            fill = self._fill(ctx, price, shares, fill_time, fees)
            note = parse_notes(notes)
            sid, legacy = classify_signal_ref(signal_id)
            if legacy:
                note = clip(f"starter signal ref {legacy}" + (f" | {note}" if note else ""), 280)
            return self._buy(ctx, symbol=symbol, signal_id=sid, fill=fill, notes=note, key=self._key(ctx))
        return self._run(actor, go)

    def _change(self, ctx: _Ctx, *, position: str, event_type: str, verb: str, states: set[str], price: object,
                shares: object | None, fill_time: str, fees: object, notes: str) -> Reply:
        pos = self._resolve(ctx, position, states=states)
        et = event_type
        if et == "ADD" and pos["state"] == "WATCHING":
            et = "OPEN"
        qty = pos["total_quantity"] if et == "CLOSE" else shares
        fill = self._fill(ctx, price, qty, fill_time, fees)
        event = self._stamp({"client_idempotency_key": self._key(ctx), "event_type": et,
                             "position_id": pos["position_id"], "source": "discord", "fill": fill},
                            ctx.actor.interaction_id)
        note = parse_notes(notes)
        if note:
            event["notes"] = note
        res, queued = self._record(ctx, event)
        if queued:
            return queued
        assert res is not None
        return self._position_reply(ctx, event, res, verb=verb)

    def add(self, actor: Actor, *, position: str, price: object, shares: object, fill_time: str = "now",
            fees: object = 0, notes: str = "") -> Reply:
        return self._run(actor, lambda ctx: self._change(
            ctx, position=position, event_type="ADD", verb="BUY", states=ACTIVE_POSITION_STATES, price=price,
            shares=shares, fill_time=fill_time, fees=fees, notes=notes))

    def reduce(self, actor: Actor, *, position: str, price: object, shares: object, fill_time: str = "now",
               fees: object = 0, notes: str = "") -> Reply:
        return self._run(actor, lambda ctx: self._change(
            ctx, position=position, event_type="PARTIAL_EXIT", verb="SELL", states=HOLDING_STATES, price=price,
            shares=shares, fill_time=fill_time, fees=fees, notes=notes))

    def close(self, actor: Actor, *, position: str, price: object, fill_time: str = "now", fees: object = 0,
              notes: str = "") -> Reply:
        return self._run(actor, lambda ctx: self._change(
            ctx, position=position, event_type="CLOSE", verb="SELL (close)", states=HOLDING_STATES, price=price,
            shares=None, fill_time=fill_time, fees=fees, notes=notes))

    def watch(self, actor: Actor, *, signal_id: str = "", ticker: str = "") -> Reply:
        def go(ctx: _Ctx) -> Reply:
            sid = signal_id.strip()
            if sid:
                if not SIGNAL_ID_RE.fullmatch(sid):
                    raise InputError("UNKNOWN_SIGNAL", "Pick a signal from the list (sig_ followed by 24 hex characters).")
                return self._watch_signal(ctx, sid)
            symbol = parse_symbol(ticker)
            mine = self.positions.positions(ctx.duid, include_closed=False)
            if any(p["symbol"] == symbol and p["state"] == "WATCHING" and not p.get("signal_id") for p in mine):
                return Reply(content=f"You are already watching {symbol}. Nothing changed.", code="ALREADY_WATCHING")
            event = self._stamp({"client_idempotency_key": self._key(ctx), "event_type": "WATCH", "symbol": symbol,
                                 "source": "discord"}, ctx.actor.interaction_id)
            res, queued = self._record(ctx, event)
            if queued:
                return queued
            assert res is not None
            return self._position_reply(ctx, event, res, verb="WATCH",
                                        notice="Manual watch: APEX has no published plan for this ticker.")
        return self._run(actor, go)

    def _watch_signal(self, ctx: _Ctx, sid: str) -> Reply:
        sig = self.signals.latest_signal(sid)
        if sig is None:
            raise ServiceError("UNKNOWN_SIGNAL", "that signal id is not a published APEX signal")
        if sig["state"] not in OPEN_SIGNAL_STATES:
            raise InputError("SIGNAL_NOT_OPEN", f"That {sig['symbol']} signal is {sig['state']}.")
        existing = self._active_for_signal(ctx, sid)
        if existing:
            return Reply(content=f"You are already tracking {existing['symbol']} from this signal ({existing['state']}, "
                                 f"ref `{_short(existing['position_id'])}`). Nothing changed.", code="ALREADY_WATCHING")
        event = self._stamp({"client_idempotency_key": self._key(ctx), "event_type": "WATCH", "signal_id": sid,
                             "source": "discord"}, ctx.actor.interaction_id)
        res, queued = self._record(ctx, event)
        if queued:
            return queued
        assert res is not None
        return self._position_reply(ctx, event, res, verb="WATCH",
                                    notice="Watching privately. APEX tracks this group signal for you; private updates "
                                           "arrive by DM if enabled, otherwise in your next APEX reply.")

    def unwatch(self, actor: Actor, *, position: str) -> Reply:
        def go(ctx: _Ctx) -> Reply:
            pos = self._resolve(ctx, position, states={"WATCHING"})
            event = self._stamp({"client_idempotency_key": self._key(ctx), "event_type": "UNWATCH",
                                 "position_id": pos["position_id"], "source": "discord"}, ctx.actor.interaction_id)
            res, queued = self._record(ctx, event)
            if queued:
                return queued
            assert res is not None
            return self._position_reply(ctx, event, res, verb="UNWATCH", notice="Stopped watching. History is kept.")
        return self._run(actor, go)

    # ------------------------------------------------------------------ destructive actions (confirm first)
    def _confirm_prompt(self, ctx: _Ctx, op: str, payload: dict, title: str, body: str, *,
                        confirm_label: str = "Confirm", replace_original: bool = False) -> Reply:
        nonce = self.pending.put(PendingAction(op, ctx.duid, ctx.actor.interaction_id, ctx.now + CONFIRM_TTL, payload))
        rows = [[Button(confirm_label, components.make(self.secret, "confirm", f"{op}.{nonce}"), 4),
                 Button("Cancel", components.make(self.secret, "cancel", nonce), 2)]]
        e = _embed(("[DEMO] " if self.demo else "") + title, body, color=CORAL,
                   footer=self._footer("Nothing changes until you confirm. This prompt expires in 10 minutes."))
        return Reply(embeds=[e], rows=rows, code="CONFIRM_REQUIRED", replace_original=replace_original)

    @staticmethod
    def _describe_event(ev: dict) -> str:
        f = ev.get("fill")
        if f:
            return f"{ev['event_type']} {f['quantity']} @ {_px(f['price'])} ({_when(f['fill_at'])}) `{ev['event_id'][:8]}`"
        return f"{ev['event_type']} `{ev['event_id'][:8]}`"

    @staticmethod
    def _pick_event(cands: list[dict], ref: str | None) -> dict:
        r = (ref or "").strip().strip("#`").lower()
        if not r:
            if len(cands) == 1:
                return cands[0]
            raise InputError("NO_FILL_SELECTED", "Choose which fill to correct (start typing to see the list).")
        hits = [e for e in cands if e["event_id"] == r] or [e for e in cands if len(r) >= 4 and e["event_id"].startswith(r)]
        if not hits:
            raise InputError("UNKNOWN_TARGET", "That fill reference is not part of this position.")
        if len(hits) > 1:
            raise InputError("NO_FILL_SELECTED", "That reference matches several fills; pick one from the list.")
        return hits[0]

    def correct(self, actor: Actor, *, position: str, fill: str = "", action: str = "void", price: object = None,
                shares: object = None, fill_time: str = "", fees: object = None, reason: str = "") -> Reply:
        def go(ctx: _Ctx) -> Reply:
            act = (action or "void").strip().lower()
            if act not in ("void", "replace", "undo"):
                raise InputError("INVALID_ACTION", "Action must be void, replace or undo.")
            pos = self._resolve(ctx, position)
            events = self._history().events(ctx.duid, pos["position_id"])
            cands = [e for e in events if (e["event_type"] == "CORRECT") == (act == "undo")
                     and (act == "undo" or e["event_type"] in FILL_TYPES)]
            target = self._pick_event(cands, fill)
            corr: dict = {"target_event_id": target["event_id"], "void": act == "void"}
            why = parse_notes(reason)
            if why:
                corr["reason"] = clip(why, 200)
            if act == "replace":
                orig = target.get("fill") or {}
                if price in (None, "") and shares in (None, "") and not (fill_time or "").strip() and fees in (None, ""):
                    raise InputError("MISSING_REPLACEMENT", "Give the corrected price and/or shares (and optionally "
                                                            "time/fees).")
                corr["replacement"] = {
                    "price": parse_price(price) if price not in (None, "") else orig["price"],
                    "quantity": parse_quantity(shares) if shares not in (None, "") else orig["quantity"],
                    "fees": parse_fees(fees) if fees not in (None, "") else orig.get("fees", "0"),
                    "fill_at": iso_utc(parse_fill_time(fill_time, ctx.now)) if (fill_time or "").strip()
                    else orig["fill_at"],
                }
            event = {"event_type": "CANCEL_CORRECTION" if act == "undo" else "CORRECT",
                     "position_id": pos["position_id"], "source": "discord", "correction": corr}
            what = {"void": "VOID (remove the effect of)", "replace": "REPLACE",
                    "undo": "UNDO the correction"}[act]
            body = f"{what}: {self._describe_event(target)} on your **{pos['symbol']}** position."
            if act == "replace":
                r = corr["replacement"]
                body += f"\nNew values: {r['quantity']} @ {_px(r['price'])}, fees {r['fees']}, {_when(r['fill_at'])}"
            body += ("\nThe original record stays in your audit history; corrections never rewrite earlier "
                     "assessments.")
            return self._confirm_prompt(ctx, "correct", {"event": event}, "Confirm correction", body,
                                        confirm_label="Confirm correction")
        return self._run(actor, go)

    def remove(self, actor: Actor, *, position_id: str | int) -> Reply:
        """Starter-compatible /apex_remove: a VOID correction of the member's own single entry (or an unwatch)."""
        def go(ctx: _Ctx) -> Reply:
            pos = self._resolve(ctx, str(position_id))
            if pos["state"] == "CLOSED":
                raise InputError("NOT_OPEN", "That entry is already closed/removed.")
            if pos["state"] == "WATCHING":
                event = {"event_type": "UNWATCH", "position_id": pos["position_id"], "source": "discord"}
                body = f"Stop watching **{pos['symbol']}** (`{_short(pos['position_id'])}`)?"
            else:
                fills = [e for e in self._history().events(ctx.duid, pos["position_id"]) if e["event_type"] in FILL_TYPES]
                if len(fills) != 1 or fills[0]["event_type"] != "OPEN":
                    raise InputError("NOT_REMOVABLE", f"Your {pos['symbol']} position has {len(fills)} fills. Use "
                                                      "/apex_correct to void a specific one, or /apex_close.")
                event = {"event_type": "CORRECT", "position_id": pos["position_id"], "source": "discord",
                         "correction": {"target_event_id": fills[0]["event_id"], "void": True,
                                        "reason": "removed with /apex_remove"}}
                body = (f"Remove your entry {self._describe_event(fills[0])} for **{pos['symbol']}**? It is voided "
                        "(kept in your audit history) and no longer counts in your positions.")
            return self._confirm_prompt(ctx, "remove", {"event": event}, "Confirm removal", body,
                                        confirm_label="Remove entry")
        return self._run(actor, go)

    def delete_my_data(self, actor: Actor) -> Reply:
        def go(ctx: _Ctx) -> Reply:
            n = len(self.positions.positions(ctx.duid, include_closed=True))
            body = (f"This permanently deletes **your** APEX data: {n} position(s)/watch(es) with all fills and "
                    "corrections, privately held notifications, muted symbols and notification preferences. Shared "
                    "group signals and aggregate signal statistics are not affected.\n\nStep 1 of 2. Continue?")
            return self._confirm_prompt(ctx, "del1", {}, "Delete my APEX data", body, confirm_label="Continue")
        return self._run(actor, go)

    def _on_confirm(self, ctx: _Ctx, subject: str) -> Reply:
        op, _, nonce = subject.partition(".")
        pa = self.pending.take(nonce, ctx.duid, ctx.now)
        if pa is None or pa.op != op:
            return Reply(content="This confirmation has expired or was already used. Nothing was changed - run the "
                                 "command again.", code="CONFIRM_EXPIRED", ok=False, replace_original=True)
        if op == "del1":
            body = "**Final confirmation (step 2 of 2):** this cannot be undone."
            return self._confirm_prompt(ctx, "del2", {}, "Delete my APEX data", body,
                                        confirm_label="Delete permanently", replace_original=True)
        if op == "del2":
            if not ctx.user_id:
                return Reply(content="Nothing to delete.", code="NOTHING", replace_original=True)
            n = self.members.delete_data(ctx.user_id)
            return Reply(content=f"Deleted: {n} position(s)/watch(es) and their history, held private notifications, "
                                 "mutes and preferences. DMs are now off. You can keep using APEX at any time.",
                         code="DELETED", replace_original=True)
        event = dict(pa.payload["event"])
        event["client_idempotency_key"] = f"discord:{pa.base_interaction_id}:confirm"
        self._stamp(event, pa.base_interaction_id)
        res, queued = self._record(ctx, event)
        if queued:
            queued.replace_original = True
            return queued
        assert res is not None
        verb = {"CORRECT": "CORRECTION", "CANCEL_CORRECTION": "UNDO CORRECTION", "UNWATCH": "UNWATCH"}.get(
            event["event_type"], event["event_type"])
        reply = self._position_reply(ctx, event, res, verb=verb,
                                     notice="Saved. The original record is kept in your audit history.")
        reply.replace_original = True
        return reply

    def _on_cancel(self, ctx: _Ctx, nonce: str) -> Reply:
        self.pending.take(nonce, ctx.duid, ctx.now)
        return Reply(content="Cancelled. Nothing was changed.", code="CANCELLED", replace_original=True)

    # ------------------------------------------------------------------ preferences and data rights
    def notifications(self, actor: Actor, *, dms: bool | None = None, high_priority_mentions: bool | None = None,
                      mute: str = "", unmute: str = "") -> Reply:
        def go(ctx: _Ctx) -> Reply:
            uid = ctx.user_id or ""
            changed = []
            if dms is not None:
                self.members.set_dm_opt_in(uid, dms)
                changed.append(f"DMs {'ON' if dms else 'OFF'}")
            if high_priority_mentions is not None:
                self.members.set_high_priority_mentions(uid, high_priority_mentions, ctx.now)
                changed.append(f"high-priority mentions {'ON' if high_priority_mentions else 'OFF'}")
            if mute.strip():
                sym = parse_symbol(mute)
                self.members.mute(uid, sym, ctx.now)
                changed.append(f"muted {sym}")
            if unmute.strip():
                sym = parse_symbol(unmute)
                self.members.unmute(uid, sym)
                changed.append(f"unmuted {sym}")
            p = self.members.prefs(uid)
            lines = [("Updated: " + ", ".join(changed) + ".") if changed else "Your notification settings:",
                     f"- Private DMs: **{'ON' if p['dm_opt_in'] else 'OFF'}** - position updates by DM. If Discord "
                     "blocks a DM it is held privately and shown in your next APEX reply; it is never posted to a "
                     "shared channel.",
                     f"- High-priority @mentions: **{'ON' if p['high_priority_mentions'] else 'OFF'}** - you are "
                     "@mentioned on high-conviction alerts (channel members can see that you opted in, nothing else).",
                     f"- Muted symbols: {', '.join(p['muted_symbols']) or 'none'} (no DMs/mentions for these)."]
            reply = Reply(content="\n".join(lines), code="NOTIFICATIONS")
            if dms:
                reply.test_dm = ("APEX test message: private DMs are on. Turn them off any time with "
                                 "/apex_notifications dms:False.")
            return reply
        return self._run(actor, go)

    def export(self, actor: Actor) -> Reply:
        def go(ctx: _Ctx) -> Reply:
            prefs = self.members.prefs(ctx.user_id or "")
            positions = []
            for p in self.positions.positions(ctx.duid, include_closed=True):
                item = dict(p)
                with contextlib.suppress(InputError):
                    item["events"] = self._history().events(ctx.duid, p["position_id"])
                positions.append(item)
            data = {
                "format": "apex-member-export-1", "exported_at": iso_utc(ctx.now), "release_mode": self.release_mode,
                "discord_user_id": ctx.duid,
                "preferences": {"dm_opt_in": prefs["dm_opt_in"], "high_priority_mentions": prefs["high_priority_mentions"],
                                "muted_symbols": prefs["muted_symbols"]},
                "positions": positions, "pending_sync": self.members.sync_status(ctx.duid),
                "held_private_notifications": len(self.members.pending_private(ctx.user_id or "")),
                "notice": "Self-reported entries - not broker records. Generated privately for you.",
            }
            text = json.dumps(data, indent=1, sort_keys=True)
            chunks = chunk_text(text, 1900)
            if len(chunks) == 1:
                return Reply(content=f"Your APEX data (private):\n```json\n{text}\n```", code="EXPORT")
            name = f"apex-export-{ctx.now.strftime('%Y%m%dT%H%M%SZ')}.json"
            return Reply(content=f"Your APEX data export is attached ({len(text)} characters, private to you).",
                         files=[(name, text.encode())], code="EXPORT")
        return self._run(actor, go)

    # ------------------------------------------------------------------ owner-only
    def _require_owner(self, ctx: _Ctx) -> None:
        if not self.owner_ids:
            raise self._refuse("NOT_OWNER", "No APEX owner is configured (APEX_OWNER_DISCORD_IDS).")
        if ctx.duid not in self.owner_ids:
            raise self._refuse("NOT_OWNER", "This command is limited to the APEX owner.")

    def admin_status(self, actor: Actor) -> Reply:
        def go(ctx: _Ctx) -> Reply:
            self._require_owner(ctx)
            c = self.members.counts()
            rt = self.runtime_status() or {}
            sigs = self.signals.list_signals(OPEN_SIGNAL_STATES, limit=200)
            ob = c.get("outbox_by_state", {})
            fields = [
                _field("Members", f"{c['members_total'] - c['members_revoked']} active, {c['members_revoked']} revoked; "
                                  f"{c['members_dm_opt_in']} DM opt-in; {c['members_high_priority_mentions']} mention "
                                  "opt-in"),
                _field("Positions (all members, counts only)",
                       ", ".join(f"{k} {v}" for k, v in sorted(c.get("positions_by_state", {}).items())) or "none"),
                _field("Open signals", str(len(sigs)), True),
                _field("Outbox", ", ".join(f"{k} {v}" for k, v in sorted(ob.items())) or "empty", True),
                _field("Dead letters", str(ob.get("dead_letter", 0)), True),
                _field("Last delivery", _when(c.get("outbox_last_sent_at")), True),
                _field("Pending sync (spool)", str(c.get("spool_pending", 0)), True),
                _field("Latest quote", _when(c.get("latest_quote_received_at")), True),
                _field("Delivery loop", (f"last run {_when(rt.get('delivery_last_run_at'))}; "
                                         f"last error {rt.get('delivery_last_error') or 'none'}"
                                         + (f"; rate-limited until {_when(rt['rate_limited_until'])}"
                                            if rt.get("rate_limited_until") else ""))),
                _field("Channels", (f"configured: {', '.join(rt.get('channels_configured', [])) or 'none'}; missing: "
                                    f"{', '.join(rt.get('channels_missing', [])) or 'none'}")),
                _field("Runtime", f"mode {self.release_mode}; store {self.backend_label}; schema "
                                  f"v{c.get('schema_version')}; lock {rt.get('instance_lock', 'n/a')}"),
            ]
            e = _embed("APEX admin status (aggregates only)", "Member fills are never shown here.", fields, SLATE,
                       footer="Owner-only. Counts only - no member positions, fills or identities.")
            return Reply(embeds=[e], code="ADMIN_STATUS")
        return self._run(actor, go, attach_private=False)

    def admin_restore(self, actor: Actor, *, target_discord_id: str) -> Reply:
        def go(ctx: _Ctx) -> Reply:
            self._require_owner(ctx)
            ok = self.members.restore(str(target_discord_id))
            return Reply(content="Member access restored." if ok else "That member is not revoked.",
                         code="RESTORED" if ok else "NOT_REVOKED")
        return self._run(actor, go, attach_private=False)

    def member_left(self, discord_user_id: str) -> None:
        self.members.revoke(str(discord_user_id), self.clock())

    # ------------------------------------------------------------------ components (buttons) and modals
    def component_kind(self, custom_id: str) -> str:
        """modal | update | message - lets the gateway pick the right acknowledgement without I/O."""
        parsed = components.parse(self.secret, custom_id)
        if not parsed:
            return "message"
        action, subject = parsed
        if action == "entry" or (action == "posupd" and subject.split(".", 1)[0] in MODAL_OPS and "." in subject):
            return "modal"
        if action in ("confirm", "cancel"):
            return "update"
        return "message"

    def component(self, actor: Actor, custom_id: str) -> Reply:
        parsed = components.parse(self.secret, custom_id)
        if parsed is None:
            return self._run(actor, lambda ctx: Reply(content="This button is invalid or has expired. Nothing was "
                                                              "recorded.", code="INVALID_COMPONENT", ok=False),
                             allow_dm=True, attach_private=False)
        action, subject = parsed
        allow_dm = action in ("posupd", "confirm", "cancel")
        return self._run(actor, lambda ctx: self._component(ctx, action, subject), allow_dm=allow_dm)

    def _component(self, ctx: _Ctx, action: str, subject: str) -> Reply:
        if action in ("entry", "watch", "evidence") and not SIGNAL_ID_RE.fullmatch(subject):
            return Reply(content="This button is invalid. Nothing was recorded.", code="INVALID_COMPONENT", ok=False)
        if action == "entry":
            sig = self.signals.latest_signal(subject)
            if sig is None:
                raise ServiceError("UNKNOWN_SIGNAL", "that signal no longer exists")
            if sig["state"] not in OPEN_SIGNAL_STATES:
                return self._error("SIGNAL_NOT_OPEN", f"This {sig['symbol']} signal is {sig['state']}. If you bought "
                                                      f"earlier, record it with /apex_entry ticker:{sig['symbol']} "
                                                      f"signal_id:{sig['id']}.")
            return Reply(modal=self._entry_modal(sig), code="MODAL")
        if action == "watch":
            return self._watch_signal(ctx, subject)
        if action == "evidence":
            return self._evidence(subject)
        if action == "mute":
            sym = parse_symbol(subject)
            new = self.members.mute(ctx.user_id or "", sym, ctx.now)
            return Reply(content=(f"Muted {sym}: " if new else f"{sym} was already muted: ")
                         + "no DMs or @mentions about it for you; private updates are held and shown in your next "
                           f"APEX reply. Undo with /apex_notifications unmute:{sym}.", code="MUTED")
        if action == "posupd":
            return self._posupd(ctx, subject)
        if action == "confirm":
            return self._on_confirm(ctx, subject)
        if action == "cancel":
            return self._on_cancel(ctx, subject)
        return Reply(content="This button is not supported.", code="INVALID_COMPONENT", ok=False)

    def _entry_modal(self, sig: dict) -> ModalSpec:
        lv = sig["levels"]
        return ModalSpec(components.make(self.secret, "entry", sig["id"]), f"Record entry: {sig['symbol']}", (
            ModalField("price", "Your fill price (USD)", f"e.g. {lv['entry_min']} (zone {lv['entry_min']}-{lv['entry_max']})",
                       max_length=20),
            ModalField("quantity", "Shares (fractions allowed)", "e.g. 20", max_length=20),
            ModalField("fill_time", "Fill time (blank = now, New York)", "now | 10:31 | 2026-10-20 10:31",
                       required=False, max_length=40),
            ModalField("fees", "Fees (optional)", "0", required=False, max_length=12),
            ModalField("notes", "Private notes (optional)", "Only you can see these", required=False, max_length=280,
                       paragraph=True),
        ))

    def _posupd(self, ctx: _Ctx, subject: str) -> Reply:
        op, _, pid = subject.partition(".") if "." in subject else ("menu", "", subject)
        if not UUID_RE.fullmatch(pid):
            return Reply(content="This button is invalid. Nothing was recorded.", code="INVALID_COMPONENT", ok=False)
        pos = self.positions.position(ctx.duid, pid)  # ownership re-checked on every step
        if op == "menu":
            rows: list[Button]
            if pos["state"] == "WATCHING":
                rows = [Button("Record my entry", components.make(self.secret, "posupd", f"open.{pid}"), 1),
                        Button("Stop watching", components.make(self.secret, "posupd", f"unwatch.{pid}"), 2)]
            elif pos["state"] in HOLDING_STATES:
                rows = [Button("Add shares", components.make(self.secret, "posupd", f"add.{pid}"), 1),
                        Button("Sell some", components.make(self.secret, "posupd", f"reduce.{pid}"), 2),
                        Button("Close all", components.make(self.secret, "posupd", f"close.{pid}"), 4),
                        Button("Details", components.make(self.secret, "posupd", f"view.{pid}"), 2)]
            else:
                return self._position_detail(ctx, pos)
            text = (f"Update your **{pos['symbol']}** position (`{_short(pid)}`, {pos['state']}, {_qty(pos)} @ avg "
                    f"{_px(pos['avg_cost'])}). Choose an action - everything stays private.")
            return Reply(content=text, rows=[rows], code="POSUPD_MENU")
        if op == "view":
            return self._position_detail(ctx, pos)
        if op == "unwatch":
            if pos["state"] != "WATCHING":
                raise InputError("NOT_WATCHING", "That position is not watch-only.")
            event = self._stamp({"client_idempotency_key": self._key(ctx), "event_type": "UNWATCH", "position_id": pid,
                                 "source": "discord"}, ctx.actor.interaction_id)
            res, queued = self._record(ctx, event)
            if queued:
                return queued
            assert res is not None
            return self._position_reply(ctx, event, res, verb="UNWATCH")
        if op in MODAL_OPS:
            if op in ("reduce", "close", "add") and pos["state"] not in HOLDING_STATES:
                raise InputError("NOT_OPEN", f"That {pos['symbol']} position is {pos['state']}.")
            if op == "open" and pos["state"] != "WATCHING":
                raise InputError("ALREADY_OPEN", "That position already has an entry; use Add shares.")
            title = {"add": f"Add to {pos['symbol']}", "reduce": f"Sell part of {pos['symbol']}",
                     "close": f"Close all {pos['total_quantity']} {pos['symbol']}",
                     "open": f"Record entry: {pos['symbol']}"}[op]
            fields = [ModalField("price", "Fill price (USD)", "e.g. 101.25", max_length=20)]
            if op != "close":
                fields.append(ModalField("quantity", "Shares sold" if op == "reduce" else "Shares bought", "e.g. 10",
                                         max_length=20))
            fields += [ModalField("fill_time", "Fill time (blank = now, New York)", "now | 10:31 | 2026-10-20 10:31",
                                  required=False, max_length=40),
                       ModalField("fees", "Fees (optional)", "0", required=False, max_length=12),
                       ModalField("notes", "Private notes (optional)", "Only you can see these", required=False,
                                  max_length=280, paragraph=True)]
            return Reply(modal=ModalSpec(components.make(self.secret, "posupd", subject), title, tuple(fields)),
                         code="MODAL")
        return Reply(content="This button is not supported.", code="INVALID_COMPONENT", ok=False)

    def modal_submit(self, actor: Actor, custom_id: str, values: dict[str, str]) -> Reply:
        parsed = components.parse(self.secret, custom_id)
        if parsed is None or parsed[0] not in ("entry", "posupd"):
            return self._run(actor, lambda ctx: Reply(content="This form is invalid or has expired. Nothing was "
                                                              "recorded.", code="INVALID_COMPONENT", ok=False),
                             allow_dm=True, attach_private=False)
        action, subject = parsed
        if action == "entry":
            return self._run(actor, lambda ctx: self._entry_submit(ctx, subject, values))
        return self._run(actor, lambda ctx: self._posupd_submit(ctx, subject, values), allow_dm=True)

    def _entry_submit(self, ctx: _Ctx, sid: str, values: dict[str, str]) -> Reply:
        if not SIGNAL_ID_RE.fullmatch(sid):
            return Reply(content="This form is invalid. Nothing was recorded.", code="INVALID_COMPONENT", ok=False)
        sig = self.signals.latest_signal(sid)
        if sig is None or sig["state"] not in PUBLISHED_STATES:
            raise ServiceError("UNKNOWN_SIGNAL", "that signal is not a published APEX signal")
        fill = self._fill(ctx, values.get("price"), values.get("quantity"), values.get("fill_time"), values.get("fees"))
        return self._buy(ctx, symbol=sig["symbol"], signal_id=sid, fill=fill, notes=parse_notes(values.get("notes")),
                         key=self._key(ctx))

    def _posupd_submit(self, ctx: _Ctx, subject: str, values: dict[str, str]) -> Reply:
        op, _, pid = subject.partition(".")
        if op not in MODAL_OPS or not UUID_RE.fullmatch(pid):
            return Reply(content="This form is invalid. Nothing was recorded.", code="INVALID_COMPONENT", ok=False)
        et, verb, states = {"add": ("ADD", "BUY", HOLDING_STATES), "open": ("OPEN", "BUY", {"WATCHING"}),
                            "reduce": ("PARTIAL_EXIT", "SELL", HOLDING_STATES),
                            "close": ("CLOSE", "SELL (close)", HOLDING_STATES)}[op]
        return self._change(ctx, position=pid, event_type=et, verb=verb, states=states, price=values.get("price"),
                            shares=values.get("quantity"), fill_time=values.get("fill_time") or "",
                            fees=values.get("fees"), notes=values.get("notes") or "")

    def _evidence(self, sid: str) -> Reply:
        sig = self.signals.latest_signal(sid)
        if sig is None:
            raise ServiceError("UNKNOWN_SIGNAL", "that signal no longer exists")
        th, lv, hz, q = sig["thesis"], sig["levels"], sig["horizon"], sig["quote"]

        def item(r: dict) -> str:
            val = ""
            if r.get("value") is not None:
                val = f" [{r['value']}{(' ' + r['unit']) if r.get('unit') else ''}]"
            src = f" <{r['source_url']}>" if r.get("source_url") else ""
            return f"{r['text']}{val}{src}"

        fields = [
            _field("Why selected", "\n".join(f"+ {item(r)}" for r in th["reasons"])),
            _field("Counter-evidence / risks", "\n".join(f"- {item(r)}" for r in th["counter_evidence"])
                   + f"\n- {sig['risk']['gap_risk_note']}\n- Earnings: {sig['risk']['earnings_status'].replace('_', ' ')}"),
        ]
        if th.get("catalysts"):
            fields.append(_field("Catalysts", "\n".join(
                f"- {c['headline']} ({c['catalyst_type']}, {c.get('direction', 'unknown')}, available "
                f"{_when(c['available_at'])})" + (f" <{c['source_url']}>" if c.get("source_url") else "")
                for c in th["catalysts"])))
        fields += [
            _field("Calibrated probability", confidence_text(sig["confidence"])),
            _field("Quality rank (NOT a probability)", f"{sig['quality_score']:.0f}/100", True),
            _field("Regime", th["regime"]["label"], True),
            _field("Plan", self._plan_text(sig) + f" | net R/R {lv['reward_to_risk']}R"),
            _field("Event definition", f"{sig['event_definition']['type'].replace('_', ' ').lower()} within "
                                       f"{sig['event_definition']['horizon_sessions']} sessions, measured from "
                                       f"{sig['event_definition']['measured_from'].replace('_', ' ').lower()}"),
            _field("Data", f"Decision as-of {_when(sig['asof_at'])}; quote {feed_label(q['provenance'])}, age "
                           f"{_age(q['age_seconds'])} at decision; horizon basis {hz['basis'].lower()}"),
        ]
        demo = "[DEMO] " if sig.get("is_demo") else ""
        title = f"{demo}Evidence: LONG {sig['symbol']} ({sig['id']} r{sig['revision']})"
        footer = f"{sig['strategy_id']} {sig['strategy_version']} | {sig['policy_version']} | {DISCLAIMER}"
        if sig.get("is_demo"):
            footer = DEMO_FOOTER + " | " + footer
        return Reply(embeds=[_embed(title, th.get("summary", ""), fields, VIOLET, footer)], code="EVIDENCE")

    # ------------------------------------------------------------------ autocomplete (never raises)
    def position_choices(self, actor: Actor, current: str, *, states: set[str] | None = None,
                         include_closed: bool = False) -> list[tuple[str, str]]:
        if self.gate(actor) is not None:
            return []
        try:
            mine = self.positions.positions(actor.discord_user_id, include_closed=include_closed)
        except Exception:
            return []
        cur = (current or "").strip().lower()
        out = []
        for p in mine:
            if states and p["state"] not in states:
                continue
            body = "watching" if p["state"] == "WATCHING" else f"{_qty(p)} @ {_px(p['avg_cost'])}"
            label = f"{p['symbol']} {p['state']} {body} #{_short(p['position_id'])}"
            if cur and cur not in label.lower() and not p["position_id"].startswith(cur):
                continue
            out.append((clip(label, CHOICE_NAME_MAX), p["position_id"]))
        return out[:CHOICES_MAX]

    def fill_choices(self, actor: Actor, position_ref: str, current: str, *, corrections: bool = False
                     ) -> list[tuple[str, str]]:
        if self.gate(actor) is not None or not UUID_RE.fullmatch((position_ref or "").strip()):
            return []
        try:
            events = self._history().events(actor.discord_user_id, position_ref.strip())
        except Exception:
            return []
        cur = (current or "").strip().lower()
        out = []
        for ev in events:
            if corrections != (ev["event_type"] == "CORRECT") or (not corrections and ev["event_type"] not in FILL_TYPES):
                continue
            label = self._describe_event(ev).replace("`", "")
            if cur and cur not in label.lower():
                continue
            out.append((clip(label, CHOICE_NAME_MAX), ev["event_id"]))
        return out[:CHOICES_MAX]

    def signal_choices(self, actor: Actor, current: str) -> list[tuple[str, str]]:
        if self.gate(actor) is not None:
            return []
        try:
            sigs = self.signals.list_signals(OPEN_SIGNAL_STATES, limit=25)
        except Exception:
            return []
        cur = (current or "").strip().lower()
        out = []
        for s in sigs:
            label = f"{'[DEMO] ' if s.get('is_demo') else ''}{s['symbol']} {s['state']} entry {s['levels']['entry_min']}-" \
                    f"{s['levels']['entry_max']} {s['id']}"
            if cur and cur not in label.lower():
                continue
            out.append((clip(label, CHOICE_NAME_MAX), s["id"]))
        return out[:CHOICES_MAX]
