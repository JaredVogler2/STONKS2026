"""Discovery V3 PR-B: contracts, trial registry, portfolio-first book, objective/Pareto, agents and the DEV pipeline.

Ids follow amendments/discovery-v3-2026-10-22/10_EXECUTABLE_ACCEPTANCE_GATES.md (ECO*, SCI*). Synthetic fixtures only.
"""

from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from apex_worker.discovery.v3 import objective as O
from apex_worker.discovery.v3 import portfolio as PF
from apex_worker.discovery.v3.contracts import (
    AllocationSpec,
    ContractError,
    DataProvenance,
    EvaluationWindow,
    HypothesisSpec,
    InputRequirement,
    PolicySpec,
    tier_for,
)
from apex_worker.discovery.v3.registry import RegistryError, SearchIncomplete, TrialRegistry
from apex_worker.research.book import run_book

BARS = InputRequirement("daily_sip_bars", True, True)
TXT = "a substantive economic statement that is clearly longer than forty characters"


def hyp(hid="h-one", n_ablations=0, inputs=(BARS,), family="f1-test", **kw) -> HypothesisSpec:
    prim = PolicySpec(f"{hid}-p", "momentum_top", "E0X1")
    abl = tuple(PolicySpec(f"{hid}-a{i}", "momentum_top", "E0X1", AllocationSpec(slots=5 + i), varies_axis="allocation",
                           parent_policy=prim.policy_id) for i in range(n_ablations))
    base = dict(hypothesis_id=hid, family_id=family, title="t", economic_mechanism=TXT, competing_explanation=TXT,
                null_hypothesis=TXT, falsification_test=TXT, negative_controls=("placebo",), baseline_ids=("spy_hold",),
                required_inputs=inputs, primary_policy=prim, ablations=abl)
    return HypothesisSpec(**(base | kw))


def prov(start="2016-01-04", cls="REAL_MARKET_DATA", dataset="d" * 64) -> DataProvenance:
    return DataProvenance(cls, "alpaca", "sip", "split", "2026-10-22T00:00:00Z", start, "2026-10-20", dataset)


# ---- contracts ------------------------------------------------------------------------------------------------------
def test_hypothesis_needs_substance_controls_baselines_and_a_bounded_budget():
    assert hyp().status_at_registration == "REGISTERED"
    with pytest.raises(ContractError):
        hyp(economic_mechanism="momentum works")
    with pytest.raises(ContractError):
        hyp(economic_mechanism="RSI 19 with MA 61 and MACD 12, Bollinger 20 and EMA 50 and SMA 200 together")
    with pytest.raises(ContractError):
        hyp(negative_controls=())
    with pytest.raises(ContractError):
        hyp(n_ablations=3)  # 1 primary + at most 2 ablations
    with pytest.raises(ContractError):
        hyp(release_mode="GROUP_ACTIONABLE")
    with pytest.raises(ContractError):
        PolicySpec("x", "none", "E0X1", varies_axis="exit")  # a variant names its parent
    with pytest.raises(ContractError):
        AllocationSpec(leverage=0.5)


def test_sci15_unavailable_or_noncausal_inputs_are_source_blocked():
    h = hyp(inputs=(BARS, InputRequirement("pit_earnings_first_public", False, False, "not licensed")))
    assert h.status_at_registration == "SOURCE_BLOCKED" and h.blocked_inputs == ["pit_earnings_first_public"]


def test_sci02_sci03_contaminated_history_caps_the_evidence_tier():
    p = prov()
    assert p.contaminated() and p.max_tier() == "DEV_DIAGNOSTIC"
    assert tier_for(p, "INDEPENDENT_CONFIRMATORY") == "DEV_DIAGNOSTIC"  # a label cannot upgrade inspected data
    assert tier_for(prov(cls="SYNTHETIC_FIXTURE_DEMO"), "DEV_DIAGNOSTIC") == "MECHANISM_PROPOSAL"
    assert prov(start="2026-11-02").max_tier() == "PROSPECTIVE_EXPLORATORY"
    with pytest.raises(ContractError):
        EvaluationWindow("2025-05-02", "2026-09-21", role="UNTOUCHED_HOLDOUT")  # the old holdout was viewed


