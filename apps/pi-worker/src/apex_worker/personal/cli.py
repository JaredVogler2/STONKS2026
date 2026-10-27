"""`apex personal ...`: the owner's $1000 method on Discord (alerts for manual trading; never an order).

  run    one run against the private channel (the scheduled workflow): `--mode live` posts, `--mode dry` reads and
         computes only. Needs DISCORD_BOT_TOKEN, APEX_PERSONAL_CHANNEL_ID, ALPACA_KEY_ID and ALPACA_SECRET_KEY; a
         missing one stops the run (exit 2). Optional: APEX_PERSONAL_USER_IDS (comma-separated Discord user ids whose
         messages count; default anyone in the channel), APEX_PERSONAL_DAILY_SUMMARY=off.
  smoke  an offline rehearsal with an in-memory channel and synthetic prices: prints the conversation.

`run` prints one line of counts and nothing else, because the Actions log of a public repository is public: no
symbol, amount, price or message text, and no exception message (only its type).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

REQUIRED = ("DISCORD_BOT_TOKEN", "APEX_PERSONAL_CHANNEL_ID", "ALPACA_KEY_ID", "ALPACA_SECRET_KEY")


def cmd_run(args) -> int:
    from apex_worker.personal.discord_rest import DiscordREST
    from apex_worker.personal.market import alpaca_source
    from apex_worker.personal.runner import Config, Runner

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
    except Exception as exc:
        print(f"personal run failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        discord.close()
    print(json.dumps(report.to_dict() | {"discord_requests": discord.requests}, sort_keys=True))
    return 0


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


__all__ = ["REQUIRED", "cmd_run", "cmd_smoke", "register"]
