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
            f"* PRESAMPLE (untouched, earlier in time): decisions {b['presample_start']} → {b['presample_end']}",
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
    out += ["", "## DEV diagnostics per arm (CONTAMINATED; selection only)", ""]
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
        out.append(f"* {sid}: PRESAMPLE mean {pct(ps.get('mean'), 3)}, p {_p(ps.get('p_one_sided_max'))} ({ps.get('status')}); "
                   f"DEV mean {pct((mech.get('DEV') or {}).get('mean'), 3)}")
    s5 = m["strategies"]["S5"]["primary"]["describe"]
    out.append(f"* S5 vs equal-weight sectors: mean daily {num(s5.get('mean_daily_vs_equal_weight'), 6)}")
    out += ["", "## Sensitivities of the primary arm (PRESAMPLE)", ""]
    rows = []
    for sid in ("S1", "S2", "S3"):
        d = m["strategies"][sid]["primary"].get("describe") or {}
        sens = m["strategies"][sid]["primary"]["test"].get("sensitivity", {}).get("L60", {})
        rows.append([sid, *(pct(d.get(f"mean_paired_{c}bps"), 3) for c in PROTOCOL_V2["costs_bps_per_side"]),
                     pct(d.get("mean_paired_optimistic"), 3), pct(d.get("mean_paired_touch_fill_sensitivity"), 3),
                     pct(d.get("ambiguous_share_of_fills"), 1), _p(sens.get("p_one_sided"))])
    out += _t(["", "0 bps", "10 bps", "25 bps", "50 bps", "Optimistic leg", "Touch-fill", "Ambiguous fills", "p (L=60)"], rows)
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
