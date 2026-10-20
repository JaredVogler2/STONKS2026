"""Tradable-universe construction.

Exclusions by default: OTC, warrants/units/rights, preferreds, leveraged/inverse ETFs, inactive/halted
symbols, penny stocks, thin liquidity and short histories. Robinhood availability is never asserted:
`likely_eligible` is inferred from listed-exchange metadata only; `user_verified` requires an explicit
owner confirmation; everything else is `unknown` and is not auto-published.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from apex_worker.market.adapters.base import AssetInfo

LISTED_EXCHANGES = {"NYSE", "NASDAQ", "ARCA", "NYSEARCA", "AMEX", "NYSEAMERICAN", "BATS", "CBOE"}


@dataclass(frozen=True)
class UniverseFilters:
    min_price: float = 10.0
    min_median_dollar_volume: float = 30e6
    min_avg_volume: float = 1e6
    min_history_sessions: int = 180
    include_etfs: bool = True
    exclude_leveraged: bool = True
    preset: str = "default"


@dataclass
class UniverseOverrides:
    include: set[str] = field(default_factory=set)
    exclude: set[str] = field(default_factory=set)
    user_verified: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class UniverseDecision:
    symbol: str
    eligible: bool
    tradability_status: str
    reasons: tuple[str, ...]
    asset: AssetInfo


def tradability(asset: AssetInfo, overrides: UniverseOverrides) -> str:
    if asset.symbol in overrides.exclude:
        return "excluded"
    if asset.symbol in overrides.user_verified:
        return "user_verified"
    if asset.exchange in LISTED_EXCHANGES and asset.tradable and asset.status == "active":
        return "likely_eligible"
    return "unknown"


def evaluate_universe(assets: list[AssetInfo], daily: dict[str, pd.DataFrame], filters: UniverseFilters = UniverseFilters(),
                      overrides: UniverseOverrides | None = None) -> list[UniverseDecision]:
    ov = overrides or UniverseOverrides()
    out = []
    for a in sorted(assets, key=lambda x: x.symbol):
        reasons: list[str] = []
        if a.status != "active":
            reasons.append("INACTIVE")
        if a.exchange == "OTC" or a.exchange not in LISTED_EXCHANGES:
            reasons.append("NOT_LISTED_EXCHANGE")
        if a.instrument in ("warrant", "unit", "right", "preferred"):
            reasons.append(f"{a.instrument.upper()}_EXCLUDED")
        if a.instrument == "etf" and not filters.include_etfs:
            reasons.append("ETF_EXCLUDED")
        if a.leveraged and filters.exclude_leveraged:
            reasons.append("LEVERAGED_EXCLUDED")
        df = daily.get(a.symbol)
        if df is None or df.empty:
            reasons.append("NO_BARS")
        else:
            tail = df.iloc[-20:]
            if len(df) < filters.min_history_sessions:
                reasons.append("INSUFFICIENT_HISTORY")
            if float(tail["close"].iloc[-1]) < filters.min_price:
                reasons.append("PRICE_BELOW_MIN")
            if float((tail["close"] * tail["volume"]).median()) < filters.min_median_dollar_volume:
                reasons.append("LOW_DOLLAR_VOLUME")
            if float(tail["volume"].mean()) < filters.min_avg_volume:
                reasons.append("LOW_SHARE_VOLUME")
        if a.symbol in ov.include and not any(r in reasons for r in ("NO_BARS", "INACTIVE", "NOT_LISTED_EXCHANGE")):
            reasons = [r for r in reasons if r in ("NO_BARS", "INACTIVE")]
        if a.symbol in ov.exclude:
            reasons.append("OWNER_EXCLUDED")
        out.append(UniverseDecision(a.symbol, not reasons, tradability(a, ov), tuple(reasons), a))
    return out


def survivorship_warning(decisions: list[UniverseDecision]) -> str:
    return ("Universe built from CURRENTLY listed assets: historical backtests omit delisted names "
            "(survivorship bias) unless a point-in-time historical universe is supplied.")
