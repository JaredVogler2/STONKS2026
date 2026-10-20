"""Pi-side model distribution: amendment acceptance cases E (corrupt/unsigned/incompatible refused, previous intact,
private alert), G (Pi offline during release catches up without duplicates or code execution) and H (registry /
Supabase unavailable: no unrecoverable state, checksums re-validated on recovery)."""

import hashlib
import json
from datetime import timedelta

import pytest

import apex_worker.models.sync as sync_mod
from agent_helpers import Clock, client, server
from apex_worker.agent.crypto import load_signing_key
from apex_worker.demo.model_fixtures import OTHER_TEST_SEED, demo_key, demo_trusted_keys, fixture_release
from apex_worker.models.manifest import sign
from apex_worker.models.sync import ModelSync, preverify
from apex_worker.runtime.agent_state import AgentState
from apex_worker.storage.local import LocalStore
from apex_worker.timeutil import iso_utc

SID = "h1_vcp_breakout"


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    srv = server(clock)
    local = LocalStore(tmp_path / "bot.sqlite3")
    state = AgentState(tmp_path / "agent.sqlite3")

    def make(c=None):
        return ModelSync(c or client(srv, clock), tmp_path / "models", demo_trusted_keys(), "DEMO", state,
                         clock=clock.now, notify=local.enqueue)

    return srv, clock, make, local, state, tmp_path


def _alerts(local):
    return [r for r in local.outbox_rows() if r.kind == "SYSTEM_ALERT"]


def _artifact_downloads(srv):
    return [p for _m, p, _s in srv.log if p.endswith("/artifact")]


def test_valid_release_activated_atomically_and_acked_once(env):
    srv, clock, make, local, _state, tmp = env
    sync = make()
    m1, a1 = fixture_release(SID, clock.now())
    srv.publish_champion(m1, a1)
    rep = sync.poll()
    assert rep.activated == [m1["model_id"]] and sync.active_models()[SID].model_id == m1["model_id"]
    assert (tmp / "models" / "strategies" / SID / "current").is_symlink()
    assert srv.activations == [{"agent_id": "agt_testpi0001", "model_id": m1["model_id"], "status": "activated",
                                "detail": f"active for {SID}"}]
    assert sync.poll().unchanged == [m1["model_id"]] and len(srv.activations) == 1 and len(_artifact_downloads(srv)) == 1
    assert _alerts(local) == []


@pytest.mark.parametrize("case", ["corrupt", "unsigned", "untrusted_key", "incompatible_schema", "pickle", "expired",
                                  "oversize"])
def test_E_bad_releases_refused_previous_model_intact_private_alert(env, case):
    srv, clock, make, local, _state, _tmp = env
    sync = make()
    m1, a1 = fixture_release(SID, clock.now())
    srv.publish_champion(m1, a1)
    sync.poll()
    m2, a2 = fixture_release(SID, clock.now(), note="v2")
    if case == "corrupt":
        a2 = a2.replace(b'"v2"', b'"v7"')
    elif case == "unsigned":
        m2 = {k: v for k, v in m2.items() if k != "signature"}
    elif case == "untrusted_key":
        m2 = sign({k: v for k, v in m2.items() if k != "signature"}, load_signing_key(OTHER_TEST_SEED))
    elif case == "incompatible_schema":
        m2 = sign({**{k: v for k, v in m2.items() if k != "signature"}, "feature_schema_version": "apex-features-0"},
                  demo_key())
    elif case == "pickle":
        a2 = b"\x80\x04\x95cos\nsystem\n(S'echo pwned'\ntR."  # a pickle payload must never be loaded
        m2 = sign({**{k: v for k, v in m2.items() if k != "signature"}, "artifact_size_bytes": len(a2),
                   "artifact_sha256": hashlib.sha256(a2).hexdigest()}, demo_key())
    elif case == "expired":
        m2, a2 = fixture_release(SID, clock.now() - timedelta(days=30), note="v2", validity_days=7)
    elif case == "oversize":
        m2 = sign({**{k: v for k, v in m2.items() if k != "signature"}, "artifact_size_bytes": 5_000_000}, demo_key())
    srv.publish_champion(m2, a2)
    rep = sync.poll()
    code = rep.rejected[m2["model_id"]]
    expected = {"corrupt": "CHECKSUM_MISMATCH", "unsigned": "BAD_SIGNATURE", "untrusted_key": "BAD_SIGNATURE",
                "incompatible_schema": "FEATURE_SCHEMA_INCOMPATIBLE", "pickle": "ARTIFACT_INVALID",
                "expired": "VALIDATION_EXPIRED", "oversize": "SIZE_LIMIT"}[case]
    assert code == expected
    assert sync.active_models()[SID].model_id == m1["model_id"]  # previous model intact and serving
    if case in ("unsigned", "untrusted_key", "incompatible_schema", "expired", "oversize"):
        assert len(_artifact_downloads(srv)) == 1  # refused before downloading anything
    (alert,) = _alerts(local)
    assert alert.route == {"type": "CHANNEL", "channel_key": "system_status"} and alert.payload["payload"]["alert"] == "MODEL_REJECTED"
    assert srv.activations[-1]["status"] == ("expired" if case == "expired" else "rejected")
    # not re-downloaded, not re-alerted on the next poll
    n = len(_artifact_downloads(srv))
    sync.poll()
    assert len(_artifact_downloads(srv)) == n and len(_alerts(local)) == 1


