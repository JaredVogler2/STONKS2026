"""Training-run reports for `POST /api/v1/release/training-runs` (dashboard observability, missed-run detection).

The values mirror the `apex.training_runs` CHECK constraints so a bad report fails here with a clear message instead of a
server 500. The report body is a compact summary (well under the 256 KB route cap, JSON depth <= 6); the full
report.json/summary.md stay in the short-retention Actions artifact.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime

from apex_worker.release.budget import minutes_between

WORKFLOWS = ("train_weekly", "drift", "release", "local_replay")
TRIGGERS = ("schedule", "workflow_dispatch", "admin_dispatch", "local")
STATUSES = ("running", "succeeded", "failed", "skipped_budget", "skipped_holiday", "cancelled")
OUTCOMES = (
    "candidate_registered",
    "champion_retained",
    "promoted",
    "rejected",
    "drift_ok",
    "drift_breach",
    "no_data",
)
JOB_STATUS = {"success": "succeeded", "failure": "failed", "cancelled": "cancelled"}


def iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str) -> datetime:
    """ISO-8601 (Z or offset) or epoch seconds, as written by `date -u +%s` in the workflow's first step."""
    v = value.strip()
    if v.isdigit():
        return datetime.fromtimestamp(int(v), UTC)
    t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return t.astimezone(UTC)


def trigger_from_env(env: Mapping[str, str], requested_by: str | None = None) -> str:
    if requested_by == "admin":
        return "admin_dispatch"
    event = env.get("GITHUB_EVENT_NAME", "")
    if event in ("schedule", "workflow_dispatch"):
        return event
    return "local" if not env.get("GITHUB_ACTIONS") else "workflow_dispatch"


def normalise_status(status: str) -> str:
    s = JOB_STATUS.get(status, status)
    if s not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES} (or success/failure/cancelled)")
    return s


def compact_training(report: dict) -> dict:
    strategies = {}
    for sid, r in (report.get("reports") or {}).items():
        h = r.get("holdout") or {}
        d = (report.get("decisions") or {}).get(sid, {})
        strategies[sid] = {
            "status": r.get("confidence_status"),
            "promotable": bool(d.get("promotable")),
            "reason": str(d.get("reason", ""))[:160],
            "holdout_n": h.get("n"),
            "brier_skill": h.get("brier_skill"),
            "ece": h.get("ece"),
            "model_id": d.get("model_id"),
        }
    return {
        "status": report.get("status"),
        "data_source": report.get("data_source"),
        "data_hash": report.get("data_hash"),
        "seconds": report.get("seconds"),
        "generated_at": report.get("generated_at"),
        "strategies": strategies,
        "candidates": [
            {k: c.get(k) for k in ("strategy_id", "model_id", "promotable", "signed")} for c in report.get("candidates") or []
        ],
    }


def build_run(
    *,
    workflow: str,
    status: str,
    started_at: datetime,
    finished_at: datetime | None = None,
    outcome: str | None = None,
    trigger: str | None = None,
    candidate_model_id: str | None = None,
    budget: dict | None = None,
    is_demo: bool = False,
    report: dict | None = None,
    env: Mapping[str, str] | None = None,
) -> dict:
    env = os.environ if env is None else env
    if workflow not in WORKFLOWS:
        raise ValueError(f"workflow must be one of {WORKFLOWS}")
    status = normalise_status(status)
    if outcome is not None and outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    trig = trigger or trigger_from_env(env)
    if trig not in TRIGGERS:
        raise ValueError(f"trigger must be one of {TRIGGERS}")
    finished = finished_at or datetime.now(UTC)
    used = minutes_between(started_at, finished)
    month_used = (budget or {}).get("measured_minutes")
    run_id = env.get("GITHUB_RUN_ID") or None
    return {
        "workflow": workflow,
        "trigger": trig,
        "github_run_id": run_id,
        "github_run_attempt": int(env.get("GITHUB_RUN_ATTEMPT") or 1),
        "code_sha": (env.get("GITHUB_SHA") or "local")[:64],
        "started_at": iso(started_at),
        "finished_at": iso(finished),
        "status": status,
        "outcome": outcome,
        "candidate_model_id": candidate_model_id,
        "minutes_used": used,
        "minutes_budget_month": (budget or {}).get("budget_minutes"),
        "minutes_used_month_estimate": None if month_used is None else round(float(month_used) + used, 2),
        "is_demo": bool(is_demo),
        "report": {
            **(report or {}),
            "minutes_note": "estimate: wall clock from the job's first step, rounded up per job like GitHub billing",
            "budget": budget or None,
            "run_url": _run_url(env, run_id),
        },
    }


def _run_url(env: Mapping[str, str], run_id: str | None) -> str | None:
    server, repo = env.get("GITHUB_SERVER_URL"), env.get("GITHUB_REPOSITORY")
    if not (server and repo and run_id):
        return None
    return f"{server}/{repo}/actions/runs/{run_id}"
