"""DEMO Flow A: offline, keyless, deterministic end-to-end journey.

synthetic market -> replay adapter -> scan -> policy -> publish (outbox) -> dry-run Discord embed ->
three members act privately (A opens+adds, B buys later with DMs blocked, C watches) -> replay advances
sessions -> independent private assessments -> A trims and closes -> signal outcome and member P&L stay
separate. Everything is stamped DEMO.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from apex_worker.demo.synthetic import SECTORS, generate
from apex_worker.discord_bot.dryrun import DryRunDiscord
from apex_worker.features.panel import AssetMeta
from apex_worker.market.adapters.replay import ReplayAdapter
from apex_worker.market.calendar import default_calendar
from apex_worker.portfolio.ledger import LedgerError
from apex_worker.runtime.engine import ApexEngine, EngineConfig
from apex_worker.runtime.local_sink import LocalSink
from apex_worker.services.positions import LocalPositionService, ServiceError
from apex_worker.storage.local import LocalStore
from apex_worker.timeutil import ReplayClock, iso_utc

DEMO_COMPONENT_SECRET = b"apex-demo-component-secret-not-for-production"
MEMBERS = {"A": "100000000000000001", "B": "100000000000000002", "C": "100000000000000003"}


def demo_meta(market) -> dict[str, AssetMeta]:
    return {
        s: AssetMeta(
            symbol=s,
            sector=spec.sector if spec.sector in SECTORS else None,
            asset_kind=spec.asset_kind,
            exchange=spec.exchange,
            instrument=spec.instrument,
            name=spec.name,
            tradability_status="likely_eligible" if spec.exchange != "OTC" else "unknown",
            leveraged=spec.leveraged,
            tags=spec.tags,
        )
        for s, spec in market.assets.items()
    }


@dataclass
class DemoResult:
    store_path: str
    sessions: list[str]
    signals: list[dict]
    member_positions: dict[str, list[dict]]
    transcript: list[dict]
    deferred_private: int
    log: list[str] = field(default_factory=list)


class LocalDemo:
    def __init__(
        self,
        store_path: Path,
        start: date = date(2026, 8, 3),
        end: date = date(2026, 10, 16),
        *,
        transcript_file: Path | None = None,
    ):
        self.cal = default_calendar()
        self.market = generate()
        self.clock = ReplayClock(self.cal.session(self.cal.previous_session(start)).close_utc + timedelta(minutes=15))
        self.store = LocalStore(store_path)
        self.sink = LocalSink(self.store)
        self.adapter = ReplayAdapter(self.market, self.clock, self.cal)
        cfg = EngineConfig(
            release_mode="DEMO",
            benchmark="DMSPY",
            sector_etfs={k: v[0] for k, v in SECTORS.items()},
            lookback_sessions=320,
        )
        earnings = {s: set(v) for s, v in self.market.earnings.items()}

        def earnings_status(symbol: str, day: str, horizon: int) -> str:
            if symbol not in earnings:
                return "UNKNOWN"
            d = date.fromisoformat(day)
            window = {
                x.isoformat()
                for x in self.cal.sessions_between(
                    d + timedelta(days=1),
                    self.cal.add_sessions(
                        self.cal.latest_completed_session(self.cal.session(d).close_utc)
                        if self.cal.is_session(d)
                        else d,
                        horizon,
                    ),
                )
            }
            return "WITHIN_HORIZON" if window & earnings[symbol] else "NONE_WITHIN_HORIZON_CONFIRMED"

        self.engine = ApexEngine(
            self.adapter,
            self.sink,
            self.clock,
            cfg,
            calendar=self.cal,
            meta=demo_meta(self.market),
            earnings_status=earnings_status,
        )
        self.positions = LocalPositionService(self.store, is_demo=True)
        self.discord = DryRunDiscord(
            self.store, DEMO_COMPONENT_SECRET, dm_blocked={MEMBERS["B"]}, out_file=transcript_file
        )
        self.start, self.end = start, end
        self.log: list[str] = []
        self.on_step_end = None  # optional callback(day) used by the web-seed exporter
        for key, uid in MEMBERS.items():
            user = self.store.ensure_member(uid, self.clock.now())
            self.store.set_dm_opt_in(user, key != "C")  # C never opted in to DMs

    async def _session(self, day: date) -> None:
        sess = self.cal.session(day)
        symbols = sorted(self.market.bars)
        for minutes in (30, 210):  # two intraday checks
            self.clock.set(sess.open_utc + timedelta(minutes=minutes))
            open_syms = sorted(
                {s["symbol"] for s, _ in self.sink.open_signals()}
                | {p["symbol"] for p in self.store.all_open_positions()}
            )
            if open_syms:
                await self.engine.poll_quotes(open_syms)
                self.engine.evaluate_intraday()
                self.engine.assess_positions()
            self.discord.deliver_due(self.clock.now())
        self.clock.set(sess.close_utc + timedelta(minutes=15))
        await self.engine.refresh_daily(symbols)
        self.engine.build_panel()
        _scan, outcome = self.engine.end_of_day(day)
        for d in outcome.decisions:
            if d.signal:
                self.log.append(f"{day} PUBLISHED {d.outcome} {d.signal['symbol']} {d.signal['id']}")
        await self.engine.poll_quotes(sorted({p["symbol"] for p in self.store.all_open_positions()}) or ["DMSPY"])
        self.engine.assess_positions()
        self.discord.deliver_due(self.clock.now())

    def _member(self, key: str, event: dict) -> dict | None:
        try:
            res = self.positions.record(MEMBERS[key], event, self.clock.now())
            self.log.append(
                f"{self.clock.now().date()} member {key} {event['event_type']} -> {res.status} {res.warnings}"
            )
            return res.position
        except (LedgerError, ServiceError) as exc:
            self.log.append(f"member {key} {event['event_type']} rejected: {getattr(exc, 'code', '')} {exc}")
            return None

    def run(self) -> DemoResult:
        return asyncio.run(self._run())

    async def _run(self) -> DemoResult:
        days = self.cal.sessions_between(self.start, self.end)
        acted: dict[str, date] = {}
        state: dict = {}
        for _i, day in enumerate(days):
            await self._session(day)
            await self._members(day, acted, state)
            if self.on_step_end:
                self.on_step_end(day)
        deferred = sum(1 for r in self.store.outbox_rows() if r.state == "deferred")
        members = {k: self.positions.positions(uid, include_closed=True) for k, uid in MEMBERS.items()}
        signals = self.store.list_signals(limit=500)
        return DemoResult(
            str(self.store.path),
            [d.isoformat() for d in days],
            signals,
            members,
            self.discord.transcript,
            deferred,
            self.log,
        )

    async def _members(self, day: date, acted: dict, state: dict) -> None:
        target_signal = state.get("signal")
        a_pos = state.get("a")
        if target_signal is None:
            pubs = [s for s, st in self.sink.open_signals() if st in ("PUBLISHED", "ACTIVE")]
            if pubs:
                state["signal"] = sorted(pubs, key=lambda s: s["created_at"])[0]
                acted["published"] = day
            return
        sig = target_signal
        lv = sig["levels"]
        mid = (float(lv["entry_min"]) + float(lv["entry_max"])) / 2
        now = self.clock.now()
        fill_at = iso_utc(now - timedelta(hours=5))
        if "A_open" not in acted:
            a_pos = state["a"] = self._member(
                "A",
                {
                    "client_idempotency_key": f"demo-A-open-{sig['id']}",
                    "event_type": "OPEN",
                    "signal_id": sig["id"],
                    "source": "replay",
                    "fill": {"price": f"{mid:.2f}", "quantity": "20", "fees": "0", "fill_at": fill_at},
                },
            )
            # an accidental double click resends the same interaction: no second fill
            self._member(
                "A",
                {
                    "client_idempotency_key": f"demo-A-open-{sig['id']}",
                    "event_type": "OPEN",
                    "signal_id": sig["id"],
                    "source": "replay",
                    "fill": {"price": f"{mid:.2f}", "quantity": "20", "fees": "0", "fill_at": fill_at},
                },
            )
            self._member(
                "C",
                {
                    "client_idempotency_key": f"demo-C-watch-{sig['id']}",
                    "event_type": "WATCH",
                    "signal_id": sig["id"],
                    "source": "replay",
                },
            )
            acted["A_open"] = day
        elif "A_add" not in acted and a_pos:
            self._member(
                "A",
                {
                    "client_idempotency_key": f"demo-A-add-{sig['id']}",
                    "event_type": "ADD",
                    "position_id": a_pos["position_id"],
                    "source": "replay",
                    "fill": {
                        "price": f"{float(lv['entry_min']):.2f}",
                        "quantity": "10",
                        "fees": "0",
                        "fill_at": fill_at,
                    },
                },
            )
            state["b"] = self._member(
                "B",
                {
                    "client_idempotency_key": f"demo-B-open-{sig['id']}",
                    "event_type": "OPEN",
                    "signal_id": sig["id"],
                    "source": "replay",
                    "fill": {
                        "price": f"{float(lv['entry_max']) * 1.01:.2f}",
                        "quantity": "5",
                        "fees": "0",
                        "fill_at": fill_at,
                    },
                },
            )
            acted["A_add"] = day
        elif "A_trim" not in acted and a_pos and (day - acted["A_add"]).days >= 7:
            q = self.engine.cache.get(sig["symbol"])
            px = f"{q.price:.2f}" if q else lv["target_primary"]
            self._member(
                "A",
                {
                    "client_idempotency_key": f"demo-A-trim-{sig['id']}",
                    "event_type": "PARTIAL_EXIT",
                    "position_id": a_pos["position_id"],
                    "source": "replay",
                    "fill": {"price": px, "quantity": "15", "fees": "0", "fill_at": fill_at},
                },
            )
            acted["A_trim"] = day
        elif "A_close" not in acted and a_pos and "A_trim" in acted and (day - acted["A_trim"]).days >= 5:
            q = self.engine.cache.get(sig["symbol"])
            px = f"{q.price:.2f}" if q else lv["target_primary"]
            self._member(
                "A",
                {
                    "client_idempotency_key": f"demo-A-close-{sig['id']}",
                    "event_type": "CLOSE",
                    "position_id": a_pos["position_id"],
                    "source": "replay",
                    "fill": {"price": px, "quantity": "15", "fees": "0", "fill_at": fill_at},
                },
            )
            acted["A_close"] = day


def main(out_dir: Path) -> DemoResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    store = out_dir / "demo.sqlite3"
    for f in (store, out_dir / "demo.sqlite3-wal", out_dir / "demo.sqlite3-shm", out_dir / "discord_transcript.jsonl"):
        if f.exists():
            f.unlink()
    res = LocalDemo(store, transcript_file=out_dir / "discord_transcript.jsonl").run()
    (out_dir / "demo_summary.json").write_text(
        json.dumps(
            {
                "is_demo": True,
                "sessions": [res.sessions[0], res.sessions[-1]],
                "signals": len(res.signals),
                "messages": len(res.transcript),
                "deferred_private_notifications": res.deferred_private,
                "log": res.log,
                "members": {
                    k: [
                        {
                            "symbol": p["symbol"],
                            "state": p["state"],
                            "avg_cost": p["avg_cost"],
                            "realized_pnl": p["realized_pnl"],
                            "off_plan": p["off_plan"],
                        }
                        for p in v
                    ]
                    for k, v in res.member_positions.items()
                },
            },
            indent=2,
        )
    )
    return res


if __name__ == "__main__":  # pragma: no cover
    import sys

    main(Path(sys.argv[1] if len(sys.argv) > 1 else ".apex-local/demo"))
