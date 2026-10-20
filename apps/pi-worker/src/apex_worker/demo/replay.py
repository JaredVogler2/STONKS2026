"""`apex replay --all | --scenario NAME`: deterministic, keyless, offline reliability scenarios.

Every scenario drives the PRODUCTION code paths (engine, lifecycle monitor, sinks, ledger, bot delivery, outbox
bridge, signed agent client, model sync) over DEMO fixtures: fictional DM* tickers, handcrafted bars or the
synthetic market, the in-process fake agent API and DEMO-signed fixture models. Each scenario asserts its outcomes
and writes `<out>/<name>.json` (default `<state_dir>/replay/`). Nothing touches the network, Discord or a broker.
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import tempfile
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from apex_worker.market.adapters.base import LatestPrint, ProviderHealth
from apex_worker.market.calendar import default_calendar
from apex_worker.notifications import intents
from apex_worker.timeutil import ReplayClock, iso_utc

CAL = default_calendar()
SECRET = b"TEST-ONLY-replay-agent-secret-000"  # fake bootstrap HMAC secret used only by the in-process fake API
AGENT = "agt_replaydemo01"
SYMBOL = "DMSEMI1"
DAY0 = date(2026, 9, 1)  # decision session for the handcrafted lifecycle scenarios
LEVELS = {"entry_min": "100.00", "entry_max": "102.00", "stop": "96.50", "target": "110.00"}
MEMBER_A, MEMBER_B = "100000000000000001", "100000000000000002"


# ---- result bookkeeping ------------------------------------------------------------------------------------------
@dataclass
class Result:
    name: str
    checks: list[dict] = field(default_factory=list)
    details: dict = field(default_factory=dict)
    summary: str = ""

    def check(self, ok: bool, label: str) -> bool:
        self.checks.append({"ok": bool(ok), "check": label})
        return bool(ok)

    @property
    def failures(self) -> list[str]:
        return [c["check"] for c in self.checks if not c["ok"]]


@dataclass
class Ctx:
    tmp: Path


# ---- fixtures ------------------------------------------------------------------------------------------------------
def session_after(day: date, n: int) -> date:
    return CAL.add_sessions(day, n)


def close_plus(day: date, minutes: int = 15) -> datetime:
    return CAL.session(day).close_utc + timedelta(minutes=minutes)


def make_bars(rows: list[tuple[float, float, float, float]], start: date = DAY0, history: int = 30) -> pd.DataFrame:
    """`history` quiet sessions ending at `start` (close 101), then the given (o, h, l, c) rows on following sessions."""
    days = [start]
    for _ in range(history - 1):
        days.insert(0, CAL.previous_session(days[0]))
    data = [(100.5, 101.5, 99.8, 101.0)] * len(days)
    for row in rows:
        days.append(CAL.next_session(days[-1]))
        data.append(row)
    idx = pd.DatetimeIndex([pd.Timestamp(x) for x in days])
    return pd.DataFrame(data, index=idx, columns=["open", "high", "low", "close"]).assign(volume=1_000_000)


class FixtureAdapter:
    """MarketDataAdapter over fixed frames; never returns bars after the clock's latest completed session."""

    provider = "replay"

    def __init__(self, clock: ReplayClock, bars: dict[str, pd.DataFrame], prints: dict[str, LatestPrint] | None = None):
        self.clock, self.bars, self.prints = clock, bars, prints or {}

    async def get_daily_bars(self, symbols, start, end, adjustment="split", feed="iex"):
        last = pd.Timestamp(CAL.latest_completed_session(self.clock.now()))
        return {s: self.bars[s].loc[:last] for s in symbols if s in self.bars}

    async def get_latest(self, symbols, feed="iex"):
        return {s: self.prints[s] for s in symbols if s in self.prints}

    async def get_assets(self):
        return []

    async def get_intraday_bars(self, *a, **k):
        return {}

    async def get_corporate_actions(self, *a, **k):
        return None

    def health(self) -> ProviderHealth:
        return ProviderHealth("replay", "OK", "demo")


