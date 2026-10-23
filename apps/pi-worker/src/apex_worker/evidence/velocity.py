"""Evidence velocity: how fast a candidate can be proved to beat its numeraire, and the limit no method can beat.

Continuous-time approximation (period returns are small). Let mu be the annual drift of X (the candidate's return in
numeraire units) and s its annual volatility (tracking error). A constant bet lam grows log-evidence at
g(lam) = lam mu - lam^2 s^2 / 2 a year. Three rates:
* information limit, IR^2 / 2 a year with IR = mu / s: the Kullback-Leibler rate of the Gaussian model. No test
  (classical, Bayesian, machine-learned or betting, even one told the true alternative) gathers expected
  log-evidence faster;
* assumption-free, max over 0 <= lam <= 1 of g: what an unlevered paper book can earn, at most the candidate's own log
  growth relative to the numeraire once mu >= s^2 ("evidence = money");
* bounded loss, lam up to 0.9 / L for a declared and checked X >= -L: approaches IR^2 / 2 for low tracking-error designs.

Time to evidence 1/alpha is about log(1/alpha) / g years: the level-crossing time of a drifted Brownian motion
(inverse Gaussian, mean a / g). `crossing_probability` gives the chance of reaching it within T years. A
fixed-horizon one-sided z-test needs ((z_{1-alpha} + z_{power}) / IR)^2 years, and may be read only once.
"""

from __future__ import annotations

import math
from statistics import NormalDist

_N = NormalDist()


def growth(mu: float, s: float, lam: float) -> float:
    return lam * mu - 0.5 * lam * lam * s * s


def best_bet(mu: float, s: float, lam_max: float = 1.0) -> tuple[float, float]:
    """(lam*, g*) for a constant bet capped at lam_max."""
    if s <= 0:
        raise ValueError("tracking error must be positive")
    if mu <= 0:
        return 0.0, 0.0
    lam = min(mu / (s * s), lam_max)
    return lam, growth(mu, s, lam)


def information_rate(mu: float, s: float) -> float:
    return 0.5 * (mu / s) ** 2 if mu > 0 else 0.0


def years_to(level: float, g: float) -> float:
    return math.inf if g <= 0 else math.log(level) / g


def _log_sf(b: float) -> float:
    """log P(Z > b) for a standard normal Z, stable for large b."""
    if b < 30:
        return math.log(max(0.5 * math.erfc(b / math.sqrt(2)), 1e-300))
    return -0.5 * b * b - math.log(b * math.sqrt(2 * math.pi)) + math.log1p(-1 / (b * b))


def crossing_probability(g: float, vol: float, level: float, years: float) -> float:
    """P(sup_{t <= T} log-evidence >= log(level)) for log-evidence = g t + vol W_t (first passage, drift g)."""
    a = math.log(level)
    if years <= 0 or vol <= 0:
        return float(g * years >= a)
    rt = vol * math.sqrt(years)
    first = _N.cdf((g * years - a) / rt)
    second = math.exp(min(2 * g * a / (vol * vol) + _log_sf((a + g * years) / rt), 0.0))
    return min(1.0, first + second)


def fixed_horizon_years(ir: float, alpha: float = 0.05, power: float = 0.5) -> float:
    """Sample length a one-sided z-test needs to reach `power` at level alpha (read once, at the horizon)."""
    if ir <= 0:
        return math.inf
    return ((_N.inv_cdf(1 - alpha) + _N.inv_cdf(power)) / ir) ** 2


def velocity(name: str, log_growth: float, te: float, *, alpha: float = 0.05,
             loss_bound: float | None = None, horizons: tuple[float, ...] = (5, 10, 20)) -> dict:
    """Velocity row for a candidate described by its log growth relative to the numeraire at lam = 1 (what the
    program's reports call active log growth) and its tracking error."""
    mu = log_growth + 0.5 * te * te
    level = 1 / alpha
    lam_f, g_f = best_bet(mu, te, 1.0)
    row = {"name": name, "log_growth": log_growth, "tracking_error": te, "drift": mu, "ir": mu / te, "alpha": alpha,
           "information_limit": {"rate": information_rate(mu, te), "years": years_to(level, information_rate(mu, te))},
           "assumption_free": {"bet": lam_f, "rate": g_f, "years": years_to(level, g_f),
                               "p_within": {str(h): crossing_probability(g_f, lam_f * te, level, h) for h in horizons}},
           "fixed_horizon_z": {"years_power_50": fixed_horizon_years(mu / te, alpha, 0.5),
                               "years_power_80": fixed_horizon_years(mu / te, alpha, 0.8)}}
    if loss_bound is not None:
        lam_b, g_b = best_bet(mu, te, 0.9 / loss_bound)
        row["bounded_loss"] = {"loss_bound": loss_bound, "bet": lam_b, "rate": g_b, "years": years_to(level, g_b),
                               "p_within": {str(h): crossing_probability(g_b, lam_b * te, level, h) for h in horizons}}
    return row