# ---- registry -------------------------------------------------------------------------------------------------------
def test_sci01_a_search_that_hides_trials_is_invalid():
    reg = TrialRegistry()
    ids = []
    for i in range(34):  # 34 hypotheses x 3 variants = 102 registered trials
        ids += reg.register(hyp(f"h-{i}", n_ablations=2), prov(), "o" * 64)
    assert reg.total_trials == 102 and reg.family_counts() == {"f1-test": 102}
    for tid in ids[:3]:  # report only the 'best 3'
        reg.look(tid, "dev")
        reg.record(tid, {"disposition": "PROMISING_EXPLORATORY"}, prov())
    with pytest.raises(SearchIncomplete):
        reg.validate_complete()
    for tid in ids[3:]:
        reg.abandon(tid, "not evaluated")
    reg.validate_complete()
    assert len(reg.index()) == 102  # the index lists every trial, losers and abandoned ones included


def test_repeat_experiment_returns_the_same_trial_and_cannot_be_counted_twice():
    reg = TrialRegistry()
    a = reg.register(hyp(), prov(), "o" * 64)
    b = reg.register(hyp(), prov(), "o" * 64)
    assert a == b and reg.total_trials == 1
    reg.record(a[0], {"disposition": "REJECTED_DEV"}, prov())
    with pytest.raises(RegistryError):
        reg.record(a[0], {"disposition": "PROMISING_EXPLORATORY"}, prov())
    revised = reg.register(hyp(), prov(dataset="e" * 64), "o" * 64)  # a source revision is visibly a new trial
    assert revised != a
    with pytest.raises(RegistryError):
        reg.register(hyp(title="changed spec, same id"), prov(), "o" * 64)


def test_eco12_sci08_objective_change_is_a_new_trial_and_peeks_are_counted():
    reg = TrialRegistry()
    (t1,) = reg.register(hyp(), prov(), O.PRINCIPAL_OBJECTIVE.sha256())
    changed = replace(O.PRINCIPAL_OBJECTIVE, scenarios={"balanced": {"l_es": 0.9, "l_dd": 0.9, "l_to": 0.0}})
    assert changed.sha256() != O.PRINCIPAL_OBJECTIVE.sha256()
    (t2,) = reg.register(hyp(), prov(), changed.sha256())
    assert t1 != t2
    reg.look(t1, "first look")
    reg.look(t1, "weekly re-look")
    res = reg.record(t1, {"disposition": "INCONCLUSIVE_DEV"}, prov())
    assert res["body"]["looks"] == 2  # sequential peeks are on the record
    with pytest.raises(RegistryError):
        reg.look("t-unregistered", "x")


def test_sci10_registry_chain_detects_edits_and_deletions(tmp_path):
    path = tmp_path / "registry.jsonl"
    reg = TrialRegistry(path)
    reg.register(hyp("h-a"), prov(), "o" * 64)
    reg.register(hyp("h-b"), prov(), "o" * 64)
    assert TrialRegistry.load(path).verify() == []
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:1] + lines[2:]) + "\n")  # drop a registration
    with pytest.raises(RegistryError):
        TrialRegistry.load(path)


# ---- portfolio book ---------------------------------------------------------------------------------------------------
def _market(n=120, syms=8, seed=3):
    rng = np.random.default_rng(seed)
    closes = {f"S{i}": 50 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, n))) for i in range(syms)}
    spy = 300 * np.exp(np.cumsum(rng.normal(0.0003, 0.009, n)))
    spy_open = np.concatenate([[spy[0]], spy[:-1]]) * (1 + rng.normal(0, 0.001, n))
    return closes, spy_open, spy