def fixture_signal(now: datetime, seed: str) -> dict:
    from apex_worker.discord_bot.samples import sample_signal

    return sample_signal(now, symbol=SYMBOL, seed=seed, entry_min=LEVELS["entry_min"], entry_max=LEVELS["entry_max"],
                         stop=LEVELS["stop"], target=LEVELS["target"])


class Lifecycle:
    """LocalStore + LocalSink + ApexEngine + dry-run Discord around one published fixture signal."""

    def __init__(self, tmp: Path, rows: list[tuple[float, float, float, float]], seed: str):
        from apex_worker.discord_bot.dryrun import DryRunDiscord
        from apex_worker.runtime.engine import ApexEngine, EngineConfig
        from apex_worker.runtime.local_sink import LocalSink
        from apex_worker.storage.local import LocalStore

        self.clock = ReplayClock(close_plus(DAY0))
        self.store = LocalStore(tmp / f"{seed}.sqlite3")
        self.sink = LocalSink(self.store)
        self.adapter = FixtureAdapter(self.clock, {SYMBOL: make_bars(rows)})
        self.engine = ApexEngine(self.adapter, self.sink, self.clock, EngineConfig(release_mode="DEMO"), calendar=CAL)
        self.discord = DryRunDiscord(self.store, b"replay-component-secret-not-for-production")
        self.signal = fixture_signal(self.clock.now(), seed)
        self.sink.publish_signal(self.signal, "RESEARCH_SETUP", intents.new_signal(self.signal, "RESEARCH_SETUP",
                                                                                   self.clock.now()), self.clock.now())
        self.discord.deliver_due(self.clock.now())

    def run_sessions(self, n: int) -> None:
        for i in range(1, n + 1):
            self.clock.set(close_plus(session_after(DAY0, i)))
            asyncio.run(self.engine.refresh_daily([SYMBOL]))
            self.engine.evaluate_signals_daily()
            self.discord.deliver_due(self.clock.now())

    def events(self) -> list[dict]:
        return self.store.signal_events(self.signal["id"])

    def state(self) -> str | None:
        return self.store.signal_state(self.signal["id"])

    def kinds(self) -> list[str]:
        return [t["kind"] for t in self.discord.transcript]


@functools.cache
def synthetic_scan_inputs():
    """Synthetic DEMO market panel (cached per process) + a session known to have PASS candidates."""
    from apex_worker.demo.synthetic import generate
    from apex_worker.features.panel import panel_from_synthetic

    return panel_from_synthetic(generate(), lookback=420), pd.Timestamp("2026-08-05")


def fake_api(clock: Callable[[], float], *, mode: str = "DEMO"):
    from apex_worker.demo.agent_server import FakeAgentServer

    server = FakeAgentServer(SECRET, mode=mode, clock=clock)
    server.register_device(AGENT, "active")
    return server


def agent_client(server, *, spool_path: Path | None = None, clock: Callable[[], float] | None = None):
    from apex_worker.agent.client import AgentClient, PostSpool

    return AgentClient("https://apex.test", AGENT, SECRET, transport=server.transport(),
                       spool=PostSpool(spool_path) if spool_path else None, clock=clock or server.clock,
                       sleep=lambda _s: None, rng=lambda: 0.0)


# ---- scenarios -----------------------------------------------------------------------------------------------------
def s_normal_publish_activation_target(ctx: Ctx, r: Result) -> None:
    lc = Lifecycle(ctx.tmp, [(101.5, 103.0, 100.5, 102.5), (103.0, 106.0, 102.0, 105.0), (106.0, 111.0, 105.0, 110.5)],
                   "normal")
    lc.run_sessions(3)
    states = [e["to_state"] for e in lc.events()]
    r.check(states[:4] == ["DISCOVERED", "EVALUATING", "QUALIFIED", "PUBLISHED"], "append-only transition chain to PUBLISHED")
    r.check(states[4:] == ["ACTIVE", "CLOSED"], "PUBLISHED -> ACTIVE -> CLOSED")
    r.check(lc.events()[-1]["payload"].get("outcome") == "TARGET", "outcome TARGET (target before stop)")
    r.check(lc.kinds() == ["NEW_SIGNAL", "SIGNAL_UPDATE", "SIGNAL_CLOSED"], "one channel message per lifecycle step")
    r.check(all(t["target"].startswith("channel:") for t in lc.discord.transcript), "signal alerts go to channels only")
    r.details = {"events": [e["event_type"] for e in lc.events()], "messages": lc.kinds()}
    r.summary = "published, activated at a fill inside the entry zone, closed at TARGET"


