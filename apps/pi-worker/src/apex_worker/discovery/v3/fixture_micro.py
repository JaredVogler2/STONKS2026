"""SYNTHETIC intraday emulation for the execution-cost study's DEMO path and tests (never market data).

Extends the daily-bar FakeAlpaca with raw daily bars, one-minute bars and NBBO quotes derived deterministically from the
fixture's daily bars: the midquote moves linearly from the session open to its close, and each symbol has a fixed
half-spread of 1-9 bps. Every quote window also returns one crossed quote first, so consumers must filter.
"""

from __future__ import annotations

import zlib

import httpx
import pandas as pd

from apex_worker.market.calendar import default_calendar
from apex_worker.research.realeq.fixture import NY, FakeAlpaca


def fixture_half_spread_bps(symbol: str) -> float:
    return float(1 + zlib.crc32(symbol.encode()) % 9)


class FakeAlpacaMicro(FakeAlpaca):
    def __init__(self, frames: dict[str, pd.DataFrame], corporate_actions: list[dict] | None = None,
                 deny_quotes: bool = False):
        super().__init__(frames, corporate_actions)
        self.cal = default_calendar()
        self.deny_quotes = deny_quotes
        self.micro_requests: list[dict] = []
        self._daily = {s: {d.date(): r for d, r in df[["open", "close"]].iterrows()} for s, df in frames.items()}

    def _mid(self, sym: str, at: pd.Timestamp) -> float | None:
        day = at.tz_convert(NY).date()
        r = self._daily.get(sym, {}).get(day)
        if r is None or not self.cal.is_session(day):
            return None
        s = self.cal.session(day)
        span = (s.close_utc - s.open_utc).total_seconds()
        f = min(1.0, max(0.0, (at.to_pydatetime() - s.open_utc).total_seconds() / span))
        return float(r["open"] + (r["close"] - r["open"]) * f)

    def handler(self, req: httpx.Request) -> httpx.Response:
        p = dict(req.url.params)
        if req.url.path == "/v2/stocks/quotes":
            self.micro_requests.append(p)
            if self.deny_quotes:
                return httpx.Response(403, json={"message": "subscription does not permit querying recent SIP data"})
            sym, end = p["symbols"], pd.Timestamp(p["end"])
            m = self._mid(sym, end)
            if m is None:
                return httpx.Response(200, json={"quotes": {}, "next_page_token": None})
            h = fixture_half_spread_bps(sym) / 1e4
            stamp = end.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
            q = [{"t": stamp, "bp": m * 1.001, "bs": 1, "ap": m * 0.999, "as": 1, "bx": "Q", "ax": "Q", "c": ["R"]},
                 {"t": stamp, "bp": m * (1 - h), "bs": 2, "ap": m * (1 + h), "as": 3, "bx": "Q", "ax": "N", "c": ["R"]}]
            return httpx.Response(200, json={"quotes": {sym: q}, "next_page_token": None})
        if req.url.path == "/v2/stocks/bars" and p.get("timeframe") == "1Day" and p.get("adjustment") == "raw":
            self.micro_requests.append(p)  # the fixture has no splits: raw equals split-adjusted
            return super().handler(httpx.Request("GET", req.url.copy_set_param("adjustment", "split"),
                                                 headers=req.headers))
        if req.url.path == "/v2/stocks/bars" and p.get("timeframe") == "1Min":
            self.micro_requests.append(p)
            sym = p["symbols"]
            start = pd.Timestamp(p["start"])
            day = start.tz_convert(NY).date()
            bars = []
            if sym in self._daily and day in self._daily[sym] and self.cal.is_session(day):
                s = self.cal.session(day)
                t = pd.Timestamp(s.open_utc)
                while t < pd.Timestamp(s.close_utc):
                    a, b = self._mid(sym, t), self._mid(sym, t + pd.Timedelta(minutes=1))
                    bars.append({"t": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "o": a, "h": max(a, b), "l": min(a, b),
                                 "c": b, "v": 1000, "n": 10, "vw": (a + b) / 2})
                    t += pd.Timedelta(minutes=1)
            return httpx.Response(200, json={"bars": {sym: bars} if bars else {}, "next_page_token": None})
        return super().handler(req)