def _random_trades(closes, n_trades=60, seed=5, lo=5, hi=100):
    rng = np.random.default_rng(seed)
    syms = sorted(closes)
    rows = []
    for _ in range(n_trades):
        s = syms[int(rng.integers(len(syms)))]
        e = int(rng.integers(lo, hi))
        x = int(min(hi + 10, e + rng.integers(0, 15)))
        rows.append({"symbol": s, "sector": f"sec{syms.index(s) % 3}", "entry_index": e, "exit_index": x,
                     "entry_price": float(closes[s][e] * 1.001), "exit_price": float(closes[s][x]),
                     "priority": float(rng.normal())})
    return pd.DataFrame(rows)


def test_cash_idle_book_reproduces_research_book_exactly():
    closes, so, sc = _market()
    tr = _random_trades(closes)
    ref = run_book(tr, closes, len(sc), 25.0, 10, 0.30, sc, 5, 110)
    mine = PF.run_portfolio(tr, closes, so, sc, 5, 110, AllocationSpec(), 25.0)
    assert np.allclose(mine.equity, ref.equity, atol=1e-12) and mine.taken == ref.taken
    assert mine.skipped == ref.skipped
    bench = PF.benchmark_returns(mine, sc)["exposure_matched"]
    assert np.allclose(np.cumprod(1 + bench), ref.bench_equity, atol=1e-12)  # the same exposure-matched comparator


def test_eco01_one_hundred_candidates_one_day_respect_slots_cash_and_sector_cap():
    closes, so, sc = _market(syms=100)
    rows = [{"symbol": s, "sector": "tech" if i >= 40 else f"s{i % 5}", "entry_index": 10, "exit_index": 30,
             "entry_price": float(closes[s][10]), "exit_price": float(closes[s][30]), "priority": float(i)}
            for i, s in enumerate(sorted(closes))]
    port = PF.run_portfolio(pd.DataFrame(rows), closes, so, sc, 10, 40, AllocationSpec(slots=10, sector_cap=0.3))
    assert port.taken == 10 and port.max_positions <= 10
    assert port.skipped["sector_cap"] > 0  # at most 3 tech names at a 30% sector cap
    assert port.stock_exposure.max() <= 1.0 + 1e-9


def test_eco05_overnight_gap_through_the_stop_loses_more_than_1r():
    closes = {"A": np.array([100.0] * 10 + [90.0] * 10)}
    so = sc = np.full(20, 300.0)
    # stop was 97 (1R = 3%): the gap opened at 90, the realised loss is ~ -10% of the position
    tr = pd.DataFrame([{"symbol": "A", "sector": "x", "entry_index": 5, "exit_index": 10, "entry_price": 100.0,
                        "exit_price": 90.0}])
    port = PF.run_portfolio(tr, closes, so, sc, 5, 15, AllocationSpec(slots=1, sector_cap=1.0), 25.0)
    assert port.taken == 1 and port.stock_pnl < -0.03 * 2  # far worse than the planned 1R loss on a full-slot position
    assert port.equity[-1] == pytest.approx(0.90 * (1 - 0.0025) / (1 + 0.0025), rel=1e-9)  # the gap, then both costs


def test_eco09_exit_proceeds_cannot_fund_the_same_sessions_entries():
    closes = {"A": np.full(20, 100.0), "B": np.full(20, 100.0)}
    so = sc = np.full(20, 300.0)
    tr = pd.DataFrame([{"symbol": "A", "sector": None, "entry_index": 2, "exit_index": 8, "entry_price": 100.0,
                        "exit_price": 100.0},
                       {"symbol": "B", "sector": None, "entry_index": 8, "exit_index": 12, "entry_price": 100.0,
                        "exit_price": 100.0}])
    port = PF.run_portfolio(tr, closes, so, sc, 2, 14, AllocationSpec(slots=1), 0.0)
    assert port.taken == 1 and port.skipped["slots_full"] == 1  # A's slot frees only after B's entry was considered


