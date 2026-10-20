import asyncio
import json
from datetime import timedelta

import pytest

from agent_helpers import AGENT, SECRET
from apex_worker.agent.client import AgentClient, PostSpool
from apex_worker.agent.secrets import MarketCredentials
from apex_worker.contracts import validate
from apex_worker.demo.agent_server import FakeAgentServer
from apex_worker.demo.flow import demo_meta
from apex_worker.demo.model_fixtures import demo_trusted_keys, fixture_release
from apex_worker.demo.synthetic import SECTORS, generate
from apex_worker.discord_bot.settings import AlreadyRunning, acquire_instance_lock
from apex_worker.market.adapters.replay import Faults, ReplayAdapter
from apex_worker.market.calendar import default_calendar
from apex_worker.models.sync import ModelSync
from apex_worker.runtime.agent_state import AgentState
from apex_worker.runtime.engine import EngineConfig
from apex_worker.runtime.outbox_bridge import OutboxBridge
from apex_worker.runtime.worker import IEX_SYMBOL_CAP, Schedule, Worker, run_worker
from apex_worker.storage.local import LocalStore
from apex_worker.timeutil import ReplayClock, iso_utc

CAL = default_calendar()
DAY = CAL.session(__import__("datetime").date(2026, 8, 5))  # a DEMO session with PASS candidates


@pytest.fixture(scope="module")
def market():
    return generate()


def make(tmp, market, clock, *, mode="DEMO", faults=None, secrets=None, adapter_factory=None, x25519=None):
    srv = FakeAgentServer(SECRET, mode=mode, clock=lambda: clock.now().timestamp())
    srv.register_device(AGENT, "active", x25519)
    c = AgentClient("https://apex.test", AGENT, SECRET, transport=srv.transport(), spool=PostSpool(tmp / "agent.sqlite3"),
                    clock=lambda: clock.now().timestamp(), sleep=lambda _s: None, rng=lambda: 0.0)
    local = LocalStore(tmp / "apex_local.sqlite3")
    state = AgentState(tmp / "agent.sqlite3")
    sync = ModelSync(c, tmp / "models", demo_trusted_keys(), mode, state, clock=clock.now, notify=local.enqueue)
    w = Worker(release_mode=mode, clock=clock, calendar=CAL, local=local, state=state, state_dir=tmp, model_sync=sync,
               engine_config=EngineConfig(release_mode=mode, benchmark="DMSPY",
                                          sector_etfs={k: v[0] for k, v in SECTORS.items()}),
               meta=demo_meta(market), universe=sorted(market.bars), client=c,
               adapter=ReplayAdapter(market, clock, CAL, faults), bridge=OutboxBridge(c, local), provider="replay",
               feed="demo", secrets=secrets, schedule=Schedule(tick_seconds=0.01), adapter_factory=adapter_factory)
    return w, srv, local


def test_run_once_after_close_scans_publishes_research_only_and_bridges(tmp_path, market):
    clock = ReplayClock(DAY.close_utc + timedelta(minutes=20))
    w, srv, local = make(tmp_path, market, clock)
    out = asyncio.run(w.run_once())
    assert out["end_of_day"]["session"] == "2026-08-05"
    (scan,) = srv.scans.values()
    assert scan["metrics"]["sectors"] and srv.feature_snapshots and scan["is_demo"] is True
    sigs = [s for (_id, _rev), s in srv.signals.items()]
    assert sigs, "a DEMO research setup is published on this session"
    # fail closed: no model -> no probability, never HIGH_CONVICTION
    assert all(s["confidence"]["status"] == "UNVALIDATED" and "p_target_first" not in s["confidence"] for s in sigs)
    assert {s["tier"] for s in sigs} == {"RESEARCH_SETUP"}
    server_keys = {r["event_key"] for r in srv.outbox.values()}
    assert server_keys and server_keys == {r.event_key for r in local.outbox_rows()}  # bridged with the same keys
    hb = srv.heartbeats[-1]
    validate("WorkerHeartbeat", hb)
    assert hb["components"]["model"]["status"] == "UNCONFIGURED" and hb["components"]["scanner"]["status"] == "OK"
    assert set(hb["resources"]) >= {"spool_pending", "clock_synced"}
    assert local.latest_signal(sigs[0]["id"])["state"] == "PUBLISHED"  # the bot can resolve it locally
    # second pass in the same evening: the session is not scanned twice
    assert asyncio.run(w.job_end_of_day(clock.now())) is None and len(srv.scans) == 1


