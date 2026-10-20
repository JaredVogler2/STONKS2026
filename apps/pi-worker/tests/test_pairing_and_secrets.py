import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from agent_helpers import AGENT, SECRET, Clock, client, server
from apex_worker.agent.canonical import from_b64url
from apex_worker.agent.crypto import DeviceKeys
from apex_worker.agent.identity import AgentIdentity
from apex_worker.agent.pairing import (
    code_hash,
    normalize_code,
    pair_device,
    pairing_message,
    pairing_request,
    server_nonce_for,
    valid_code,
)
from apex_worker.agent.secrets import MarketCredentials, SecretManager, announced_versions

VEC = Path(__file__).resolve().parents[3] / "packages/quant/vectors"
FAKE_ALPACA = {"key_id": "TESTKEYID000000000000", "secret_key": "TEST-ONLY-not-a-real-alpaca-secret"}


# ---- cross-language pairing vector (verified against apps/web pairing.ts + canonical.ts + ed25519.ts) -------------
def test_pairing_vector_matches_typescript_formula():
    v = json.loads((VEC / "pairing.json").read_text())
    keys = DeviceKeys(X25519PrivateKey.from_private_bytes(from_b64url(v["x25519_private_b64url"])),
                      Ed25519PrivateKey.from_private_bytes(from_b64url(v["ed25519_seed_b64url"])))
    assert keys.ed25519_public == v["ed25519_public_key"] and keys.x25519_public == v["x25519_public_key"]
    assert keys.fingerprint == v["fingerprint"]
    pub = Ed25519PublicKey.from_public_bytes(from_b64url(v["ed25519_public_key"]))
    for c in v["cases"]:
        assert normalize_code(c["code_input"]) == c["code_normalized"]
        assert code_hash(c["code_input"]) == c["code_hash"]
        assert server_nonce_for(c["code_input"]) == c["server_nonce"]
        msg = pairing_message(c["agent_id"], c["code_input"], keys.x25519_public)
        assert msg.decode() == c["message_utf8"]
        req = pairing_request(keys, c["agent_id"], c["code_input"], "apex-pi")
        assert req["proof"] == c["proof"]  # Ed25519 is deterministic
        pub.verify(from_b64url(c["proof"]), msg)


def test_code_validation():
    assert valid_code("abcd-ef23") and valid_code("ABCD EF23")
    assert not valid_code("ABCD-EF2") and not valid_code("ABCD-EF2O")  # O is not in the alphabet


