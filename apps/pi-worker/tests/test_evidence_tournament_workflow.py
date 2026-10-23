"""Static security/budget invariants of .github/workflows/apex-evidence-tournament.yml (actionlint runs in CI separately)."""

import re

import pytest

from apex_worker.release.budget import PROFILE
from apex_worker.release.schedule import EVIDENCE_TOURNAMENT, EVIDENCE_TOURNAMENT_RETRY

yaml = pytest.importorskip("yaml")

from test_release_workflows import SHA_PIN, WF_DIR, load, triggers  # noqa: E402

NAME = "apex-evidence-tournament.yml"


@pytest.fixture(scope="module")
def wf():
    return load(NAME)


@pytest.fixture(scope="module")
def raw():
    return (WF_DIR / NAME).read_text()


def test_schedules_and_dispatch_only(wf):
    on = triggers(wf)
    assert set(on) == {"schedule", "workflow_dispatch"}
    crons = on["schedule"]
    assert len(crons) == 2 and all(c["timezone"] == "America/New_York" for c in crons)
    for c, sched in zip(crons, (EVIDENCE_TOURNAMENT, EVIDENCE_TOURNAMENT_RETRY), strict=True):
        minute, hour, dom, month, dow = c["cron"].split()
        assert (int(minute), int(hour)) == (sched.at.minute, sched.at.hour) and int(minute) not in (0, 30)
        assert (dom, month, dow) == ("*", "*", "1-5") and set(sched.weekdays) == {0, 1, 2, 3, 4}
    assert set(on["workflow_dispatch"]["inputs"]) == {"reason"}


def test_least_privilege_token_trusted_branch_and_its_own_concurrency(wf, raw):
    assert wf["permissions"] == {"contents": "read"}
    (job,) = wf["jobs"].values()
    assert job["permissions"] == {"contents": "read", "issues": "write"}
    assert "default_branch" in job["if"] and job["environment"] == "real-equities-research"
    assert job["runs-on"] == "ubuntu-24.04"
    assert wf["concurrency"] == {"group": "apex-evidence-tournament", "cancel-in-progress": False}
    for bad in ("pull_request_target", "write-all", "git push", "gh pr", "contents: write", "id-token"):
        assert bad not in raw


def test_secrets_only_in_the_recorder_step_and_dry_until_activated(wf, raw):
    (job,) = wf["jobs"].values()
    holders = [s for s in job["steps"] if "secrets." in str(s) or "github.token" in str(s)]
    assert [s.get("id") for s in holders] == ["record"]
    assert holders[0]["env"] == {"ALPACA_KEY_ID": "${{ secrets.ALPACA_API_KEY_ID }}",
                                 "ALPACA_SECRET_KEY": "${{ secrets.ALPACA_API_SECRET_KEY }}",
                                 "GITHUB_TOKEN": "${{ github.token }}",
                                 "TOURNAMENT_LANE": "${{ vars.APEX_TOURNAMENT_LANE }}"}
    assert set(re.findall(r"secrets\.([A-Z_]+)", raw)) == {"ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"}
    run = holders[0]["run"]
    assert "store=dry" in run and 'if [ "$TOURNAMENT_LANE" = "on" ]; then store=github; fi' in run
    assert "--lane tournament" in run and "--source alpaca" in run and "echo $ALPACA" not in raw


def test_actions_pinned_and_budget_profile(wf):
    (job,) = wf["jobs"].values()
    for step in job["steps"]:
        if "uses" in step:
            assert SHA_PIN.match(step["uses"]), step["uses"]
    prof = {p.key: p for p in PROFILE}
    for key in ("evidence_tournament", "evidence_tournament_retry"):
        assert prof[key].workflow_file == NAME and prof[key].timeout_minutes == job["timeout-minutes"]
    rec = next(s for s in job["steps"] if s.get("id") == "record")
    assert rec["timeout-minutes"] < job["timeout-minutes"]


def test_artifact_and_cache_hold_no_market_data(wf):
    (job,) = wf["jobs"].values()
    up = next(s for s in job["steps"] if "upload-artifact" in s.get("uses", ""))
    paths = [p.strip() for p in up["with"]["path"].splitlines() if p.strip()]
    assert [p.rsplit("/", 1)[-1] for p in paths] == ["report.json", "summary.md", "outbox.json"]
    assert up["with"]["retention-days"] <= 30
    caches = [s for s in job["steps"] if "actions/cache" in s.get("uses", "")]
    assert len(caches) == 2 and all(s["with"]["path"].endswith("apex-tournament/outbox.json") for s in caches)
    assert all("apex-tournament-outbox-" in s["with"]["key"] for s in caches)  # never the frozen lane's outbox


def test_the_frozen_lane_workflow_is_untouched():
    fs = load("apex-forward-shadow.yml")
    assert list(fs["jobs"]) == ["record"] and "tournament" not in (WF_DIR / "apex-forward-shadow.yml").read_text()
