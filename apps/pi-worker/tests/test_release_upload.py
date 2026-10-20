"""Candidate upload, failed-candidate handling (acceptance D), training-run reports and promote/rollback CLI.

A small in-memory fake of the release API (same status/code shapes as apps/web) stands behind httpx.MockTransport.
"""

import base64
import json
from datetime import UTC, datetime

import httpx
import pytest

from apex_worker.agent.crypto import load_signing_key, public_from_signing_key
from apex_worker.release import cli as rcli
from apex_worker.release.client import ReleaseClient, ReleaseConfig
from apex_worker.release.runs import build_run, compact_training
from apex_worker.release.upload import CandidateInvalid, collect_candidates, upload_candidates, verify_champions
from apex_worker.research.pipeline import run_training

KEY = load_signing_key("BwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc")  # TEST-ONLY key
OTHER = load_signing_key("CAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAg")
TRUST = {1: public_from_signing_key(KEY)}
NOW = datetime(2026, 10, 20, 20, 0, tzinfo=UTC)
ENV = {
    "APEX_RELEASE_URL": "https://apex.example.test",
    "APEX_RELEASE_AGENT_ID": "rel_ci0000test",
    "APEX_RELEASE_HMAC_SECRET": base64.b64encode(bytes(range(32))).decode(),  # TEST-ONLY
}


class FakeRegistry:
    """Just enough of /api/v1/release/* to exercise the CI flow (frozen-policy gate is the server's job)."""

    def __init__(self, mode="DEMO", promotion_mode="manual", champions=None):
        self.mode, self.promotion_mode = mode, promotion_mode
        self.models: dict[str, dict] = {}
        self.artifacts: dict[str, str] = {}
        self.champion: dict[str, str] = dict(champions or {})  # strategy -> model id
        self.runs: list[dict] = []
        self.calls: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.calls.append(f"{req.method} {path}")
        body = json.loads(req.content) if req.content else {}
        if path.endswith("/policy"):
            return httpx.Response(200, json={"mode": self.mode, "policy": {"promotion_mode": self.promotion_mode},
                                             "budgets": {"actions_monthly_minutes": 1500, "drift_enabled": True},
                                             "usage_month": {"minutes_used": 12.0, "runs": 3}, "last_runs": {}})
        if path.endswith("/models"):
            m = body["manifest"]
            if self.mode != "DEMO" and m.get("is_demo"):
                return httpx.Response(422, json={"error": {"code": "MODE_MISMATCH", "message": "DEMO models are not accepted"}})
            if "signature" not in m:
                return httpx.Response(422, json={"error": {"code": "UNSIGNED_MANIFEST", "message": "manifest is not signed"}})
            created = m["model_id"] not in self.models
            self.models.setdefault(m["model_id"], m)
            self.artifacts.setdefault(m["model_id"], body["artifact_b64"])
            return httpx.Response(200, json={"model_id": m["model_id"], "created": created,
                                             "promotion": {"promoted": False, "reason": "manual promotion mode: awaiting owner review"}})
        if path.endswith("/promotions"):
            mid = body["model_id"]
            if mid not in self.models:
                return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "model not found"}})
            sid = self.models[mid]["strategy_id"]
            if body["action"] == "promote":
                if self.models[mid]["evaluation"]["confidence_status"] == "UNVALIDATED":
                    return httpx.Response(200, json={"action": "promote", "requested_model_id": mid, "promoted": False,
                                                     "reason": "candidate failed validation gates"})
                self.champion[sid] = mid
                return httpx.Response(200, json={"action": "promote", "requested_model_id": mid, "promoted": True, "reason": "ok"})
            return httpx.Response(200, json={"action": "rollback", "requested_model_id": mid, "rolled_back": False,
                                             "reason": "no previous champion to roll back to"})
        if path.endswith("/champions"):
            champs = {sid: {"model_id": mid, "holdout": {"brier_skill": 0.9}, "gates": {}, "confidence_status": "CALIBRATED"}
                      for sid, mid in self.champion.items()}
            out = {"champions": champs}
            if req.url.params.get("include") == "artifacts":
                out["manifests"] = [self.models[mid] for mid in self.champion.values() if mid in self.models]
                out["artifacts"] = {mid: self.artifacts[mid] for mid in self.champion.values() if mid in self.artifacts}
            return httpx.Response(200, json=out)
        if path.endswith("/training-runs"):
            self.runs.append(body)
            return httpx.Response(200, json={"id": f"run-{len(self.runs)}"})
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": path}})


def client(fake) -> ReleaseClient:
    return ReleaseClient(ReleaseConfig.from_env(ENV), transport=httpx.MockTransport(fake), sleep=lambda s: None)


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    out = tmp_path_factory.mktemp("train")
    res = run_training("redteam", out, signing_key=KEY, now=NOW, code_sha="test")
    assert res.status == "succeeded" and res.candidates and res.candidates[0]["promotable"], res.detail
    return out, res


