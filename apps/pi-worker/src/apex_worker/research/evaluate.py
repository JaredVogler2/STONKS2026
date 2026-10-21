"""Strategy evaluation, frozen promotion policy and champion/challenger comparison.

The promotion policy below is FROZEN (version `POLICY_VERSION`) before any candidate is compared to a
champion. A model is only CALIBRATED when every gate passes on the untouched holdout; EXPERIMENTAL with
a smaller sample; otherwise UNVALIDATED (no probability is shown). Models trained on synthetic fixtures
can never be used for live signals regardless of their metrics.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

import numpy as np
import pandas as pd

from apex_worker.research import metrics as M
from apex_worker.research.validation import ValidationConfig, WalkForwardResult, walk_forward

POLICY_VERSION = "promotion-policy-2"  # v2: permutation test (K shuffled-label runs) replaces a single shuffle
COST_GRID_BPS = (0.0, 10.0, 25.0, 50.0)


@dataclass(frozen=True)
class PromotionPolicy:
    min_holdout_entries_calibrated: int = 200
    min_holdout_entries_experimental: int = 100
    min_distinct_dates: int = 50
    min_regimes: int = 2
    min_brier_skill: float = 0.0
    max_ece: float = 0.06
    eval_cost_bps: float = 25.0
    min_selected_trades: int = 30
    min_lower_bound_mean_return: float = 0.0  # p10 of block-bootstrap mean net return at eval cost
    max_shuffled_skill: float = 0.01  # leakage alarm on the MEAN shuffled-label holdout skill
    shuffle_permutations: int = 20  # one shuffle is too noisy at small holdouts (sd up to ~0.13 measured)
    permutation_quantile: float = 0.95  # real holdout skill must beat this quantile of shuffled-label skills
    challenger_min_skill_gain: float = 0.005
    validity_days: int = 21


def net_at_cost(gross: pd.Series, bps: float) -> pd.Series:
    c = bps / 1e4
    return (1 + gross) * (1 - c) / (1 + c) - 1


def choose_threshold(oos: pd.DataFrame, cost_bps: float, min_trades: int) -> float | None:
    """Pick the P(target) cut on DEVELOPMENT out-of-sample predictions only (never the holdout)."""
    if oos.empty:
        return None
    best, best_val = None, -np.inf
    for thr in np.round(np.arange(0.20, 0.70, 0.02), 2):
        sel = oos[oos["p_target"] >= thr]
        if len(sel) < min_trades:
            continue
        val = float(net_at_cost(sel["return_gross"], cost_bps).mean())
        if val > best_val:
            best, best_val = float(thr), val
    return best


def selection_summary(df: pd.DataFrame, cost_bps: float) -> dict:
    if df.empty:
        return {"n": 0}
    r = net_at_cost(df["return_gross"], cost_bps).to_numpy(float)
    weeks = pd.to_datetime(df["decision_date"]).dt.to_period("W").astype(str).to_numpy()
    return {
        "n": len(df),
        "mean_return_net": round(float(r.mean()), 6),
        "ci": M.block_bootstrap_mean(r, weeks),
        "target_first_rate": round(float((df["outcome"] == "TARGET").mean()), 4),
        "mean_r_multiple": round(float(df["r_multiple"].mean()), 4),
    }


@dataclass
class StrategyReport:
    strategy_id: str
    data_source: str
    confidence_status: str
    status_reasons: list[str]
    gates: dict
    walk_forward: dict
    holdout: dict
    threshold: float | None
    selected_holdout: dict
    cost_sensitivity: dict
    baselines: dict
    regime_breakdown: dict
    shuffled_control: dict
    economics_all: dict
    bias_warnings: list[str]
    policy_version: str = POLICY_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_strategy(
    events: pd.DataFrame,
    strategy_id: str,
    features: tuple[str, ...],
    data_source: str,
    baselines: dict[str, pd.DataFrame] | None = None,
    bench_excess: pd.Series | None = None,
    policy: PromotionPolicy = PromotionPolicy(),
    vcfg: ValidationConfig = ValidationConfig(),
    bias_warnings: list[str] | None = None,
    holdout_start: pd.Timestamp | None = None,
) -> tuple[StrategyReport, WalkForwardResult]:
    ev = events[events["strategy_id"] == strategy_id].copy()
    wf = walk_forward(ev, features, vcfg, holdout_start=holdout_start)
    shuffled_runs = [
        walk_forward(ev, features, replace(vcfg, seed=vcfg.seed + i), shuffle_labels=True, holdout_start=holdout_start)
        for i in range(max(1, policy.shuffle_permutations))
    ]
    shuffled = shuffled_runs[0]
    shuffled_skills = np.array(
        [r.holdout_metrics["brier_skill"] for r in shuffled_runs if r.holdout_metrics.get("brier_skill") is not None],
        float,
    )
    thr = choose_threshold(wf.oos, policy.eval_cost_bps, policy.min_selected_trades)
    hold = wf.holdout
    selected = hold[hold["p_target"] >= thr] if (thr is not None and not hold.empty) else hold.iloc[0:0]
    sel_sum = selection_summary(selected, policy.eval_cost_bps)
    cost_sens = {f"{int(b)}bps": selection_summary(selected, b) for b in COST_GRID_BPS}
    hm = wf.holdout_metrics
    reasons: list[str] = []
    gates = {
        "holdout_entries": hm.get("n", 0),
        "distinct_dates": int(hold["decision_date"].nunique()) if not hold.empty else 0,
        "regimes": int(hold["regime"].nunique()) if not hold.empty else 0,
        "brier_skill": hm.get("brier_skill"),
        "ece": hm.get("ece"),
        "selected_trades": sel_sum.get("n", 0),
        "selected_mean_lower_p10": (sel_sum.get("ci") or {}).get("p10"),
        "shuffled_skill": round(float(shuffled_skills.mean()), 5) if len(shuffled_skills) else None,
        "shuffled_skill_q": round(float(np.quantile(shuffled_skills, policy.permutation_quantile)), 5)
        if len(shuffled_skills)
        else None,
        "permutation_p": round(
            float((1 + np.sum(shuffled_skills >= hm["brier_skill"])) / (1 + len(shuffled_skills))), 4
        )
        if len(shuffled_skills) and hm.get("brier_skill") is not None
        else None,
    }
    if gates["holdout_entries"] < policy.min_holdout_entries_experimental:
        reasons.append(f"holdout entries {gates['holdout_entries']} < {policy.min_holdout_entries_experimental}")
    if gates["distinct_dates"] < policy.min_distinct_dates:
        reasons.append(f"distinct dates {gates['distinct_dates']} < {policy.min_distinct_dates}")
    if gates["regimes"] < policy.min_regimes:
        reasons.append("holdout covers fewer than 2 regimes")
    if gates["brier_skill"] is None or gates["brier_skill"] <= policy.min_brier_skill:
        reasons.append("no Brier skill over the base rate on holdout")
    if gates["ece"] is None or gates["ece"] > policy.max_ece:
        reasons.append(f"calibration error {gates['ece']} > {policy.max_ece}")
    if thr is None or sel_sum.get("n", 0) < policy.min_selected_trades:
        reasons.append("too few selected holdout trades")
    elif (
        gates["selected_mean_lower_p10"] is None
        or gates["selected_mean_lower_p10"] <= policy.min_lower_bound_mean_return
    ):
        reasons.append(f"selected trades' p10 mean net return <= 0 at {policy.eval_cost_bps:g} bps/side")
    if gates["shuffled_skill"] is not None and gates["shuffled_skill"] > policy.max_shuffled_skill:
        reasons.append("RED TEAM FAIL: shuffled labels still show skill on average (leakage suspected)")
    if (
        gates["brier_skill"] is not None
        and gates["shuffled_skill_q"] is not None
        and gates["brier_skill"] <= gates["shuffled_skill_q"]
    ):
        reasons.append(
            f"holdout skill {gates['brier_skill']} not above the shuffled-label "
            f"{policy.permutation_quantile:.0%} quantile {gates['shuffled_skill_q']} (permutation p={gates['permutation_p']})"
        )
    if not reasons:
        status = "CALIBRATED" if gates["holdout_entries"] >= policy.min_holdout_entries_calibrated else "EXPERIMENTAL"
    else:
        status = "UNVALIDATED"
    base = {}
    for name, b in (baselines or {}).items():
        if b.empty:
            continue
        bh = (
            b[(b["decision_date"] >= wf.holdout_start) & b["filled"]]
            if wf.holdout_start is not pd.NaT
            else b[b["filled"]]
        )
        base[name] = selection_summary(bh, policy.eval_cost_bps)
    base["always_flat"] = {"n": 0, "mean_return_net": 0.0}
    if bench_excess is not None and not selected.empty:
        ex = bench_excess.reindex(selected.index)
        base["benchmark_same_windows"] = {"n": int(ex.notna().sum()), "mean_return": round(float(ex.mean()), 6)}
    regime_bd = {}
    if not hold.empty:
        for reg, g in hold.groupby("regime"):
            regime_bd[str(reg)] = {
                "n": len(g),
                "target_first_rate": round(float((g["outcome"] == "TARGET").mean()), 4),
                "mean_return_net_25bps": round(float(net_at_cost(g["return_gross"], 25).mean()), 6),
            }
    warn = list(bias_warnings or [])
    if data_source == "synthetic_fixture":
        warn.insert(
            0, "SYNTHETIC FIXTURE DATA: results demonstrate the pipeline only and are NOT evidence of a real edge"
        )
        if status != "UNVALIDATED":
            status = "EXPERIMENTAL"
            reasons.append("synthetic data: capped at EXPERIMENTAL and never eligible for live use")
    report = StrategyReport(
        strategy_id=strategy_id,
        data_source=data_source,
        confidence_status=status,
        status_reasons=reasons,
        gates=gates,
        walk_forward={
            "folds": [asdict(f) for f in wf.folds],
            "purged_samples": wf.purged_count,
            "holdout_start": str(wf.holdout_start.date()) if wf.holdout_start is not pd.NaT else None,
        },
        holdout=hm,
        threshold=thr,
        selected_holdout=sel_sum,
        cost_sensitivity=cost_sens,
        baselines=base,
        regime_breakdown=regime_bd,
        shuffled_control={
            **shuffled.holdout_metrics,
            "permutations": len(shuffled_skills),
            "skill_mean": gates["shuffled_skill"],
            "skill_sd": round(float(shuffled_skills.std()), 5) if len(shuffled_skills) else None,
            f"skill_q{int(policy.permutation_quantile * 100)}": gates["shuffled_skill_q"],
            "skill_max": round(float(shuffled_skills.max()), 5) if len(shuffled_skills) else None,
        },
        economics_all=M.economic_summary(ev),
        bias_warnings=warn,
    )
    return report, wf


def challenger_beats_champion(
    challenger: dict, champion: dict | None, policy: PromotionPolicy = PromotionPolicy()
) -> tuple[bool, str]:
    """Frozen comparison on the SAME most-recent holdout window. Higher backtest profit alone never promotes."""
    if challenger.get("confidence_status") == "UNVALIDATED":
        return False, "challenger failed validation gates"
    if champion is None:
        return True, "no incumbent champion"
    cs = (challenger.get("holdout") or {}).get("brier_skill")
    ps = (champion.get("holdout") or {}).get("brier_skill")
    if cs is None or ps is None:
        return False, "missing comparable holdout skill"
    if cs < ps + policy.challenger_min_skill_gain:
        return False, f"skill gain {cs - ps:+.4f} below required {policy.challenger_min_skill_gain}"
    clb = (challenger.get("gates") or {}).get("selected_mean_lower_p10")
    plb = (champion.get("gates") or {}).get("selected_mean_lower_p10")
    if clb is not None and plb is not None and clb < plb:
        return False, "economic lower bound worse than champion"
    return True, "challenger improves calibrated skill without weaker economics"
