"""Actions-minutes budget gate and schedule/SLA arithmetic (DST-aware, America/New_York wall clock)."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from apex_worker.release import cli as rcli
from apex_worker.release.budget import EXIT_SKIP, PROFILE, decide, max_possible_month, minutes_between
from apex_worker.release.schedule import (
    EVIDENCE_TOURNAMENT,
    EVIDENCE_TOURNAMENT_RETRY,
    FORWARD_SHADOW,
    FORWARD_SHADOW_RETRY,
    FORWARD_SHADOW_WEEKEND,
    WEEKDAY_DRIFT,
    WEEKLY_TRAIN,
    remaining_runs_in_month,
    runs_in_month,
    sla_status,
)


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def test_weekly_train_fires_1917_new_york_across_dst():
    # 2026-10-26 is EDT (UTC-4); DST ends 2026-11-01, so 2026-11-02 is EST (UTC-5)
    hits = WEEKLY_TRAIN.occurrences(utc(2026, 10, 25), utc(2026, 11, 4))
    assert hits == [utc(2026, 10, 26, 23, 17), utc(2026, 11, 3, 0, 17)]
    # spring forward 2027-03-14: Monday 2027-03-15 19:17 EDT = 23:17 UTC
    assert WEEKLY_TRAIN.next(utc(2027, 3, 14, 12)) == utc(2027, 3, 15, 23, 17)


def test_drift_weekday_runs_and_month_counts():
    hits = WEEKDAY_DRIFT.occurrences(utc(2026, 10, 19), utc(2026, 10, 26))
    assert len(hits) == 5 and hits[0] == utc(2026, 10, 20, 0, 23)  # Monday 20:23 EDT = 00:23 UTC Tuesday
    assert runs_in_month(WEEKLY_TRAIN, utc(2026, 10, 15)) == 4  # Mondays in October 2026
    # UTC billing-month accounting: Monday 2026-11-30 19:17 EST is 2026-12-01 00:17 UTC, so it counts in December
    assert runs_in_month(WEEKLY_TRAIN, utc(2026, 11, 15)) == 4
    assert runs_in_month(WEEKLY_TRAIN, utc(2026, 12, 15)) == 5  # Dec 1 00:17 UTC + Dec 7, 14, 21, 28
    assert runs_in_month(WEEKLY_TRAIN, utc(2027, 3, 10)) == 5  # Mar 2 00:17 UTC (EST) ... Mar 29 23:17 UTC (EDT)
    assert remaining_runs_in_month(WEEKLY_TRAIN, utc(2026, 11, 20)) == 1


def test_max_possible_month_is_timeout_times_frequency():
    now = utc(2026, 11, 10)
    train = 45 * runs_in_month(WEEKLY_TRAIN, now)
    drift = 10 * runs_in_month(WEEKDAY_DRIFT, now)
    forward = 12 * sum(runs_in_month(s, now) for s in (FORWARD_SHADOW, FORWARD_SHADOW_RETRY, FORWARD_SHADOW_WEEKEND))
    tournament = 8 * sum(runs_in_month(s, now) for s in (EVIDENCE_TOURNAMENT, EVIDENCE_TOURNAMENT_RETRY))
    assert max_possible_month(now) == train + drift + forward + tournament
    assert max_possible_month(now, "auto") == train + drift + forward + tournament + 10 * runs_in_month(WEEKLY_TRAIN, now)
    # the default budget covers the worst case of every APEX workflow, in every month of the next two years
    worst = max(max_possible_month(utc(2026 + (11 + i) // 12, (11 + i) % 12 + 1, 15), "auto") for i in range(24))
    assert max_possible_month(now, "auto") <= worst < 1500


def test_required_job_proceeds_until_its_own_timeout_would_exceed_budget():
    now = utc(2026, 10, 26, 23, 0)
    assert decide("train", now, budget_minutes=1500, measured_minutes=1455).proceed
    d = decide("train", now, budget_minutes=1500, measured_minutes=1456)
    assert not d.proceed and d.exit_code == EXIT_SKIP and "exhausted" in d.reason
    assert decide("train", now, measured_minutes=None).proceed  # unknown usage: bounded by its timeout
    assert not decide("train", now, budget_minutes=0, measured_minutes=0).proceed


def test_optional_drift_reserves_remaining_required_runs_and_fails_safe():
    now = utc(2026, 11, 2, 12)  # remaining train slots this UTC month: Nov 3, 10, 17, 24 (00:17 UTC) -> 4 x 45 = 180
    fwd = 12 * sum(remaining_runs_in_month(s, now) for s in (FORWARD_SHADOW, FORWARD_SHADOW_RETRY, FORWARD_SHADOW_WEEKEND))
    # 21 weekday evenings Mon 2 Nov .. Mon 30 Nov at 18:12 ET; the 21:52 ET retry of 30 Nov is 02:52 UTC on 1 Dec;
    # Saturdays 7, 14, 21, 28 Nov at 12:12 ET
    assert fwd == 12 * (21 + 20 + 4)
    # the evidence tournament: 21 weekday evenings at 18:42 ET; the 22:22 ET retry of 30 Nov is 03:22 UTC on 1 Dec
    tour = 8 * sum(remaining_runs_in_month(s, now) for s in (EVIDENCE_TOURNAMENT, EVIDENCE_TOURNAMENT_RETRY))
    assert tour == 8 * (21 + 20)
    edge = 1500 - 10 - 180 - fwd - tour
    d = decide("drift", now, budget_minutes=1500, measured_minutes=edge)
    assert d.proceed and d.remaining_required_worst_case == 180 + fwd + tour  # prospective lanes reserved before drift
    low = decide("drift", now, budget_minutes=1500, measured_minutes=edge + 1)
    assert not low.proceed and "budget low" in low.reason
    assert not decide("drift", now, measured_minutes=None).proceed  # unknown => skip optional work
    off = decide("drift", now, measured_minutes=0, drift_enabled=False)
    assert not off.proceed and "disabled" in off.reason


def test_minutes_round_up_per_job():
    t = utc(2026, 10, 20, 20)
    assert minutes_between(t, t + timedelta(seconds=61)) == 2
    assert minutes_between(t, t) == 1


def test_sla_status_missed_and_grace():
    last_ok = utc(2026, 10, 26, 23, 40)  # the Monday 19:17 ET run reported
    assert not sla_status(WEEKLY_TRAIN, last_ok, utc(2026, 10, 28))["missed"]
    # Monday 2026-11-02 slot (00:17 UTC Tuesday) never reported; grace is 24 h
    assert not sla_status(WEEKLY_TRAIN, last_ok, utc(2026, 11, 3, 12))["missed"]
    s = sla_status(WEEKLY_TRAIN, last_ok, utc(2026, 11, 4, 1))
    assert s["missed"] and s["expected_at"] == "2026-11-03T00:17:00Z" and s["overdue_minutes"] > 0
    assert sla_status(WEEKDAY_DRIFT, None, utc(2026, 10, 21, 12))["missed"]
    with pytest.raises(ValueError):
        sla_status(WEEKLY_TRAIN, None, datetime(2026, 10, 21))


def test_profile_matches_schedules():
    keys = {p.key: p for p in PROFILE}
    assert keys["train"].schedule is WEEKLY_TRAIN and keys["drift"].schedule is WEEKDAY_DRIFT
    assert keys["drift"].optional and not keys["train"].optional and keys["release"].auto_only
    assert keys["forward_shadow"].schedule is FORWARD_SHADOW and not keys["forward_shadow"].optional
    assert keys["forward_shadow_retry"].schedule is FORWARD_SHADOW_RETRY and not keys["forward_shadow_retry"].optional
    assert keys["forward_shadow_weekend"].schedule is FORWARD_SHADOW_WEEKEND
    assert keys["evidence_tournament"].schedule is EVIDENCE_TOURNAMENT and not keys["evidence_tournament"].optional
    assert keys["evidence_tournament_retry"].schedule is EVIDENCE_TOURNAMENT_RETRY


def test_budget_cli_uses_server_policy_and_exit_codes(tmp_path, monkeypatch):
    from apex_worker.cli import main

    pol = {"mode": "DEMO", "policy": {"promotion_mode": "manual"},
           "budgets": {"actions_monthly_minutes": 1500, "drift_enabled": False}, "usage_month": {"minutes_used": 10, "runs": 2}}
    (tmp_path / "policy.json").write_text(json.dumps(pol))
    monkeypatch.delenv("APEX_ACTIONS_MONTHLY_BUDGET", raising=False)
    gh = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh))
    assert main(["budget", "check", "--job", "drift", "--policy", str(tmp_path / "policy.json"), "--github-output"]) == EXIT_SKIP
    assert "proceed=false" in gh.read_text()
    assert main(["budget", "check", "--job", "train", "--policy", str(tmp_path / "policy.json"),
                 "--out", str(tmp_path / "b.json")]) == 0
    b = json.loads((tmp_path / "b.json").read_text())
    assert b["budget_source"] == "server settings" and b["measured_minutes"] == 10
    monkeypatch.setenv("APEX_ACTIONS_MONTHLY_BUDGET", "0")
    assert main(["budget", "check", "--job", "train", "--policy", str(tmp_path / "policy.json")]) == EXIT_SKIP
    monkeypatch.setenv("APEX_ACTIONS_MONTHLY_BUDGET", "lots")
    assert main(["budget", "check", "--job", "train", "--policy", str(tmp_path / "policy.json")]) == 2
    monkeypatch.delenv("APEX_ACTIONS_MONTHLY_BUDGET")
    monkeypatch.setattr(rcli, "_client", lambda: (_ for _ in ()).throw(AssertionError("must not call the API offline")))
    assert main(["budget", "check", "--job", "drift", "--offline"]) == EXIT_SKIP
