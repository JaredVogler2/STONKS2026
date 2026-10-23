"""`apex research discovery ...` (research only: no orders, alerts, signing or promotion).

  plans     list the registered plans and their sha256 (a plan is committed before the run that evaluates it)
  run       one bounded DEV experiment: --plan ID --source alpaca|fixture --experiment-id SLUG --out DIR
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def cmd_plans(args) -> int:
    from apex_worker.discovery.v3.plans import PLANS

    print(json.dumps({k: {"title": p.title, "sha256": p.sha256(),
                          "fixture_only": p.fixture_only, "hypotheses": [h.hypothesis_id for h in p.hypotheses]}
                      for k, p in PLANS.items()}, indent=1))
    return 0


def cmd_run(args) -> int:
    from apex_worker.discovery.v3.pipeline import load_data, run_experiment
    from apex_worker.discovery.v3.plans import PLANS
    from apex_worker.research.realeq.data import RunStop

    if args.plan not in PLANS:
        print(f"unknown plan {args.plan}; registered: {sorted(PLANS)}", file=sys.stderr)
        return 5
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.experiment_id or ""):
        print("experiment id must be a lower-case slug", file=sys.stderr)
        return 5
    out = Path(args.out)
    demo = args.source == "fixture"
    if demo and not args.experiment_id.startswith("demo-"):
        print("fixture (DEMO) runs need an experiment id starting with 'demo-'", file=sys.stderr)
        return 5
    if demo and "research-results" in out.resolve().parts:
        print("DEMO output may never be written under research-results/", file=sys.stderr)
        return 5
    if PLANS[args.plan].fixture_only and not demo:
        print(f"plan {args.plan} is fixture-only (an engine smoke plan)", file=sys.stderr)
        return 5
    try:
        data = load_data(args.source, per_sector=args.per_sector, max_requests=args.max_requests)
    except RunStop as stop:
        print(f"{stop.status}: {stop.reason}", file=sys.stderr)
        return 2 if "BLOCKED" in stop.status else 3
    summary = run_experiment(PLANS[args.plan], data, out, experiment_id=args.experiment_id,
                             progress=lambda m: print(m, flush=True), n_boot=args.n_boot)
    (out / "data_quality_summary.json").write_text(json.dumps(data.data_quality, indent=1, sort_keys=True, default=str))
    print(json.dumps(summary, indent=1, default=str))
    return 0


def register(rsub) -> None:
    d = rsub.add_parser("discovery", help="Discovery V3 bounded DEV experiments (research only)")
    sub = d.add_subparsers(dest="discovery_cmd", required=True)
    p = sub.add_parser("plans", help="list registered plans")
    p.set_defaults(func=cmd_plans)
    r = sub.add_parser("run", help="run one registered plan on DEV (contaminated history: diagnostics only)")
    r.add_argument("--plan", required=True)
    r.add_argument("--source", choices=["alpaca", "fixture"], required=True)
    r.add_argument("--experiment-id", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--per-sector", type=int, default=6, help="fixture only: synthetic stocks per sector")
    r.add_argument("--max-requests", type=int, default=None)
    r.add_argument("--n-boot", type=int, default=999)
    r.set_defaults(func=cmd_run)