def test_eco15_no_candidates_is_cash_and_spy_idle_tracks_spy_less_costs():
    closes, so, sc = _market()
    empty = pd.DataFrame(columns=["symbol", "sector", "entry_index", "exit_index", "entry_price", "exit_price"])
    cash = PF.run_portfolio(empty, closes, so, sc, 5, 100)
    assert np.allclose(cash.equity, 1.0) and PF.describe_portfolio(cash)["cash_days_share"] == 1.0
    idle = PF.run_portfolio(empty, closes, so, sc, 5, 100, AllocationSpec(idle_capital="SPY"), idle_cost_bps=5.0)
    spy_growth = sc[100] / sc[4]
    assert idle.equity[-1] == pytest.approx(spy_growth / (1 + 5e-4), rel=1e-9)  # one sweep cost, then buy-and-hold
    assert idle.idle_exposure.min() > 0.999


def test_spy_idle_book_keeps_invariants_and_pays_sweep_costs():
    closes, so, sc = _market(n=160)
    tr = _random_trades(closes, n_trades=80, hi=140)
    a = PF.run_portfolio(tr, closes, so, sc, 5, 150, AllocationSpec(idle_capital="SPY"), 25.0, 5.0)
    b = PF.run_portfolio(tr, closes, so, sc, 5, 150, AllocationSpec(idle_capital="SPY"), 25.0, 0.0)
    assert (a.stock_exposure + a.idle_exposure <= 1 + 1e-9).all() and a.costs_paid > b.costs_paid
    assert (a.stock_exposure + a.idle_exposure).mean() > 0.95  # idle capital is invested, never left in cash


def test_eco03_eco07_benchmarks_and_metrics_are_reported_separately():
    closes, so, sc = _market()
    port = PF.run_portfolio(_random_trades(closes), closes, so, sc, 5, 110)
    bench = PF.benchmark_returns(port, sc)
    assert set(bench) == {"cash", "spy_hold", "exposure_matched"}
    m = PF.describe_portfolio(port)
    for k in ("log_growth_annual", "max_drawdown", "expected_shortfall_5", "avg_stock_exposure", "turnover_per_year",
              "net_return_per_capital_day", "cash_days_share"):
        assert k in m
    assert PF.metrics(bench["exposure_matched"])["log_growth_annual"] < PF.metrics(bench["spy_hold"])["log_growth_annual"]


# ---- objective --------------------------------------------------------------------------------------------------------
def test_constraints_reject_inactive_or_overdrawn_policies():
    ok = {"max_drawdown": -0.1, "turnover_per_year": 5, "avg_stock_exposure": 0.4, "max_positions": 10}
    assert O.constraint_violations(ok) == []
    assert O.constraint_violations(ok | {"avg_stock_exposure": 0.01})  # 'always cash' is not skill
    assert O.constraint_violations(ok | {"max_drawdown": -0.45})
    assert O.constraint_violations(ok | {"turnover_per_year": 80})


def test_eco13_pareto_front_keeps_only_non_dominated_policies():
    pts = {"a": {"log_growth_annual": 0.10, "max_drawdown": -0.20, "expected_shortfall_5": -0.02, "turnover_per_year": 5},
           "b": {"log_growth_annual": 0.08, "max_drawdown": -0.10, "expected_shortfall_5": -0.015, "turnover_per_year": 5},
           "c": {"log_growth_annual": 0.07, "max_drawdown": -0.25, "expected_shortfall_5": -0.03, "turnover_per_year": 30}}
    assert O.pareto_front(pts) == ["a", "b"]  # the complex high-turnover 'c' is dominated and drops out


# ---- pipeline (synthetic fixture) ---------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def smoke(tmp_path_factory):
    from apex_worker.discovery.v3.pipeline import load_data, run_experiment
    from apex_worker.discovery.v3.plans import ENGINE_SMOKE

    data = load_data("fixture", per_sector=4)
    out = tmp_path_factory.mktemp("v3")
    summary = run_experiment(ENGINE_SMOKE, data, out, experiment_id="demo-v3-test", progress=lambda _m: None, n_boot=199)
    return data, out, summary


