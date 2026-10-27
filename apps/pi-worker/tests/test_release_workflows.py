"""Static checks of .github/workflows (YAML parse + security/budget invariants). actionlint runs in CI separately."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from apex_worker.release.budget import PROFILE
from apex_worker.release.schedule import WEEKDAY_DRIFT, WEEKLY_TRAIN

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[3]
WF_DIR = ROOT / ".github" / "workflows"
MODEL_WORKFLOWS = ("apex-model-train-weekly.yml", "apex-model-drift.yml", "apex-model-release.yml")
SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}$")


def load(name: str) -> dict:
    return yaml.safe_load((WF_DIR / name).read_text())


def triggers(wf: dict) -> dict:
    on = wf.get("on", wf.get(True))  # YAML 1.1 reads a bare `on` key as boolean True
    return on if isinstance(on, dict) else {k: None for k in ([on] if isinstance(on, str) else on)}


def all_workflows() -> list[str]:
    return sorted(p.name for p in WF_DIR.glob("*.yml"))


def steps(wf: dict):
    for job_id, job in wf["jobs"].items():
        for step in job.get("steps", []) or []:
            yield job_id, job, step


def test_expected_workflows_exist_and_parse():
    names = all_workflows()
    for required in ("ci.yml", *MODEL_WORKFLOWS):
        assert required in names
    for n in names:
        assert isinstance(load(n), dict)


@pytest.mark.parametrize("name", ["ci.yml", *MODEL_WORKFLOWS])
def test_least_privilege_timeouts_and_pinned_actions(name):
    wf = load(name)
    assert wf["permissions"] == {"contents": "read"}, "top-level token must be read-only"
    raw = (WF_DIR / name).read_text()
    assert "pull_request_target" not in raw and "write-all" not in raw
    # the only write scope anywhere is id-token (the GitHub OIDC second factor), and only on model-release jobs
    oidc = {"contents": "read", "id-token": "write"}
    for job_id, job in wf["jobs"].items():
        perms = job.get("permissions")
        if "uses" in job:  # reusable workflow call: local file only, and it sets its own timeouts
            assert job["uses"].startswith("./.github/workflows/")
            assert perms == (oidc if name in MODEL_WORKFLOWS else {"contents": "read"})
            continue
        assert isinstance(job.get("timeout-minutes"), int), f"{name}:{job_id} needs timeout-minutes"
        assert job["runs-on"].startswith("ubuntu-"), "standard hosted Linux runners only"
        if name in MODEL_WORKFLOWS:
            assert job.get("environment") == "model-release" and perms == oidc, f"{name}:{job_id}"
        elif perms == {"contents": "read", "actions": "read"}:
            # ci.yml `tested` lists this workflow's runs; it runs on pushes to main only, never on pull-request code
            assert name == "ci.yml" and job.get("if") == "github.event_name == 'push'", f"{name}:{job_id}"
        else:
            assert perms in (None, {"contents": "read"})
    if name == "ci.yml":
        assert "id-token" not in raw, "untrusted PR code must never mint OIDC tokens"
    for _, _, step in steps(wf):
        uses = step.get("uses")
        if uses and not uses.startswith("./"):
            assert SHA_PIN.match(uses), f"{uses} must be pinned to a full commit SHA"
            line = next(ln for ln in raw.splitlines() if uses in ln)
            assert re.search(r"#\s*v\d+\.\d+\.\d+", line), f"{uses} needs a trailing `# vX.Y.Z` comment"
        if uses and uses.startswith("actions/checkout@"):
            assert (step.get("with") or {}).get("persist-credentials") is False


def test_ci_has_no_secrets_and_runs_the_gates():
    raw = (WF_DIR / "ci.yml").read_text()
    wf = load("ci.yml")
    assert "secrets." not in raw and "environment" not in "".join(str(j.keys()) for j in wf["jobs"].values())
    assert set(triggers(wf)) == {"pull_request", "push"}
    assert wf["concurrency"]["cancel-in-progress"] is True and "github.ref" in wf["concurrency"]["group"]
    for needle in ("pnpm install --frozen-lockfile", "contracts:check", "gen:check", "pnpm lint", "pnpm typecheck",
                   "pnpm test", "pnpm build", "APEX_MODE: DEMO", "ruff check", "pytest", "secret-scan.mjs",
                   "secret-scan.test.mjs", "actionlint"):
        assert needle in raw, needle


def test_ci_heavy_jobs_skip_only_for_unaffected_paths_or_an_already_tested_tree():
    wf = load("ci.yml")
    for job, area in (("python", "python"), ("forward-shadow-smoke", "smoke"), ("node", "node")):
        j = wf["jobs"][job]
        assert j["needs"] == ["changes", "tested"], job
        # fail open: a failed or skipped classifier leaves the outputs empty, and empty never skips
        assert j["if"] == (f"${{{{ !cancelled() && needs.changes.outputs.{area} != 'false' "
                           "&& needs.tested.outputs.duplicate != 'true' }}"), job
    for job in ("secret-scan", "workflow-lint", "changes"):
        assert "if" not in wf["jobs"][job] and "permissions" not in wf["jobs"][job], job
    assert wf["jobs"]["tested"]["if"] == "github.event_name == 'push'"
    assert "status=success" in str(wf["jobs"]["tested"]["steps"]) and "event=pull_request" in str(wf["jobs"]["tested"])


def _classify(tmp_path: Path, files: list[str] | None, base: str | None = None) -> dict[str, str]:
    """Run ci.yml's `changes` script in a scratch repository: an empty base commit, then `files` committed on top."""
    step = next(s for s in load("ci.yml")["jobs"]["changes"]["steps"] if s.get("id") == "areas")
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=repo,
                              check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "base")
    first = git("rev-parse", "HEAD")
    for f in files or []:
        (repo / f).parent.mkdir(parents=True, exist_ok=True)
        (repo / f).write_text("x\n")
    git("add", "-A")
    git("commit", "-q", "--allow-empty", "-m", "change")
    out, summary = tmp_path / "out", tmp_path / "summary"
    env = {"PATH": shutil.which("git").rsplit("/", 1)[0] + ":/usr/bin:/bin", "BASE": first if base is None else base,
           "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary)}
    subprocess.run(["bash", "-e", "-c", step["run"]], cwd=repo, env=env, check=True)
    return dict(line.split("=", 1) for line in out.read_text().split())


@pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="needs git and bash")
@pytest.mark.parametrize(("files", "expected"), [
    (["apps/web/src/app/page.tsx", "pnpm-lock.yaml"], "python=false smoke=false node=true"),
    # the Python tests read registrations, results and workflow files, so docs still run them (not the smoke)
    (["docs/evidence/DESIGN_STUDY_2.md", "research-results/x/REVIEW.md"], "python=true smoke=false node=false"),
    (["apps/pi-worker/src/apex_worker/cli.py"], "python=true smoke=true node=false"),
    (["fixtures/catalysts/edgar_8k_items.json"], "python=true smoke=true node=false"),
    (["apps/pi-worker/src/apex_worker/contracts/models.py"], "python=true smoke=true node=true"),
    (["apps/pi-worker/uv.lock"], "python=true smoke=true node=true"),
    (["supabase/migrations/0001.sql"], "python=true smoke=false node=true"),
    (["packages/contracts/schemas/signal.json"], "python=true smoke=true node=true"),
    (["raspberry-pi/bot/position_store.py"], "python=true smoke=false node=false"),
])
def test_ci_changed_paths_classifier(tmp_path, files, expected):
    got = _classify(tmp_path, files)
    assert " ".join(f"{k}={got[k]}" for k in ("python", "smoke", "node")) == expected


@pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="needs git and bash")
@pytest.mark.parametrize("base", ["", "0000000000000000000000000000000000000000", "deadbeef" * 5, "same"])
def test_ci_runs_everything_without_a_usable_base(tmp_path, base):
    # "same": an empty diff (nothing to classify) runs everything too
    got = _classify(tmp_path, [] if base == "same" else ["apps/web/x.ts"], base=None if base == "same" else base)
    assert got == {"python": "true", "smoke": "true", "node": "true"}


@pytest.mark.parametrize("name", MODEL_WORKFLOWS)
def test_model_workflows_never_run_for_pull_requests_and_only_on_default_branch(name):
    wf = load(name)
    assert not {"pull_request", "pull_request_target", "push"} & set(triggers(wf))
    assert "workflow_dispatch" in triggers(wf)
    for job in wf["jobs"].values():
        if "uses" in job:
            continue
        assert job["environment"] == "model-release"
        assert job["if"] == (
            "github.event_name == 'schedule' || github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
        )


@pytest.mark.parametrize("name", MODEL_WORKFLOWS)
def test_secrets_only_in_environment_jobs_and_signing_key_only_in_train_step(name):
    wf = load(name)
    raw = (WF_DIR / name).read_text()
    for job in wf["jobs"].values():
        job_text = yaml.safe_dump(job)
        if "secrets." in job_text:
            assert job.get("environment") == "model-release"
        assert "APEX_MODEL_SIGNING_KEY" not in yaml.safe_dump(job.get("env", {})), "signing key must not be job-wide"
    holders = [(j, s.get("name")) for j, _, s in steps(wf) if "APEX_MODEL_SIGNING_KEY" in yaml.safe_dump(s)]
    if name == "apex-model-train-weekly.yml":
        assert holders == [("train", "Train, validate, calibrate, sign")]
    else:
        assert holders == [] and "APEX_MODEL_SIGNING_KEY" not in raw


