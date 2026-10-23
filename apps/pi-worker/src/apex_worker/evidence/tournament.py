"""A perpetual prospective evidence tournament: evidence-proportional allocation of a paper book (EPA).

Research only: a paper book, no orders, alerts or promotion. Method: docs/evidence/EVIDENCE_TOURNAMENT.md section 3.

Candidates. Candidate k receives prior mass gamma_k of a total budget of 1, fixed when it is admitted (before the
first period it takes part in), so candidates can be admitted forever. Its evidence against SPY is the relative wealth
of its lam sub-accounts (assumption-free, `eprocess`), optionally also under a declared and checked loss bound, and
optionally against its own twin (a negative-control numeraire: the same mask, schedule and sizes, random selection).

Claims (every one holds under continuous monitoring, over every candidate ever admitted):
* edge: claim k when its assumption-free evidence reaches 1 / (alpha gamma_k); P(any false claim) <= alpha sum(gamma);
* edge under the declared loss bound: the same threshold on the bounded-loss evidence while the bound holds; a later
  break voids the claim;
* mechanism: both the edge evidence and the twin evidence reached the threshold. It is false only if one of the two
  nulls is true, so its error is also at most alpha gamma_k;
* selection: the twin evidence alone reached the threshold (the candidate beats its own negative control, which removes
  beta, sector and universe effects); with a declared twin loss bound, also on the bounded twin evidence while it holds.
  Each claim type is its own family, with the family-wise guarantee above;
* global: G = (1 - sum gamma) + sum gamma_k e_k is a nonnegative supermartingale if no candidate beats SPY.

The paper book, relative to SPY. A prior mass `null_mass` (u) belongs to "no candidate beats SPY" and is held in SPY.
* `book="evidence_weighted"` (default): candidate k is held at weight (1 - u) gamma_k eps_k / (u + (1 - u) G_eps), where
  eps is the allocation evidence (the bounded-loss evidence while its bound holds, else the assumption-free evidence)
  and G_eps the matching global sum, all read BEFORE the period (predictable), and the rest is in SPY. Weights are
  long-only and sum to at most 1, so the book's wealth relative to SPY is itself a nonnegative supermartingale under
  the global null. Capital follows evidence: Bayesian model averaging with e-values as likelihood ratios.
* `book="mixture"`: the literal identity. The book holds every lam sub-account, so its wealth relative to SPY is
  exactly B = u + (1 - u) G. Pathwise, B >= u, and log G >= log(gamma_k w_j) + log W_kj for every candidate k and
  bet j (regret against any sub-account in hindsight).
Backtests (contaminated DEV history included) may set the prior masses: they are fixed before the prospective data
arrive, which is all validity needs. Backtests can never supply evidence.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass

import numpy as np

from apex_worker.evidence.eprocess import (
    FREE_LAMBDAS,
    VALID,
    EProcess,
    bounded_lambdas,
    check_grid,
    claim_threshold,
    e_bh,
    logsumexp,
    relative_return,
)

SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
EDGE, MECHANISM, EDGE_BOUNDED = "EDGE_VS_SPY", "EDGE_AND_MECHANISM", "EDGE_VS_SPY_BOUNDED_LOSS"
SELECTION, SELECTION_BOUNDED = "SELECTION_VS_TWIN", "SELECTION_VS_TWIN_BOUNDED_LOSS"
BOOKS = ("evidence_weighted", "mixture")


def evidence_weighted_path(x, log_e, gamma, null_mass: float = 0.5, cost_per_side: float = 0.0002) -> np.ndarray:
    """Whole-path evidence-weighted book: log relative wealth after each period, shape (..., T).

    x[..., K, T] are the candidates' relative returns; log_e[..., K, T] their allocation evidence AFTER each period
    (the book uses it one period later); gamma[..., K] their prior masses (summing to at most 1; the rest is unassigned
    and treated as evidence 1). The book starts in SPY; every change of weights is charged at `cost_per_side`."""
    x, log_e = np.asarray(x, dtype=float), np.asarray(log_e, dtype=float)
    g = np.broadcast_to(np.asarray(gamma, dtype=float), x.shape[:-1])
    prev = np.concatenate([np.zeros((*x.shape[:-1], 1)), log_e[..., :-1]], axis=-1)
    lg = np.log(g)[..., None] + prev + math.log(1 - null_mass)
    base = np.log(null_mass + (1 - null_mass) * np.maximum(1 - g.sum(axis=-1), 0.0))  # SPY: null + unassigned mass
    base = np.broadcast_to(base[..., None, None], (*x.shape[:-2], 1, x.shape[-1]))
    a = np.exp(lg - logsumexp(np.concatenate([base, lg], axis=-2), axis=-2)[..., None, :])
    y = (a * x).sum(axis=-2)
    drifted = np.concatenate([np.zeros((*x.shape[:-1], 1)),
                              a[..., :-1] * (1 + x[..., :-1]) / (1 + y[..., None, :-1])], axis=-1)
    turn = np.abs(a - drifted).sum(axis=-2)
    return np.cumsum(np.log1p(y) + np.log1p(-2 * cost_per_side * turn), axis=-1)


@dataclass(frozen=True)
class Entry:
    candidate_id: str
    prior_mass: float
    admitted: str  # ISO date of the first settlement the candidate takes part in
    twin: bool = False
    note: str = ""
    loss_bound: float | None = None  # declared X >= -L against SPY (None: the tournament's default)
    twin_loss_bound: float | None = None  # declared X >= -L against the twin (None: no bounded twin evidence)


class Tournament:
    def __init__(self, *, null_mass: float = 0.5, book: str = "evidence_weighted", lambdas=FREE_LAMBDAS, prior=None,
                 cost_per_side: float = 0.0002, alpha: float = 0.05, evidence_loss_bound: float | None = None):
        check_grid(lambdas, prior, None)  # sub-accounts are real long-only, unlevered paper portfolios: bets <= 1
        if not 0 < null_mass < 1 or not 0 < alpha < 1 or book not in BOOKS:
            raise ValueError(f"null_mass and alpha must be in (0, 1) and book one of {BOOKS}")
        self.null_mass, self.book, self.alpha = float(null_mass), book, float(alpha)
        self.cost_per_side, self.lambdas, self.prior = float(cost_per_side), tuple(lambdas), prior
        self.evidence_loss_bound = evidence_loss_bound
        self.entries: dict[str, Entry] = {}
        self.spy: dict[str, EProcess] = {}
        self.twin: dict[str, EProcess] = {}
        self.bounded: dict[str, EProcess] = {}
        self.twin_bounded: dict[str, EProcess] = {}
        self.claims: dict[str, dict] = {}
        self.holdings: dict[str, float] = {}  # evidence-weighted book: candidate weights drifted to the last close
        self.last_session: str | None = None
        self.sessions, self.log_book, self.log_g_max = 0, 0.0, 0.0

    # ---- admission ------------------------------------------------------------------------------------------------
    @property
    def mass_admitted(self) -> float:
        return sum(e.prior_mass for e in self.entries.values())

    def admit(self, candidate_id: str, prior_mass: float, session: str, *, twin: bool = False, note: str = "",
              loss_bound: float | None = None, twin_loss_bound: float | None = None) -> Entry:
        if not SLUG.fullmatch(candidate_id or ""):
            raise ValueError("candidate_id must be a lower-case slug")
        if candidate_id in self.entries:
            raise ValueError(f"{candidate_id} is already admitted; a changed candidate is a new candidate")
        if self.last_session is not None and session <= self.last_session:
            raise ValueError("a candidate must be admitted before the first period it takes part in")
        if not 0 < prior_mass <= 1 or self.mass_admitted + prior_mass > 1 + 1e-12:
            raise ValueError("prior masses are positive and may not exceed 1 in total")
        if twin_loss_bound is not None and not twin:
            raise ValueError("a twin loss bound needs a twin")
        bound = self.evidence_loss_bound if loss_bound is None else loss_bound
        entry = Entry(candidate_id, float(prior_mass), session, twin, note, bound, twin_loss_bound)
        self.entries[candidate_id] = entry
        self.spy[candidate_id] = EProcess(self.lambdas, self.prior, self.cost_per_side)
        if twin:
            self.twin[candidate_id] = EProcess(self.lambdas, self.prior, self.cost_per_side)
        # bounded-loss evidence is a test statistic, not a portfolio: no rebalancing charge
        if bound is not None:
            self.bounded[candidate_id] = EProcess(bounded_lambdas(bound), None, 0.0, bound)
        if twin_loss_bound is not None:
            self.twin_bounded[candidate_id] = EProcess(bounded_lambdas(twin_loss_bound), None, 0.0, twin_loss_bound)
        return entry

    def active(self, session: str) -> list[str]:
        return [k for k, e in self.entries.items() if e.admitted <= session]

    # ---- evidence and allocation ----------------------------------------------------------------------------------
    @property
    def global_e(self) -> float:
        return (1.0 - self.mass_admitted) + sum(e.prior_mass * self.spy[k].e for k, e in self.entries.items())

    def allocation_evidence(self, k: str) -> float:
        b = self.bounded.get(k)
        return b.e if b is not None and b.status == VALID else self.spy[k].e

    @property
    def book_relative_wealth(self) -> float:
        if self.book == "mixture":
            return self.null_mass + (1.0 - self.null_mass) * self.global_e
        return math.exp(self.log_book)

    def allocation(self, for_session: str | None = None) -> dict[str, float]:
        """Weights for the period `for_session` (default: the last settled session's candidates), decided from
        evidence before it: each active candidate's share of the book, the rest in SPY."""
        nxt, u = for_session or self.last_session or "", self.null_mass
        act = [k for k, e in self.entries.items() if e.admitted <= nxt]
        if self.book == "mixture":
            b = self.book_relative_wealth
            out = {k: (1 - u) * self.entries[k].prior_mass * self.spy[k].e * self.spy[k].bet() / b for k in act}
        else:
            eps = {k: self.allocation_evidence(k) for k in self.entries}
            den = u + (1 - u) * ((1.0 - self.mass_admitted) + sum(e.prior_mass * eps[k] for k, e in self.entries.items()))
            out = {k: (1 - u) * self.entries[k].prior_mass * eps[k] / den for k in act}
        out["SPY"] = 1.0 - sum(out.values())
        return out

    # ---- settlement -----------------------------------------------------------------------------------------------
    def settle(self, session: str, r_spy: float, returns: dict[str, float],
               twin_returns: dict[str, float] | None = None) -> dict:
        """One settlement period: SPY's return and every active candidate's net return (and its twin's, if any)."""
        if self.last_session is not None and session <= self.last_session:
            raise ValueError("sessions must strictly increase")
        act = self.active(session)
        if set(returns) != set(act):
            raise ValueError(f"returns must cover exactly the active candidates {sorted(act)}")
        twin_returns = twin_returns or {}
        need = {k for k in act if k in self.twin}
        if set(twin_returns) != need:
            raise ValueError(f"twin returns must cover exactly {sorted(need)}")
        xs = {k: float(relative_return(returns[k], r_spy)) for k in act}
        if self.book == "evidence_weighted":
            w = self.allocation(session)
            y = sum(w[k] * xs[k] for k in act)
            turn = sum(abs(w.get(k, 0.0) - self.holdings.get(k, 0.0)) for k in (set(w) - {"SPY"}) | set(self.holdings))
            self.log_book += math.log1p(y) + math.log1p(-2 * self.cost_per_side * turn)
            self.holdings = {k: w[k] * (1 + xs[k]) / (1 + y) for k in act}
        for k in act:
            self.spy[k].update(xs[k], session)
            if k in self.bounded:
                self.bounded[k].update(xs[k], session)
            if k in self.twin:
                xt = float(relative_return(returns[k], twin_returns[k]))
                self.twin[k].update(xt, session)
                if k in self.twin_bounded:
                    self.twin_bounded[k].update(xt, session)
        self.last_session, self.sessions = session, self.sessions + 1
        self.log_g_max = max(self.log_g_max, math.log(self.global_e))
        self._claims(session)
        return self.report()

    def _claims(self, session: str) -> None:
        for k, entry in self.entries.items():
            thr, made = math.log(claim_threshold(self.alpha, entry.prior_mass)), self.claims.get(k, {})
            if EDGE not in made and self.spy[k].log_e_max >= thr:
                self.claims.setdefault(k, {})[EDGE] = session
            if MECHANISM not in made and k in self.twin and min(self.spy[k].log_e_max, self.twin[k].log_e_max) >= thr:
                self.claims.setdefault(k, {})[MECHANISM] = session
            if SELECTION not in made and k in self.twin and self.twin[k].log_e_max >= thr:
                self.claims.setdefault(k, {})[SELECTION] = session
            for kind, b in ((EDGE_BOUNDED, self.bounded.get(k)), (SELECTION_BOUNDED, self.twin_bounded.get(k))):
                made = self.claims.get(k, {})
                if b is not None and b.status != VALID and kind in made:
                    made[kind] = f"VOID: loss bound broken on {b.violation['label']}"
                elif b is not None and b.status == VALID and kind not in made and b.log_e_max >= thr:
                    self.claims.setdefault(k, {})[kind] = session

    def report(self) -> dict:
        alloc = self.allocation()
        cands = {}
        for k, entry in self.entries.items():
            row = {"entry": asdict(entry), "threshold": claim_threshold(self.alpha, entry.prior_mass),
                   "vs_spy": self.spy[k].state(), "allocation": alloc.get(k, 0.0), "claims": self.claims.get(k, {})}
            if k in self.twin:
                row["vs_twin"] = self.twin[k].state()
            if k in self.bounded:
                row["vs_spy_bounded_loss"] = self.bounded[k].state()
            if k in self.twin_bounded:
                row["vs_twin_bounded_loss"] = self.twin_bounded[k].state()
            cands[k] = row
        seen = {k: self.spy[k].e for k in self.entries if self.spy[k].n > 0}
        return {"sessions": self.sessions, "last_session": self.last_session, "book": self.book,
                "null_mass": self.null_mass, "alpha": self.alpha, "book_relative_wealth": self.book_relative_wealth,
                "global_e": self.global_e, "global_p_anytime": min(1.0, math.exp(-self.log_g_max)),
                "mass_unassigned": 1.0 - self.mass_admitted, "allocation_spy": alloc["SPY"], "candidates": cands,
                "e_bh_now": e_bh(seen, self.alpha) if seen else [],
                "mode": "RESEARCH_ONLY", "orders": "none", "group_actionable": False}
