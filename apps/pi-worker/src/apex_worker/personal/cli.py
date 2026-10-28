"""`apex personal ...`: the owner's $1000 method on Discord (alerts for manual trading; never an order).

  run    one run against the private channel (the scheduled workflow): `--mode live` posts, `--mode dry` reads and
         computes only. Needs DISCORD_BOT_TOKEN, APEX_PERSONAL_CHANNEL_ID, ALPACA_KEY_ID and ALPACA_SECRET_KEY; a
         missing one stops the run (exit 2). Optional: APEX_PERSONAL_USER_IDS (comma-separated Discord user ids whose
         messages count; default anyone in the channel), APEX_PERSONAL_DAILY_SUMMARY=off.
  smoke  an offline rehearsal with an in-memory channel and synthetic prices: prints the conversation.
  signal the adopted method's signal on today's Alpaca data, for a fresh $1000 account (needs only the Alpaca keys; no
         Discord, nothing personal). Prints the decision session, the target and the signal as a percentage, never a
         price.

`run` prints one line of counts and nothing else, because the Actions log of a public repository is public: no
symbol, amount, price or message text. A failed run prints the exception's type, plus the HTTP status for a Discord
or market-data failure, and never an exception message.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

REQUIRED = ("DISCORD_BOT_TOKEN", "APEX_PERSONAL_CHANNEL_ID", "ALPACA_KEY_ID", "ALPACA_SECRET_KEY")


def cmd_run(args) -> int:
    from apex_worker.personal.discord_rest import DiscordError, DiscordREST
    from apex_worker.personal.market import alpaca_source
    from apex_worker.personal.runner import Config, MarketUnavailable, Runner

    env = os.environ
    missing = [k for k in REQUIRED if not (env.get(k) or "").strip()]
    if missing:
        print(f"personal run blocked: {', '.join(missing)} not set (personal/README.md, owner setup)", file=sys.stderr)
        return 2
    users = frozenset(u.strip() for u in (env.get("APEX_PERSONAL_USER_IDS") or "").split(",") if u.strip())
    cfg = Config(env["APEX_PERSONAL_CHANNEL_ID"].strip(), allowed_users=users,
                 daily_summary=(env.get("APEX_PERSONAL_DAILY_SUMMARY") or "on").strip().lower() != "off",
                 live=args.mode == "live")
    discord = DiscordREST(env["DISCORD_BOT_TOKEN"].strip())
    try:
        report = Runner(cfg, discord, alpaca_source(env["ALPACA_KEY_ID"].strip(), env["ALPACA_SECRET_KEY"].strip()),
                        now=datetime.now(UTC)).run()
    except (DiscordError, MarketUnavailable) as exc:  # statuses and type names only (see their docstrings)
        print(f"personal run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # any other message could carry personal text: its type only
        print(f"personal run failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        discord.close()
    print(json.dumps(report.to_dict() | {"discord_requests": discord.requests}, sort_keys=True))
    return 0


def cmd_signal(args) -> int:
    from datetime import timedelta

    from apex_worker.market.calendar import default_calendar
    from apex_worker.personal.market import Request, alpaca_source
    from apex_worker.personal.method import METHODS, decide
    from apex_worker.personal.runner import HISTORY_DAYS, PERSONAL_METHOD, SETTLE
    from apex_worker.research.training_data import BENCHMARK
    from apex_worker.timeutil import ET

    env = os.environ
    missing = [k for k in ("ALPACA_KEY_ID", "ALPACA_SECRET_KEY") if not (env.get(k) or "").strip()]
    if missing:
        print(f"personal signal blocked: {', '.join(missing)} not set", file=sys.stderr)
        return 2
    spec, cal, now = METHODS[PERSONAL_METHOD], default_calendar(), datetime.now(UTC)
    today = now.astimezone(ET).date()
    try:
        snap = alpaca_source(env["ALPACA_KEY_ID"].strip(), env["ALPACA_SECRET_KEY"].strip())(Request(
            daily=tuple(sorted({BENCHMARK, *spec.data_symbols})), latest=(), start=today - timedelta(days=HISTORY_DAYS),
            end=today))
    except Exception as exc:  # the type and an HTTP status only
        status = getattr(exc, "status", None)
        print(f"personal signal failed: {type(exc).__name__}" + (f" {status}" if isinstance(status, int) else ""),
              file=sys.stderr)
        return 1
    last = cal.latest_completed_session(now - SETTLE)
    if not all(snap.has_day(s, last) for s in spec.data_symbols):
        last = cal.previous_session(last)
    dec = decide(spec, snap.closes(), last, {}, 1000.0)
    out = {"method": spec.method_id, "decided_on": last.isoformat(), "target": dec.target,
           "orders_for_a_fresh_1000": [(o.action, o.symbol, o.shares) for o in dec.orders],
           "signal_pct": {k: round(100 * v, 1) for k, v in dec.signals.items()},
           "bars": {s: len(snap.bars.get(s, ())) for s in spec.data_symbols}}
    print(json.dumps(out, sort_keys=True))
    return 0 if dec.target else 3


def cmd_smoke(args) -> int:
    from apex_worker.personal.smoke import rehearse

    for line in rehearse():
        print(line)
    return 0


def register(sub) -> None:
    p = sub.add_parser("personal", help="the personal $1000 method: Discord alerts for manual trading (no orders)")
    ps = p.add_subparsers(dest="personal_cmd", required=True)
    r = ps.add_parser("run", help="one run against the private channel (counts-only output)")
    r.add_argument("--mode", choices=["live", "dry"], default="dry")
    r.set_defaults(func=cmd_run)
    s = ps.add_parser("smoke", help="offline rehearsal: in-memory channel, synthetic prices")
    s.set_defaults(func=cmd_smoke)
    g = ps.add_parser("signal", help="the adopted method's signal on today's Alpaca data (no Discord, no prices)")
    g.set_defaults(func=cmd_signal)


__all__ = ["REQUIRED", "cmd_run", "cmd_signal", "cmd_smoke", "register"]
