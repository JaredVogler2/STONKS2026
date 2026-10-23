"""Decision logic of the prospective evidence tournament `v3x-epa-tournament-v1` (docs/evidence/TOURNAMENT_LANE.md).

Research only: paper books, no orders. Target weights are decided at the close of t from causal data through t and
traded at the close of t + 1. Every target is long-only, unlevered and stored as integer micro-weights (1e-6) that sum to
exactly 1,000,000, so a recorded decision replays exactly. `CASH` holds whatever a book cannot invest.

Books (rebalance sessions are counted from the lane's first decision session; phase 0 is a rebalance, so every book is
invested from its first trade):
* g2         pilot 2's G2: equal weight in the top 10 by 60-session return under the explicit mask (U1 membership at t
             plus the price, liquidity and history floors, no risk-off breaker), every 60 sessions, with a rank buffer
             (held names stay while ranked below 20);
* g2-twin    its negative control: the mean of 20 random buffered books with the same mask, schedule and buffer, ranked
             by draws seeded with (book seed, sub-book, protocol session index): reproducible from the data alone;
* ew         equal weight over the explicit mask, every 20 sessions (the twin of every tilt);
* mom-tilt   ew x (1 + 0.5 z): z is the cross-sectional rank of the 60-session return, mapped to [-1, 1];
* rev-tilt   the same with z from minus the 5-session return (short-term reversal), every 5 sessions;
* hi52-tilt  the same with z from the close's distance below its 252-session high close (George and Hwang, 2004).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from apex_worker.discovery.v3.agents import explicit_mask
from apex_worker.research.realeq import universe_v2 as U
from apex_worker.research.training_data import BENCHMARK

MICRO = 1_000_000
CASH = "CASH"
HIGH_LOOKBACK = 252
BOOKS: dict[str, dict] = {
    "g2": {"kind": "buffered_top", "feature": "ret_60", "n": 10, "keep_rank": 20, "every": 60},
    "g2-twin": {"kind": "random_buffered_mean", "n": 10, "keep_rank": 20, "every": 60, "sub_books": 20,
                "seed": 20261026},
    "ew": {"kind": "equal_weight", "every": 20},
    "mom-tilt": {"kind": "tilt", "feature": "ret_60", "sign": 1, "strength": 0.5, "every": 20},
    "rev-tilt": {"kind": "tilt", "feature": "ret_5", "sign": -1, "strength": 0.5, "every": 5},
    "hi52-tilt": {"kind": "tilt", "feature": "high_252", "sign": 1, "strength": 0.5, "every": 20},
}
# Cohort 0 of the tournament (owner-approved 2026-10-23): prior mass 0.45; the remaining 0.55 is reserved for later
# admissions (S1/S3 books, SEC-event and cap-weighted designs). loss_bound / twin_loss_bound: declared worst one-session
# relative loss against SPY / against the twin (checked on every observation; a break voids that evidence).
CANDIDATES: tuple[dict, ...] = (
    {"candidate_id": "g2", "book": "g2", "twin": "g2-twin", "prior_mass": 0.10, "loss_bound": 0.20,
     "twin_loss_bound": 0.20,
     "mechanism": "pilot 2's strongest DEV result: quarterly buffered top-10 momentum (concentrated; decades to evidence)"},
    {"candidate_id": "mom-tilt", "book": "mom-tilt", "twin": "ew", "prior_mass": 0.10, "loss_bound": 0.05,
     "twin_loss_bound": 0.03,
     "mechanism": "diversified momentum tilt: the same signal at low tracking error (the information-rate design rule)"},
    {"candidate_id": "rev-tilt", "book": "rev-tilt", "twin": "ew", "prior_mass": 0.10, "loss_bound": 0.05,
     "twin_loss_bound": 0.03,
     "mechanism": "short-term reversal tilt, weekly at the close (liquidity provision; cheap close execution)"},
    {"candidate_id": "hi52-tilt", "book": "hi52-tilt", "twin": "ew", "prior_mass": 0.10, "loss_bound": 0.05,
     "twin_loss_bound": 0.03, "mechanism": "52-week-high proximity tilt (anchoring; George and Hwang 2004)"},
    {"candidate_id": "ew", "book": "ew", "twin": None, "prior_mass": 0.05, "loss_bound": 0.05, "twin_loss_bound": None,
     "mechanism": "equal weight over the explicit mask: the tilts' common twin, as its own claim against SPY"},
)
TOURNAMENT = {"book": "evidence_weighted", "null_mass": 0.5, "alpha": 0.05,
              "lambdas": [1 / 16, 1 / 8, 1 / 4, 1 / 2, 3 / 4, 1.0], "cost_per_side": 0.0002}
COSTS_BPS = {"stock_close": 2.0, "spy": 5.0, "cash": 0.0}  # per side, cost-v3-2
FEATURES = {"ret_60": "60-session log return (features.compute)", "ret_5": "5-session log return (features.compute)",
            "high_252": "close / max(close over the last 252 sessions, t included) - 1, from split-adjusted closes"}
FEATURE_SCHEMA_SHA256 = hashlib.sha256(json.dumps(FEATURES, sort_keys=True).encode()).hexdigest()


@dataclass
class Panel:
    """The causal inputs the books read, on the session axis of SPY's bars: a light stand-in for study_v2.prepare with
    the same definitions (features.compute: log returns, 20-session median dollar volume, 20-session mean volume, bars
    available), so explicit_mask applies unchanged. Seconds instead of minutes on the full U1 window."""

    sessions: pd.DatetimeIndex
    stocks: list[str]
    frames: dict[str, pd.DataFrame]
    bars: dict[str, pd.DataFrame]


def light_panel(bars: dict[str, pd.DataFrame]) -> Panel:
    meta = U.u1_meta()
    sessions = pd.DatetimeIndex(bars[BENCHMARK].index)
    stocks = sorted(s for s, m in meta.items() if s in bars and m.asset_kind == "stock")
    frames = {}
    for s in stocks:
        df = bars[s].sort_index()
        c, v = df["close"].astype(float), df["volume"].astype(float)
        logc = np.log(c)
        frames[s] = pd.DataFrame({"close": c, "ret_5": logc - logc.shift(5), "ret_60": logc - logc.shift(60),
                                  "dollar_vol_med_20": (c * v).rolling(20, min_periods=20).median(),
                                  "avg_vol_20": v.rolling(20, min_periods=20).mean(),
                                  "bars_available": np.arange(1, len(df) + 1, dtype=float)},
                                 index=df.index).reindex(sessions)
    return Panel(sessions, stocks, frames, bars)


def to_micro(weights: dict[str, float]) -> dict[str, int]:
    """Integer micro-weights summing to exactly MICRO (largest remainder, ties by symbol)."""
    tot = sum(weights.values())
    if tot <= 0:
        return {CASH: MICRO}
    raw = {k: v / tot * MICRO for k, v in weights.items() if v > 0}
    base = {k: int(np.floor(v)) for k, v in raw.items()}
    short = MICRO - sum(base.values())
    for k in sorted(raw, key=lambda k: (-(raw[k] - base[k]), k))[:short]:
        base[k] += 1
    return {k: v for k, v in sorted(base.items()) if v}


def feature_at(pre: Panel, name: str, t: int) -> np.ndarray:
    if name == "high_252":
        out = np.full(len(pre.stocks), np.nan)
        for i, s in enumerate(pre.stocks):
            c = pre.bars[s]["close"].reindex(pre.sessions).to_numpy(float)[max(0, t - HIGH_LOOKBACK + 1): t + 1]
            if np.isfinite(c[-1]) and np.sum(np.isfinite(c)) >= HIGH_LOOKBACK // 2:
                out[i] = c[-1] / np.nanmax(c) - 1.0
        return out
    return np.array([float(pre.frames[s][name].iloc[t]) for s in pre.stocks])


def _buffered_pick(score: np.ndarray, mask: np.ndarray, syms: list[str], held: list[str], n: int, keep: int
                   ) -> list[str]:
    cand = [i for i in np.flatnonzero(mask) if np.isfinite(score[i])]
    order = [syms[i] for i in sorted(cand, key=lambda i: (-score[i], syms[i]))]
    rank = {s: r for r, s in enumerate(order)}
    kept = [s for s in held if s in rank and rank[s] < keep]
    return sorted(kept + [s for s in order if s not in kept][: max(0, n - len(kept))])


def _equal(names: list[str], n: int | None = None) -> dict[str, int]:
    if not names:
        return {CASH: MICRO}
    w = dict.fromkeys(names, 1.0 / (n or len(names)))
    if n and len(names) < n:
        w[CASH] = (n - len(names)) / n  # fewer qualifiers: the rest stays in cash (G2's registered rule)
    return to_micro(w)


def _tilt(score: np.ndarray, mask: np.ndarray, syms: list[str], strength: float) -> dict[str, int]:
    idx = list(np.flatnonzero(mask))
    if not idx:
        return {CASH: MICRO}
    fin = sorted((i for i in idx if np.isfinite(score[i])), key=lambda i: (score[i], syms[i]))
    z = dict.fromkeys(idx, 0.0)  # a name without the feature gets the neutral weight
    if len(fin) > 1:
        z |= {i: 2.0 * r / (len(fin) - 1) - 1.0 for r, i in enumerate(fin)}
    return to_micro({syms[i]: 1.0 + strength * z[i] for i in idx})


def decide(pre: Panel, t: int, t_abs: int, phase: int, previous: dict[str, dict | None],
           mask: np.ndarray | None = None) -> dict[str, dict | None]:
    """Targets decided at the close of t for every book: a book rebalances when phase % every == 0, or at its first
    recorded decision (a late activation or a missed first session must not leave it in SPY until the next scheduled
    rebalance), otherwise None (hold). A missed rebalance session is not made up. `previous`: each book's last recorded
    target (its buffer state)."""
    syms = list(pre.stocks)
    el = explicit_mask(pre)[:, t] if mask is None else mask
    out: dict[str, dict | None] = {}
    for book, spec in BOOKS.items():
        if phase % spec["every"] and previous.get(book) is not None:
            out[book] = None
            continue
        kind, prev = spec["kind"], previous.get(book) or {}
        if kind == "buffered_top":
            score = feature_at(pre, spec["feature"], t)
            held = [s for s in prev.get("micro", {}) if s != CASH]
            names = _buffered_pick(score, el, syms, held, spec["n"], spec["keep_rank"])
            out[book] = {"micro": _equal(names, spec["n"])}
        elif kind == "random_buffered_mean":
            subs, mixed = [], {}
            old = prev.get("sub_books") or [[] for _ in range(spec["sub_books"])]
            for j in range(spec["sub_books"]):
                rng = np.random.default_rng([spec["seed"], j, t_abs])
                names = _buffered_pick(rng.random(len(syms)), el, syms, old[j], spec["n"], spec["keep_rank"])
                subs.append(names)
                for s, w in _equal(names, spec["n"]).items():
                    mixed[s] = mixed.get(s, 0.0) + w / spec["sub_books"]
            out[book] = {"micro": to_micro(mixed), "sub_books": subs}
        elif kind == "equal_weight":
            out[book] = {"micro": _equal([syms[i] for i in np.flatnonzero(el)])}
        else:
            out[book] = {"micro": _tilt(spec["sign"] * feature_at(pre, spec["feature"], t), el, syms, spec["strength"])}
    return out
