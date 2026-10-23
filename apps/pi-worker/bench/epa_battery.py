"""Evidence engine battery: size under continuous monitoring, evidence velocity, and a multiverse of allocation
policies (docs/evidence/EPA_BATTERY.md). Synthetic and seeded: no market data is read.

Markets are fat-tailed with volatility clustering: SPY follows a GARCH(1,1) with t(5) shocks (8% a year drift, 18%
volatility); a candidate is SPY plus a drift alpha(t) plus tracking noise that shares SPY's volatility state, with a
common factor across candidates (correlation 0.3) and t(4) shocks.

    uv run python bench/epa_battery.py size 4000 > size.json
    uv run python bench/epa_battery.py velocity 400 > velocity.json
    uv run python bench/epa_battery.py multiverse 1000 > multiverse.json
"""

from __future__ import annotations

import json
import math
import sys

import numpy as np

from apex_worker.evidence.eprocess import FREE_LAMBDAS, bounded_lambdas, eprocess_path
from apex_worker.evidence.tournament import evidence_weighted_path
from apex_worker.evidence.velocity import velocity

DAYS = 252
LEVEL = 20.0  # evidence 1/alpha with alpha = 0.05
COST = 0.0002  # 2 bps per side for rebalancing a sub-account (close execution, cost-v3-2)


def spy_paths(rng, n: int, t: int, mu: float = 0.08, vol: float = 0.18, a: float = 0.06, b: float = 0.92, nu: int = 5):
    """SPY daily returns and the volatility state (sigma_t / long-run sigma)."""
    hbar = vol * vol / DAYS
    omega = hbar * (1 - a - b)
    z = rng.standard_t(nu, size=(n, t)) / math.sqrt(nu / (nu - 2))
    h = np.full(n, hbar)
    r, scale = np.empty((n, t)), np.empty((n, t))
    for i in range(t):
        scale[:, i] = np.sqrt(h / hbar)
        e = np.sqrt(h) * z[:, i]
        r[:, i] = mu / DAYS + e
        h = omega + a * e * e + b * h
    return np.maximum(r, -0.5), scale


def t4(rng, shape):
    return rng.standard_t(4, size=shape) / math.sqrt(2.0)


def candidates(rng, r_spy, scale, alpha, te, rho: float = 0.3):
    """Relative returns X[n, k, t] of K candidates: R_k = R_spy + alpha_k(t) / 252 + noise. alpha has shape (k, t) or
    (n, k, t); te has shape (n, k)."""
    n, t = r_spy.shape
    k = te.shape[1]
    f = t4(rng, (n, 1, t))
    noise = (te[..., None] / math.sqrt(DAYS)) * scale[:, None, :] * (math.sqrt(rho) * f
                                                                     + math.sqrt(1 - rho) * t4(rng, (n, k, t)))
    rc = np.maximum(r_spy[:, None, :] + alpha / DAYS + noise, -0.95)
    return (1 + rc) / (1 + r_spy[:, None, :]) - 1


def running_t(x, start: int):
    """t-statistic of the mean of x[..., :n] for every n (NaN before `start`)."""
    n = np.arange(1, x.shape[-1] + 1)
    s1, s2 = np.cumsum(x, axis=-1), np.cumsum(x * x, axis=-1)
    mean = s1 / n
    var = np.maximum(s2 / n - mean * mean, 1e-18) * n / np.maximum(n - 1, 1)
    t = mean / np.sqrt(var / n)
    t[..., : start - 1] = np.nan
    return t