def test_report_records_data_hash(trained):
    out, res = trained
    report = json.loads((out / "report.json").read_text())
    assert report["data_hash"] == res.data_hash == "redteam-planted-alpha-seed99"
    assert "data hash" in (out / "summary.md").read_text()


def test_collect_checks_signature_checksum_and_ids(trained, tmp_path):
    out, _ = trained
    _, cands = collect_candidates(out, TRUST)
    assert cands and all(c.manifest["signature"] for c in cands)
    with pytest.raises(CandidateInvalid, match="APEX_MODEL_TRUSTED_KEYS"):
        collect_candidates(out, {1: public_from_signing_key(OTHER)})
    # tampered artifact on disk is caught before anything is sent
    import shutil

    copy = tmp_path / "copy"
    shutil.copytree(out, copy)
    mid = cands[0].model_id
    (copy / "releases" / mid / "model.json").write_bytes(b'{"kind":"logistic_competing_v1"}')
    with pytest.raises(CandidateInvalid, match=r"size mismatch|sha256"):
        collect_candidates(copy, TRUST)
    # path traversal through a crafted report is refused
    rep = json.loads((copy / "report.json").read_text())
    rep["candidates"][0]["model_id"] = "../../etc"
    (copy / "report.json").write_text(json.dumps(rep))
    with pytest.raises(CandidateInvalid, match="invalid model id"):
        collect_candidates(copy, TRUST)


def test_upload_registers_promotable_candidate_in_manual_mode(trained):
    out, _ = trained
    fake = FakeRegistry()
    with client(fake) as c:
        res = upload_candidates(c, out, policy={"mode": "DEMO", "policy": {"promotion_mode": "manual"}}, trusted=TRUST)
    assert res["outcome"] == "candidate_registered" and len(res["registered"]) == 1
    assert fake.champion == {}  # manual policy: registration never promotes by itself
    with client(fake) as c:  # idempotent re-run (e.g. job re-attempt)
        again = upload_candidates(c, out, policy={"mode": "DEMO"}, trusted=TRUST)
    assert again["registered"][0]["created"] is False


def test_demo_candidate_never_sent_to_live_registry(trained):
    out, _ = trained
    fake = FakeRegistry(mode="LIVE")
    with client(fake) as c:
        res = upload_candidates(c, out, policy={"mode": "LIVE"}, trusted=TRUST)
    assert res["registered"] == [] and "DEMO/synthetic" in res["skipped"][0]["reason"]
    assert not any(call.endswith("/models") for call in fake.calls)
    with client(fake) as c:  # older server without /policy: the server's MODE_MISMATCH is treated as a skip
        res2 = upload_candidates(c, out, policy=None, trusted=TRUST)
    assert res2["skipped"] and res2["outcome"] == "champion_retained"


def test_failed_candidate_leaves_incumbent_untouched(tmp_path):
    """Acceptance D: the challenger does not beat the champion -> not promotable -> never uploaded."""
    champions = {"redteam_planted_alpha": {"model_id": "mdl_incumbent0001", "holdout": {"brier_skill": 0.99},
                                           "gates": {"selected_mean_lower_p10": 0.5}, "confidence_status": "CALIBRATED"}}
    res = run_training("redteam", tmp_path, signing_key=KEY, now=NOW, code_sha="test", champions=champions)
    assert res.candidates and not res.candidates[0]["promotable"]
    assert "below required" in res.decisions["redteam_planted_alpha"]["reason"]
    fake = FakeRegistry(champions={"redteam_planted_alpha": "mdl_incumbent0001"})
    with client(fake) as c:
        up = upload_candidates(c, tmp_path, policy={"mode": "DEMO", "policy": {"promotion_mode": "auto"}}, trusted=TRUST)
    assert up["outcome"] == "champion_retained" and up["registered"] == []
    assert fake.champion == {"redteam_planted_alpha": "mdl_incumbent0001"}
    assert not any(call.endswith(("/models", "/promotions")) for call in fake.calls)
    run = build_run(workflow="train_weekly", status="success", started_at=NOW, finished_at=NOW, outcome=up["outcome"],
                    report={"training": compact_training(json.loads((tmp_path / "report.json").read_text()))}, env={})
    assert run["status"] == "succeeded" and run["outcome"] == "champion_retained" and run["candidate_model_id"] is None


