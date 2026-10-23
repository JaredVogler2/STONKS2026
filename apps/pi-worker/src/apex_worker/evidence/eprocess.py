"""Anytime-valid evidence that a candidate beats a numeraire (SPY, or a twin book), as the wealth of a paper portfolio.

Research only: no orders, alerts, signing or promotion. Method and proofs: docs/evidence/EVIDENCE_TOURNAMENT.md.

Identity (exact). For one settlement period let R_c be the candidate's net simple return, R_n the numeraire's, and
X = (1 + R_c) / (1 + R_n) - 1 the candidate's return in units of the numeraire. A sub-account holding a fraction lam of
its wealth in the candidate and 1 - lam in the numeraire at the start of every period has

    wealth / numeraire wealth = prod_s (1 + lam X_s),     because 1 + lam R_c + (1 - lam) R_n = (1 + R_n)(1 + lam X).

Null H0: E[X_s | everything known when period s starts] <= 0 ("no edge": no mix with the candidate grows faster than
the numeraire in expectation). Under H0 every such product with 0 <= lam <= 1 is a nonnegative supermartingale, and so
is any mixture over lam, so by Ville's inequality P(sup_t E_t >= 1/alpha) <= alpha. The evidence may be read after
every session, forever, with no penalty for looking.

Evidence = money. Every one-period e-variable for this nonparametric null is dominated by 1 + lam X for some lam in
[0, 1], so no assumption-free test accumulates evidence faster than some unlevered paper portfolio accumulates wealth
relative to the numeraire. Faster evidence needs an assumption: `loss_bound = L` declares X >= -L in every period and
admits notional bets up to lam < 1/L. The assumption is checked on every observation; a violation marks the process
INVALID permanently (it is never clipped or repaired) and its evidence may not support a claim.

Costs. Each sub-account is rebalanced back to lam at settlement; the traded notional 2|q - lam| (q is the drifted
weight) is charged at `cost_per_side`. Charges only shrink wealth, so the supermartingale property is preserved, and
a book's rebalancing nets across sub-accounts, so its true cost is never higher (conservative).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

FREE_LAMBDAS: tuple[float, ...] = (1 / 16, 1 / 8, 1 / 4, 1 / 2, 3 / 4, 1.0)
VALID, INVALID = "VALID", "INVALID_ASSUMPTION"


def relative_return(r_candidate, r_numeraire) -> np.ndarray:
    """X = (1 + R_c) / (1 + R_n) - 1: the candidate's simple return in units of the numeraire."""
    rc, rn = np.asarray(r_candidate, dtype=float), np.asarray(r_numeraire, dtype=float)
    if not (np.all(np.isfinite(rc)) and np.all(np.isfinite(rn))):
        raise ValueError("returns must be finite")
    if np.any(rc <= -1) or np.any(rn <= -1):
        raise ValueError("simple returns must exceed -1")
    return (1.0 + rc) / (1.0 + rn) - 1.0


def bounded_lambdas(loss_bound: float, n: int = 10) -> tuple[float, ...]:
    """Geometric notional bets from 1/16 to 0.9 / loss_bound: each keeps 1 + lam X >= 0.1 under the declared bound."""
    if not 0 < loss_bound < 1:
        raise ValueError("loss_bound must be in (0, 1)")
    return tuple(float(v) for v in np.geomspace(1 / 16, 0.9 / loss_bound, n))