# ---- A: size under continuous monitoring ----------------------------------------------------------------------------
def battery_size(n: int, seed: int = 20261023, years: int = 10) -> dict:
    t = years * DAYS
    out = {}
    for name, alpha in (("exact_null", 0.0), ("cost_null", -0.01)):
        rng = np.random.default_rng(seed + (0 if alpha == 0 else 1))
        hits = {k: 0 for k in ("e_free", "e_bounded_0.2", "t_daily", "t_monthly", "t_annual", "t_fixed_horizon")}
        violations = 0
        for lo in range(0, n, 500):
            m = min(500, n - lo)
            r, sc = spy_paths(rng, m, t)
            x = candidates(rng, r, sc, np.full((1, t), alpha), np.full((m, 1), 0.15))[:, 0, :]
            free = eprocess_path(x, FREE_LAMBDAS, None, COST)
            hits["e_free"] += int(np.sum(free.log_e_max[:, -1] >= math.log(LEVEL)))
            bnd = eprocess_path(x, bounded_lambdas(0.2), None, 0.0, 0.2)  # a test statistic, not a portfolio
            hits["e_bounded_0.2"] += int(np.sum((bnd.log_e_max[:, -1] >= math.log(LEVEL)) & ~bnd.violated))
            violations += int(bnd.violated.sum())
            ts = running_t(x, 63)
            z = 1.6448536269514722
            hits["t_daily"] += int(np.sum(np.nanmax(ts, axis=1) >= z))
            hits["t_monthly"] += int(np.sum(np.nanmax(ts[:, 62::21], axis=1) >= z))
            hits["t_annual"] += int(np.sum(np.nanmax(ts[:, DAYS - 1::DAYS], axis=1) >= z))
            hits["t_fixed_horizon"] += int(np.sum(ts[:, -1] >= z))
        out[name] = {"alpha_per_year": alpha, "tracking_error": 0.15, "years": years, "paths": n,
                     "false_claim_rate": {k: v / n for k, v in hits.items()},
                     "bounded_loss_violations": violations / n}
    return {"battery": "size", "level": LEVEL, "nominal": 0.05, "results": out,
            "note": "false_claim_rate = share of null paths on which the rule ever claims an edge within the horizon. "
                    "t_* rules are one-sided t > 1.645 on daily relative returns, read every session / every 21 "
                    "sessions / every 252 sessions (from session 63), or once at the horizon."}


# ---- B: evidence velocity -------------------------------------------------------------------------------------------
VELOCITY_CASES = (  # name, arithmetic drift of X a year, tracking error, loss bound for the bounded process
    ("g2-at-dev-estimate", 0.1221, 0.268, 0.2),
    ("strong-long-only-book", 0.12, 0.15, 0.2),
    ("diversified-tilt-ir-1.0", 0.03, 0.03, 0.05),
    ("diversified-tilt-ir-1.5", 0.045, 0.03, 0.05),
)


def first_year(log_e_max, level: float):
    hit = log_e_max >= math.log(level)
    idx = np.where(hit.any(axis=-1), hit.argmax(axis=-1), -1)
    return np.where(idx >= 0, (idx + 1) / DAYS, np.inf)


def battery_velocity(n: int, seed: int = 20261024, years: int = 30) -> dict:
    t = years * DAYS
    rows = []
    for j, (name, mu, te, bound) in enumerate(VELOCITY_CASES):
        rng = np.random.default_rng(seed + j)
        yf, yb, viol = [], [], 0
        for lo in range(0, n, 100):
            m = min(100, n - lo)
            r, sc = spy_paths(rng, m, t)
            x = candidates(rng, r, sc, np.full((1, t), mu), np.full((m, 1), te))[:, 0, :]
            yf.append(first_year(eprocess_path(x, FREE_LAMBDAS, None, COST).log_e_max, LEVEL))
            b = eprocess_path(x, bounded_lambdas(bound), None, 0.0, bound)
            yb.append(np.where(b.violated, np.inf, first_year(b.log_e_max, LEVEL)))
            viol += int(b.violated.sum())
        yf, yb = np.concatenate(yf), np.concatenate(yb)
        analytic = velocity(name, mu - 0.5 * te * te, te, loss_bound=bound, horizons=(5, 10, 20, 30))

        def summ(y):
            return {"p_within": {str(h): float(np.mean(y <= h)) for h in (5, 10, 20, 30)},
                    "median_years": float(np.median(y)) if np.mean(np.isfinite(y)) > 0.5 else None}

        rows.append({"name": name, "drift": mu, "tracking_error": te, "ir": mu / te, "loss_bound": bound,
                     "simulated": {"assumption_free": summ(yf), "bounded_loss": summ(yb),
                                   "bounded_loss_violations": viol / n},
                     "analytic": analytic})
    return {"battery": "velocity", "level": LEVEL, "paths": n, "years": years, "cases": rows}


