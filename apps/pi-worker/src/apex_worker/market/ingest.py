"""`apex ingest`: universe + daily bar refresh through a MarketDataAdapter (fixture or Alpaca).

fixture  : deterministic replay of the synthetic DEMO market (no keys, no network)
alpaca   : real Alpaca Basic data; `--dry-run` validates credentials/quotas and reports what WOULD be fetched
           with a handful of requests, without a full download.
Bars are stored locally as compressed Parquet when pyarrow is available (Supabase only receives curated rows).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from apex_worker.config import WorkerConfig
from apex_worker.market.calendar import default_calendar
from apex_worker.market.universe import UniverseFilters, evaluate_universe, survivorship_warning


def _store_bars(bars: dict[str, pd.DataFrame], root: Path, feed: str) -> int:
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        return 0
    root.mkdir(parents=True, exist_ok=True)
    n = 0
    for sym, df in bars.items():
        if df.empty:
            continue
        df.to_parquet(root / f"{sym.replace('.', '_')}.{feed}.parquet", compression="zstd")
        n += 1
    return n


async def run_ingest(
    mode: str, cfg: WorkerConfig, *, dry_run: bool = False, symbols: list[str] | None = None, asof: date | None = None
) -> dict:
    cal = default_calendar()
    now = datetime.now(UTC)
    if mode == "fixture":
        from apex_worker.demo.synthetic import generate
        from apex_worker.market.adapters.replay import ReplayAdapter
        from apex_worker.timeutil import ReplayClock

        day = asof or date(2026, 10, 16)
        clock = ReplayClock(cal.session(day).close_utc + timedelta(minutes=20))
        adapter = ReplayAdapter(generate(), clock, cal)
        feed = "demo"
    elif mode == "alpaca":
        if not cfg.has_market_credentials():
            return {
                "status": "disabled",
                "mode": mode,
                "detail": "DISABLED - Alpaca credentials not provisioned to this Pi "
                "(configure in dashboard Settings, then `apex pair`)",
            }
        from apex_worker.market.adapters.alpaca import AlpacaAdapter

        adapter = AlpacaAdapter(cfg.alpaca_key_id or "", cfg.alpaca_secret_key or "")
        feed = "sip"
        clock = None
    else:
        return {"status": "error", "detail": f"unknown mode {mode}"}

    assets = await adapter.get_assets()
    wanted = [a.symbol for a in assets if (not symbols or a.symbol in symbols)]
    end = asof or cal.latest_completed_session(clock.now() if clock else now)
    start = cal.sessions_between(end - timedelta(days=420), end)[0]
    if dry_run:
        probe = wanted[:5]
        bars = await adapter.get_daily_bars(probe, end - timedelta(days=10), end, adjustment="split", feed=feed)
        return {
            "status": "dry_run",
            "mode": mode,
            "assets_listed": len(assets),
            "would_fetch_symbols": len(wanted),
            "estimated_requests": max(1, len(wanted) // 100 + 1) * 3,
            "probe": {s: len(b) for s, b in bars.items()},
            "health": adapter.health().__dict__ | {"last_success_at": str(adapter.health().last_success_at)},
            "feed_label": "DEMO" if mode == "fixture" else "SIP HISTORICAL (delayed >= 15 min on Basic)",
        }
    bars = await adapter.get_daily_bars(wanted, start, end, adjustment="split", feed=feed)
    decisions = evaluate_universe(assets, bars, UniverseFilters())
    eligible = [d.symbol for d in decisions if d.eligible]
    stored = _store_bars(bars, cfg.state_dir / "bars", feed)
    excluded: dict[str, int] = {}
    for d in decisions:
        for r in d.reasons:
            excluded[r] = excluded.get(r, 0) + 1
    actions = await adapter.get_corporate_actions(eligible[:100], start, end)
    return {
        "status": "ok",
        "mode": mode,
        "asof_session": str(end),
        "assets": len(assets),
        "with_bars": len(bars),
        "eligible": len(eligible),
        "excluded_by_reason": dict(sorted(excluded.items())),
        "parquet_files": stored,
        "corporate_actions": "unsupported by provider plan - split handling relies on adjusted bars"
        if actions is None
        else len(actions),
        "survivorship_warning": survivorship_warning(decisions),
        "is_demo": mode == "fixture",
        "feed_label": "DEMO" if mode == "fixture" else "SIP HISTORICAL (delayed)",
    }