def test_build_run_validates_enums_and_estimates_minutes():
    started = datetime(2026, 10, 20, 23, 17, tzinfo=UTC)
    finished = datetime(2026, 10, 20, 23, 21, 5, tzinfo=UTC)
    env = {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_SHA": "a" * 40, "GITHUB_EVENT_NAME": "schedule",
           "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "o/r", "GITHUB_ACTIONS": "true"}
    run = build_run(workflow="drift", status="failure", started_at=started, finished_at=finished, outcome="drift_ok",
                    budget={"budget_minutes": 1500, "measured_minutes": 100.0}, env=env)
    assert run["minutes_used"] == 5 and run["minutes_used_month_estimate"] == 105.0 and run["minutes_budget_month"] == 1500
    assert run["trigger"] == "schedule" and run["github_run_id"] == "123" and run["github_run_attempt"] == 2
    assert run["status"] == "failed" and run["report"]["run_url"] == "https://github.com/o/r/actions/runs/123"
    for bad in ({"workflow": "nope"}, {"status": "great"}, {"outcome": "won"}, {"trigger": "cron"}):
        kw = {"workflow": "drift", "status": "succeeded", "started_at": started, "env": env} | bad
        with pytest.raises(ValueError):
            build_run(**kw)


def test_cli_upload_promote_and_report_run(trained, tmp_path, monkeypatch):
    out, _ = trained
    fake = FakeRegistry(promotion_mode="auto")
    monkeypatch.setattr(rcli, "_client", lambda: client(fake))
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("APEX_MODEL_TRUSTED_KEYS", json.dumps({"1": TRUST[1]}))
    gh_out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))
    from apex_worker.cli import main

    assert main(["release", "upload", "--dir", str(out), "--out", str(tmp_path / "up.json"), "--github-output"]) == 0
    outputs = dict(line.split("=", 1) for line in gh_out.read_text().splitlines())
    assert outputs["outcome"] == "candidate_registered" and outputs["promotion_mode"] == "auto"
    mid = outputs["candidate_model_id"]
    assert main(["release", "promote", "--model-ids", mid, "--out", str(tmp_path / "rel.json"), "--fail-on-reject"]) == 0
    rel = json.loads((tmp_path / "rel.json").read_text())
    assert rel["outcome"] == "promoted" and rel["results"][0]["verified_champion"] is True
    assert rel["registry_verification"][mid]["ok"] and rel["signature_checked"]
    # rollback without a previous champion is refused by the server -> strict mode fails the job
    assert main(["release", "rollback", "--model-ids", mid, "--out", str(tmp_path / "rb.json"), "--fail-on-reject"]) == 1
    assert main(["release", "promote", "--model-ids", "not-an-id"]) == 2
    monkeypatch.setenv("GITHUB_RUN_ID", "777")
    assert main(["release", "report-run", "--workflow", "train_weekly", "--status", "success", "--started-at", "1792512000",
                 "--dir", str(out), "--upload", str(tmp_path / "up.json"), "--reason", "unit test"]) == 0
    run = fake.runs[-1]
    assert run["outcome"] == "candidate_registered" and run["candidate_model_id"] == mid and run["is_demo"] is True
    assert run["report"]["training"]["data_hash"] and run["report"]["reason"] == "unit test"
    assert "APEX_RELEASE_HMAC_SECRET" not in json.dumps(run) and ENV["APEX_RELEASE_HMAC_SECRET"] not in json.dumps(run)


def test_report_run_without_training_output_is_a_failure(tmp_path, monkeypatch):
    fake = FakeRegistry()
    monkeypatch.setattr(rcli, "_client", lambda: client(fake))
    from apex_worker.cli import main

    assert main(["release", "report-run", "--workflow", "train_weekly", "--status", "success", "--dir", str(tmp_path)]) == 0
    assert fake.runs[-1]["status"] == "failed" and "no report.json" in fake.runs[-1]["report"]["detail"]
    assert main(["release", "report-run", "--workflow", "train_weekly", "--status", "skipped_budget"]) == 0
    assert fake.runs[-1]["status"] == "skipped_budget" and fake.runs[-1]["outcome"] is None


def test_verify_champions_detects_registry_tampering(trained):
    out, _ = trained
    _, cands = collect_candidates(out, TRUST)
    c = cands[0]
    good = {"manifests": [c.manifest], "artifacts": {c.model_id: base64.b64encode(c.artifact).decode()}}
    assert verify_champions(good, TRUST)[c.model_id]["ok"]
    bad = {"manifests": [c.manifest], "artifacts": {c.model_id: base64.b64encode(c.artifact + b" ").decode()}}
    assert not verify_champions(bad, TRUST)[c.model_id]["ok"]
    forged = dict(c.manifest, evaluation={**c.manifest["evaluation"], "confidence_status": "CALIBRATED"})
    assert not verify_champions({"manifests": [forged], "artifacts": good["artifacts"]}, TRUST)[c.model_id]["ok"]
