"""`apex worker`: the single-process market/monitoring scheduler on the Raspberry Pi.

Driven by the XNYS calendar (holidays, half days, DST) and the server `/config` (cadence, slots, risk policy,
strategy toggles, model poll minutes). Jobs, all on one asyncio loop:
  heartbeat     every 60 s: components + resources (never secrets); replays the POST spool once reachable
  work          every 30 s: GET /work -> outbox bridge (server outbox -> bot's local queue -> acks) + positions
  config        every 30 min (and at start): sanitized server config; announces newer secret versions
  intraday      during the regular session at open + k x cadence (15/30 min): latest prints for <= 30 slots
                (IEX Basic cap; bucket/quota in market/ratelimit), lifecycle annotations, member assessments
  end_of_day    session close + 15 min (early closes included), once per session, never after the next open
  nightly       at cadence.nightly_refresh_et on session days: universe + open-signal refresh
  models        every models.poll_minutes (default 45): verified atomic model refresh (models/sync.py)
  notices       hourly: missed training (>8 days), registry unreachable, model expiring (<3 days), quota low
Offline (no network, server down, device pending): the loop keeps running; writes spool locally, the last verified
model keeps serving until it expires, then only UNVALIDATED research setups can be published. SIGTERM/SIGINT stop
the loop between jobs. A flock on <state_dir>/worker.lock prevents two workers. `--once` runs one pass for tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from apex_worker.agent.client import AgentClient, AgentHTTPError, AgentUnavailable
from apex_worker.agent.secrets import SecretManager, announced_versions
from apex_worker.contracts import validate
from apex_worker.features.panel import AssetMeta
from apex_worker.market.adapters.base import MarketDataAdapter
from apex_worker.market.calendar import MarketCalendar, MarketPhase, OutOfCalendarRange
from apex_worker.market.priority import SlotRequest, plan_slots
from apex_worker.models.sync import ModelSync
from apex_worker.runtime.agent_state import AgentState
from apex_worker.runtime.engine import ApexEngine, EngineConfig
from apex_worker.runtime.notices import NoticeRouter, SystemNotices
from apex_worker.runtime.outbox_bridge import OutboxBridge
from apex_worker.runtime.remote_sink import RemoteSink
from apex_worker.signals.levels import LevelConfig
from apex_worker.signals.policy import PolicyConfig, SignalPolicy
from apex_worker.signals.strategies.library import enabled_strategies
from apex_worker.storage.local import LocalStore
from apex_worker.timeutil import Clock, et_datetime, iso_utc

log = logging.getLogger("apex.worker")

WORKER_VERSION = "0.1.0"
IEX_SYMBOL_CAP = 30
LIVE_BENCHMARK = "SPY"
LIVE_SECTOR_ETFS = {
    "technology": "XLK", "financials": "XLF", "energy": "XLE", "health_care": "XLV", "industrials": "XLI",
    "consumer_discretionary": "XLY", "consumer_staples": "XLP", "utilities": "XLU", "materials": "XLB",
    "real_estate": "XLRE", "communication_services": "XLC",
}
STALE_POLLS_FOR_NOTICE = 2


class ProviderUnconfigured(RuntimeError):
    pass


class NoAdapter:
    """Placeholder until market credentials arrive; every call fails closed (nothing is invented)."""

    provider = "none"

    async def _fail(self, *_a, **_k):
        raise ProviderUnconfigured("market data not configured")

    get_assets = get_daily_bars = get_intraday_bars = get_latest = get_corporate_actions = _fail

    def health(self):  # pragma: no cover - trivial
        return None


@dataclass
class Schedule:
    heartbeat_seconds: int = 60
    work_seconds: int = 30
    config_seconds: int = 1800
    notices_seconds: int = 3600
    eod_delay_minutes: int = 15
    tick_seconds: float = 5.0
    stop_grace_seconds: float = 20.0


@dataclass
class ServerSettings:
    """Sanitized server configuration (GET /config) with safe defaults when the server is unreachable."""

    intraday_minutes: int = 15
    nightly_refresh_et: str = "19:30"
    max_slots: int = 25
    reserve_for_positions: int = 10
    model_poll_minutes: int = 45
    risk: dict = field(default_factory=dict)
    compliance: dict = field(default_factory=dict)
    strategies: dict = field(default_factory=dict)
    budgets: dict = field(default_factory=dict)
    training: dict | None = None
    universe: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    def apply(self, data: dict) -> None:
        cad, slots, models = data.get("cadence") or {}, data.get("slots") or {}, data.get("models") or {}
        if cad.get("intraday_minutes") in (15, 30):
            self.intraday_minutes = int(cad["intraday_minutes"])
        if isinstance(cad.get("nightly_refresh_et"), str) and len(cad["nightly_refresh_et"]) == 5:
            self.nightly_refresh_et = cad["nightly_refresh_et"]
        if isinstance(slots.get("max_slots"), int):
            self.max_slots = max(1, min(IEX_SYMBOL_CAP, slots["max_slots"]))
            self.reserve_for_positions = max(0, min(self.max_slots, int(slots.get("reserve_for_positions") or 0)))
        if isinstance(models.get("poll_minutes"), int):
            self.model_poll_minutes = max(15, min(120, models["poll_minutes"]))
        for name in ("risk", "compliance", "strategies", "budgets", "universe"):
            if isinstance(data.get(name), dict):
                setattr(self, name, data[name])
        if isinstance(data.get("training"), dict):
            self.training = data["training"]
        self.raw = data

    def policy(self) -> PolicyConfig:
        r = self.risk
        base = PolicyConfig()
        evs = int(r.get("entry_valid_sessions") or base.levels.entry_valid_sessions)
        return PolicyConfig(
            min_quality=float(r.get("min_quality", base.min_quality)),
            max_new_per_session=int(r.get("max_new_per_session", base.max_new_per_session)),
            max_open_per_sector=int(r.get("max_open_per_sector", base.max_open_per_sector)),
            publish_research_setups=bool(r.get("research_setups_enabled", base.publish_research_setups)),
            research_min_quality=float(r.get("research_min_quality", base.research_min_quality)),
            levels=LevelConfig(entry_valid_sessions=max(1, min(5, evs))),
        )


# ---- resources -----------------------------------------------------------------------------------------------
def _read(path: str) -> str | None:
    try:
        return Path(path).read_text()
    except OSError:
        return None


def resources(state_dir: Path, spool_pending: int, clock_synced: bool | None) -> dict:
    out: dict = {"spool_pending": int(spool_pending), "clock_synced": clock_synced}
    status = _read("/proc/self/status") or ""
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            out["rss_mb"] = round(int(line.split()[1]) / 1024, 1)
    for line in (_read("/proc/meminfo") or "").splitlines():
        if line.startswith("MemAvailable:"):
            out["mem_available_mb"] = round(int(line.split()[1]) / 1024, 1)
    with contextlib.suppress(OSError):
        du = shutil.disk_usage(state_dir)
        out["disk_free_pct"] = round(100 * du.free / du.total, 1)
    temp = _read("/sys/class/thermal/thermal_zone0/temp")
    out["cpu_temp_c"] = round(int(temp.strip()) / 1000, 1) if temp and temp.strip().isdigit() else None
    with contextlib.suppress(OSError, AttributeError):
        out["load_1m"] = round(os.getloadavg()[0], 2)
    return out


def _component(status: str, detail: str = "", last_ok: datetime | None = None, metrics: dict | None = None) -> dict:
    c: dict = {"status": status}
    if detail:
        c["detail"] = detail[:240]
    if last_ok:
        c["last_ok_at"] = iso_utc(last_ok)
    if metrics:
        c["metrics"] = metrics
    return c


# ---- worker ---------------------------------------------------------------------------------------------------
class Worker:
    def __init__(
        self,
        *,
        release_mode: str,
        clock: Clock,
        calendar: MarketCalendar,
        local: LocalStore,
        state: AgentState,
        state_dir: Path,
        model_sync: ModelSync,
        engine_config: EngineConfig,
        meta: dict[str, AssetMeta],
        universe: list[str],
        sink=None,
        client: AgentClient | None = None,
        adapter: MarketDataAdapter | None = None,
        secrets: SecretManager | None = None,
        adapter_factory: Callable | None = None,
        bridge: OutboxBridge | None = None,
        schedule: Schedule | None = None,
        provider: str = "alpaca",
        feed: str = "iex",
        catalysts_provider: Callable[[], list[dict]] | None = None,
        earnings_status=None,
        universe_refresh: Callable | None = None,
    ):
        from apex_worker.runtime.local_sink import LocalSink

        self.release_mode = release_mode
        self.is_demo = release_mode == "DEMO"
        self.clock = clock
        self.cal = calendar
        self.local = local
        self.state = state
        self.state_dir = state_dir
        self.client = client
        self.secrets = secrets
        self.adapter_factory = adapter_factory
        self.model_sync = model_sync
        self.bridge = bridge
        self.schedule = schedule or Schedule()
        self.server = ServerSettings()
        self.provider, self.feed = provider, feed
        # Pi system notices: server outbox first (dashboard history), local outbox only when it cannot be reached
        self.notify = NoticeRouter(client, local.enqueue) if client else local.enqueue
        if client is not None:
            model_sync.notify = self.notify
        self.sink = sink or (RemoteSink(client, state, release_mode=release_mode, provider=provider, feed=feed,
                                        local_enqueue=self.notify, mirror=local) if client else LocalSink(local))
        self.notices = SystemNotices(self.notify, is_demo=self.is_demo)
        self.universe = list(universe)
        self.catalysts_provider = catalysts_provider
        self.universe_refresh = universe_refresh
        self.engine = ApexEngine(adapter or NoAdapter(), self.sink, clock, engine_config, models={}, calendar=calendar,
                                 meta=meta, earnings_status=earnings_status)
        self._has_adapter = adapter is not None
        self._next: dict[str, datetime] = {}
        self.status: dict[str, dict] = {}
        self.device_status = "unknown"
        self._stale_polls = 0
        self._last_fresh: datetime | None = None
        self._last_scan: dict | None = state.get("last_scan_status")

    # ---- helpers ------------------------------------------------------------------------------------------
    def _due(self, job: str, now: datetime, every: timedelta) -> bool:
        nxt = self._next.get(job)
        if nxt is not None and now < nxt:
            return False
        self._next[job] = now + every
        return True

    def _set(self, name: str, status: str, detail: str = "", ok: bool = False, metrics: dict | None = None) -> None:
        prev = self.status.get(name, {})
        last_ok = self.clock.now() if ok else (datetime.fromisoformat(prev["last_ok_at"].replace("Z", "+00:00"))
                                               if prev.get("last_ok_at") else None)
        self.status[name] = _component(status, detail, last_ok, metrics)

    def _ensure_adapter(self, auth_failed: bool = False, announced: dict | None = None) -> bool:
        """Build/rebuild the market adapter when credentials arrive or rotate (`secret_versions` on config/heartbeat)."""
        if self.secrets is None or self.adapter_factory is None:
            return self._has_adapter
        changed = self.secrets.maybe_refresh(self.server.raw, announced, auth_failed=auth_failed)
        creds = self.secrets.credentials
        if creds and (changed or not self._has_adapter or auth_failed):
            self.engine.adapter = self.adapter_factory(creds, self.server.budgets)
            self._has_adapter = True
        return self._has_adapter

    def _apply_server(self) -> None:
        self.engine.cfg.compliance = dict(self.server.compliance)
        self.engine.policy = SignalPolicy(self.server.policy(), self.cal)
        self.engine.candidates.strategies = enabled_strategies(self.server.strategies)
        self.engine.models = self.model_sync.active_models()

    # ---- jobs ---------------------------------------------------------------------------------------------
    def job_config(self, now: datetime) -> None:
        if self.client is None:
            self._set("server", "DISABLED", "not paired: running offline on the local store")
            return
        try:
            self.server.apply(self.client.get("config"))
            self._set("server", "OK", f"device {self.device_status}", ok=True)
        except AgentHTTPError as exc:
            self._set("server", "DEGRADED", f"config refused: {exc.code}")
        except AgentUnavailable:
            self._set("server", "DOWN", "unreachable; using cached settings and the local spool")
        self._apply_server()

    def heartbeat_body(self, now: datetime) -> dict:
        spool = self.client.spool if self.client else None
        agent_pending = spool.pending_count() if spool else 0
        bot_pending = self.local.spool_pending_count()
        comps = dict(self.status)
        comps["spool"] = _component("OK" if agent_pending < 500 else "DEGRADED", "agent POSTs and bot member events "
                                    "awaiting sync", metrics={"agent_pending": agent_pending, "bot_pending": bot_pending,
                                                              "agent_rejected": spool.rejected_count() if spool else 0})
        models = self.model_sync.active_models()
        comps["model"] = _component(
            "OK" if models else "UNCONFIGURED",
            ", ".join(f"{k}={v.model_id}" for k, v in sorted(models.items()))[:200] if models
            else "no valid unexpired champion: only UNVALIDATED research setups (no probabilities)",
            metrics={"active_models": len(models), "registry_failures": self.model_sync.consecutive_failures})
        comps["training_schedule"] = _component({"OK": "OK", "STALE": "DEGRADED"}.get(
            self.notices.status.get("training", "UNKNOWN"), "UNKNOWN"), "from server /config training.last_success_at")
        rows = self.local.query("SELECT MIN(created_at) AS oldest, COUNT(*) AS n FROM notification_outbox WHERE state='pending'")
        oldest = rows[0]["oldest"] if rows else None
        lag = int((now - datetime.fromisoformat(oldest.replace("Z", "+00:00"))).total_seconds()) if oldest else 0
        comps["discord_delivery"] = _component("OK" if lag < 600 else "DEGRADED",
                                               "local outbox delivered by the bot process (apex bot)",
                                               metrics={"pending": int(rows[0]["n"] or 0), "oldest_pending_seconds": lag})
        if self.secrets is not None:
            comps["market_credentials"] = _component(self.secrets.status, self.secrets.detail,
                                                     metrics={"secret_version": self.secrets.version})
        hb = {"agent_id": self.client.agent_id if self.client else "agt_offline0", "sent_at": iso_utc(now.replace(microsecond=0)),
              "worker_version": WORKER_VERSION, "release_mode": self.release_mode,
              "secret_version_ack": self.secrets.version if self.secrets else 0, "components": comps,
              "resources": resources(self.state_dir, agent_pending + bot_pending, self.client.clock_synced if self.client else None)}
        validate("WorkerHeartbeat", hb)
        return hb

    def job_heartbeat(self, now: datetime) -> dict | None:
        hb = self.heartbeat_body(now)
        if self.client is None:
            return hb
        try:
            res = self.client.post("heartbeat", hb).data or {}
        except (AgentHTTPError, AgentUnavailable) as exc:
            self._set("server", "DOWN" if isinstance(exc, AgentUnavailable) else "DEGRADED",
                      getattr(exc, "code", "unreachable"))
            return hb
        self.device_status = str(res.get("status", self.device_status))
        if announced_versions(res).get("alpaca", 0) > (self.secrets.version if self.secrets else 0):
            self._ensure_adapter(announced=res)  # rotation picked up within one heartbeat
        if self.client.spool is not None and self.client.spool.pending_count():
            counts = self.client.flush_spool()
            log.info("spool replay: %s", counts)
        return hb

    def job_work(self, now: datetime) -> None:
        if self.client is None or self.bridge is None:
            return
        try:
            work = self.client.get("work", {"limit": 20})
        except (AgentHTTPError, AgentUnavailable) as exc:
            self._set("outbox_bridge", "DOWN", f"lease failed: {getattr(exc, 'code', 'unreachable')}")
        else:
            counts = self.bridge.ingest(work.get("outbox") or [], now)
            if isinstance(self.sink, RemoteSink):
                self.sink.set_work(work)
            self._set("outbox_bridge", "OK", "server outbox -> local delivery queue", ok=True, metrics=counts)
        acks = self.bridge.ack_pass(now)
        self.status.setdefault("outbox_bridge", _component("UNKNOWN"))["metrics"] = {
            **self.status["outbox_bridge"].get("metrics", {}), **{f"ack_{k}": v for k, v in acks.items()}}

    def job_models(self, now: datetime) -> None:
        rep = self.model_sync.poll()
        self.engine.models = self.model_sync.active_models()
        if self.client is None:
            self._set("model_registry", "DISABLED", "offline local mode: models only via `apex model install`")
            return
        self._set("model_registry", "OK" if rep.registry_ok else "DOWN", rep.detail or "manifest polled",
                  ok=rep.registry_ok, metrics={"activated": len(rep.activated), "rejected": len(rep.rejected),
                                               "rolled_back": len(rep.rolled_back)})

    def job_notices(self, now: datetime) -> None:
        self.notices.check_training(self.server.training, now)
        self.notices.check_registry(self.model_sync.consecutive_failures, self.model_sync.last_success_at, now)
        local = [(sid, a.model_id, a.manifest["validation_expires_at"]) for sid, a in self.model_sync.active_models().items()]
        server = [(c.get("strategy_id", ""), c.get("model_id", ""), c.get("validation_expires_at", ""))
                  for c in (self.server.training or {}).get("champions") or [] if isinstance(c, dict)]
        seen = {m[1] for m in local}
        self.notices.check_expiry(local + [s for s in server if s[1] not in seen], now)
        quota = getattr(self.engine.adapter, "quota", None)
        self.notices.check_quota([quota.snapshot()] if quota is not None else [], now)

    def _slot_symbols(self) -> list[str]:
        reqs = [SlotRequest(p.symbol, "position") for p in self.sink.monitored_positions()]
        reqs += [SlotRequest(s["symbol"], "signal", float(s.get("quality_score") or 0)) for s, _ in self.sink.open_signals()]
        if self.engine.last_scan:
            reqs += [SlotRequest(c["symbol"], "candidate", float(c.get("quality_score") or 0),
                                 (c.get("_internal") or {}).get("sector")) for c in self.engine.last_scan.candidates[:40]]
        plan = plan_slots(reqs, set(), max_slots=min(IEX_SYMBOL_CAP, self.server.max_slots))
        return list(plan.selected)

    def _intraday_slot(self, now: datetime) -> str | None:
        ph = self.cal.phase(now)
        if ph.phase is not MarketPhase.OPEN or ph.session is None:
            return None
        k = int((now - ph.session.open_utc).total_seconds() // (60 * self.server.intraday_minutes))
        return f"{ph.session.day.isoformat()}:{k}" if k >= 1 else None

    async def job_intraday(self, now: datetime) -> bool:
        slot = self._intraday_slot(now)
        if slot is None or self.state.get("intraday_slot") == slot:
            return False
        self.state.put("intraday_slot", slot)
        if not self._ensure_adapter():
            self._set("market_feed", "UNCONFIGURED", "no market credentials on this Pi (Settings -> Integrations -> Alpaca)")
            return False
        symbols = self._slot_symbols()
        if not symbols:
            self._set("market_feed", "OK", "nothing to monitor this slot", ok=True)
            return True
        await self._poll(symbols, now)
        self.engine.evaluate_intraday()
        self.engine.assess_positions()
        self._flush_sink(now)
        return True

    async def _poll(self, symbols: list[str], now: datetime) -> None:
        error = None
        try:
            await self.engine.poll_quotes(symbols)
        except Exception as exc:  # provider errors are data problems here, never a crash
            error = type(exc).__name__
            if getattr(exc, "status", None) in (401, 403):
                self._ensure_adapter(auth_failed=True)
        sla = self.engine.cfg.fresh_sla_seconds
        stale = [s for s in symbols if (q := self.engine.cache.get(s)) is None or q.age_seconds(now) > sla]
        metrics = {"feed": self.feed, "subscriptions": len(symbols), "subscription_cap": IEX_SYMBOL_CAP,
                   "plan_limit": IEX_SYMBOL_CAP, "stale": len(stale)}
        if not stale:
            self._stale_polls, self._last_fresh = 0, now
            self._set("market_feed", "OK", f"{self.feed.upper()} latest prints (single venue)", ok=True, metrics=metrics)
            return
        self._stale_polls += 1
        self._set("market_feed", "DOWN" if error else "STALE", f"{len(stale)} stale symbol(s)" + (f"; {error}" if error else ""),
                  metrics=metrics)
        if self._stale_polls >= STALE_POLLS_FOR_NOTICE:  # hysteresis: one bad poll is not an incident
            self.notices.stale_feed(stale, self._last_fresh, now)

    def _flush_sink(self, now: datetime) -> None:
        if not isinstance(self.sink, RemoteSink):
            return
        self.sink.flush_assessments()
        obs = [q.to_contract(now, self.is_demo) for q in self.engine.cache.all().values()]
        if obs:
            self.sink.mirror_quotes(obs)
            self.sink.post_observations(obs)

    def _eod_day(self, now: datetime) -> date | None:
        try:
            day = self.cal.latest_completed_session(now)
            close = self.cal.session(day).close_utc
            next_open = self.cal.session(self.cal.next_session(day)).open_utc
        except OutOfCalendarRange:
            return None
        if now < close + timedelta(minutes=self.schedule.eod_delay_minutes) or now >= next_open:
            return None  # too early, or too late (a scan published after the next open would be stale)
        return None if self.state.get("last_scan_session") == day.isoformat() else day

    async def job_end_of_day(self, now: datetime) -> dict | None:
        day = self._eod_day(now)
        if day is None:
            return None
        if not self._ensure_adapter():
            self._set("scanner", "UNCONFIGURED", "no market data source; nothing is scanned or published")
            return None
        self._apply_server()
        bench = self.engine.cfg.benchmark
        symbols = sorted(set(self.universe) | {bench} | set(self.engine.cfg.sector_etfs.values())
                         | {s["symbol"] for s, _ in self.sink.open_signals(include_closed=True)}
                         | {p.symbol for p in self.sink.monitored_positions()})
        if isinstance(self.sink, RemoteSink):
            self.sink.scan_started_at = now
        try:
            bars = await self.engine.refresh_daily(symbols)
        except Exception as exc:
            self._set("scanner", "DEGRADED", f"daily bars unavailable ({type(exc).__name__}); no scan, no alerts")
            return None
        ref = bars.get(bench)
        if ref is None or ref.empty or ref.index[-1].date() < day:
            self._set("scanner", "STALE", f"benchmark bars do not reach {day}; scan skipped (fail closed)")
            return None
        self.engine.build_panel()
        scan, outcome = self.engine.end_of_day(day)
        self.engine.assess_positions()
        self._flush_sink(now)
        self.state.put("last_scan_session", day.isoformat())
        summary = {"session": day.isoformat(), "candidates": len(scan.candidates), "published": len(outcome.signals),
                   "no_trade_reason": outcome.no_trade_reason}
        self.state.put("last_scan_status", summary)
        self._set("scanner", "OK", f"session {day}: {summary['published']} published"
                  + (f" ({outcome.no_trade_reason})" if outcome.no_trade_reason else ""), ok=True,
                  metrics={"candidates": summary["candidates"], "published": summary["published"]})
        return summary

    async def job_nightly(self, now: datetime) -> bool:
        local_day = self.cal.session_date_for(now)
        hh, mm = (int(x) for x in self.server.nightly_refresh_et.split(":"))
        if not self.cal.is_session(local_day) or now < et_datetime(local_day, hh, mm):
            return False
        if self.state.get("last_nightly") == local_day.isoformat():
            return False
        self.state.put("last_nightly", local_day.isoformat())
        if isinstance(self.sink, RemoteSink):
            self.sink.refresh_open_signals()
        if self.universe_refresh is not None and self._ensure_adapter():
            try:
                self.universe = await self.universe_refresh(self.engine.adapter, now, self.server.universe)
            except Exception as exc:
                log.warning("universe refresh failed: %s", type(exc).__name__)
        if self.catalysts_provider is not None and isinstance(self.sink, RemoteSink):
            with contextlib.suppress(Exception):
                self.sink.post_catalysts(self.catalysts_provider())
        return True

    # ---- loop ---------------------------------------------------------------------------------------------
    def start(self) -> None:
        self.model_sync.load_local()
        if isinstance(self.sink, RemoteSink):
            self.sink.refresh_open_signals()
        self.job_config(self.clock.now())

    async def run_once(self) -> dict:
        """One pass of every job that is due now (used by `apex worker --once` and tests)."""
        now = self.clock.now()
        self.start()
        self.job_heartbeat(now)
        self.job_models(now)
        self.job_work(now)
        out = {"intraday": await self.job_intraday(now), "end_of_day": await self.job_end_of_day(now),
               "nightly": await self.job_nightly(now)}
        self.job_notices(now)
        self.job_work(now)  # ack anything the bot already delivered
        out["heartbeat"] = self.job_heartbeat(now)["components"]
        return out

    async def tick(self) -> None:
        now = self.clock.now()
        s = self.schedule
        if self._due("config", now, timedelta(seconds=s.config_seconds)):
            self.job_config(now)
        if self._due("heartbeat", now, timedelta(seconds=s.heartbeat_seconds)):
            self.job_heartbeat(now)
        if self._due("models", now, timedelta(minutes=self.server.model_poll_minutes)):
            self.job_models(now)
        if self._due("work", now, timedelta(seconds=s.work_seconds)):
            self.job_work(now)
        await self.job_intraday(now)
        await self.job_end_of_day(now)
        await self.job_nightly(now)
        if self._due("notices", now, timedelta(seconds=s.notices_seconds)):
            self.job_notices(now)

    async def run(self, stop: asyncio.Event) -> None:
        self.start()
        while not stop.is_set():
            tick = asyncio.create_task(self.tick())
            waiter = asyncio.create_task(stop.wait())
            await asyncio.wait({tick, waiter}, return_when=asyncio.FIRST_COMPLETED)
            waiter.cancel()
            if not tick.done():
                # SIGTERM mid-job: a short grace period, then cancel (writes are transactional; an unfinished scan
                # simply reruns after restart). Keeps shutdown inside systemd's TimeoutStopSec=30.
                with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                    await asyncio.wait_for(tick, timeout=self.schedule.stop_grace_seconds)
            if tick.done() and not tick.cancelled() and tick.exception() is not None:
                # one failing job never kills the appliance; it is reported in health
                log.error("worker tick failed: %s", type(tick.exception()).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.schedule.tick_seconds)

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            if self.client.spool is not None:
                self.client.spool.close()


# ---- production wiring ------------------------------------------------------------------------------------------
def build_worker(cfg) -> Worker:
    from apex_worker.agent.client import PostSpool
    from apex_worker.agent.crypto import DeviceKeys
    from apex_worker.agent.secrets import MarketCredentials
    from apex_worker.discord_bot.settings import BotSettings
    from apex_worker.market.calendar import default_calendar
    from apex_worker.runtime.agent_state import STATE_FILE
    from apex_worker.runtime.universe_job import asset_meta, load_universe, refresh_universe, save_universe
    from apex_worker.timeutil import SystemClock

    state_dir = Path(cfg.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    clock, cal = SystemClock(), default_calendar()
    local = LocalStore(BotSettings.load(cfg).local_db)  # the SAME file the bot delivers from
    state = AgentState(state_dir / STATE_FILE)
    client = None
    if cfg.api_base_url and cfg.agent_id and cfg.agent_hmac_secret:
        client = AgentClient(cfg.api_base_url, cfg.agent_id, cfg.agent_hmac_secret,
                             spool=PostSpool(state_dir / STATE_FILE), bypass_secret=cfg.vercel_bypass_secret)
    keys = DeviceKeys.load(state_dir) if client else None
    manual = (MarketCredentials(cfg.alpaca_key_id, cfg.alpaca_secret_key, 0, "manual_env")
              if cfg.has_market_credentials() else None)  # clearly-marked manual fallback
    secrets = SecretManager(client, keys, manual=manual) if client and keys else None

    def adapter_factory(creds, budgets: dict):
        from apex_worker.market.adapters.alpaca import AlpacaAdapter
        from apex_worker.market.ratelimit import QuotaTracker

        return AlpacaAdapter(creds.key_id, creds.secret_key,
                             quota=QuotaTracker("alpaca", int((budgets or {}).get("alpaca_daily_requests") or 150_000)))

    adapter = adapter_factory(manual, {}) if manual and secrets is None and cfg.release_mode != "DEMO" else None
    uni = load_universe(state_dir)
    sector_map = {}
    with contextlib.suppress(OSError, ValueError):
        sector_map = json.loads((state_dir / "sectors.json").read_text())

    async def universe_refresh(ad, now, ucfg):
        data = await refresh_universe(ad, cal, now, ucfg, max_symbols=int(os.environ.get("APEX_UNIVERSE_MAX", "400")))
        save_universe(state_dir, data)
        worker.engine.meta = asset_meta(data, sector_map, LIVE_SECTOR_ETFS, LIVE_BENCHMARK)
        return data["symbols"]

    sync = ModelSync(client, state_dir / "models", cfg.model_trusted_keys, cfg.release_mode, state, clock=clock.now,
                     notify=local.enqueue)
    worker = Worker(
        release_mode=cfg.release_mode, clock=clock, calendar=cal, local=local, state=state, state_dir=state_dir,
        model_sync=sync, engine_config=EngineConfig(release_mode=cfg.release_mode, benchmark=LIVE_BENCHMARK,
                                                    sector_etfs=dict(LIVE_SECTOR_ETFS)),
        meta=asset_meta(uni, sector_map, LIVE_SECTOR_ETFS, LIVE_BENCHMARK), universe=uni.get("symbols") or [],
        client=client, adapter=adapter, secrets=secrets, adapter_factory=adapter_factory if secrets else None,
        bridge=OutboxBridge(client, local, component_secret=cfg.component_secret) if client else None,
        universe_refresh=universe_refresh,
    )
    return worker


EXIT_OK, EXIT_CONFIG, EXIT_LOCKED = 0, 2, 3  # systemd: RestartPreventExitStatus=2 3; anything else is restarted
TRUE = {"1", "true", "yes", "on"}


def config_problems(cfg) -> list[str]:
    """Problems a restart cannot fix. Each entry is one operator-facing 'how to fix' line (never a secret value)."""
    from apex_worker.agent.crypto import DeviceKeys

    problems = list(getattr(cfg, "config_errors", []) or [])
    paired = bool(cfg.api_base_url and cfg.agent_id and cfg.agent_hmac_secret)
    offline = (os.environ.get("APEX_WORKER_OFFLINE") or "").strip().lower() in TRUE
    if not paired and not offline:
        missing = [n for n, v in (("APEX_API_BASE_URL", cfg.api_base_url), ("agent id", cfg.agent_id),
                                  ("AGENT_HMAC_SECRET", cfg.agent_hmac_secret)) if not v]
        problems.append(f"not paired ({', '.join(missing)} missing): run `apex pair CODE --server https://<dashboard>` "
                        "with AGENT_HMAC_SECRET in the env file, or set APEX_WORKER_OFFLINE=1 for offline local mode")
    if paired:
        try:
            if DeviceKeys.load(Path(cfg.state_dir)) is None:
                problems.append("device_keys.json is missing from the state dir: re-run `apex pair CODE --server URL "
                                "--new-identity`")
        except PermissionError:
            problems.append(f"{Path(cfg.state_dir) / 'device_keys.json'} must be owner-only: chmod 600 it")
        except (ValueError, KeyError):
            problems.append("device_keys.json is unreadable: re-run `apex pair CODE --server URL --new-identity`")
    if cfg.release_mode != "DEMO" and paired and not cfg.model_trusted_keys:
        log.warning("APEX_MODEL_TRUSTED_KEYS is not set: no model can be verified, so only UNVALIDATED research "
                    "setups are possible (set the public key printed by `apex keygen signing`)")
    return problems


def run_worker(once: bool = False) -> int:
    """Exit codes: 0 clean stop (SIGTERM/SIGINT or --once), 2 configuration error, 3 another worker holds the lock,
    anything else = transient crash (systemd restarts it)."""
    from apex_worker.config import WorkerConfig
    from apex_worker.discord_bot.settings import AlreadyRunning, acquire_instance_lock

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = WorkerConfig.load()
    problems = config_problems(cfg)
    if problems:
        for line in problems:
            log.error("apex worker configuration error: %s", line)
        return EXIT_CONFIG
    try:
        lock = acquire_instance_lock(cfg.state_dir, "worker.lock")
    except AlreadyRunning:
        log.error("apex worker: another worker already holds %s; stop it first", cfg.state_dir / "worker.lock")
        return EXIT_LOCKED
    worker = None
    try:
        worker = build_worker(cfg)
        if worker.client is None:
            log.warning("APEX_WORKER_OFFLINE=1: offline local mode (no server; the bot delivers from the local store)")
        if once:
            print(json.dumps(asyncio.run(worker.run_once()), indent=2, sort_keys=True, default=str))
            return EXIT_OK
        asyncio.run(_serve(worker))
        return EXIT_OK
    finally:
        if worker is not None:
            worker.close()
        lock.release()


async def _serve(worker: Worker) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    log.info("apex worker started (mode=%s)", worker.release_mode)
    await worker.run(stop)
    log.info("apex worker stopped cleanly")
