import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from apex_worker.agent.canonical import canonical_json, from_b64url
from apex_worker.agent.crypto import (
    DeviceKeys, EnvelopeError, load_signing_key, open_envelope, public_from_signing_key, sign_manifest, verify_manifest,
)
from apex_worker.agent.signing import canonical_request, sign

VEC = Path(__file__).resolve().parents[3] / "packages/quant/vectors"


def test_hmac_vectors_match_typescript():
    data = json.loads((VEC / "hmac.json").read_text())
    import base64
    secret = base64.b64decode(data["secret_b64"])
    for c in data["cases"]:
        h = sign(secret, c["agent_id"], c["method"], c["path"], c["body"].encode(), timestamp=int(c["timestamp"]),
                 nonce=c["nonce"])
        assert h.body_sha256 == c["body_sha256"]
        assert canonical_request(c["agent_id"], c["method"], c["path"], c["timestamp"], c["nonce"], h.body_sha256) == c["canonical"]
        assert h.signature == c["signature"]


def test_envelope_vector_opens_and_tamper_fails():
    data = json.loads((VEC / "envelope.json").read_text())
    priv = X25519PrivateKey.from_private_bytes(from_b64url(data["device_private_raw_b64url"]))
    env = data["envelope"]
    assert open_envelope(priv, env, expected_device="agt_testpi0001", now_iso="2026-10-20T20:01:00Z") == data["plaintext"]
    with pytest.raises(EnvelopeError):
        open_envelope(priv, env, expected_device="agt_other00001", now_iso="2026-10-20T20:01:00Z")
    with pytest.raises(EnvelopeError):
        open_envelope(priv, env, expected_device="agt_testpi0001", now_iso="2026-10-20T20:06:00Z")
    tampered = json.loads(json.dumps(env))
    tampered["meta"]["secret_version"] = 4
    with pytest.raises(EnvelopeError):
        open_envelope(priv, tampered, expected_device="agt_testpi0001", now_iso="2026-10-20T20:01:00Z")
    other = X25519PrivateKey.generate()
    with pytest.raises(EnvelopeError):
        open_envelope(other, env, expected_device="agt_testpi0001", now_iso="2026-10-20T20:01:00Z")


def test_canonical_json_rules():
    assert canonical_json({"b": 1, "a": [True, None, "x"]}) == '{"a":[true,null,"x"],"b":1}'
    with pytest.raises(ValueError):
        canonical_json({"x": 1.5})
    with pytest.raises(ValueError):
        canonical_json("ü")


def test_manifest_signature_roundtrip_and_rejections():
    key = load_signing_key("BwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc")
    pub = public_from_signing_key(key)
    manifest = {"model_id": "mdl_test0001", "artifact_sha256": "0" * 64, "signer_key_version": 1, "feature_schema_version": "f1"}
    manifest["signature"] = sign_manifest(key, manifest)
    assert verify_manifest(manifest, {1: pub})
    assert not verify_manifest({**manifest, "artifact_sha256": "1" * 64}, {1: pub})
    assert not verify_manifest(manifest, {2: pub})
    assert not verify_manifest({k: v for k, v in manifest.items() if k != "signature"}, {1: pub})


def test_device_keys_saved_owner_only(tmp_path):
    keys = DeviceKeys.generate()
    keys.save(tmp_path / "state")
    path = tmp_path / "state" / "device_keys.json"
    assert path.stat().st_mode & 0o777 == 0o600
    loaded = DeviceKeys.load(tmp_path / "state")
    assert loaded.ed25519_public == keys.ed25519_public
    path.chmod(0o644)
    with pytest.raises(PermissionError):
        DeviceKeys.load(tmp_path / "state")
