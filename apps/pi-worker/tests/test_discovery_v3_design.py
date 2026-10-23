"""Discovery V3 design study `design-study-1` (docs/evidence/DESIGN_STUDY_1.md): the registered plan, cohort-0 books
reproduced verbatim, the lane's replay on arrays, exact neutrality, the velocity read-outs, the risk-only proposal
rule and the aggregates-only output."""

from __future__ import annotations

import functools
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from apex_worker.discovery.v3 import design as D
from apex_worker.discovery.v3 import tournament_books as TB
from apex_worker.evidence.velocity import best_bet

DESIGN_STUDY_1_SHA256 = "d5fef5eb047d5854eeb65bc6a828fd3faacdb5df0618d2a7478a344257fd7689"  # DESIGN_STUDY_1.md


def test_plan_is_the_registered_one():
    """If this fails the plan changed after registration: register a NEW plan id instead of editing this one."""
    assert D.DESIGN_STUDY_1.sha256() == DESIGN_STUDY_1_SHA256
    doc = (Path(__file__).resolve().parents[3] / "docs" / "evidence" / "DESIGN_STUDY_1.md").read_text()
    assert DESIGN_STUDY_1_SHA256 in doc
    assert list(D.DESIGN_STUDY_1.designs()) == [f"{n}-{v}" for n in ("mom", "rev", "hi52") for v in D.VARIANTS] + [
        "composite-full", "lowvol-full"]


@functools.cache
def _panel():
    from apex_worker.research.realeq.fixture_v2 import factor_frames_v2

    return TB.light_panel(factor_frames_v2(per_sector=4, seed=7, start=pd.Timestamp("2022-01-03").date(),
                                           end=pd.Timestamp("2024-06-28").date()))


def test_raw_variants_are_cohort_0s_books_verbatim():
    pre = _panel()
    inp = D.inputs(pre)
    t = len(pre.sessions) - 1
    lane = TB.decide(pre, t, t, 0, dict.fromkeys(TB.BOOKS))
    mine, _ = D.decide(inp, t, 0, D.DESIGN_STUDY_1, True)
    assert mine["ew"] == lane["ew"]["micro"]
    for n in ("mom", "rev", "hi52"):
        assert mine[f"{n}-raw"] == lane[f"{n}-tilt"]["micro"]
    np.testing.assert_array_equal(D.feature(inp, "high_252", t, D.DESIGN_STUDY_1), TB.feature_at(pre, "high_252", t))


def test_the_array_replay_is_the_lanes_replay():
    from apex_worker.research.forward import tournament_lane as T

    pre = _panel()
    inp = D.inputs(pre, {pre.stocks[0]: [pre.sessions[-5]]})
    t0, t1 = len(pre.sessions) - 60, len(pre.sessions) - 1
    targets, decisions = {}, []
    for t in range(t0, t1):
        tg, _ = D.decide(inp, t, t - t0, D.DESIGN_STUDY_1, t == t0)
        if "ew" in tg:
            targets[t] = tg["ew"]
        books = dict.fromkeys(TB.BOOKS) | ({"ew": {"micro": tg["ew"]}} if "ew" in tg else {})
        decisions.append({"session": pre.sessions[t].date().isoformat(), "body": {"books": books}})
    closes = pd.DataFrame({s: pre.bars[s]["close"].reindex(pre.sessions) for s in [*pre.stocks, "SPY"]})
    lane = T.replay(decisions, closes.iloc[t0: t1 + 1], {pre.stocks[0]: [pre.sessions[-5]]})
    mine, turnover, cost = D.replay(inp, targets, t0, t1)
    got = [mine[i] for i in range(t0 + 1, t1 + 1)]
    want = [lane[pre.sessions[i].date().isoformat()]["books"]["ew"] for i in range(t0 + 1, t1 + 1)]
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-12)
    assert cost[t0 + 1] > 0 and turnover[t0 + 1] == pytest.approx(1.0, abs=1e-6)  # out of SPY into the targets


def test_neutral_variants_are_exactly_sector_beta_and_size_neutral():
    rng = np.random.default_rng(3)
    n = 60
    inp = SimpleNamespace(stocks=[f"S{i:02d}" for i in range(n)], sector=np.array(["a", "b", "c"] * 20, dtype=object))
    idx, score = np.arange(n), rng.normal(size=n)
    expo = np.column_stack([rng.normal(1, 0.3, n), rng.normal(20, 1, n)])
    z = D.neutral_score(score, idx, inp, "sector")
    for g in "abc":
        assert abs(z[inp.sector == g].sum()) < 1e-12 and z[inp.sector == g].max() == 1.0
    e = D._residual(z, expo, inp.sector)
    assert abs(e @ expo[:, 0]) < 1e-9 and abs(e @ expo[:, 1]) < 1e-9
    assert all(abs(e[inp.sector == g].sum()) < 1e-12 for g in "abc")
    a = D.neutral_score(score, idx, inp, "full", expo)
    assert np.all(np.abs(a) <= 1.0) and np.std(a) == pytest.approx(D.UNIFORM_SD, rel=0.05)
    w = D._weights(a, idx, inp.stocks, 0.5)
    assert sum(w.values()) == TB.MICRO and min(w.values()) > 0  # long-only: every name between 0.5x and 1.5x


