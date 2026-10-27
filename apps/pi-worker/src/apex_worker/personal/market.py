"""Market data for the personal alerts, from Alpaca's data API only: split-adjusted SIP daily bars (signals, closes),
IEX last trades (prices during the session) and corporate actions (SPY's dividends, splits of held ETFs). No trading
endpoint is ever called, and nothing fetched is written anywhere but the private Discord channel's messages.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd


@dataclass(frozen=True)
class Request:
    daily: tuple[str, ...]  # daily bars for these symbols
    latest: tuple[str, ...]  # IEX last trades for these
    start: date
    end: date
    actions: tuple[str, ...] = ()  # corporate actions for these, from `actions_start`
    actions_start: date | None = None


@dataclass
class Snapshot:
    bars: dict[str, pd.DataFrame]  # open/high/low/close by session date (split-adjusted)
    latest: dict[str, tuple[float, datetime]] = field(default_factory=dict)  # IEX last trade price and time
    actions: list[dict] | None = None  # None: the provider did not serve corporate actions
    fetched_at: datetime | None = None

    def closes(self) -> dict[str, pd.Series]:
        return {s: df["close"].astype(float) for s, df in self.bars.items() if len(df)}

    def has_day(self, sym: str, day: date) -> bool:
        df = self.bars.get(sym)
        return df is not None and pd.Timestamp(day) in df.index

    def close_on(self, sym: str, day: date) -> float | None:
        df = self.bars.get(sym)
        ts = pd.Timestamp(day)
        return float(df.at[ts, "close"]) if df is not None and ts in df.index else None

    def last_close(self, sym: str, on_or_before: date | None = None) -> tuple[date, float] | None:
        df = self.bars.get(sym)
        if df is None or not len(df):
            return None
        if on_or_before is not None:
            df = df[df.index <= pd.Timestamp(on_or_before)]
            if not len(df):
                return None
        return df.index[-1].date(), float(df["close"].iloc[-1])

    def range_on(self, sym: str, day: date) -> tuple[float, float] | None:
        df = self.bars.get(sym)
        ts = pd.Timestamp(day)
        if df is None or ts not in df.index:
            return None
        return float(df.at[ts, "low"]), float(df.at[ts, "high"])

    def spy_dividends(self) -> list[tuple[date, Decimal]] | None:
        if self.actions is None:
            return None
        out = []
        for a in self.actions:
            if a.get("type") == "cash_dividends" and a.get("symbol") == "SPY" and a.get("ex_date") and a.get("rate"):
                out.append((date.fromisoformat(str(a["ex_date"])[:10]), Decimal(str(a["rate"]))))
        return sorted(set(out))

    def splits(self) -> list[dict]:
        """Forward and reverse splits: {symbol, ex_date, new, old}."""
        out = []
        for a in self.actions or []:
            if a.get("type") in ("forward_splits", "reverse_splits") and a.get("ex_date"):
                try:
                    new, old = float(a["new_rate"]), float(a["old_rate"])
                except (KeyError, TypeError, ValueError):
                    continue
                out.append({"symbol": a.get("symbol"), "ex_date": str(a["ex_date"])[:10], "new": new, "old": old})
        return out


async def _fetch(key_id: str, secret: str, req: Request) -> Snapshot:
    from apex_worker.market.adapters.alpaca import AlpacaAdapter

    ad = AlpacaAdapter(key_id, secret)
    try:
        bars = await ad.get_daily_bars(sorted(set(req.daily)), req.start, req.end, adjustment="split", feed="sip")
        latest = await ad.get_latest(sorted(set(req.latest)), feed="iex") if req.latest else {}
        actions = None
        if req.actions and req.actions_start:
            actions = await ad.get_corporate_actions(sorted(set(req.actions)), req.actions_start, req.end)
    finally:
        await ad.aclose()
    return Snapshot(bars, {s: (p.price, p.ts_exchange) for s, p in latest.items()}, actions, datetime.now(UTC))


def alpaca_source(key_id: str, secret: str):
    """A market source for the runner: Request -> Snapshot (one event loop per run)."""

    def source(req: Request) -> Snapshot:
        return asyncio.run(_fetch(key_id, secret, req))

    return source


__all__ = ["Request", "Snapshot", "alpaca_source"]
