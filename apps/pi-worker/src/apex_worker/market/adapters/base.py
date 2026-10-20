"""MarketDataAdapter interface implemented by the Alpaca adapter and the offline replay adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

import pandas as pd


@dataclass(frozen=True)
class AssetInfo:
    symbol: str
    name: str | None
    exchange: str | None
    asset_class: str
    instrument: str  # common | etf | warrant | preferred | unit | adr | right
    tradable: bool
    fractionable: bool | None
    status: str  # active | inactive
    attributes: tuple[str, ...] = ()
    sector: str | None = None
    leveraged: bool = False


@dataclass(frozen=True)
class LatestPrint:
    symbol: str
    price: float
    size: int | None
    ts_exchange: datetime
    feed: str
    bid: float | None = None
    ask: float | None = None


@dataclass
class ProviderHealth:
    provider: str
    status: str  # OK | DEGRADED | DOWN | DISABLED | UNCONFIGURED
    feed: str
    last_success_at: datetime | None = None
    last_error: str | None = None
    last_reconnect_at: datetime | None = None
    quota: dict = field(default_factory=dict)
    detail: str = ""


class MarketDataAdapter(Protocol):
    provider: str

    async def get_assets(self) -> list[AssetInfo]: ...

    async def get_daily_bars(self, symbols: list[str], start: date, end: date, adjustment: str = "split",
                             feed: str = "iex") -> dict[str, pd.DataFrame]: ...

    async def get_intraday_bars(self, symbols: list[str], start: datetime, end: datetime, timeframe: str = "15Min",
                                feed: str = "iex") -> dict[str, pd.DataFrame]: ...

    async def get_latest(self, symbols: list[str], feed: str = "iex") -> dict[str, LatestPrint]: ...

    async def get_corporate_actions(self, symbols: list[str], start: date, end: date) -> list[dict] | None: ...

    def health(self) -> ProviderHealth: ...
