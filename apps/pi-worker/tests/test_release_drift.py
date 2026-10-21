"""Drift diagnostic: PSI, latest-window calibration vs signed criteria, verification and the `apex drift` CLI."""

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from apex_worker.agent.crypto import load_signing_key, public_from_signing_key
from apex_worker.release.drift import champion_drift, overall_outcome, psi, render_markdown
from apex_worker.research.model import CompetingModel
from apex_worker.research.pipeline import run_training
from apex_worker.research.redteam import planted_alpha_events

KEY = load_signing_key("BwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc")  # TEST-ONLY key
NOW = datetime(2026, 10, 20, 20, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def champion(tmp_path_factory):
    out = tmp_path_factory.mktemp("champ")
    res = run_training("redteam", out, signing_key=KEY, now=NOW, code_sha="test")
    mid = res.candidates[0]["model_id"]
    manifest = json.loads((out / "releases" / mid / "manifest.json").read_text())
    artifact = (out / "releases" / mid / "model.json").read_bytes()
    return manifest, artifact


def test_psi_detects_shift_and_handles_degenerate_inputs():
    rng = np.random.default_rng(3)
    a, b = rng.normal(0, 1, 2000), rng.normal(0, 1, 2000)
    assert psi(a, b) < 0.05
    assert psi(a, rng.normal(1.5, 1, 2000)) > 0.25
    assert psi(np.ones(500), np.ones(500)) is None  # constant feature: no bins
    assert psi(a[:5], b[:5]) is None  # too few rows


def test_champion_on_its_own_regime_is_not_a_breach(champion):
    manifest, artifact = champion
    model = CompetingModel.from_json(artifact.decode())
    r = champion_drift(manifest, model, planted_alpha_events())
    assert r["status"] in ("ok", "ok_features_only") and r["calibration"]["n"] >= 30
    assert r["calibration"]["brier_skill"] > 0 and not r["breaches"]
    assert r["latest_events"] >= r["latest_resolved"] > 0 and r["reference_events"] > 0


def test_broken_relationship_breaches_calibration(champion):
    manifest, artifact = champion
    model = CompetingModel.from_json(artifact.decode())
    ev = planted_alpha_events()
    cut = model.metadata["trained_through"]
    late = ev["decision_date"] > cut
    rng = np.random.default_rng(0)
    ev.loc[late, "outcome"] = rng.permutation(ev.loc[late, "outcome"].to_numpy())  # the edge disappears after training
    ev.loc[late, "rel_volume_1"] = ev.loc[late, "rel_volume_1"] + 5.0  # and a feature shifts
    r = champion_drift(manifest, model, ev)
    assert r["status"] == "breach" and any("PSI(rel_volume_1)" in b for b in r["breaches"])
    assert overall_outcome([r]) == "drift_breach"
    assert "breach" in render_markdown({"champions": [r], "outcome": "drift_breach"})


def test_too_few_resolved_events_is_unknown_not_a_pass(champion):
    manifest, artifact = champion
    model = CompetingModel.from_json(artifact.decode())
    ev = planted_alpha_events()
    r = champion_drift(manifest, model, ev[ev["decision_date"] <= model.metadata["trained_through"]])
    assert r["status"] == "insufficient_data"
    assert overall_outcome([r]) == "no_data"


def test_drift_cli_offline_champions_file_verifies_before_use(champion, tmp_path, monkeypatch):
    from apex_worker.cli import main

    manifest, artifact = champion
    monkeypatch.setenv("APEX_MODEL_TRUSTED_KEYS", json.dumps({"1": public_from_signing_key(KEY)}))
    tampered = dict(manifest, drift_criteria={"max_ece": "9", "min_brier_skill": "-9", "max_feature_psi": "9"})
    data = {"champions": {}, "manifests": [tampered], "artifacts": {manifest["model_id"]: base64.b64encode(artifact).decode()}}
    f = tmp_path / "champions.json"
    f.write_text(json.dumps(data))
    rc = main(["drift", "--source", "synthetic", "--champions-file", str(f), "--out", str(tmp_path / "d")])
    out = json.loads((tmp_path / "d" / "drift.json").read_text())
    assert rc == 1 and out["outcome"] == "no_data" and out["champions"][0]["status"] == "unverified"
    assert (tmp_path / "d" / "drift.md").exists()


def test_drift_cli_end_to_end_on_synthetic_fixture(champion, tmp_path, monkeypatch):
    """A DEMO champion assessed on the synthetic market (the redteam strategy has no synthetic events => UNKNOWN)."""
    from apex_worker.cli import main

    manifest, artifact = champion
    monkeypatch.delenv("APEX_MODEL_TRUSTED_KEYS", raising=False)
    monkeypatch.delenv("ALPACA_KEY_ID", raising=False)
    data = {"manifests": [manifest], "artifacts": {manifest["model_id"]: base64.b64encode(artifact).decode()}}
    f = tmp_path / "champions.json"
    f.write_text(json.dumps(data))
    assert main(["drift", "--champions-file", str(f), "--out", str(tmp_path / "d"), "--window-years", "1"]) == 0
    out = json.loads(Path(tmp_path / "d" / "drift.json").read_text())
    assert out["is_demo"] is True and out["data_source"] == "synthetic_fixture" and len(out["data_hash"]) == 64
    assert out["outcome"] == "no_data" and out["status"] == "succeeded"  # never a fabricated pass
    assert out["champions"][0]["status"] == "insufficient_data" and "library" in out["champions"][0]["reason"]
