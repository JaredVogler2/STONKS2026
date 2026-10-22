"""Reference intraday paths and a "truth" order model for testing daily-bar execution (Quant V2 M0/M1).

`paths()` builds continuous piecewise-linear intraday paths with overnight gaps; the daily simulator only sees their
OHLC. `truth()` executes the protocol-2 order semantics (research.execution_v2) on the path itself: a buy limit with an
open guard, then a resting stop (fixed, or trailing from daily closes/ATR through d-1), an optional resting target and a
time exit at the close of fill+H. Because the path is known, the truth has no ambiguity.
"""

from __future__ import annotations

import numpy as np


def paths(rng: np.random.Generator, days: int = 40, steps: int = 24, p0: float = 100.0):
    out, price = [], p0
    for _ in range(days):
        price *= float(np.exp(rng.normal(0, 0.012)))
        walk = price * np.exp(np.cumsum(np.concatenate([[0.0], rng.normal(0, 0.004, steps - 1)])))
        if rng.random() < 0.15:
            k = int(rng.integers(1, steps))
            walk[k:] *= float(np.exp(rng.normal(0, 0.03)))
        out.append(walk)
        price = float(walk[-1])
    o = np.array([p[0] for p in out])
    h = np.array([p.max() for p in out])
    low = np.array([p.min() for p in out])
    c = np.array([p[-1] for p in out])
    return out, o, h, low, c


def atr_series(h: np.ndarray, low: np.ndarray, c: np.ndarray, n: int = 3) -> np.ndarray:
    """A short causal ATR (mean true range of the last n sessions, through d) for trailing-stop tests."""
    prev = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - low, np.maximum(abs(h - prev), abs(low - prev)))
    out = np.full(len(c), np.nan)
    for d in range(n - 1, len(c)):
        out[d] = tr[d - n + 1 : d + 1].mean()
    return out


def truth(ps, t, limit, min_open, stop, target, valid=2, horizon=20, trail_atr=None, atr=None, closes=None,
          through=0.0):
    fill = None
    for d in range(t + 1, min(len(ps), t + 1 + valid)):
        p = ps[d]
        if p[0] < min_open:
            continue
        if p[0] <= limit:
            fill = (d, 0, float(p[0]))
            break
        hit = np.flatnonzero(p <= limit - through)
        if len(hit):
            fill = (d, int(hit[0]), float(limit))
            break
    if fill is None:
        return None
    fd, fk, px = fill
    hi = lo = px
    last = min(len(ps) - 1, fd + horizon)
    s = stop
    for d in range(fd, last + 1):
        p = ps[d]
        if trail_atr is not None and d > fd:
            s = max(s, float(np.max(closes[fd:d]) - trail_atr * atr[d - 1]))
        if d > fd and p[0] <= s:
            return _r("STOP", float(p[0]), px, fd, d, hi, min(lo, p[0]))
        if d > fd and target is not None and p[0] >= target:
            return _r("TARGET", float(p[0]), px, fd, d, max(hi, p[0]), lo)
        pts = p[fk:] if d == fd else p[1:]
        if d > fd:
            hi, lo = max(hi, p[0]), min(lo, p[0])
        for raw in pts:
            x = float(raw)
            if x <= s:
                return _r("STOP", s, px, fd, d, hi, min(lo, s))
            if target is not None and x >= target + through:  # a resting sell limit needs the same trade-through
                return _r("TARGET", target, px, fd, d, max(hi, target), lo)
            hi, lo = max(hi, x), min(lo, x)
        if d == fd + horizon:
            return _r("TIME", float(p[-1]), px, fd, d, hi, lo)
    return _r("DATA_END", float(ps[last][-1]), px, fd, last, hi, lo)


def _r(reason, exit_, px, fd, d, hi, lo):
    return {"reason": reason, "exit": exit_, "fill": px, "fill_day": fd, "exit_day": d, "mfe": hi / px - 1,
            "mae": lo / px - 1}