# ---- C: multiverse of allocation policies ---------------------------------------------------------------------------
# Two worlds of K = 10 candidates: concentrated long-only books (tracking error 10-25%) and diversified index tilts
# (2-4%). Scenario = candidate 0's alpha a year (backtest, deployment years 0-3, deployment years 3-10); every other
# candidate carries the world's null alpha (costs).
WORLDS = {
    "long-only-books": {"te": (0.10, 0.25), "null_alpha": -0.01, "loss_bound": 0.2, "scenarios": {
        "null": (-0.01, -0.01, -0.01), "mirage": (0.10, -0.01, -0.01), "weak-edge": (0.04, 0.04, 0.04),
        "strong-edge": (0.12, 0.12, 0.12), "decay": (0.12, 0.12, -0.01)}},
    "diversified-tilts": {"te": (0.02, 0.04), "null_alpha": -0.005, "loss_bound": 0.05, "scenarios": {
        "null": (-0.005, -0.005, -0.005), "mirage": (0.05, -0.005, -0.005), "weak-edge": (0.02, 0.02, 0.02),
        "strong-edge": (0.045, 0.045, 0.045), "decay": (0.045, 0.045, -0.005)}},
}
K, BACKTEST_Y, DEPLOY_Y, CORE, NULL_MASS = 10, 7, 10, 0.5, 0.5
POLICIES = ("P0_spy", "P1_backtest_best", "P1h_backtest_holm", "P2_epa_uniform", "P2b_epa_backtest_prior",
            "P2c_evidence_weighted", "P2d_evidence_weighted_backtest_prior", "P3_oracle")


def _holm_pick(tstat):
    """Index of the best candidate if its one-sided p passes Holm's first step at 5% (p_min * K < 0.05), else -1."""
    p = 0.5 * np.vectorize(math.erfc)(tstat / math.sqrt(2))
    best = np.argmin(p, axis=1)
    return np.where(p[np.arange(p.shape[0]), best] * p.shape[1] < 0.05, best, -1)


def _count_claims(store, log_e_max, gamma, alpha_true, tb, valid=None):
    """First session at which each candidate's evidence reaches 1 / (0.05 gamma). A claim is false when the claimed
    candidate's null held throughout deployment up to the claim (its true alpha was never > 0), and void when its
    declared loss bound breaks during deployment."""
    hit = log_e_max >= np.log(1.0 / (0.05 * gamma))[..., None]
    first = np.where(hit.any(-1), hit.argmax(-1), -1)
    ever_up = np.maximum.accumulate(alpha_true[:, tb:] > 0, axis=-1)  # (K, td): the null has failed by session t
    for k in range(gamma.shape[1]):
        when = first[:, k]
        made = when >= 0
        if valid is not None:
            store["void"] += int(np.sum(made & ~valid[:, k]))
            made = made & valid[:, k]
        true_at = ever_up[k, np.maximum(when, 0)]
        store["false"] += int(np.sum(made & ~true_at))
        store["true"] += int(np.sum(made & true_at))


