"""Probabilistic and economic evaluation metrics with block-bootstrap uncertainty."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2)) if len(p) else float("nan")


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))) if len(p) else float("nan")


def reliability(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict]:
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for i in range(bins):
        m = (p >= edges[i]) & ((p < edges[i + 1]) if i < bins - 1 else (p <= 1))
        if m.sum() == 0:
            continue
        out.append(
            {
                "bin_lo": round(float(edges[i]), 2),
                "bin_hi": round(float(edges[i + 1]), 2),
                "n": int(m.sum()),
                "mean_predicted": round(float(p[m].mean()), 4),
                "observed_rate": round(float(y[m].mean()), 4),
            }
        )
    return out


def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    rel = reliability(p, y, bins)
    n = len(p)
    return float(sum(b["n"] / n * abs(b["mean_predicted"] - b["observed_rate"]) for b in rel)) if n else float("nan")


def brier_skill(p: np.ndarray, y: np.ndarray, base_rate: float) -> float:
    ref = brier(np.full_like(p, base_rate), y)
    return 1 - brier(p, y) / ref if ref > 0 else float("nan")


def block_bootstrap_mean(
    values: np.ndarray,
    groups: np.ndarray,
    n_boot: int = 2000,
    seed: int = 11,
    qs: tuple[float, ...] = (0.05, 0.1, 0.5, 0.9, 0.95),
) -> dict[str, float]:
    """Bootstrap the mean resampling whole groups (e.g. decision weeks) to respect clustering."""
    if len(values) == 0:
        return {f"p{int(q * 100)}": float("nan") for q in qs}
    rng = np.random.default_rng(seed)
    uniq = np.unique(groups)
    idx_by = [np.flatnonzero(groups == g) for g in uniq]
    means = np.empty(n_boot)
    for i in range(n_boot):
        pick = rng.integers(0, len(uniq), len(uniq))
        sel = np.concatenate([idx_by[j] for j in pick])
        means[i] = values[sel].mean()
    return {f"p{int(q * 100)}": round(float(np.quantile(means, q)), 6) for q in qs}


def max_drawdown(returns_in_order: np.ndarray) -> float:
    if len(returns_in_order) == 0:
        return 0.0
    equity = np.cumprod(1 + returns_in_order)
    peak = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    return float((equity / peak - 1).min())


def expected_shortfall(r: np.ndarray, alpha: float = 0.05) -> float:
    if len(r) == 0:
        return float("nan")
    cut = np.quantile(r, alpha)
    tail = r[r <= cut]
    return float(tail.mean()) if len(tail) else float(cut)


def effective_sample_size(dates: pd.Series, holding_sessions: pd.Series) -> float:
    """Crude ESS: count of non-overlapping holding windows across the sample (overlap reduces information)."""
    if dates.empty:
        return 0.0
    span = (dates.max() - dates.min()).days * 252 / 365 + float(holding_sessions.fillna(0).mean() or 1)
    return float(min(len(dates), max(1.0, span / max(1.0, float(holding_sessions.fillna(1).mean())))))


def economic_summary(events: pd.DataFrame) -> dict:
    f = events[events["filled"]] if "filled" in events else events
    r = f["return_net"].to_numpy(float) if len(f) else np.array([])
    rm = f["r_multiple"].dropna().to_numpy(float) if len(f) else np.array([])
    out = {
        "candidates": len(events),
        "entries": len(f),
        "fill_rate": round(float(len(f) / len(events)), 4) if len(events) else None,
        "target_first_rate": round(float((f["outcome"] == "TARGET").mean()), 4) if len(f) else None,
        "stop_first_rate": round(float((f["outcome"] == "STOP").mean()), 4) if len(f) else None,
        "time_expiry_rate": round(float((f["outcome"].isin(["TIME", "DATA_END"])).mean()), 4) if len(f) else None,
        "mean_return_net": round(float(r.mean()), 6) if len(r) else None,
        "median_return_net": round(float(np.median(r)), 6) if len(r) else None,
        "mean_r_multiple": round(float(rm.mean()), 4) if len(rm) else None,
        "median_r_multiple": round(float(np.median(rm)), 4) if len(rm) else None,
        "expected_shortfall_5": round(expected_shortfall(r), 6) if len(r) else None,
        "max_drawdown_sequential": round(max_drawdown(r), 4) if len(r) else None,
        "ambiguous_same_bar_rate": round(float(f["ambiguous"].mean()), 4) if len(f) and "ambiguous" in f else None,
        "gap_through_stop_rate": round(float(f["gap_stop"].mean()), 4) if len(f) and "gap_stop" in f else None,
        "mean_mfe": round(float(f["mfe"].mean()), 6) if len(f) and "mfe" in f else None,
        "mean_mae": round(float(f["mae"].mean()), 6) if len(f) and "mae" in f else None,
        "mean_sessions_held": round(float(f["sessions_held"].mean()), 2) if len(f) and "sessions_held" in f else None,
    }
    gains = r[r > 0].sum()
    losses = -r[r < 0].sum()
    out["profit_factor"] = round(float(gains / losses), 3) if len(r) >= 30 and losses > 0 else None
    if len(f):
        weeks = pd.to_datetime(f["decision_date"]).dt.to_period("W").astype(str).to_numpy()
        out["mean_return_net_ci"] = block_bootstrap_mean(r, weeks)
        out["distinct_dates"] = int(pd.to_datetime(f["decision_date"]).nunique())
        if "sector" in f:
            out["distinct_sectors"] = int(f["sector"].nunique())
    return out


def safe(x):
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x
