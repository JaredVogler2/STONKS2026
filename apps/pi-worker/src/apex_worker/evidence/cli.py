"""`apex research evidence ...` (research only: no orders, alerts, signing or promotion).

  velocity  years of prospective data a candidate needs before its evidence can reach 1/alpha, and the limits
  replay    the anytime-valid e-process of a returns file (CSV: a candidate column and a numeraire column, simple
            returns per settlement period, oldest first); prints aggregates only, never rows
"""

from __future__ import annotations

import json
import math
import sys


def cmd_velocity(args) -> int:
    from apex_worker.evidence.velocity import velocity

    print(json.dumps(velocity(args.name, args.log_growth, args.te, alpha=args.alpha, loss_bound=args.loss_bound),
                     indent=1))
    return 0


def cmd_replay(args) -> int:
    import numpy as np
    import pandas as pd

    from apex_worker.evidence.eprocess import (
        FREE_LAMBDAS,
        INVALID,
        VALID,
        bounded_lambdas,
        eprocess_path,
        relative_return,
    )

    df = pd.read_csv(args.csv)
    missing = [c for c in (args.candidate, args.numeraire) if c not in df.columns]
    if missing:
        print(f"columns not found: {missing}", file=sys.stderr)
        return 2
    x = relative_return(df[args.candidate].to_numpy(float), df[args.numeraire].to_numpy(float))
    lam = FREE_LAMBDAS if args.loss_bound is None else bounded_lambdas(args.loss_bound)
    path = eprocess_path(x, lam, None, args.cost_bps / 1e4, args.loss_bound)
    level = math.log(1 / args.alpha)
    hit = np.flatnonzero(path.log_e_max >= level)
    invalid = bool(path.violated)
    print(json.dumps({
        "periods": int(x.size), "assumption": "none" if args.loss_bound is None else f"X >= -{args.loss_bound}",
        "status": INVALID if invalid else VALID, "e_final": float(np.exp(path.log_e[-1])),
        "e_max": float(np.exp(path.log_e_max[-1])),
        "p_anytime": 1.0 if invalid else float(min(1.0, np.exp(-path.log_e_max[-1]))),
        "bet_final": float(path.bet[-1]), "threshold": 1 / args.alpha,
        "first_period_at_threshold": None if invalid or not hit.size else int(hit[0]),
        "mode": "RESEARCH_ONLY"}, indent=1))
    return 0


def register(rsub) -> None:
    ev = rsub.add_parser("evidence", help="anytime-valid evidence engine (research only)")
    sub = ev.add_subparsers(dest="evidence_cmd", required=True)
    v = sub.add_parser("velocity", help="years of prospective data needed before a claim is possible")
    v.add_argument("--name", default="candidate")
    v.add_argument("--log-growth", type=float, required=True, help="log growth a year relative to the numeraire")
    v.add_argument("--te", type=float, required=True, help="tracking error a year against the numeraire")
    v.add_argument("--alpha", type=float, default=0.05)
    v.add_argument("--loss-bound", type=float, default=None, help="declared worst one-period relative loss")
    v.set_defaults(func=cmd_velocity)
    r = sub.add_parser("replay", help="e-process of a CSV of returns (aggregates only)")
    r.add_argument("--csv", required=True)
    r.add_argument("--candidate", required=True)
    r.add_argument("--numeraire", required=True)
    r.add_argument("--alpha", type=float, default=0.05)
    r.add_argument("--cost-bps", type=float, default=0.0, help="rebalancing cost per side, bps")
    r.add_argument("--loss-bound", type=float, default=None)
    r.set_defaults(func=cmd_replay)