def test_eod_timing_early_close_and_never_after_next_open(tmp_path, market):
    clock = ReplayClock(DAY.close_utc)
    w, _srv, _local = make(tmp_path, market, clock)
    half = CAL.session(__import__("datetime").date(2026, 11, 27))  # day after Thanksgiving: 13:00 ET close
    assert half.early_close
    assert w._eod_day(half.close_utc + timedelta(minutes=10)) is None
    assert w._eod_day(half.close_utc + timedelta(minutes=16)) == half.day
    nxt = CAL.session(CAL.next_session(half.day))
    assert w._eod_day(nxt.open_utc + timedelta(minutes=1)) is None  # stale after the next open


def test_intraday_follows_cadence_and_iex_cap(tmp_path, market):
    clock = ReplayClock(DAY.close_utc + timedelta(minutes=20))
    w, srv, local = make(tmp_path, market, clock)
    asyncio.run(w.run_once())
    nxt = CAL.session(CAL.next_session(DAY.day))
    assert w._intraday_slot(nxt.open_utc + timedelta(minutes=10)) is None
    clock.set(nxt.open_utc + timedelta(minutes=15))
    assert asyncio.run(w.job_intraday(clock.now())) is True
    assert asyncio.run(w.job_intraday(clock.now())) is False  # same slot: no second poll
    assert srv.quotes and local.get_quote(next(iter(srv.quotes)))  # posted and mirrored for the bot
    srv.config["cadence"]["intraday_minutes"] = 30
    srv.config["slots"]["max_slots"] = 40
    w.job_config(clock.now())
    assert w.server.max_slots == IEX_SYMBOL_CAP and len(w._slot_symbols()) <= IEX_SYMBOL_CAP
    clock.set(nxt.open_utc + timedelta(minutes=29))
    assert w._intraday_slot(clock.now()) is None  # first 30-minute slot not reached yet


def test_offline_keeps_running_on_spool_then_replays(tmp_path, market):
    clock = ReplayClock(DAY.close_utc + timedelta(minutes=20))
    w, srv, _local = make(tmp_path, market, clock)
    srv.down = True
    out = asyncio.run(w.run_once())
    assert out["end_of_day"] is not None and srv.scans == {}
    assert w.client.spool.pending_count() >= 2  # scan + signal publish waiting
    assert out["heartbeat"]["server"]["status"] == "DOWN"
    srv.down = False
    clock.advance(timedelta(minutes=10))
    w.client.offline_until = 0
    w.job_heartbeat(clock.now())
    assert w.client.spool.pending_count() == 0 and len(srv.scans) == 1 and srv.signals and not srv.duplicates


def test_server_config_applies_strategy_toggles_and_risk(tmp_path, market):
    clock = ReplayClock(DAY.close_utc + timedelta(minutes=20))
    w, srv, _local = make(tmp_path, market, clock)
    srv.config["strategies"] = {"h1_vcp_breakout": False}
    srv.config["risk"] = {**srv.config["risk"], "min_quality": 82, "research_setups_enabled": False}
    w.job_config(clock.now())
    ids = {s.strategy_id for s in w.engine.candidates.strategies}
    assert "h1_vcp_breakout" not in ids and ids
    assert w.engine.policy.cfg.min_quality == 82 and w.engine.policy.cfg.publish_research_setups is False