def check_grid(lambdas, prior=None, loss_bound: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Validate a bet grid and its prior weights; bets above 1 are notional leverage and need a declared loss bound."""
    lam = np.asarray(lambdas, dtype=float)
    if lam.ndim != 1 or lam.size == 0 or not np.all(np.isfinite(lam)) or np.any(lam <= 0):
        raise ValueError("lambdas must be a non-empty list of positive bets")
    if loss_bound is None:
        if np.any(lam > 1):
            raise ValueError("bets above 1 are notional leverage: declare loss_bound")
    elif not 0 < loss_bound < 1 or np.any(lam * loss_bound >= 1):
        raise ValueError("every bet must satisfy lam * loss_bound < 1")
    w = np.full(lam.size, 1.0 / lam.size) if prior is None else np.asarray(prior, dtype=float)
    if w.shape != lam.shape or np.any(w <= 0) or abs(float(w.sum()) - 1.0) > 1e-9:
        raise ValueError("prior must be positive and sum to 1")
    return lam, w


def logsumexp(a, axis: int = -1) -> np.ndarray:
    a = np.asarray(a, dtype=float)
    m = np.max(a, axis=axis, keepdims=True)
    m = np.where(np.isfinite(m), m, 0.0)
    with np.errstate(divide="ignore"):
        out = np.log(np.sum(np.exp(a - m), axis=axis, keepdims=True)) + m
    return np.squeeze(out, axis=axis)


def log_factors(x, lam: np.ndarray, cost_per_side: float = 0.0) -> np.ndarray:
    """log of each sub-account's one-period wealth factor, shape x.shape + (len(lam),):
    (1 + lam x)(1 - 2c|q - lam|) with q - lam = lam (1 - lam) x / (1 + lam x). A non-positive factor is -inf (ruin)."""
    x = np.asarray(x, dtype=float)[..., None]
    g = 1.0 + lam * x
    safe = np.where(g > 0, g, 1.0)
    cf = 1.0 - 2.0 * cost_per_side * np.abs(lam * (1.0 - lam) * x / safe)
    ok = (g > 0) & (cf > 0)
    return np.where(ok, np.log(safe) + np.log(np.where(ok, cf, 1.0)), -np.inf)


@dataclass(frozen=True)
class EPath:
    """Whole-path evidence. `bet[..., t]` is the effective fraction held in the candidate DURING period t: it is
    computed from periods before t only (predictable)."""

    log_e: np.ndarray  # (..., T) log evidence after each period
    log_e_max: np.ndarray  # (..., T) running maximum: 1 / exp(log_e_max) is the anytime-valid p-value
    bet: np.ndarray  # (..., T)
    violated: np.ndarray  # (...,) the declared loss bound was broken (always False without one)


def eprocess_path(x, lambdas=FREE_LAMBDAS, prior=None, cost_per_side: float = 0.0,
                  loss_bound: float | None = None) -> EPath:
    """The mixture e-process over `lambdas` for relative returns x[..., t] (time on the last axis)."""
    lam, w = check_grid(lambdas, prior, loss_bound)
    x = np.asarray(x, dtype=float)
    if x.ndim == 0 or x.shape[-1] == 0:
        raise ValueError("x needs at least one period on its last axis")
    if not np.all(np.isfinite(x)) or np.any(x <= -1):
        raise ValueError("relative returns must be finite and exceed -1")
    lw = np.cumsum(log_factors(x, lam, cost_per_side), axis=-2) + np.log(w)
    log_e = logsumexp(lw, axis=-1)
    start = np.broadcast_to(np.log(w), lw[..., :1, :].shape)
    prev = np.concatenate([start, lw[..., :-1, :]], axis=-2)
    bet = np.sum(np.exp(prev - logsumexp(prev, axis=-1)[..., None]) * lam, axis=-1)
    violated = np.zeros(x.shape[:-1], dtype=bool) if loss_bound is None else np.any(x < -loss_bound, axis=-1)
    return EPath(log_e, np.maximum.accumulate(log_e, axis=-1), bet, violated)


class EProcess:
    """Streaming form of `eprocess_path` (one observation per settlement) for a prospective lane."""

    def __init__(self, lambdas=FREE_LAMBDAS, prior=None, cost_per_side: float = 0.0, loss_bound: float | None = None):
        if not 0 <= cost_per_side < 0.5:
            raise ValueError("cost_per_side is a fraction of notional in [0, 0.5)")
        self.lam, w = check_grid(lambdas, prior, loss_bound)
        self.log_prior = np.log(w)
        self.cost_per_side, self.loss_bound = float(cost_per_side), loss_bound
        self.log_w = np.zeros(self.lam.size)
        self.n, self.log_e_max, self.violation = 0, 0.0, None

    @property
    def log_e(self) -> float:
        return float(logsumexp(self.log_prior + self.log_w))

    @property
    def e(self) -> float:
        return math.exp(min(self.log_e, 700.0))

    @property
    def status(self) -> str:
        return VALID if self.violation is None else INVALID

    def bet(self) -> float:
        """Effective fraction in the candidate for the NEXT period (wealth-weighted mean bet)."""
        a = self.log_prior + self.log_w
        return float(np.exp(a - logsumexp(a)) @ self.lam)

    def update(self, x: float, label: str | None = None) -> float:
        x = float(x)
        if not math.isfinite(x) or x <= -1:
            raise ValueError("a relative return must be finite and exceed -1")
        if self.loss_bound is not None and x < -self.loss_bound and self.violation is None:
            self.violation = {"period": self.n, "x": x, "label": label}
        self.log_w = self.log_w + log_factors(x, self.lam, self.cost_per_side)
        self.n += 1
        self.log_e_max = max(self.log_e_max, self.log_e)
        return self.log_e

    def p_anytime(self) -> float:
        """1 / sup e: P(this ever falls to alpha or below) <= alpha under H0. An invalid process carries no evidence."""
        return 1.0 if self.violation is not None else min(1.0, math.exp(-self.log_e_max))

    def state(self) -> dict:
        return {"periods": self.n, "e": self.e, "e_max": math.exp(min(self.log_e_max, 700.0)),
                "p_anytime": self.p_anytime(), "bet_next": self.bet(), "status": self.status,
                "loss_bound": self.loss_bound, "violation": self.violation}


def claim_threshold(alpha: float, prior_mass: float = 1.0) -> float:
    """Evidence needed for a claim on a hypothesis carrying `prior_mass` of a total budget of 1 (online FWER <= alpha)."""
    if not 0 < alpha < 1 or not 0 < prior_mass <= 1:
        raise ValueError("alpha and prior_mass must be in (0, 1)")
    return 1.0 / (alpha * prior_mass)


def e_bh(e_values: dict[str, float], alpha: float) -> list[str]:
    """e-BH (Wang & Ramdas 2022): reject the k* largest e-values, k* = max{k : e_(k) >= K / (alpha k)}. FDR <= alpha
    under arbitrary dependence, for e-values read at a fixed or stopping time."""
    ranked = sorted(e_values.items(), key=lambda kv: (-kv[1], kv[0]))
    big_k, k_star = len(ranked), 0
    for k, (_, e) in enumerate(ranked, start=1):
        if e >= big_k / (alpha * k):
            k_star = k
    return [name for name, _ in ranked[:k_star]]
