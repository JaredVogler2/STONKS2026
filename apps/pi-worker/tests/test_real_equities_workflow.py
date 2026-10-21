"""Static security/budget invariants of apex-real-equities-research.yml and the registry preflight (Phase 14 A and F)."""

import json
import re
from pathlib import Path

import pytest

from apex_worker.research.realeq.report import OUTPUT_FILES

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[3]
WF = ROOT / ".github" / "workflows" / "apex-real-equities-research.yml"
SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}$")


@pytest.fixture(scope="module")
def wf():
    return yaml.safe_load(WF.read_text())


@pytest.fixture(scope="module")
def raw():
    return WF.read_text()


def _on(wf):
    return wf.get("on", wf.get(True))


def _job(wf):
    (job,) = wf["jobs"].values()
    return job


def test_manual_dispatch_only_with_bounded_inputs(wf):
    on = _on(wf)
    assert set(on) == {"workflow_dispatch"}, "no schedule, push or pull_request trigger"
    inputs = on["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"source", "years", "experiment_id", "universe", "time_start", "time_end"}
    assert inputs["source"]["options"] == ["alpaca"] and inputs["source"]["default"] == "alpaca"
    assert inputs["universe"]["options"] == ["fixed78"]
    assert inputs["years"]["default"] == "8" and inputs["experiment_id"]["required"] is True
    assert "research_only" not in inputs, "research_only is hard-coded, never an input"


def test_least_privilege_no_oidc_no_writes(wf, raw):
    job = _job(wf)
    assert wf["permissions"] == {"contents": "read"} and job["permissions"] == {"contents": "read"}
    code = "\n".join(ln for ln in raw.splitlines() if not ln.lstrip().startswith("#"))
    for needle in ("id-token", "write-all", "contents: write", "pull_request_target", "git push", "gh pr", "git commit"):
        assert needle not in code, needle
    assert job["env"]["APEX_RESEARCH_ONLY"] == "true"


def test_trusted_branch_environment_runner_timeout_concurrency(wf):
    job = _job(wf)
    assert job["if"] == "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
    assert job["environment"] == "real-equities-research"
    assert job["runs-on"] == "ubuntu-24.04" and isinstance(job["timeout-minutes"], int) and job["timeout-minutes"] <= 30
    assert wf["concurrency"] == {"group": "apex-real-equities-research", "cancel-in-progress": False}
    research = next(s for s in job["steps"] if s.get("id") == "research")
    assert research["timeout-minutes"] < job["timeout-minutes"]
    budget = next(s for s in job["steps"] if s.get("id") == "budget")
    assert f"--timeout-minutes {job['timeout-minutes']}" in budget["run"], "checkpoint uses the job's real worst case"


def test_only_alpaca_market_data_secrets_and_only_in_two_steps(wf, raw):
    secrets = set(re.findall(r"secrets\.([A-Z0-9_]+)", raw))
    assert secrets == {"ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"}
    job = _job(wf)
    assert "secrets." not in yaml.safe_dump(job.get("env", {})), "never job-wide"
    holders = [s.get("id") for s in job["steps"] if "secrets." in yaml.safe_dump(s)]
    assert holders == ["preflight", "research"]
    for banned in ("APEX_RELEASE", "APEX_MODEL_SIGNING_KEY", "HMAC", "DISCORD", "SUPABASE", "VERCEL"):
        assert banned not in raw, banned


def test_steps_order_pins_and_no_cache(wf, raw):
    steps = _job(wf)["steps"]
    ids = [s.get("id") for s in steps]
    assert ids.index("preflight") < ids.index("budget") < ids.index("research")
    research = steps[ids.index("research")]
    assert research["if"] == "steps.budget.outputs.proceed == 'true'"
    assert "apex research real-equities" in research["run"] and "--source alpaca" in research["run"]
    assert "BLOCKED — ALPACA_ACTIONS_SECRETS_MISSING" in raw
    assert "actions/cache" not in raw, "raw vendor bars are never persisted between runs"
    for s in steps:
        uses = s.get("uses")
        if uses:
            assert SHA_PIN.match(uses), uses
            line = next(ln for ln in raw.splitlines() if uses in ln)
            assert re.search(r"#\s*v\d+\.\d+\.\d+", line)
        if uses and uses.startswith("actions/checkout@"):
            assert s["with"]["persist-credentials"] is False
        assert not re.search(r"\$\{\{\s*(inputs\.|github\.event\.)", s.get("run") or ""), s.get("name")


def test_artifact_is_exactly_the_seven_aggregate_files(wf):
    (up,) = [s for s in _job(wf)["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    w = up["with"]
    assert int(w["retention-days"]) <= 7 and up["if"] == "always()"
    paths = [p.strip() for p in w["path"].splitlines() if p.strip()]
    assert sorted(Path(p).name for p in paths) == sorted(OUTPUT_FILES)
    assert all("*" not in p and "history" not in p and "cache" not in p for p in paths)


# ---- PRODUCTION_REGISTRY_UNCONFIGURED preflight in the production workflows ------------------------------------------
@pytest.mark.parametrize("name,report", [("apex-model-drift.yml", "Report drift run"),
                                         ("apex-model-train-weekly.yml", "Report training run")])
def test_registry_steps_are_gated_by_the_preflight(name, report):
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())
    (job,) = [j for j in wf["jobs"].values() if "steps" in j]
    steps = job["steps"]
    ids = [s.get("id") for s in steps]
    pre = ids.index("preflight")
    assert "apex release preflight" in steps[pre]["run"] and "--fail-when-unconfigured" in steps[pre]["run"]
    assert 'if [ "$EVENT" != "schedule" ]' in steps[pre]["run"], "manual runs fail, schedules skip cleanly"
    gate = "steps.preflight.outputs.configured == 'true'"
    for s in steps:
        run = s.get("run") or ""
        if "apex release policy" in run or "apex budget check" in run or "apex release sla" in run:
            assert s.get("if") == gate, s["name"]
            assert steps.index(s) > pre
        if s.get("name") == report:
            assert s["if"] == f"always() && {gate}"
    assert job["permissions"] == {"contents": "read", "id-token": "write"}, "authentication unchanged"


def _preflight(monkeypatch, tmp_path, env: dict, *flags):
    from apex_worker.cli import main

    for k in ("APEX_RELEASE_URL", "APEX_RELEASE_AGENT_ID", "APEX_RELEASE_HMAC_SECRET", "GITHUB_ACTIONS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    out_file, gh_out, summary = tmp_path / "pf.json", tmp_path / "gh_out", tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    rc = main(["release", "preflight", "--out", str(out_file), "--github-output", *flags])
    return rc, json.loads(out_file.read_text()), gh_out.read_text(), summary.read_text() if summary.exists() else ""


def test_preflight_reports_unconfigured_registry_without_network(monkeypatch, tmp_path):
    rc, body, out, summary = _preflight(monkeypatch, tmp_path, {})
    assert rc == 0 and body["status"] == "PRODUCTION_REGISTRY_UNCONFIGURED" and body["configured"] is False
    assert "configured=false" in out and "PRODUCTION_REGISTRY_UNCONFIGURED" in summary
    rc2, *_ = _preflight(monkeypatch, tmp_path, {}, "--fail-when-unconfigured")
    assert rc2 == 2


def test_preflight_fails_a_partial_configuration_and_never_prints_values(monkeypatch, tmp_path, capsys):
    rc, body, out, _ = _preflight(monkeypatch, tmp_path, {"APEX_RELEASE_URL": "https://apex.example.com"})
    assert rc == 2 and body["status"] == "PRODUCTION_REGISTRY_MISCONFIGURED" and "configured=false" in out
    import base64

    good = {"APEX_RELEASE_URL": "https://apex.example.com", "APEX_RELEASE_AGENT_ID": "rel_abcdefgh12",
            "APEX_RELEASE_HMAC_SECRET": base64.b64encode(b"k" * 32).decode()}
    rc, body, out, _ = _preflight(monkeypatch, tmp_path, good)
    assert rc == 0 and body["configured"] is True and "configured=true" in out
    printed = capsys.readouterr()
    assert good["APEX_RELEASE_HMAC_SECRET"] not in printed.out + printed.err + json.dumps(body)
