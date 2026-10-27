"""The readable track record of `v3x-live-picks-v1`, rendered from the verified ledger (picks/README.md on main).

`render` is a pure function of the records and their anchors: every run regenerates the whole folder, so anyone can
rebuild it from the ledger issue and compare. Files (published under picks/ on the `picks-ledger` branch, never main):
* README.md     the latest session, the open picks, the labelled track record with its controls and SPY;
* picks.csv     one row per plan (pick or qualified setup), its plan and, once labelled, its outcome;
* sessions.csv  one row per session: DECISION or MISSED_OBSERVATION, the pick, the funnel, the anchor;
* ledger.jsonl  every record verbatim (canonical JSON), and anchors.json: GitHub's timestamp and URL of each.
Plans are in units of the decision close and outcomes are returns: no prices or volumes are ever written.
"""

from __future__ import annotations

import csv
import io
import json
import statistics

from apex_worker.discovery.v3.live_picks import PICK, QUALIFIED
from apex_worker.research.forward.records import DECISION, MISSED, OUTCOME

MIN_FOR_ANY_READING = 30  # below this many labelled picks the table is shown with an explicit "too few" warning
DISCLAIMER = ("> **Research only. Not investment advice.** These are the would-be picks of the APEX live engine, which no "
              "study has validated: every real-data batch so far ended NO_TRADE (research-results/README.md on main). "
              "Nothing here was traded, posted or alerted. The record exists to measure the picks honestly before "
              "anyone relies on them.")
PLAN_COLUMNS = ["session", "kind", "symbol", "name", "sector", "strategy_id", "quality", "confidence", "earnings_status",
                "regime", "entry_min_pct", "entry_max_pct", "stop_pct", "target_pct", "reward_to_risk_net", "stop_atr",
                "outcome", "filled", "entry_session", "exit_session", "sessions_held", "net_pct", "gross_pct",
                "r_multiple", "mfe_pct", "mae_pct", "spy_pct", "sector_etf", "sector_etf_pct", "controls_n",
                "controls_mean_pct", "vs_controls_pct", "vs_spy_pct", "flags", "decision_record_id", "decision_digest",
                "outcome_record_id"]
SESSION_COLUMNS = ["session", "record_type", "picks", "qualified", "no_trade_reason", "universe", "eligible",
                   "triggered", "regime", "anchored_at", "record_id", "digest"]


def _pct(x, signed: bool = True) -> str:
    if x is None:
        return ""
    return f"{x * 100:+.2f}" if signed else f"{x * 100:.2f}"


def _rel(ratio) -> str:
    """A plan level in units of the close, as a signed percent distance from it."""
    return "" if ratio is None else f"{(ratio - 1) * 100:+.2f}"


def plan_rows(records: list[dict]) -> list[dict]:
    """One row per plan, with its outcome once labelled (both from the ledger only)."""
    outcomes = {r["session"]: r for r in records if r["record_type"] == OUTCOME}
    rows = []
    for d in (r for r in records if r["record_type"] == DECISION):
        o = outcomes.get(d["session"])
        labels = {(p["kind"], p["symbol"]): p for p in (o["body"]["plans"] if o else [])}
        for e in d["body"].get("plans") or []:
            pl, lab = e["plan"], labels.get((e["kind"], e["symbol"]), {})
            ctrl = lab.get("controls") or {}
            rows.append({
                "session": d["session"], "kind": e["kind"], "symbol": e["symbol"], "name": e.get("name") or "",
                "sector": e.get("sector") or "", "strategy_id": e["strategy_id"], "quality": e.get("quality"),
                "confidence": e.get("confidence"), "earnings_status": e.get("earnings_status"), "regime": e.get("regime"),
                "entry_min_pct": _rel(pl["entry_min"]), "entry_max_pct": _rel(pl["entry_max"]),
                "stop_pct": _rel(pl["stop"]), "target_pct": _rel(pl["target"]),
                "reward_to_risk_net": pl.get("reward_to_risk_net"), "stop_atr": pl.get("stop_atr"),
                "outcome": lab.get("outcome", "PENDING" if o is None else ""), "filled": lab.get("filled", ""),
                "entry_session": lab.get("entry_session", ""), "exit_session": lab.get("exit_session", ""),
                "sessions_held": lab.get("sessions_held", ""), "net_pct": _pct(lab.get("net")),
                "gross_pct": _pct(lab.get("gross")), "r_multiple": lab.get("r_multiple", ""),
                "mfe_pct": _pct(lab.get("mfe")), "mae_pct": _pct(lab.get("mae")), "spy_pct": _pct(lab.get("spy")),
                "sector_etf": lab.get("sector_etf") or "", "sector_etf_pct": _pct(lab.get("sector_etf_return")),
                "controls_n": ctrl.get("n", ""), "controls_mean_pct": _pct(ctrl.get("mean_net")),
                "vs_controls_pct": _pct(lab.get("vs_controls")), "vs_spy_pct": _pct(lab.get("vs_spy")),
                "flags": ";".join(lab.get("flags") or []), "decision_record_id": d["record_id"],
                "decision_digest": d["digest"], "outcome_record_id": o["record_id"] if o else "",
                "_label": lab if o else None})
    return rows


