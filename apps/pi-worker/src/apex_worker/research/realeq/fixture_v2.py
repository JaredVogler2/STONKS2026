"""REAL-SOURCE EMULATION FIXTURE for protocol-2 tests and DEMO runs: never market data.

The deterministic synthetic market (apex_worker.demo.synthetic) from 2016-01-04, served under SPY, the 11 sector SPDR
symbols and the first `per_sector` U1 symbols of each sector (snapshot order). Anything built from it is labelled
SYNTHETIC_FIXTURE_DEMO and can never be published as a real experiment.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from apex_worker.research.realeq.universe_v2 import u1_constituents
from apex_worker.research.training_data import BENCHMARK, SECTOR_ETFS

FIXTURE_V2_START = date(2016, 1, 4)


def universe_frames_v2(per_sector: int = 8, start: date = FIXTURE_V2_START) -> dict[str, pd.DataFrame]:
    from apex_worker.demo.synthetic import generate

    market = generate(stocks_per_sector=per_sector, start=start)
    out: dict[str, pd.DataFrame] = {}
    for sym, spec in market.assets.items():
        if spec.asset_kind == "benchmark" and sym == "DMSPY":
            out[BENCHMARK] = market.adjusted(sym)
        elif spec.asset_kind == "etf" and spec.sector in SECTOR_ETFS:
            out[SECTOR_ETFS[spec.sector]] = market.adjusted(sym)
    for sector in SECTOR_ETFS:
        real = [c.symbol for c in u1_constituents() if c.sector == sector][:per_sector]
        synth = sorted(s for s, a in market.assets.items()
                       if a.sector == sector and a.asset_kind == "stock" and a.instrument == "common" and not a.tags)
        for r, f in zip(real, synth, strict=False):
            out[r] = market.adjusted(f)
    return out


def factor_frames_v2(per_sector: int = 12, seed: int = 5, start: date = FIXTURE_V2_START, end: date = date(2026, 10, 16),
                     planted: dict[str, float] | None = None) -> dict[str, pd.DataFrame]:
    """A calm factor-model market (SPY drift ~7%/yr, vol ~13%) for engine tests: gates stay open most of the time.

    `planted` maps a symbol to an extra daily drift; tests use it to plant a known edge. Never market data."""
    import numpy as np

    from apex_worker.market.calendar import default_calendar

    days = default_calendar().sessions_between(start, end)
    idx = pd.DatetimeIndex(pd.to_datetime([d.isoformat() for d in days]), name="session_date")
    n = len(idx)
    rng = np.random.default_rng(seed)
    mkt = rng.normal(0.00028, 0.008, n)
    out: dict[str, pd.DataFrame] = {}

    def frame(ret: np.ndarray, p0: float, vol: float, r: np.random.Generator) -> pd.DataFrame:
        close = p0 * np.exp(np.cumsum(ret))
        prev = np.concatenate([[p0], close[:-1]])
        open_ = prev * np.exp(r.normal(0, 0.002, n))
        span = np.abs(r.normal(0, 0.006, n)) + 0.002
        high = np.maximum(open_, close) * (1 + span * r.uniform(0.3, 1.0, n))
        low = np.minimum(open_, close) * (1 - span * r.uniform(0.3, 1.0, n))
        volume = vol * np.exp(r.normal(0, 0.25, n))
        return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)

    out[BENCHMARK] = frame(mkt, 200.0, 8e7, rng)
    sec_ret = {}
    for sector, etf in SECTOR_ETFS.items():
        sec_ret[sector] = 0.9 * mkt + rng.normal(0, 0.004, n)
        out[etf] = frame(sec_ret[sector], 60.0, 1e7, rng)
    for sector in SECTOR_ETFS:
        for sym in [c.symbol for c in u1_constituents() if c.sector == sector][:per_sector]:
            r = np.random.default_rng(abs(hash((seed, sym))) % 2**32)
            ret = r.uniform(0.8, 1.2) * sec_ret[sector] + r.normal(0, 0.012, n) + (planted or {}).get(sym, 0.0)
            out[sym] = frame(ret, float(r.uniform(30, 300)), float(r.uniform(3e6, 2e7)), r)
    return out