# ---- apex pair ---------------------------------------------------------------------------------------------------
@pytest.fixture
def pair_env(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("APEX_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("AGENT_HMAC_SECRET", base64.b64encode(SECRET).decode())
    for k in ("APEX_API_BASE_URL", "APEX_AGENT_ID"):
        monkeypatch.delenv(k, raising=False)
    clock = Clock(datetime.now(UTC))  # pair_device signs with the real wall clock
    srv = server(clock)
    srv.devices.clear()
    return tmp_path / "state", srv


def test_pair_device_end_to_end_pending_then_fingerprint(pair_env):
    state, srv = pair_env
    srv.start_pairing("ABCD-EF23")
    out = []
    assert pair_device("abcd-ef23", server="https://apex.test", device_name="kitchen-pi", transport=srv.transport(),
                       out=out.append) == 0
    keys = DeviceKeys.load(state)
    ident = AgentIdentity.load(state)
    assert (state / "device_keys.json").stat().st_mode & 0o777 == 0o600
    assert (state / "agent.json").stat().st_mode & 0o777 == 0o600
    assert ident.status == "pending_approval" and ident.fingerprint == keys.fingerprint
    dev = srv.devices[ident.agent_id]
    assert dev["status"] == "pending_approval" and dev["x25519"] == keys.x25519_public
    text = "\n".join(out)
    assert keys.fingerprint in text and "PENDING APPROVAL" in text
    assert SECRET.decode() not in text and "x25519" not in text.lower()
    # the second use of a one-time code fails, with a friendly message
    out.clear()
    assert pair_device("ABCD-EF23", server="https://apex.test", transport=srv.transport(), out=out.append) == 1
    assert "invalid, already used or expired" in "\n".join(out)


def test_pair_waits_for_owner_approval(pair_env, monkeypatch):
    state, srv = pair_env
    srv.start_pairing("HJKM-NPQR")
    import apex_worker.agent.pairing as pairing

    def approve_then_sleep(_s):
        for d in srv.devices.values():
            d["status"] = "active"

    orig = pairing.wait_for_approval
    monkeypatch.setattr(pairing, "wait_for_approval",
                        lambda c, mode, w, **kw: orig(c, mode, w, sleep=approve_then_sleep, poll=0.01))
    out = []
    assert pair_device("HJKM-NPQR", server="https://apex.test", wait_seconds=5, transport=srv.transport(),
                       out=out.append) == 0
    assert AgentIdentity.load(state).status == "active" and "APPROVED" in "\n".join(out)
    assert srv.heartbeats and srv.heartbeats[0]["components"]["pairing"]["status"] == "UNKNOWN"


def test_repair_reuses_identity_new_identity_rotates(pair_env):
    state, srv = pair_env
    for code in ("WXYZ-2345", "WXYZ-2346", "WXYZ-2347"):
        srv.start_pairing(code)
    pair_device("WXYZ-2345", server="https://apex.test", transport=srv.transport(), out=lambda _s: None)
    first = AgentIdentity.load(state).agent_id
    fp = DeviceKeys.load(state).fingerprint
    pair_device("WXYZ-2346", server="https://apex.test", transport=srv.transport(), out=lambda _s: None)
    assert AgentIdentity.load(state).agent_id == first and DeviceKeys.load(state).fingerprint == fp
    srv.devices[first]["status"] = "revoked"
    out = []
    assert pair_device("WXYZ-2347", server="https://apex.test", transport=srv.transport(), out=out.append) == 1
    assert "--new-identity" in "\n".join(out)
    srv.start_pairing("WXYZ-2348")
    assert pair_device("WXYZ-2348", server="https://apex.test", new_identity=True, transport=srv.transport(),
                       out=lambda _s: None) == 0
    assert AgentIdentity.load(state).agent_id != first and DeviceKeys.load(state).fingerprint != fp


def test_fingerprint_mismatch_aborts(pair_env):
    state, srv = pair_env
    srv.start_pairing("MNPQ-RSTV")
    orig = srv._pair
    srv._pair = lambda a, b, p: {**orig(a, b, p), "fingerprint": "AAAA-BBBB-CCCC-DDDD"}
    out = []
    assert pair_device("MNPQ-RSTV", server="https://apex.test", transport=srv.transport(), out=out.append) == 1
    assert "FINGERPRINT_MISMATCH" in "\n".join(out) and AgentIdentity.load(state) is None


def test_pair_preflight_without_network(pair_env, monkeypatch):
    _state, srv = pair_env
    assert pair_device("BAD", server="https://apex.test", transport=srv.transport(), out=lambda _s: None) == 2
    monkeypatch.delenv("AGENT_HMAC_SECRET")
    assert pair_device("ABCD-EF23", server="https://apex.test", transport=srv.transport(), out=lambda _s: None) == 2
    assert srv.log == []


# ---- secrets -----------------------------------------------------------------------------------------------------
@pytest.fixture
def secret_env(tmp_path):
    clock = Clock()
    keys = DeviceKeys.generate()
    srv = server(clock, x25519=keys.x25519_public)
    srv.set_integration("alpaca", FAKE_ALPACA, 3)
    c = client(srv, clock)
    return srv, keys, SecretManager(c, keys, clock=clock.now), clock, tmp_path


def test_secret_fetched_decrypted_held_in_memory_and_acked(secret_env):
    srv, keys, mgr, _clock, tmp = secret_env
    keys.save(tmp)
    creds = mgr.fetch()
    assert creds.key_id == FAKE_ALPACA["key_id"] and creds.secret_key == FAKE_ALPACA["secret_key"]
    assert mgr.version == 3 and mgr.status == "OK" and srv.secret_acks == [("alpaca", 3)]
    assert FAKE_ALPACA["secret_key"] not in repr(creds) + repr(mgr)
    for f in tmp.rglob("*"):
        if f.is_file():
            assert FAKE_ALPACA["secret_key"].encode() not in f.read_bytes()


def test_refetch_only_when_a_newer_version_is_announced(secret_env):
    srv, _keys, mgr, _clock, _tmp = secret_env
    mgr.fetch()
    calls = len(srv.log)
    assert mgr.maybe_refresh({"secret_versions": {"alpaca": 3}}) is False and len(srv.log) == calls
    srv.set_integration("alpaca", {**FAKE_ALPACA, "key_id": "TESTKEYID000000000001"}, 4)
    assert mgr.maybe_refresh({"ok": True}, {"secret_versions": {"alpaca": 4}}) is True
    assert mgr.version == 4 and mgr.credentials.key_id.endswith("1")
    assert announced_versions({"secret_versions": {"alpaca": True}}) == {}


def test_envelope_for_another_device_or_expired_is_rejected_and_old_creds_kept(secret_env):
    srv, _keys, mgr, clock, _tmp = secret_env
    mgr.fetch()
    srv.devices[AGENT]["x25519"] = DeviceKeys.generate().x25519_public  # sealed to a different key
    srv.set_integration("alpaca", FAKE_ALPACA, 5)
    mgr.maybe_refresh({"secret_versions": {"alpaca": 5}})
    assert mgr.status == "DEGRADED" and mgr.version == 3  # previous credentials still in memory
    mgr2 = SecretManager(mgr.client, mgr.keys, clock=lambda: clock.now() + timedelta(minutes=10))
    srv.devices[AGENT]["x25519"] = mgr.keys.x25519_public
    assert mgr2.fetch() is None and "expired" in mgr2.detail


def test_unconfigured_secret_retry_is_rate_limited_and_manual_fallback_is_labelled(secret_env):
    srv, keys, _mgr, clock, _tmp = secret_env
    srv.integrations.clear()
    manual = MarketCredentials("MANUALKEY", "TEST-ONLY-manual", 0, "manual_env")
    mgr = SecretManager(client(srv, clock), keys, manual=manual, clock=clock.now)
    assert mgr.fetch() is manual and mgr.status == "UNCONFIGURED"
    n = len(srv.log)
    mgr.maybe_refresh({})
    assert len(srv.log) == n  # retried at most every 30 minutes
    clock.advance(1801)
    mgr.maybe_refresh({})
    assert len(srv.log) == n + 1
    assert mgr.maybe_refresh({}, auth_failed=True) is False and len(srv.log) == n + 2  # provider 401 forces a refetch
