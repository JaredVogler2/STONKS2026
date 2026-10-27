"""Nightly tradable-universe refresh for the live worker (bounded memory, bounded requests).

Assets are pre-filtered on metadata alone (listed exchange, instrument type, active/tradable) so bars are fetched
only for plausible names, in chunks of `chunk` symbols. Liquidity filters use SIP (consolidated, delayed) daily bars,
never IEX-only volume. The top `max_symbols` eligible names by median dollar volume are persisted to
`<state_dir>/universe.json` and scanned at the end of each session.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from apex_worker.features.panel import AssetMeta
from apex_worker.market.adapters.base import MarketDataAdapter
from apex_worker.market.calendar import MarketCalendar
from apex_worker.market.universe import LISTED_EXCHANGES, UniverseFilters, UniverseOverrides, evaluate_universe
from apex_worker.timeutil import iso_utc

UNIVERSE_FILE = "universe.json"
EXCLUDED_INSTRUMENTS = {"warrant", "unit", "right", "preferred"}


def filters_from_config(u: dict) -> tuple[UniverseFilters, UniverseOverrides]:
    f = UniverseFilters(
        min_price=float(u.get("min_price", 10)), min_median_dollar_volume=float(u.get("min_median_dollar_volume", 30e6)),
        min_avg_volume=float(u.get("min_avg_volume", 1e6)), min_history_sessions=int(u.get("min_history_sessions", 180)),
        include_etfs=bool(u.get("include_etfs", True)), exclude_leveraged=bool(u.get("exclude_leveraged", True)),
    )
    ov = UniverseOverrides(set(u.get("include") or []), set(u.get("exclude") or []), set(u.get("user_verified") or []))
    return f, ov


async def refresh_universe(adapter: MarketDataAdapter, cal: MarketCalendar, now: datetime, universe_cfg: dict, *,
                           max_symbols: int = 400, chunk: int = 100) -> dict:
    filters, overrides = filters_from_config(universe_cfg)
    assets = await adapter.get_assets()
    pre = [a for a in assets if a.status == "active" and a.tradable and a.exchange in LISTED_EXCHANGES
           and a.instrument not in EXCLUDED_INSTRUMENTS and a.symbol not in overrides.exclude
           and not (a.leveraged and filters.exclude_leveraged)]
    end = cal.latest_completed_session(now)
    start = end - timedelta(days=int(filters.min_history_sessions * 1.6) + 10)
    ranked: list[tuple[float, str]] = []
    meta: dict[str, dict] = {}
    for i in range(0, len(pre), chunk):
        part = pre[i : i + chunk]
        bars = await adapter.get_daily_bars([a.symbol for a in part], start, end, adjustment="split", feed="sip")
        for d in evaluate_universe(part, bars, filters, overrides):
            if not d.eligible:
                continue
            tail = bars[d.symbol].iloc[-20:]
            ranked.append((float((tail["close"] * tail["volume"]).median()), d.symbol))
            meta[d.symbol] = {"exchange": d.asset.exchange, "instrument": d.asset.instrument, "name": d.asset.name,
                              "tradability_status": d.tradability_status, "leveraged": d.asset.leveraged,
                              "sector": d.asset.sector}
        del bars  # keep peak memory to one chunk on the 4 GB Pi
    keep = [s for _v, s in sorted(ranked, reverse=True)[:max_symbols]]
    return {"asof_session": end.isoformat(), "generated_at": iso_utc(now), "feed": "sip", "assets_listed": len(assets),
            "prefiltered": len(pre), "eligible": len(ranked), "symbols": keep, "meta": {s: meta[s] for s in keep}}


def snapshot_sectors() -> dict[str, str]:
    """S&P 500 symbol -> sector (the live keys of LIVE_SECTOR_ETFS) from the pinned snapshot (APEX-013: Alpaca assets
    carry no sector, so every live name used to share one "unknown" bucket and its caps)."""
    from apex_worker.research.realeq.universe_v2 import snapshot

    return {sym: sector for sym, sector, *_rest in snapshot()["rows"] if sector}


def save_universe(state_dir: Path, data: dict) -> Path:
    path = state_dir / UNIVERSE_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True))
    os.replace(tmp, path)
    return path


def load_universe(state_dir: Path) -> dict:
    path = state_dir / UNIVERSE_FILE
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def asset_meta(universe: dict, sector_map: dict[str, str], sector_etfs: dict[str, str], benchmark: str) -> dict[str, AssetMeta]:
    out: dict[str, AssetMeta] = {benchmark: AssetMeta(benchmark, None, "benchmark", tradability_status="likely_eligible")}
    for sec, etf in sector_etfs.items():
        out[etf] = AssetMeta(etf, sec, "etf", instrument="etf", tradability_status="likely_eligible")
    for sym, m in (universe.get("meta") or {}).items():
        if sym in out:
            continue
        out[sym] = AssetMeta(sym, sector_map.get(sym) or m.get("sector"), "etf" if m.get("instrument") == "etf" else "stock",
                             exchange=m.get("exchange"), instrument=m.get("instrument") or "common", name=m.get("name"),
                             tradability_status=m.get("tradability_status") or "unknown", leveraged=bool(m.get("leveraged")))
    return out