def s_same_bar_stop_first(ctx: Ctx, r: Result) -> None:
    lc = Lifecycle(ctx.tmp, [(101.5, 102.5, 100.5, 101.0), (101.0, 111.0, 96.0, 100.0)], "samebar")
    lc.run_sessions(2)
    last = lc.events()[-1]
    r.check(last["to_state"] == "CLOSED" and last["payload"].get("outcome") == "STOP", "ambiguous bar resolves STOP-first")
    r.check(last["payload"].get("ambiguous_same_bar") is True, "ambiguity is recorded, not hidden")
    r.details = {"cause": last["cause"], "payload": last["payload"]}
    r.summary = "bar touched both target and stop: conservative STOP-first outcome, flagged ambiguous"


def s_gap_through_stop(ctx: Ctx, r: Result) -> None:
    lc = Lifecycle(ctx.tmp, [(101.5, 102.5, 100.5, 101.0), (94.0, 95.0, 93.0, 94.5)], "gap")
    lc.run_sessions(2)
    p = lc.events()[-1]["payload"]
    r.check(p.get("outcome") == "STOP" and p.get("gap_through_stop") is True, "overnight gap below the stop detected")
    r.check(float(p.get("exit_price_modeled", 99)) < float(LEVELS["stop"]), "exit modeled at the open, below the stop")
    r.details = {"payload": p}
    r.summary = f"gap open 94.00 < stop {LEVELS['stop']}: exit modeled at {p.get('exit_price_modeled')} (not the stop)"


def s_entry_never_filled(ctx: Ctx, r: Result) -> None:
    lc = Lifecycle(ctx.tmp, [(105.0, 107.0, 103.5, 106.0), (106.0, 108.0, 104.0, 107.0), (107.0, 108.0, 105.0, 107.5)],
                   "nofill")
    lc.run_sessions(3)
    last = lc.events()[-1]
    r.check(lc.state() == "EXPIRED" and last["payload"].get("outcome") == "ENTRY_EXPIRED", "PUBLISHED -> EXPIRED")
    r.check("ACTIVE" not in [e["to_state"] for e in lc.events()], "never activated (no phantom fill)")
    r.check(lc.kinds()[-1] == "SIGNAL_CLOSED", "members told the entry window closed")
    r.summary = "price stayed above the entry zone for the whole entry window: EXPIRED, no theoretical fill"


def s_earnings_veto(ctx: Ctx, r: Result) -> None:
    from apex_worker.signals.candidates import CandidateEngine

    panel, day = synthetic_scan_inputs()
    base = CandidateEngine(calendar=CAL).scan(panel, day, [], lambda s, d, h: "NONE_WITHIN_HORIZON_CONFIRMED")
    passing = [c for c in base.candidates if c["gate_status"] == "PASS"]
    r.check(bool(passing), "baseline scan has PASS candidates")
    target = passing[0]["symbol"] if passing else ""
    vetoed = CandidateEngine(calendar=CAL).scan(
        panel, day, [], lambda s, d, h: "WITHIN_HORIZON" if s == target else "NONE_WITHIN_HORIZON_CONFIRMED")
    mine = [c for c in vetoed.candidates if c["symbol"] == target]
    r.check(not [c for c in mine if c["gate_status"] == "PASS"], f"{target} no longer passes the gates")
    r.check(vetoed.stats.vetoes.get("EARNINGS_WITHIN_HORIZON", 0) >= 1, "veto reason EARNINGS_WITHIN_HORIZON counted")
    others = {c["symbol"] for c in vetoed.candidates if c["gate_status"] == "PASS"}
    r.check(others == {c["symbol"] for c in passing} - {target}, "other candidates unaffected")
    r.details = {"symbol": target, "session": str(day.date()), "vetoes": dict(vetoed.stats.vetoes)}
    r.summary = f"scheduled earnings inside the holding window vetoed {target}"


