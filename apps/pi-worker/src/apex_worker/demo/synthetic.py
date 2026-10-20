"""Deterministic SYNTHETIC market generator for DEMO mode, replay and research red-team tests.

Everything produced here is fictional: tickers carry a `DM` prefix plus a digit or ETF-like
suffix so they cannot be confused with listed securities, and every bar is stamped
provider="replay", feed="demo". The generator deliberately embeds a *known* noisy relationship
(volatility-contraction bases that resolve upward more often in constructive regimes) so the
research lab can prove it detects a real simulated edge and collapses to baseline on shuffled
labels. Results computed on this data are NEVER evidence about real markets.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from apex_worker.market.calendar import MarketCalendar, default_calendar

GENERATOR_VERSION = "synthetic-market-1"
DEFAULT_SEED = 20261020
DEFAULT_START = date(2019, 1, 2)
DEFAULT_END = date(2026, 10, 16)

REGIMES = ("bull", "chop", "riskoff", "rebound", "shock")
# daily drift, daily vol for the market factor in each latent regime
REGIME_PARAMS = {
    "bull": (0.0005, 0.0085),
    "chop": (0.0000, 0.0100),
    "riskoff": (-0.0010, 0.0165),
    "rebound": (0.0012, 0.0145),
    "shock": (-0.0060, 0.0330),
}
REGIME_TRANSITIONS = np.array([
    # bull   chop   riskoff rebound shock
    [0.975, 0.018, 0.005, 0.000, 0.002],
    [0.025, 0.955, 0.016, 0.000, 0.004],
    [0.000, 0.020, 0.950, 0.024, 0.006],
    [0.035, 0.010, 0.005, 0.950, 0.000],
    [0.000, 0.000, 0.150, 0.250, 0.600],
])

SECTORS = {
    "technology": ("DMXLK", ["SEMI", "SEMI", "SEMI", "SEMI", "SOFT", "SOFT", "SOFT", "SOFT"]),
    "financials": ("DMXLF", ["BANK"] * 8),
    "energy": ("DMXLE", ["OIL"] * 8),
    "health_care": ("DMXLV", ["BIO", "BIO", "BIO", "BIO", "MED", "MED", "MED", "MED"]),
    "industrials": ("DMXLI", ["IND"] * 8),
    "consumer_discretionary": ("DMXLY", ["RETL"] * 8),
    "consumer_staples": ("DMXLP", ["FOOD"] * 8),
    "utilities": ("DMXLU", ["UTIL"] * 8),
    "materials": ("DMXLB", ["MATL"] * 8),
    "real_estate": ("DMXLRE", ["REIT"] * 8),
    "communication_services": ("DMXLC", ["MEDIA"] * 8),
}
SECTOR_BETA = {
    "technology": 1.25, "financials": 1.05, "energy": 0.95, "health_care": 0.8, "industrials": 1.0,
    "consumer_discretionary": 1.15, "consumer_staples": 0.6, "utilities": 0.5, "materials": 1.0,
    "real_estate": 0.85, "communication_services": 1.05,
}


@dataclass(frozen=True)
class AssetSpec:
    symbol: str
    name: str
    sector: str
    asset_kind: str  # stock | etf | benchmark
    exchange: str  # NASDAQ | NYSE | ARCA | OTC
    base_price: float
    base_volume: float
    beta: float
    idio_vol: float
    tags: tuple[str, ...] = ()
    listed_from: date | None = None
    delisted_on: date | None = None
    split: tuple[str, float] | None = None  # (iso date, ratio new/old shares e.g. 2.0 or 0.1)
    halts: tuple[str, ...] = ()
    leveraged: bool = False
    instrument: str = "common"  # common | etf | warrant | preferred | unit | adr

    def metadata(self) -> dict:
        data = asdict(self)
        data["listed_from"] = self.listed_from.isoformat() if self.listed_from else None
        data["delisted_on"] = self.delisted_on.isoformat() if self.delisted_on else None
        data["tags"] = list(self.tags)
        data["halts"] = list(self.halts)
        data["is_demo"] = True
        return data


@dataclass
class SyntheticMarket:
    seed: int
    sessions: list[date]
    assets: dict[str, AssetSpec]
    bars: dict[str, pd.DataFrame]  # raw (unadjusted) daily bars, index = session date
    regimes: pd.Series  # latent regime label per session (ground truth, never a feature)
    setups: pd.DataFrame  # ground-truth injected setups (for red-team tests only)
    corporate_actions: list[dict] = field(default_factory=list)
    earnings: dict[str, list[str]] = field(default_factory=dict)

    def adjusted(self, symbol: str) -> pd.DataFrame:
        """Split-adjusted OHLCV (prices divided, volume multiplied by cumulative future ratio)."""
        df = self.bars[symbol].copy()
        for action in self.corporate_actions:
            if action["symbol"] != symbol or action["type"] != "split":
                continue
            eff = pd.Timestamp(action["effective_date"])
            ratio = action["ratio"]
            mask = df.index < eff
            for col in ("open", "high", "low", "close"):
                df.loc[mask, col] = df.loc[mask, col] / ratio
            df.loc[mask, "volume"] = (df.loc[mask, "volume"] * ratio).round()
        return df

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        h.update(f"{GENERATOR_VERSION}|{self.seed}".encode())
        for sym in sorted(self.bars):
            df = self.bars[sym]
            h.update(sym.encode())
            h.update(np.ascontiguousarray(df[["open", "high", "low", "close"]].to_numpy()).tobytes())
            h.update(np.ascontiguousarray(df["volume"].to_numpy()).tobytes())
        return h.hexdigest()


def _symbol_seed(seed: int, symbol: str) -> int:
    return int(hashlib.sha256(f"{seed}|{symbol}".encode()).hexdigest()[:15], 16)


def universe_specs(stocks_per_sector: int = 8) -> list[AssetSpec]:
    """Default (8 per sector) is the DEMO universe; research presets add more names without changing existing ones."""
    specs: list[AssetSpec] = [
        AssetSpec("DMSPY", "Demo Broad Market Index ETF", "benchmark", "benchmark", "ARCA", 320.0, 7.5e7, 1.0, 0.0,
                  instrument="etf"),
        AssetSpec("DMQQQ", "Demo Growth 100 Index ETF", "benchmark", "benchmark", "NASDAQ", 260.0, 4.5e7, 1.15, 0.0,
                  instrument="etf"),
        AssetSpec("DMIWM", "Demo Small Cap Index ETF", "benchmark", "benchmark", "ARCA", 150.0, 3.0e7, 1.2, 0.0,
                  instrument="etf"),
    ]
    for sector, (etf, _) in SECTORS.items():
        specs.append(AssetSpec(etf, f"Demo {sector.replace('_', ' ').title()} Sector ETF", sector, "etf", "ARCA",
                               70.0, 1.2e7, SECTOR_BETA[sector], 0.0, instrument="etf"))
    rng = np.random.default_rng(DEFAULT_SEED)
    for sector, (_, base_codes) in SECTORS.items():
        counters: dict[str, int] = {}
        codes = list(base_codes)
        while len(codes) < stocks_per_sector:
            codes.append(base_codes[len(codes) % len(base_codes)])
        for i, code in enumerate(codes):
            if i >= len(base_codes):
                srng = np.random.default_rng(_symbol_seed(DEFAULT_SEED, f"{sector}|{i}"))
            else:
                srng = rng
            counters[code] = counters.get(code, 0) + 1
            n = counters[code]
            symbol = f"DM{code}{n}"
            base_price = float(np.round(srng.uniform(18, 260), 2))
            base_volume = float(np.round(srng.uniform(1.4e6, 1.2e7), -3))
            beta = float(np.round(SECTOR_BETA[sector] * srng.uniform(0.75, 1.3), 3))
            idio = float(np.round(srng.uniform(0.010, 0.022), 4))
            exch = "NASDAQ" if code in {"SEMI", "SOFT", "BIO", "MEDIA"} else "NYSE"
            specs.append(AssetSpec(symbol, f"Demo {code.title()} Holdings {n}", sector, "stock", exch, base_price,
                                   base_volume, beta, idio))
    # special cases exercised by universe filters, data-quality checks and survivorship warnings
    specs += [
        AssetSpec("DMPENNY1", "Demo Penny Mining Corp", "materials", "stock", "NASDAQ", 4.2, 3.0e6, 1.4, 0.03,
                  tags=("penny",)),
        AssetSpec("DMTHIN1", "Demo Thinly Traded Industries", "industrials", "stock", "NYSE", 42.0, 6.0e4, 1.0, 0.02,
                  tags=("thin",)),
        AssetSpec("DMLEV3X", "Demo 3x Leveraged Growth ETF", "benchmark", "etf", "ARCA", 55.0, 2.0e7, 3.0, 0.0,
                  tags=("leveraged",), leveraged=True, instrument="etf"),
        AssetSpec("DMOTC1", "Demo Over-the-Counter Ltd", "industrials", "stock", "OTC", 25.0, 2.0e6, 1.0, 0.025,
                  tags=("otc",)),
        AssetSpec("DMSPAC.WS", "Demo Acquisition Corp Warrant", "financials", "stock", "NASDAQ", 12.0, 2.0e6, 1.0,
                  0.04, tags=("warrant",), instrument="warrant"),
        AssetSpec("DMBANK.PR", "Demo Bank Preferred Series A", "financials", "stock", "NYSE", 25.0, 1.5e6, 0.3,
                  0.005, tags=("preferred",), instrument="preferred"),
        AssetSpec("DMDELIST1", "Demo Former Retail Co (delisted)", "consumer_discretionary", "stock", "NYSE", 60.0,
                  3.0e6, 1.2, 0.025, tags=("delisted",), delisted_on=date(2024, 3, 15)),
        AssetSpec("DMSPLIT1", "Demo Split Semiconductor", "technology", "stock", "NASDAQ", 240.0, 4.0e6, 1.3, 0.018,
                  tags=("split",), split=("2023-06-12", 2.0)),
        AssetSpec("DMRSPLIT1", "Demo Reverse Split Biotech", "health_care", "stock", "NASDAQ", 3.1, 9.0e6, 1.2, 0.03,
                  tags=("reverse_split",), split=("2022-11-07", 0.1)),
        AssetSpec("DMHALT1", "Demo Halted Medical Devices", "health_care", "stock", "NYSE", 75.0, 3.0e6, 0.9, 0.018,
                  tags=("halt",), halts=("2026-08-10", "2026-08-11")),
        AssetSpec("DMNEW1", "Demo Recent IPO Software", "technology", "stock", "NASDAQ", 48.0, 5.0e6, 1.3, 0.025,
                  tags=("new_listing",), listed_from=date(2026, 3, 2)),
        AssetSpec("DMADR1", "Demo Foreign Telecom ADR", "communication_services", "stock", "NYSE", 14.0, 4.0e5, 0.8,
                  0.02, tags=("adr", "thin"), instrument="adr"),
    ]
    return specs


def _regime_path(rng: np.random.Generator, n: int) -> np.ndarray:
    states = np.empty(n, dtype=np.int8)
    s = 0
    cum = REGIME_TRANSITIONS.cumsum(axis=1)
    draws = rng.random(n)
    for i in range(n):
        states[i] = s
        s = int(np.searchsorted(cum[s], draws[i]))
        s = min(s, len(REGIMES) - 1)
    return states


def generate(seed: int = DEFAULT_SEED, start: date = DEFAULT_START, end: date = DEFAULT_END,
             calendar: MarketCalendar | None = None, symbols: list[str] | None = None,
             stocks_per_sector: int = 8) -> SyntheticMarket:
    cal = calendar or default_calendar()
    sessions = cal.sessions_between(start, end)
    n = len(sessions)
    root = np.random.SeedSequence(seed)
    rng_market, rng_assets = (np.random.default_rng(s) for s in root.spawn(2))

    regime_idx = _regime_path(rng_market, n)
    mu = np.array([REGIME_PARAMS[REGIMES[i]][0] for i in regime_idx])
    sig = np.array([REGIME_PARAMS[REGIMES[i]][1] for i in regime_idx])
    # GARCH-flavoured vol clustering on top of regime vol
    vol_mult = np.exp(np.convolve(rng_market.normal(0, 0.18, n), np.ones(10) / 10, mode="same"))
    market_ret = mu + sig * vol_mult * rng_market.standard_normal(n)

    sector_names = list(SECTORS)
    sector_rng = np.random.default_rng(root.spawn(1)[0])
    sector_ret = {}
    for sec in sector_names:
        # slow rotation: AR(1) drift component gives cross-sectional leadership persistence
        drift = np.zeros(n)
        eps = sector_rng.normal(0, 0.00006, n)
        for i in range(1, n):
            drift[i] = 0.985 * drift[i - 1] + eps[i]
        sector_ret[sec] = drift + sector_rng.normal(0, 0.0055, n)

    specs = universe_specs(stocks_per_sector)
    if symbols is not None:
        wanted = set(symbols)
        specs = [s for s in specs if s.symbol in wanted]
    del rng_assets
    seed_by_symbol = {s.symbol: _symbol_seed(seed, s.symbol) for s in specs}

    bars: dict[str, pd.DataFrame] = {}
    setups_rows: list[dict] = []
    actions: list[dict] = []
    earnings: dict[str, list[str]] = {}
    idx = pd.DatetimeIndex(pd.to_datetime([d.isoformat() for d in sessions]), name="session_date")

    for spec in specs:
        rng = np.random.default_rng(seed_by_symbol[spec.symbol])
        ret = spec.beta * market_ret
        if spec.sector in sector_ret:
            ret = ret + sector_ret[spec.sector] * (1.0 if spec.asset_kind == "stock" else 0.8)
        if spec.symbol == "DMQQQ":
            ret = ret + 0.6 * sector_ret["technology"] + 0.2 * sector_ret["communication_services"]
        if spec.symbol == "DMIWM":
            ret = ret + rng.normal(0, 0.004, n)
        idio_scale = np.full(n, spec.idio_vol)
        vol_surge = np.ones(n)
        earn_gap = np.zeros(n)
        inject = np.zeros(n)

        if spec.asset_kind == "stock" and not spec.leveraged:
            # quarterly earnings: scheduled dates (published in the fixture calendar) with gap risk
            first = int(rng.integers(5, 60))
            edays = list(range(first, n, 63))
            earnings[spec.symbol] = [sessions[i].isoformat() for i in edays]
            for i in edays:
                earn_gap[i] = rng.normal(0.0, 0.055)
                vol_surge[i] *= 2.8
            # inject volatility-contraction bases with regime-dependent resolution (synthetic alpha)
            i = int(rng.integers(60, 140))
            while i < n - 45:
                base_len = int(rng.integers(15, 30))
                lo, hi = i, i + base_len
                idio_scale[lo:hi] *= 0.38
                ret[lo:hi] = ret[lo:hi] * 0.45 + spec.beta * mu[lo:hi] * 0.2
                regime = REGIMES[int(regime_idx[hi])]
                p_up = {"bull": 0.62, "chop": 0.50, "riskoff": 0.28, "rebound": 0.58, "shock": 0.2}[regime]
                # planted, OBSERVABLE-at-decision relationship: longer/tighter bases succeed more often
                p_up = min(0.9, p_up + 0.012 * (base_len - 22))
                success = rng.random() < p_up
                run = int(rng.integers(8, 18))
                if success:
                    inject[hi:hi + run] += rng.uniform(0.0040, 0.0080)
                    vol_surge[hi:hi + 3] *= rng.uniform(2.2, 3.5)  # strong participation on real breakouts
                else:
                    inject[hi:hi + 2] += 0.008
                    inject[hi + 2:hi + 2 + run] -= rng.uniform(0.0035, 0.0065)
                    vol_surge[hi:hi + 2] *= rng.uniform(1.1, 1.5)
                setups_rows.append({"symbol": spec.symbol, "base_start": sessions[lo], "breakout": sessions[hi],
                                    "regime": regime, "success": bool(success), "run": run})
                i = hi + run + int(rng.integers(40, 110))
            # remove the injected setups' unconditional drift: the simulated edge is TIMING only
            ret = ret + inject - inject.mean()

        idio = idio_scale * rng.standard_t(5, n) / np.sqrt(5 / 3)
        total = ret + idio + earn_gap
        if spec.leveraged:
            total = 3.0 * market_ret + 0.0002 * rng.standard_normal(n)
        log_close = np.log(spec.base_price) + np.cumsum(total)
        close = np.exp(log_close)

        gap = rng.normal(0, 0.0035, n) + earn_gap * 0.85
        prev_close = np.concatenate([[spec.base_price], close[:-1]])
        open_ = prev_close * np.exp(gap)
        # keep open consistent with direction of total move
        rng_range = np.abs(rng.normal(0, 1, n)) * (spec.idio_vol * 0.6 + sig * 0.4) + 0.002
        high = np.maximum(open_, close) * np.exp(rng_range * rng.uniform(0.3, 1.0, n))
        low = np.minimum(open_, close) * np.exp(-rng_range * rng.uniform(0.3, 1.0, n))
        abs_z = np.abs(total) / max(spec.idio_vol, 0.006)
        volume = spec.base_volume * np.exp(rng.normal(0, 0.28, n)) * (1 + 0.35 * np.minimum(abs_z, 6)) * vol_surge
        regime_vol = np.array([{"bull": 0.95, "chop": 1.0, "riskoff": 1.25, "rebound": 1.15, "shock": 1.8}[REGIMES[k]]
                               for k in regime_idx])
        volume = volume * regime_vol

        df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)

        if spec.split:
            eff = date.fromisoformat(spec.split[0])
            ratio = spec.split[1]
            post = df.index >= pd.Timestamp(eff)
            for col in ("open", "high", "low", "close"):
                df.loc[post, col] = df.loc[post, col] / ratio
            df.loc[post, "volume"] = df.loc[post, "volume"] * ratio
            actions.append({"symbol": spec.symbol, "type": "split", "effective_date": eff.isoformat(), "ratio": ratio,
                            "is_demo": True})

        df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].round(2)
        df["high"] = df[["open", "high", "close"]].max(axis=1)
        df["low"] = df[["open", "low", "close"]].min(axis=1).clip(lower=0.01)
        df["volume"] = df["volume"].round().astype("int64")
        df["vwap"] = ((df["high"] + df["low"] + 2 * df["close"]) / 4).round(4)
        df["trade_count"] = (df["volume"] / rng.uniform(80, 160)).round().astype("int64")
        if spec.listed_from:
            df = df[df.index >= pd.Timestamp(spec.listed_from)]
        if spec.delisted_on:
            df = df[df.index < pd.Timestamp(spec.delisted_on)]
            actions.append({"symbol": spec.symbol, "type": "delisting", "effective_date": spec.delisted_on.isoformat(),
                            "is_demo": True})
        if spec.halts:
            df = df[~df.index.isin(pd.to_datetime(list(spec.halts)))]
        bars[spec.symbol] = df

    regimes = pd.Series([REGIMES[i] for i in regime_idx], index=idx, name="regime")
    setups = pd.DataFrame(setups_rows)
    return SyntheticMarket(seed, sessions, {s.symbol: s for s in specs}, bars, regimes, setups, actions, earnings)


def manifest(market: SyntheticMarket) -> dict:
    return {
        "generator_version": GENERATOR_VERSION,
        "seed": market.seed,
        "first_session": market.sessions[0].isoformat(),
        "last_session": market.sessions[-1].isoformat(),
        "symbols": sorted(market.bars),
        "fingerprint_sha256": market.fingerprint(),
        "is_demo": True,
        "warning": "SYNTHETIC DEMO DATA. Fictional tickers and prices. Not market data. Not evidence of real edge.",
    }


def manifest_json(market: SyntheticMarket) -> str:
    return json.dumps(manifest(market), indent=2, sort_keys=True) + "\n"
