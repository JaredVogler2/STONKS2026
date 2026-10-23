"""Evidence engine: the betting e-process (docs/evidence/EVIDENCE_TOURNAMENT.md sections 1-2)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from apex_worker.evidence.eprocess import (
    FREE_LAMBDAS,
    INVALID,
    VALID,
    EProcess,
    bounded_lambdas,
    check_grid,
    claim_threshold,
    e_bh,
    eprocess_path,
    log_factors,
    relative_return,
)


def _market(rng, n, t, drift=0.0, te=0.15):
    """Fat-tailed SPY and candidate daily returns with a shared volatility regime; drift is the candidate's alpha."""
    vol = np.where(rng.random((n, t)) < 0.2, 2.5, 1.0)
    r_s = 0.0003 + 0.011 * vol * rng.standard_t(4, (n, t)) / math.sqrt(2)
    r_c = r_s + drift / 252 + te / math.sqrt(252) * vol * rng.standard_t(4, (n, t)) / math.sqrt(2)
    return np.maximum(r_c, -0.9), np.maximum(r_s, -0.9)


def test_identity_sub_account_relative_wealth_equals_portfolio_over_spy():
    rng = np.random.default_rng(1)
    r_c, r_s = (a[0] for a in _market(rng, 1, 300, drift=0.05))
    lam, cost = np.asarray(FREE_LAMBDAS), 0.0007
    x = relative_return(r_c, r_s)
    log_w = log_factors(x, lam, cost).sum(axis=0)
    for j, lj in enumerate(lam):  # an explicit portfolio: lam in the candidate, 1 - lam in SPY, rebalanced each period
        c_val, s_val, spy = lj, 1 - lj, 1.0
        for t in range(x.size):
            c_val, s_val, spy = c_val * (1 + r_c[t]), s_val * (1 + r_s[t]), spy * (1 + r_s[t])
            v = c_val + s_val
            traded = abs(c_val - lj * v) + abs(s_val - (1 - lj) * v)
            v *= 1 - cost * traded / v
            c_val, s_val = lj * v, (1 - lj) * v
        assert math.isclose(math.log(v / spy), log_w[j], rel_tol=1e-9, abs_tol=1e-12)


def test_mixture_is_one_portfolio_holding_the_predictable_wealth_weighted_bet():
    rng = np.random.default_rng(2)
    x = relative_return(*(a[0] for a in _market(rng, 1, 400, drift=0.08)))
    p = eprocess_path(x)
    e = np.exp(p.log_e)
    prev = np.concatenate([[1.0], e[:-1]])
    assert np.allclose(e, prev * (1 + p.bet * x), rtol=1e-10)  # e_t = e_{t-1} (1 + bet_t X_t), bet_t known before X_t
    assert np.all((p.bet > 0) & (p.bet <= 1))


def test_bet_is_predictable():
    rng = np.random.default_rng(3)
    x = relative_return(*(a[0] for a in _market(rng, 1, 200)))
    y = x.copy()
    y[120] = 0.3  # change the future
    a, b = eprocess_path(x), eprocess_path(y)
    assert np.array_equal(a.bet[:121], b.bet[:121]) and not np.array_equal(a.bet[121:], b.bet[121:])


def test_ville_size_under_continuous_monitoring_and_naive_peeking_is_not():
    rng = np.random.default_rng(4)
    x = relative_return(*_market(rng, 600, 1500))  # exact null: E[X | past] = 0
    p = eprocess_path(x, FREE_LAMBDAS, None, 0.0002)
    assert np.mean(p.log_e_max[:, -1] >= math.log(20)) <= 0.05
    pb = eprocess_path(x, bounded_lambdas(0.5), None, 0.0, 0.5)  # t(4) tails break X >= -0.5 on a few paths
    assert pb.violated.mean() < 0.02 and np.mean((pb.log_e_max[:, -1] >= math.log(20)) & ~pb.violated) <= 0.05
    n = np.arange(1, x.shape[1] + 1)
    mean = np.cumsum(x, axis=1) / n
    sd = np.sqrt(np.maximum(np.cumsum(x * x, axis=1) / n - mean ** 2, 1e-18))
    peek = (mean / (sd / np.sqrt(n)))[:, 62:]
    assert np.mean(peek.max(axis=1) >= 1.645) > 0.15  # the same paths fool a t-test read every session


