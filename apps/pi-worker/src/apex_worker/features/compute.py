"""Vectorized, strictly causal per-symbol feature computation.

Contract: row t of the returned frame depends ONLY on bars with session_date <= t. Every rolling
window is trailing (never centered), there is no backward fill, and insufficient history yields NaN
(serialized as JSON null), never an invented zero. `tests/test_features.py` proves causality by
recomputing on truncated histories.

Indicators here are measurements, not trading rules. Their predictive value (if any) is tested in
the research lab (Phase 06) with walk-forward validation.
"""

from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pandas as pd

FEATURE_VERSION = "apex-features-1"
TRADING_DAYS = 252
MIN_HISTORY_SESSIONS = 180

# feature name -> (family, description). Families matter: correlated measurements within a family are
# NOT independent confirmations (see signals/quality.py).
FEATURE_CATALOG: dict[str, tuple[str, str]] = {
    "close": ("price", "last close (split-adjusted)"),
    "ret_5": ("momentum", "5-session log return"),
    "ret_10": ("momentum", "10-session log return"),
    "ret_20": ("momentum", "20-session log return"),
    "ret_60": ("momentum", "60-session log return"),
    "ret_120": ("momentum", "120-session log return"),
    "rs_bench_20": ("relative_strength", "20-session log return minus benchmark"),
    "rs_bench_60": ("relative_strength", "60-session log return minus benchmark"),
    "rs_sector_20": ("relative_strength", "20-session log return minus sector ETF"),
    "mtf_consistency": ("momentum", "share of positive returns among 5/10/20/60-session horizons"),
    "trend_slope_50": ("trend", "OLS slope of log close over 50 sessions, per session, in ATR-normalized units"),
    "sma50_slope_20": ("trend", "20-session % change of the 50-session SMA"),
    "dist_sma20_atr": ("trend", "(close - SMA20) / ATR14"),
    "dist_sma50_atr": ("trend", "(close - SMA50) / ATR14"),
    "dist_sma200_atr": ("trend", "(close - SMA200) / ATR14"),
    "above_sma200": ("trend", "1 if close > SMA200"),
    "pct_from_high_60": ("structure", "drawdown from 60-session high close"),
    "days_since_high_20": ("structure", "sessions since the 20-session high close"),
    "days_since_high_60": ("structure", "sessions since the 60-session high close"),
    "efficiency_ratio_20": ("trend", "Kaufman efficiency ratio over 20 sessions"),
    "autocorr_60": ("trend", "lag-1 autocorrelation of daily log returns over 60 sessions"),
    "clv_1": ("price", "close location value of last bar (-1..1)"),
    "clv_5": ("price", "mean close location value over 5 sessions"),
    "gap_1": ("price", "last open vs prior close log gap"),
    "overnight_ret_20": ("price", "sum of overnight (open vs prior close) log returns over 20 sessions"),
    "intraday_ret_20": ("price", "sum of intraday (close vs open) log returns over 20 sessions"),
    "atr_14": ("volatility", "14-session Wilder ATR (price units)"),
    "atr_pct_14": ("volatility", "ATR14 / close"),
    "rv_5": ("volatility", "5-session realized vol, annualized"),
    "rv_10": ("volatility", "10-session realized vol, annualized"),
    "rv_20": ("volatility", "20-session realized vol, annualized"),
    "parkinson_20": ("volatility", "Parkinson high-low volatility estimator, 20 sessions, annualized"),
    "garman_klass_20": ("volatility", "Garman-Klass OHLC volatility estimator, 20 sessions, annualized"),
    "vol_expansion": ("volatility", "rv_5 / rv_20"),
    "squeeze_pct_120": ("compression", "percentile of 20-session Bollinger width within trailing 120 sessions"),
    "range_pct_1": ("volatility", "last bar (high-low)/close"),
    "dollar_vol_med_20": ("liquidity", "median 20-session dollar volume (same feed)"),
    "avg_vol_20": ("liquidity", "mean 20-session share volume (same feed)"),
    "rel_volume_1": ("participation", "last volume / prior 20-session mean volume"),
    "volume_z_1": ("participation", "z-score of last log volume vs prior 20 sessions"),
    "up_down_vol_ratio_20": ("participation", "volume on up days / volume on down days, 20 sessions"),
    "amihud_20": ("liquidity", "Amihud illiquidity: mean |ret| / dollar volume x 1e9, 20 sessions"),
    "base_length": ("compression", "sessions the close has held within a 1.5xATR-wide band ending today"),
    "base_depth_pct": ("compression", "(max-min)/max of closes over the current base"),
    "pivot_high_20": ("structure", "highest high of the 20 sessions BEFORE today"),
    "dist_pivot_atr": ("structure", "(close - pivot_high_20) / ATR14"),
    "breakout_today": ("structure", "1 if close > pivot_high_20"),
    "failed_breakouts_60": ("structure", "breakouts in the last 60 sessions that closed back below pivot within 3 sessions"),
    "bars_available": ("data_quality", "number of daily bars available up to today"),
}


