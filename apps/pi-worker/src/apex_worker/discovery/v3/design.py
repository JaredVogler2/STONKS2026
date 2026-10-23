"""Discovery V3 design study `design-study-1` (docs/evidence/DESIGN_STUDY_1.md): how fast could a tilt design earn
prospective evidence?

The evidence tournament is limited by the information rate of a candidate's return relative to its numeraire. For the
bounded test it is also limited by the declared worst one-session loss L, because bets are capped at 0.9 / L
(docs/evidence/EVIDENCE_TOURNAMENT.md sections 2 and 4). This study MEASURES, on the DEV window, the two quantities a
design controls:
* the tracking error of each tilt against its equal-weight twin (and against SPY);
* its worst one-session relative loss, which sets the loss bound it can declare.

It compares cohort 0's tilts (`raw`) with two neutralized versions at the same active-weight dispersion:
* `sector`: within-sector ranks, so the active weights are sector-neutral;
* `full`: those ranks' cross-sectional residual on 252-session market beta and log dollar volume (a size proxy),
  demeaned within sector, so the active weights are also beta- and size-neutral.
The design rule predicts a lower tracking error for the neutral versions, and so faster evidence.

A measurement, not a strategy test: the registered proposal rule reads only risk (tracking error and the worst
session). DEV means are written as labelled DEV_CONTAMINATED diagnostics and select nothing. Every book reuses the
tournament lane's conventions (`tournament_books`):
* the explicit mask;
* long-only integer micro-weight targets, decided at the close of t and traded at the close of t + 1;
* cost-v3-2 close costs;
* books starting in SPY.
Only aggregates leave the runner: no symbol, price or per-stock value is written.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import warnings
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from apex_worker.discovery.v3 import tournament_books as TB
from apex_worker.discovery.v3.agents import explicit_mask
from apex_worker.evidence.velocity import best_bet, years_to
from apex_worker.research.realeq import universe_v2 as U
from apex_worker.research.training_data import BENCHMARK

UNIFORM_SD = 1 / math.sqrt(3)  # cross-sectional sd of a rank mapped to [-1, 1]: every variant's active dispersion
VARIANTS = ("raw", "sector", "full")


@dataclass(frozen=True)
class DesignStudyPlan:
    plan_id: str
    title: str
    signals: tuple[tuple[str, str, int, int], ...]  # (name, feature, sign, rebalance every n sessions)
    extras: tuple[tuple[str, int], ...]  # (name, every): new designs, `full` variant only
    variants: tuple[str, ...] = VARIANTS
    strength: float = 0.5  # weight = equal weight x (1 + strength x score), score in [-1, 1]
    twin_every: int = 20
    beta_lookback: int = 252
    beta_min_obs: int = 126
    vol_lookback: int = 60
    vol_min_obs: int = 40
    twin_loss_grid: tuple[float, ...] = (0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.05)
    spy_loss_grid: tuple[float, ...] = (0.01, 0.015, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2)
    safety: float = 2.0  # proposed loss bound: the smallest grid value >= safety x the worst DEV session
    # velocity read-outs: a hypothetical gross active alpha against the twin (before costs), net of each design's
    # measured extra cost; the claim level is cohort 0's threshold
    hypothetical_gross_alpha: tuple[float, ...] = (0.005, 0.01, 0.02)
    claim_level: float = 800.0
    target_years: float = 10.0
    proposed_mass: float = 0.04
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def designs(self) -> dict[str, dict]:
        """Every measured book except the twin: name -> {signal, variant, every}."""
        out = {f"{n}-{v}": {"signal": n, "variant": v, "every": every} for n, _f, _s, every in self.signals
               for v in self.variants}
        out |= {f"{n}-full": {"signal": n, "variant": "full", "every": every} for n, every in self.extras}
        return out


DESIGN_STUDY_1 = DesignStudyPlan(
    plan_id="design-study-1",
    title="Information rate of neutralized tilts against their equal-weight twin (U1, DEV dates)",
    signals=(("mom", "ret_60", 1, 20), ("rev", "ret_5", -1, 5), ("hi52", "high_252", 1, 20)),
    extras=(("composite", 5), ("lowvol", 20)),
    notes="Registered before the run. Risk only selects: for each signal, the proposed variant is the one with the "
    "lowest tracking error against the equal-weight twin at the reference dispersion (a rank's 1/sqrt(3); ties: the "
    "more neutral); `composite-full` (the mean of the "
    "three signals' standardized full-neutral scores, every 5 sessions) and `lowvol-full` (minus the 60-session "
    "volatility of daily log returns, every 20) are proposed alongside. Each proposed loss bound is the smallest grid "
    "value at least twice the worst DEV session (against the twin, and against SPY). DEV means are DEV_CONTAMINATED "
    "diagnostics and select nothing. The proposal is for a later, separately registered cohort of the evidence "
    "tournament, at prior mass 0.04 each from the reserve; it changes no recorded series.")

DESIGN_PLANS: dict[str, DesignStudyPlan] = {p.plan_id: p for p in (DESIGN_STUDY_1,)}


# ---- inputs on the session axis -------------------------------------------------------------------------------------
@dataclass
class Inputs:
    sessions: pd.DatetimeIndex
    stocks: list[str]
    sector: np.ndarray  # sector label per stock ("" if unknown)
    close: np.ndarray  # sessions x stocks, split-adjusted closes (NaN where no bar)
    ret: np.ndarray  # sessions x stocks, simple close-to-close returns; 0 for a missing or quarantined bar
    logret: np.ndarray  # sessions x stocks, log returns; NaN where either bar is missing
    spy_ret: np.ndarray  # SPY simple returns (0 if missing)
    spy_logret: np.ndarray
    frames: dict[str, np.ndarray]  # ret_5, ret_60, dollar_vol_med_20 from the panel's frames (sessions x stocks)
    mask: np.ndarray  # stocks x sessions: the explicit mask


def inputs(pre, quarantine: dict | None = None) -> Inputs:
    """`pre`: study_v2.Prepared or tournament_books.Panel (both carry sessions, stocks, frames and bars)."""
    sessions, stocks = pd.DatetimeIndex(pre.sessions), list(pre.stocks)
    meta = U.u1_meta()
    sector = np.array([(meta[s].sector if s in meta and meta[s].sector else "") for s in stocks], dtype=object)
    close = np.column_stack([pre.bars[s]["close"].reindex(sessions).to_numpy(float) for s in stocks])
    spy = pre.bars[BENCHMARK]["close"].reindex(sessions).to_numpy(float)
    q = {(s, pd.Timestamp(x)) for s, xs in (quarantine or {}).items() for x in xs}
    prev = np.vstack([np.full((1, close.shape[1]), np.nan), close[:-1]])
    with np.errstate(divide="ignore", invalid="ignore"):
        simple = close / prev - 1.0
        logret = np.log(close / prev)
    ok = np.isfinite(simple) & (prev > 0)
    col, row = {s: j for j, s in enumerate(stocks)}, {d: i for i, d in enumerate(sessions)}
    for s, d in q:
        if s in col and d in row:
            ok[row[d], col[s]] = False
    ret = np.where(ok, simple, 0.0)
    logret = np.where(np.isfinite(logret), logret, np.nan)
    sprev = np.concatenate([[np.nan], spy[:-1]])
    with np.errstate(divide="ignore", invalid="ignore"):
        sr = spy / sprev - 1.0
    spy_ok = np.isfinite(sr) & (sprev > 0) & np.array([(BENCHMARK, d) not in q for d in sessions])
    spy_ret = np.where(spy_ok, sr, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        spy_log = np.where(spy_ok, np.log(spy / sprev), np.nan)
    frames = {k: np.column_stack([pre.frames[s][k].to_numpy(float) for s in stocks])
              for k in ("ret_5", "ret_60", "dollar_vol_med_20")}
    return Inputs(sessions, stocks, sector, close, ret, logret, spy_ret, spy_log, frames, explicit_mask(pre))


def feature(inp: Inputs, name: str, t: int, plan: DesignStudyPlan) -> np.ndarray:
    """The causal signal at the close of t, per stock (NaN if unavailable)."""
    if name in ("ret_5", "ret_60"):
        return inp.frames[name][t].copy()
    if name == "high_252":  # tournament_books.feature_at, vectorized
        w = inp.close[max(0, t - TB.HIGH_LOOKBACK + 1): t + 1]
        ok = np.isfinite(w[-1]) & (np.isfinite(w).sum(axis=0) >= TB.HIGH_LOOKBACK // 2)
        with np.errstate(invalid="ignore"):
            hi = np.nanmax(np.where(np.isfinite(w), w, -np.inf), axis=0)
        return np.where(ok, w[-1] / np.where(ok, hi, 1.0) - 1.0, np.nan)
    if name == "vol_60":
        w = inp.logret[max(0, t - plan.vol_lookback + 1): t + 1]
        n = np.isfinite(w).sum(axis=0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN columns: masked below
            sd = np.nanstd(w, axis=0, ddof=1)
        return np.where(n >= plan.vol_min_obs, sd, np.nan)
    raise ValueError(f"unknown feature {name}")


def betas(inp: Inputs, t: int, plan: DesignStudyPlan) -> np.ndarray:
    """OLS beta of daily log returns on SPY's over the last `beta_lookback` sessions through t (NaN if too few)."""
    lo = max(0, t - plan.beta_lookback + 1)
    y, m = inp.logret[lo: t + 1], inp.spy_logret[lo: t + 1, None]
    ok = np.isfinite(y) & np.isfinite(m)
    n = ok.sum(axis=0)
    mm = np.where(ok, m, 0.0)
    yy = np.where(ok, y, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mbar, ybar = mm.sum(axis=0) / n, yy.sum(axis=0) / n
        cov = (np.where(ok, (mm - mbar) * (yy - ybar), 0.0)).sum(axis=0)
        var = (np.where(ok, (mm - mbar) ** 2, 0.0)).sum(axis=0)
        b = cov / var
    return np.where((n >= plan.beta_min_obs) & (var > 0), b, np.nan)


def _fill_by_sector(x: np.ndarray, idx: np.ndarray, sector: np.ndarray) -> np.ndarray:
    """x over idx with missing values replaced by their sector's median (else the overall median, else 0)."""
    v = x[idx].astype(float)
    if np.all(~np.isfinite(v)):
        return np.zeros_like(v)
    overall = float(np.nanmedian(v))
    for s in np.unique(sector[idx]):
        sel = sector[idx] == s
        fin = v[sel & np.isfinite(v)]
        fill = float(np.median(fin)) if fin.size else overall
        v[sel & ~np.isfinite(v)] = fill
    return v


def _ranks(score: np.ndarray, idx: np.ndarray, syms: list[str]) -> np.ndarray:
    """tournament_books._tilt's score: rank among finite scores mapped to [-1, 1] (ties by symbol); 0 if missing."""
    z = np.zeros(idx.size)
    fin = sorted((k for k in range(idx.size) if np.isfinite(score[idx[k]])), key=lambda k: (score[idx[k]],
                                                                                          syms[idx[k]]))
    if len(fin) > 1:
        for r, k in enumerate(fin):
            z[k] = 2.0 * r / (len(fin) - 1) - 1.0
    return z


def neutral_score(score: np.ndarray, idx: np.ndarray, inp: Inputs, variant: str,
                  exposures: np.ndarray | None = None) -> np.ndarray:
    """The active score in [-1, 1] of each masked stock (idx) under a variant. `exposures`: idx x k matrix (beta, log
    dollar volume) for `full`, already filled."""
    if variant == "raw":
        return _ranks(score, idx, inp.stocks)
    z = _sector_ranks(score, idx, inp)
    if variant == "sector":
        return z
    if variant != "full":
        raise ValueError(f"unknown variant {variant}")
    return _scale(_residual(z, exposures, inp.sector[idx]))


def _residual(z: np.ndarray, exposures: np.ndarray, sec: np.ndarray) -> np.ndarray:
    """z minus its least-squares fit on the within-sector-demeaned exposures. z is sector-demeaned, so the residual is
    exactly sector-, beta- and size-neutral: sum over each sector = 0 and sum(residual * exposure) = 0."""
    x = exposures.astype(float).copy()
    for s in np.unique(sec):
        sel = sec == s
        x[sel] -= x[sel].mean(axis=0)
    keep = np.abs(x).sum(axis=0) > 0
    if not keep.any():
        return z
    x = x[:, keep]
    coef, *_ = np.linalg.lstsq(x, z, rcond=None)
    return z - x @ coef


def _scale(e: np.ndarray) -> np.ndarray:
    sd = float(np.std(e))
    if not np.isfinite(sd) or sd <= 0:
        return np.zeros_like(e)
    return np.clip(e * (UNIFORM_SD / sd), -1.0, 1.0)


def _weights(a: np.ndarray, idx: np.ndarray, syms: list[str], strength: float) -> dict[str, int]:
    if idx.size == 0:
        return {TB.CASH: TB.MICRO}
    return TB.to_micro({syms[i]: 1.0 + strength * float(a[k]) for k, i in enumerate(idx)})


def decide(inp: Inputs, t: int, phase: int, plan: DesignStudyPlan, first: bool) -> tuple[dict, dict]:
    """Targets (micro-weights) decided at the close of t for every book due to rebalance (all of them at the first
    decision), and each decided tilt's active exposures against the static equal weight at t: |sum over a sector|
    (largest), beta, size, and the dispersion of its score."""
    idx = np.flatnonzero(inp.mask[:, t])
    syms, sec = inp.stocks, inp.sector[idx]
    designs = plan.designs()
    out: dict[str, dict[str, int]] = {}
    if first or phase % plan.twin_every == 0:
        out["ew"] = TB._equal([syms[i] for i in idx])
    due = sorted(k for k, d in designs.items() if first or phase % d["every"] == 0)
    if not due or idx.size == 0:
        return out | {k: {TB.CASH: TB.MICRO} for k in due if idx.size == 0}, {}
    beta = betas(inp, t, plan)
    dv = inp.frames["dollar_vol_med_20"][t]
    size = np.log(np.where(dv > 0, dv, np.nan))
    expo = np.column_stack([_fill_by_sector(beta, idx, inp.sector), _fill_by_sector(size, idx, inp.sector)])
    sig = {n: sign * feature(inp, f, t, plan) for n, f, sign, _e in plan.signals}
    if any(designs[k]["signal"] == "lowvol" for k in due):
        sig["lowvol"] = -feature(inp, "vol_60", t, plan)
    expos: dict[str, dict] = {}
    for k in due:
        d = designs[k]
        if d["signal"] == "composite":  # the mean of the signals' standardized full-neutral residuals
            parts = []
            for n, _f, _s, _e in plan.signals:
                e = _residual(_sector_ranks(sig[n], idx, inp), expo, sec)
                if (sd := float(np.std(e))) > 0:
                    parts.append(e / sd)
            a = _scale(np.mean(parts, axis=0)) if parts else np.zeros(idx.size)
            out[k] = _weights(a, idx, syms, plan.strength)
        elif d["variant"] == "raw":
            a = _ranks(sig[d["signal"]], idx, syms)
            out[k] = TB._tilt(sig[d["signal"]], inp.mask[:, t], syms, plan.strength)  # cohort 0's book, verbatim
        else:
            a = neutral_score(sig[d["signal"]], idx, inp, d["variant"], expo)
            out[k] = _weights(a, idx, syms, plan.strength)
        act = np.array([out[k].get(syms[i], 0) for i in idx], float) / TB.MICRO - 1.0 / idx.size
        expos[k] = {"sector_max": float(max(abs(act[sec == g].sum()) for g in np.unique(sec))),
                    "beta": float(act @ expo[:, 0]), "size": float(act @ expo[:, 1]),
                    "score_sd": float(np.std(a))}
    return out, expos


def _sector_ranks(score: np.ndarray, idx: np.ndarray, inp: Inputs) -> np.ndarray:
    sec, z = inp.sector[idx], np.zeros(idx.size)
    for g in np.unique(sec):
        sel = np.flatnonzero(sec == g)
        z[sel] = _ranks(score, idx[sel], inp.stocks)
    return z


# ---- the paper books ------------------------------------------------------------------------------------------------
def replay(inp: Inputs, targets: dict[int, dict[str, int]], t0: int, t1: int
           ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """tournament_lane.replay's semantics on arrays: daily net returns for sessions t0 + 1 .. t1 (NaN elsewhere), and
    each session's one-way turnover and cost. Starts fully in SPY; the target decided at the close of t trades at the
    close of t + 1; CASH, a missing bar and a quarantined pair earn nothing."""
    n = len(inp.stocks)
    col = {s: j for j, s in enumerate(inp.stocks)} | {BENCHMARK: n, TB.CASH: n + 1}
    cost = np.concatenate([np.full(n, TB.COSTS_BPS["stock_close"]), [TB.COSTS_BPS["spy"], TB.COSTS_BPS["cash"]]]) / 1e4
    r_all = np.column_stack([inp.ret, inp.spy_ret, np.zeros(len(inp.sessions))])

    def vec(m: dict[str, int]) -> np.ndarray:
        v = np.zeros(n + 2)
        for s, w in m.items():
            v[col[s]] = w / TB.MICRO
        return v

    h = np.zeros(n + 2)
    h[n] = 1.0
    out, turnover, paid = (np.full(len(inp.sessions), np.nan) for _ in range(3))
    pending = targets.get(t0)
    for i in range(t0 + 1, t1 + 1):
        r = r_all[i]
        gross = float(h @ r)
        h = h * (1.0 + r) / (1.0 + gross)
        c = tv = 0.0
        if pending is not None:
            new = vec(pending)
            d = np.abs(new - h)
            c, tv = float(cost @ d), 0.5 * float(d.sum())
            h = new
        out[i], turnover[i], paid[i] = (1.0 + gross) * (1.0 - c) - 1.0, tv, c
        pending = targets.get(i)
    return out, turnover, paid


def _grid_up(x: float, grid: tuple[float, ...]) -> float | None:
    return next((g for g in grid if g >= x - 1e-12), None)


def rel_stats(x: np.ndarray, days: pd.DatetimeIndex) -> dict:
    sd = float(np.std(x, ddof=1))
    te = sd * math.sqrt(252)
    i = int(np.argmin(x))
    lw = np.cumsum(np.log1p(x))
    mean = float(np.mean(x)) * 252
    return {"sessions": int(x.size), "tracking_error": te, "worst_session": float(x[i]),
            "worst_session_date": days[i].date().isoformat(), "q0.001": float(np.quantile(x, 0.001)),
            "q0.01": float(np.quantile(x, 0.01)), "best_session": float(np.max(x)),
            "dev_contaminated": {"label": "DEV_CONTAMINATED diagnostic: selects nothing", "mean_annual": mean,
                                 "ir": mean / te if te > 0 else None,
                                 "max_drawdown_log": float(np.max(np.maximum.accumulate(lw) - lw))}}


def _alpha_needed(te: float, g: float, lam_max: float | None) -> float:
    """The drift mu at which the best constant bet (capped at lam_max; None: uncapped) grows log-evidence at g a year,
    for tracking error te: g = mu^2 / (2 te^2) uncapped, else g = lam mu - lam^2 te^2 / 2 at the cap."""
    mu = te * math.sqrt(2 * g)
    if lam_max is None or mu / (te * te) <= lam_max:
        return mu
    return (g + 0.5 * lam_max * lam_max * te * te) / lam_max


def velocity_rows(te: float, extra_cost: float, loss_bound: float | None, plan: DesignStudyPlan) -> dict:
    """How fast evidence against the twin could come, at the measured tracking error and extra cost:
    * the gross active alpha a year (before costs) needed to reach `claim_level` within `target_years`, at the
      information limit (IR^2 / 2, no test does better) and with the bounded test at the proposed loss bound (bets
      capped at 0.9 / L);
    * the years to `claim_level` at each hypothetical gross alpha, net of the extra cost.
    The assumption-free test (bets <= 1) needs a drift of about log(level) / years, far above any tilt's: omitted."""
    g = math.log(plan.claim_level) / plan.target_years
    cap = None if loss_bound is None else 0.9 / loss_bound
    needed = {"information_limit": extra_cost + _alpha_needed(te, g, None),
              "bounded": None if cap is None else extra_cost + _alpha_needed(te, g, cap)}
    years = {}
    for a in plan.hypothetical_gross_alpha:
        mu = a - extra_cost
        ir_rate = 0.5 * (mu / te) ** 2 if mu > 0 else 0.0
        row = {"net_alpha": mu, "information_limit": years_to(plan.claim_level, ir_rate)}
        if cap is not None:
            row["bounded"] = years_to(plan.claim_level, best_bet(mu, te, cap)[1] if mu > 0 else 0.0)
        years[f"{a:g}"] = row
    return {"claim_level": plan.claim_level, "target_years": plan.target_years,
            "gross_alpha_needed": needed, "years_at_gross_alpha": years}


def proposal(designs: dict[str, dict], plan: DesignStudyPlan) -> dict:
    """The registered rule (reads only risk): per signal, the variant with the lowest tracking error against the twin
    at the reference dispersion (ties: the more neutral), plus every extra design; loss bounds from the worst DEV
    session x safety, rounded up on the grid (None: no grid value is large enough, so no bounded test)."""
    chosen = []
    for n, _f, _s, _e in plan.signals:
        tes = {v: designs[f"{n}-{v}"]["vs_twin"]["tracking_error_per_unit_dispersion"] for v in plan.variants}
        chosen.append(f"{n}-{min(reversed(plan.variants), key=lambda v: tes[v])}")
    chosen += [f"{n}-full" for n, _e in plan.extras]
    out = []
    for k in chosen:
        d = designs[k]
        out.append({"design": k, "twin": "ew", "rebalance_every": d["every"], "prior_mass": plan.proposed_mass,
                    "twin_loss_bound": _grid_up(plan.safety * abs(min(d["vs_twin"]["worst_session"], 0.0)),
                                                plan.twin_loss_grid),
                    "loss_bound": _grid_up(plan.safety * abs(min(d["vs_spy"]["worst_session"], 0.0)),
                                           plan.spy_loss_grid)})
    return {"rule": "per signal, the lowest tracking error against the twin at the reference dispersion (ties: the "
            f"more neutral), plus the extras; loss bound = smallest grid value >= {plan.safety} x the worst DEV "
            "session", "candidates": out,
            "prior_mass_total": round(plan.proposed_mass * len(out), 6),
            "status": "PROPOSAL for a later, separately registered cohort; changes no recorded series"}


def _no_symbols(body: dict, symbols: set[str]) -> None:
    """Publication guard: no stock symbol may appear as a key or a string value of the published body."""
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in symbols:
                    raise ValueError("a symbol would be published")
                walk(v)
        elif isinstance(o, list | tuple):
            for v in o:
                walk(v)
        elif isinstance(o, str) and o in symbols:
            raise ValueError("a symbol would be published")
    walk(body)


def run_design_study(plan: DesignStudyPlan, data, out_dir: Path, *, experiment_id: str,
                     progress: Callable[[str], None] = print, code_sha: str | None = None) -> dict:
    """`data`: pipeline.Loaded (pre, b, quarantine, provenance). Writes design_study.json and DESIGN_STUDY.md."""
    out_dir.mkdir(parents=True, exist_ok=True)
    inp = inputs(data.pre, data.quarantine)
    d0, d1 = data.b.dev_range
    t0 = d0 - 1  # the first decision: the close before the DEV window (the agents' convention)
    designs = plan.designs()
    targets: dict[str, dict[int, dict]] = {k: {} for k in ["ew", *designs]}
    exposure_sums: dict[str, dict[str, list[float]]] = {k: {"sector_max": [], "beta": [], "size": [], "score_sd": []}
                                                        for k in designs}
    for t in range(t0, d1):
        tg, ex = decide(inp, t, t - t0, plan, t == t0)
        for k, m in tg.items():
            targets[k][t] = m
        for k, e in ex.items():
            for f, v in e.items():
                exposure_sums[k][f].append(abs(v))
        if (t - t0) % 250 == 0:
            progress(f"decisions through {inp.sessions[t].date()}")
    days = slice(t0 + 2, d1 + 1)  # every evaluated session holds the books' own targets (the first trade is t0 + 1)
    ev_days = inp.sessions[days]
    years = (d1 + 1 - (t0 + 2)) / 252
    rets, books = {}, {}
    for k, tg in targets.items():  # turnover and cost over the evaluated sessions (the move out of SPY excluded)
        r, turn, paid = replay(inp, tg, t0, d1)
        rets[k] = r[days]
        books[k] = {"rebalances": len(tg), "turnover_one_way_per_year": float(np.sum(turn[days])) / years,
                    "cost_per_year": float(np.sum(paid[days])) / years}
    spy = inp.spy_ret[days]
    rows = {}
    for k, d in designs.items():
        x_twin = (1 + rets[k]) / (1 + rets["ew"]) - 1
        x_spy = (1 + rets[k]) / (1 + spy) - 1
        tw, sp = rel_stats(x_twin, ev_days), rel_stats(x_spy, ev_days)
        active = {f: float(np.mean(v)) if v else None for f, v in exposure_sums[k].items()}
        # tracking error at the reference dispersion (a rank's 1/sqrt(3)): a variant cannot win with a smaller tilt
        tw["tracking_error_per_unit_dispersion"] = (tw["tracking_error"] * UNIFORM_SD / active["score_sd"]
                                                    if active["score_sd"] else None)
        lb_twin = _grid_up(plan.safety * abs(min(tw["worst_session"], 0.0)), plan.twin_loss_grid)
        extra = books[k]["cost_per_year"] - books["ew"]["cost_per_year"]
        rows[k] = d | {"vs_twin": tw, "vs_spy": sp, "book": books[k] | {"extra_cost_per_year_vs_twin": extra},
                       "mean_abs_active": active,
                       "velocity_vs_twin": velocity_rows(tw["tracking_error"], extra, lb_twin, plan)}
    twin = {"vs_spy": rel_stats((1 + rets["ew"]) / (1 + spy) - 1, ev_days), "book": books["ew"]}
    cohort0 = {f"{n}-raw": {"declared_twin_loss_bound": c["twin_loss_bound"], "declared_loss_bound": c["loss_bound"],
                            "worst_vs_twin": rows[f"{n}-raw"]["vs_twin"]["worst_session"],
                            "worst_vs_spy": rows[f"{n}-raw"]["vs_spy"]["worst_session"],
                            "twin_bound_held_on_dev": rows[f"{n}-raw"]["vs_twin"]["worst_session"]
                            >= -c["twin_loss_bound"],
                            "spy_bound_held_on_dev": rows[f"{n}-raw"]["vs_spy"]["worst_session"] >= -c["loss_bound"]}
               for n, _f, _s, _e in plan.signals
               for c in TB.CANDIDATES if c["candidate_id"] == f"{n}-tilt"}
    body = {"schema": "apex-discovery-v3-design-study-1", "experiment_id": experiment_id, "plan": plan.to_dict(),
            "plan_sha256": plan.sha256(), "code_sha": code_sha or os.environ.get("GITHUB_SHA"),
            "provenance": data.provenance.__dict__, "research_only": True, "status": "COMPLETED",
            "window": {"first_decision": inp.sessions[t0].date().isoformat(),
                       "evaluated": [ev_days[0].date().isoformat(), ev_days[-1].date().isoformat()],
                       "sessions": int(ev_days.size), "label": "DEV_CONTAMINATED", "tier": "DEV_DIAGNOSTIC"},
            "universe": {"stocks": len(inp.stocks), "mean_masked": float(inp.mask[:, t0:d1].sum(axis=0).mean())},
            "twin": twin, "designs": rows, "cohort0_declared_bounds_on_dev": cohort0,
            "proposal": proposal(rows, plan),
            "publication": "aggregates only: no symbol, price or per-stock value (provider terms)",
            "finished_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    _no_symbols(body, set(inp.stocks))
    (out_dir / "design_study.json").write_text(json.dumps(body, indent=1, sort_keys=True, default=str))
    (out_dir / "DESIGN_STUDY.md").write_text(summary_md(body))
    return {"status": body["status"], "sessions": body["window"]["sessions"], "proposal": body["proposal"]}


def _pct(x: float | None, d: int = 2) -> str:
    return "—" if x is None else f"{100 * x:.{d}f}%"


def _yrs(x: float) -> str:
    return "never" if not math.isfinite(x) else f"{x:.1f}"


def _ir(x: float | None) -> str:
    return "—" if x is None else f"{x:+.2f}"


def summary_md(body: dict) -> str:
    w, plan = body["window"], body["plan"]
    lines = [f"# Design study `{plan['plan_id']}` ({body['experiment_id']})", "",
             f"Status: **{body['status']}**. Sessions {w['evaluated'][0]} → {w['evaluated'][1]} ({w['sessions']}; "
             f"{w['label']}, {w['tier']}). Research only: paper books, no orders.", "",
             "Tracking error and worst session are risk measurements; the means are DEV_CONTAMINATED diagnostics and "
             "select nothing.", "",
             "| Design | every | TE vs twin (per unit dispersion) | worst vs twin | TE vs SPY | worst vs SPY | "
             "turnover/yr | extra cost/yr | gross alpha for 800 in 10 y: limit · bounded | DEV IR vs twin |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for k, r in body["designs"].items():
        tw_, need = r["vs_twin"], r["velocity_vs_twin"]["gross_alpha_needed"]
        lines.append(f"| `{k}` | {r['every']} | {_pct(tw_['tracking_error'])} "
                     f"({_pct(tw_['tracking_error_per_unit_dispersion'])}) | "
                     f"{_pct(tw_['worst_session'])} ({tw_['worst_session_date']}) | "
                     f"{_pct(r['vs_spy']['tracking_error'])} | {_pct(r['vs_spy']['worst_session'])} | "
                     f"{r['book']['turnover_one_way_per_year']:.2f} | "
                     f"{_pct(r['book']['extra_cost_per_year_vs_twin'], 3)} | "
                     f"{_pct(need['information_limit'])} · {_pct(need['bounded'])} | "
                     f"{_ir(tw_['dev_contaminated']['ir'])} |")
    tw = body["twin"]
    lines += ["", f"Twin `ew` vs SPY: TE {_pct(tw['vs_spy']['tracking_error'])}, worst session "
              f"{_pct(tw['vs_spy']['worst_session'])} ({tw['vs_spy']['worst_session_date']}).", "",
              "Cohort 0's declared loss bounds on DEV:"]
    for k, c in body["cohort0_declared_bounds_on_dev"].items():
        lines.append(f"- `{k}`: vs twin worst {_pct(c['worst_vs_twin'])} against L {c['declared_twin_loss_bound']} "
                     f"({'held' if c['twin_bound_held_on_dev'] else 'BROKEN'}); vs SPY worst {_pct(c['worst_vs_spy'])} "
                     f"against L {c['declared_loss_bound']} ({'held' if c['spy_bound_held_on_dev'] else 'BROKEN'})")
    p = body["proposal"]
    lines += ["", f"**Proposal** ({p['status']}): {p['rule']}.", "",
              "| Candidate | every | twin loss bound | loss bound vs SPY | prior mass |", "|---|---|---|---|---|"]
    for c in p["candidates"]:
        lines.append(f"| `{c['design']}` | {c['rebalance_every']} | {c['twin_loss_bound']} | {c['loss_bound']} | "
                     f"{c['prior_mass']} |")
    return "\n".join(lines) + "\n"