def test_trusted_keys_come_from_the_pi_not_the_server(env):
    srv, clock, make, _local, _state, _tmp = env
    other = load_signing_key(OTHER_TEST_SEED)
    m, a = fixture_release(SID, clock.now(), key=other)
    from apex_worker.agent.crypto import public_from_signing_key

    m["trusted_keys"] = {"1": public_from_signing_key(other)}  # a hostile hint in the response is ignored
    srv.publish_champion(m, a)
    assert make().poll().rejected[m["model_id"]] == "BAD_SIGNATURE"


def test_G_pi_offline_during_release_catches_up_once(env):
    srv, clock, make, local, _state, _tmp = env
    sync = make()
    m1, a1 = fixture_release(SID, clock.now())
    srv.publish_champion(m1, a1)
    sync.poll()
    srv.down = True
    m2, a2 = fixture_release(SID, clock.now(), note="v2")
    srv.publish_champion(m2, a2)  # released while the Pi cannot reach the registry
    for _ in range(3):
        clock.advance(45 * 60)
        rep = sync.poll()
        assert rep.registry_ok is False and sync.active_models()[SID].model_id == m1["model_id"]
    assert sync.consecutive_failures == 3
    srv.down = False
    clock.advance(45 * 60)
    sync.client.offline_until = 0
    assert sync.poll().activated == [m2["model_id"]]
    assert sync.poll().unchanged == [m2["model_id"]]
    acks = [(a["model_id"], a["status"]) for a in srv.activations]
    assert acks == [(m1["model_id"], "activated"), (m2["model_id"], "activated")]
    assert sync.stores[SID].previous_id() == m1["model_id"]  # instant rollback target kept
    assert _alerts(local) == []


def test_H_registry_unavailable_then_restart_revalidates_checksums(env):
    srv, clock, make, _local, _state, tmp = env
    sync = make()
    m1, a1 = fixture_release(SID, clock.now())
    srv.publish_champion(m1, a1)
    sync.poll()
    srv.fail_5xx_next = 100  # Supabase paused / database not ready
    rep = sync.poll()
    assert rep.registry_ok is False and SID in sync.active_models()
    # restart while the registry is still down: the on-disk champion is re-verified from scratch
    fresh = make()
    assert fresh.load_local()[SID].model_id == m1["model_id"]
    # tampered on-disk artifact: fails closed on restart (no model rather than an unverified one)
    path = tmp / "models" / "strategies" / SID / "versions" / m1["model_id"] / "model.json"
    path.write_bytes(path.read_bytes().replace(b'"v1"', b'"v8"'))
    tampered = make()
    assert tampered.load_local() == {}
    # recovery: the registry is back and the champion is reinstalled from a verified download
    srv.fail_5xx_next = 0
    tampered.client.offline_until = 0
    srv.publish_champion(*fixture_release(SID, clock.now(), note="v3"))
    assert tampered.poll().activated and SID in tampered.active_models()


def test_fail_closed_on_expiry_and_expired_ack(env):
    srv, clock, make, _local, _state, _tmp = env
    sync = make()
    m1, a1 = fixture_release(SID, clock.now(), validity_days=2)
    srv.publish_champion(m1, a1)
    sync.poll()
    assert SID in sync.active_models()
    assert [a.model_id for a in sync.expiring(3)] == [m1["model_id"]]
    clock.advance(3 * 86400)
    assert sync.active_models() == {}  # no valid unexpired model: the policy has no forecast at all
    sync.poll()
    assert srv.activations[-1]["status"] in ("expired", "rejected")


def test_post_activation_health_failure_rolls_back_and_quarantines(env, monkeypatch):
    srv, clock, make, local, _state, _tmp = env
    sync = make()
    m1, a1 = fixture_release(SID, clock.now())
    srv.publish_champion(m1, a1)
    sync.poll()
    m2, a2 = fixture_release(SID, clock.now(), note="v2")
    srv.publish_champion(m2, a2)
    monkeypatch.setattr(sync_mod, "health_check", lambda active, rows=None: "probe drift" if active.model_id == m2["model_id"] else None)
    rep = sync.poll()
    assert rep.rolled_back == [m2["model_id"]] and sync.active_models()[SID].model_id == m1["model_id"]
    assert srv.activations[-1]["status"] == "rolled_back"
    assert [a.payload["payload"]["alert"] for a in _alerts(local)] == ["MODEL_ROLLED_BACK"]
    sync.poll()  # quarantined: not reinstalled every poll
    assert len([a for a in srv.activations if a["model_id"] == m2["model_id"]]) == 1


def test_live_mode_refuses_demo_and_synthetic_models(tmp_path):
    clock = Clock()
    srv = server(clock, mode="PAPER_LIVE_DATA")
    sync = ModelSync(client(srv, clock), tmp_path / "m", demo_trusted_keys(), "PAPER_LIVE_DATA",
                     AgentState(tmp_path / "s.sqlite3"), clock=clock.now)
    m, a = fixture_release(SID, clock.now())
    srv.publish_champion(m, a)
    assert sync.poll().rejected[m["model_id"]] == "SYNTHETIC_NOT_ALLOWED"
    with pytest.raises(sync_mod.ManifestRejected):
        preverify(m, demo_trusted_keys(), now_iso=iso_utc(clock.now()), release_mode="PAPER_LIVE_DATA")


def test_offline_sync_without_client_is_explicit(tmp_path):
    clock = Clock()
    sync = ModelSync(None, tmp_path / "m", demo_trusted_keys(), "DEMO", AgentState(":memory:"), clock=clock.now)
    rep = sync.poll()
    assert rep.registry_ok is False and "not connected" in rep.detail
    assert json.dumps(rep.__dict__)
