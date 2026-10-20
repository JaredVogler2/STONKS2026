"""APEX runtime engine: end-of-day scan/publish, lifecycle evaluation and member assessments.

The engine is transport-agnostic: it writes through a `Sink` (LocalSink for DEMO/offline, the signed cloud
agent API in connected mode). DEMO replay and live operation therefore exercise the same code path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Protocol

import pandas as pd

from apex_worker.features.panel import AssetMeta, FeaturePanel, build_panel
from apex_worker.market.adapters.base import MarketDataAdapter
from apex_worker.market.calendar import MarketCalendar, default_calendar
from apex_worker.market.quote_cache import QuoteCache
from apex_worker.models.store import ActiveModel
from apex_worker.notifications import intents
from apex_worker.portfolio.assessment import AssessContext, assess, materiality_key, should_notify
from apex_worker.signals.candidates import CandidateEngine, ScanResult
from apex_worker.signals.lifecycle import SignalEventRecord
from apex_worker.signals.monitor import evaluate_daily, evaluate_intraday
from apex_worker.signals.policy import PolicyConfig, PolicyOutcome, SignalPolicy
from apex_worker.timeutil import Clock, iso_utc


@dataclass
class MonitoredPosition:
    user_id: str
    discord_user_id: str | None
    position_id: str
    symbol: str
    signal_id: str | None
    ledger_state: object  # LedgerState
    last_sent: dict | None = None


class Sink(Protocol):
    def publish_signal(self, signal: dict, tier: str, intent: dict, now: datetime) -> bool: ...
    def append_signal_events(self, signal: dict, events: list[dict], intents_: list[dict], now: datetime) -> int: ...
    def record_scan(self, scan: ScanResult, outcome: PolicyOutcome, now: datetime) -> None: ...
    def open_signals(self, include_closed: bool = False) -> list[tuple[dict, str]]: ...
    def monitored_positions(self) -> list[MonitoredPosition]: ...
    def save_assessment(self, pos: MonitoredPosition, assessment: dict, intent: dict | None, now: datetime) -> None: ...
    def enqueue(self, intent: dict, now: datetime) -> bool: ...


@dataclass
class EngineConfig:
    release_mode: str = "DEMO"
    benchmark: str = "DMSPY"
    sector_etfs: dict[str, str] = field(default_factory=dict)
    lookback_sessions: int = 320
    fresh_sla_seconds: int = 120
    compliance: dict[str, bool] = field(default_factory=dict)
    policy: PolicyConfig = field(default_factory=PolicyConfig)


class ApexEngine:
    def __init__(
        self,
        adapter: MarketDataAdapter,
        sink: Sink,
        clock: Clock,
        config: EngineConfig,
        models: dict[str, ActiveModel] | None = None,
        calendar: MarketCalendar | None = None,
        meta: dict[str, AssetMeta] | None = None,
        earnings_status=None,
        catalysts_provider=None,
    ):
        self.adapter = adapter
        self.sink = sink
        self.clock = clock
        self.cfg = config
        self.models = models or {}
        self.cal = calendar or default_calendar()
        self.meta = meta or {}
        self.cache = QuoteCache()
        self.candidates = CandidateEngine(is_demo=config.release_mode == "DEMO", calendar=self.cal)
        self.policy = SignalPolicy(config.policy, self.cal)
        self.earnings_status = earnings_status
        self.catalysts_provider = catalysts_provider or (lambda: [])
        self._bars: dict[str, pd.DataFrame] = {}
        self.last_panel: FeaturePanel | None = None
        self.last_scan: ScanResult | None = None

    @property
    def is_demo(self) -> bool:
        return self.cfg.release_mode == "DEMO"

    # ---- data ------------------------------------------------------------------
    async def refresh_daily(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        now = self.clock.now()
        start = self.cal.add_sessions(self.cal.first_day, 0)
        end = self.cal.latest_completed_session(now)
        start = max(start, end - timedelta(days=int(self.cfg.lookback_sessions * 1.6)))
        self._bars = await self.adapter.get_daily_bars(symbols, start, end, adjustment="split")
        return self._bars

    def build_panel(self) -> FeaturePanel:
        panel = build_panel(
            self._bars, self.meta, self.cfg.benchmark, self.cfg.sector_etfs, lookback=self.cfg.lookback_sessions
        )
        self.last_panel = panel
        return panel

    # ---- end of day ---------------------------------------------------------------
    def end_of_day(self, session_day: date) -> tuple[ScanResult, PolicyOutcome]:
        now = self.clock.now()
        panel = self.last_panel or self.build_panel()
        session = pd.Timestamp(session_day)
        cats = self.catalysts_provider()
        scan = self.candidates.scan(panel, session, cats, self.earnings_status, created_at=now)
        self.last_scan = scan
        open_sigs = [s for s, st in self.sink.open_signals() if st in ("PUBLISHED", "ACTIVE")]
        quotes = {s: q.to_contract(now, self.is_demo) for s, q in self.cache.all().items()}
        outcome = self.policy.decide(
            scan,
            models=self.models,
            release_mode=self.cfg.release_mode,
            quotes=quotes,
            open_signals=open_sigs,
            now=now,
            compliance=self.cfg.compliance,
        )
        for d in outcome.decisions:
            if d.signal:
                self.sink.publish_signal(d.signal, d.outcome, intents.new_signal(d.signal, d.outcome, now), now)
        self.sink.record_scan(scan, outcome, now)
        self.evaluate_signals_daily()
        return scan, outcome

    def evaluate_signals_daily(self) -> int:
        now = self.clock.now()
        n = 0
        for sig, state in self.sink.open_signals():
            bars = self._bars.get(sig["symbol"])
            if bars is None:
                continue
            evs: list[SignalEventRecord] = evaluate_daily(sig, state, bars, now, self.cal)
            if evs:
                contracts = [e.to_contract() for e in evs]
                notes = [i for i in (intents.signal_event(sig, e, now) for e in contracts) if i]
                n += self.sink.append_signal_events(sig, contracts, notes, now)
        return n

    # ---- intraday ----------------------------------------------------------------
    async def poll_quotes(self, symbols: list[str]) -> int:
        now = self.clock.now()
        latest = await self.adapter.get_latest(symbols)
        n = 0
        for s, lp in latest.items():
            got = self.cache.ingest(
                symbol=s,
                price=lp.price,
                bid=lp.bid,
                ask=lp.ask,
                provider=self.adapter.provider,
                feed=lp.feed,
                latency_class="replay" if lp.feed == "demo" else "live",
                ts_exchange=lp.ts_exchange,
                ts_received=now,
                now=now,
                volume=lp.size,
            )
            n += got is not None
        return n

    def evaluate_intraday(self) -> int:
        now = self.clock.now()
        market_open = self.cal.is_open(now)
        n = 0
        for sig, state in self.sink.open_signals():
            evs = evaluate_intraday(
                sig, state, self.cache.get(sig["symbol"]), now, self.cfg.fresh_sla_seconds, market_open
            )
            if evs:
                contracts = [e.to_contract() for e in evs]
                notes = [i for i in (intents.signal_event(sig, e, now) for e in contracts) if i]
                n += self.sink.append_signal_events(sig, contracts, notes, now)
        return n

    def assess_positions(self) -> list[dict]:
        """One shared quote per symbol -> independent per-member assessments; notify only on material change."""
        now = self.clock.now()
        market_open = self.cal.is_open(now)
        signals = {s["id"]: (s, st) for s, st in self.sink.open_signals(include_closed=True)}
        regime = (self.last_scan.regime.get("label") if self.last_scan else "unknown") or "unknown"
        allow = bool(self.cfg.compliance.get("ALLOW_PERSONALIZED_GROUP_GUIDANCE"))
        sent = []
        for mp in self.sink.monitored_positions():
            sig, sst = signals.get(mp.signal_id, (None, None)) if mp.signal_id else (None, None)
            earnings = self.earnings_status(mp.symbol, iso_utc(now)[:10], 20) if self.earnings_status else "UNKNOWN"
            ctx = AssessContext(
                now=now,
                market_open=market_open,
                fresh_sla_seconds=self.cfg.fresh_sla_seconds,
                regime_label=regime,
                earnings_status=earnings,
                release_mode=self.cfg.release_mode,
                allow_personalized_guidance=allow,
                previous_status=(mp.last_sent or {}).get("status"),
            )
            a = assess(
                position_id=mp.position_id,
                symbol=mp.symbol,
                state=mp.ledger_state,
                signal=sig,
                signal_state=sst,
                quote=self.cache.get(mp.symbol),
                ctx=ctx,
                cal=self.cal,
            )
            intent = None
            if should_notify(a, mp.last_sent, now):
                intent = intents.position_update(mp.user_id, mp.discord_user_id, a, now, self.is_demo)
                sent.append(a)
            self.sink.save_assessment(mp, {**a, "_key": materiality_key(a)}, intent, now)
        return sent
