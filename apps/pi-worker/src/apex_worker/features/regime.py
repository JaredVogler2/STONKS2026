"""Market regime probabilities (rule-based, version regime-rules-1).

Outputs a probability vector over {trending_up, choppy_neutral, risk_off, rebound, volatility_shock}
via a softmax of transparent, hand-specified logits. The weights are a STARTING HYPOTHESIS, not a
fitted model; Phase 06 tests whether regime gating improves out-of-sample outcomes versus ignoring
it, and an HMM is only acceptable if it beats this baseline out of sample.

Inputs are causal: benchmark features at t and cross-sectional breadth at t.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

REGIME_VERSION = "regime-rules-1"
REGIME_STATES = ("trending_up", "choppy_neutral", "risk_off", "rebound", "volatility_shock")


def breadth_series(panel: dict[str, pd.DataFrame], min_names: int = 20) -> pd.DataFrame:
    """Share of names above their SMA50 / SMA200 and share making 20-day highs, per session."""
    above50 = pd.DataFrame({s: (f["dist_sma50_atr"] > 0).astype(float).where(f["dist_sma50_atr"].notna())
                            for s, f in panel.items()})
    above200 = pd.DataFrame({s: f["above_sma200"] for s, f in panel.items()})
    highs = pd.DataFrame({s: (f["days_since_high_20"] == 0).astype(float).where(f["days_since_high_20"].notna())
                          for s, f in panel.items()})
    count = above50.notna().sum(axis=1)
    out = pd.DataFrame({
        "breadth_above_sma50": above50.mean(axis=1),
        "breadth_above_sma200": above200.mean(axis=1),
        "new_high_share_20": highs.mean(axis=1),
        "breadth_names": count,
    })
    return out.where(count >= min_names)


def distribution_days(bench: pd.DataFrame, window: int = 25) -> pd.Series:
    c = bench["close"].astype(float)
    v = bench["volume"].astype(float)
    dist = ((c.pct_change() <= -0.002) & (v > v.shift(1))).astype(float)
    return dist.rolling(window, min_periods=window).sum()


def _clip(x: pd.Series, lo: float = -1.0, hi: float = 1.0) -> pd.Series:
    return x.clip(lower=lo, upper=hi)


def regime_frame(bench_features: pd.DataFrame, bench_bars: pd.DataFrame, breadth: pd.DataFrame) -> pd.DataFrame:
    """Per-session regime probabilities + the inputs used (for explanation)."""
    f = bench_features
    b = breadth.reindex(f.index)
    dd = distribution_days(bench_bars).reindex(f.index)
    hi120 = f["close"].rolling(120, min_periods=120).max()
    drawdown = 1.0 - f["close"] / hi120
    trend = _clip(f["dist_sma200_atr"] / 4.0)
    slope = _clip(f["sma50_slope_20"] * 25.0)
    breadth_c = _clip((b["breadth_above_sma50"] - 0.5) * 2.5)
    vol = f["rv_20"]
    vol_z = _clip((vol - 0.16) / 0.10, -1.5, 3.0)
    ret5 = f["ret_5"]
    ret10 = f["ret_10"]
    logits = pd.DataFrame(index=f.index)
    logits["trending_up"] = 1.6 * trend + 1.2 * slope + 1.2 * breadth_c - 0.8 * vol_z.clip(lower=0)
    logits["choppy_neutral"] = 0.6 - 1.2 * trend.abs() - 0.8 * slope.abs() - 0.3 * vol_z.clip(lower=0)
    logits["risk_off"] = -1.5 * trend - 1.0 * slope - 1.2 * breadth_c + 0.2 * (dd.fillna(6) - 6) + 0.5 * vol_z.clip(lower=0)
    logits["rebound"] = (2.5 * ((drawdown > 0.07) & (ret10 > 0.02)).astype(float) + 0.8 * _clip(ret10 * 15)
                         - 0.8 * trend.clip(lower=0) - 1.0)
    logits["volatility_shock"] = 2.2 * ((vol - 0.32) / 0.08).clip(lower=0, upper=3) + 1.5 * (-ret5 / 0.06).clip(0, 2) - 1.5
    valid = (f["dist_sma200_atr"].notna() & f["rv_20"].notna() & b["breadth_above_sma50"].notna()
             & logits.notna().all(axis=1))
    z = logits.where(valid, 0.0).to_numpy(dtype=float)
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    p = e / e.sum(axis=1, keepdims=True)
    probs = pd.DataFrame(p, index=f.index, columns=list(REGIME_STATES)).where(valid)
    label = pd.Series(np.array(REGIME_STATES, dtype=object)[np.argmax(p, axis=1)], index=f.index)
    probs["label"] = label.where(valid)
    probs["input_trend"] = trend
    probs["input_sma50_slope"] = slope
    probs["input_breadth_above_sma50"] = b["breadth_above_sma50"]
    probs["input_rv20"] = vol
    probs["input_distribution_days_25"] = dd
    probs["input_drawdown_120"] = drawdown
    return probs


def regime_at(frame: pd.DataFrame, session) -> dict:
    row = frame.loc[session]
    if pd.isna(row.get("label")):
        return {"label": "unknown", "probabilities": {}, "version": REGIME_VERSION, "inputs": {}}
    return {
        "label": str(row["label"]),
        "probabilities": {s: round(float(row[s]), 4) for s in REGIME_STATES},
        "version": REGIME_VERSION,
        "inputs": {k.removeprefix("input_"): (None if pd.isna(row[k]) else round(float(row[k]), 6))
                   for k in row.index if k.startswith("input_")},
    }
