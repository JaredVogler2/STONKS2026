"""Static security/budget invariants of .github/workflows/apex-forward-shadow.yml (actionlint runs in CI separately)."""

import re

import pytest

from apex_worker.release.budget import PROFILE
from apex_worker.release.schedule import FORWARD_SHADOW, FORWARD_SHADOW_RETRY, FORWARD_SHADOW_WEEKEND

yaml = pytest.importorskip("yaml")

from test_release_workflows import SHA_PIN, WF_DIR, load, triggers  # noqa: E402

NAME = "apex-forward-shadow.yml"


@pytest.fixture(scope="module")
def wf():
    return load(NAME)


@pytest.fixture(scope="module")
def raw():
    return (WF_DIR / NAME).read_text()


def test_schedules_and_dispatch_only(wf):
    on = triggers(wf)
    assert set(on) == {"schedule", "workflow_dispatch"}  # never pull_request / push / pull_request_target
    crons = on["schedule"]
    assert len(crons) == 3 and all(c["timezone"] == "America/New_York" for c in crons)
    for c, sched, days in zip(crons, (FORWARD_SHADOW, FORWARD_SHADOW_RETRY, FORWARD_SHADOW_WEEKEND), ("1-5", "1-5", "6"),
                              strict=True):
        minute, hour, dom, month, dow = c["cron"].split()
        assert (int(minute), int(hour)) == (sched.at.minute, sched.at.hour) and int(minute) not in (0, 30)
        assert (dom, month, dow) == ("*", "*", days)
        lo, _, hi = days.partition("-")
        assert {(d - 1) % 7 for d in range(int(lo), int(hi or lo) + 1)} == set(sched.weekdays)
    assert set(on["workflow_dispatch"]["inputs"]) == {"reason"}  # no ref, source, store or path inputs


def test_least_privilege_token_and_trusted_branch(wf, raw):
    assert wf["permissions"] == {"contents": "read"}
    (job,) = wf["jobs"].values()
    assert job["permissions"] == {"contents": "read", "issues": "write"}  # no contents:write, id-token or PR write
    assert "default_branch" in job["if"] and job["environment"] == "real-equities-research"
    assert job["timeout-minutes"] == 12 and job["runs-on"] == "ubuntu-24.04"
    assert wf["concurrency"] == {"group": "apex-forward-shadow", "cancel-in-progress": False}
    for bad in ("pull_request_target", "write-all", "git push", "gh pr", "contents: write", "id-token"):
        assert bad not in raw


def test_secrets_only_in_the_recorder_step_and_never_echoed(wf, raw):
    (job,) = wf["jobs"].values()
    holders = [s for s in job["steps"] if "secrets." in str(s) or "github.token" in str(s)]
    assert [s.get("id") for s in holders] == ["record"]
    env = holders[0]["env"]
    assert env == {"ALPACA_KEY_ID": "${{ secrets.ALPACA_API_KEY_ID }}",
                   "ALPACA_SECRET_KEY": "${{ secrets.ALPACA_API_SECRET_KEY }}", "GITHUB_TOKEN": "${{ github.token }}"}
    assert set(re.findall(r"secrets\.([A-Z_]+)", raw)) == {"ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"}
    assert "--store github --source alpaca" in holders[0]["run"] and "echo $ALPACA" not in raw


def test_actions_pinned_and_budget_profile(wf):
    (job,) = wf["jobs"].values()
    for step in job["steps"]:
        if "uses" in step:
            assert SHA_PIN.match(step["uses"]), step["uses"]
    prof = {p.key: p for p in PROFILE}
    for key in ("forward_shadow", "forward_shadow_retry", "forward_shadow_weekend"):
        assert prof[key].workflow_file == NAME and prof[key].timeout_minutes == job["timeout-minutes"]
    rec = next(s for s in job["steps"] if s.get("id") == "record")
    assert rec["timeout-minutes"] < job["timeout-minutes"]  # leave time for the always() summary/outbox/report steps


def test_artifact_and_cache_hold_no_market_data(wf):
    (job,) = wf["jobs"].values()
    up = next(s for s in job["steps"] if "upload-artifact" in s.get("uses", ""))
    paths = [p.strip() for p in up["with"]["path"].splitlines() if p.strip()]
    assert [p.rsplit("/", 1)[-1] for p in paths] == ["report.json", "summary.md", "outbox.json"]
    assert up["with"]["retention-days"] <= 30
    caches = [s for s in job["steps"] if "actions/cache" in s.get("uses", "")]
    assert len(caches) == 2 and all(s["with"]["path"].endswith("/outbox.json") for s in caches)


def test_ci_runs_the_demo_smoke_without_secrets():
    ci = load("ci.yml")
    job = ci["jobs"]["forward-shadow-smoke"]
    assert "secrets." not in str(job) and "environment" not in job and isinstance(job["timeout-minutes"], int)
    assert any("forward-shadow smoke" in str(s.get("run", "")) for s in job["steps"])