def _wilder(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def _rolling_slope(y: pd.Series, n: int) -> pd.Series:
    x = np.arange(n, dtype=float)
    x = x - x.mean()
    denom = float((x * x).sum())

    def slope(window: np.ndarray) -> float:
        return float(np.dot(x, window - window.mean()) / denom)

    return y.rolling(n, min_periods=n).apply(slope, raw=True)


def _rolling_percentile_last(s: pd.Series, n: int) -> pd.Series:
    def pct(window: np.ndarray) -> float:
        last = window[-1]
        if np.isnan(last):
            return np.nan
        valid = window[~np.isnan(window)]
        return float((valid <= last).sum() - 1) / max(1, len(valid) - 1)

    return s.rolling(n, min_periods=max(20, n // 2)).apply(pct, raw=True)


def _days_since_max(s: pd.Series, n: int) -> pd.Series:
    def since(window: np.ndarray) -> float:
        return float(len(window) - 1 - int(np.nanargmax(window)))

    return s.rolling(n, min_periods=n).apply(since, raw=True)


def _base_length(close: np.ndarray, atr: np.ndarray, max_len: int = 60) -> np.ndarray:
    """Sessions ending at t during which closes stayed inside a band 1.5 x ATR(t) wide. Causal by construction."""
    out = np.full(len(close), np.nan)
    for t in range(len(close)):
        if np.isnan(atr[t]):
            continue
        band = 1.5 * atr[t]
        hi = lo = close[t]
        length = 1
        for k in range(t - 1, max(-1, t - max_len), -1):
            c = close[k]
            hi2, lo2 = max(hi, c), min(lo, c)
            if hi2 - lo2 > band:
                break
            hi, lo = hi2, lo2
            length += 1
        out[t] = length
    return out


def _failed_breakouts(close: np.ndarray, pivot: np.ndarray, window: int = 60, confirm: int = 3) -> np.ndarray:
    """Count breakouts (close > prior-20 pivot) that closed back below that pivot within `confirm` sessions.

    A breakout at day b is only classified once days b+1..b+confirm are known, so the count at t only
    uses breakouts with b <= t - confirm (causal).
    """
    n = len(close)
    failed = np.zeros(n, dtype=bool)
    for b in range(n):
        if np.isnan(pivot[b]) or close[b] <= pivot[b]:
            continue
        end = b + confirm
        if end < n and np.any(close[b + 1:end + 1] < pivot[b]):
            failed[end] = True  # recorded on the day the failure became knowable
    counts = pd.Series(failed.astype(float)).rolling(window, min_periods=1).sum().to_numpy()
    return counts


def compute_features(bars: pd.DataFrame, bench: pd.DataFrame | None = None,
                     sector: pd.DataFrame | None = None) -> pd.DataFrame:
    """bars/bench/sector: split-adjusted daily OHLCV indexed by session date (ascending)."""
    if bars.empty:
        return pd.DataFrame(columns=list(FEATURE_CATALOG))
    df = bars.sort_index()
    o, h, low, c = (df[k].astype(float) for k in ("open", "high", "low", "close"))
    v = df["volume"].astype(float)
    logc = np.log(c)
    r1 = logc.diff()
    out = pd.DataFrame(index=df.index)
    out["close"] = c
    for n in (5, 10, 20, 60, 120):
        out[f"ret_{n}"] = logc - logc.shift(n)

    def rel(other: pd.DataFrame | None, n: int) -> pd.Series:
        if other is None or other.empty:
            return pd.Series(np.nan, index=df.index)
        oc = np.log(other["close"].astype(float)).reindex(df.index)
        return (logc - logc.shift(n)) - (oc - oc.shift(n))

    out["rs_bench_20"] = rel(bench, 20)
    out["rs_bench_60"] = rel(bench, 60)
    out["rs_sector_20"] = rel(sector, 20)
    rets = out[["ret_5", "ret_10", "ret_20", "ret_60"]]
    out["mtf_consistency"] = (rets > 0).sum(axis=1).where(rets.notna().all(axis=1)) / 4.0

    prev_c = c.shift(1)
    tr = pd.concat([h - low, (h - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1)
    atr = _wilder(tr, 14)
    out["atr_14"] = atr
    out["atr_pct_14"] = atr / c
    sma20, sma50, sma200 = (c.rolling(n, min_periods=n).mean() for n in (20, 50, 200))
    out["dist_sma20_atr"] = (c - sma20) / atr
    out["dist_sma50_atr"] = (c - sma50) / atr
    out["dist_sma200_atr"] = (c - sma200) / atr
    out["above_sma200"] = (c > sma200).astype(float).where(sma200.notna())
    out["sma50_slope_20"] = sma50 / sma50.shift(20) - 1.0
    out["trend_slope_50"] = _rolling_slope(logc, 50) / (atr / c)

    hi60 = c.rolling(60, min_periods=60).max()
    out["pct_from_high_60"] = c / hi60 - 1.0
    out["days_since_high_20"] = _days_since_max(c, 20)
    out["days_since_high_60"] = _days_since_max(c, 60)
    change = (c - c.shift(20)).abs()
    path = c.diff().abs().rolling(20, min_periods=20).sum()
    out["efficiency_ratio_20"] = (change / path).where(path > 0)
    out["autocorr_60"] = r1.rolling(60, min_periods=60).corr(r1.shift(1))

    rng = (h - low)
    clv = ((c - low) - (h - c)) / rng.where(rng > 0)
    out["clv_1"] = clv
    out["clv_5"] = clv.rolling(5, min_periods=5).mean()
    overnight = np.log(o) - np.log(prev_c)
    intraday = np.log(c) - np.log(o)
    out["gap_1"] = overnight
    out["overnight_ret_20"] = overnight.rolling(20, min_periods=20).sum()
    out["intraday_ret_20"] = intraday.rolling(20, min_periods=20).sum()

    ann = math.sqrt(TRADING_DAYS)
    for n in (5, 10, 20):
        out[f"rv_{n}"] = r1.rolling(n, min_periods=n).std() * ann
    hl = np.log(h / low)
    out["parkinson_20"] = np.sqrt((hl ** 2).rolling(20, min_periods=20).mean() / (4 * math.log(2))) * ann
    co = np.log(c / o)
    gk = 0.5 * hl ** 2 - (2 * math.log(2) - 1) * co ** 2
    out["garman_klass_20"] = np.sqrt(gk.rolling(20, min_periods=20).mean().clip(lower=0)) * ann
    out["vol_expansion"] = out["rv_5"] / out["rv_20"].where(out["rv_20"] > 0)
    bb_width = (4 * c.rolling(20, min_periods=20).std()) / sma20
    out["squeeze_pct_120"] = _rolling_percentile_last(bb_width, 120)
    out["range_pct_1"] = rng / c

    dv = c * v
    out["dollar_vol_med_20"] = dv.rolling(20, min_periods=20).median()
    out["avg_vol_20"] = v.rolling(20, min_periods=20).mean()
    prior_mean = v.shift(1).rolling(20, min_periods=20).mean()
    out["rel_volume_1"] = v / prior_mean.where(prior_mean > 0)
    lv = np.log(v.where(v > 0))
    lmu = lv.shift(1).rolling(20, min_periods=20).mean()
    lsd = lv.shift(1).rolling(20, min_periods=20).std()
    out["volume_z_1"] = (lv - lmu) / lsd.where(lsd > 0)
    up_vol = v.where(r1 > 0, 0.0).rolling(20, min_periods=20).sum()
    dn_vol = v.where(r1 < 0, 0.0).rolling(20, min_periods=20).sum()
    out["up_down_vol_ratio_20"] = up_vol / dn_vol.where(dn_vol > 0)
    out["amihud_20"] = (r1.abs() / dv.where(dv > 0)).rolling(20, min_periods=20).mean() * 1e9

    base_len = _base_length(c.to_numpy(), atr.to_numpy())
    out["base_length"] = base_len
    closes = c.to_numpy()
    depth = np.full(len(closes), np.nan)
    for t, n in enumerate(base_len):
        if not np.isnan(n):
            w = closes[t - int(n) + 1:t + 1]
            depth[t] = (w.max() - w.min()) / w.max()
    out["base_depth_pct"] = depth
    pivot = h.shift(1).rolling(20, min_periods=20).max()
    out["pivot_high_20"] = pivot
    out["dist_pivot_atr"] = (c - pivot) / atr
    out["breakout_today"] = (c > pivot).astype(float).where(pivot.notna())
    out["failed_breakouts_60"] = _failed_breakouts(closes, pivot.to_numpy())
    out["failed_breakouts_60"] = out["failed_breakouts_60"].where(pivot.notna())
    out["bars_available"] = np.arange(1, len(df) + 1, dtype=float)
    return out[list(FEATURE_CATALOG)].replace([np.inf, -np.inf], np.nan)


def cross_sectional_ranks(panel: dict[str, pd.DataFrame], columns: tuple[str, ...] = ("ret_20", "ret_60", "rs_bench_20")
                          ) -> dict[str, pd.DataFrame]:
    """Per-date percentile rank across symbols (uses only same-date values, so it is causal)."""
    out: dict[str, pd.DataFrame] = {s: pd.DataFrame(index=f.index) for s, f in panel.items()}
    for col in columns:
        wide = pd.DataFrame({s: f[col] for s, f in panel.items() if col in f})
        ranks = wide.rank(axis=1, pct=True)
        for s in out:
            if s in ranks:
                out[s][f"xs_rank_{col}"] = ranks[s].reindex(out[s].index)
    return out


def snapshot_row(features: pd.DataFrame, session) -> dict[str, float | None]:
    row = features.loc[session]
    return {k: (None if (v is None or (isinstance(v, float) and math.isnan(v))) else round(float(v), 8))
            for k, v in row.items()}


def integrity_hash(symbol: str, asof_iso: str, values: dict[str, float | None], input_fingerprint: str) -> str:
    payload = json.dumps({"symbol": symbol, "asof": asof_iso, "v": FEATURE_VERSION, "features": values,
                          "inputs": input_fingerprint}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def bars_fingerprint(bars: pd.DataFrame) -> str:
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(bars[["open", "high", "low", "close"]].to_numpy(dtype=float)).tobytes())
    h.update(np.ascontiguousarray(bars["volume"].to_numpy(dtype=float)).tobytes())
    h.update(str(bars.index[-1]).encode() if len(bars) else b"")
    return h.hexdigest()
