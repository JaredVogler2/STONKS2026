"""Quant V2 cross-sectional features for S1-S3 (amendment 05). Causal: the value at t uses bars <= t only.

All matrices share ONE session index (the benchmark's). Cross-sections use only stocks ELIGIBLE at t (live gates plus
universe membership), so a stock that joins later never enters an earlier rank, percentile or breadth.

Columns added to each stock frame:
  v2_sector_pct_ret_20, v2_sector_pct_ret_60  percentile within the stock's sector among eligible stocks at t
                                              (all eligible stocks when the sector has fewer than 8)
  v2_leader                                   both percentiles >= 0.75, rs_bench_60 > 0, ret_20 <= ln(1.25)
  v2_peak_age_10                              sessions since the highest close of the last 10 (most recent if tied)
  v2_leader_at_peak                           v2_leader on that peak session
  v2_pullback_atr                             (that highest close - close) / ATR14
  v2_trough_low                               lowest low from the peak session through t
  v2_prev_high                                high of t-1
  v2_low                                      low of t (S3 retest test)
  v2_sector_breadth                           share of eligible sector stocks above their SMA50
  v2_resid_score_20                           20-session residual return vs SPY and (sector ETF - SPY), betas fitted
                                              on the 126 sessions ending t-20 (never the scoring window), divided by
                                              the fit's residual sd x sqrt(20); one-factor (SPY) fit when the sector
                                              ETF lacks history; NaN when fewer than 100 fit observations
  v2_resid_model                              2, 1 or 0 factors used
  v2_xs_pct_resid, v2_xs_pct_ret_20           percentiles among eligible stocks with a value at t
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

QV2_FEATURE_VERSION = "qv2-features-1"
FIT, SCORE, MIN_FIT = 126, 20, 100


def _wide(frames: dict[str, pd.DataFrame], col: str, index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame({s: f[col].reindex(index).astype(float) for s, f in frames.items()}, index=index)


def _pct(values: pd.DataFrame, elig: pd.DataFrame) -> pd.DataFrame:
    return values.where(elig).rank(axis=1, pct=True)


def _rolling_sum(a: np.ndarray, w: int) -> np.ndarray:
    """Sum over the trailing window of w rows (NaN -> 0); first w-1 rows are NaN."""
    c = np.cumsum(np.nan_to_num(a), axis=0)
    out = np.full_like(c, np.nan, dtype=float)
    out[w - 1 :] = c[w - 1 :] - np.concatenate([np.zeros((1, *c.shape[1:])), c[: -w]], axis=0)
    return out


def _lag(a: np.ndarray, good: np.ndarray) -> np.ndarray:
    """Value fitted at t-SCORE, placed at t (invalid fits become NaN)."""
    return np.concatenate([np.full(SCORE, np.nan), np.where(good, a, np.nan)[:-SCORE]])


def residual_scores(y: np.ndarray, x1: np.ndarray, x2: np.ndarray, ridge: float = 1e-3) -> tuple[np.ndarray, np.ndarray]:
    """(score, model) for one stock's daily log returns y vs x1 = SPY, x2 = sector - SPY (1-D arrays, one index).

    Rolling OLS with intercept via windowed moments; a small ridge (ridge x mean diagonal) stabilises the 2x2 solve and
    windows whose regressors are nearly collinear (squared correlation > 0.999 after the ridge) are rejected."""
    n = len(y)
    score, model = np.full(n, np.nan), np.zeros(n, np.int8)
    for k in (2, 1):
        cols = (x1, x2) if k == 2 else (x1,)
        ok = np.isfinite(y) & np.all([np.isfinite(x) for x in cols], axis=0)
        yy = np.where(ok, y, 0.0)
        xs = [np.where(ok, x, 0.0) for x in cols]
        cnt = _rolling_sum(ok.astype(float), FIT)
        with np.errstate(divide="ignore", invalid="ignore"):
            sy = _rolling_sum(yy, FIT)
            cyy = _rolling_sum(yy * yy, FIT) - sy * sy / cnt
            sx = [_rolling_sum(x, FIT) for x in xs]
            c = [_rolling_sum(x * yy, FIT) - sxi * sy / cnt for x, sxi in zip(xs, sx, strict=True)]
            a11 = _rolling_sum(xs[0] * xs[0], FIT) - sx[0] ** 2 / cnt
            if k == 2:
                a22 = _rolling_sum(xs[1] * xs[1], FIT) - sx[1] ** 2 / cnt
                a12 = _rolling_sum(xs[0] * xs[1], FIT) - sx[0] * sx[1] / cnt
                lam = ridge * (a11 + a22) / 2
                d11, d22 = a11 + lam, a22 + lam
                det = d11 * d22 - a12 * a12
                b1 = (d22 * c[0] - a12 * c[1]) / det
                b2 = (d11 * c[1] - a12 * c[0]) / det
                ssr = cyy - b1 * c[0] - b2 * c[1]
                stable = det / (d11 * d22) > 1e-3
                betas = (b1, b2)
            else:
                d11 = a11 * (1 + ridge)
                b1 = c[0] / d11
                ssr = cyy - b1 * c[0]
                stable = d11 > 0
                betas = (b1,)
            sd = np.sqrt(ssr / (cnt - k - 1))
        good = (cnt >= MIN_FIT) & stable & np.isfinite(sd) & (ssr > 0)
        # betas fitted on [t-145, t-20] are applied to the scoring window [t-19, t] (never the same sessions)
        okw = _rolling_sum(ok.astype(float), SCORE)
        resid = _rolling_sum(yy, SCORE) - sum(_lag(b, good) * _rolling_sum(x, SCORE) for b, x in zip(betas, xs, strict=True))
        with np.errstate(divide="ignore", invalid="ignore"):
            s = resid / (_lag(sd, good) * math.sqrt(SCORE))
        fill = np.isnan(score) & np.isfinite(s) & (okw == SCORE)
        score[fill], model[fill] = s[fill], k
    return score, model


def add_v2_features(ctx: dict[str, pd.DataFrame], eligible: dict[str, pd.Series], sector: dict[str, str | None],
                    index: pd.DatetimeIndex, spy_close: pd.Series, sector_close: dict[str, pd.Series],
                    lows: dict[str, pd.Series], highs: dict[str, pd.Series]) -> dict[str, pd.DataFrame]:
    """New frames on `index` = each stock's context frame plus the v2 columns (inputs are not modified)."""
    syms = sorted(ctx)
    out = {s: ctx[s].reindex(index).copy() for s in syms}
    elig = pd.DataFrame({s: eligible[s].reindex(index).fillna(False).astype(bool) for s in syms}, index=index)
    r20, r60 = _wide(out, "ret_20", index), _wide(out, "ret_60", index)
    all20, all60 = _pct(r20, elig), _pct(r60, elig)
    pct20, pct60 = all20.copy(), all60.copy()
    above50 = (_wide(out, "dist_sma50_atr", index) > 0).astype(float).where(_wide(out, "dist_sma50_atr", index).notna())
    breadth = pd.DataFrame(np.nan, index=index, columns=syms)
    for sec in sorted({v for v in sector.values() if v}):
        cols = [s for s in syms if sector.get(s) == sec]
        e = elig[cols]
        small = e.sum(axis=1) < 8
        s20, s60 = _pct(r20[cols], e), _pct(r60[cols], e)
        pct20[cols] = s20.where(~small, all20[cols], axis=0)
        pct60[cols] = s60.where(~small, all60[cols], axis=0)
        b = above50[cols].where(e).mean(axis=1)
        for s in cols:
            breadth[s] = b
    spy_lr = np.log(spy_close.reindex(index).astype(float)).diff().to_numpy()
    resid = pd.DataFrame(np.nan, index=index, columns=syms)
    model = pd.DataFrame(0, index=index, columns=syms)
    sec_lr = {k: np.log(v.reindex(index).astype(float)).diff().to_numpy() for k, v in sector_close.items()}
    for s in syms:
        f = out[s]
        c = f["close"].to_numpy(float)
        y = np.diff(np.log(c), prepend=np.nan)
        x2 = sec_lr.get(sector.get(s), np.full(len(index), np.nan)) - spy_lr
        sc, md = residual_scores(y, spy_lr, x2)
        resid[s], model[s] = sc, md
        # ---- S1 pullback geometry ----
        n = len(c)
        age = np.full(n, np.nan)
        peak = np.full(n, np.nan)
        if n >= 10:
            win = np.lib.stride_tricks.sliding_window_view(np.where(np.isfinite(c), c, -np.inf), 10)
            last_max = 9 - np.argmax(win[:, ::-1], axis=1)  # position of the most recent maximum in the window
            valid = np.all(np.isfinite(np.lib.stride_tricks.sliding_window_view(c, 10)), axis=1)
            age[9:] = np.where(valid, 9 - last_max, np.nan)
            peak[9:] = np.where(valid, win.max(axis=1), np.nan)
        lo = lows[s].reindex(index).to_numpy(float)
        trough = np.full(n, np.nan)
        leader = ((pct20[s] >= 0.75) & (pct60[s] >= 0.75) & (f["rs_bench_60"] > 0) & (f["ret_20"] <= math.log(1.25))
                  ).to_numpy(bool)
        at_peak = np.zeros(n, bool)
        for t in np.flatnonzero(np.isfinite(age)):
            a = int(age[t])
            trough[t] = np.nanmin(lo[t - a : t + 1]) if np.isfinite(lo[t - a : t + 1]).any() else np.nan
            at_peak[t] = leader[t - a]
        atr = f["atr_14"].to_numpy(float)
        f["v2_sector_pct_ret_20"] = pct20[s]
        f["v2_sector_pct_ret_60"] = pct60[s]
        f["v2_leader"] = leader.astype(float)
        f["v2_peak_age_10"] = age
        f["v2_leader_at_peak"] = at_peak.astype(float)
        with np.errstate(divide="ignore", invalid="ignore"):
            f["v2_pullback_atr"] = (peak - c) / atr
        f["v2_trough_low"] = trough
        f["v2_prev_high"] = highs[s].reindex(index).shift(1).to_numpy(float)
        f["v2_low"] = lo
        f["v2_sector_breadth"] = breadth[s]
    pres = _pct(resid, elig & resid.notna())
    praw = all20
    for s in syms:
        out[s]["v2_resid_score_20"] = resid[s]
        out[s]["v2_resid_model"] = model[s].astype(float)
        out[s]["v2_xs_pct_resid"] = pres[s]
        out[s]["v2_xs_pct_ret_20"] = praw[s]
    return out