def multiverse_world(world: str, n: int, seed: int) -> dict:
    spec = WORLDS[world]
    tb, td, bound = BACKTEST_Y * DAYS, DEPLOY_Y * DAYS, spec["loss_bound"]
    out = {}
    for s_i, (scen, (a_bt, a_d1, a_d2)) in enumerate(spec["scenarios"].items()):
        rng = np.random.default_rng(seed + s_i)
        alpha = np.full((K, tb + td), spec["null_alpha"])
        alpha[0, :tb], alpha[0, tb:tb + 3 * DAYS], alpha[0, tb + 3 * DAYS:] = a_bt, a_d1, a_d2
        res = {p: [] for p in POLICIES}
        claims = {p: {"false": 0, "true": 0, "void": 0} for p in POLICIES[2:7]}
        picked0 = {"P1_backtest_best": 0, "P1h_backtest_holm": 0}
        for lo in range(0, n, 100):
            m = min(100, n - lo)
            r, sc = spy_paths(rng, m, tb + td)
            te = rng.uniform(*spec["te"], size=(m, K))
            x = candidates(rng, r, sc, alpha, te)
            xb, xd = x[..., :tb], x[..., tb:]
            rows = np.arange(m)
            dep = np.log1p(xd).sum(axis=-1) / DEPLOY_Y  # each candidate's deployment log growth a year at lam = 1
            res["P0_spy"].append(np.zeros(m))
            best = np.log1p(xb).sum(axis=-1).argmax(axis=1)
            res["P1_backtest_best"].append(dep[rows, best])
            picked0["P1_backtest_best"] += int(np.sum(best == 0))
            tstat = xb.mean(-1) / (xb.std(-1, ddof=1) / math.sqrt(tb))
            h = _holm_pick(tstat)
            res["P1h_backtest_holm"].append(np.where(h >= 0, dep[rows, np.maximum(h, 0)], 0.0))
            picked0["P1h_backtest_holm"] += int(np.sum(h == 0))
            real_now = alpha[np.maximum(h, 0), tb] > 0
            claims["P1h_backtest_holm"]["false"] += int(np.sum((h >= 0) & ~real_now))
            claims["P1h_backtest_holm"]["true"] += int(np.sum((h >= 0) & real_now))
            free = eprocess_path(xd, FREE_LAMBDAS, None, COST)  # (m, K, td)
            bnd = eprocess_path(xd, bounded_lambdas(bound), None, 0.0, bound)
            broken = np.maximum.accumulate(xd < -bound, axis=-1)  # the declared bound has failed by session t
            used = np.where(broken, free.log_e, bnd.log_e)  # allocation falls back to assumption-free evidence
            soft = np.exp(tstat - tstat.max(axis=1, keepdims=True))
            priors = {"uniform": np.full((m, K), 1.0 / K), "backtest": soft / soft.sum(axis=1, keepdims=True)}
            for pol, g in (("P2_epa_uniform", priors["uniform"]), ("P2b_epa_backtest_prior", priors["backtest"])):
                res[pol].append(np.log(CORE + (1 - CORE) * (g * np.exp(free.log_e[..., -1])).sum(axis=1)) / DEPLOY_Y)
                _count_claims(claims[pol], free.log_e_max, g, alpha, tb)
            for pol, g in (("P2c_evidence_weighted", priors["uniform"]),
                           ("P2d_evidence_weighted_backtest_prior", priors["backtest"])):
                res[pol].append(evidence_weighted_path(xd, used, g, NULL_MASS, COST)[..., -1] / DEPLOY_Y)
                _count_claims(claims[pol], bnd.log_e_max, g, alpha, tb, valid=~bnd.violated)
            lam_o = np.clip(alpha[0, tb:] / te[:, :1] ** 2, 0, 1)  # the oracle knows the true drift and TE
            res["P3_oracle"].append(np.log1p(lam_o * xd[:, 0, :]).sum(-1) / DEPLOY_Y)
        summary = {}
        for pol, vals in res.items():
            v = np.concatenate(vals)
            summary[pol] = {"mean": float(v.mean()), "median": float(np.median(v)), "p5": float(np.quantile(v, 0.05)),
                            "p95": float(np.quantile(v, 0.95)),
                            "share_relative_wealth_below_0.8": float(np.mean(v * DEPLOY_Y < math.log(0.8)))}
        out[scen] = {"candidate0_alpha": {"backtest": a_bt, "deploy_years_0_3": a_d1, "deploy_years_3_10": a_d2},
                     "relative_log_growth_per_year": summary,
                     "claims_per_universe": {p: {k: v / n for k, v in c.items()} for p, c in claims.items()},
                     "picked_candidate0_share": {p: v / n for p, v in picked0.items()}}
    return {"tracking_error_range": spec["te"], "null_alpha": spec["null_alpha"], "loss_bound": bound,
            "scenarios": out}


def battery_multiverse(n: int, seed: int = 20261025) -> dict:
    return {"battery": "multiverse", "universes": n, "candidates": K, "backtest_years": BACKTEST_Y,
            "deployment_years": DEPLOY_Y, "core": CORE, "null_mass": NULL_MASS, "rebalance_cost_per_side": COST,
            "worlds": {w: multiverse_world(w, n, seed + 100 * i) for i, w in enumerate(WORLDS)},
            "note": "relative log growth = annual log growth of the policy's deployment-window book minus SPY's. A claim "
                    "is false when the claimed candidate's true alpha at the claim time is <= 0; P1h's claim is its "
                    "Holm pass on the backtest."}


def main(argv: list[str]) -> int:
    kind, n = argv[1], int(argv[2])
    fn = {"size": battery_size, "velocity": battery_velocity, "multiverse": battery_multiverse}[kind]
    print(json.dumps(fn(n), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