def s_stale_feed(ctx: Ctx, r: Result) -> None:
    from apex_worker.models.sync import ModelSync
    from apex_worker.runtime.agent_state import AgentState
    from apex_worker.runtime.engine import EngineConfig
    from apex_worker.runtime.worker import Worker
    from apex_worker.storage.local import LocalStore

    lc = Lifecycle(ctx.tmp, [(101.5, 102.5, 100.5, 101.0)], "stale")
    lc.run_sessions(1)
    r.check(lc.state() == "ACTIVE", "signal is ACTIVE before the outage")
    day = session_after(DAY0, 2)
    clock = ReplayClock(CAL.session(day).open_utc)
    old = CAL.session(session_after(DAY0, 1)).close_utc  # the last print is from the previous close
    adapter = FixtureAdapter(clock, {}, {SYMBOL: LatestPrint(SYMBOL, 101.0, 100, old, "demo")})
    state = AgentState(ctx.tmp / "stale_state.sqlite3")
    store: LocalStore = lc.store
    w = Worker(release_mode="DEMO", clock=clock, calendar=CAL, local=store, state=state, state_dir=ctx.tmp,
               model_sync=ModelSync(None, ctx.tmp / "models", {}, "DEMO", state, clock=clock.now),
               engine_config=EngineConfig(release_mode="DEMO"), meta={}, universe=[], adapter=adapter, feed="demo")
    before = len(store.outbox_rows())
    for minutes in (16, 31):
        clock.set(CAL.session(day).open_utc + timedelta(minutes=minutes))
        asyncio.run(w.job_intraday(clock.now()))
    rows = store.outbox_rows()[before:]
    stale_events = [e for e in lc.events() if e["event_type"] == "STALE_DATA"]
    r.check(bool(stale_events), "STALE_DATA annotations recorded on the signal")
    r.check(not [x for x in rows if x.kind in ("NEW_SIGNAL", "SIGNAL_UPDATE", "SIGNAL_CLOSED")], "no new member alerts")
    notices = [x for x in rows if x.kind == "SYSTEM_ALERT" and x.payload["payload"]["alert"] == "STALE_DATA"]
    r.check(len(notices) == 1 and notices[0].route == {"type": "CHANNEL", "channel_key": "system_status"},
            "exactly one private #system-status STALE_DATA notice (after hysteresis)")
    r.check(w.status["market_feed"]["status"] == "STALE", "feed health reported STALE")
    r.summary = "quotes older than the 120 s SLA: annotations only, no price alerts, one system notice"