def test_models_and_notices_jobs(tmp_path, market):
    clock = ReplayClock(DAY.close_utc + timedelta(minutes=20))
    w, srv, local = make(tmp_path, market, clock)
    m, a = fixture_release("h1_vcp_breakout", clock.now(), validity_days=2)
    srv.publish_champion(m, a)
    old = iso_utc(clock.now() - timedelta(days=10))
    srv.config["training"] = {"last_success_at": old, "last_run_at": old, "last_run_status": "failed",
                              "champions": [{"strategy_id": "h1_vcp_breakout", "model_id": m["model_id"],
                                             "validation_expires_at": m["validation_expires_at"]}]}
    w.start()
    w.job_models(clock.now())
    assert w.engine.models["h1_vcp_breakout"].model_id == m["model_id"]
    w.job_notices(clock.now())
    on_server = sorted(r["intent"]["payload"]["alert"] for r in srv.outbox.values() if r["intent"]["kind"] == "SYSTEM_ALERT")
    assert on_server == ["MISSED_TRAINING", "MODEL_EXPIRING"]  # recorded for the dashboard first
    assert [r for r in local.outbox_rows() if r.kind == "SYSTEM_ALERT"] == []  # not queued locally as well
    w.job_work(clock.now())  # ...and delivered to #system-status through the normal outbox bridge
    alerts = sorted(r.payload["payload"]["alert"] for r in local.outbox_rows() if r.kind == "SYSTEM_ALERT")
    assert alerts == ["MISSED_TRAINING", "MODEL_EXPIRING"]
    assert w.heartbeat_body(clock.now())["components"]["training_schedule"]["status"] == "DEGRADED"


def test_stale_feed_reported_without_crashing(tmp_path, market):
    clock = ReplayClock(DAY.close_utc + timedelta(minutes=20))
    w, _srv, _local = make(tmp_path, market, clock, faults=Faults())
    asyncio.run(w.run_once())
    w.engine.adapter.faults.unavailable = True
    nxt = CAL.session(CAL.next_session(DAY.day))
    clock.set(nxt.open_utc + timedelta(minutes=15))
    asyncio.run(w.job_intraday(clock.now()))
    assert w.status["market_feed"]["status"] in ("DOWN", "STALE")


def test_heartbeat_never_contains_credentials(tmp_path, market):
    class Held:
        version, status, detail = 2, "OK", "version 2 held in memory"
        credentials = MarketCredentials("TESTKEYID0000", "TEST-ONLY-super-secret-value", 2, "envelope")

        def maybe_refresh(self, *a, **k):
            return False

    clock = ReplayClock(DAY.close_utc + timedelta(minutes=20))
    w, srv, _local = make(tmp_path, market, clock, secrets=Held())
    w.job_heartbeat(clock.now())
    text = json.dumps(srv.heartbeats[-1])
    assert "TEST-ONLY-super-secret-value" not in text and "TESTKEYID0000" not in text
    assert srv.heartbeats[-1]["secret_version_ack"] == 2


async def test_stop_during_a_long_job_cancels_after_grace(tmp_path, market):
    clock = ReplayClock(DAY.close_utc + timedelta(minutes=20))
    w, _srv, _local = make(tmp_path, market, clock)
    w.schedule.stop_grace_seconds = 0.05
    started = asyncio.Event()

    async def slow_eod(now):
        started.set()
        await asyncio.sleep(3600)

    w.job_end_of_day = slow_eod
    stop = asyncio.Event()
    task = asyncio.create_task(w.run(stop))
    await asyncio.wait_for(started.wait(), timeout=30)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert w.state.get("last_scan_session") is None  # the interrupted scan is not marked done