def test_pipeline_freezes_registers_self_checks_and_closes_every_trial(smoke):
    _data, out, summary = smoke
    man = json.loads((out / "manifest.json").read_text())
    assert man["status_at_freeze"] == "FROZEN_BEFORE_METRICS" and len(man["trials"]) == 3
    reg = TrialRegistry.load(out / "registry.jsonl")
    reg.validate_complete()
    kinds = [e["kind"] for e in reg.events]
    assert kinds.index("LOOK") > max(i for i, k in enumerate(kinds) if k == "REGISTER_TRIAL")  # registered first
    assert summary["self_checks"]["lookahead_trap"]["detected"] and not summary["self_checks"]["placebo"]["warning"]
    assert summary["aggregate"] == "NO_TRADE"
    assert sorted(summary["dispositions"].values())[-1] in ("SOURCE_BLOCKED",)
    res = json.loads((out / "results.json").read_text())["trials"]
    evaluated = [r for r in res.values() if "metrics" in r]
    assert {r["comparator"] for r in evaluated} == {"spy_hold", "exposure_matched"}
    assert all(r["holm_m"] == 2 for r in evaluated) and all(r["signals"]["control_books"] > 0 for r in evaluated)
    assert "prices" not in (out / "results.json").read_text() and "entry_price" not in (out / "results.json").read_text()


def test_fixture_only_plan_is_refused_on_market_data(smoke, tmp_path):
    from apex_worker.discovery.v3.pipeline import Loaded, run_experiment
    from apex_worker.discovery.v3.plans import ENGINE_SMOKE

    data = smoke[0]
    real = Loaded(data.pre, data.b, data.quarantine, replace(data.provenance, data_class="REAL_MARKET_DATA"))
    with pytest.raises(ValueError):
        run_experiment(ENGINE_SMOKE, real, tmp_path, experiment_id="x")


def test_failed_self_check_invalidates_the_batch(smoke, tmp_path, monkeypatch):
    from apex_worker.discovery.v3 import pipeline as P
    from apex_worker.discovery.v3.plans import ENGINE_SMOKE

    monkeypatch.setattr(P, "self_checks", lambda data: {"lookahead_trap": {"detected": False},
                                                         "placebo": {"warning": False}, "engine_valid": False})
    s = P.run_experiment(ENGINE_SMOKE, smoke[0], tmp_path, experiment_id="demo-x", progress=lambda _m: None, n_boot=99)
    assert {v for v in s["dispositions"].values()} == {"SIMULATOR_INVALID", "SOURCE_BLOCKED"}


def test_weights_agents_spy_and_momentum(smoke):
    from apex_worker.discovery.v3.pipeline import evaluate_policy

    data = smoke[0]
    spy = evaluate_policy(PolicySpec("ref-spy", "spy", "WEIGHTS", cost_bps_per_side=5.0), data, n_boot=99)
    assert abs(spy["active_vs_spy_hold"]["active_log_growth_annual"]) < 0.005  # SPY vs SPY: only cost timing differs
    mom = evaluate_policy(PolicySpec("ref-mom", "momentum_weights", "WEIGHTS", params={"n": 10, "every": 20}), data,
                          n_boot=99)
    assert mom["metrics"]["sessions"] > 1000 and mom["comparator"] == "spy_hold"


def test_cli_guards(tmp_path):
    from apex_worker.cli import main

    assert main(["research", "discovery", "run", "--plan", "engine-smoke", "--source", "alpaca",
                 "--experiment-id", "x", "--out", str(tmp_path)]) == 5  # fixture-only plan
    assert main(["research", "discovery", "run", "--plan", "nope", "--source", "fixture",
                 "--experiment-id", "demo-x", "--out", str(tmp_path)]) == 5
    assert main(["research", "discovery", "run", "--plan", "engine-smoke", "--source", "fixture",
                 "--experiment-id", "not-demo", "--out", str(tmp_path)]) == 5