def s_dm_blocked_private(ctx: Ctx, r: Result) -> None:
    from apex_worker.discord_bot.backend import LocalMemberBackend
    from apex_worker.discord_bot.delivery import CHANNEL_KEYS, ChannelMap, MemoryTransport, OutboxDelivery
    from apex_worker.discord_bot.samples import sample_assessment
    from apex_worker.runtime.outbox_bridge import OutboxBridge
    from apex_worker.storage.local import LocalStore

    now = close_plus(DAY0)
    server = fake_api(now.timestamp)
    server.set_dm_opt_in(MEMBER_B, True)  # opted in on the server, but Discord refuses the DM
    pos = server.positions.record(MEMBER_B, {"client_idempotency_key": "replay-b-open", "event_type": "OPEN",
                                             "symbol": SYMBOL, "source": "replay",
                                             "fill": {"price": "101", "quantity": "5", "fees": "0",
                                                      "fill_at": iso_utc(now - timedelta(hours=3))}}, now).position
    uid = server.member_id(MEMBER_B)
    a = {**sample_assessment(pos["position_id"], SYMBOL, now), "price_status": "FRESH_VERIFIED"}
    client = agent_client(server)
    client.post("positions/assessments", {"items": [{"discord_user_id": MEMBER_B, "assessment": a,
                                                      "assessment_key": "replay:" + a["asof_at"],
                                                      "intent": intents.position_update(uid, MEMBER_B, a, now, True)}]})
    store = LocalStore(ctx.tmp / "bot.sqlite3")
    bridge = OutboxBridge(client, store)
    bridge.ingest(client.get("work").get("outbox"), now)
    local = store.outbox_rows()
    r.check(len(local) == 1 and local[0].route["type"] == "DM", "leased row stays a DM in the bot's queue")
    transport = MemoryTransport(dm_forbidden={MEMBER_B})
    members = LocalMemberBackend(store)
    channels = ChannelMap(dict.fromkeys(CHANNEL_KEYS, 100000000000000001), "replay")
    asyncio.run(OutboxDelivery(store, transport, b"replay-secret", channels, members, clock=lambda: now).deliver_due(now))
    bridge.ack_pass(now)
    srv = next(iter(server.outbox.values()))
    r.check(store.outbox_rows()[0].state == "deferred", "DM refused -> held privately (deferred)")
    r.check(not transport.sent, "nothing was posted to any channel")
    r.check(srv["state"] == "deferred", "server acked dm_blocked (deferred, never re-routed)")
    shown = members.release_private(store.member_id(MEMBER_B) or "", now)
    bridge.ack_pass(now)
    r.check(len(shown) == 1 and srv["state"] == "sent", "shown in the member's next ephemeral reply, then acked delivered")
    r.summary = "blocked DM held privately, never posted to a channel; delivered ephemerally on next interaction"


def s_duplicate_interaction(ctx: Ctx, r: Result) -> None:
    from apex_worker.discord_bot.backend import LocalPositionBackend
    from apex_worker.discord_bot.remote_backend import RemotePositionBackend
    from apex_worker.storage.local import LocalStore

    now = close_plus(DAY0)
    ev = {"client_idempotency_key": "discord:1300000000000000001", "event_type": "OPEN", "symbol": SYMBOL,
          "source": "discord", "fill": {"price": "101", "quantity": "10", "fees": "0",
                                        "fill_at": iso_utc(now - timedelta(hours=2))}}
    local = LocalPositionBackend(LocalStore(ctx.tmp / "dup.sqlite3"), is_demo=True)
    first, second = local.record(MEMBER_A, ev, now), local.record(MEMBER_A, ev, now)
    r.check((first.status, second.status) == ("persisted", "duplicate"), "local: second click is a duplicate")
    r.check(second.position["event_count"] == 1, "local: exactly one fill recorded")
    server = fake_api(now.timestamp)
    remote = RemotePositionBackend(agent_client(server))
    a, b = remote.record(MEMBER_A, ev, now), remote.record(MEMBER_A, ev, now)
    r.check((a.status, b.status) == ("persisted", "duplicate") and b.position["event_count"] == 1,
            "cloud: idempotency key dedupes the replayed interaction")
    spool = LocalStore(ctx.tmp / "spool.sqlite3")
    puts = [spool.spool_put("discord_position_event", ev["client_idempotency_key"], {"event": ev}, now) for _ in range(2)]
    r.check(puts == [True, False] and spool.spool_pending_count() == 1, "offline queue holds the interaction once")
    r.summary = "double-clicked Record My Entry: one fill locally, in the cloud and in the offline queue"