def session_rows(records: list[dict], anchors: dict) -> list[dict]:
    rows = []
    for r in records:
        if r["record_type"] not in (DECISION, MISSED):
            continue
        b, a = r["body"], anchors.get(r["digest"])
        plans = b.get("plans") or []
        scan = b.get("scan") or {}
        rows.append({"session": r["session"], "record_type": r["record_type"],
                     "picks": " ".join(e["symbol"] for e in plans if e["kind"] == PICK),
                     "qualified": " ".join(e["symbol"] for e in plans if e["kind"] == QUALIFIED),
                     "no_trade_reason": b.get("no_trade_reason") or (b.get("reason") if r["record_type"] == MISSED else "")
                     or "", "universe": scan.get("universe_size", ""), "eligible": scan.get("eligible", ""),
                     "triggered": scan.get("triggered", ""), "regime": (scan.get("regime") or {}).get("label", ""),
                     "anchored_at": getattr(a, "created_at", "") if a is not None else "", "record_id": r["record_id"],
                     "digest": r["digest"]})
    return rows


def _csv(rows: list[dict], columns: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for row in rows:
        w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in columns})
    return buf.getvalue()


def _mean(xs: list[float]) -> float | None:
    return statistics.fmean(xs) if xs else None


def stats(rows: list[dict]) -> dict:
    """Descriptive statistics of the labelled, complete plans in `rows` (no test, no claim)."""
    labs = [r["_label"] for r in rows if r["_label"] and r["_label"].get("complete")]
    filled = [x for x in labs if x.get("filled")]
    with_ctrl = [x for x in labs if x.get("vs_controls") is not None]
    with_spy = [x for x in filled if x.get("vs_spy") is not None]
    return {"labelled": len(labs), "filled": len(filled),
            "hit_rate": (sum(1 for x in filled if (x.get("net") or 0) > 0) / len(filled)) if filled else None,
            "mean_net": _mean([x["net"] for x in labs]),
            "mean_controls": _mean([x["controls"]["mean_net"] for x in with_ctrl]),
            "mean_vs_controls": _mean([x["vs_controls"] for x in with_ctrl]),
            "mean_spy": _mean([x["spy"] for x in with_spy]), "mean_vs_spy": _mean([x["vs_spy"] for x in with_spy]),
            "targets": sum(1 for x in filled if x.get("outcome") == "TARGET"),
            "stops": sum(1 for x in filled if x.get("outcome") == "STOP"),
            "time_exits": sum(1 for x in filled if x.get("outcome") == "TIME")}


def _stats_md(s: dict, what: str) -> list[str]:
    def p(x):
        return "—" if x is None else f"{x * 100:+.2f}%"

    hit = "—" if s["hit_rate"] is None else f"{s['hit_rate'] * 100:.0f}%"
    out = [f"{s['labelled']} {what} labelled, {s['filled']} filled (targets {s['targets']}, stops {s['stops']}, time "
           f"exits {s['time_exits']})."]
    if s["labelled"] < MIN_FOR_ANY_READING:
        out.append(f"**Too few to conclude anything:** fewer than {MIN_FOR_ANY_READING} labelled {what}. These numbers "
                   "are descriptive and can move a lot with each new label.")
    out += ["", "| | Value |", "|---|---|",
            f"| Mean net return per {what[:-1]} (a no-fill counts as 0) | {p(s['mean_net'])} |",
            f"| Mean of its matched controls | {p(s['mean_controls'])} |",
            f"| Mean difference vs controls | {p(s['mean_vs_controls'])} |",
            f"| Hit rate (filled, net > 0) | {hit} |",
            f"| Mean SPY over the same holds (filled) | {p(s['mean_spy'])} |",
            f"| Mean difference vs SPY (filled) | {p(s['mean_vs_spy'])} |"]
    return out


def _latest_md(d: dict | None) -> list[str]:
    if d is None:
        return ["_No decision recorded yet._"]
    b = d["body"]
    out = [f"## Latest session: {d['session']}", ""]
    picks = [e for e in b.get("plans") or [] if e["kind"] == PICK]
    for e in picks:
        pl = e["plan"]
        out += [f"**PICK: {e['symbol']}**" + (f" ({e['name']}" + (f", {e['sector']})" if e.get("sector") else ")")
                                              if e.get("name") else "")
                + f" · `{e['strategy_id']}` · quality {e['quality']:.0f} · confidence {e['confidence']} · earnings "
                f"{e.get('earnings_status')}", "",
                "| Entry zone (from the close) | Stop | Target | Entry valid | Longest hold | Net reward/risk |",
                "|---|---|---|---|---|---|",
                f"| {_rel(pl['entry_min'])}% to {_rel(pl['entry_max'])}% | {_rel(pl['stop'])}% | {_rel(pl['target'])}% | "
                f"next {pl['entry_valid_sessions']} sessions | {pl['horizon_sessions']} sessions | "
                f"{pl['reward_to_risk_net']} |", ""]
    if not picks:
        scan = b.get("scan") or {}
        out += [f"**NO PICK:** {b.get('no_trade_reason')}. Scanned {scan.get('universe_size')}, eligible "
                f"{scan.get('eligible')}, triggered {scan.get('triggered')}.", ""]
    qual = [e["symbol"] for e in b.get("plans") or [] if e["kind"] == QUALIFIED]
    if qual:
        out += [f"Also qualified (the engine would post them only if it posted more than one a session; not picks): "
                f"{', '.join(qual)}.", ""]
    return out


