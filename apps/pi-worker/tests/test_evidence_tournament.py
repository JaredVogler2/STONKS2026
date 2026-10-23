"""Evidence engine: the perpetual prospective tournament (docs/evidence/EVIDENCE_TOURNAMENT.md section 3)."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from apex_worker.evidence.eprocess import FREE_LAMBDAS, eprocess_path, relative_return
from apex_worker.evidence.tournament import EDGE, EDGE_BOUNDED, MECHANISM, Tournament, evidence_weighted_path


def _days(n, start=0):
    return [f"2027-{1 + (start + i) // 28:02d}-{1 + (start + i) % 28:02d}" for i in range(n)]


def _paths(rng, t, drifts, te=0.12):
    r_s = 0.0003 + 0.011 * rng.standard_t(5, t) / math.sqrt(5 / 3)
    out = {k: r_s + d / 252 + te / math.sqrt(252) * rng.standard_t(5, t) / math.sqrt(5 / 3) for k, d in drifts.items()}
    return r_s, out


def test_mixture_book_identity_floor_and_long_only_allocation():
    rng = np.random.default_rng(11)
    days = _days(300)
    r_s, rc = _paths(rng, 300, {"winner": 0.6, "loser": -0.3})
    t = Tournament(book="mixture", null_mass=0.4, cost_per_side=0.0002)
    t.admit("winner", 0.3, days[0])
    t.admit("loser", 0.3, days[0])
    for i, d in enumerate(days):
        alloc = t.allocation(d)
        assert all(v >= -1e-12 for v in alloc.values()) and math.isclose(sum(alloc.values()), 1.0)
        rep = t.settle(d, r_s[i], {k: v[i] for k, v in rc.items()})
        g = 0.4 + 0.3 * t.spy["winner"].e + 0.3 * t.spy["loser"].e  # unassigned mass 0.4 stays in SPY
        assert math.isclose(rep["global_e"], g) and math.isclose(rep["book_relative_wealth"], 0.4 + 0.6 * g)
        assert rep["book_relative_wealth"] >= 0.4
    alloc = t.allocation()
    assert alloc["winner"] > alloc["loser"] > 0  # capital followed evidence
    assert rep["mode"] == "RESEARCH_ONLY" and rep["orders"] == "none" and rep["group_actionable"] is False
    json.dumps(rep)


def test_mixture_book_wealth_is_the_paper_portfolio_relative_to_spy():
    """Simulate the actual holdings (null mass in SPY + per-candidate sub-accounts) against the reported wealth."""
    rng = np.random.default_rng(12)
    days = _days(120)
    r_s, rc = _paths(rng, 120, {"a": 0.2, "b": 0.0})
    t = Tournament(book="mixture", null_mass=0.5, cost_per_side=0.0)
    t.admit("a", 0.5, days[0])
    t.admit("b", 0.25, days[0])
    lam, w = np.asarray(FREE_LAMBDAS), 1 / len(FREE_LAMBDAS)
    subs = {k: (1 - 0.5) * m * w * np.ones(lam.size) for k, m in (("a", 0.5), ("b", 0.25))}
    spy_only, spy = (0.5 + 0.5 * 0.25), 1.0
    for i, d in enumerate(days):
        t.settle(d, r_s[i], {"a": rc["a"][i], "b": rc["b"][i]})
        for k, v in subs.items():  # rebalanced sub-accounts: value * (lam (1 + R_c) + (1 - lam)(1 + R_s))
            subs[k] = v * (lam * (1 + rc[k][i]) + (1 - lam) * (1 + r_s[i]))
        spy_only *= 1 + r_s[i]
        spy *= 1 + r_s[i]
        book = spy_only + sum(v.sum() for v in subs.values())
        assert math.isclose(book / spy, t.book_relative_wealth, rel_tol=1e-9)


def test_evidence_weighted_book_streaming_matches_the_path_and_follows_evidence():
    rng = np.random.default_rng(15)
    days = _days(400)
    r_s, rc = _paths(rng, 400, {"good": 0.5, "flat": 0.0, "bad": -0.4})
    names, gamma = list(rc), np.array([0.3, 0.3, 0.3])
    x = np.stack([relative_return(rc[k], r_s) for k in names])
    path = evidence_weighted_path(x, eprocess_path(x, FREE_LAMBDAS, None, 0.0002).log_e, gamma, 0.5, 0.0002)
    t = Tournament(null_mass=0.5, cost_per_side=0.0002)
    for k, g in zip(names, gamma, strict=True):
        t.admit(k, float(g), days[0])
    for i, d in enumerate(days):
        w = t.allocation(d)
        assert all(v >= 0 for v in w.values()) and math.isclose(sum(w.values()), 1.0)
        t.settle(d, r_s[i], {k: rc[k][i] for k in names})
        assert math.isclose(math.log(t.book_relative_wealth), path[i], rel_tol=1e-9, abs_tol=1e-12)
    w = t.allocation()
    assert w["good"] > w["flat"] > w["bad"] and w["SPY"] > 0


def test_evidence_weighted_book_is_a_supermartingale_under_the_global_null():
    rng = np.random.default_rng(16)
    n, k, steps = 500, 4, 1000
    r_s = 0.0003 + 0.011 * rng.standard_t(4, (n, 1, steps)) / math.sqrt(2)
    r_c = np.maximum(r_s + 0.15 / math.sqrt(252) * rng.standard_t(4, (n, k, steps)) / math.sqrt(2), -0.9)
    x = relative_return(r_c, np.broadcast_to(r_s, r_c.shape))
    log_b = evidence_weighted_path(x, eprocess_path(x).log_e, np.full(k, 1 / k), 0.5, 0.0002)
    assert np.mean(log_b.max(axis=1) >= math.log(20)) <= 0.05
    b = np.exp(log_b[:, -1])
    assert b.mean() <= 1 + 3 * b.std() / math.sqrt(n)


def test_admission_rules():
    t = Tournament()
    t.admit("a", 0.5, "2027-01-04")
    with pytest.raises(ValueError, match="already admitted"):
        t.admit("a", 0.1, "2027-01-05")
    with pytest.raises(ValueError, match="exceed 1"):
        t.admit("b", 0.6, "2027-01-05")
    with pytest.raises(ValueError, match="slug"):
        t.admit("Bad Id", 0.1, "2027-01-05")
    t.settle("2027-01-04", 0.001, {"a": 0.002})
    with pytest.raises(ValueError, match="before the first period"):
        t.admit("c", 0.1, "2027-01-04")
    with pytest.raises(ValueError, match="strictly increase"):
        t.settle("2027-01-04", 0.0, {"a": 0.0})
    with pytest.raises(ValueError, match="exactly the active"):
        t.settle("2027-01-05", 0.0, {})
    with pytest.raises(ValueError, match="exactly the active"):
        t.settle("2027-01-05", 0.0, {"a": 0.0, "ghost": 0.0})
    with pytest.raises(ValueError):
        Tournament(lambdas=(0.5, 2.0))  # the book may not be levered
    with pytest.raises(ValueError):
        Tournament(book="kelly")


def test_late_admission_waits_in_spy_and_starts_at_one():
    t = Tournament(null_mass=0.25)
    t.admit("early", 0.5, "2027-01-04")
    t.admit("late", 0.25, "2027-02-01")
    t.settle("2027-01-04", 0.01, {"early": 0.03})
    assert t.spy["late"].n == 0 and t.spy["late"].e == 1.0 and "late" not in t.allocation()
    assert math.isclose(t.global_e, 0.25 + 0.5 * t.spy["early"].e + 0.25)
    t.settle("2027-02-01", 0.0, {"early": 0.0, "late": 0.01})
    assert t.spy["late"].n == 1 and t.allocation()["late"] > 0


def test_claims_edge_mechanism_and_bounded_void():
    rng = np.random.default_rng(13)
    days = _days(1500)
    r_s, rc = _paths(rng, 1500, {"skill": 0.8, "beta": 0.8}, te=0.12)
    twin = {"skill": r_s, "beta": rc["beta"]}  # "beta" has no selection skill: it equals its own twin
    t = Tournament(evidence_loss_bound=0.25, alpha=0.2)  # threshold 1 / (0.2 * 0.5) = 10
    t.admit("skill", 0.5, days[0], twin=True)
    t.admit("beta", 0.5, days[0], twin=True)
    for i, d in enumerate(days):
        t.settle(d, r_s[i], {k: v[i] for k, v in rc.items()}, {k: v[i] for k, v in twin.items()})
    c = t.claims
    assert EDGE in c["skill"] and MECHANISM in c["skill"] and EDGE_BOUNDED in c["skill"]
    assert EDGE in c["beta"] and MECHANISM not in c["beta"]  # beats SPY, but not its own negative control
    assert t.twin["beta"].e == pytest.approx(1.0)
    assert t.report()["candidates"]["skill"]["threshold"] == pytest.approx(10.0)
    t.settle("2028-01-01", 0.0, {"skill": -0.3, "beta": 0.0}, {"skill": 0.0, "beta": 0.0})  # breaks X >= -0.25
    assert t.claims["skill"][EDGE_BOUNDED].startswith("VOID") and t.bounded["skill"].status != "VALID"


def test_global_null_size_and_pathwise_regret():
    rng = np.random.default_rng(14)
    n, k, steps = 400, 4, 1000
    r_s = 0.0003 + 0.011 * rng.standard_t(4, (n, 1, steps)) / math.sqrt(2)
    r_c = r_s + 0.15 / math.sqrt(252) * rng.standard_t(4, (n, k, steps)) / math.sqrt(2)
    x = relative_return(np.maximum(r_c, -0.9), np.broadcast_to(r_s, r_c.shape))
    ep = eprocess_path(x)
    g = np.exp(ep.log_e).mean(axis=1)  # equal prior masses summing to 1: the global e-process G
    assert np.mean(np.maximum.accumulate(g, axis=1)[:, -1] >= 20) <= 0.05
    lam = np.asarray(FREE_LAMBDAS)
    log_w = np.log1p(lam * x[..., None]).sum(axis=2)  # (n, k, M) final sub-account log wealth
    bound = np.log(1 / k) + np.log(1 / lam.size) + log_w.max(axis=(1, 2))
    assert np.all(np.log(g[:, -1]) >= bound - 1e-9)