def s_oversell_rejected(ctx: Ctx, r: Result) -> None:
    from apex_worker.discord_bot.backend import LocalPositionBackend
    from apex_worker.discord_bot.remote_backend import RemotePositionBackend
    from apex_worker.portfolio.ledger import LedgerError
    from apex_worker.storage.local import LocalStore

    now = close_plus(DAY0)
    fill_at = iso_utc(now - timedelta(hours=2))
    server = fake_api(now.timestamp)
    for label, backend in (("local", LocalPositionBackend(LocalStore(ctx.tmp / "os.sqlite3"), is_demo=True)),
                           ("cloud", RemotePositionBackend(agent_client(server)))):
        pos = backend.record(MEMBER_A, {"client_idempotency_key": f"os-open-{label}", "event_type": "OPEN",
                                        "symbol": SYMBOL, "source": "discord",
                                        "fill": {"price": "101", "quantity": "10", "fees": "0", "fill_at": fill_at}},
                             now).position
        try:
            backend.record(MEMBER_A, {"client_idempotency_key": f"os-sell-{label}", "event_type": "PARTIAL_EXIT",
                                      "position_id": pos["position_id"], "source": "discord",
                                      "fill": {"price": "105", "quantity": "15", "fees": "0", "fill_at": fill_at}}, now)
            code = None
        except LedgerError as exc:
            code = exc.code
        after = backend.position(MEMBER_A, pos["position_id"])
        r.check(code == "OVERSELL", f"{label}: selling 15 of 10 shares rejected with OVERSELL")
        r.check(after["total_quantity"] == "10" and after["event_count"] == 1, f"{label}: position unchanged")
    r.summary = "oversell rejected by the ledger (local and cloud) with nothing persisted"


def _sync(ctx: Ctx, server, clock: ReplayClock, name: str):
    from apex_worker.demo.model_fixtures import demo_trusted_keys
    from apex_worker.models.sync import ModelSync
    from apex_worker.runtime.agent_state import AgentState
    from apex_worker.storage.local import LocalStore

    store = LocalStore(ctx.tmp / f"{name}-bot.sqlite3")
    state = AgentState(ctx.tmp / f"{name}-state.sqlite3")
    sync = ModelSync(agent_client(server, clock=lambda: clock.now().timestamp()), ctx.tmp / f"{name}-models",
                     demo_trusted_keys(), "DEMO", state, clock=clock.now, notify=store.enqueue)
    return sync, store


def s_corrupt_model_rejected(ctx: Ctx, r: Result) -> None:
    from apex_worker.demo.model_fixtures import fixture_release

    clock = ReplayClock(close_plus(DAY0))
    server = fake_api(lambda: clock.now().timestamp())
    sync, store = _sync(ctx, server, clock, "corrupt")
    m1, a1 = fixture_release("h1_vcp_breakout", clock.now(), note="v1")
    server.publish_champion(m1, a1)
    r.check(sync.poll().activated == [m1["model_id"]], "v1 verified and activated")
    m2, a2 = fixture_release("h1_vcp_breakout", clock.now(), note="v2")
    server.publish_champion(m2, a2.replace(b'"v2"', b'"v9"'))  # bytes no longer match the signed sha256
    rep2 = sync.poll()
    m3, a3 = fixture_release("h1_vcp_breakout", clock.now(), note="v3")
    unsigned = {k: v for k, v in m3.items() if k != "signature"}
    server.publish_champion(unsigned, a3)
    downloads_before = sum(1 for m, p, _s in server.log if p.endswith("/artifact"))
    rep3 = sync.poll()
    downloads_after = sum(1 for m, p, _s in server.log if p.endswith("/artifact"))
    active = sync.active_models().get("h1_vcp_breakout")
    r.check(rep2.rejected.get(m2["model_id"]) == "CHECKSUM_MISMATCH", "corrupt artifact refused (CHECKSUM_MISMATCH)")
    r.check(rep3.rejected.get(m3["model_id"]) == "BAD_SIGNATURE", "unsigned manifest refused (BAD_SIGNATURE)")
    r.check(downloads_after == downloads_before, "unsigned release refused BEFORE any download")
    r.check(active is not None and active.model_id == m1["model_id"], "previous model intact and still serving")
    alerts = [x for x in store.outbox_rows() if x.kind == "SYSTEM_ALERT"]
    r.check(len(alerts) == 2 and all(x.route["channel_key"] == "system_status" for x in alerts),
            "one private #system-status alert per refused model")
    r.check({a["status"] for a in server.activations} == {"activated", "rejected"}, "registry told: activated / rejected")
    r.summary = "tampered and unsigned releases refused; the verified previous model kept serving"


