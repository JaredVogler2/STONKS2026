"""Pre-registered portfolio objective, owner scenarios and the Pareto frontier (05 section 1; 07 section 3).

There is no universal "best" strategy without an owner's risk preferences, so V3 fixes, BEFORE any V3 outcome:
* the PRINCIPAL estimand: annualised log growth of the policy's account minus that of the investable alternative the
  policy actually competes with (exposure-matched SPY/cash for a cash-idle book; full SPY buy-and-hold for a policy
  whose idle capital is in SPY), net of 25 bps per side, with its dependent-bootstrap uncertainty;
* hard constraints any candidate must meet (no leverage, the account caps, a drawdown floor, a turnover ceiling,
  a minimum activity level so "always cash" is not mistaken for skill);
* three owner scenarios of a composite utility U = log growth - l_es * 10 * |ES5 (daily)| - l_dd * |MaxDD| - l_to *
  turnover per year (10 daily ES5 is roughly a two-week tail loss).
  The coefficients are illustrations fixed here; changing any of them is a new objective (new sha256) and every
  trial registered under the old objective stays attached to it.
Raw interpretable metrics are always reported next to any composite; no 0-100 score exists.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from apex_worker.discovery.v3.contracts import sha256_json


@dataclass(frozen=True)
class Objective:
    objective_id: str = "obj-v3-1"
    principal: str = ("annualised log growth minus the investable alternative (exposure-matched SPY/cash for cash-idle "
                      "books, SPY buy-and-hold for SPY-idle books), net 25 bps/side, pessimistic fills")
    minimum_effect_annual: float = 0.01  # +1 pp/yr of log growth: below this a policy is not economically interesting
    alpha: float = 0.05  # one-sided, applied after Holm over EVERY registered trial of the batch
    constraints: dict = field(default_factory=lambda: {
        "leverage": 0.0, "max_positions": 10, "sector_cap": 0.30, "max_drawdown_floor": -0.30,
        "turnover_per_year_max": 40.0, "min_avg_exposure": 0.05})
    scenarios: dict = field(default_factory=lambda: {
        "conservative": {"l_es": 0.6, "l_dd": 0.6, "l_to": 0.002},
        "balanced": {"l_es": 0.3, "l_dd": 0.3, "l_to": 0.001},
        "growth": {"l_es": 0.1, "l_dd": 0.1, "l_to": 0.0005}})

    def to_dict(self) -> dict:
        return asdict(self)

    def sha256(self) -> str:
        return sha256_json(self.to_dict())


PRINCIPAL_OBJECTIVE = Objective()

# Pilot 2 (registered with v3-pilot-2, before its run): the same principal at the MEASURED cost model cost-v3-2, a
# drawdown limit relative to the comparator (pilot-1 finding O-1: SPY's own DEV drawdown broke the absolute -30% floor)
# and turnover counted on stock trades only (finding A-2: SPY sweeps are not strategy turnover).
OBJ_V3_2 = Objective(
    objective_id="obj-v3-2",
    principal="annualised log growth minus the investable alternative (exposure-matched SPY/cash for cash-idle books, "
    "SPY buy-and-hold for SPY-idle books and weights agents), net of the measured cost model cost-v3-2, pessimistic "
    "fills",
    constraints={"leverage": 0.0, "max_positions": 10, "sector_cap": 0.30, "max_drawdown_vs_comparator": -0.10,
                 "turnover_per_year_max": 40.0, "turnover_basis": "stock", "min_avg_exposure": 0.05})

OBJECTIVES: dict[str, Objective] = {o.objective_id: o for o in (PRINCIPAL_OBJECTIVE, OBJ_V3_2)}


def constraint_violations(m: dict, obj: Objective = PRINCIPAL_OBJECTIVE, *, is_benchmark: bool = False,
                          comparator: dict | None = None) -> list[str]:
    c, out = obj.constraints, []
    if "max_drawdown_floor" in c and (m.get("max_drawdown") or 0) < c["max_drawdown_floor"]:
        out.append(f"max drawdown {m.get('max_drawdown')} below {c['max_drawdown_floor']}")
    if "max_drawdown_vs_comparator" in c:  # obj-v3-2: relative to the investable alternative's own drawdown
        if comparator is None or comparator.get("max_drawdown") is None:
            raise ValueError(f"{obj.objective_id} needs the comparator's metrics")
        floor = comparator["max_drawdown"] + c["max_drawdown_vs_comparator"]
        if (m.get("max_drawdown") or 0) < floor:
            out.append(f"max drawdown {m.get('max_drawdown')} below the comparator's {comparator['max_drawdown']} "
                       f"{c['max_drawdown_vs_comparator']:+}")
    to_key = "stock_turnover_per_year" if c.get("turnover_basis") == "stock" else "turnover_per_year"
    if (m.get(to_key) or 0) > c["turnover_per_year_max"]:
        out.append(f"{to_key} {m.get(to_key)}/yr above {c['turnover_per_year_max']}")
    expo = (m.get("avg_stock_exposure") or 0) + (m.get("avg_idle_spy_exposure") or 0)
    if not is_benchmark and expo < c["min_avg_exposure"]:
        out.append(f"average exposure {round(expo, 4)} below {c['min_avg_exposure']}: too inactive to judge")
    if (m.get("max_positions") or 0) > c["max_positions"]:
        out.append("more positions than the account allows")
    return out


def utility(m: dict, lam: dict) -> float | None:
    g, es, dd = m.get("log_growth_annual"), m.get("expected_shortfall_5"), m.get("max_drawdown")
    if g is None or es is None or dd is None:
        return None
    return round(g - lam["l_es"] * 10 * abs(es) - lam["l_dd"] * abs(dd) - lam["l_to"] * (m.get("turnover_per_year") or 0.0),
                 6)


def evaluate(m: dict, active: dict, obj: Objective = PRINCIPAL_OBJECTIVE, comparator: dict | None = None) -> dict:
    v = constraint_violations(m, obj, comparator=comparator)
    return {"objective_id": obj.objective_id, "objective_sha256": obj.sha256(),
            "principal_active_log_growth": active.get("active_log_growth_annual"),
            "principal_p_one_sided": active.get("p_one_sided"), "constraints_ok": not v, "violations": v,
            "utilities": {k: utility(m, lam) for k, lam in obj.scenarios.items()}}


def pareto_front(points: dict[str, dict], maximize: tuple[str, ...] = ("log_growth_annual",),
                 minimize_abs: tuple[str, ...] = ("max_drawdown", "expected_shortfall_5"),
                 minimize: tuple[str, ...] = ("turnover_per_year",)) -> list[str]:
    """Ids of the non-dominated points (descriptive; DEV-only frontiers are exploratory and never a selection rule)."""
    def vec(m):
        vals = [m.get(k) for k in maximize] + [(-abs(m[k]) if m.get(k) is not None else None) for k in minimize_abs] + [
            (-(m.get(k) or 0.0)) for k in minimize]
        return None if any(x is None for x in vals) else vals

    vs = {k: vec(m) for k, m in points.items()}
    vs = {k: v for k, v in vs.items() if v is not None}
    front = []
    for k, v in vs.items():
        dominated = any(all(o >= x for o, x in zip(w, v, strict=True)) and any(o > x for o, x in zip(w, v, strict=True))
                        for j, w in vs.items() if j != k)
        if not dominated:
            front.append(k)
    return sorted(front)
