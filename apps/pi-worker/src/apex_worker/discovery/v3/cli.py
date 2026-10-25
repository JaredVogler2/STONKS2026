"""`apex research discovery ...` (research only: no orders, alerts, signing or promotion).

  plans     list the registered plans and their sha256 (a plan is committed before the run that evaluates it)
  run       one bounded DEV experiment: --plan ID --source alpaca|fixture --experiment-id SLUG --out DIR
  costs     the registered execution-cost measurement (cost-study-*): same arguments; aggregates only
  design    the registered design measurement (design-study-*): tracking error and worst sessions of tilt designs
            against their twin (docs/evidence/DESIGN_STUDY_1.md; SEC-event designs: DESIGN_STUDY_2.md, which also needs
            the owner's APEX_SEC_USER_AGENT); same arguments; aggregates only
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def cmd_plans(args) -> int:
    from apex_worker.discovery.v3.costs import COST_PLANS
    from apex_worker.discovery.v3.design import DESIGN_PLANS
    from apex_worker.discovery.v3.design_events import EVENT_PLANS
    from apex_worker.discovery.v3.plans import PLANS

    out = {k: {"title": p.title, "sha256": p.sha256(), "fixture_only": p.fixture_only,
               "hypotheses": [h.hypothesis_id for h in p.hypotheses]} for k, p in PLANS.items()}
    out |= {k: {"title": p.title, "sha256": p.sha256(), "kind": "measurement"} for k, p in COST_PLANS.items()}
    out |= {k: {"title": p.title, "sha256": p.sha256(), "kind": "measurement"} for k, p in DESIGN_PLANS.items()}
    out |= {k: {"title": p.title, "sha256": p.sha256(), "kind": "measurement", "needs": "APEX_SEC_USER_AGENT"}
            for k, p in EVENT_PLANS.items()}
    print(json.dumps(out, indent=1))
    return 0


def _guard(args) -> str | None:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.experiment_id or ""):
        return "experiment id must be a lower-case slug"
    demo = args.source == "fixture"
    if demo and not args.experiment_id.startswith("demo-"):
        return "fixture (DEMO) runs need an experiment id starting with 'demo-'"
    if demo and "research-results" in Path(args.out).resolve().parts:
        return "DEMO output may never be written under research-results/"
    return None


def cmd_costs(args) -> int:
    import os

    from apex_worker.discovery.v3.costs import COST_PLANS, run_cost_study
    from apex_worker.discovery.v3.pipeline import load_data
    from apex_worker.research.realeq.data import RunStop

    if args.plan not in COST_PLANS:
        print(f"unknown cost study {args.plan}; registered: {sorted(COST_PLANS)}", file=sys.stderr)
        return 5
    if (why := _guard(args)) is not None:
        print(why, file=sys.stderr)
        return 5
    try:
        data = load_data(args.source, per_sector=args.per_sector, max_requests=args.max_requests)
    except RunStop as stop:
        print(f"{stop.status}: {stop.reason}", file=sys.stderr)
        return 2 if "BLOCKED" in stop.status else 3
    if args.source == "fixture":
        from apex_worker.discovery.v3.fixture_micro import FakeAlpacaMicro
        from apex_worker.research.realeq.fixture import FIXTURE_NOW
        from apex_worker.research.realeq.fixture_v2 import factor_frames_v2

        fake = FakeAlpacaMicro(factor_frames_v2(per_sector=args.per_sector), corporate_actions=[])

        def factory():
            return fake.adapter(FIXTURE_NOW)
    else:
        from apex_worker.market.adapters.alpaca import AlpacaAdapter
        from apex_worker.research.realeq.data import credentials_from_env

        key, secret = credentials_from_env(os.environ)

        def factory():
            return AlpacaAdapter(key, secret)
    summary = run_cost_study(COST_PLANS[args.plan], data, factory, Path(args.out), experiment_id=args.experiment_id,
                             progress=lambda m: print(m, flush=True))
    (Path(args.out) / "data_quality_summary.json").write_text(json.dumps(data.data_quality, indent=1, sort_keys=True,
                                                                         default=str))
    print(json.dumps(summary, indent=1, default=str))
    return {"COMPLETED": 0, "SOURCE_BLOCKED": 2}.get(summary["status"], 3)


def cmd_design(args) -> int:
    from apex_worker.discovery.v3.design import DESIGN_PLANS, run_design_study
    from apex_worker.discovery.v3.design_events import EVENT_PLANS
    from apex_worker.discovery.v3.pipeline import load_data
    from apex_worker.research.realeq.data import RunStop

    if args.plan not in DESIGN_PLANS and args.plan not in EVENT_PLANS:
        print(f"unknown design study {args.plan}; registered: {sorted(DESIGN_PLANS | EVENT_PLANS)}", file=sys.stderr)
        return 5
    if (why := _guard(args)) is not None:
        print(why, file=sys.stderr)
        return 5
    if args.plan in EVENT_PLANS:
        return _event_design(args, EVENT_PLANS[args.plan])
    try:
        data = load_data(args.source, per_sector=args.per_sector, max_requests=args.max_requests)
    except RunStop as stop:
        print(f"{stop.status}: {stop.reason}", file=sys.stderr)
        return 2 if "BLOCKED" in stop.status else 3
    out = Path(args.out)
    summary = run_design_study(DESIGN_PLANS[args.plan], data, out, experiment_id=args.experiment_id,
                               progress=lambda m: print(m, flush=True))
    (out / "data_quality_summary.json").write_text(json.dumps(data.data_quality, indent=1, sort_keys=True, default=str))
    print(json.dumps(summary, indent=1, default=str))
    return 0


def _event_design(args, plan) -> int:
    """design-study-2: the SEC contact is checked before any download, then the daily bars, then the SEC events of the
    loaded stocks (DEMO: fictional events)."""
    from apex_worker.discovery.v3 import design_events as E
    from apex_worker.discovery.v3.pipeline import load_data
    from apex_worker.market.calendar import default_calendar
    from apex_worker.research import sec_history as SH
    from apex_worker.research.realeq.data import RunStop

    cal = default_calendar()
    try:
        ua = None if args.source == "fixture" else SH.user_agent_from_env()
        data = load_data(args.source, per_sector=args.per_sector, max_requests=args.max_requests)
        start, end = E.history_window(data)
        stocks = list(data.pre.stocks)
        if ua is None:
            history = SH.synthetic_history(stocks, [d.date() for d in data.pre.sessions])
        else:
            history = SH.load_sec_history(stocks, start=start, end=end, cal=cal, user_agent=ua,
                                          progress=lambda m: print(m, flush=True))
    except RunStop as stop:
        print(f"{stop.status}: {stop.reason}", file=sys.stderr)
        return 2 if "BLOCKED" in stop.status else 3
    out = Path(args.out)
    summary = E.run_event_design_study(plan, data, history, out, experiment_id=args.experiment_id, cal=cal,
                                       progress=lambda m: print(m, flush=True))
    (out / "data_quality_summary.json").write_text(json.dumps(data.data_quality, indent=1, sort_keys=True, default=str))
    print(json.dumps(summary, indent=1, default=str))
    return 0


def cmd_run(args) -> int:
    from apex_worker.discovery.v3.pipeline import load_data, run_experiment
    from apex_worker.discovery.v3.plans import PLANS
    from apex_worker.research.realeq.data import RunStop

    if args.plan not in PLANS:
        print(f"unknown plan {args.plan}; registered: {sorted(PLANS)}", file=sys.stderr)
        return 5
    if (why := _guard(args)) is not None:
        print(why, file=sys.stderr)
        return 5
    out = Path(args.out)
    if PLANS[args.plan].fixture_only and args.source != "fixture":
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
    c = sub.add_parser("costs", help="run a registered execution-cost measurement (aggregates only)")
    c.add_argument("--plan", required=True)
    c.add_argument("--source", choices=["alpaca", "fixture"], required=True)
    c.add_argument("--experiment-id", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--per-sector", type=int, default=6, help="fixture only: synthetic stocks per sector")
    c.add_argument("--max-requests", type=int, default=None, help="daily-bar download cap (the study has its own)")
    c.set_defaults(func=cmd_costs)
    g = sub.add_parser("design", help="run a registered design measurement (aggregates only)")
    g.add_argument("--plan", required=True)
    g.add_argument("--source", choices=["alpaca", "fixture"], required=True)
    g.add_argument("--experiment-id", required=True)
    g.add_argument("--out", required=True)
    g.add_argument("--per-sector", type=int, default=6, help="fixture only: synthetic stocks per sector")
    g.add_argument("--max-requests", type=int, default=None)
    g.set_defaults(func=cmd_design)
