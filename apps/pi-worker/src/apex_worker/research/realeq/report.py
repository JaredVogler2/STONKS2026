"""Writers for the seven compact files of research-results/real-equities/<experiment_id>/.

Only derived aggregates go here: counts, hashes, dates, returns/ratios computed over groups of simulated trades.
No vendor OHLCV rows, no per-trade prices, no credentials, no user positions.
"""

from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path

import numpy as np

from apex_worker.research.realeq.protocol import PROTOCOL

OUTPUT_FILES = (
    "manifest.json",
    "data_quality.json",
    "strategy_metrics.json",
    "trades_summary.csv",
    "comparisons.md",
    "recommendations.md",
    "execution_log.md",
)
CSV_COLUMNS = ("name", "kind", "segment", "horizon", "cost_bps_per_side", "signals", "filled", "no_fill", "completed",
               "target_first", "stop_first", "time_exit", "ambiguous_same_bar", "gap_through_stop", "mean_net",
               "median_net", "win_rate", "mean_r")
DEMO_BANNER = ("> **DEMO — SYNTHETIC FIXTURE (real-source emulation), NOT MARKET DATA.** Pipeline smoke test only; "
               "nothing here is evidence about real equities and it must never be filed under research-results/.")


def clean(obj):
    """JSON-safe: NaN/inf -> None, numpy scalars -> Python, sets/tuples -> lists."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(obj, np.bool_):
        return bool(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(clean(obj), indent=2, sort_keys=True, allow_nan=False) + "\n")


def write_csv(path: Path, rows: list[dict]) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(CSV_COLUMNS), lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in CSV_COLUMNS})
    path.write_text(buf.getvalue())


def pct(x, nd: int = 2) -> str:
    return "—" if x is None else f"{100 * float(x):.{nd}f}%"


def num(x, nd: int = 3) -> str:
    return "—" if x is None else f"{float(x):.{nd}f}"


def _table(head: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head), *("| " + " | ".join(r) + " |" for r in rows)]


def comparisons_md(m: dict) -> str:
    demo = m["data_class"] != "REAL_MARKET_DATA"
    b = m.get("boundaries") or {}
    H, C = PROTOCOL["primary_horizon"], PROTOCOL["primary_cost_bps"]
    out = [f"# Real-equity strategy evaluation — `{m['experiment_id']}`", ""]
    if demo:
        out += [DEMO_BANNER, ""]
    out += [f"* Run status: **{m['run_status']}**", f"* Aggregate conclusion: **{m['aggregate'].get('conclusion', 'NOT RUN')}** "
            f"— {m['aggregate'].get('live_alerts', 'live alerts disabled')}",
            f"* Protocol `{PROTOCOL['version']}` (sha256 `{m['protocol_sha256'][:16]}…`), code `{m.get('code_sha', 'unknown')}`",
            f"* Data: Alpaca `{m.get('feed', 'sip')}` daily bars, split-adjusted, price-only; universe fixed78 "
            f"(66 traded stocks); dataset hash `{(m.get('dataset_hash') or '')[:16]}…`"]
    if b:
        out += [f"* Development {b['decision_start']} → {b['development_end']}; embargo/purge before {b['embargo_limit']}; "
                f"**untouched holdout {b['holdout_start']} → {b['holdout_end']}** ({b['holdout_sessions']} sessions)"]
    out.append("")
    strat = m.get("strategies") or {}
    if not strat or m["run_status"] != "COMPLETED":
        out += ["No strategy results: the run did not complete (see execution_log.md). No backtest is claimed.", ""]
        return "\n".join(out) + "\n"
    out += [f"## Summary (primary configuration: H={H} sessions, {C} bps per side, holdout, rule = qualified + "
            "regime-allowed + de-duplicated signals)", ""]
    rows = []
    for sid, r in strat.items():
        h = r.get("holdout") or {}
        ci = h.get("mean_net_ci") or {}
        rnd = r.get("matched_random") or {}
        rows.append([sid, f"**{r['disposition']}**", str(h.get("completed", 0)), pct(h.get("mean_net"), 3),
                     pct(ci.get("p10"), 3), pct(h.get("win_rate"), 1), num(h.get("mean_r"), 3), num(h.get("profit_factor"), 2),
                     pct(rnd.get("median"), 3), num(rnd.get("strategy_percentile"), 2), str(r.get("forecast_status") or "—")])
    out += _table(["Hypothesis", "Disposition", "Holdout trades", "Mean net", "Bootstrap p10", "Win rate", "Mean R",
                   "Profit factor", "Matched-random median", "Pctl vs random", "Forecast"], rows)
    out += ["", "A high win rate is not an edge: compare mean net, the bootstrap p10 and mean R (average losses vs gains).", ""]
    bl = m.get("baselines") or {}
    out += ["## Baselines (same levels-from-ATR, execution and costs; holdout)", ""]
    brows = []
    for name in ("momentum_20", "breakout_basic"):
        h = (bl.get(name) or {}).get("holdout") or {}
        ci = h.get("mean_net_ci") or {}
        brows.append([name, str(h.get("completed", 0)), pct(h.get("mean_net"), 3), pct(ci.get("p10"), 3),
                      pct(h.get("win_rate"), 1), num(h.get("mean_r"), 3)])
    out += _table(["Baseline", "Holdout trades", "Mean net", "Bootstrap p10", "Win rate", "Mean R"], brows)
    spy = (bl.get("spy_buy_and_hold") or {}).get("holdout") or {}
    out += ["", f"SPY buy-and-hold over the holdout: total {pct(spy.get('total_return'))}, CAGR {pct(spy.get('cagr'))}, "
            f"max drawdown {pct(spy.get('max_drawdown'))} — 100% exposure, not a fair comparator for a swing book.", ""]
    out += ["## Cost sensitivity (holdout mean net per trade)", ""]
    crow = []
    for sid, r in strat.items():
        g = r.get("holdout_cost_grid") or {}
        crow.append([sid, *(pct((g.get(f"{c}bps") or {}).get("mean_net"), 3) for c in PROTOCOL["costs_bps_per_side"])])
    out += _table(["Hypothesis", *(f"{c} bps/side" for c in PROTOCOL["costs_bps_per_side"])], crow)
    out += ["", f"## Horizon sensitivity (holdout mean net at {C} bps; time exit at H sessions)", ""]
    hrow = []
    for sid, r in strat.items():
        g = r.get("holdout_horizons") or {}
        hrow.append([sid, *(f"{pct((g.get(f'H{h}') or {}).get('mean_net'), 3)} (n={(g.get(f'H{h}') or {}).get('completed', 0)})"
                            for h in PROTOCOL["horizons"])])
    out += _table(["Hypothesis", *(f"H={h}" for h in PROTOCOL["horizons"])], hrow)
    vals = [(sid, (r.get("holdout") or {}).get("mean_net")) for sid, r in strat.items()]
    vals = [(s, v) for s, v in vals if v is not None]
    if vals:
        labels = ", ".join(f'"{s.split("_")[0].upper()}"' for s, _ in vals)
        out += ["", "```mermaid", "xychart-beta", f'    title "Holdout mean net return per trade, bps ({C} bps/side)"',
                f"    x-axis [{labels}]", '    y-axis "bps"', f"    bar [{', '.join(f'{1e4 * v:.1f}' for _, v in vals)}]", "```"]
    out += ["", f"## Capital-constrained book ({PROTOCOL['portfolio']['slots']} equal slots, holdout, {C} bps/side)", ""]
    prow = []
    for name, p in [*((sid, r.get("portfolio")) for sid, r in strat.items()),
                    *((n, (bl.get(n) or {}).get("portfolio")) for n in ("momentum_20", "breakout_basic"))]:
        if not p or not p.get("trades_taken"):
            continue
        prow.append([name, str(p["trades_taken"]), str(p["trades_skipped_capacity"]), pct(p.get("total_return")),
                     pct(p.get("cagr")), pct(p.get("max_drawdown")), pct(p.get("avg_exposure"), 1),
                     pct(p.get("spy_total_return_same_period")), pct(p.get("spy_exposure_scaled_return"))])
    out += _table(["Book", "Taken", "Skipped (capacity)", "Total", "CAGR", "Max DD", "Avg exposure", "SPY same period",
                   "SPY × exposure"], prow) if prow else ["No completed holdout trades to allocate."]
    out += ["", "## Does Quality rank realized net R? (Spearman, weekly-block bootstrap 90% interval)", ""]
    qrow = []
    for sid, r in strat.items():
        q = (r.get("quality_rank") or {}).get("holdout") or {}
        d = (r.get("quality_rank") or {}).get("development") or {}
        if q.get("n"):
            qrow.append([sid, str(q.get("n")), num(q.get("spearman"), 3), str(q.get("ci90") or "—"), num(d.get("spearman"), 3)])
    out += _table(["Hypothesis", "Holdout n", "Spearman", "90% CI", "Development Spearman"], qrow) if qrow else ["n/a"]
    out += ["", "## Robustness: held-out sector groups (rule, holdout) and cross-sector model", ""]
    grp = PROTOCOL["robustness_splits"]["sector_groups"]
    out += [f"Group A = {', '.join(grp['A'])}; group B = {', '.join(grp['B'])}.", ""]
    srow = []
    for sid, r in strat.items():
        s = r.get("sector_rule") or {}
        x = r.get("sector_cross_model") or {}
        if not s:
            continue
        srow.append([sid, f"{pct((s.get('A') or {}).get('mean_net'), 3)} (n={(s.get('A') or {}).get('completed', 0)})",
                     f"{pct((s.get('B') or {}).get('mean_net'), 3)} (n={(s.get('B') or {}).get('completed', 0)})",
                     num((x.get('train_A_test_B') or {}).get('brier_skill'), 4),
                     num((x.get('train_B_test_A') or {}).get('brier_skill'), 4)])
    out += _table(["Hypothesis", "Group A mean net", "Group B mean net", "Model A→B Brier skill", "Model B→A Brier skill"], srow) \
        if srow else ["n/a"]
    out += ["", "## Forecast model (existing pipeline, promotion-policy-2 gates on the same frozen holdout)", ""]
    mrow = []
    for sid, r in strat.items():
        mo = r.get("model") or {}
        if not mo:
            continue
        g, h, sel = mo.get("gates") or {}, mo.get("holdout") or {}, mo.get("selected_holdout") or {}
        mrow.append([sid, str(mo.get("confidence_status")), "—" if h.get("n") is None else str(h["n"]),
                     num(h.get("brier_skill"), 4), num(h.get("ece"), 4),
                     num(g.get("permutation_p"), 3), num(mo.get("threshold"), 2), str(sel.get("n", 0)), pct(sel.get("mean_return_net"), 3)])
    out += _table(["Hypothesis", "Status", "Holdout n", "Brier skill", "ECE", "Permutation p", "Threshold", "Selected n",
                   "Selected mean net"], mrow) if mrow else ["n/a"]
    out += ["", "## Execution mechanics (holdout, rule)", ""]
    erow = []
    for sid, r in strat.items():
        h = r.get("holdout") or {}
        if not h.get("signals"):
            continue
        n = h.get("completed") or 0
        erow.append([sid, str(h.get("signals")), pct(h.get("fill_rate"), 1), str(h.get("no_fill")),
                     f"{h.get('target_first')}/{h.get('stop_first')}/{h.get('time_exit')}",
                     f"{h.get('ambiguous_same_bar')} ({pct((h.get('ambiguous_same_bar') or 0) / n if n else None, 1)})",
                     str(h.get("gap_through_stop")), pct(h.get("mean_net_unresolved_excluded"), 3), num(h.get("mean_sessions_held"), 1),
                     f"{pct(h.get('mean_mfe'), 2)} / {pct(h.get('mean_mae'), 2)}"])
    out += _table(["Hypothesis", "Signals", "Fill rate", "No fill", "Target/Stop/Time", "Same-bar ambiguous", "Gap through stop",
                   "Mean net excl. ambiguous", "Avg hold", "MFE / MAE"], erow) if erow else ["n/a"]
    out += ["", "## Funnel (all sessions in the decision window, H=20)", ""]
    frow = [[sid, *(str((r.get("funnel") or {}).get(k, 0)) for k in ("triggered", "gate_eligible", "quality_scored",
                                                                       "levels_valid", "regime_allowed", "rule_after_dedup",
                                                                       "filled", "completed_horizon"))]
            for sid, r in strat.items()]
    out += _table(["Hypothesis", "Triggered", "Gate eligible", "Quality scored", "Levels valid", "Regime allowed",
                   "After de-dup", "Filled", "Completed"], frow)
    out += ["", "## Ablations and other attempted variants (holdout)", ""]
    arow = []
    for sid, r in strat.items():
        ab = r.get("ablations") or {}
        if not ab:
            continue
        arow.append([sid, pct((r.get("holdout") or {}).get("mean_net"), 3), pct((ab.get("all_regimes") or {}).get("mean_net"), 3),
                     pct((ab.get("quality_top_half") or {}).get("mean_net"), 3),
                     num((ab.get("model_full_features") or {}).get("brier_skill"), 4),
                     num((ab.get("model_without_quality_features") or {}).get("brier_skill"), 4)])
    out += _table(["Hypothesis", "Rule mean net", "All regimes", "Quality top half", "Model skill (full)", "Model skill (no Quality)"],
                  arow) if arow else ["n/a"]
    out += ["", f"Configurations evaluated in total: **{m.get('configurations_attempted', 0)}** (all listed in "
            "strategy_metrics.json; dispositions use only the pre-registered primary configuration, Holm-adjusted).", ""]
    out += ["## Data and limitations", ""]
    out += [f"* {w}" for w in m.get("bias_warnings", [])]
    out += ["* Daily OHLC cannot reveal the intraday order of the high and low; same-bar target+stop is booked as a loss.",
            "* The universe is 66 surviving large caps: results describe this sample, not a generalizable population.", ""]
    return "\n".join(out) + "\n"


def recommendations_md(m: dict) -> str:
    demo = m["data_class"] != "REAL_MARKET_DATA"
    out = [f"# Recommendations — `{m['experiment_id']}`", ""]
    if demo:
        out += [DEMO_BANNER, ""]
    strat = m.get("strategies") or {}
    if m["run_status"] != "COMPLETED":
        out += [f"Run status **{m['run_status']}**: no strategy evidence was produced, so no strategy is kept, modified or "
                "retired on the basis of this run.", "", "## Required next action", "",
                m.get("blocker_action", "See execution_log.md."), ""]
        return "\n".join(out) + "\n"
    out += [f"**Aggregate: {m['aggregate']['conclusion']}.** {m['aggregate']['live_alerts']}.", "",
            "## Per hypothesis", ""]
    rows = []
    for sid, r in strat.items():
        h = r.get("holdout") or {}
        ev = (f"holdout n={h.get('completed', 0)}, mean net {pct(h.get('mean_net'), 3)}, p10 "
              f"{pct((h.get('mean_net_ci') or {}).get('p10'), 3)}, Holm p={r.get('holm_adjusted_p')}") if h.get("completed") else \
            (r.get("not_evaluable_reason") or "no completed trades")
        rows.append([sid, r["disposition"], r["recommendation"], ev + "; " + "; ".join(r.get("disposition_reasons", []))])
    out += _table(["Hypothesis", "Disposition", "Recommendation", "Evidence"], rows)
    out += ["", "## Prioritised next experiments (ranked by evidence; each is a new pre-registered protocol version)", ""]
    for i, item in enumerate(next_experiments(m), 1):
        out.append(f"{i}. {item}")
    out += ["", "No untested H1/H2/H3 weighting changes are recommended from this run. Do not lower any gate to graduate a "
            "model; keep live/group high-confidence alerts disabled.", ""]
    return "\n".join(out) + "\n"


def next_experiments(m: dict) -> list[str]:
    dq = m.get("data_quality") or {}
    strat = m.get("strategies") or {}
    items: list[str] = []
    excluded = [s["symbol"] for s in dq.get("symbols", []) if s.get("status") not in ("OK", None)]
    flagged = [s["symbol"] for s in dq.get("symbols", []) if s.get("large_move_sessions")]
    items.append("**Data integrity first.** "
                 + (f"Excluded symbols: {', '.join(excluded)}. " if excluded else "No symbol was excluded. ")
                 + (f"Review flagged >25% daily moves for {', '.join(flagged[:10])}. " if flagged else "")
                 + ("Corporate actions were unavailable: confirm split adjustment independently. "
                    if str(dq.get("corporate_actions", "")).startswith("UNAVAILABLE") else "")
                 + "Add a point-in-time earnings calendar before trusting earnings-sensitive results.")
    amb = [(s, (r.get("holdout") or {}).get("ambiguous_same_bar") or 0, (r.get("holdout") or {}).get("completed") or 0)
           for s, r in strat.items()]
    hi_amb = [s for s, a, n in amb if n and a / n > 0.05]
    stop_heavy = [s for s, r in strat.items() if (r.get("holdout") or {}).get("completed")
                  and ((r["holdout"].get("stop_first") or 0) / r["holdout"]["completed"]) > 0.5]
    items.append("**Entry/stop/target mechanics.** "
                 + (f"Same-bar ambiguity above 5% for {', '.join(hi_amb)}: test intraday (15-min) resolution before "
                    "changing levels. " if hi_amb else "Same-bar ambiguity is small. ")
                 + (f"Stops hit first in most trades for {', '.join(stop_heavy)}: test wider ATR stops / later "
                    "entries as a pre-registered variant. " if stop_heavy else "")
                 + "Compare H=5..20 results only as sensitivity; the disposition uses H=20.")
    qbad = [s for s, r in strat.items() if ((r.get("quality_rank") or {}).get("holdout") or {}).get("n")
            and not r["quality_rank"]["holdout"].get("positive_and_significant")]
    items.append("**Ranking calibration.** "
                 + (f"Quality does not significantly rank realized net R for {', '.join(qbad)}: do not present Quality "
                    "as a confidence proxy; re-fit the score on development data only. " if qbad else
                    "Quality ranks net R where tested; confirm out of sample with a forward test. ")
                 + "Probability forecasts stay hidden unless the frozen calibration gates pass.")
    items.append("**Catalysts and regimes (later).** H3/H7 need point-in-time catalyst history (available_at <= decision); "
                 "do not reconstruct catalysts after the fact or use delayed congressional disclosures as if known at "
                 "execution. Regime-conditional variants only after the base rules are economically positive.")
    return items


def execution_log_md(m: dict, steps: list[dict]) -> str:
    gh = m.get("runner") or {}
    out = [f"# Execution log — `{m['experiment_id']}`", ""]
    if m["data_class"] != "REAL_MARKET_DATA":
        out += [DEMO_BANNER, ""]
    out += [f"* Run status: **{m['run_status']}**" + (f" — {m.get('run_reason')}" if m.get("run_reason") else ""),
            f"* GitHub Actions run: {gh.get('run_url') or 'not a GitHub Actions run (local)'}",
            f"* Workflow: {gh.get('workflow') or '—'}; event {gh.get('event') or '—'}; ref {gh.get('ref') or '—'}; "
            f"attempt {gh.get('run_attempt') or '—'}",
            f"* Runner: {gh.get('runner_os') or '—'} {gh.get('runner_arch') or ''} image {gh.get('image_os') or '—'} "
            f"{gh.get('image_version') or ''}; Python {gh.get('python')}",
            f"* Code commit: `{m.get('code_sha', 'unknown')}`", f"* Started {m.get('started_at')} — finished {m.get('finished_at')}",
            f"* Credentials: ALPACA_KEY_ID {m.get('secret_presence', {}).get('ALPACA_KEY_ID', 'n/a')}, "
            f"ALPACA_SECRET_KEY {m.get('secret_presence', {}).get('ALPACA_SECRET_KEY', 'n/a')} (presence only; values never logged)",
            "", "## Steps", ""]
    out += _table(["Step", "Status", "Detail"], [[s["step"], s["status"], str(s.get("detail") or "").replace("|", "/")] for s in steps])
    errs = m.get("errors") or []
    out += ["", "## Data / API errors", ""] + ([f"* {e}" for e in errs] if errs else ["None recorded."])
    not_run = [s["step"] for s in steps if s["status"] in ("not_run", "skipped")]
    out += ["", "## Explicitly NOT run", ""] + ([f"* {s}" for s in not_run] if not_run else ["Nothing skipped."])
    out += ["", "## Never done by this workflow", "",
            "* no model signing, registry upload, promotion or champion change; no Discord/group messages; no orders",
            "* no raw OHLCV, keys or user positions in outputs or artifacts (bars lived only in the runner's temp dir)", ""]
    if m.get("blocker_action"):
        out += ["## Blocker and owner action", "", m["blocker_action"], ""]
    return "\n".join(out) + "\n"
