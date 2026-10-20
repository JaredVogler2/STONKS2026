"""Historical-market-data loader for unattended CI training (`apex train --source alpaca`, `apex drift`).

A fixed, pre-registered research universe: the S&P 500 SPDR benchmark, the 11 sector SPDR ETFs and six long-history,
highly liquid members per sector (78 symbols). It is deliberately small so each weekly run stays inside its request and
minute budgets, and FIXED so walk-forward results are comparable week to week.

Known, reported limitations (written into every run report as bias warnings):
* survivorship: members are today's large, listed companies, which flatters historical results;
* sector labels are today's classification (e.g. communication services only exists as an ETF from mid-2018), a mild
  look-ahead in the sector-relative features;
* no free point-in-time earnings calendar in CI: `earnings_unknown` stays set and earnings vetoes are not modelled;
* no catalysts in CI: catalyst-dependent strategies produce no events;
* prices are split-adjusted, dividends are not modelled (price-only returns).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from apex_worker.features.panel import AssetMeta, build_panel
from apex_worker.market.calendar import default_calendar
from apex_worker.market.history import HistoryResult, HistorySpec, sync_history

BENCHMARK = "SPY"
SECTOR_ETFS: dict[str, str] = {
    "technology": "XLK",
    "financials": "XLF",
    "energy": "XLE",
    "health_care": "XLV",
    "industrials": "XLI",
    "consumer_discretionary": "XLY",
    "consumer_staples": "XLP",
    "utilities": "XLU",
    "materials": "XLB",
    "real_estate": "XLRE",
    "communication_services": "XLC",
}
MEMBERS: dict[str, tuple[str, ...]] = {
    "technology": ("AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CSCO"),
    "financials": ("JPM", "BAC", "WFC", "GS", "MS", "BLK"),
    "energy": ("XOM", "CVX", "COP", "SLB", "EOG", "PSX"),
    "health_care": ("JNJ", "UNH", "PFE", "MRK", "ABT", "TMO"),
    "industrials": ("CAT", "HON", "UNP", "GE", "LMT", "DE"),
    "consumer_discretionary": ("AMZN", "HD", "MCD", "NKE", "SBUX", "LOW"),
    "consumer_staples": ("PG", "KO", "PEP", "WMT", "CL", "MO"),
    "utilities": ("NEE", "DUK", "SO", "D", "AEP", "EXC"),
    "materials": ("LIN", "APD", "SHW", "ECL", "NEM", "FCX"),
    "real_estate": ("PLD", "AMT", "CCI", "SPG", "EQIX", "PSA"),
    "communication_services": ("GOOGL", "VZ", "T", "DIS", "CMCSA", "NFLX"),
}
BIAS_WARNINGS = (
    "SURVIVORSHIP: fixed universe of currently listed large caps; historical results are optimistic",
    "SECTOR LABELS: current classification applied to all history (mild look-ahead in sector-relative features)",
    "EARNINGS UNKNOWN: no point-in-time earnings calendar in CI; earnings vetoes not modelled",
    "NO CATALYSTS: catalyst-dependent strategies produce no events in CI training",
    "PRICE-ONLY RETURNS: split-adjusted bars, dividends not modelled",
)


def universe_symbols() -> tuple[str, ...]:
    out = [BENCHMARK, *SECTOR_ETFS.values()]
    for sector in SECTOR_ETFS:
        out.extend(MEMBERS[sector])
    return tuple(out)


def universe_meta() -> dict[str, AssetMeta]:
    meta = {BENCHMARK: AssetMeta(BENCHMARK, None, "benchmark", exchange="ARCA", instrument="etf")}
    for sector, etf in SECTOR_ETFS.items():
        meta[etf] = AssetMeta(etf, sector, "etf", exchange="ARCA", instrument="etf", tradability_status="likely_eligible")
        for sym in MEMBERS[sector]:
            meta[sym] = AssetMeta(sym, sector, "stock", instrument="common", tradability_status="likely_eligible")
    return meta


@dataclass
class LoadInfo:
    """What the loader actually did; written next to report.json as history.json (no secrets)."""

    spec: HistorySpec
    result: HistoryResult | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=lambda: list(BIAS_WARNINGS))

    def to_dict(self) -> dict:
        out: dict = {
            "provider": "alpaca",
            "feed": self.spec.feed,
            "adjustment": self.spec.adjustment,
            "years": self.spec.years,
            "universe_size": len(self.spec.symbols),
            "max_requests": self.spec.max_requests,
            "bias_warnings": self.warnings,
        }
        if self.result is not None:
            out.update(self.result.summary())
        if self.error:
            out["error"] = self.error
        return out


def make_alpaca_loader(
    key_id: str,
    secret_key: str,
    cache_dir: Path,
    spec: HistorySpec | None = None,
    *,
    now: datetime | None = None,
    adapter_factory: Callable | None = None,
) -> tuple[Callable[[], tuple | None], LoadInfo]:
    """Returns (loader, info). The loader yields run_training's 5-tuple, or None (=> no_data, champion retained)."""
    spec = spec or HistorySpec(symbols=universe_symbols())
    info = LoadInfo(spec)

    def loader() -> tuple | None:
        from apex_worker.market.adapters.alpaca import AlpacaAdapter, AlpacaError

        at = now or datetime.now(UTC)
        end = default_calendar().latest_completed_session(at)

        async def run() -> HistoryResult:
            adapter = adapter_factory() if adapter_factory else AlpacaAdapter(key_id, secret_key)
            try:
                return await sync_history(adapter, cache_dir, spec, end=end, now=at)
            finally:
                await adapter.aclose()

        try:
            res = asyncio.run(run())
        except (AlpacaError, ValueError, OSError) as exc:
            info.error = f"{type(exc).__name__}: {str(exc)[:200]}"
            return None
        info.result = res
        if BENCHMARK not in res.bars or len(res.bars[BENCHMARK]) < 260:
            info.error = "benchmark history missing or shorter than one year"
            return None
        stocks = [s for s, m in universe_meta().items() if m.asset_kind == "stock" and s in res.bars]
        if len(stocks) < 20:
            info.error = f"only {len(stocks)} member stocks have history"
            return None
        if res.missing:
            info.warnings.append(f"MISSING HISTORY: {', '.join(sorted(res.missing)[:12])}")
        if res.stale:
            info.warnings.append(f"STALE HISTORY (request budget): {', '.join(sorted(res.stale)[:12])}")
        meta = {s: m for s, m in universe_meta().items() if s in res.bars}
        panel = build_panel(res.bars, meta, BENCHMARK, {k: v for k, v in SECTOR_ETFS.items() if v in res.bars})
        return panel, res.bars, None, [], "historical_market_data"

    return loader, info
