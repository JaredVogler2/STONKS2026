"""Null-calibrated inference for clustered, overlapping trade outcomes (QV2-003/004/005/012; amendment 04 section 3).

Protocol-1's `p_one_sided = mean(bootstrap means <= 0)` is a descriptive tail share. The M0 audit measured a 10.9-17.1%
rejection rate of true nulls at nominal 5% for H8- and H5-shaped samples. This module replaces it for protocol-2:

* Unit: one value per trade, usually a strategy-minus-matched-control difference. Each value is placed on the NYSE
  session index of its decision date. All values of one session move together, so cross-sectional correlation is kept.
* Statistic: the trade-weighted mean, studentized with a block-cluster (batch-means) standard error that includes a
  Bartlett lag-1 term for adjacent blocks (20-session holds straddle block borders).
* Null distribution: a circular block bootstrap of the session index with block length L sessions (about the label
  length or longer, e.g. 20/40/60; the Bartlett term covers adjacent-block overlap), CENTRED at the estimate. So
  p = P*(t* >= t_hat) tests H0: mean <= 0, and the percentile-t bounds are intervals for the mean.
* Fewer than `min_blocks` non-empty, non-overlapping L-blocks gives INSUFFICIENT_CLUSTERS and no p-value. The rule
  applies to a FAILED reading as much as to a VALIDATED one.

`calibrate()` measures the procedure on simulated designs (true nulls and planted effects), so its size and power are
evidence rather than an assumption. See bench/qv2_m1_inference_battery.py and docs/quant-v2/M1_ENGINE.md.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass

import numpy as np

INFERENCE_VERSION = "inference-v2.1"  # 2.1 (A1): optional Student-t small-sample floor (protocol-2 uses it)
BLOCK_LENGTHS = (20, 40, 60)
MIN_BLOCKS = 12
N_BOOT = 4999


@dataclass(frozen=True)
class BlockTest:
    status: str  # OK | INSUFFICIENT_CLUSTERS | EMPTY
    n: int
    sessions: int
    block_len: int
    blocks: int
    nonempty_blocks: int
    mean: float | None = None
    se: float | None = None
    t: float | None = None
    p_one_sided: float | None = None
    lower_95: float | None = None  # one-sided 95% lower confidence bound for the mean (percentile-t)
    ci90: tuple[float, float] | None = None
    design_effect: float | None = None  # block variance / iid variance of the mean
    n_boot: int = 0
    seed: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ci90"] = list(self.ci90) if self.ci90 else None
        return d


def _sums(values: np.ndarray, idx: np.ndarray, sessions: int) -> tuple[np.ndarray, np.ndarray]:
    s = np.bincount(idx, weights=values, minlength=sessions).astype(float)
    n = np.bincount(idx, minlength=sessions).astype(float)
    return np.concatenate([[0.0], np.cumsum(s)]), np.concatenate([[0.0], np.cumsum(n)])


def _block(prefix: np.ndarray, starts: np.ndarray, length: int, sessions: int) -> np.ndarray:
    """Circular block sums over [start, start + length) on the session index."""
    end = starts + length
    inside = np.minimum(end, sessions)
    out = prefix[inside] - prefix[starts]
    wrap = end > sessions
    if np.any(wrap):
        out = out + np.where(wrap, prefix[np.clip(end - sessions, 0, sessions)], 0.0)
    return out


def _studentize(sb: np.ndarray, nb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Ratio mean and block-cluster SE along the last axis (blocks, in time order).

    Holds overlap block borders, so adjacent block residuals correlate; a Bartlett lag-1 term covers that (positive
    semi-definite). The same formula studentizes the bootstrap draws. Empty blocks carry no information."""
    tot = nb.sum(axis=-1)
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = sb.sum(axis=-1) / tot
        resid = sb - mean[..., None] * nb
        g = (nb > 0).sum(axis=-1)
        q = (resid**2).sum(axis=-1) + (resid[..., 1:] * resid[..., :-1]).sum(axis=-1)
        var = q / tot**2 * g / np.maximum(g - 1, 1)
    return mean, np.sqrt(var)


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta function (modified Lentz)."""
    tiny, qab, qap, qam = 1e-300, a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c if abs(1.0 + aa / c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c if abs(1.0 + aa / c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbt) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbt) * _betacf(b, a, 1.0 - x) / b


def student_t_sf(t: float, df: float) -> float:
    """P(T > t) for Student's t with `df` degrees of freedom (no scipy in the runtime environment)."""
    tail = 0.5 * _betainc(df / 2.0, 0.5, df / (df + t * t))
    return tail if t > 0 else 1.0 - tail