def s_model_expired_unvalidated(ctx: Ctx, r: Result) -> None:
    from apex_worker.demo.model_fixtures import demo_trusted_keys, fixture_artifact, fixture_release
    from apex_worker.models.store import ModelStore
    from apex_worker.models.sync import ModelSync
    from apex_worker.runtime.agent_state import AgentState
    from apex_worker.signals.candidates import CandidateEngine
    from apex_worker.signals.policy import SignalPolicy

    panel, day = synthetic_scan_inputs()
    scan = CandidateEngine(calendar=CAL).scan(panel, day)
    sid = next(c["strategy_id"] for c in scan.candidates if c["gate_status"] == "PASS")
    t0 = CAL.session(day.date()).close_utc + timedelta(minutes=15)
    clock = ReplayClock(t0)
    man, art = fixture_release(sid, t0, note="exp", validity_days=3, artifact=fixture_artifact("exp", target_bias=2.0))
    state = AgentState(ctx.tmp / "exp-state.sqlite3")
    sync = ModelSync(None, ctx.tmp / "exp-models", demo_trusted_keys(), "DEMO", state, clock=clock.now)
    ModelStore(ctx.tmp / "exp-models" / "strategies" / sid, demo_trusted_keys(), "DEMO").install(man, art, iso_utc(t0))
    policy = SignalPolicy(calendar=CAL)

    def decide(models):
        return policy.decide(scan, models=models, release_mode="DEMO", quotes={}, open_signals=[], now=clock.now())

    valid = decide(sync.load_local())
    tiers_valid = [d.outcome for d in valid.decisions if d.signal]
    r.check(any(s["confidence"]["status"] != "UNVALIDATED" for s in valid.signals), "valid model: calibrated forecast")
    clock.set(t0 + timedelta(days=4))  # past validation_expires_at
    expired_models = sync.load_local()
    expired = decide(expired_models)
    r.check(expired_models == {}, "expired model is not offered to the policy (fail closed)")
    r.check(all(d.outcome != "HIGH_CONVICTION" for d in expired.decisions), "no HIGH_CONVICTION after expiry")
    r.check(all(s["confidence"]["status"] == "UNVALIDATED" and "p_target_first" not in s["confidence"]
                for s in expired.signals), "only UNVALIDATED research setups, no probability shown")
    r.details = {"strategy": sid, "tiers_valid": tiers_valid, "tiers_expired": [d.outcome for d in expired.decisions
                                                                                  if d.signal]}
    r.summary = f"{sid}: forecasts while valid; after expiry only UNVALIDATED research setups"