def test_alpha_needed_and_the_bet_cap():
    te, g = 0.01, math.log(800) / 10
    free = D._alpha_needed(te, g, None)
    assert 0.5 * (free / te) ** 2 == pytest.approx(g)  # the information limit IR^2 / 2
    capped = D._alpha_needed(te, g, 20.0)  # mu / te^2 would exceed the cap: the bounded test needs more alpha
    assert capped > free and best_bet(capped, te, 20.0)[1] == pytest.approx(g)
    assert D._alpha_needed(te, g, 1e6) == pytest.approx(free)
    rows = D.velocity_rows(te, 0.002, 0.03, D.DESIGN_STUDY_1)
    assert rows["gross_alpha_needed"]["bounded"] > rows["gross_alpha_needed"]["information_limit"] > free
    assert rows["years_at_gross_alpha"]["0.01"]["net_alpha"] == pytest.approx(0.008)


def _design(te_unit: float, worst_twin: float, worst_spy: float = -0.01, every: int = 20) -> dict:
    return {"every": every, "vs_twin": {"tracking_error_per_unit_dispersion": te_unit, "worst_session": worst_twin},
            "vs_spy": {"worst_session": worst_spy}}


def test_the_proposal_reads_only_risk():
    rows = {f"{n}-{v}": _design(0.01, -0.002) for n in ("mom", "rev", "hi52") for v in D.VARIANTS}
    rows |= {"composite-full": _design(0.01, -0.0035, every=5), "lowvol-full": _design(0.01, -0.03, -0.5)}
    rows["mom-sector"] = _design(0.005, -0.002)  # mom: sector has the lowest tracking error
    rows["rev-raw"] = _design(0.008, -0.002)
    rows["rev-full"] = _design(0.008, -0.002)  # rev: a tie between raw and full goes to the more neutral
    p = D.proposal(rows, D.DESIGN_STUDY_1)
    chosen = [c["design"] for c in p["candidates"]]
    assert chosen == ["mom-sector", "rev-full", "hi52-full", "composite-full", "lowvol-full"]
    by = {c["design"]: c for c in p["candidates"]}
    assert by["mom-sector"]["twin_loss_bound"] == 0.005 and by["mom-sector"]["loss_bound"] == 0.02
    assert by["composite-full"]["twin_loss_bound"] == 0.0075  # 2 x 0.35% rounded up on the grid
    assert by["lowvol-full"]["twin_loss_bound"] is None  # 2 x 3% is beyond the twin grid: no bounded twin test
    assert by["lowvol-full"]["loss_bound"] is None
    assert p["prior_mass_total"] == pytest.approx(0.2) and "changes no recorded series" in p["status"]


def test_the_study_writes_aggregates_only(tmp_path):
    pre = _panel()
    n = len(pre.sessions)
    data = SimpleNamespace(pre=pre, b=SimpleNamespace(dev_range=(n - 300, n - 1)), quarantine={},
                           provenance=SimpleNamespace(data_class="SYNTHETIC_FIXTURE_DEMO"))
    s = D.run_design_study(D.DESIGN_STUDY_1, data, tmp_path, experiment_id="demo-design", progress=lambda _m: None)
    assert s["status"] == "COMPLETED" and s["sessions"] == 299  # t0 + 2 .. d1: every session holds the targets
    raw = (tmp_path / "design_study.json").read_text()
    body = json.loads(raw)
    assert body["plan_sha256"] == DESIGN_STUDY_1_SHA256 and body["window"]["label"] == "DEV_CONTAMINATED"
    assert not any(f'"{sym}"' in raw for sym in pre.stocks)  # no symbol anywhere
    full = body["designs"]["mom-full"]["mean_abs_active"]
    raw_ = body["designs"]["mom-raw"]["mean_abs_active"]
    assert full["beta"] < raw_["beta"] / 5 and full["sector_max"] < raw_["sector_max"] / 5
    assert set(body["cohort0_declared_bounds_on_dev"]) == {"mom-raw", "rev-raw", "hi52-raw"}
    assert len(body["proposal"]["candidates"]) == 5
    assert "DEV_CONTAMINATED" in (tmp_path / "DESIGN_STUDY.md").read_text()


def test_the_publication_guard_refuses_a_symbol():
    with pytest.raises(ValueError):
        D._no_symbols({"rows": [{"x": "AAPL"}]}, {"AAPL"})
    with pytest.raises(ValueError):
        D._no_symbols({"AAPL": 1}, {"AAPL"})
    D._no_symbols({"rows": [{"x": "aapl-free text AAPL"}]}, {"AAPL"})  # only exact keys and values


def test_cli_guards_and_plan_listing(tmp_path, capsys):
    from apex_worker.cli import main

    assert main(["research", "discovery", "design", "--plan", "nope", "--source", "fixture", "--experiment-id",
                 "demo-x", "--out", str(tmp_path)]) == 5
    assert main(["research", "discovery", "design", "--plan", "design-study-1", "--source", "fixture",
                 "--experiment-id", "real-looking", "--out", str(tmp_path)]) == 5
    capsys.readouterr()
    assert main(["research", "discovery", "plans"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["design-study-1"] == {"title": D.DESIGN_STUDY_1.title, "sha256": DESIGN_STUDY_1_SHA256,
                                        "kind": "measurement"}
