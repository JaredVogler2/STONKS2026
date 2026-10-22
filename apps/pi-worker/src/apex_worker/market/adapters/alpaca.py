"""Alpaca market data adapter (Basic plan aware).

* Real time: IEX only (single venue). Never labelled consolidated/NBBO.
* Historical: daily/intraday bars; with feed=sip the most recent 15 minutes are not available on Basic,
  so requests clamp `end` to now-16min and the data is labelled SIP DELAYED.
* Hard ceiling 200 historical calls/minute (bucket default 190/min to leave headroom); 429 Retry-After
  honoured with jittered exponential backoff; 5xx retried a bounded number of times.
* Credentials are injected (from the Pi's decrypted envelope or manual provisioning); never logged.
API reference (verify at deployment): https://docs.alpaca.markets/us/docs/about-market-data-api
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, date, datetime, timedelta

import httpx
import pandas as pd

from apex_worker.market.adapters.base import AssetInfo, LatestPrint, ProviderHealth
from apex_worker.market.ratelimit import Priority, QuotaTracker, TokenBucket

DATA_BASE = "https://data.alpaca.markets"
TRADING_BASE = "https://paper-api.alpaca.markets"  # asset metadata is readable with paper keys; no orders are ever sent
SIP_DELAY = timedelta(minutes=16)
BATCH = 100
PAGE_LIMIT = 10000
INSTRUMENT_HINTS = (("warrant", ".WS"), ("unit", ".U"), ("right", ".R"))
LEVERAGE_WORDS = ("2X", "3X", "ULTRA", "-1X", "INVERSE", "LEVERAGED", "BEAR", "BULL 2", "BULL 3")


class AlpacaError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"Alpaca HTTP {status}: {message}")
        self.status = status


def _instrument(symbol: str, name: str | None, attributes: list[str]) -> tuple[str, bool]:
    n = (name or "").upper()
    for kind, suffix in INSTRUMENT_HINTS:
        if symbol.endswith(suffix):
            return kind, False
    if "PREFERRED" in n or "PFD" in n or ("." in symbol and symbol.rsplit(".", 1)[-1].startswith("P")):
        return "preferred", False
    if "ETF" in n or "FUND" in n or ("TRUST" in n and "SHARES" in n):
        return "etf", any(w in n for w in LEVERAGE_WORDS)
    if "ADR" in n or "ADS" in n or "DEPOSITARY" in n:
        return "adr", False
    return "common", False


class AlpacaAdapter:
    provider = "alpaca"

    def __init__(
        self,
        key_id: str,
        secret_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        bucket: TokenBucket | None = None,
        quota: QuotaTracker | None = None,
        max_retries: int = 4,
        sleep=asyncio.sleep,
        now=lambda: datetime.now(UTC),
    ):
        if not key_id or not secret_key:
            raise ValueError("Alpaca credentials not configured")
        self._headers = {
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept": "application/json",
            "User-Agent": "apex-worker/0.1 (private research; no order routing)",
        }
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))
        self.bucket = bucket or TokenBucket(rate_per_sec=190 / 60, capacity=20)
        self.quota = quota or QuotaTracker("alpaca", daily_limit=150_000)
        self.max_retries = max_retries
        self._sleep = sleep
        self._now = now
        self._last_ok: datetime | None = None
        self._last_error: str | None = None
        self.requests = 0

    async def _get(self, url: str, params: dict, priority: Priority = Priority.NORMAL) -> dict | list:
        attempt = 0
        while True:
            wait = self.bucket.try_acquire()
            while wait > 0:
                await self._sleep(wait)
                wait = self.bucket.try_acquire()
            self.quota.record(self._now().date().isoformat(), 1, priority)
            self.requests += 1
            try:
                resp = await self._client.get(url, params=params, headers=self._headers)
            except httpx.HTTPError as exc:
                self._last_error = f"network error: {type(exc).__name__}"
                if attempt >= self.max_retries:
                    raise AlpacaError(0, self._last_error) from None
                await self._sleep(min(30.0, 2**attempt + random.random()))  # noqa: S311 - jitter, not crypto
                attempt += 1
                continue
            if resp.status_code == 429:
                retry = float(resp.headers.get("Retry-After") or 0) or min(60.0, 2**attempt + random.random())  # noqa: S311
                self.bucket.penalize(retry)
                self._last_error = "429 rate limited"
                if attempt >= self.max_retries:
                    raise AlpacaError(429, "rate limited")
                await self._sleep(retry)
                attempt += 1
                continue
            if resp.status_code >= 500:
                self._last_error = f"{resp.status_code} server error"
                if attempt >= self.max_retries:
                    raise AlpacaError(resp.status_code, "server error")
                await self._sleep(min(30.0, 2**attempt + random.random()))  # noqa: S311
                attempt += 1
                continue
            if resp.status_code in (401, 403):
                self._last_error = "authentication failed (check key/secret or plan entitlements)"
                raise AlpacaError(resp.status_code, self._last_error)
            if resp.status_code >= 400:
                self._last_error = f"{resp.status_code} request rejected"
                raise AlpacaError(resp.status_code, resp.text[:200])
            self._last_ok = self._now()
            return resp.json()

    async def get_assets(self) -> list[AssetInfo]:
        data = await self._get(f"{TRADING_BASE}/v2/assets", {"status": "active", "asset_class": "us_equity"})
        out = []
        for a in data:  # type: ignore[union-attr]
            inst, lev = _instrument(a.get("symbol", ""), a.get("name"), a.get("attributes") or [])
            out.append(
                AssetInfo(
                    a["symbol"],
                    a.get("name"),
                    a.get("exchange"),
                    a.get("class", "us_equity"),
                    inst,
                    bool(a.get("tradable")),
                    a.get("fractionable"),
                    a.get("status", "active"),
                    tuple(a.get("attributes") or ()),
                    None,
                    lev,
                )
            )
        return out

    def _clamp_end(self, end: datetime, feed: str) -> datetime:
        if feed == "sip":
            return min(end, self._now() - SIP_DELAY)
        return min(end, self._now())

    async def _bars(
        self,
        symbols: list[str],
        timeframe: str,
        start: datetime,
        end: datetime,
        adjustment: str,
        feed: str,
        priority: Priority,
    ) -> dict[str, pd.DataFrame]:
        out: dict[str, list[dict]] = {}
        end = self._clamp_end(end, feed)
        for i in range(0, len(symbols), BATCH):
            chunk = symbols[i : i + BATCH]
            token = None
            while True:
                params = {
                    "symbols": ",".join(chunk),
                    "timeframe": timeframe,
                    "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "limit": PAGE_LIMIT,
                    "adjustment": adjustment,
                    "feed": feed,
                    "sort": "asc",
                }
                if token:
                    params["page_token"] = token
                data = await self._get(f"{DATA_BASE}/v2/stocks/bars", params, priority)
                for sym, bars in (data.get("bars") or {}).items():  # type: ignore[union-attr]
                    out.setdefault(sym, []).extend(bars)
                token = data.get("next_page_token")  # type: ignore[union-attr]
                if not token:
                    break
        frames = {}
        for sym, bars in out.items():
            df = pd.DataFrame(bars).rename(
                columns={
                    "t": "start_at",
                    "o": "open",
                    "h": "high",
                    "l": "low",
                    "c": "close",
                    "v": "volume",
                    "n": "trade_count",
                    "vw": "vwap",
                }
            )
            df["start_at"] = pd.to_datetime(df["start_at"], utc=True)
            frames[sym] = df.set_index("start_at").sort_index()
        return frames

    async def get_daily_bars(
        self, symbols: list[str], start: date, end: date, adjustment: str = "split", feed: str = "sip"
    ) -> dict[str, pd.DataFrame]:
        frames = await self._bars(
            symbols,
            "1Day",
            datetime(start.year, start.month, start.day, tzinfo=UTC),
            datetime(end.year, end.month, end.day, 23, 59, tzinfo=UTC),
            adjustment,
            feed,
            Priority.OPTIONAL,
        )
        out = {}
        for sym, df in frames.items():
            # daily bars are stamped at 04:00/05:00 UTC (midnight ET): convert to exchange session dates
            idx = pd.DatetimeIndex(df.index.tz_convert("America/New_York").date, name="session_date")
            out[sym] = df.set_axis(pd.to_datetime(idx)).astype(
                {"open": float, "high": float, "low": float, "close": float}
            )
        return out

    async def get_intraday_bars(
        self, symbols: list[str], start: datetime, end: datetime, timeframe: str = "15Min", feed: str = "iex"
    ) -> dict[str, pd.DataFrame]:
        return await self._bars(symbols, timeframe, start, end, "raw", feed, Priority.NORMAL)

    async def get_latest(self, symbols: list[str], feed: str = "iex") -> dict[str, LatestPrint]:
        out: dict[str, LatestPrint] = {}
        for i in range(0, len(symbols), BATCH):
            chunk = ",".join(symbols[i : i + BATCH])
            trades = await self._get(
                f"{DATA_BASE}/v2/stocks/trades/latest", {"symbols": chunk, "feed": feed}, Priority.ESSENTIAL
            )
            quotes = await self._get(
                f"{DATA_BASE}/v2/stocks/quotes/latest", {"symbols": chunk, "feed": feed}, Priority.ESSENTIAL
            )
            qmap = (quotes.get("quotes") or {}) if isinstance(quotes, dict) else {}
            for sym, t in ((trades.get("trades") or {}) if isinstance(trades, dict) else {}).items():
                q = qmap.get(sym) or {}
                out[sym] = LatestPrint(
                    sym,
                    float(t["p"]),
                    int(t.get("s") or 0),
                    pd.Timestamp(t["t"]).to_pydatetime().astimezone(UTC),
                    feed,
                    bid=float(q["bp"]) if q.get("bp") is not None else None,
                    ask=float(q["ap"]) if q.get("ap") is not None else None,
                )
        return out

    async def get_corporate_actions(
        self, symbols: list[str], start: date, end: date, types: str = "forward_split,reverse_split,cash_dividend"
    ) -> list[dict] | None:
        try:
            data = await self._get(
                f"{DATA_BASE}/v1beta1/corporate-actions",
                {
                    "symbols": ",".join(symbols[:BATCH]),
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "types": types,
                },
                Priority.OPTIONAL,
            )
        except AlpacaError as exc:
            if exc.status in (403, 404):
                return None  # unsupported for this plan: caller flags a data-quality limitation instead of guessing
            raise
        actions = []
        for kind, rows in (data.get("corporate_actions") or {}).items():  # type: ignore[union-attr]
            for r in rows:
                actions.append({"type": kind, **r})
        return actions

    async def get_corporate_actions_paged(
        self, symbols: list[str], start: date, end: date, types: str = "forward_split,reverse_split,spin_off"
    ) -> list[dict] | None:
        """Every symbol (batches of BATCH) and every page. A batch whose long window is rejected (400/422) is retried in
        calendar-year windows. None = endpoint unsupported for this plan (403/404) or still rejected per year."""
        actions: list[dict] = []

        async def window(chunk: list[str], a: date, b: date) -> list[dict]:
            got, token = [], None
            while True:
                params = {"symbols": ",".join(chunk), "start": a.isoformat(), "end": b.isoformat(), "types": types,
                          "limit": 1000}
                if token:
                    params["page_token"] = token
                data = await self._get(f"{DATA_BASE}/v1beta1/corporate-actions", params, Priority.OPTIONAL)
                for kind, rows in (data.get("corporate_actions") or {}).items():  # type: ignore[union-attr]
                    got += [{"type": kind, **r} for r in rows]
                token = data.get("next_page_token")  # type: ignore[union-attr]
                if not token:
                    return got

        for i in range(0, len(symbols), BATCH):
            chunk = symbols[i : i + BATCH]
            try:
                actions += await window(chunk, start, end)
                continue
            except AlpacaError as exc:
                if exc.status in (403, 404):
                    return None
                if exc.status not in (400, 422):
                    raise
            for year in range(start.year, end.year + 1):
                a, b = max(start, date(year, 1, 1)), min(end, date(year, 12, 31))
                try:
                    actions += await window(chunk, a, b)
                except AlpacaError as exc:
                    if exc.status in (400, 403, 404, 422):
                        return None
                    raise
        return actions

    def health(self) -> ProviderHealth:
        status = "OK" if self._last_ok and not self._last_error else ("DEGRADED" if self._last_ok else "UNKNOWN")
        return ProviderHealth(
            "alpaca",
            status,
            "iex",
            self._last_ok,
            self._last_error,
            quota=self.quota.snapshot(),
            detail="IEX single-venue real time; SIP historical delayed >=15 min on Basic",
        )

    async def aclose(self) -> None:
        await self._client.aclose()
