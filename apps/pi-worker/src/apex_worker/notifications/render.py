"""Deterministic Discord message rendering from structured outbox payloads.

Rendering is a pure function so payload snapshots can be tested and the dashboard signal card and the
Discord embed show identical facts. Discord limits are enforced (title 256, description 4096, 25 fields,
field name 256 / value 1024, footer 2048, 6000 total, content 2000). Shared-channel renders never contain
member positions; DM renders are only produced for the owning member.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apex_worker.notifications import components
from apex_worker.timeutil import et_label, parse_utc

GREEN, CORAL, AMBER, SLATE, VIOLET = 0x2E9E6B, 0xD9675A, 0xC9A227, 0x5B6573, 0x7A67C9
DISCLAIMER = "Research only - not investment advice. APEX places no orders. Verify prices at your broker."
DEMO_PREFIX = "[DEMO] "
DEMO_FOOTER = "DEMO MODE - fictional tickers and synthetic prices. Not market data."

FEED_LABEL = {
    ("iex", "live"): "IEX LIVE (single venue, not NBBO)",
    ("sip", "delayed"): "SIP DELAYED",
    ("sip", "historical"): "SIP HISTORICAL (delayed)",
    ("demo", "replay"): "DEMO",
    ("iex", "historical"): "IEX HISTORICAL",
}

STATUS_TEXT = {
    # status: (neutral wording used while compliance-gated, ungated wording)
    "HOLD_THESIS_INTACT": ("Thesis intact (informational)", "Hold: thesis intact"),
    "REVIEW_INVALIDATION": ("Invalidation condition met - review your plan", "Review invalidation"),
    "CONSIDER_TRIM": ("Remaining reward/risk has narrowed (informational)", "Consider trimming"),
    "CONSIDER_EXIT": ("Exit conditions of the original thesis are met (informational)", "Consider exiting"),
    "TARGET_TOUCHED_FILL_UNKNOWN": ("Target touched - fill unknown", "Target touched - fill unknown"),
    "TIME_EXIT_REVIEW": ("Time horizon reached - review", "Time exit review"),
    "ADD_CRITERIA_MET": (
        "Original entry criteria are present again (informational, not a recommendation)",
        "Add criteria met",
    ),
    "PRICE_UNCERTAIN": ("PRICE UNCERTAIN - CHECK BROKER", "PRICE UNCERTAIN - CHECK BROKER"),
    "STALE_DATA": ("Data stale - assessment paused", "Data stale - assessment paused"),
    "MANUAL_WATCH": ("Watching", "Watching"),
    "CLOSED": ("Closed", "Closed"),
}


@dataclass
class RenderedMessage:
    content: str = ""
    embeds: list[dict] = field(default_factory=list)
    components: list[dict] = field(default_factory=list)
    ephemeral: bool = False

    def to_discord(self) -> dict:
        out: dict = {"content": self.content[:2000], "embeds": self.embeds, "allowed_mentions": {"parse": []}}
        if self.components:
            out["components"] = self.components
        if self.ephemeral:
            out["flags"] = 64
        return out


def _clip(s: str, n: int) -> str:
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _field(name: str, value: str, inline: bool = True) -> dict:
    return {"name": _clip(name, 256), "value": _clip(value or "-", 1024), "inline": inline}


def _embed(title: str, description: str, color: int, fields: list[dict], footer: str) -> dict:
    e = {
        "title": _clip(title, 256),
        "description": _clip(description, 4096),
        "color": color,
        "fields": fields[:25],
        "footer": {"text": _clip(footer, 2048)},
    }
    total = (
        len(e["title"])
        + len(e["description"])
        + len(e["footer"]["text"])
        + sum(len(f["name"]) + len(f["value"]) for f in e["fields"])
    )
    while total > 6000 and e["fields"]:
        f = e["fields"].pop()
        total -= len(f["name"]) + len(f["value"])
    return e


def _when(iso: str | None) -> str:
    if not iso:
        return "n/a"
    t = parse_utc(iso)
    return f"{et_label(t)} / {t.strftime('%H:%M')} UTC"


def feed_label(prov: dict) -> str:
    return FEED_LABEL.get(
        (prov.get("feed"), prov.get("latency_class")),
        f"{prov.get('feed', '?').upper()} {prov.get('latency_class', '?').upper()}",
    )


def _buttons(secret: bytes, signal_id: str, symbol: str) -> list[dict]:
    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 1,
                    "label": "Record My Entry",
                    "custom_id": components.make(secret, "entry", signal_id),
                },
                {"type": 2, "style": 2, "label": "Watch", "custom_id": components.make(secret, "watch", signal_id)},
                {
                    "type": 2,
                    "style": 2,
                    "label": "View Evidence",
                    "custom_id": components.make(secret, "evidence", signal_id),
                },
                {"type": 2, "style": 4, "label": "Mute Symbol", "custom_id": components.make(secret, "mute", symbol)},
            ],
        }
    ]


def confidence_text(conf: dict) -> str:
    if conf["status"] == "UNVALIDATED" or conf.get("p_target_first") is None:
        return "**UNVALIDATED** - no calibrated probability (insufficient out-of-sample evidence)"
    p = conf["p_target_first"]
    lo, hi = conf.get("interval_80") or [None, None]
    rng = f" (80% interval {lo:.0%}-{hi:.0%})" if lo is not None else ""
    return (
        f"{conf['label']}: P(target before stop within horizon) = **{p:.0%}**{rng}; "
        f"n={conf.get('sample_count', 0)} out-of-sample; model {conf.get('calibration_version', '?')}"
    )


def render_new_signal(payload: dict, secret: bytes) -> RenderedMessage:
    s = payload["signal"]
    tier = payload["tier"]
    demo = s["is_demo"]
    lv, hz, q = s["levels"], s["horizon"], s["quote"]
    title = (DEMO_PREFIX if demo else "") + (
        f"HIGH-CONVICTION LONG {s['symbol']}"
        if tier == "HIGH_CONVICTION"
        else f"Research setup (not high-conviction): LONG {s['symbol']}"
    )
    if s.get("company_name"):
        title += f" - {s['company_name']}"
    reasons = "\n".join(f"+ {r['text']}" for r in s["thesis"]["reasons"][:5])
    against = "\n".join(f"- {r['text']}" for r in s["thesis"]["counter_evidence"][:4])
    targets = lv["target_primary"] + (f" / {lv['target_secondary']}" if lv.get("target_secondary") else "")
    quote_line = (
        f"{q['price']} ({feed_label(q['provenance'])}, age {q['age_seconds'] // 60} min)"
        if s["release_mode"] == "DEMO" or q.get("is_tradable_proxy") or q["provenance"].get("feed") == "demo"
        else "See your broker for the current price (live redistribution disabled)"
    )
    fields = [
        _field("Quality rank (NOT a probability)", f"{s['quality_score']:.0f}/100"),
        _field("Calibrated probability", confidence_text(s["confidence"]), inline=False),
        _field("Entry zone", f"{lv['entry_min']} - {lv['entry_max']}"),
        _field("Stop / invalidation", lv["stop_level"]),
        _field("Target(s)", targets),
        _field("Net reward/risk (after modeled costs)", f"{lv['reward_to_risk']}R"),
        _field(
            "Horizon",
            f"{hz['min_sessions']}-{hz['max_sessions']} trading sessions ({hz['basis'].lower().replace('_', ' ')})",
        ),
        _field("Entry valid until", _when(hz["entry_expires_at"])),
        _field("Decision as-of", _when(s["asof_at"]), inline=False),
        _field("Quote", quote_line, inline=False),
        _field("Why selected", reasons, inline=False),
        _field("Counter-evidence / risks", against + f"\n- {s['risk']['gap_risk_note']}", inline=False),
        _field(
            "Account eligibility",
            f"Tradability: {s['risk']['tradability_status'].replace('_', ' ')} (not verified for your broker account)",
            inline=False,
        ),
    ]
    footer = f"{s['id']} r{s['revision']} | {s['strategy_id']} {s['strategy_version']} | {s['policy_version']} | {DISCLAIMER}"
    if demo:
        footer = DEMO_FOOTER + " | " + footer
    color = GREEN if tier == "HIGH_CONVICTION" else VIOLET
    return RenderedMessage(
        content=_clip(title, 200),
        embeds=[_embed(title, s["thesis"].get("summary", ""), color, fields, footer)],
        components=_buttons(secret, s["id"], s["symbol"]),
    )


def render_signal_event(payload: dict) -> RenderedMessage:
    ev = payload["event"]
    demo = payload["is_demo"]
    title = (DEMO_PREFIX if demo else "") + f"{payload['symbol']}: {ev['event_type'].replace('_', ' ').title()}"
    pl = ev.get("payload", {})
    fields = [
        _field("Signal", payload["signal_id"]),
        _field("New state", ev["to_state"]),
        _field("When", _when(ev["event_at"]), inline=False),
        _field("Cause", ev["cause"], inline=False),
    ]
    if "return_net_modeled" in pl:
        fields.append(
            _field(
                "Modeled outcome (theoretical, not your fill)",
                f"{pl['return_net_modeled']:+.2%} net, {pl.get('r_multiple_modeled')}R, "
                f"{pl.get('sessions_held')} sessions",
            )
        )
    color = (
        CORAL
        if ev["event_type"] in ("STOP_HIT", "INVALIDATED", "INTRADAY_STOP_TOUCH")
        else (GREEN if ev["event_type"] in ("TARGET_REACHED", "INTRADAY_TARGET_TOUCH") else SLATE)
    )
    footer = (
        (DEMO_FOOTER + " | " if demo else "")
        + "Group signal update only - your own position is tracked privately. "
        + DISCLAIMER
    )
    return RenderedMessage(embeds=[_embed(title, "", color, fields, footer)])


def render_position_update(payload: dict, secret: bytes) -> RenderedMessage:
    a = payload["assessment"]
    gated = a.get("compliance_gated", True)
    text = STATUS_TEXT.get(a["status"], (a["status"], a["status"]))[0 if gated else 1]
    prov = a.get("quote_provenance")
    demo = bool(prov and prov.get("is_demo"))
    title = (DEMO_PREFIX if demo else "") + f"Your {a['symbol']} position: {text}"
    pnl = a.get("unrealized_pnl")
    pct = a.get("unrealized_pct")
    fields = [
        _field("Self-reported cost basis", f"{a['avg_cost']} x {a['total_quantity']} sh"),
        _field(
            "Price",
            f"{a.get('price', 'n/a')} ({feed_label(prov) if prov else 'unavailable'}, "
            f"age {(a.get('quote_age_seconds') or 0) // 60} min)"
            if prov
            else "unavailable",
        ),
        _field("Est. unrealized P&L", f"{pnl} ({pct:+.2%})" if pnl is not None and pct is not None else "n/a"),
        _field(
            "Distance to stop / target",
            f"{_pct(a.get('distance_to_stop_pct'))} / {_pct(a.get('distance_to_target_pct'))}",
        ),
        _field("Sessions held / remaining", f"{a.get('sessions_held', 0)} / {a.get('sessions_remaining', 'n/a')}"),
        _field("Why", "\n".join(a.get("reasons", [])) or "-", inline=False),
    ]
    if a.get("risk_flags"):
        fields.append(
            _field("Risk flags", ", ".join(f.replace("_", " ").lower() for f in a["risk_flags"]), inline=False)
        )
    footer = (
        "Private to you. Self-reported entries are not broker records. "
        + ("Personalized guidance is disabled pending compliance review; wording is informational. " if gated else "")
        + DISCLAIMER
    )
    if demo:
        footer = DEMO_FOOTER + " | " + footer
    color = (
        CORAL
        if a["status"] in ("REVIEW_INVALIDATION", "CONSIDER_EXIT", "PRICE_UNCERTAIN")
        else (AMBER if a["status"] in ("STALE_DATA", "TIME_EXIT_REVIEW", "CONSIDER_TRIM") else GREEN)
    )
    btn = [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 1,
                    "label": "Update My Position",
                    "custom_id": components.make(secret, "posupd", a["position_id"]),
                }
            ],
        }
    ]
    return RenderedMessage(embeds=[_embed(title, "", color, fields, footer)], components=btn)


def _pct(v) -> str:
    return "n/a" if v is None else f"{v:+.1%}"


def render_system_alert(payload: dict, is_demo: bool) -> RenderedMessage:
    title = (DEMO_PREFIX if is_demo else "") + f"SYSTEM: {payload['title']}"
    fields = [
        _field("What happened", payload["detail"], inline=False),
        _field("What to do", payload["action"], inline=False),
        _field("Last good update", _when(payload.get("last_good_at")), inline=False),
    ]
    return RenderedMessage(embeds=[_embed(title, "", AMBER, fields, "Operational notice - not an investment alert.")])


def render_performance(payload: dict, is_demo: bool) -> RenderedMessage:
    st = payload["stats"]
    title = (DEMO_PREFIX if is_demo else "") + f"Signal scorecard - {payload['period']}"
    fields = [_field(k.replace("_", " "), str(v)) for k, v in list(st.items())[:20]]
    footer = (
        "Measured on ALL published signals incl. no-fills and expirations; theoretical entries, not member fills. "
        + DISCLAIMER
    )
    return RenderedMessage(embeds=[_embed(title, "", SLATE, fields, footer)])


def render(intent: dict, secret: bytes) -> RenderedMessage:
    kind = intent["kind"]
    p = intent["payload"]
    if kind == "NEW_SIGNAL":
        return render_new_signal(p, secret)
    if kind in ("SIGNAL_UPDATE", "SIGNAL_CLOSED"):
        return render_signal_event(p)
    if kind == "POSITION_UPDATE":
        return render_position_update(p, secret)
    if kind == "SYSTEM_ALERT":
        return render_system_alert(p, intent["is_demo"])
    if kind == "PERFORMANCE_REPORT":
        return render_performance(p, intent["is_demo"])
    raise ValueError(f"no renderer for {kind}")
