"""Static security invariants of .github/workflows/apex-live-picks.yml (actionlint runs in CI separately).

The record job may write issue comments (the ledger) and never the repository; the publish job may write the repository
and nothing else: no secrets, no environment, no repository code or third-party package, one fast-forward push to
refs/heads/picks-ledger, never main."""

import re

import pytest

from apex_worker.release.budget import PROFILE
from apex_worker.release.schedule import LIVE_PICKS, LIVE_PICKS_RETRY

yaml = pytest.importorskip("yaml")

from test_release_workflows import SHA_PIN, WF_DIR, load, triggers  # noqa: E402

NAME = "apex-live-picks.yml"


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
    for c, sched in zip(crons, (LIVE_PICKS, LIVE_PICKS_RETRY), strict=True):
        minute, hour, dom, month, dow = c["cron"].split()
        assert (int(minute), int(hour)) == (sched.at.minute, sched.at.hour) and int(minute) not in (0, 30)
        assert (dom, month, dow) == ("*", "*", "1-5") and set(sched.weekdays) == {0, 1, 2, 3, 4}
    assert set(on["workflow_dispatch"]["inputs"]) == {"reason"}


def test_least_privilege_per_job_trusted_branch_and_its_own_concurrency(wf, raw):
    assert wf["permissions"] == {"contents": "read"}
    assert set(wf["jobs"]) == {"record", "publish"}
    rec, pub = wf["jobs"]["record"], wf["jobs"]["publish"]
    assert rec["permissions"] == {"contents": "read", "issues": "write"}
    assert "default_branch" in rec["if"] and rec["environment"] == "real-equities-research"
    assert pub["permissions"] == {"contents": "write"} and "environment" not in pub and pub["needs"] == "record"
    assert "needs.record.outputs.store == 'github'" in pub["if"] and "needs.record.outputs.view == 'true'" in pub["if"]
    assert all(j["runs-on"] == "ubuntu-24.04" and isinstance(j["timeout-minutes"], int) for j in (rec, pub))
    assert wf["concurrency"] == {"group": "apex-live-picks", "cancel-in-progress": False}
    for bad in ("pull_request_target", "write-all", "id-token", "gh pr", "refs/heads/main", "push --force", "push -f"):
        assert bad not in raw


def test_secrets_only_in_the_recorder_step_and_dry_until_activated(wf, raw):
    rec = wf["jobs"]["record"]
    holders = [s for s in rec["steps"] if "secrets." in str(s) or "github.token" in str(s)]
    assert [s.get("id") for s in holders] == ["record"]
    assert holders[0]["env"] == {"ALPACA_KEY_ID": "${{ secrets.ALPACA_API_KEY_ID }}",
                                 "ALPACA_SECRET_KEY": "${{ secrets.ALPACA_API_SECRET_KEY }}",
                                 "APEX_SEC_USER_AGENT": "${{ secrets.APEX_SEC_USER_AGENT }}",
                                 "GITHUB_TOKEN": "${{ github.token }}", "PICKS_LANE": "${{ vars.APEX_PICKS_LANE }}"}
    assert set(re.findall(r"secrets\.([A-Z_]+)", raw)) == {"ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY",
                                                           "APEX_SEC_USER_AGENT"}
    run = holders[0]["run"]
    assert "store=dry" in run and 'if [ "$PICKS_LANE" = "on" ]; then store=github; fi' in run
    assert "forward-shadow run-picks" in run and '--out-dir "$APEX_OUT"' in run and "--source alpaca" in run
    assert "echo $ALPACA" not in raw and "echo $APEX_SEC" not in raw


def test_the_publish_job_runs_no_code_and_pushes_only_the_picks_ledger_branch(wf):
    pub = wf["jobs"]["publish"]
    steps = pub["steps"]
    assert [s.get("uses", "").split("@")[0] for s in steps] == ["actions/download-artifact", ""]
    text = str(pub)
    assert "secrets." not in text and "uv " not in text and "python" not in text and "apex " not in text
    push = steps[1]
    assert push["env"] == {"GH_TOKEN": "${{ github.token }}", "REPO": "${{ github.repository }}",
                           "SERVER": "${{ github.server_url }}"}
    script = push["run"]
    assert script.count(" push ") == 1 and "--force" not in script and "+HEAD" not in script
    assert re.search(r"push -q \"\$SERVER/\$REPO\.git\" \\\n\s+HEAD:refs/heads/picks-ledger\n", script)
    assert "::add-mask::" in script and "git init -q -b picks-ledger" in script
    # the artifact may hold only picks/ text files, no links or folders inside, and is size-bounded
    for guard in ("-type l", "'*.md'", "'*.csv'", "'*.json'", "'*.jsonl'", "du -sk", "picks/*"):
        assert guard in script


def test_actions_pinned_and_not_in_the_private_minutes_budget(wf):
    for job in wf["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:
                assert SHA_PIN.match(step["uses"]), step["uses"]
    assert NAME not in {p.workflow_file for p in PROFILE}  # public repository: standard runners are free
    rec = next(s for s in wf["jobs"]["record"]["steps"] if s.get("id") == "record")
    assert rec["timeout-minutes"] < wf["jobs"]["record"]["timeout-minutes"]


def test_artifacts_and_cache_hold_no_market_data(wf):
    steps = wf["jobs"]["record"]["steps"]
    ups = [s for s in steps if "upload-artifact" in s.get("uses", "")]
    report, view = ups
    paths = [p.strip() for p in report["with"]["path"].splitlines() if p.strip()]
    assert [p.rsplit("/", 1)[-1] for p in paths] == ["report.json", "summary.md", "outbox.json"]
    assert report["with"]["retention-days"] <= 30
    assert view["with"]["path"].endswith("/view/") and view["with"]["retention-days"] <= 7
    assert "steps.record.outputs.view == 'true'" in view["if"]  # a dry run uploads no view: nothing to publish
    assert "steps.record.outputs.store == 'github'" in view["if"]
    caches = [s for s in steps if "actions/cache" in s.get("uses", "")]
    assert {s["with"]["path"].rsplit("/", 1)[-1] for s in caches} == {"outbox.json"}
    assert all(s["with"]["key"].startswith("apex-picks-outbox-") for s in caches)


def test_the_other_lane_workflows_are_untouched():
    for other in ("apex-forward-shadow.yml", "apex-evidence-tournament.yml"):
        text = (WF_DIR / other).read_text()
        assert "picks" not in text and "contents: write" not in text