async def test_sigterm_style_stop_ends_loop(tmp_path, market):
    clock = ReplayClock(DAY.close_utc + timedelta(minutes=20))
    w, _srv, _local = make(tmp_path, market, clock)
    w.state.put("last_scan_session", DAY.day.isoformat())  # nothing long pending: only the periodic jobs run
    stop = asyncio.Event()
    task = asyncio.create_task(w.run(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=30)
    assert task.done() and task.exception() is None


@pytest.fixture
def worker_env(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("APEX_ENV_FILE", str(tmp_path / "missing.env"))
    for k in ("APEX_API_BASE_URL", "APEX_AGENT_ID", "AGENT_HMAC_SECRET", "ALPACA_KEY_ID", "ALPACA_SECRET_KEY",
              "APEX_MODEL_TRUSTED_KEYS", "APEX_WORKER_OFFLINE", "APEX_MODE"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def test_exit_code_2_for_configuration_errors(worker_env, monkeypatch, caplog):
    assert run_worker(once=True) == 2  # not paired
    assert "apex pair CODE --server" in caplog.text
    monkeypatch.setenv("APEX_WORKER_OFFLINE", "1")
    monkeypatch.setenv("APEX_MODEL_TRUSTED_KEYS", '{"1": "not-a-key"}')
    caplog.clear()
    assert run_worker(once=True) == 2 and "APEX_MODEL_TRUSTED_KEYS" in caplog.text
    monkeypatch.delenv("APEX_MODEL_TRUSTED_KEYS")
    monkeypatch.setenv("AGENT_HMAC_SECRET", "dG9vLXNob3J0")
    caplog.clear()
    assert run_worker(once=True) == 2 and "apex keygen hmac" in caplog.text
    monkeypatch.setenv("AGENT_HMAC_SECRET", __import__("base64").b64encode(SECRET).decode())
    monkeypatch.setenv("APEX_API_BASE_URL", "https://apex.test")
    monkeypatch.setenv("APEX_AGENT_ID", AGENT)
    caplog.clear()
    assert run_worker(once=True) == 2 and "device_keys.json is missing" in caplog.text
    assert "dG9vLXNob3J0" not in caplog.text and SECRET.decode() not in caplog.text


def test_single_instance_lock_and_once_offline(worker_env, monkeypatch, capsys):
    tmp_path = worker_env
    monkeypatch.setenv("APEX_WORKER_OFFLINE", "1")
    lock = acquire_instance_lock(tmp_path, "worker.lock")
    with pytest.raises(AlreadyRunning):
        acquire_instance_lock(tmp_path, "worker.lock")
    assert run_worker(once=True) == 3
    lock.release()
    assert run_worker(once=True) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["heartbeat"]["server"]["status"] == "DISABLED"  # not paired: offline local mode, explicit
    assert (tmp_path / "apex_local.sqlite3").exists()  # the same file the bot delivers from


def test_secret_rotation_announced_in_heartbeat_rebuilds_the_adapter(tmp_path, market):
    from apex_worker.agent.crypto import DeviceKeys
    from apex_worker.agent.secrets import SecretManager

    clock = ReplayClock(DAY.close_utc + timedelta(minutes=20))
    keys = DeviceKeys.generate()
    built = []
    w, srv, _local = make(tmp_path, market, clock, x25519=keys.x25519_public,
                          adapter_factory=lambda creds, budgets: built.append(creds.version) or w.engine.adapter)
    w.secrets = SecretManager(w.client, keys, clock=clock.now)
    srv.set_integration("alpaca", {"key_id": "TESTKEYID000000000000", "secret_key": "TEST-ONLY-one"}, 1)
    w.job_config(clock.now())
    assert w._ensure_adapter() and built == [1]
    w.job_heartbeat(clock.now())
    assert built == [1]  # same version announced: no refetch
    srv.set_integration("alpaca", {"key_id": "TESTKEYID000000000001", "secret_key": "TEST-ONLY-two"}, 2)
    w.job_heartbeat(clock.now())
    assert built == [1, 2] and w.secrets.version == 2 and srv.secret_acks[-1] == ("alpaca", 2)
    assert "TEST-ONLY-two" not in json.dumps(srv.heartbeats)

