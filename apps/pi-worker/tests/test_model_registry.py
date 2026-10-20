import json
import os
from datetime import UTC, datetime

import pytest

from apex_worker.agent.crypto import load_signing_key, public_from_signing_key
from apex_worker.models.manifest import ManifestRejected
from apex_worker.models.store import ModelStore
from apex_worker.research.pipeline import run_training

KEY = load_signing_key("BwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc")  # TEST-ONLY key
OTHER = load_signing_key("CAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAg")
TRUST = {1: public_from_signing_key(KEY)}
NOW = datetime(2026, 10, 20, 20, 0, tzinfo=UTC)
NOW_ISO = "2026-10-20T20:00:00Z"


@pytest.fixture(scope="module")
def release(tmp_path_factory):
    out = tmp_path_factory.mktemp("train")
    res = run_training("redteam", out, signing_key=KEY, now=NOW, code_sha="test")
    assert res.status == "succeeded" and res.candidates, res.detail
    rel = out / "releases" / res.candidates[0]["model_id"]
    return json.loads((rel / "manifest.json").read_text()), (rel / "model.json").read_bytes(), out


def test_training_outputs_and_summary(release):
    manifest, _artifact, out = release
    assert manifest["data_source"] == "synthetic_fixture" and manifest["is_demo"] is True
    assert "signature" in manifest
    assert (out / "summary.md").read_text().startswith("# APEX training run")
    report = json.loads((out / "report.json").read_text())
    assert report["decisions"]["redteam_planted_alpha"]["status"] == "EXPERIMENTAL"


def test_install_activate_and_rollback(release, tmp_path):
    manifest, artifact, _ = release
    store = ModelStore(tmp_path, TRUST, "DEMO")
    active = store.install(manifest, artifact, NOW_ISO)
    assert store.current_id() == manifest["model_id"] and active.model.predict([{}]).shape == (1, 3)
    # a second (re-signed, different) version becomes current; previous points at the first
    m2 = dict(manifest)
    a2 = artifact.replace(b'"metadata":{', b'"metadata":{"note":"v2",')
    import hashlib

    from apex_worker.models.manifest import sign

    m2.update(
        {
            "model_id": manifest["model_id"][:-2] + "zz",
            "artifact_sha256": hashlib.sha256(a2).hexdigest(),
            "artifact_size_bytes": len(a2),
        }
    )
    m2.pop("signature")
    m2 = sign(m2, KEY)
    store.install(m2, a2, NOW_ISO)
    assert store.current_id() == m2["model_id"] and store.previous_id() == manifest["model_id"]
    back = store.rollback(NOW_ISO)
    assert back.model_id == manifest["model_id"] and store.previous_id() == m2["model_id"]
    # restart: re-verify from disk
    fresh = ModelStore(tmp_path, TRUST, "DEMO")
    assert fresh.load_active(NOW_ISO).model_id == manifest["model_id"]


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("unsigned", "BAD_SIGNATURE"),
        ("wrong_key", "BAD_SIGNATURE"),
        ("tampered_artifact", "CHECKSUM_MISMATCH"),
        ("tampered_manifest", "BAD_SIGNATURE"),
        ("expired", "VALIDATION_EXPIRED"),
        ("live_mode", "SYNTHETIC_NOT_ALLOWED"),
        ("pickle_payload", "SIZE_MISMATCH"),
    ],
)
def test_rejections_keep_previous_model(release, tmp_path, mutation, code):
    manifest, artifact, _ = release
    store = ModelStore(tmp_path, TRUST, "DEMO")
    store.install(manifest, artifact, NOW_ISO)
    m, a, now_iso, s = dict(manifest), artifact, NOW_ISO, store
    if mutation == "unsigned":
        m.pop("signature")
    elif mutation == "wrong_key":
        from apex_worker.models.manifest import sign

        m.pop("signature")
        m = sign(m, OTHER)
    elif mutation == "tampered_artifact":
        a = artifact.replace(b'"kind"', b'"kind" ', 1)[: len(artifact)]
    elif mutation == "tampered_manifest":
        m["evaluation"] = {**m["evaluation"], "confidence_status": "CALIBRATED"}
    elif mutation == "expired":
        now_iso = "2099-01-01T00:00:00Z"
    elif mutation == "live_mode":
        s = ModelStore(tmp_path / "live", TRUST, "PAPER_LIVE_DATA")
    elif mutation == "pickle_payload":
        a = b"\x80\x04\x95" + b"cos\nsystem\n" + b"." * 10
    with pytest.raises(ManifestRejected) as err:
        s.install(m, a, now_iso)
    assert err.value.code == code
    assert store.current_id() == manifest["model_id"]  # incumbent untouched


def test_tampered_file_on_disk_fails_closed(release, tmp_path):
    manifest, artifact, _ = release
    store = ModelStore(tmp_path, TRUST, "DEMO")
    store.install(manifest, artifact, NOW_ISO)
    path = tmp_path / "versions" / manifest["model_id"] / "model.json"
    os.chmod(path, 0o644)
    path.write_bytes(artifact.replace(b"0", b"1", 1))
    assert ModelStore(tmp_path, TRUST, "DEMO").load_active(NOW_ISO) is None
