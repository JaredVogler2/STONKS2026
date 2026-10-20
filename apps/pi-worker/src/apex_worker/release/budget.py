"""GitHub Actions minutes budget gate for the APEX model workflows (`apex budget check`).

Forecast for the current calendar month (UTC):

    max_possible_month = sum(job timeout-minutes x scheduled runs this month)        (worst case, never the average)
    worst_case         = measured_minutes_so_far + this job's timeout + remaining scheduled worst case

* REQUIRED jobs (weekly train, and its optional nested auto-release) proceed while `measured + own timeout <= budget`:
  one more run can never push the month past the configured budget, even if it runs to its timeout.
* OPTIONAL jobs (weekday drift) proceed only if `measured + own timeout + every remaining required run at its timeout
  <= budget`, the owner left `drift_enabled` on, and measured usage is actually known (fail-safe: unknown => skip).
* Exit code EXIT_SKIP (3) tells the workflow to skip the work and report `skipped_budget`; 0 = proceed.

This is an APPLICATION-LEVEL forecast. It cannot see minutes used by other repositories, by ci.yml, or by GitHub
features outside APEX, and GitHub's billing cycle may not align with the calendar month. It is NOT a billing safeguard:
the only hard stop is a GitHub budget with "Stop usage when budget limit is reached" (see docs/AUTONOMOUS_TRAINING.md).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from apex_worker.release.schedule import WEEKDAY_DRIFT, WEEKLY_TRAIN, Schedule, remaining_runs_in_month, runs_in_month

INCLUDED_FREE_MINUTES = 2000  # GitHub Free, private repos, standard runners (verify at deployment; plan-wide, shared)
DEFAULT_BUDGET_MINUTES = 1500  # leaves 500 of the free 2,000 for ci.yml, coding-agent CI and other repositories
EXIT_PROCEED = 0
EXIT_SKIP = 3


@dataclass(frozen=True)
class JobProfile:
    key: str  # train | release | drift
    workflow_file: str
    job_id: str
    timeout_minutes: int  # MUST equal the job's timeout-minutes in the workflow file (asserted by tests)
    schedule: Schedule
    optional: bool
    auto_only: bool = False  # only runs when the server's promotion_mode is "auto"


PROFILE: tuple[JobProfile, ...] = (
    JobProfile("train", "apex-model-train-weekly.yml", "train", 45, WEEKLY_TRAIN, optional=False),
    JobProfile("release", "apex-model-release.yml", "release", 10, WEEKLY_TRAIN, optional=False, auto_only=True),
    JobProfile("drift", "apex-model-drift.yml", "drift", 10, WEEKDAY_DRIFT, optional=True),
)


@dataclass
class BudgetDecision:
    job: str
    proceed: bool
    exit_code: int
    reason: str
    budget_minutes: int
    measured_minutes: float | None
    measured_runs: int | None
    measured_source: str
    max_possible_month: int
    remaining_required_worst_case: int
    remaining_optional_worst_case: int
    worst_case_month: float | None
    headroom_minutes: float | None
    drift_enabled: bool
    promotion_mode: str
    asof: str

    def to_dict(self) -> dict:
        return asdict(self)


def _active(profiles: tuple[JobProfile, ...], promotion_mode: str) -> list[JobProfile]:
    return [p for p in profiles if not (p.auto_only and promotion_mode != "auto")]


def max_possible_month(now: datetime, promotion_mode: str = "manual", profiles: tuple[JobProfile, ...] = PROFILE) -> int:
    return sum(p.timeout_minutes * runs_in_month(p.schedule, now) for p in _active(profiles, promotion_mode))


def decide(
    job: str,
    now: datetime,
    *,
    budget_minutes: int = DEFAULT_BUDGET_MINUTES,
    measured_minutes: float | None = None,
    measured_runs: int | None = None,
    measured_source: str = "unknown",
    drift_enabled: bool = True,
    promotion_mode: str = "manual",
    profiles: tuple[JobProfile, ...] = PROFILE,
) -> BudgetDecision:
    by_key = {p.key: p for p in profiles}
    if job not in by_key:
        raise ValueError(f"unknown job {job!r}; expected one of {sorted(by_key)}")
    me = by_key[job]
    active = _active(profiles, promotion_mode)
    rem_req = sum(p.timeout_minutes * remaining_runs_in_month(p.schedule, now) for p in active if not p.optional)
    rem_opt = sum(p.timeout_minutes * remaining_runs_in_month(p.schedule, now) for p in active if p.optional)
    used = None if measured_minutes is None else max(0.0, float(measured_minutes))
    worst = None if used is None else used + me.timeout_minutes + rem_req + rem_opt
    headroom = None if used is None else budget_minutes - used

    def out(proceed: bool, reason: str) -> BudgetDecision:
        return BudgetDecision(
            job=job,
            proceed=proceed,
            exit_code=EXIT_PROCEED if proceed else EXIT_SKIP,
            reason=reason,
            budget_minutes=budget_minutes,
            measured_minutes=None if used is None else round(used, 2),
            measured_runs=measured_runs,
            measured_source=measured_source,
            max_possible_month=max_possible_month(now, promotion_mode, profiles),
            remaining_required_worst_case=rem_req,
            remaining_optional_worst_case=rem_opt,
            worst_case_month=None if worst is None else round(worst, 2),
            headroom_minutes=None if headroom is None else round(headroom, 2),
            drift_enabled=drift_enabled,
            promotion_mode=promotion_mode,
            asof=now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    if budget_minutes <= 0:
        return out(False, "monthly Actions budget is 0: all APEX model workflows are paused")
    if me.optional:
        if not drift_enabled:
            return out(False, "drift diagnostics disabled by the owner (budgets.drift_enabled = false)")
        if used is None:
            return out(False, "measured minutes unknown (release API unreachable): optional job skipped fail-safe")
        need = used + me.timeout_minutes + rem_req
        if need > budget_minutes:
            return out(
                False,
                f"budget low: {used:.0f} used + {me.timeout_minutes} this run + {rem_req} reserved for remaining "
                f"required runs = {need:.0f} > {budget_minutes}",
            )
        return out(True, f"ok: worst case {need:.0f} of {budget_minutes} min with required runs reserved")
    if used is None:
        return out(True, "measured minutes unknown: required job proceeds, bounded by its own timeout")
    if used + me.timeout_minutes > budget_minutes:
        return out(
            False,
            f"budget exhausted: {used:.0f} used + {me.timeout_minutes} worst case for this run > {budget_minutes}",
        )
    return out(True, f"ok: {used:.0f} used + {me.timeout_minutes} worst case <= {budget_minutes}")


def minutes_between(started_at: datetime, finished_at: datetime) -> int:
    """GitHub bills each job rounded UP to the whole minute; a step-measured estimate of one job."""
    return max(1, math.ceil((finished_at - started_at).total_seconds() / 60))
