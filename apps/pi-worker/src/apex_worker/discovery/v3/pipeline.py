"""Bounded DEV experiment pipeline for Discovery V3 (03 sections 3-7; 07 sections 1-6).

  plan (registered hypotheses, committed BEFORE the run) -> data (protocol-2 SIP path, or the DEMO fixture) -> manifest
  frozen and every trial registered BEFORE any metric -> engine self-checks (look-ahead trap must be detected, placebo
  must not be) -> each policy: selector -> execution arm (study_v2.run_arm: the protocol's execution, quarantine,
  de-duplication and matched controls) -> portfolio book (cash- or SPY-idle) -> metrics vs CASH, SPY and exposure-matched
  SPY/cash, matched-control books, attribution, objective -> Holm over EVERY registered trial -> pre-registered
  dispositions -> aggregate files only (no prices).

Every window before 2026-10-23 is DEV_CONTAMINATED: the best possible disposition is PROMISING_EXPLORATORY, which only
nominates a policy for a separately registered prospective shadow. Nothing here can validate, promote or trade.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from apex_worker.discovery.v3 import agents as A
from apex_worker.discovery.v3 import objective as O
from apex_worker.discovery.v3 import portfolio as PF
from apex_worker.discovery.v3.contracts import DataProvenance, EvaluationWindow, HypothesisSpec, PolicySpec, sha256_json
from apex_worker.discovery.v3.registry import TrialRegistry
from apex_worker.research import inference as INF
from apex_worker.research.realeq import study_v2 as S
from apex_worker.research.realeq.protocol_v2 import BoundariesV2
from apex_worker.research.training_data import BENCHMARK
from apex_worker.signals.strategies.quant_v2 import allocation_returns

K = S.K
LABEL = S.P["chronology"]["label_sessions"]


@dataclass(frozen=True)
class ExperimentPlan:
    plan_id: str
    title: str
    hypotheses: tuple[HypothesisSpec, ...]
    fixture_only: bool = False  # engine smoke plans can never run on real data
    notes: str = ""

    def sha256(self) -> str:
        return sha256_json({"plan_id": self.plan_id, "hypotheses": [h.sha256() for h in self.hypotheses],
                            "fixture_only": self.fixture_only})


@dataclass
class Loaded:
    pre: S.Prepared
    b: BoundariesV2  # evaluation boundaries: dev_range = the DEV decision window
    quarantine: dict
    provenance: DataProvenance
    data_quality: dict = field(default_factory=dict)


def eval_bounds(b: BoundariesV2) -> BoundariesV2:
    """Only the DEV decision window is evaluated (PRESAMPLE is never touched by V3)."""
    return BoundariesV2(b.data_start, b.data_end, "", "", b.dev_start, b.dev_end, (-10, -5), b.dev_range, b.sessions)


def _arrays(pre: S.Prepared):
    spy = pre.bars[BENCHMARK].reindex(pre.sessions)
    return spy["open"].astype(float).to_numpy(), spy["close"].astype(float).to_numpy()


def _trades(rows: pd.DataFrame) -> pd.DataFrame:
    if rows is None or not len(rows):
        return pd.DataFrame(columns=["symbol", "sector", "entry_index", "exit_index", "entry_price", "exit_price",
                                     "priority"])
    f = rows[rows["filled"].astype(bool)]
    return f[["symbol", "sector", "entry_index", "exit_index", "entry_price", "exit_price", "priority"]].copy()


def _control_trades(rows: pd.DataFrame, pre: S.Prepared, j: int) -> pd.DataFrame:
    recs = []
    if rows is None or not len(rows) or "ctrl_trades" not in rows:
        return _trades(None)
    for ts, syms, pri in zip(rows["ctrl_trades"], rows["ctrl_symbols"], rows["priority"], strict=True):
        if ts and j < len(ts) and ts[j].filled:
            recs.append({"symbol": syms[j], "sector": pre.sector.get(syms[j]), "entry_index": ts[j].entry_index,
                         "exit_index": ts[j].pess.exit_index, "entry_price": ts[j].entry_price,
                         "exit_price": ts[j].pess.exit_price, "priority": pri})
    return pd.DataFrame(recs) if recs else _trades(None)


def _weights_result(w: pd.DataFrame, pre: S.Prepared, lo: int, hi: int, cost: float) -> PF.PortfolioResult:
    cols = list(w.columns)
    opens = {s: pre.bars[s]["open"].astype(float) for s in cols}
    closes = {s: pre.bars[s]["close"].astype(float) for s in cols}
    r, x, _xo = allocation_returns(w.reindex(pre.sessions), opens, closes, cost, with_open_exposure=True)
    rr, xx = r.iloc[lo : hi + 1].to_numpy(float), x.iloc[lo : hi + 1].to_numpy(float)
    eq = np.cumprod(1 + rr)
    return PF.PortfolioResult(rr, eq, xx, np.zeros(len(rr)), int((~w.isna().all(axis=1)).sum()), {}, 0.0, 0.0, 0.0,
                              float(np.sum(xx * eq)), 0, lo, hi, ["WEIGHTS agent: turnover/costs inside returns"])


def evaluate_policy(pol: PolicySpec, data: Loaded, n_boot: int = 999) -> dict:
    pre, b, q = data.pre, data.b, data.quarantine
    lo, hi = b.dev_range
    end = min(len(pre.sessions) - 1, hi + LABEL + 3)  # every exit of a decision in the window is inside the book
    spy_open, spy_close = _arrays(pre)
    closes = {s: pre.market.c[s] for s in pre.market.symbols}
    out: dict = {"policy_id": pol.policy_id, "policy_sha256": pol.sha256(), "selector": pol.selector, "arm": pol.arm,
                 "allocation": pol.allocation.__dict__, "cost_bps_per_side": pol.cost_bps_per_side}
    ctrl_r = None
    if pol.arm == "WEIGHTS":
        w = A.WEIGHT_AGENTS[pol.selector](pre, b, pol.params)
        port = _weights_result(w, pre, lo, end, pol.cost_bps_per_side)
        zero = _weights_result(w, pre, lo, end, 0.0)
        out["signals"] = {"rebalance_rows": port.taken}
    else:
        sigs = A.SELECTORS[pol.selector](pre, b, pol.params)
        run = S.run_arm(pol.params.get("seed_name", pol.policy_id), pol.arm, sigs, pre, b, q,
                        stop_k=pol.params.get("stop_atr"))
        rows = run.rows
        trades = _trades(rows)
        port = PF.run_portfolio(trades, closes, spy_open, spy_close, lo, end, pol.allocation, pol.cost_bps_per_side)
        zero = PF.run_portfolio(trades, closes, spy_open, spy_close, lo, end, pol.allocation, 0.0, 0.0)
        books = [PF.run_portfolio(_control_trades(rows, pre, j), closes, spy_open, spy_close, lo, end, pol.allocation,
                                  pol.cost_bps_per_side) for j in range(K)]
        books = [x for x in books if x.taken > 0]
        if books:
            ctrl_r = np.mean([x.returns for x in books], axis=0)
        kept = rows[rows["value"].notna()] if len(rows) else rows
        out["signals"] = {"funnel": run.funnel["DEV"], "kept": len(kept),
                          "mean_per_signal_net": PF._r(kept["value"].mean()) if len(kept) else None,
                          "mean_control_per_signal_net": PF._r(kept["ctrl_value"].mean()) if len(kept) and
                          "ctrl_value" in kept else None, "control_books": len(books)}
    bench = PF.benchmark_returns(port, spy_close)
    comparator = "spy_hold" if (pol.allocation.idle_capital == "SPY" or pol.arm == "WEIGHTS") else "exposure_matched"
    m = PF.describe_portfolio(port)
    active = PF.active_test(port.returns, bench[comparator], n_boot)
    out |= {"comparator": comparator, "metrics": m,
            "benchmarks": {k: PF.metrics(v) for k, v in bench.items()},
            "active_vs_comparator": active,
            "active_vs_spy_hold": PF.active_test(port.returns, bench["spy_hold"], n_boot),
            "active_vs_control_books": PF.active_test(port.returns, ctrl_r, n_boot) if ctrl_r is not None else None,
            "zero_cost_metrics": PF.metrics(zero.returns),
            "decomposition": PF.decompose(port.returns, spy=PF.spy_returns(spy_close, lo, end),
                                          exposure_matched=bench["exposure_matched"], controls=ctrl_r,
                                          zero_cost=zero.returns),
            "objective": O.evaluate(m, active),
            "cost_sensitivity_note": "the zero-cost book bounds the friction; 25 bps/side is an assumption, not a "
            "measured spread"}
    return out


def disposition(res: dict, holm_p: float | None, obj: O.Objective = O.PRINCIPAL_OBJECTIVE) -> tuple[str, list[str]]:
    """Registered before outcomes. Contaminated DEV history can only reject or nominate for a prospective test."""
    reasons = list(res["objective"]["violations"])
    g = res["active_vs_comparator"].get("active_log_growth_annual")
    if reasons:
        return "REJECTED_DEV", reasons
    if g is None or g <= 0:
        return "REJECTED_DEV", [f"does not beat its investable alternative ({res['comparator']}): {g}"]
    if g >= obj.minimum_effect_annual and holm_p is not None and holm_p < obj.alpha:
        return "PROMISING_EXPLORATORY", ["positive on contaminated DEV after Holm over every trial: a candidate for a "
                                         "separately registered prospective shadow, not evidence of a future edge"]
    return "INCONCLUSIVE_DEV", [f"active {g} with Holm p {holm_p}: not distinguishable from the alternative"]


def self_checks(data: Loaded, every: int = 5) -> dict:
    """Look-ahead trap (reads the next 20 sessions on purpose: it MUST be detected) and the random placebo."""
    pre, b, q = data.pre, data.b, data.quarantine
    lo, hi = b.dev_range
    trap, n = [], len(pre.sessions)
    for t in range(lo, hi + 1, every):
        el = [s for s in pre.stocks if pre.eligible[s][t]]
        fwd = {s: pre.market.c[s][min(n - 1, t + 20)] / pre.market.c[s][t] for s in el}
        fwd = {s: v for s, v in fwd.items() if math.isfinite(v)}
        if fwd:
            trap.append(S.Signal(max(fwd, key=lambda s: (fwd[s], s)), t, None, 0.0))  # CHEATING BY CONSTRUCTION
    placebo = A.sel_random_eligible(pre, b, {"seed": 7})[::every]
    out = {}
    for name, sigs in (("lookahead_trap", trap), ("placebo", placebo)):
        r = S.run_arm(f"v3_selfcheck_{name}", "E0X1", sigs, pre, b, q, stop_k=2.0, self_check=True)
        out[name] = {"signals": len(sigs), "test": S.paired_test(r.rows, b, "DEV")["summary"]}
    p_trap = out["lookahead_trap"]["test"].get("p_one_sided_max")
    out["lookahead_trap"]["detected"] = p_trap is not None and p_trap < 0.001
    p_pl = out["placebo"]["test"].get("p_one_sided_max")
    out["placebo"]["warning"] = p_pl is not None and p_pl < 0.01
    out["engine_valid"] = out["lookahead_trap"]["detected"] and not out["placebo"]["warning"]
    return out


def run_experiment(plan: ExperimentPlan, data: Loaded, out_dir: Path, *, experiment_id: str,
                   progress: Callable[[str], None] = print, n_boot: int = 999, code_sha: str | None = None) -> dict:
    if plan.fixture_only and data.provenance.data_class == "REAL_MARKET_DATA":
        raise ValueError(f"plan {plan.plan_id} is fixture-only (an engine smoke plan) and never runs on market data")
    out_dir.mkdir(parents=True, exist_ok=True)
    S.OPEN = data.pre.open_sessions
    lo, hi = data.b.dev_range
    window = EvaluationWindow(data.pre.sessions[lo].date().isoformat(), data.pre.sessions[hi].date().isoformat())
    obj = O.PRINCIPAL_OBJECTIVE
    reg = TrialRegistry(out_dir / "registry.jsonl")
    trials: dict[str, tuple[HypothesisSpec, PolicySpec]] = {}
    for h in plan.hypotheses:
        for tid, pol in zip(reg.register(h, data.provenance, obj.sha256()), h.policies, strict=True):
            trials[tid] = (h, pol)
    manifest = {"schema": "apex-discovery-v3-manifest-1", "experiment_id": experiment_id, "plan_id": plan.plan_id,
                "plan_sha256": plan.sha256(), "objective": obj.to_dict(), "objective_sha256": obj.sha256(),
                "provenance": data.provenance.__dict__, "window": window.__dict__,
                "max_evidence_tier": data.provenance.max_tier(), "trials": sorted(trials),
                "registry_head_at_freeze": reg.events[-1]["digest"] if reg.events else None,
                "code_sha": code_sha or os.environ.get("GITHUB_SHA"), "research_only": True,
                "frozen_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "status_at_freeze": "FROZEN_BEFORE_METRICS"}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    progress(f"manifest frozen: {len(trials)} trial(s) registered before any metric")
    checks = self_checks(data)
    progress(f"self-checks: trap detected {checks['lookahead_trap']['detected']}, placebo warning "
             f"{checks['placebo']['warning']}")
    results: dict[str, dict] = {}
    for tid, (h, pol) in trials.items():
        if h.blocked_inputs:
            reg.abandon(tid, f"SOURCE_BLOCKED: {h.blocked_inputs}")
            results[tid] = {"hypothesis_id": h.hypothesis_id, "policy_id": pol.policy_id, "disposition": "SOURCE_BLOCKED"}
            continue
        reg.look(tid, "the single registered DEV evaluation")
        results[tid] = {"hypothesis_id": h.hypothesis_id, "family_id": h.family_id} | evaluate_policy(pol, data, n_boot)
        progress(f"{tid} {pol.policy_id}: active vs {results[tid]['comparator']} "
                 f"{results[tid]['active_vs_comparator'].get('active_log_growth_annual')}")
    pv = {t: r.get("active_vs_comparator", {}).get("p_one_sided") for t, r in results.items()
          if r.get("disposition") != "SOURCE_BLOCKED"}
    adj = INF.holm({t: (1.0 if p is None else p) for t, p in pv.items()}) if pv else {}
    for tid, r in results.items():
        if r.get("disposition") == "SOURCE_BLOCKED":
            continue
        if not checks["engine_valid"]:
            disp, why = "SIMULATOR_INVALID", ["engine self-checks failed: no result of this batch is interpretable"]
        else:
            disp, why = disposition(r, adj.get(tid), obj)
        r |= {"holm_p_all_trials": PF._r(adj.get(tid), 5), "holm_m": len(pv), "disposition": disp,
              "disposition_reasons": why}
        reg.record(tid, {"disposition": disp, "disposition_reasons": why, "evidence_tier": "DEV_DIAGNOSTIC",
                         "principal": r["active_vs_comparator"], "holm_p": r["holm_p_all_trials"]}, data.provenance)
    reg.validate_complete()
    points = {r["policy_id"]: r["metrics"] for r in results.values() if "metrics" in r}
    frontier = {"axes": {"maximize": ["log_growth_annual"], "minimize_abs": ["max_drawdown", "expected_shortfall_5"],
                         "minimize": ["turnover_per_year"]},
                "non_dominated": O.pareto_front(points), "points": points,
                "status": "EXPLORATORY: DEV-only frontier on contaminated history; never a selection rule"}
    summary = {"experiment_id": experiment_id, "plan_id": plan.plan_id, "trials": len(trials),
               "dispositions": {t: r["disposition"] for t, r in results.items()}, "self_checks": checks,
               "aggregate": "NO_TRADE", "aggregate_reason": "V3 DEV results are diagnostics on contaminated history; "
               "no live or group-actionable output under any outcome"}
    (out_dir / "results.json").write_text(json.dumps({"summary": summary, "trials": results}, indent=1, sort_keys=True,
                                                     default=str))
    (out_dir / "frontier.json").write_text(json.dumps(frontier, indent=1, sort_keys=True))
    (out_dir / "TRIAL_INDEX.md").write_text(trial_index_md(reg, results))
    return summary


def trial_index_md(reg: TrialRegistry, results: dict[str, dict]) -> str:
    lines = ["# Trial index (every registered trial, losers included)", "",
             "| Trial | Hypothesis | Family | Policy | Axis | Disposition | Active log growth/yr vs comparator | Holm p "
             "(all trials) | Looks |", "|---|---|---|---|---|---|---|---|---|"]
    for row in reg.index():
        r = results.get(row["trial_id"], {})
        a = (r.get("active_vs_comparator") or {}).get("active_log_growth_annual")
        lines.append(f"| `{row['trial_id']}` | {row['hypothesis_id']} | {row['family_id']} | {row['policy_id']} | "
                     f"{row['varies_axis'] or 'primary'} | {row['disposition'] or row['closed']} | "
                     f"{'—' if a is None else f'{a:+.4f}'} vs {r.get('comparator', '—')} | "
                     f"{r.get('holm_p_all_trials', '—')} | {row['looks']} |")
    return "\n".join(lines) + "\n"


# ---- data ---------------------------------------------------------------------------------------------------------------
def load_data(source: str, *, now: datetime | None = None, per_sector: int = 6, env: dict | None = None,
              max_requests: int | None = None) -> Loaded:
    """Protocol-2's own SIP path (fixed 2016-01-04 start, 2-session finality lag, quality inventory, quarantine), or the
    synthetic DEMO fixture. Bars stay in a temporary directory; nothing but aggregates leaves this function's callers."""
    from apex_worker.market.calendar import default_calendar
    from apex_worker.research.dataset import data_hash
    from apex_worker.research.realeq.data import credentials_from_env
    from apex_worker.research.realeq.data_v2 import (
        data_end,
        fetch_v2,
        quality_inventory_v2,
        quarantine_from_quality,
        spec_v2,
    )
    from apex_worker.research.realeq.protocol_v2 import compute_boundaries_v2
    from apex_worker.research.realeq.universe_v2 import snapshot_sha256

    cal = default_calendar()
    env = os.environ if env is None else env
    demo = source == "fixture"
    if demo:
        from apex_worker.research.realeq.fixture import FIXTURE_NOW, FakeAlpaca
        from apex_worker.research.realeq.fixture_v2 import factor_frames_v2

        now = FIXTURE_NOW
        fake = FakeAlpaca(factor_frames_v2(per_sector=per_sector), corporate_actions=[])

        def factory():
            return fake.adapter(FIXTURE_NOW)
    elif source == "alpaca":
        from apex_worker.market.adapters.alpaca import AlpacaAdapter

        key, secret = credentials_from_env(env)
        now = now or datetime.now(UTC)

        def factory():
            return AlpacaAdapter(key, secret)
    else:
        raise ValueError("source must be alpaca or fixture")
    end = data_end(cal, now)
    spec = spec_v2(max_requests)
    with tempfile.TemporaryDirectory(prefix="apex-discovery-") as tmp:
        loaded = fetch_v2(factory, Path(tmp), spec, end=end, now=now)
    dq, usable = quality_inventory_v2(loaded, cal, min_usable_stocks=20 if demo else None)
    b = compute_boundaries_v2(usable[BENCHMARK].index)
    pre = S.prepare(usable)
    prov = DataProvenance("SYNTHETIC_FIXTURE_DEMO" if demo else "REAL_MARKET_DATA",
                          "fixture-emulation" if demo else "alpaca", dq["feed"], dq["adjustment"], dq["fetched_at"],
                          str(usable[BENCHMARK].index[0].date()), str(end), data_hash(usable), dq["history_hash"],
                          universe_snapshot_sha256=snapshot_sha256(), corporate_actions=dq["corporate_actions"]["status"])
    return Loaded(pre, eval_bounds(b), quarantine_from_quality(dq), prov,
                  {k: dq[k] for k in ("counts", "provider_requests_total", "data_end_after_finality_lag")})
