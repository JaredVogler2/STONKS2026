"""`apex research real-equities` and `apex research budget-check` (research-only; no release API, no signing)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path


def _date(raw: str | None) -> date | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an ISO date (YYYY-MM-DD): {raw!r}") from None


def cmd_real_equities(args) -> int:
    from apex_worker.research.realeq.runner import RunConfig, run

    def progress(msg: str) -> None:
        print(msg, flush=True)

    outcome = run(RunConfig(source=args.source, experiment_id=args.experiment_id, out_dir=Path(args.out), years=args.years,
                            universe=args.universe, time_start=args.time_start, time_end=args.time_end,
                            max_requests=args.max_requests, progress=progress, protocol=args.protocol))
    print(f"real-equities research status={outcome.status} exit={outcome.exit_code} out={outcome.out_dir}")
    if outcome.exit_code != 0:
        print(f"::error title={outcome.status}::{outcome.reason[:900]}" if os.environ.get("GITHUB_ACTIONS") else outcome.reason,
              file=sys.stderr)
    if args.github_output and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"status={outcome.status}\n")
    return outcome.exit_code


def cmd_budget_check(args) -> int:
    """Static checkpoint before any fetch: documented worst case of every scheduled APEX job this month plus this run's
    timeout must fit the configured budget. Measured usage is NOT visible to a read-only token, so it is reported unknown;
    the hard stop remains the owner's GitHub spending limit."""
    from apex_worker.release.budget import DEFAULT_BUDGET_MINUTES, EXIT_SKIP, max_possible_month

    now = datetime.now(UTC)
    raw = (os.environ.get("APEX_ACTIONS_MONTHLY_BUDGET") or "").strip()
    budget = int(raw) if raw.isdigit() else DEFAULT_BUDGET_MINUTES
    scheduled = max_possible_month(now, "auto")
    worst = scheduled + args.timeout_minutes
    ok = worst <= budget
    body = {"job": "real_equities_research", "proceed": ok, "budget_minutes": budget,
            "scheduled_worst_case_month": scheduled, "this_run_timeout_minutes": args.timeout_minutes,
            "worst_case_month": worst, "measured_minutes": None,
            "measured_source": "unknown (GITHUB_TOKEN has no billing scope); worst-case static check only",
            "reason": "ok" if ok else f"worst case {worst} min > budget {budget} min: skipped before any data fetch",
            "asof": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    text = json.dumps(body, indent=2, sort_keys=True)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"proceed={'true' if ok else 'false'}\n")
    return 0 if ok else EXIT_SKIP


def register(sub) -> None:
    rs = sub.add_parser("research", help="research-only experiments (no signing, no registry, no promotion, no orders)")
    rsub = rs.add_subparsers(dest="research_cmd", required=True)
    re_ = rsub.add_parser("real-equities", help="H1-H8 (protocol-1) or S1-S5 (protocol-2) on real Alpaca SIP history")
    re_.add_argument("--source", choices=["alpaca", "fixture"], required=True,
                     help="alpaca = real historical SIP (needs ALPACA_KEY_ID/ALPACA_SECRET_KEY); fixture = DEMO smoke test")
    re_.add_argument("--experiment-id", required=True, help="lower-case slug, e.g. 2026-10-fixed78-sip-8y (fixture: demo-...)")
    re_.add_argument("--out", required=True, help="output directory for the seven report files")
    re_.add_argument("--years", type=int, default=8)
    re_.add_argument("--universe", choices=["fixed78", "u1-sp500"], default="fixed78",
                     help="fixed78 (realeq-protocol-1) or u1-sp500 (realeq-protocol-2)")
    re_.add_argument("--protocol", choices=["realeq-protocol-1", "realeq-protocol-2"], default="realeq-protocol-1",
                     help="frozen protocol; protocol-2 is opt-in (docs/quant-v2, research-results/real-equities/PROTOCOL_V2.md)")
    re_.add_argument("--time-start", type=_date, default=None, help="optional first decision date (YYYY-MM-DD)")
    re_.add_argument("--time-end", type=_date, default=None, help="optional last data session (YYYY-MM-DD)")
    re_.add_argument("--max-requests", type=int, default=300)
    re_.add_argument("--github-output", action="store_true")
    re_.set_defaults(func=cmd_real_equities)
    bc = rsub.add_parser("budget-check", help="static Actions-minutes checkpoint before the research fetch")
    bc.add_argument("--timeout-minutes", type=int, required=True)
    bc.add_argument("--out")
    bc.set_defaults(func=cmd_budget_check)
