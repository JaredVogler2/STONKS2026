"""Writers for the seven protocol-2 files (same names as protocol-1, so the workflow artifact list is unchanged).

strategy_metrics.json consolidates the template's metrics, portfolio, ablation and error-sensitivity sections
(amendment 12 section 2 allows consolidation with an index). Aggregates only: no vendor OHLCV, no per-trade prices,
no credentials, no member positions.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from apex_worker.research.realeq.protocol_v2 import PROTOCOL_V2
from apex_worker.research.realeq.report import DEMO_BANNER, clean, execution_log_md, num, pct, write_json  # noqa: F401

CSV_COLUMNS_V2 = ("name", "arm", "segment", "cost_bps_per_side", "signals", "filled", "mean_per_signal_net",
                  "control_mean_per_signal_net", "mean_paired")
INDEX = {
    "aggregate": "NO_TRADE unless a RULE_VALIDATED_RESEARCH_OOS; live alerts disabled either way",
    "strategies.<S>.selection": "DEV-only arm choice (the rule is frozen in the protocol)",
    "strategies.<S>.primary": "the ONE PRESAMPLE look at the selected arm: decision test, optimistic leg, sensitivities",
    "strategies.<S>.arms.<arm>.DEV": "DEV_CONTAMINATED diagnostics for every arm",
    "strategies.<S>.mechanism": "ablation: the strategy's own null (leaders, raw momentum, retest vs immediate, EW sectors)",
    "strategies.<S>.books": "portfolio: 10-slot capital-constrained books vs matched control books and SPY/cash",
    "legacy": "H1-H8 comparators on the same engine (own Holm family)",
    "baselines": "raw momentum, basic breakout, SPY, flat",
    "engine_self_checks": "look-ahead trap and placebo",
    "multiplicity": "family, raw and Holm-adjusted p",
    "selection_lock": "the DEV-only arm choice, hashed before any PRESAMPLE outcome existed (A1 isolation)",
    "configurations_attempted": "every strategy x arm x segment evaluated (search ledger)",
}


def write_csv_v2(path: Path, rows: list[dict]) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(CSV_COLUMNS_V2), lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in CSV_COLUMNS_V2})
    path.write_text(buf.getvalue())


def _t(head: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(r) + " |" for r in rows]


def _p(x) -> str:
    return "—" if x is None else f"{x:.4f}"


def comparisons_md_v2(m: dict) -> str:
    demo = m["data_class"] != "REAL_MARKET_DATA"
    out = [f"# Comparisons — `{m['experiment_id']}` (`{PROTOCOL_V2['version']}`)", ""]
    if demo:
        out += [DEMO_BANNER, ""]
    if m.get("run_status") != "COMPLETED":
        out += [f"**Run status: {m.get('run_status')}** — {m.get('run_reason', '')}", "", "No strategy was evaluated.", ""]
        return "\n".join(out) + "\n"
    b, agg = m["boundaries"], m["aggregate"]
    out += [f"**Aggregate: {agg['conclusion']}.** Live alerts, promotion and orders stay disabled.", "",
            f"* PRESAMPLE (earlier in time than the design data): decisions {b['presample_start']} → {b['presample_end']}. "
            "Evidence type: **backward-time historical robustness, not forward out-of-sample**. Integrity: **exposed** — "
            "the cancelled A0 run 39829472789 computed and published PRESAMPLE outcomes (log, job summary, artifact); "
            "the analyst never read them and the design was not changed in response (PROTOCOL_V2.md §12)",
            f"* DEV (contaminated: design and diagnostics only): decisions {b['dev_start']} → {b['dev_end']}",
            f"* Universe: U1 {PROTOCOL_V2['universe']['classification']} (snapshot {PROTOCOL_V2['universe']['snapshot']}); "
            f"costs {PROTOCOL_V2['primary_cost_bps']} bps per side; pessimistic leg; protocol sha256 `{m['protocol_sha256'][:16]}…`",
            "", "## Primary family (one PRESAMPLE look per strategy)", ""]
    rows = []
    for sid in ("S1", "S2", "S3", "S4", "S5"):
        st = m["strategies"][sid]
        if sid == "S4":
            rows.append([sid, st["title"], "—", "—", "—", "—", "—", "—", "—", f"**{st['disposition']}**"])
            continue
        pr = st["primary"]
        s = pr["test"]["summary"]
        d = pr.get("describe") or {}
        if sid == "S5":
            rows.append([sid, st["title"], pr["arm"], str(d.get("sessions", "—")),
                         num(d.get("total_return"), 4), num(d.get("exposure_matched_spy_total_return"), 4),
                         num(s.get("mean"), 6) + " /day", _p(s.get("p_one_sided_max")), _p(st.get("holm_adjusted_p")),
                         f"**{st['disposition']}**"])
            continue
        rows.append([sid, st["title"], pr["arm"], str(d.get("signals", 0)), pct(d.get("mean_per_signal_net"), 3),
                     pct(d.get("control_mean_per_signal_net"), 3), pct(s.get("mean"), 3), _p(s.get("p_one_sided_max")),
                     _p(st.get("holm_adjusted_p")), f"**{st['disposition']}**"])
    out += _t(["", "Strategy", "Arm (DEV)", "Signals", "Per-signal net", "Control", "Paired mean", "p (cons.)", "Holm p",
               "Disposition"], rows)
    out += ["", "S5 columns: sessions, total return, exposure-matched SPY/cash total return, mean daily active return.", "",
            "## Reasons", ""]
    for sid in ("S1", "S2", "S3", "S4", "S5"):
        st = m["strategies"][sid]
        out.append(f"* **{sid}** {st['disposition']}: {'; '.join(st.get('disposition_reasons') or [])}")
    out += ["", "## Decision statistics (PRESAMPLE, locked arm; conservative over L = 20 and 40)", ""]
    rows = []
    for sid in ("S1", "S2", "S3", "S5"):
        st = m["strategies"][sid]
        t = st["primary"]["test"]
        s = t["summary"]
        cis = [((t.get(f"L{L}") or {}).get("ci90") or [None, None]) for L in (20, 40)]
        lo95 = min((c[0] for c in cis if c[0] is not None), default=None)
        def fmt(x, s5=sid == "S5"):
            return num(x, 6) if s5 else pct(x, 3)

        rows.append([sid, str(t.get("n", "—")), str(s.get("status")), fmt(s.get("mean")), fmt(lo95),
                     fmt(s.get("upper_95_max")), _p(s.get("p_one_sided_max")), _p(st.get("holm_adjusted_p")),
                     _p((st["primary"].get("optimistic_leg") or {}).get("summary", {}).get("p_one_sided_max"))])
    out += _t(["", "n", "Clusters", "Mean (paired; S5 daily active)", "90% CI low", "90% CI high", "p (cons.)",
               "Holm p (family of 4)", "p optimistic leg"], rows)
    out += ["", "The 90% interval is the two-sided studentized block interval (one-sided 95% bounds), widest over the "
            "decision block lengths. A p-value exists only with >= 12 non-empty blocks at every decision length.",
            "", "## DEV diagnostics per arm (CONTAMINATED; selection only)", ""]
    rows = []
    for sid in ("S1", "S2", "S3"):
        for arm, v in m["strategies"][sid]["arms"].items():
            dv = v["DEV"]
            s = dv["test"]["summary"]
            rows.append([sid, arm, str(dv.get("signals", 0)), pct(dv.get("mean_per_signal_net"), 3), pct(s.get("mean"), 3),
                         str(s.get("status")), "✔" if m["strategies"][sid]["selection"]["chosen_arm"] == arm else ""])
    for arm, v in m["strategies"]["S5"]["arms"].items():
        s = v["DEV"]["test"]["summary"]
        rows.append(["S5", arm, str(v["DEV"]["sessions"]), num(v["DEV"]["total_return"], 4), num(s.get("mean"), 6),
                     str(s.get("status")), "✔" if m["strategies"]["S5"]["selection"]["chosen_arm"] == arm else ""])
    out += _t(["", "Arm", "Signals", "Per-signal net", "Paired mean", "Test status", "Chosen"], rows)
    out += ["", "## Mechanism contrasts (each strategy's own null; descriptive)", ""]
    for sid in ("S1", "S2", "S3"):
        mech = m["strategies"][sid].get("mechanism") or {}
        ps = mech.get("PRESAMPLE") or {}
        pres_txt = (f"PRESAMPLE {ps['withheld']}" if "withheld" in ps else
                    f"PRESAMPLE mean {pct(ps.get('mean'), 3)}, p {_p(ps.get('p_one_sided_max'))} ({ps.get('status')})")
        out.append(f"* {sid}: {pres_txt}; DEV mean {pct((mech.get('DEV') or {}).get('mean'), 3)} (DEV is contaminated)")
    s5 = m["strategies"]["S5"]["primary"]["describe"]
    out.append(f"* S5 vs equal-weight sectors: mean daily {num(s5.get('mean_daily_vs_equal_weight'), 6)}")
    out += ["", "## Sensitivities of the primary arm (PRESAMPLE)", ""]
    rows = []
    for sid in ("S1", "S2", "S3"):
        d = m["strategies"][sid]["primary"].get("describe") or {}
        sens = m["strategies"][sid]["primary"]["test"].get("sensitivity", {}).get("L60", {})
        rows.append([sid, *(pct(d.get(f"mean_paired_{c}bps"), 3) for c in PROTOCOL_V2["costs_bps_per_side"]),
                     pct(d.get("mean_per_signal_net_0bps"), 3), pct(d.get("mean_per_signal_net_50bps"), 3),
                     pct(d.get("mean_paired_optimistic"), 3), pct(d.get("mean_paired_touch_fill_sensitivity"), 3),
                     pct(d.get("ambiguous_share_of_fills"), 1), _p(sens.get("p_one_sided"))])
    out += _t(["", "Paired 0 bps", "Paired 10 bps", "Paired 25 bps", "Paired 50 bps", "Net/signal 0 bps",
               "Net/signal 50 bps", "Optimistic leg (paired)", "Touch-fill (paired)", "Ambiguous fills", "p (L=60)"], rows)
    out += ["", "Paired differences barely move with cost because the strategy and its controls pay the same cost per "
            "fill; the absolute per-signal net shows the cost drag."]
    out += ["", "## Trade economics of the primary arm (PRESAMPLE, pessimistic leg, 25 bps)", ""]
    rows = []
    for sid in ("S1", "S2", "S3"):
        d = m["strategies"][sid]["primary"].get("describe") or {}
        net = (d.get("per_filled_trade") or {}).get("net_25bps") or {}
        gross = (d.get("per_filled_trade") or {}).get("gross_0bps") or {}
        ex = (d.get("exit_shares_of_fills") or {}).get("pessimistic") or {}
        rows.append([sid, str(d.get("initial_signals", "—")), str(d.get("signals", 0)), pct(d.get("fill_rate"), 1),
                     pct(d.get("net_per_initial_signal"), 3), pct(gross.get("mean"), 3), pct(net.get("mean"), 3),
                     pct(net.get("median"), 3), pct(net.get("win_rate"), 1), pct(net.get("avg_win"), 2),
                     pct(net.get("avg_loss"), 2), num(net.get("payoff_ratio"), 2),
                     *(pct((ex.get(k) or {}).get("share"), 1) for k in ("STOP", "TARGET", "TIME")),
                     pct(d.get("no_fill_share_of_signals"), 1), "yes" if net.get("small_sample") else ""])
    out += _t(["", "Initial signals", "Kept", "Fill rate", "Net / initial signal", "Gross / fill", "Net / fill",
               "Median net / fill", "Win rate", "Avg win", "Avg loss", "Payoff", "Stop", "Target", "Time", "No fill",
               "Small sample"], rows)
    rows = []
    for sid in ("S1", "S2", "S3"):
        pr = m["strategies"][sid]["primary"]
        d = pr.get("describe") or {}
        res = d.get("exit_outcomes_resolved") or {}
        mfe, mae = d.get("mfe_bounds_mean") or [None, None], d.get("mae_bounds_mean") or [None, None]
        mx = pr.get("market_exposure") or {}
        rows.append([sid, str(res.get("STOP", 0)), str(res.get("TARGET", 0)), str(res.get("TIME", 0)),
                     str(res.get("UNRESOLVED_DAILY_BAR_AMBIGUITY", 0)), f"{pct(mfe[0], 2)} … {pct(mfe[1], 2)}",
                     f"{pct(mae[0], 2)} … {pct(mae[1], 2)}", num(d.get("mean_sessions_held"), 1),
                     num(mx.get("beta"), 2), pct(mx.get("alpha_per_trade"), 3),
                     pct(mx.get("signals_on_top_5pct_sessions_share"), 1)])
    out += ["", "Kept = after the levels screen, quarantine, the DEV-crossing exclusion and de-duplication. Exit shares "
            "are of fills on the pessimistic leg (an ambiguous bar counts as a stop there); the next table separates "
            "the exits daily bars resolve from the ones they cannot.",
            "", "Exits that daily bars resolve, the unresolvable ones, excursion bounds and market exposure:", ""]
    out += _t(["", "Stop (resolved)", "Target (resolved)", "Time", "Unresolved (daily-bar ambiguity)",
               "MFE bounds (mean)", "MAE bounds (mean)", "Sessions held", "Beta to SPY (trade window)",
               "Alpha / trade", "Signals on top 5% sessions"], rows)
    out += ["", "MFE/MAE are bounds, not observations: daily bars cannot order the high and low around the fill. Beta "
            "and alpha regress each trade's net return on SPY's return from the decision close to the exit close "
            "(approximate, descriptive).", "",
            "Where the paired mean comes from: paired = p_s (r_s − r_c) + (p_s − p_c) r_c, with p the fill rate and r the "
            "net per fill (no-fill = 0 for both legs):", ""]
    rows = []
    for sid in ("S1", "S2", "S3"):
        d = m["strategies"][sid]["primary"].get("describe") or {}
        dec = d.get("paired_decomposition") or {}
        rows.append([sid, pct(dec.get("strategy_fill_rate"), 1), pct(d.get("control_fill_rate"), 1),
                     pct(dec.get("strategy_net_per_fill"), 3), pct(dec.get("control_net_per_fill"), 3),
                     pct(dec.get("per_fill_term"), 3), pct(dec.get("fill_rate_term"), 3),
                     ", ".join(f"{k} {v}" for k, v in (d.get("trade_flags") or {}).items()) or "none"])
    out += _t(["", "Strategy fill rate", "Control fill rate", "Net / fill (strategy)", "Net / fill (controls)",
               "Per-fill term (selection + exits)", "Fill-rate term", "Data flags (missing bars etc.)"], rows)
    out += ["", "## Stability by calendar year (paired mean, locked arm)", ""]
    rows = []
    for sid in ("S1", "S2", "S3"):
        st = m["strategies"][sid]
        for seg, br in (("PRESAMPLE", (st["primary"].get("breakdowns") or {}).get("year") or {}),
                        ("DEV", (st.get("dev_breakdowns") or {}).get("year") or {})):
            cells = ", ".join(f"{y}: {pct(v.get('mean_paired'), 2)} (n={v.get('n')}{'*' if v.get('small_sample') else ''})"
                              for y, v in sorted(br.items()))
            rows.append([sid, seg, cells or "—"])
    out += _t(["", "Segment", "Per year (n < 30 marked *)"], rows)
    out += ["", "DEV years are contaminated diagnostics (the design data), shown only for stability context."]
    out += ["", "## Sample size, power and concentration (PRESAMPLE)", ""]
    rows = []
    for sid in ("S1", "S2", "S3", "S5"):
        pr = m["strategies"][sid]["primary"]
        ic, pw = pr.get("independent_counts") or {}, pr.get("power") or {}
        blocks = ic.get("non_empty_blocks") or {}
        sc = (pr.get("describe") or {}).get("sector_concentration") or {}
        rows.append([sid, str(ic.get("distinct_sessions", pr.get("describe", {}).get("sessions", "—"))),
                     " / ".join(str(blocks.get(k, "—")) for k in ("L20", "L40", "L60")) if blocks else
                     " / ".join(str((pr["test"].get(k) or {}).get("nonempty_blocks", "—")) for k in ("L20", "L40")),
                     str(ic.get("sectors", "—")), num(sc.get("hhi"), 3), pct(sc.get("top_share"), 1),
                     num(pw.get("mde_presample_realised"), 5), num(pw.get("economic_minimum"), 5),
                     "yes" if pw.get("underpowered_for_economic_minimum") else "no"])
    out += _t(["", "Distinct sessions", "Blocks L20 / L40 / L60", "Sectors", "Sector HHI", "Top sector", "MDE",
               "Economic min.", "Underpowered"], rows)
    out += ["", "MDE = (z(1 − 0.05/4) + z(0.8)) × the largest realised PRESAMPLE SE (per signal; S5 per day). "
            "Underpowered means a true edge at the economic minimum would most likely read INCONCLUSIVE.", "",
            "## Capital-constrained books (10 slots, 30% sector cap, 25 bps; descriptive)", ""]
    rows = []
    for sid in ("S1", "S2", "S3"):
        for seg in ("PRESAMPLE", "DEV"):
            bk = (m["strategies"][sid].get("books") or {}).get(seg) or {}
            act = bk.get("daily_active_vs_control_books") or {}
            rows.append([sid, seg, str(bk.get("trades_taken", 0)), pct(bk.get("total_return"), 2),
                         pct(bk.get("max_drawdown"), 2), pct(bk.get("avg_exposure"), 1), num(bk.get("turnover"), 1),
                         num(bk.get("sharpe_annualised"), 2), pct(bk.get("exposure_matched_spy_total_return"), 2),
                         num(bk.get("information_ratio_vs_exposure_matched_spy"), 2),
                         pct(bk.get("control_mean_total_return"), 2), _p(act.get("p_one_sided_max"))])
    out += _t(["", "Segment", "Trades", "Total return", "Max DD", "Avg exposure", "Turnover", "Sharpe",
               "Exp.-matched SPY", "IR vs SPY/cash", "Control books", "p vs controls"], rows)
    s5p = m["strategies"]["S5"]["primary"].get("describe") or {}
    grid = s5p.get("cost_grid") or {}
    out += ["", "## S5 sector-ETF allocation (PRESAMPLE, selected arm)", "",
            f"* Mean daily net {num(s5p.get('mean_daily_net'), 6)}; active vs exposure-matched SPY/cash "
            f"{num(s5p.get('mean_daily_active_vs_exposure_matched_spy'), 6)}; total {pct(s5p.get('total_return'), 2)} vs "
            f"{pct(s5p.get('exposure_matched_spy_total_return'), 2)} (SPY 100%: {pct(s5p.get('spy_total_return'), 2)})",
            f"* Max drawdown {pct(s5p.get('max_drawdown'), 2)} (SPY {pct(s5p.get('spy_max_drawdown'), 2)}); average "
            f"exposure {pct(s5p.get('avg_exposure'), 1)}; Sharpe {num(s5p.get('sharpe_annualised'), 2)}; IR "
            f"{num(s5p.get('information_ratio_vs_exposure_matched_spy'), 2)}",
            "* Mean daily active by cost: " + ", ".join(f"{k} {num((v or {}).get('mean_daily_active'), 6)}"
                                                        for k, v in grid.items()),
            f"* Invested sessions by regime: {s5p.get('invested_sessions_by_regime')}", ""]
    out += ["## Descriptive baselines (same engine; not registered hypotheses)", ""]
    rows = []
    for name in ("momentum_20", "breakout_basic"):
        for seg in ("PRESAMPLE", "DEV"):
            d = (m.get("baselines") or {}).get(name, {}).get(seg) or {}
            rows.append([name, seg, str(d.get("signals", 0)), pct(d.get("mean_per_signal_net"), 3),
                         pct(d.get("control_mean_per_signal_net"), 3), pct(d.get("mean_paired"), 3)])
    pl = ((m.get("engine_self_checks") or {}).get("placebo") or {}).get("describe") or {}
    rows.append(["matched-random (placebo: one random eligible member per session)", "PRESAMPLE",
                 str(pl.get("signals", 0)), pct(pl.get("mean_per_signal_net"), 3),
                 pct(pl.get("control_mean_per_signal_net"), 3), pct(pl.get("mean_paired"), 3)])
    out += _t(["Baseline", "Segment", "Signals", "Per-signal net", "Control", "Paired mean"], rows)
    out += ["", "Every strategy's matched control is itself a matched-random selection (same session, sector and "
            "volatility stratum, same stop geometry), so the 'Control' columns are the matched-random benchmark."]
    out += ["", "## Legacy comparators (same engine; own Holm family)", ""]
    rows = []
    for sid, v in m["legacy"].items():
        ps = v.get("PRESAMPLE") or {}
        s = (ps.get("test") or {}).get("summary") or {}
        rows.append([sid, str(ps.get("signals", "—")), pct(ps.get("mean_per_signal_net"), 3), pct(s.get("mean"), 3),
                     _p(s.get("p_one_sided_max")), _p(v.get("holm_adjusted_p_legacy_family")), v["disposition"]])
    out += _t(["Rule", "Signals", "Per-signal net", "Paired mean", "p (cons.)", "Holm p", "Disposition"], rows)
    out += ["", "## Engine self-checks", ""]
    sc = m["engine_self_checks"]
    out += [f"* Look-ahead trap (cheats by construction): mean {pct(sc['lookahead_trap']['test'].get('mean'), 3)}, "
            f"p {_p(sc['lookahead_trap']['test'].get('p_one_sided_max'))} → detected: **{sc['lookahead_trap']['detected']}**",
            f"* Placebo (random member): mean {pct(sc['placebo']['test'].get('mean'), 3)}, "
            f"p {_p(sc['placebo']['test'].get('p_one_sided_max'))}; warning: {sc['placebo']['warning']}",
            "", "## Limitations", ""] + [f"* {w}" for w in m.get("bias_warnings", [])]
    return "\n".join(out) + "\n"


ACTION = {
    "RULE_VALIDATED_RESEARCH_OOS": "KEEP (research) — run the registered forward shadow before any live consideration",
    "RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD": "KEEP AS CANDIDATE — forward shadow; no alerts",
    "UNCERTAINTY_TOO_HIGH": "REVISE — resolve the intraday ambiguity (minute bars on the ambiguous subset) before re-testing",
    "FAILED_WITH_POWER": "RETIRE — powered evidence against a worthwhile edge",
    "INCONCLUSIVE": "EXTEND DATA — do not use; more independent clusters (forward shadow) are needed",
    "NOT_EVALUABLE_MISSING_INPUT": "BLOCKED — needs a verified point-in-time event source",
    "RESEARCH_ENGINE_INVALID": "BLOCKED — fix the engine; no strategy conclusion",
}


def recommendations_md_v2(m: dict) -> str:
    out = [f"# Recommendations — `{m['experiment_id']}`", ""]
    if m["data_class"] != "REAL_MARKET_DATA":
        out += [DEMO_BANNER, ""]
    if m.get("run_status") != "COMPLETED":
        return "\n".join([*out, f"Run status {m.get('run_status')}: nothing to recommend.", ""]) + "\n"
    out += [f"**Aggregate: {m['aggregate']['conclusion']}.** No live alerts, promotion or orders under any outcome.", ""]
    for sid in ("S1", "S2", "S3", "S4", "S5"):
        st = m["strategies"][sid]
        out.append(f"* **{sid} — {st['title']}**: {st['disposition']} → {ACTION.get(st['disposition'], 'REVIEW')}")
    out += ["", "Legacy comparators:", ""]
    for sid, v in m["legacy"].items():
        out.append(f"* {sid}: {v['disposition']} → {ACTION.get(v['disposition'], 'REVIEW')}")
    out += ["", "Next experiments (each needs its own registration):", "",
            f"* Forward shadow from {PROTOCOL_V2['chronology']['forward_shadow']['decisions_from']} under this protocol; "
            f"earliest evaluation {PROTOCOL_V2['chronology']['forward_shadow']['earliest_evaluation']}.",
            "* A licensed point-in-time membership source (U2) would remove the survivorship limitation.",
            "* A verified first-publication earnings source would make S4 evaluable.", ""]
    return "\n".join(out) + "\n"
