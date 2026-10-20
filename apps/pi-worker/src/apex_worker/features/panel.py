"""Universe-wide feature panel: per-symbol features + cross-sectional ranks + sector context + regime.

Live scanning on the Pi uses a bounded lookback (default 320 sessions) so memory stays small
(~500 symbols x 320 rows x ~60 float32 columns ~ 40 MB). Research runs pass full history.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from apex_worker.features.compute import compute_features, cross_sectional_ranks
from apex_worker.features.regime import breadth_series, regime_frame

LIVE_LOOKBACK_SESSIONS = 320


@dataclass
class AssetMeta:
    symbol: str
    sector: str | None
    asset_kind: str  # stock | etf | benchmark
    exchange: str | None = None
    instrument: str = "common"
    name: str | None = None
    tradability_status: str = "unknown"
    leveraged: bool = False
    tags: tuple[str, ...] = ()


@dataclass
class FeaturePanel:
    features: dict[str, pd.DataFrame]
    regime: pd.DataFrame
    breadth: pd.DataFrame
    sector_frame: pd.DataFrame
    meta: dict[str, AssetMeta]
    benchmark: str
    sector_etfs: dict[str, str]
    bar_fingerprints: dict[str, str] = field(default_factory=dict)

    @property
    def sessions(self) -> pd.DatetimeIndex:
        return self.regime.index

    def row(self, symbol: str, session) -> pd.Series | None:
        f = self.features.get(symbol)
        if f is None or session not in f.index:
            return None
        return f.loc[session]


def build_panel(bars: dict[str, pd.DataFrame], meta: dict[str, AssetMeta], benchmark: str,
                sector_etfs: dict[str, str], lookback: int | None = None) -> FeaturePanel:
    if benchmark not in bars:
        raise KeyError(f"benchmark {benchmark} missing from bars")
    if lookback:
        bars = {s: b.iloc[-lookback:] for s, b in bars.items()}
    bench_bars = bars[benchmark]
    feats: dict[str, pd.DataFrame] = {}
    for sym, b in bars.items():
        m = meta.get(sym)
        sector_sym = sector_etfs.get(m.sector) if m and m.sector else None
        sector_bars = bars.get(sector_sym) if sector_sym and sector_sym != sym else None
        f = compute_features(b, None if sym == benchmark else bench_bars, sector_bars)
        feats[sym] = f.astype("float32")

    stocks = {s: f for s, f in feats.items() if meta.get(s) and meta[s].asset_kind == "stock"}
    ranks = cross_sectional_ranks(stocks)
    for s, r in ranks.items():
        for col in r.columns:
            feats[s][col] = r[col].astype("float32")

    # sector context: each sector ETF's 20-session return rank among sectors, now and 20 sessions ago
    sector_ret = pd.DataFrame({sec: feats[etf]["ret_20"] for sec, etf in sector_etfs.items() if etf in feats})
    sector_rank = sector_ret.rank(axis=1, pct=True)
    sector_frame = pd.concat({"rank": sector_rank, "rank_change_20": sector_rank - sector_rank.shift(20)}, axis=1)
    for s, f in feats.items():
        m = meta.get(s)
        if m and m.sector in sector_rank.columns:
            f["sector_rank_20"] = sector_rank[m.sector].reindex(f.index).astype("float32")
            f["sector_rank_change_20"] = (sector_rank[m.sector] - sector_rank[m.sector].shift(20)).reindex(f.index) \
                .astype("float32")
        else:
            f["sector_rank_20"] = np.float32(np.nan)
            f["sector_rank_change_20"] = np.float32(np.nan)

    breadth = breadth_series(stocks)
    regime = regime_frame(feats[benchmark].astype(float), bench_bars, breadth)
    return FeaturePanel(feats, regime, breadth, sector_frame, meta, benchmark, sector_etfs)


def panel_from_synthetic(market, symbols: list[str] | None = None, lookback: int | None = None) -> FeaturePanel:
    from apex_worker.demo.synthetic import SECTORS

    syms = symbols or sorted(market.bars)
    bars = {s: market.adjusted(s) for s in syms if s in market.bars}
    meta = {
        s: AssetMeta(symbol=s, sector=spec.sector if spec.sector in SECTORS else None, asset_kind=spec.asset_kind,
                     exchange=spec.exchange, instrument=spec.instrument, name=spec.name,
                     tradability_status="likely_eligible", leveraged=spec.leveraged, tags=spec.tags)
        for s, spec in market.assets.items() if s in bars
    }
    sector_etfs = {sec: etf for sec, (etf, _) in SECTORS.items()}
    return build_panel(bars, meta, "DMSPY", sector_etfs, lookback=lookback)