def student_t_isf(q: float, df: float) -> float:
    """The t with P(T > t) = q (bisection on student_t_sf; q in (0, 1))."""
    lo, hi = -1e3, 1e3
    for _ in range(200):
        mid = (lo + hi) / 2
        if student_t_sf(mid, df) > q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def block_test(
    values: Sequence[float] | np.ndarray,
    session_index: Sequence[int] | np.ndarray,
    sessions: int,
    block_len: int = 20,
    n_boot: int = N_BOOT,
    seed: int = 11,
    min_blocks: int = MIN_BLOCKS,
    t_floor: bool = False,
) -> BlockTest:
    """One-sided test of H0: mean <= 0 for values clustered on `session_index` (0..sessions-1).

    `t_floor` (protocol-2 A1): the p-value is the LARGER of the bootstrap-t p and the Student-t p with df = non-empty
    blocks - 1, and the bounds use the wider of the two quantiles. With few effective clusters and fat tails the
    bootstrap-t alone over-rejects; the floor can only make the test more conservative."""
    v = np.asarray(values, float)
    idx = np.asarray(session_index, int)
    if len(v) != len(idx):
        raise ValueError("values and session_index differ in length")
    ok = np.isfinite(v)
    v, idx = v[ok], idx[ok]
    if len(v) and (idx.min() < 0 or idx.max() >= sessions):
        raise ValueError("session_index outside 0..sessions-1")
    k = math.ceil(sessions / block_len) if sessions > 0 else 0
    base = {"n": len(v), "sessions": sessions, "block_len": block_len, "blocks": k}
    if len(v) == 0 or sessions <= 0:
        return BlockTest("EMPTY", nonempty_blocks=0, **base)
    ps, pn = _sums(v, idx, sessions)
    starts = np.arange(k) * block_len  # non-overlapping partition (the last block may be shorter)
    ends = np.minimum(starts + block_len, sessions)
    sb, nb = ps[ends] - ps[starts], pn[ends] - pn[starts]
    nonempty = int((nb > 0).sum())
    mean, se = (float(x) for x in _studentize(sb, nb))
    iid_var = float(v.var(ddof=1) / len(v)) if len(v) > 1 else float("nan")
    deff = float(se**2 / iid_var) if iid_var and iid_var > 0 and math.isfinite(iid_var) else None
    if nonempty < min_blocks or sessions < block_len or not se > 0:
        return BlockTest("INSUFFICIENT_CLUSTERS", nonempty_blocks=nonempty, mean=mean, se=se if se > 0 else None,
                         design_effect=deff, **base)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, sessions, size=(n_boot, k))
    m_star, se_star = _studentize(_block(ps, draws, block_len, sessions), _block(pn, draws, block_len, sessions))
    good = np.isfinite(m_star) & (se_star > 0)
    if good.sum() < max(100, n_boot // 10):  # degenerate resampling: no reliable null distribution
        return BlockTest("INSUFFICIENT_CLUSTERS", nonempty_blocks=nonempty, mean=mean, se=se, design_effect=deff, **base)
    t_star = (m_star[good] - mean) / se_star[good]
    t_hat = mean / se
    p = float((1 + np.sum(t_star >= t_hat)) / (1 + len(t_star)))
    q05, q95 = np.quantile(t_star, [0.05, 0.95])
    if t_floor:
        df = nonempty - 1
        p = max(p, student_t_sf(t_hat, df))
        tq = student_t_isf(0.05, df)
        q05, q95 = min(q05, -tq), max(q95, tq)
    return BlockTest(
        "OK", nonempty_blocks=nonempty, mean=mean, se=se, t=t_hat, p_one_sided=p,
        lower_95=float(mean - q95 * se), ci90=(float(mean - q95 * se), float(mean - q05 * se)), design_effect=deff,
        n_boot=len(t_star), seed=seed, **base,
    )


def block_tests(values, session_index, sessions: int, lengths: Sequence[int] = BLOCK_LENGTHS, **kw) -> dict:
    """The test at each block length plus the CONSERVATIVE summary: the largest p and the smallest lower bound."""
    out = {f"L{L}": block_test(values, session_index, sessions, block_len=L, **kw) for L in lengths}
    ok = [r for r in out.values() if r.status == "OK"]
    summary = {
        "status": "OK" if len(ok) == len(out) else ("INSUFFICIENT_CLUSTERS" if any(
            r.status == "INSUFFICIENT_CLUSTERS" for r in out.values()) else "EMPTY"),
        "p_one_sided_max": max(r.p_one_sided for r in ok) if len(ok) == len(out) else None,
        "lower_95_min": min(r.lower_95 for r in ok) if len(ok) == len(out) else None,
        "mean": next((r.mean for r in out.values() if r.mean is not None), None),
        "lengths": list(lengths),
    }
    return {"summary": summary, **{k: r.to_dict() for k, r in out.items()}}


def holm(pvals: dict[str, float | None]) -> dict[str, float | None]:
    """Holm step-down adjusted p-values over the hypotheses that HAVE a valid p; the others stay None."""
    items = sorted(((k, p) for k, p in pvals.items() if p is not None), key=lambda kv: kv[1])
    m, run, out = len(items), 0.0, {k: None for k in pvals}
    for i, (k, p) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[k] = run
    return out


# ---- simulation battery (Type I and power) -----------------------------------------------------------------------
@dataclass(frozen=True)
class Design:
    """A synthetic trade panel shaped like a real study: `dates` decision sessions spread over `span` sessions,
    `per_date` trades each, 20-session overlapping holds driven by market + sector + idiosyncratic daily shocks.
    `paired=True` subtracts a same-date control exposed to the same market shock (a strategy-minus-control value)."""

    name: str
    span: int
    dates: int
    per_date: int
    hold: int = 20
    tail_df: float | None = None  # Student-t daily shocks (fat tails) when set
    paired: bool = True
    hetero: bool = False  # volatility regimes: one third of the span at triple volatility


def simulate_design(d: Design, rng: np.random.Generator, effect: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    t = d.span + d.hold

    def shocks(size, scale):
        z = rng.standard_t(d.tail_df, size) / math.sqrt(d.tail_df / (d.tail_df - 2)) if d.tail_df else rng.normal(0, 1, size)
        return z * scale

    vol = np.ones(t)
    if d.hetero:
        a = int(rng.integers(0, t - t // 3))
        vol[a : a + t // 3] = 3.0
    mkt = shocks(t, 0.009) * vol
    sec = shocks((11, t), 0.006) * vol
    starts = np.sort(rng.choice(d.span, size=min(d.dates, d.span), replace=False))
    vals, idx = [], []
    for s in starts:
        w = slice(s + 1, s + 1 + d.hold)
        for _ in range(d.per_date):
            a, b = int(rng.integers(11)), int(rng.integers(11))
            x = (mkt[w] + sec[a, w] + shocks(d.hold, 0.012) * vol[w]).sum()
            if d.paired:
                x -= (mkt[w] + sec[b, w] + shocks(d.hold, 0.012) * vol[w]).sum()
            vals.append(x + effect)
            idx.append(s)
    return np.array(vals), np.array(idx)


def calibrate(
    designs: Sequence[Design],
    reps: int = 1000,
    effects: Sequence[float] = (0.0,),
    lengths: Sequence[int] = BLOCK_LENGTHS,
    alphas: Sequence[float] = (0.05, 0.10),
    n_boot: int = 999,
    seed: int = 20261022,
    min_blocks: int = MIN_BLOCKS,
    progress: Callable[[str], None] = lambda _m: None,
) -> list[dict]:
    """Rejection rates (with 95% Monte Carlo intervals) of the conservative block test and of each block length."""
    rows = []
    for d in designs:
        for eff in effects:
            rng = np.random.default_rng(seed)
            ps: dict[str, list[float | None]] = {f"L{L}": [] for L in lengths} | {"conservative": []}
            insufficient = 0
            for r in range(reps):
                v, ix = simulate_design(d, rng, eff)
                res = block_tests(v, ix, d.span, lengths=lengths, n_boot=n_boot, seed=r, min_blocks=min_blocks)
                for L in lengths:
                    ps[f"L{L}"].append(res[f"L{L}"]["p_one_sided"])
                ps["conservative"].append(res["summary"]["p_one_sided_max"])
                insufficient += res["summary"]["status"] != "OK"
            row = {"design": asdict(d), "effect": eff, "reps": reps, "n_boot": n_boot,
                   "insufficient_clusters_share": round(insufficient / reps, 4)}
            for key, vals in ps.items():
                arr = np.array([1.0 if p is None else p for p in vals])  # no p-value = no rejection
                for a in alphas:
                    rate = float((arr < a).mean())
                    half = 1.96 * math.sqrt(max(rate * (1 - rate), 1e-12) / reps)
                    row[f"{key}_reject_{a}"] = round(rate, 4)
                    row[f"{key}_reject_{a}_mc95"] = [round(max(0.0, rate - half), 4), round(min(1.0, rate + half), 4)]
            rows.append(row)
            progress(f"{d.name} effect={eff}: conservative reject@5% {row['conservative_reject_0.05']}")
    return rows
