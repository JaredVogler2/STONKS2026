"""Evidence engine: evidence velocity and the information limit (docs/evidence/EVIDENCE_TOURNAMENT.md section 4)."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from apex_worker.evidence.velocity import (
    best_bet,
    crossing_probability,
    fixed_horizon_years,
    growth,
    information_rate,
    velocity,
    years_to,
)


def test_rates_are_ordered_assumption_free_le_bounded_le_information_limit():
    for mu, s in ((0.03, 0.03), (0.12, 0.15), (0.122, 0.268), (0.01, 0.2)):
        free, bnd, info = best_bet(mu, s, 1.0)[1], best_bet(mu, s, 9.0)[1], information_rate(mu, s)
        assert 0 < free <= bnd <= info + 1e-12
    assert best_bet(-0.01, 0.1) == (0.0, 0.0) and years_to(20, 0.0) == math.inf
    lam, g = best_bet(0.01, 0.2)  # unconstrained optimum below 1: the information rate is reached
    assert lam == pytest.approx(0.25) and g == pytest.approx(information_rate(0.01, 0.2))
    assert growth(0.1, 0.2, 1.0) == pytest.approx(0.08)


def test_crossing_probability_matches_monte_carlo():
    rng = np.random.default_rng(21)
    g, vol, level, years, dt = 0.2, 0.3, 20.0, 15.0, 1 / 252
    steps = int(years / dt)
    paths = np.cumsum(g * dt + vol * math.sqrt(dt) * rng.standard_normal((4000, steps)), axis=1)
    mc = np.mean(paths.max(axis=1) >= math.log(level))
    assert abs(crossing_probability(g, vol, level, years) - mc) < 0.03
    assert crossing_probability(0.1, 0.2, 20, 0.0) == 0.0 and crossing_probability(1.0, 0.0, 20, 10.0) == 1.0


def test_fixed_horizon_formula():
    assert fixed_horizon_years(1.0, 0.05, 0.5) == pytest.approx(1.6448536 ** 2, rel=1e-6)
    assert fixed_horizon_years(0.5, 0.05, 0.8) == pytest.approx(((1.6448536 + 0.8416212) / 0.5) ** 2, rel=1e-6)
    assert fixed_horizon_years(0.0) == math.inf


def test_g2_at_its_dev_estimate_needs_decades():
    """Pilot 2's strongest candidate (results.json: +0.0862 log growth a year against SPY; tracking error 0.268 from its
    volatility 0.353, SPY's 0.195 and beta 1.2), taken at face value."""
    row = velocity("g2", 0.0862, 0.268, loss_bound=0.2)
    assert row["assumption_free"]["years"] == pytest.approx(34.75, abs=0.05)
    assert row["information_limit"]["years"] == pytest.approx(28.9, abs=0.1)
    assert row["assumption_free"]["p_within"]["10"] < 0.01 and row["assumption_free"]["p_within"]["20"] < 0.25
    assert row["fixed_horizon_z"]["years_power_80"] > 25
    json.dumps(row)


def test_cli_velocity_and_replay(tmp_path, capsys):
    from apex_worker.cli import main

    assert main(["research", "evidence", "velocity", "--log-growth", "0.0862", "--te", "0.268"]) == 0
    assert json.loads(capsys.readouterr().out)["assumption_free"]["bet"] == 1.0
    rng = np.random.default_rng(22)
    spy = 0.0003 + 0.01 * rng.standard_normal(600)
    cand = spy + 0.002 + 0.004 * rng.standard_normal(600)
    csv = tmp_path / "r.csv"
    csv.write_text("cand,spy\n" + "\n".join(f"{a:.8f},{b:.8f}" for a, b in zip(cand, spy, strict=True)))
    assert main(["research", "evidence", "replay", "--csv", str(csv), "--candidate", "cand", "--numeraire", "spy",
                 "--cost-bps", "2", "--loss-bound", "0.1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "VALID" and out["periods"] == 600 and out["e_max"] >= 20 and out["mode"] == "RESEARCH_ONLY"
    assert set(out) >= {"p_anytime", "first_period_at_threshold", "assumption"}
    assert main(["research", "evidence", "replay", "--csv", str(csv), "--candidate", "nope", "--numeraire", "spy"]) == 2