def test_null_evidence_is_a_supermartingale_in_mean():
    rng = np.random.default_rng(5)
    x = relative_return(*_market(rng, 4000, 250))
    e_t = np.exp(eprocess_path(x).log_e[:, -1])
    assert e_t.mean() <= 1 + 3 * e_t.std() / math.sqrt(e_t.size)


def test_costs_only_shrink_evidence_pathwise():
    rng = np.random.default_rng(6)
    x = relative_return(*_market(rng, 50, 300, drift=0.1))
    assert np.all(eprocess_path(x, cost_per_side=0.001).log_e <= eprocess_path(x).log_e + 1e-12)


def test_evidence_equals_money_a_large_edge_is_slow_without_an_assumption_and_fast_with_one():
    rng = np.random.default_rng(7)
    x = relative_return(*_market(rng, 200, 2520, drift=0.4, te=0.15))  # +40% a year at 15% tracking error, 10 years
    free = eprocess_path(x)
    bnd = eprocess_path(x, bounded_lambdas(0.2), None, 0.0, 0.2)
    reach_free = np.mean(free.log_e_max[:, -1] >= math.log(20))
    reach_bnd = np.mean((bnd.log_e_max[:, -1] >= math.log(20)) & ~bnd.violated)
    assert reach_free < 0.5 < 0.9 < reach_bnd  # the unlevered book must earn ~20x (times the grid's prior) first
    down = eprocess_path(relative_return(*_market(rng, 200, 2520, drift=-0.05, te=0.15)))
    assert np.mean(down.log_e_max[:, -1] >= math.log(20)) == 0.0


def test_notional_bets_need_a_declared_and_checked_loss_bound():
    with pytest.raises(ValueError, match="declare loss_bound"):
        check_grid((0.5, 2.0))
    with pytest.raises(ValueError, match="lam \\* loss_bound < 1"):
        check_grid((0.5, 2.0), loss_bound=0.6)
    with pytest.raises(ValueError, match="prior"):
        check_grid((0.5, 1.0), prior=(0.2, 0.2))
    ep = EProcess(bounded_lambdas(0.1), loss_bound=0.1)
    for x in (0.01, -0.02, 0.03):
        ep.update(x, "ok")
    assert ep.status == VALID and ep.p_anytime() <= 1
    ep.update(-0.15, "2026-11-02")
    assert ep.status == INVALID and ep.violation["label"] == "2026-11-02" and ep.p_anytime() == 1.0
    assert eprocess_path(np.array([0.01, -0.15]), bounded_lambdas(0.1), None, 0.0, 0.1).violated


def test_streaming_matches_the_path_and_reports_state():
    rng = np.random.default_rng(8)
    x = relative_return(*(a[0] for a in _market(rng, 1, 250, drift=0.06)))
    p = eprocess_path(x, cost_per_side=0.0003)
    ep = EProcess(cost_per_side=0.0003)
    for t, xt in enumerate(x):
        assert math.isclose(ep.bet(), p.bet[t], rel_tol=1e-10)
        ep.update(xt)
    assert math.isclose(ep.log_e, p.log_e[-1], rel_tol=1e-10)
    assert math.isclose(ep.log_e_max, p.log_e_max[-1], rel_tol=1e-10)
    st = ep.state()
    assert st["periods"] == 250 and st["status"] == VALID and math.isclose(st["p_anytime"], min(1, 1 / st["e_max"]))


def test_inputs_are_validated():
    with pytest.raises(ValueError):
        relative_return([0.01, -1.0], [0.0, 0.0])
    with pytest.raises(ValueError):
        relative_return([np.nan], [0.0])
    with pytest.raises(ValueError):
        EProcess().update(-1.0)
    with pytest.raises(ValueError):
        EProcess(cost_per_side=0.6)
    with pytest.raises(ValueError):
        eprocess_path(np.array([]))


def test_e_bh_and_thresholds():
    assert e_bh({"a": 100.0, "b": 50.0, "c": 0.5}, 0.05) == ["a", "b"]  # e_(2) = 50 >= 3 / (0.05 * 2) = 30
    assert e_bh({"a": 59.0, "b": 0.5, "c": 0.5}, 0.05) == []  # e_(1) = 59 < 3 / 0.05 = 60
    assert e_bh({"a": 60.0, "b": 0.5, "c": 0.5}, 0.05) == ["a"]
    assert claim_threshold(0.05) == pytest.approx(20) and claim_threshold(0.05, 0.1) == pytest.approx(200)
    with pytest.raises(ValueError):
        claim_threshold(0.05, 0.0)