def s_pi_offline_spool_replay(ctx: Ctx, r: Result) -> None:
    from apex_worker.runtime.agent_state import AgentState
    from apex_worker.runtime.remote_sink import RemoteSink
    from apex_worker.signals.candidates import ScanResult, ScanStats
    from apex_worker.signals.lifecycle import SignalState, transition
    from apex_worker.signals.policy import PolicyOutcome

    clock = ReplayClock(close_plus(DAY0))
    server = fake_api(lambda: clock.now().timestamp())
    client = agent_client(server, spool_path=ctx.tmp / "spool.sqlite3", clock=lambda: clock.now().timestamp())
    sink = RemoteSink(client, AgentState(ctx.tmp / "spool.sqlite3"), release_mode="DEMO", provider="replay", feed="demo")
    sig = fixture_signal(clock.now(), "offline")
    server.down = True
    now = clock.now()
    sink.publish_signal(sig, "RESEARCH_SETUP", intents.new_signal(sig, "RESEARCH_SETUP", now), now)
    sink.publish_signal(sig, "RESEARCH_SETUP", intents.new_signal(sig, "RESEARCH_SETUP", now), now)  # restart replay
    act = transition(sig["id"], SignalState.PUBLISHED, SignalState.ACTIVE, at=now, cause="theoretical entry",
                     discriminator="ENTRY").to_contract()
    sink.append_signal_events(sig, [act], [i for i in [intents.signal_event(sig, act, now)] if i], now)
    scan = ScanResult(DAY0.isoformat(), iso_utc(CAL.session(DAY0).close_utc), [], ScanStats(), {"label": "unknown"},
                      "NO_QUALIFIED_CANDIDATE")
    sink.record_scan(scan, PolicyOutcome(DAY0.isoformat(), no_trade_reason="NO_QUALIFIED_CANDIDATE"), now)
    pending = client.spool.pending_count()
    r.check(pending == 3, f"6-hour outage: 3 writes spooled locally, duplicate publish collapsed (got {pending})")
    clock.advance(timedelta(hours=6))
    server.down = False
    client.offline_until = 0
    counts = client.flush_spool()
    again = client.flush_spool()
    r.check(counts["sent"] == 3 and again["sent"] == 0 and client.spool.pending_count() == 0, "replayed once, in order")
    r.check(server.heads[sig["id"]]["state"] == "ACTIVE", "server state machine accepted the ordered replay")
    r.check(len(server.signals) == 1 and len(server.scans) == 1, "exactly one signal and one scan on the server")
    keys = [row["event_key"] for row in server.outbox.values()]
    r.check(len(keys) == len(set(keys)) == 2, "two notifications (new signal + activation), none duplicated")
    res = client.post("signals", {"signal": sig, "tier": "RESEARCH_SETUP", "events": [], "intent": None})
    r.check(res.data == {"created": False} and len(server.signals) == 1, "late duplicate POST is a server no-op")
    r.details = {"server_log": [f"{m} {p} {s}" for m, p, s in server.log]}
    r.summary = "writes spooled during the outage replayed in order on reconnect, nothing duplicated"


SCENARIOS: dict[str, Callable[[Ctx, Result], None]] = {
    "normal_publish_activation_target": s_normal_publish_activation_target,
    "same_bar_stop_first": s_same_bar_stop_first,
    "gap_through_stop": s_gap_through_stop,
    "entry_never_filled_expired": s_entry_never_filled,
    "earnings_veto": s_earnings_veto,
    "stale_feed_no_alerts": s_stale_feed,
    "dm_blocked_held_privately": s_dm_blocked_private,
    "duplicate_discord_interaction": s_duplicate_interaction,
    "oversell_rejected": s_oversell_rejected,
    "corrupt_or_unsigned_model_rejected": s_corrupt_model_rejected,
    "model_expired_unvalidated_only": s_model_expired_unvalidated,
    "pi_offline_spool_replay": s_pi_offline_spool_replay,
}


def _default_out() -> Path:
    from apex_worker.config import DEFAULT_STATE

    return Path(os.environ.get("APEX_STATE_DIR") or DEFAULT_STATE) / "replay"


def run_scenario(name: str, out_dir: Path | None = None) -> dict:
    fn = SCENARIOS[name]
    res = Result(name)
    out = Path(out_dir) if out_dir else _default_out()
    out.mkdir(parents=True, exist_ok=True)
    # scratch stores live under the report dir: the systemd sandbox only allows writes inside APEX_STATE_DIR
    with tempfile.TemporaryDirectory(prefix=f".work-{name}-", dir=out) as tmp:
        try:
            fn(Ctx(Path(tmp)), res)
        except Exception as exc:  # a crash is a failed scenario with its traceback in the report
            res.check(False, f"crashed: {type(exc).__name__}: {exc}")
            res.details["traceback"] = traceback.format_exc()[-2000:]
    report = {"name": name, "passed": bool(res.checks) and not res.failures, "summary": res.summary,
              "failures": res.failures, "checks": res.checks, "details": res.details, "is_demo": True,
              "warning": "DEMO fixtures only: fictional tickers and synthetic prices.",
              "generated_at": iso_utc(datetime.now(UTC).replace(microsecond=0))}
    path = out / f"{name}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    report["report"] = str(path)
    return report


def run_all(out_dir: Path | None = None) -> list[dict]:
    return [run_scenario(name, out_dir) for name in SCENARIOS]
