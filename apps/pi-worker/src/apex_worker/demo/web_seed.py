"""Export the deterministic DEMO run as a timeline of agent-API calls for the dashboard.

The web app replays these calls through the SAME ingestion services that serve the real Pi (heartbeat,
observations, scans, signals + events + outbox intents, catalysts, member position events, assessments,
delivery acks, research reports, model registry). Every record is DEMO-stamped and uses fictional tickers.

Output: fixtures/demo/web-seed.json (committed; regenerate with `apex export-web-seed`).
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from apex_worker.agent.crypto import load_signing_key, public_from_signing_key
from apex_worker.demo.flow import MEMBERS, LocalDemo
from apex_worker.notifications.render import render
from apex_worker.runtime.local_sink import LocalSink
from apex_worker.timeutil import iso_utc

SEED_VERSION = 1
DEMO_AGENT_ID = "agt_demodevice01"
DEMO_GUILD_ID = "100000000000000099"
# PUBLIC TEST-ONLY signing key for DEMO model manifests (never trusted outside DEMO mode)
DEMO_MODEL_SIGNING_SEED = "REVNTy1PTkxZLUFQRVgtTU9ERUwtU0lHTklORy1LRVk"
CHART_HISTORY_SESSIONS = 160


class RecordingSink(LocalSink):
    def __init__(self, store, recorder: list[dict]):
        super().__init__(store)
        self.rec = recorder
        self.tiers: dict[str, str] = {}

    def publish_signal(self, signal, tier, intent, now):
        created = super().publish_signal(signal, tier, intent, now)
        if created:
            events = [e for e in self.store.signal_events(signal["id"])]
            self.rec.append({"op": "signal", "body": {"signal": signal, "tier": tier, "events": events, "intent": intent}})
            self.tiers[signal["id"]] = tier
        return created

    def append_signal_events(self, signal, events, intents_, now):
        n = super().append_signal_events(signal, events, intents_, now)
        if n:
            self.rec.append({"op": "signal_events", "body": {"events": events, "intents": intents_}})
        return n

    def record_scan(self, scan, outcome, now):
        super().record_scan(scan, outcome, now)
        sid = "scan_" + hashlib.sha256(f"{scan.session}|{scan.asof_at}".encode()).hexdigest()[:16]
        cands = [{k: v for k, v in c.items() if k != "_internal"} for c in scan.candidates[:12]]
        self.rec.append({"op": "scan", "body": {
            "scan": {"id": sid, "started_at": iso_utc(now - timedelta(seconds=40)), "finished_at": iso_utc(now),
                     "asof_at": scan.asof_at, "universe_size": scan.stats.universe_size, "eligible_count": scan.stats.eligible,
                     "scanned_count": scan.stats.scanned, "published_count": len(outcome.signals),
                     "no_trade_reason": outcome.no_trade_reason, "is_demo": True,
                     "metrics": {"vetoes": dict(scan.stats.vetoes), "triggered": scan.stats.triggered,
                                 "decisions": [{"symbol": d.symbol, "outcome": d.outcome, "reasons": d.reasons[:4]}
                                               for d in outcome.decisions[:20]]}},
            "candidates": cands, "regime": {**scan.regime, "version": "regime-rules-1"}}})

    def save_assessment(self, pos, assessment, intent, now):
        super().save_assessment(pos, assessment, intent, now)
        if intent is not None:
            a = {k: v for k, v in assessment.items() if not k.startswith("_")}
            self.rec.append({"op": "assessment", "body": {"discord_user_id": pos.discord_user_id, "assessment": a,
                                                          "assessment_key": assessment["_key"] + ":" + assessment["asof_at"],
                                                          "intent": intent}})


def _bars_payload(df: pd.DataFrame, symbol: str, cal) -> list[dict]:
    out = []
    for ts, r in df.iterrows():
        day = ts.date()
        close_utc = cal.session(day).close_utc
        out.append({"symbol": symbol, "timeframe": "1Day", "session_date": day.isoformat(),
                    "start_at": iso_utc(cal.session(day).open_utc), "open": f"{r['open']:.2f}", "high": f"{r['high']:.2f}",
                    "low": f"{r['low']:.2f}", "close": f"{r['close']:.2f}", "volume": int(r["volume"]), "adjustment": "split",
                    "provenance": {"provider": "replay", "feed": "demo", "latency_class": "replay",
                                   "ts_received": iso_utc(close_utc + timedelta(minutes=15)), "is_demo": True},
                    "is_provisional": False, "asof_at": iso_utc(close_utc)})
    return out


def _research_calls(work: Path, now: datetime) -> list[dict]:
    """Research lab outputs: synthetic strategy reports (honest UNVALIDATED results) + red-team DEMO champion."""
    from apex_worker.research.pipeline import run_training

    key = load_signing_key(DEMO_MODEL_SIGNING_SEED)
    calls: list[dict] = []
    for source, sps in (("synthetic", 8), ("redteam", 8)):
        out = work / f"train-{source}"
        res = run_training(source, out, signing_key=key, now=now, code_sha="demo-seed", stocks_per_sector=sps,
                           start=date(2019, 1, 2))
        report = json.loads((out / "report.json").read_text())
        run_id = hashlib.sha256(f"{source}|{report['generated_at']}".encode()).hexdigest()[:16]
        calls.append({"op": "training_run", "body": {
            "workflow": "local_replay", "trigger": "local", "github_run_id": f"demo-{run_id}", "github_run_attempt": 1,
            "code_sha": "demo-seed", "started_at": iso_utc(now - timedelta(seconds=res.seconds + 5)), "finished_at": iso_utc(now),
            "status": "succeeded" if res.status == "succeeded" else "failed",
            "outcome": "candidate_registered" if res.candidates else "champion_retained",
            "minutes_used": "0", "report": {"summary": (out / "summary.md").read_text(), "decisions": res.decisions},
            "is_demo": True}})
        for sid, rep in res.reports.items():
            calls.append({"op": "backtest", "body": {"id": f"bt_{source}_{sid}", "strategy_id": sid, "report": rep,
                                                     "data_source": rep["data_source"], "is_demo": True,
                                                     "config_hash": hashlib.sha256(sid.encode()).hexdigest(),
                                                     "data_hash": "synthetic-fixture", "code_hash": "demo-seed",
                                                     "started_at": iso_utc(now - timedelta(minutes=2)),
                                                     "completed_at": iso_utc(now)}})
        for c in res.candidates:
            rel = out / "releases" / c["model_id"]
            calls.append({"op": "model_release", "body": {
                "manifest": json.loads((rel / "manifest.json").read_text()),
                "artifact_b64": base64.b64encode((rel / "model.json").read_bytes()).decode(),
                "promote": bool(c["promotable"]), "reason": res.decisions[c["strategy_id"]]["reason"]}})
    return calls


def export_web_seed(out_path: Path, work: Path, start: date = date(2026, 8, 3), end: date = date(2026, 10, 16)) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    store_path = work / "seed.sqlite3"
    for f in work.glob("seed.sqlite3*"):
        f.unlink()
    demo = LocalDemo(store_path, start, end)
    recorder: list[dict] = []
    demo.sink = RecordingSink(demo.store, recorder)
    demo.engine.sink = demo.sink
    steps: list[dict] = []
    charted: set[str] = set()
    orig_member = demo._member
    acked_keys: set[str] = set()

    def member(key: str, event: dict):
        res = orig_member(key, event)
        recorder.append({"op": "position_event", "body": {"discord_user_id": MEMBERS[key], "guild_id": DEMO_GUILD_ID,
                                                          "event": event, "at": iso_utc(demo.clock.now()),
                                                          "py_position_id": (res or {}).get("position_id")}})
        return res

    demo._member = member  # type: ignore[method-assign]

    def step_end(day: date) -> None:
        now = demo.clock.now()
        calls = list(recorder)
        recorder.clear()
        # chart bars: full history when a symbol first appears, then one bar per session
        symbols = {"DMSPY"} | {s["symbol"] for s, _ in demo.sink.open_signals(include_closed=True)}
        bars = []
        for sym in sorted(symbols):
            df = demo.market.adjusted(sym)
            df = df.loc[:pd.Timestamp(day)]
            if sym not in charted:
                bars += _bars_payload(df.iloc[-CHART_HISTORY_SESSIONS:], sym, demo.cal)
                charted.add(sym)
            elif pd.Timestamp(day) in df.index:
                bars += _bars_payload(df.loc[[pd.Timestamp(day)]], sym, demo.cal)
        obs = [q.to_contract(now, True) for q in demo.engine.cache.all().values()]
        calls.insert(0, {"op": "observations", "body": {"observations": obs, "bars": bars}})
        calls.insert(0, {"op": "heartbeat", "body": heartbeat(now, len(obs))})
        # delivery outcomes produced by the dry-run Discord adapter during this session
        for r in demo.store.outbox_rows():
            if r.state in ("sent", "deferred") and r.event_key not in acked_keys:
                acked_keys.add(r.event_key)
                msg = render(r.payload, b"demo").to_discord()
                calls.append({"op": "outbox_ack", "body": {"event_key": r.event_key,
                                                           "status": "delivered" if r.state == "sent" else "dm_blocked",
                                                           "rendered": msg if r.route.get("type") == "CHANNEL" else None}})
        steps.append({"session": day.isoformat(), "at": iso_utc(now), "calls": calls})

    demo.on_step_end = step_end
    demo.run()
    initial = max(1, int(len(steps) * 0.85))
    # the weekly research run lands at the dashboard's "present" so its validation window covers the remaining replay
    research_at = datetime.fromisoformat(steps[initial - 1]["at"].replace("Z", "+00:00"))
    steps[initial - 1]["calls"] = _research_calls(work, research_at) + steps[initial - 1]["calls"]
    steps[0]["calls"] = _catalyst_calls() + steps[0]["calls"]
    seed = {
        "version": SEED_VERSION, "is_demo": True, "generated_by": "apex export-web-seed",
        "warning": "SYNTHETIC DEMO DATA: fictional DM* tickers and prices. Not market data, not investment advice.",
        "agent_id": DEMO_AGENT_ID, "guild_id": DEMO_GUILD_ID, "owner_discord_id": MEMBERS["A"],
        "members": MEMBERS, "trusted_model_keys": {"1": public_from_signing_key(load_signing_key(DEMO_MODEL_SIGNING_SEED))},
        "initial_steps": initial, "steps": steps,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(seed, separators=(",", ":"), sort_keys=True) + "\n")
    return out_path


def heartbeat(now: datetime, quotes: int) -> dict:
    return {"agent_id": DEMO_AGENT_ID, "sent_at": iso_utc(now), "worker_version": "0.1.0", "release_mode": "DEMO",
            "secret_version_ack": 0,
            "components": {
                "market_feed": {"status": "OK", "last_ok_at": iso_utc(now), "detail": "DEMO replay (synthetic fixture)",
                                "metrics": {"feed": "demo", "subscriptions": min(quotes, 25), "subscription_cap": 25,
                                            "plan_limit": 30}},
                "discord_gateway": {"status": "OK", "last_ok_at": iso_utc(now), "detail": "dry-run adapter (no token in DEMO)"},
                "scanner": {"status": "OK", "last_ok_at": iso_utc(now), "detail": "end-of-day scan complete"},
                "spool": {"status": "OK", "metrics": {"pending": 0}},
                "model": {"status": "OK", "detail": "no live champion; research setups are UNVALIDATED"},
            },
            "resources": {"rss_mb": 212.0, "mem_available_mb": 3100.0, "disk_free_pct": 71.0, "cpu_temp_c": 51.0, "load_1m": 0.4,
                          "spool_pending": 0, "clock_synced": True}}


def _catalyst_calls() -> list[dict]:
    """Fictional catalyst fixtures parsed by the real Phase 05 parsers (DEMO issuers)."""
    try:
        from apex_worker.demo.catalysts import demo_catalysts
    except ImportError:
        demo_catalysts = None
    items: list[dict] = []
    if demo_catalysts is not None:
        items = demo_catalysts()
    return [{"op": "catalysts", "body": {"items": items}}] if items else []


def _now() -> datetime:  # pragma: no cover
    return datetime.now(UTC)
