"""Decision logic of cohort 1 of the evidence tournament, series `v3x-epa-tournament-c1-v1`
(docs/evidence/TOURNAMENT_COHORT_1.md).

Research only: paper books, no orders. The five neutral tilt designs that the registered design study `design-study-1`
measured and proposed (docs/evidence/DESIGN_STUDY_1.md; results research-results/discovery-v3/
design-study-1-2026-10-23/), with their equal-weight twin. The decisions are exactly the study's: they are computed by
`discovery.v3.design.decide` with the study's plan restricted to its `full` variants, so the books recorded
prospectively are the books that were measured. The conventions are cohort 0's (`tournament_books`):
* the explicit mask;
* long-only integer micro-weight targets, decided at the close of t from data through t, traded at the close of t + 1;
* rebalance sessions counted from the first decision session;
* every book invests at its first recorded decision.

Books:
* ew              equal weight over the mask, every 20 sessions (the twin);
* mom-full        the 60-session return, every 20 sessions;
* rev-full        minus the 5-session return, every 5;
* hi52-full       the close's distance below its 252-session high close, every 20;
* composite-full  the mean of the three signals' standardized neutral scores, every 5;
* lowvol-full     minus the 60-session volatility of daily log returns, every 20.
Every design ranks its signal within sectors, takes the least-squares residual on the within-sector-demeaned 252-session
market beta and log dollar volume (exactly sector-, beta- and size-neutral), scales it to a rank's dispersion (1/sqrt(3)),
clips it to [-1, 1] and holds equal weight x (1 + 0.5 x score).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from apex_worker.discovery.v3 import design as D
from apex_worker.discovery.v3 import tournament_books as TB
from apex_worker.evidence.tournament import CLAIM_RULE, SELECTION, SELECTION_BOUNDED

# design-study-1's plan restricted to its full-neutral designs: exactly the five measured books (and the twin)
PLAN = replace(D.DESIGN_STUDY_1, plan_id="tournament-c1-books", variants=("full",),
               notes="design-study-1's plan restricted to its full-neutral designs (cohort 1 of the evidence tournament)")
BOOKS: dict[str, dict] = {"ew": {"kind": "equal_weight", "every": PLAN.twin_every}} | {
    k: {"kind": "neutral_composite" if d["signal"] == "composite" else "neutral_tilt", "signal": d["signal"],
        "every": d["every"], "strength": PLAN.strength}
    for k, d in PLAN.designs().items()}
NEUTRALIZATION = {
    "ranks": "within each sector, mapped to [-1, 1] (ties by symbol); a name without the signal scores 0",
    "residual": "least squares on the within-sector-demeaned exposures (exactly sector-, beta- and size-neutral)",
    "exposures": {"beta": f"{PLAN.beta_lookback}-session OLS of daily log returns on SPY's, at least "
                          f"{PLAN.beta_min_obs} observations (missing: the sector median)",
                  "size": "log 20-session median dollar volume (missing: the sector median)"},
    "scale": "cross-sectional sd 1/sqrt(3), clipped to [-1, 1]; weight = equal weight x (1 + 0.5 x score)",
    "signals": {"mom": "60-session log return", "rev": "minus the 5-session log return",
                "hi52": "close / 252-session high close - 1", "composite": "mean of mom, rev, hi52 standardized",
                "lowvol": f"minus the {PLAN.vol_lookback}-session sd of daily log returns (at least {PLAN.vol_min_obs})"},
    "measured_by": {"plan": D.DESIGN_STUDY_1.plan_id, "plan_sha256": D.DESIGN_STUDY_1.sha256(),
                    "results": "research-results/discovery-v3/design-study-1-2026-10-23/"},
}
_MECH = {
    "mom-full": "momentum with its sector, beta and size bets removed (residual momentum: about 44% less noise than "
                "cohort 0's mom-tilt on DEV)",
    "rev-full": "within-sector short-term reversal, beta- and size-neutral, weekly at the close (liquidity provision)",
    "hi52-full": "52-week-high proximity, sector-, beta- and size-neutral (anchoring; George and Hwang 2004)",
    "composite-full": "the three signals combined: one neutral score, weekly",
    "lowvol-full": "low idiosyncratic volatility, beta-neutral (the most testable design measured: 0.80% tracking error)",
}
# Measured by design-study-1 (the registered 2x rule on the worst DEV session, rounded up on its grid).
_TWIN_BOUND = {"mom-full": 0.02, "rev-full": 0.015, "hi52-full": 0.02, "composite-full": 0.015, "lowvol-full": 0.0075}
PRIOR_MASS = 0.04
LOSS_BOUND_VS_SPY = 0.075
CLAIMS = [SELECTION, SELECTION_BOUNDED]  # the mechanism tests; cohort 0's `ew` already tests equal weight against SPY
CANDIDATES: tuple[dict, ...] = tuple(
    {"candidate_id": k, "book": k, "twin": "ew", "prior_mass": PRIOR_MASS, "loss_bound": LOSS_BOUND_VS_SPY,
     "twin_loss_bound": _TWIN_BOUND[k], "claims": list(CLAIMS), "mechanism": _MECH[k]}
    for k in PLAN.designs())
TOURNAMENT = dict(TB.TOURNAMENT)
if TOURNAMENT["claim_rule"] != CLAIM_RULE:  # pragma: no cover - guarded by tests
    raise RuntimeError("cohort 1 must use the engine's claim rule")
FEATURES = {"design_plan": PLAN.to_dict(), "neutralization": NEUTRALIZATION}
FEATURE_SCHEMA_SHA256 = hashlib.sha256(json.dumps(FEATURES, sort_keys=True).encode()).hexdigest()


def decide(pre, t: int, t_abs: int, phase: int, previous: dict[str, dict | None], mask=None) -> dict[str, dict | None]:
    """Targets decided at the close of t for every book (None: hold). Every book rebalances at the first recorded
    decision (all previous targets None), then when phase % every == 0. `pre`: tournament_books.Panel (or
    study_v2.Prepared); `t_abs` and `mask` are accepted for the lane's interface and not needed (the twin and the
    designs read the explicit mask at t)."""
    inp = D.inputs(pre)
    first = all(previous.get(b) is None for b in BOOKS)
    targets, _exposures = D.decide(inp, t, phase, PLAN, first)
    return {b: ({"micro": targets[b]} if b in targets else None) for b in BOOKS}
