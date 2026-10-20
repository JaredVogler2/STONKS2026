"""Offline replay adapter: the exact MarketDataAdapter interface over the deterministic synthetic market.

It never returns data from the future of its ReplayClock: daily bars stop at the latest completed session,
the current session's provisional bar is built only from intraday bars that have closed, and latest prints
come from the intraday path at the replay instant. Faults (429, disconnects, stale symbols, bad prints) can
be injected for reliability tests. Every value is stamped provider=replay, feed=demo.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from apex_worker.demo.synthetic import SyntheticMarket
from apex_worker.market.adapters.base import AssetInfo, LatestPrint, ProviderHealth
from apex_worker.market.calendar import MarketCalendar, default_calendar
from apex_worker.timeutil import ReplayClock


class ProviderRateLimited(RuntimeError):
    def __init__(self, retry_after: float):
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


class ProviderUnavailable(RuntimeError):
    pass


@dataclass
class Faults:
    rate_limit_next: int = 0
    retry_after: float = 2.0
    unavailable: bool = False
    stale_symbols: set[str] = field(default_factory=set)
    bad_prints: dict[str, float] = field(default_factory=dict)


def intraday_path(symbol: str, day: date, o: float, h: float, low: float, c: float, n: int) -> np.ndarray:
    """Deterministic Brownian-bridge path of n+1 points from open to close, clamped to [low, high]."""
    seed = int(hashlib.sha256(f"{symbol}|{day.isoformat()}".encode()).hexdigest()[:12], 16)
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 1, n)
    walk = np.concatenate([[0.0], np.cumsum(steps)])
    t = np.linspace(0, 1, n + 1)
    bridge = walk - t * walk[-1]
    scale = (h - low) / (np.ptp(bridge) + 1e-9) * 0.9
    path = o + (c - o) * t + bridge * scale
    path = np.clip(path, low, h)
    path[0], path[-1] = o, c
    return path


class ReplayAdapter:
    provider = "replay"

    def __init__(
        self,
        market: SyntheticMarket,
        clock: ReplayClock,
        calendar: MarketCalendar | None = None,
        faults: Faults | None = None,
    ):
        self.market = market
        self.clock = clock
        self.cal = calendar or default_calendar()
        self.faults = faults or Faults()
        self._last_ok: datetime | None = None
        self._last_error: str | None = None
        self.calls = 0

    def _gate(self) -> None:
        self.calls += 1
        if self.faults.unavailable:
            self._last_error = "provider unavailable (injected)"
            raise ProviderUnavailable(self._last_error)
        if self.faults.rate_limit_next > 0:
            self.faults.rate_limit_next -= 1
            self._last_error = "429 (injected)"
            raise ProviderRateLimited(self.faults.retry_after)
        self._last_ok = self.clock.now()

    async def get_assets(self) -> list[AssetInfo]:
        self._gate()
        out = []
        for s, spec in sorted(self.market.assets.items()):
            listed = spec.listed_from is None or spec.listed_from <= self.clock.now().date()
            delisted = spec.delisted_on is not None and spec.delisted_on <= self.clock.now().date()
            out.append(
                AssetInfo(
                    s,
                    spec.name,
                    spec.exchange,
                    "us_equity",
                    "etf" if spec.asset_kind in ("etf", "benchmark") else spec.instrument,
                    tradable=listed and not delisted and spec.exchange != "OTC",
                    fractionable=True,
                    status="inactive" if (delisted or not listed) else "active",
                    attributes=spec.tags,
                    sector=spec.sector,
                    leveraged=spec.leveraged,
                )
            )
        return out

    def _completed_session(self) -> pd.Timestamp:
        return pd.Timestamp(self.cal.latest_completed_session(self.clock.now()))

    async def get_daily_bars(
        self, symbols: list[str], start: date, end: date, adjustment: str = "split", feed: str = "demo"
    ) -> dict[str, pd.DataFrame]:
        self._gate()
        cutoff = min(pd.Timestamp(end), self._completed_session())
        out = {}
        for s in symbols:
            if s not in self.market.bars:
                continue
            df = self.market.adjusted(s) if adjustment in ("split", "all") else self.market.bars[s]
            out[s] = df.loc[pd.Timestamp(start) : cutoff].copy()
        return out

    async def get_intraday_bars(
        self, symbols: list[str], start: datetime, end: datetime, timeframe: str = "15Min", feed: str = "demo"
    ) -> dict[str, pd.DataFrame]:
        self._gate()
        now = self.clock.now()
        end = min(end, now)
        minutes = {"15Min": 15, "5Min": 5, "1Min": 1}[timeframe]
        out: dict[str, pd.DataFrame] = {}
        for s in symbols:
            df = self.market.bars.get(s)
            if df is None:
                continue
            rows = []
            for day in self.cal.sessions_between(start.date(), end.date()):
                ts = pd.Timestamp(day)
                if ts not in df.index:
                    continue
                sess = self.cal.session(day)
                n = int((sess.close_utc - sess.open_utc).total_seconds() // (minutes * 60))
                b = df.loc[ts]
                path = intraday_path(s, day, b["open"], b["high"], b["low"], b["close"], n)
                vols = np.full(n, b["volume"] / n)
                for k in range(n):
                    bstart = sess.open_utc + timedelta(minutes=minutes * k)
                    bend = bstart + timedelta(minutes=minutes)
                    if bend > end or bstart < start:
                        continue
                    seg = path[k : k + 2]
                    rows.append(
                        {
                            "start_at": bstart,
                            "open": seg[0],
                            "high": seg.max(),
                            "low": seg.min(),
                            "close": seg[-1],
                            "volume": int(vols[k]),
                        }
                    )
            out[s] = pd.DataFrame(rows).set_index("start_at") if rows else pd.DataFrame()
        return out

    async def get_latest(self, symbols: list[str], feed: str = "demo") -> dict[str, LatestPrint]:
        self._gate()
        now = self.clock.now()
        info = self.cal.phase(now)
        out = {}
        for s in symbols:
            df = self.market.bars.get(s)
            if df is None or df.empty:
                continue
            if s in self.faults.stale_symbols:
                continue  # feed silently stops for this symbol
            if info.phase.value == "OPEN" and info.session and pd.Timestamp(info.session.day) in df.index:
                day = info.session.day
                b = df.loc[pd.Timestamp(day)]
                n = int((info.session.close_utc - info.session.open_utc).total_seconds() // 60)
                path = intraday_path(s, day, b["open"], b["high"], b["low"], b["close"], n)
                k = min(n, int((now - info.session.open_utc).total_seconds() // 60))
                px, ts = float(path[k]), info.session.open_utc + timedelta(minutes=k)
            else:
                last_day = self.cal.latest_completed_session(now)
                hist = df.loc[: pd.Timestamp(last_day)]
                if hist.empty:
                    continue
                px = float(hist["close"].iloc[-1])
                ts = self.cal.session(hist.index[-1].date()).close_utc
            if s in self.faults.bad_prints:
                px = self.faults.bad_prints[s]
            out[s] = LatestPrint(s, round(px, 4), 100, ts, "demo", bid=round(px - 0.01, 4), ask=round(px + 0.01, 4))
        return out

    async def get_corporate_actions(self, symbols: list[str], start: date, end: date) -> list[dict] | None:
        self._gate()
        return [
            a
            for a in self.market.corporate_actions
            if a["symbol"] in symbols and start.isoformat() <= a["effective_date"] <= end.isoformat()
        ]

    def health(self) -> ProviderHealth:
        status = "DOWN" if self.faults.unavailable else ("DEGRADED" if self.faults.rate_limit_next else "OK")
        return ProviderHealth(
            "replay",
            status,
            "demo",
            self._last_ok,
            self._last_error,
            detail="DEMO replay of synthetic fixture data - not market data",
        )
