"""Forward-shadow health: what has actually been recorded, anchored, missed and matured. Counts only.

Outcome VALUES are never summarised here: the ledger holds them, and the registered look is the first time they are
aggregated (interim looks would be descriptive at best and invite peeking). `status`:
* SETUP_REQUIRED   no genesis record exists (the recorder has never run successfully);
* INTEGRITY_FAILURE the chain or a stored record does not verify;
* RUNNING          one of the last 3 closed windows has a DECISION whose anchor GitHub created before the deadline;
* STALE            otherwise (the recorder ran once but is not capturing sessions on time).
"""

from __future__ import annotations

from datetime import date, datetime

from apex_worker.market.calendar import MarketCalendar
from apex_worker.research.forward import windows as W
from apex_worker.research.forward.records import ATTEMPT_FAILED, DECISION, GENESIS, MISSED, OUTCOME

REGISTERED_STATUS_CHECK = "2027-04-30"  # PROTOCOL_V2.chronology.forward_shadow.earliest_evaluation (status/integrity)
FIRST_POWERED_LOOK = "about late October 2028 (480 decision sessions with matured labels; FORWARD_SHADOW_PLAN.md)"
RUNNER_MINUTES_PER_RUN = {"decision_run_estimate": 4, "no_op_run_estimate": 1}


def health(records: list[dict], anchors: dict, cal: MarketCalendar, now: datetime, decisions_from: date,
           problems: list[str] | None = None) -> dict:
    problems = problems or []
    by_type: dict[str, list[dict]] = {}
    for r in records:
        by_type.setdefault(r["record_type"], []).append(r)
    genesis = (by_type.get(GENESIS) or [None])[0]
    windows = W.due_windows(cal, decisions_from, now)
    closed = [w for w in windows if w.state(now) == "CLOSED"]
    open_now = [w for w in windows if w.state(now) == "OPEN"]
    on_time, late = [], []
    for r in by_type.get(DECISION, []):
        a = anchors.get(r["digest"])
        dl = W.window(cal, date.fromisoformat(r["session"])).deadline_utc
        (on_time if a is not None and W.utc(a.created_at) < dl else late).append(r["session"])
    missed = by_type.get(MISSED, [])
    pre_genesis = [r["session"] for r in missed if r["body"].get("reason") == "BEFORE_SERIES_GENESIS"]
    missed_real = [r["session"] for r in missed if r["body"].get("reason") != "BEFORE_SERIES_GENESIS"]
    outcomes = {r["session"] for r in by_type.get(OUTCOME, [])}
    pending = sorted(r["session"] for r in by_type.get(DECISION, []) if r["session"] not in outcomes)
    terminal = {r["session"] for r in [*by_type.get(DECISION, []), *missed]}
    unrecorded_closed = [w.session.isoformat() for w in closed if w.session.isoformat() not in terminal]
    recent = [w.session.isoformat() for w in closed[-3:]]
    if genesis is None:
        status = "SETUP_REQUIRED"
    elif problems:
        status = "INTEGRITY_FAILURE"
    elif any(s in on_time for s in recent) or (not closed and genesis is not None):
        status = "RUNNING"
    else:
        status = "STALE"
    planned: dict[str, int] = {}
    for r in by_type.get(DECISION, []):
        for k, a in r["body"].get("arms", {}).items():
            planned[k] = planned.get(k, 0) + int((a.get("funnel") or {}).get("planned", 0) or 0)
    head = records[-1] if records else None
    return {
        "status": status, "series_started_at": genesis["observed_at"] if genesis else None,
        "decisions_from": decisions_from.isoformat(), "asof": W.iso(now),
        "expected_sessions_closed": len(closed), "window_open_now": [w.session.isoformat() for w in open_now],
        "decisions_on_time": len(on_time), "decisions_anchored_late": len(late), "late_sessions": late[-10:],
        "missed": len(missed_real), "missed_sessions": missed_real[-10:], "missed_before_genesis": len(pre_genesis),
        "attempts_failed": len(by_type.get(ATTEMPT_FAILED, [])), "unrecorded_closed_windows": unrecorded_closed,
        "last_decision_session": max(on_time + late) if on_time or late else None,
        "outcomes_recorded": len(outcomes), "outcomes_pending": len(pending),
        "next_maturity_session": W.maturity_session(cal, date.fromisoformat(pending[0])).isoformat() if pending else None,
        "planned_signals_by_arm": planned, "head": {"seq": head["seq"], "digest": head["digest"]} if head else None,
        "chain_problems": problems[:10], "edited_comments": sum(1 for a in anchors.values() if a.edited),
        "registered_status_check": REGISTERED_STATUS_CHECK, "first_powered_look": FIRST_POWERED_LOOK,
        "outcome_values": "recorded in the ledger, deliberately not summarised before the registered look",
        "trading": "research only: no orders, no alerts, no group-actionable output",
    }


def markdown(h: dict, report: dict | None = None) -> str:
    rep = report or {}
    lines = [f"## APEX forward shadow — {h['status']}", "",
             "Research only: no orders, no alerts, no group-actionable output.", "",
             "| | |", "|---|---|",
             f"| Series started | {h['series_started_at'] or '—'} (decisions from {h['decisions_from']}) |",
             f"| Sessions with closed windows | {h['expected_sessions_closed']} |",
             f"| Decisions anchored on time / late | {h['decisions_on_time']} / {h['decisions_anchored_late']} |",
             f"| Missed (after genesis) / before genesis | {h['missed']} / {h['missed_before_genesis']} |",
             f"| Failed attempts recorded | {h['attempts_failed']} |",
             f"| Outcomes recorded / pending | {h['outcomes_recorded']} / {h['outcomes_pending']} |",
             f"| Next maturity | {h['next_maturity_session'] or '—'} |",
             f"| Chain head | {('seq ' + str(h['head']['seq']) + ' · ' + h['head']['digest'][:16] + '…') if h['head'] else '—'} |",
             f"| Registered status check / first powered look | {h['registered_status_check']} / {h['first_powered_look']} |"]
    if rep:
        lines += ["", f"Run status **{rep.get('status')}** (exit {rep.get('exit_code')}); provider requests "
                  f"{rep.get('provider_requests', 0)}; GitHub API requests {rep.get('github_requests', 0)}."]
        for a in rep.get("actions", []):
            lines.append(f"- {a['record_type']} {a.get('session') or ''} seq {a['seq']} → anchored "
                         f"{a.get('anchored_at')} {a.get('anchor_url') or ''}")
        for m in rep.get("messages", []):
            lines.append(f"- {m}")
        if rep.get("owner_setup"):
            lines += ["", "### OWNER_SETUP_REQUIRED", *[f"1. {s}" for s in rep["owner_setup"]]]
    if h["chain_problems"]:
        lines += ["", "### Chain problems", *[f"- {p}" for p in h["chain_problems"]]]
    return "\n".join(lines) + "\n"