def _open_md(d: dict | None) -> list[str]:
    opened = [o for o in ((d or {}).get("body") or {}).get("open_picks") or [] if o["status_t"] in ("PUBLISHED", "ACTIVE")]
    if not opened:
        return ["_No open picks._"]
    out = ["| Picked | Symbol | Status | Entered | Sessions held | Return to date (preliminary) |",
           "|---|---|---|---|---|---|"]
    for o in opened:
        out.append(f"| {o['session']} | {o['symbol']} | {o['status_t']} | {o.get('entry_session') or '—'} | "
                   f"{o.get('sessions_held', '—')} | {_pct(o.get('return_to_date')) or '—'} |")
    return out


def _table_md(rows: list[dict], limit: int = 60) -> list[str]:
    done = [r for r in rows if r["_label"]][-limit:]
    if not done:
        return ["_Nothing labelled yet: a plan is labelled once its 22 sessions are final (about five weeks)._"]
    out = ["| Session | Symbol | Strategy | Quality | Outcome | Net % | Controls % | vs controls | SPY % | Sector ETF % | "
           "Held |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in reversed(done):
        out.append(f"| {r['session']} | {r['symbol']} | {r['strategy_id']} | {r['quality']:.0f} | {r['outcome']} | "
                   f"{r['net_pct']} | {r['controls_mean_pct']} | {r['vs_controls_pct']} | {r['spy_pct']} | "
                   f"{r['sector_etf_pct']} | {r['sessions_held']} |")
    return out


def render(records: list[dict], anchors: dict, *, series_id: str, ledger_url: str | None = None,
           generated_at: str | None = None) -> dict[str, str]:
    """picks/ as {relative path: text}. A pure function of the verified records and their anchors."""
    rows = plan_rows(records)
    sess = session_rows(records, anchors)
    decisions = [r for r in records if r["record_type"] == DECISION]
    last = decisions[-1] if decisions else None
    head = records[-1] if records else None
    picks = [r for r in rows if r["kind"] == PICK]
    qual = [r for r in rows if r["kind"] == QUALIFIED]
    missed = sum(1 for r in records if r["record_type"] == MISSED and r["body"].get("reason") != "BEFORE_SERIES_GENESIS")
    lines = ["# APEX live picks: track record", "", DISCLAIMER, "",
             f"Rendered from the verified ledger of series `{series_id}`"
             + (f" (head seq {head['seq']}, digest `{head['digest'][:16]}…`)" if head else "")
             + (f" at {generated_at}" if generated_at else "") + ". Every run regenerates this folder from the ledger: "
             "do not edit it." + (f" Ledger: {ledger_url}." if ledger_url else "")
             + " Method: `picks/README.md` on the main branch.", "",
             f"{len(decisions)} sessions decided ({len(picks)} picks, {len(qual)} qualified setups), {missed} missed.",
             "", *_latest_md(last), "## Open picks", "", *_open_md(last), "",
             "## Track record: picks", "", *_stats_md(stats(picks), "picks"), "", *_table_md(picks), "",
             "## Qualified setups (not picks)", "",
             "The setups the engine would also post if it posted up to five a session: a larger sample of its top "
             "setups, labelled the same way.", "", *_stats_md(stats(qual), "setups"), "", *_table_md(qual, 30), "",
             "## Files", "",
             "- `picks.csv`: every plan with its outcome (returns and plan distances in percent; no prices).",
             "- `sessions.csv`: every session, its pick or the reason for none, and GitHub's anchor time.",
             "- `ledger.jsonl` and `anchors.json`: the records verbatim and where GitHub stored each one.", "",
             "## How to verify", "",
             "Each record's sha256 digest covers its content and its predecessor's digest, so an edit, insertion, deletion "
             "or reordering breaks the chain. GitHub's comment time on the ledger issue is the independent proof that a "
             "decision existed before the next open. To check: `apex research forward-shadow verify --lane picks "
             "--store github`, or recompute the digests in `ledger.jsonl`.", ""]
    rows_out = [{k: v for k, v in r.items() if k != "_label"} for r in rows]
    return {
        "picks/README.md": "\n".join(lines),
        "picks/picks.csv": _csv(rows_out, PLAN_COLUMNS),
        "picks/sessions.csv": _csv(sess, SESSION_COLUMNS),
        "picks/ledger.jsonl": "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in records),
        "picks/anchors.json": json.dumps({d: (a.to_dict() if hasattr(a, "to_dict") else a) for d, a in anchors.items()},
                                         indent=1, sort_keys=True) + "\n",
    }


__all__ = ["DISCLAIMER", "PLAN_COLUMNS", "SESSION_COLUMNS", "plan_rows", "render", "session_rows", "stats"]
