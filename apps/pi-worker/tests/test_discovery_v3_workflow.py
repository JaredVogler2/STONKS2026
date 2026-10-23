"""Static invariants of .github/workflows/apex-discovery-v3.yml (actionlint runs in CI separately)."""

import re

import pytest

yaml = pytest.importorskip("yaml")

from test_release_workflows import SHA_PIN, WF_DIR, load, triggers  # noqa: E402

NAME = "apex-discovery-v3.yml"


def test_manual_only_read_only_trusted_branch():
    wf, raw = load(NAME), (WF_DIR / NAME).read_text()
    assert set(triggers(wf)) == {"workflow_dispatch"}
    assert set(triggers(wf)["workflow_dispatch"]["inputs"]) == {"plan", "experiment_id"}
    assert wf["permissions"] == {"contents": "read"}
    (job,) = wf["jobs"].values()
    assert job["permissions"] == {"contents": "read"} and job["environment"] == "real-equities-research"
    assert "default_branch" in job["if"] and job["timeout-minutes"] == 45
    for bad in ("pull_request", "write", "git push", "gh pr", "id-token"):
        assert bad not in raw.replace("contents: read", "")


def test_secrets_only_in_the_research_step_and_inputs_only_through_env():
    wf, raw = load(NAME), (WF_DIR / NAME).read_text()
    (job,) = wf["jobs"].values()
    holders = [s for s in job["steps"] if "secrets." in str(s)]
    assert [s.get("id") for s in holders] == ["research"]
    assert set(re.findall(r"secrets\.([A-Z_]+)", raw)) == {"ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"}
    for s in job["steps"]:
        assert "${{ inputs." not in s.get("run", "")  # inputs reach scripts only through env, validated as slugs
    assert "--source alpaca" in holders[0]["run"]
    assert any("budget-check" in s.get("run", "") for s in job["steps"])


def test_pinned_actions_and_aggregate_only_artifact():
    wf = load(NAME)
    (job,) = wf["jobs"].values()
    for step in job["steps"]:
        if "uses" in step:
            assert SHA_PIN.match(step["uses"]), step["uses"]
    up = next(s for s in job["steps"] if "upload-artifact" in s.get("uses", ""))
    names = [p.strip().rsplit("/", 1)[-1] for p in up["with"]["path"].splitlines() if p.strip()]
    assert names == ["manifest.json", "registry.jsonl", "results.json", "frontier.json", "TRIAL_INDEX.md",
                     "data_quality_summary.json", "cost_study.json", "COST_STUDY.md", "design_study.json",
                     "DESIGN_STUDY.md"]
    assert up["with"]["retention-days"] <= 7


def test_cost_study_plans_route_to_the_measurement():
    (job,) = load(NAME)["jobs"].values()
    run = next(s for s in job["steps"] if s.get("id") == "research")["run"]
    assert 'if [[ "$PLAN" == cost-study-* ]]; then cmd=costs; fi' in run
    assert 'if [[ "$PLAN" == design-study-* ]]; then cmd=design; fi' in run
    assert 'apex research discovery "$cmd" --plan "$PLAN" --source alpaca' in run
    assert 'exit "$rc"' in run  # a blocked endpoint (rc 2) still fails the job visibly, after the warning