@pytest.mark.parametrize("name", MODEL_WORKFLOWS)
def test_artifacts_are_small_short_lived_and_never_contain_keys_or_models(name):
    for _, _, step in steps(load(name)):
        if str(step.get("uses", "")).startswith("actions/upload-artifact@"):
            w = step["with"]
            assert int(w["retention-days"]) <= 7
            paths = [p.strip() for p in str(w["path"]).splitlines() if p.strip()]
            assert paths and all(p.endswith(("report.json", "summary.md", "drift.json", "drift.md")) for p in paths)
            assert not any("releases" in p or "key" in p.lower() or "*" in p for p in paths)


def test_registry_concurrency_never_cancels_and_nested_call_cannot_deadlock():
    train = load("apex-model-train-weekly.yml")
    assert train["concurrency"] == {"group": "apex-model-registry", "cancel-in-progress": False, "queue": "max"}
    rel = load("apex-model-release.yml")
    conc = rel["jobs"]["release"]["concurrency"]
    assert conc["cancel-in-progress"] is False and "'apex-model-registry'" in conc["group"]
    assert "apex-model-registry-nested" in conc["group"] and "inputs.caller_run_id" in conc["group"]
    assert "concurrency" not in rel  # a workflow-level group equal to the caller's would deadlock the nested call
    call = train["jobs"]["promote"]
    assert call["uses"] == "./.github/workflows/apex-model-release.yml" and call["needs"] == "train"
    assert "promotion_mode == 'auto'" in call["if"] and call["with"]["caller_run_id"] == "${{ github.run_id }}"
    assert set(triggers(rel)) == {"workflow_dispatch", "workflow_call"}
    drift = load("apex-model-drift.yml")
    assert drift["concurrency"]["group"] != "apex-model-registry"


def _cron(name: str) -> dict:
    (entry,) = triggers(load(name))["schedule"]
    return entry


def test_schedules_use_documented_timezone_and_match_schedule_module():
    for name, sched in (("apex-model-train-weekly.yml", WEEKLY_TRAIN), ("apex-model-drift.yml", WEEKDAY_DRIFT)):
        entry = _cron(name)
        assert entry["timezone"] == "America/New_York"
        minute, hour, dom, month, dow = entry["cron"].split()
        assert (int(minute), int(hour)) == (sched.at.minute, sched.at.hour) and int(minute) not in (0, 30)
        assert dom == "*" and month == "*"
        days = {int(d) for part in dow.split(",") for d in (range(int(part[0]), int(part[-1]) + 1) if "-" in part else [part])}
        assert {(d - 1) % 7 for d in days} == set(sched.weekdays)  # cron Sunday=0 vs Python Monday=0


def test_job_timeouts_match_budget_profile():
    for p in PROFILE:
        job = load(p.workflow_file)["jobs"][p.job_id]
        assert job["timeout-minutes"] == p.timeout_minutes, f"{p.workflow_file}:{p.job_id} vs budget profile"
    train = load("apex-model-train-weekly.yml")["jobs"]["train"]
    step = next(s for s in train["steps"] if s.get("id") == "train")
    assert step["timeout-minutes"] < train["timeout-minutes"], "leave time for the always() report steps"


def test_workflow_inputs_never_interpolated_into_scripts():
    """Script-injection hygiene: ${{ inputs.* }} / ${{ github.event.* }} only through env, never inside run: blocks."""
    for name in all_workflows():
        for _, _, step in steps(load(name)):
            run = step.get("run") or ""
            assert not re.search(r"\$\{\{\s*(inputs\.|github\.event\.)", run), f"{name}: {step.get('name')}"
