"""Static invariants of .github/workflows/apex-personal.yml (actionlint runs in CI separately).

The personal alerts run on a schedule against the owner's private Discord channel. The repository is public, so the
job may read the repository and nothing else, its secrets reach one step, and nothing personal can leave through the
log, a job summary or an artifact."""

import pytest

yaml = pytest.importorskip("yaml")

from test_release_workflows import SHA_PIN, WF_DIR, load, triggers  # noqa: E402

NAME = "apex-personal.yml"


@pytest.fixture(scope="module")
def wf():
    return load(NAME)


@pytest.fixture(scope="module")
def raw():
    return (WF_DIR / NAME).read_text()


def test_schedule_in_new_york_time_and_a_dry_by_default_dispatch(wf):
    on = triggers(wf)
    assert set(on) == {"schedule", "workflow_dispatch"}
    crons = on["schedule"]
    assert crons and all(c["timezone"] == "America/New_York" for c in crons)
    assert all(c["cron"].split()[0] not in ("0", "30") and not c["cron"].startswith("*") for c in crons)
    weekday = crons[0]["cron"].split()
    assert weekday[1] == "7-20" and weekday[4] == "1-5"  # the morning reminder through the month-end evening
    mode = on["workflow_dispatch"]["inputs"]["mode"]
    assert mode["default"] == "dry" and mode["options"] == ["dry", "live"]


def test_read_only_default_branch_activation_and_its_own_concurrency(wf, raw):
    assert wf["permissions"] == {"contents": "read"}
    (job,) = wf["jobs"].values()
    assert job["permissions"] == {"contents": "read"} and job["environment"] == "personal-alerts"
    assert "default_branch" in job["if"] and "vars.APEX_PERSONAL_ALERTS == 'on'" in job["if"]
    assert "github.event_name == 'workflow_dispatch'" in job["if"]
    assert job["runs-on"] == "ubuntu-24.04" and job["timeout-minutes"] <= 10
    assert wf["concurrency"] == {"group": "apex-personal", "cancel-in-progress": False}
    for bad in ("pull_request_target", "write-all", "id-token", "upload-artifact", "GITHUB_STEP_SUMMARY", "set -x",
                "contents: write", "issues: write"):
        assert bad not in raw


def test_secrets_reach_the_run_step_only_and_schedules_post_live(wf):
    (job,) = wf["jobs"].values()
    holders = [s for s in job["steps"] if "secrets." in str(s)]
    assert len(holders) == 1
    env = holders[0]["env"]
    assert {k for k, v in env.items() if "secrets." in v} == {
        "DISCORD_BOT_TOKEN", "APEX_PERSONAL_CHANNEL_ID", "APEX_PERSONAL_USER_IDS", "ALPACA_KEY_ID", "ALPACA_SECRET_KEY"}
    assert env["MODE"] == "${{ github.event_name == 'schedule' && 'live' || inputs.mode }}"
    run = holders[0]["run"]
    assert 'apex personal run --mode "$MODE"' in run and "secrets" not in run and "echo $" not in run
    assert "${{" not in run  # inputs and secrets only through env


def test_actions_are_pinned_and_checkout_keeps_no_credentials(wf):
    (job,) = wf["jobs"].values()
    uses = [s for s in job["steps"] if "uses" in s]
    assert uses and all(SHA_PIN.match(s["uses"]) for s in uses)
    checkout = next(s for s in uses if s["uses"].startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] is False
