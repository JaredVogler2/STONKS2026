"""Level and selection diagnostics study (SYNTHETIC market, development window only).

    uv run python bench/level_study.py [--out DIR] [--strategies h1_vcp_breakout,...] [--quick]

Per strategy with development-window entries it measures:
  1. excursions: MAE / MFE in ATR and R, the stop-too-tight and target-too-close signals, time to exit;
  2. entry: fill rate, outcome by where in the zone the fill happened, gap-through-stop rate, and a next-open market
     entry variant;
  3. a level grid: the SAME setups re-scored with the SAME execution model under stop x target-R x horizon variants,
     with a multiple-testing deflation of the best configuration;
  4. selection: the Quality score, its evidence-group sub-scores and the development out-of-fold model probability
     against realised outcomes;
  5. breakdowns by regime, sector and year (cells with n < 20 suppressed);
  6. the same grid for the pipeline's matched baselines (random_qualified, momentum_20, breakout_basic).

HOLDOUT DISCIPLINE. Every diagnostic uses only decision dates strictly before the holdout start that
`research.pipeline.run_training` would use for that strategy: `ValidationConfig().holdout_fraction` with the split rule
of `validation.walk_forward`, asserted equal to walk_forward's own `holdout_start` on every run. The split needs the
decision dates of ALL filled events, so build_events runs over the full history exactly as in the pipeline; beyond those
dates the study never reads a holdout row. Before the production walk_forward is called (for the development
out-of-fold predictions), holdout rows are reduced to their decision dates: labels, returns and features are blanked.
Baselines are built from a development-only reference and a development-only date window (the production generator
yields the same development rows that way), ending at the EARLIEST strategy cutoff.

SYNTHETIC ONLY. The data is demo/synthetic.py's deterministic, fictional market, which plants a known edge. Nothing here
is evidence about real markets. Production code and defaults are untouched. Where a production function lacks a knob,
the knob is wrapped (WRAP) or the minimal calculation copied (COPY) here, with a comment.
"""

from __future__ import annotations

import argparse
import functools
import inspect
import json
import math
import sys
import time
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

from apex_worker.research import metrics as M
from apex_worker.research.dataset import MODEL_FEATURES, DatasetSpec, baseline_events, build_events, data_hash
from apex_worker.research.evaluate import net_at_cost, selection_summary
from apex_worker.research.execution import ExecConfig, simulate
from apex_worker.research.pipeline import _load_synthetic, run_training
from apex_worker.research.validation import ValidationConfig, embargo_limit, walk_forward
from apex_worker.signals.context import context_frame
from apex_worker.signals.levels import LevelConfig, Levels, compute_levels
from apex_worker.signals.model_features import assemble
from apex_worker.signals.policy import PolicyConfig
from apex_worker.signals.strategies.library import STRATEGIES

STUDY_VERSION = "level-study-1"
SYNTHETIC_LABEL = (
    "SYNTHETIC FIXTURE DATA: diagnostics of the method on a fictional, generator-planted market; "
    "NOT evidence of a real edge"
)
STUDY_SEED = 20261021  # sign-flip null and Spearman bootstraps (the p10 bootstrap keeps metrics.py's own seed)
# smoke-test universe: the generator's minimum (8/sector) restricted to 4 plain stocks per sector + benchmarks + ETFs
QUICK_UNIVERSE = {"stocks_per_sector": 8, "start": date(2022, 1, 3), "keep_stocks_per_sector": 4}

PROD_LEVELS = LevelConfig()
PROD_EXEC = ExecConfig()
STRUCTURAL = "structural"
STOP_MODES: tuple = (STRUCTURAL, 1.0, 1.5, 2.0, 2.5, 3.0)
TARGET_RS = (1.5, 2.0, 2.5, 3.0)
HORIZONS = (10, 15, 20)
QUICK_GRID = ((STRUCTURAL, 1.5, 2.5), (2.0, 3.0), (10, 20))
PRODUCTION = (STRUCTURAL, PROD_LEVELS.target_r_multiple, PROD_EXEC.horizon_sessions)
EVAL_BPS = 25.0  # PromotionPolicy.eval_cost_bps
MIN_CELL = 20
QS = (0.1, 0.25, 0.5, 0.75, 0.9)
TIME_OUTCOMES = ("TIME", "DATA_END")
SUBSCORES = ("setup_score", "g_price", "g_sector", "g_volume", "g_volatility", "g_regime", "g_catalyst")
QUALITY_BUCKETS = ((-math.inf, 50.0, "<50"), (50.0, 60.0, "50-60"), (60.0, 70.0, "60-70"), (70.0, 80.0, "70-80"),
                   (80.0, math.inf, "80+"))
SUBSCORE_BUCKETS = ((-math.inf, -0.5, "<-0.5"), (-0.5, 0.0, "-0.5-0"), (0.0, 0.5, "0-0.5"), (0.5, math.inf, ">=0.5"))
SLIP = PROD_EXEC.cost_bps_per_side / 1e4
ND = NormalDist()


def log(msg: str) -> None:
    print(f"[level-study] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------------------------------------------------
# data: exactly the pipeline's synthetic loader and event builder
# ---------------------------------------------------------------------------------------------------------------------
def pipeline_universe() -> dict:
    """run_training's own synthetic defaults (read from its signature so the study follows the pipeline)."""
    p = inspect.signature(run_training).parameters
    return {"stocks_per_sector": p["stocks_per_sector"].default, "start": p["start"].default,
            "keep_stocks_per_sector": None}


@functools.lru_cache(maxsize=2)
def load_market(stocks_per_sector: int, start: date, keep_stocks_per_sector: int | None = None) -> tuple:
    """The pipeline's synthetic loader. With `keep_stocks_per_sector` (--quick only): COPY of pipeline._load_synthetic
    (it has no symbol knob) passing the generator a subset: benchmarks, sector ETFs and the first N plain stocks per
    sector. Per-symbol RNG streams keep those symbols' bars identical to the full universe's (cross-sectional features
    differ); run_training can consume the same tuple through its `loader` argument."""
    if keep_stocks_per_sector is None:
        return _load_synthetic(stocks_per_sector, start)
    from apex_worker.demo.synthetic import generate, universe_specs
    from apex_worker.features.panel import panel_from_synthetic

    keep, per_sector = [], {}
    for sp in universe_specs(stocks_per_sector):
        if sp.tags:  # special-case fixtures (penny, OTC, warrant, leveraged, delisted, ...)
            continue
        if sp.asset_kind == "stock":
            per_sector[sp.sector] = per_sector.get(sp.sector, 0) + 1
            if per_sector[sp.sector] > keep_stocks_per_sector:
                continue
        keep.append(sp.symbol)
    market = generate(stocks_per_sector=stocks_per_sector, start=start, symbols=keep)
    panel = panel_from_synthetic(market)
    bars = {s: market.adjusted(s) for s in market.bars}
    return panel, bars, market.earnings, [], "synthetic_fixture"


def pipeline_events(panel, bars, earnings, cats, ids: list[str]) -> tuple[pd.DataFrame, DatasetSpec]:
    spec = DatasetSpec(strategies=[STRATEGIES[s] for s in ids if s in STRATEGIES])  # as run_training builds it
    return build_events(panel, bars, spec, catalysts=cats, earnings=earnings), spec


@functools.lru_cache(maxsize=2)
def load_inputs(stocks_per_sector: int, start: date, keep_stocks_per_sector: int | None, ids: tuple[str, ...]) -> tuple:
    """(panel, bars, earnings, catalysts, data_source, events, spec), cached so an in-process caller (the smoke test)
    can re-check the study against the very same production build_events output. Nothing downstream mutates it."""
    panel, bars, earnings, cats, ds = load_market(stocks_per_sector, start, keep_stocks_per_sector)
    events, spec = pipeline_events(panel, bars, earnings, cats, list(ids))
    return panel, bars, earnings, cats, ds, events, spec


def holdout_cutoff(ev_s: pd.DataFrame, vcfg: ValidationConfig) -> pd.Timestamp | None:
    """COPY of the split rule inside validation.walk_forward (it has no standalone function): the first decision date
    of the last `holdout_fraction` of the strategy's FILLED decision dates. dev_walk_forward asserts that walk_forward
    itself returns the same date, so the copy cannot drift from production silently."""
    ev = ev_s[ev_s["filled"]].sort_values("decision_date")
    dates = np.sort(ev["decision_date"].unique())
    if len(dates) < 10:  # walk_forward returns no split below 10 dates
        return None
    return pd.Timestamp(dates[int(len(dates) * (1 - vcfg.holdout_fraction))])


def dev_walk_forward(ev_s: pd.DataFrame, cutoff: pd.Timestamp, vcfg: ValidationConfig):
    """Production walk_forward on the strategy's events with every holdout row reduced to its decision date.

    Development folds and their out-of-fold predictions depend only on development rows, so they are identical to the
    pipeline's; the split itself only needs the holdout decision dates. walk_forward still fits a final model and scores
    the (now label-free, feature-free) holdout rows: those outputs are meaningless here and discarded unread."""
    masked = ev_s.copy()
    late = masked["decision_date"] >= cutoff
    blank = [c for c in (*MODEL_FEATURES, "return_net", "return_gross", "r_multiple", "mfe", "mae", "sessions_held",
                         "stop", "target", "entry_min", "entry_max") if c in masked]
    masked[blank] = masked[blank].astype(float)
    masked.loc[late, blank] = np.nan
    masked.loc[late, "outcome"] = "TIME"
    masked.loc[late, ["entry_date", "exit_date"]] = pd.NaT
    wf = walk_forward(masked, MODEL_FEATURES, vcfg)
    if wf.holdout_start != cutoff:
        raise AssertionError(f"split drift: walk_forward holdout_start {wf.holdout_start} != study cutoff {cutoff}")
    oos = wf.oos
    if not oos.empty and not bool((oos["decision_date"] < cutoff).all()):
        raise AssertionError("out-of-fold rows outside the development window")
    return oos, wf.folds, wf.purged_count


# ---------------------------------------------------------------------------------------------------------------------
# setups: the inputs compute_levels saw for each development event
# ---------------------------------------------------------------------------------------------------------------------
class Contexts:
    """Per-symbol production context frames and bars aligned exactly as dataset.build_events aligns them.

    Strategies get context_frame(panel, symbol, catalysts) as in build_events. Baselines reuse it: catalysts only add
    cat_* columns, and baselines read only close / atr_14 / resistance."""

    def __init__(self, panel, bars, cats):
        self.panel, self.bars, self.cats = panel, bars, list(cats)
        self._cache: dict[str, tuple] = {}

    def get(self, symbol: str) -> tuple[pd.DataFrame, tuple, np.ndarray]:
        if symbol not in self._cache:
            ctx = context_frame(self.panel, symbol, self.cats)
            b = self.bars[symbol].reindex(pd.DatetimeIndex(ctx.index))
            ohlc = tuple(b[k].to_numpy(float) for k in ("open", "high", "low", "close"))
            # COPY of build_events / baseline_events: overhead supply = prior 120-session closing high
            res = ctx["close"].rolling(120, min_periods=60).max().shift(1).to_numpy(float)
            self._cache[symbol] = (ctx, ohlc, res)
        return self._cache[symbol]


@dataclass
class Book:
    """The setups one study arm re-scores: one row per development setup + bars per symbol."""

    name: str
    cutoff: pd.Timestamp
    rows: pd.DataFrame  # symbol, decision_date, t, close, atr, hint, resistance (+ strategy metadata)
    bars: dict[str, tuple]


META_COLS = ("symbol", "sector", "decision_date", "decision_index", "regime", "regime_allowed", "quality", *SUBSCORES)


def strategy_book(sid: str, dev: pd.DataFrame, ctxs: Contexts, cutoff: pd.Timestamp) -> Book:
    strat = STRATEGIES[sid]
    parts = []
    for sym, g in dev.groupby("symbol", sort=False):
        ctx, _, res = ctxs.get(sym)
        hint = strat.trigger_frame(ctx)["invalidation_hint"].to_numpy(float)
        t = g["decision_index"].to_numpy(int)
        parts.append(pd.DataFrame({"t": t, "close": ctx["close"].to_numpy(float)[t], "atr": ctx["atr_14"].to_numpy(float)[t],
                                   "hint": hint[t], "resistance": res[t]}, index=g.index))
    inputs = pd.concat(parts).loc[dev.index]
    rows = dev[[c for c in META_COLS if c in dev]].join(inputs)
    return Book(sid, cutoff, rows, {s: ctxs.get(s)[1] for s in rows["symbol"].unique()})


def baseline_book(name: str, dev: pd.DataFrame, ctxs: Contexts, cutoff: pd.Timestamp) -> Book:
    parts = []
    for sym, g in dev.groupby("symbol", sort=False):
        ctx, _, res = ctxs.get(sym)
        t = ctx.index.get_indexer(pd.DatetimeIndex(g["decision_date"]))
        close, atr = ctx["close"].to_numpy(float)[t], ctx["atr_14"].to_numpy(float)[t]
        parts.append(pd.DataFrame({"t": t, "close": close, "atr": atr,
                                   "hint": close - 1.5 * atr,  # COPY of baseline_events' generic invalidation
                                   "resistance": res[t]}, index=g.index))
    inputs = pd.concat(parts).loc[dev.index]
    rows = dev[["symbol", "decision_date"]].join(inputs)
    return Book(name, cutoff, rows, {s: ctxs.get(s)[1] for s in rows["symbol"].unique()})


# ---------------------------------------------------------------------------------------------------------------------
# the level grid: production compute_levels + simulate under different knobs
# ---------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class GridCfg:
    stop: str | float
    target_r: float
    horizon: int

    @property
    def label(self) -> str:
        s = STRUCTURAL if self.stop == STRUCTURAL else f"{float(self.stop):g}ATR"
        return f"stop={s} R={self.target_r:g} H={self.horizon}"

    @property
    def is_production(self) -> bool:
        return (self.stop, float(self.target_r), int(self.horizon)) == PRODUCTION


def grid_configs(quick: bool) -> list[GridCfg]:
    stops, rs, hs = QUICK_GRID if quick else (STOP_MODES, TARGET_RS, HORIZONS)
    cfgs = [GridCfg(s, float(r), int(h)) for s in stops for r in rs for h in hs]
    if sum(c.is_production for c in cfgs) != 1:
        raise AssertionError("the production configuration must be in the grid exactly once")
    return cfgs


def level_config(stop: str | float, target_r: float) -> LevelConfig:
    """WRAP of compute_levels' own knobs. A fixed ATR stop pins both clamp ends to k (stop = mid - k ATR); the structural
    mode keeps the production clamp [1, 3] ATR around the strategy's invalidation hint. min_net_rr is disabled so every
    variant re-scores the SAME setups (the production 1.5 net-R/R filter would delete every 1.5R-target setup); how many
    variant levels would fail it is reported per configuration. The resistance cap applies in every configuration."""
    cfg = replace(PROD_LEVELS, target_r_multiple=float(target_r), min_net_rr=-math.inf)
    if stop != STRUCTURAL:
        cfg = replace(cfg, stop_min_atr=float(stop), stop_max_atr=float(stop))
    return cfg


SIM_COLS = ("rejected", "entry_min", "entry_max", "stop", "target", "capped", "rr_net", "stop_dist_atr", "filled",
            "outcome", "entry_index", "exit_index", "entry_price", "exit_price", "return_gross", "return_net",
            "r_multiple", "sessions_held", "gap_stop", "ambiguous")


def _stop_dist_atr(lv: Levels, atr: float) -> float | None:
    """The model's stop_dist_atr exactly as the shared training/live assembler (signals.model_features) defines it."""
    return assemble({"atr_14": atr}, None, None, {}, lv, False)["stop_dist_atr"]


def _record(lv: Levels | None, code: str | None, res, atr: float = math.nan) -> tuple:
    if lv is None:
        return (code, np.nan, np.nan, np.nan, np.nan, False, np.nan, np.nan, False, "LEVELS_REJECTED", None, None,
                np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, False, False)
    return (None, lv.entry_min, lv.entry_max, lv.stop, lv.target, lv.target_secondary is not None, lv.reward_to_risk_net,
            _stop_dist_atr(lv, atr), res.filled, res.outcome, res.entry_index, res.exit_index, res.entry_price,
            res.exit_price, res.return_gross, res.return_net, res.r_multiple, res.sessions_held, res.gap_through_stop,
            res.ambiguous_same_bar)


def _frame(records: list[tuple], index: pd.Index) -> pd.DataFrame:
    df = pd.DataFrame(records, columns=list(SIM_COLS), index=index)
    for c in ("filled", "capped", "gap_stop", "ambiguous"):
        df[c] = df[c].astype(bool)
    for c in ("entry_min", "entry_max", "stop", "target", "rr_net", "stop_dist_atr", "entry_price", "exit_price",
              "return_gross", "return_net", "r_multiple", "sessions_held"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


def run_grid(book: Book, configs: list[GridCfg]) -> dict[GridCfg, pd.DataFrame]:
    level_keys = list(dict.fromkeys((c.stop, c.target_r) for c in configs))
    lcfg = {k: level_config(*k) for k in level_keys}
    by_level = {k: [c for c in configs if (c.stop, c.target_r) == k] for k in level_keys}
    ecfg = {h: replace(PROD_EXEC, horizon_sessions=h) for h in {c.horizon for c in configs}}
    out: dict[GridCfg, list[tuple]] = {c: [] for c in configs}
    r = book.rows
    for sym, t, close, atr, hint, res in zip(r["symbol"], r["t"], r["close"], r["atr"], r["hint"], r["resistance"],
                                             strict=True):
        o, hi, lo, cl = book.bars[sym]
        for k in level_keys:
            lv = compute_levels(float(close), float(atr), hint, res, lcfg[k])
            for c in by_level[k]:
                if not isinstance(lv, Levels):
                    out[c].append(_record(None, lv.code, None))
                    continue
                sim = simulate(o, hi, lo, cl, int(t), lv.entry_min, lv.entry_max, lv.stop, lv.target, ecfg[c.horizon])
                out[c].append(_record(lv, None, sim, float(atr)))
    return {c: _frame(v, r.index) for c, v in out.items()}


# ---------------------------------------------------------------------------------------------------------------------
# small statistics helpers
# ---------------------------------------------------------------------------------------------------------------------
def weeks_of(dates) -> np.ndarray:
    """Decision-week groups, exactly as evaluate.selection_summary builds them for the block bootstrap."""
    return pd.to_datetime(pd.Series(dates)).dt.to_period("W").astype(str).to_numpy()


def r_at_cost(f: pd.DataFrame, bps: float) -> pd.Series:
    """COPY of execution.simulate's R definition, re-priced at another per-side cost. In the execution model fills and
    exits never depend on the cost, so only the slippage applied to the raw fill / exit prices changes."""
    raw_fill = f["entry_price"] / (1 + SLIP)
    raw_exit = f["exit_price"] / (1 - SLIP)
    c = bps / 1e4
    fill, exit_ = raw_fill * (1 + c), raw_exit * (1 - c)
    risk = fill - f["stop"]
    return ((exit_ - fill) / risk).where(risk > 0)


def _f(x, nd: int = 6):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, nd) if math.isfinite(x) else None


def _quantiles(values, nd: int = 4) -> dict:
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    if not len(v):
        return {"n": 0}
    return {"n": len(v), **{f"p{int(q * 100)}": round(float(np.quantile(v, q)), nd) for q in QS}}


def _rates(f: pd.DataFrame) -> dict:
    o = f["outcome"]
    return {"target_first_rate": _f((o == "TARGET").mean(), 4), "stop_first_rate": _f((o == "STOP").mean(), 4),
            "time_exit_rate": _f(o.isin(TIME_OUTCOMES).mean(), 4)}


def perf(f: pd.DataFrame, ci: bool = False, n_boot: int = 2000) -> dict:
    """Outcome rates, mean net R and mean net return at 10 / 25 bps per side for filled trades."""
    if f.empty:
        return {"n": 0}
    gross = f["return_gross"].astype(float)
    out = {"n": len(f), **_rates(f),
           "mean_net_r_10": _f(f["r_multiple"].astype(float).mean(), 4),
           "mean_net_r_25": _f(r_at_cost(f, EVAL_BPS).mean(), 4) if "entry_price" in f else None,
           "mean_net_return_10": _f((f["return_net"].astype(float) if "return_net" in f
                                     else net_at_cost(gross, PROD_EXEC.cost_bps_per_side)).mean()),
           "mean_net_return_25": _f(net_at_cost(gross, EVAL_BPS).mean())}
    if ci:
        boot = M.block_bootstrap_mean(net_at_cost(gross, EVAL_BPS).to_numpy(float), weeks_of(f["decision_date"]),
                                      n_boot=n_boot)
        out["p10_mean_net_return_25"] = boot["p10"]
        out["ci_mean_net_return_25"] = boot
    return out


def _rank(a: np.ndarray) -> np.ndarray:
    """Average ranks (ties share their mean rank), numpy-only so the bench needs no optional dependency."""
    order = np.argsort(a, kind="mergesort")
    _, inv, counts = np.unique(a[order], return_inverse=True, return_counts=True)
    ends = np.cumsum(counts)
    avg = (ends - counts + ends - 1) / 2.0
    ranks = np.empty(len(a))
    ranks[order] = avg[inv]
    return ranks


def spearman(x, y) -> float:
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 3:
        return float("nan")
    rx, ry = _rank(x), _rank(y)
    if rx.std() == 0 or ry.std() == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def spearman_boot(x: np.ndarray, y: np.ndarray, weeks: np.ndarray, draws: int, seed: int) -> dict:
    """Weekly block bootstrap of Spearman's rho (decision weeks resampled whole, as in metrics.block_bootstrap_mean)."""
    _, inv = np.unique(weeks, return_inverse=True)
    groups = [np.flatnonzero(inv == g) for g in range(inv.max() + 1)]
    rng = np.random.default_rng(seed)
    vals = np.array([spearman(x[idx], y[idx]) for idx in
                     (np.concatenate([groups[j] for j in rng.integers(0, len(groups), len(groups))]) for _ in range(draws))])
    vals = vals[np.isfinite(vals)]
    if not len(vals):
        return {}
    return {"p5": round(float(np.quantile(vals, 0.05)), 4), "p50": round(float(np.quantile(vals, 0.5)), 4),
            "p95": round(float(np.quantile(vals, 0.95)), 4)}


def sign_flip_max(cols: dict[str, tuple[np.ndarray, np.ndarray]], draws: int, seed: int) -> dict:
    """Reality check for the best of K configurations under a sign-randomised null.

    H0: no configuration has an edge, i.e. each decision week's net P&L is symmetric around zero. One random sign per
    week is applied to EVERY configuration at once (keeping their strong cross-correlation), and the max over the K
    configuration means is recomputed. p = P(null max >= observed max)."""
    cols = {k: v for k, v in cols.items() if len(v[0])}
    if not cols:
        return {}
    labels = sorted(set().union(*(set(w) for _, w in cols.values())))
    pos = {w: i for i, w in enumerate(labels)}
    keys = list(cols)
    s = np.zeros((len(labels), len(keys)))
    cnt = np.zeros_like(s)
    for k, key in enumerate(keys):
        v, w = cols[key]
        idx = np.fromiter((pos[x] for x in w), int, len(w))
        np.add.at(s[:, k], idx, v)
        np.add.at(cnt[:, k], idx, 1.0)
    n = cnt.sum(axis=0)
    obs = s.sum(axis=0) / n
    rng = np.random.default_rng(seed)
    signs = rng.choice((-1.0, 1.0), size=(draws, len(labels)))
    null_max = ((signs @ s) / n).max(axis=1)
    best = int(np.argmax(obs))
    return {"configs": len(keys), "weeks": len(labels), "draws": draws, "best": keys[best],
            "observed_max": _f(obs[best]), "p_value": _f((1 + int((null_max >= obs[best]).sum())) / (1 + draws), 4),
            "null_max_p50": _f(np.quantile(null_max, 0.5)), "null_max_p95": _f(np.quantile(null_max, 0.95))}


def bonferroni(mean: float | None, ci: dict, k: int) -> dict:
    """Bonferroni-style z deflation: z from the weekly block-bootstrap SE (p5..p95 width / 2 x 1.645), one-sided raw p,
    p x K, back to a z."""
    if mean is None or not ci or ci.get("p95") is None or ci.get("p5") is None:
        return {}
    se = (ci["p95"] - ci["p5"]) / (2 * ND.inv_cdf(0.95))
    if se <= 0:
        return {}
    z = mean / se
    p_raw = 1 - ND.cdf(z)
    p_adj = min(1.0, k * p_raw)
    z_adj = None if p_adj >= 1 else ND.inv_cdf(1 - max(p_adj, 1e-15))
    return {"se": _f(se), "z": _f(z, 3), "p_raw_one_sided": _f(p_raw, 5), "k": k, "p_bonferroni": _f(p_adj, 5),
            "z_deflated": _f(z_adj, 3), "z_required_5pct": _f(ND.inv_cdf(1 - 0.05 / k), 3)}


# ---------------------------------------------------------------------------------------------------------------------
# section 3: grid report
# ---------------------------------------------------------------------------------------------------------------------
def config_summary(cfg: GridCfg, sim: pd.DataFrame, rows: pd.DataFrame, n_boot: int) -> dict:
    s = sim.join(rows[["decision_date", "atr"]])
    ok = s[s["rejected"].isna()]
    f = s[s["filled"]]
    mid = (ok["entry_min"] + ok["entry_max"]) / 2
    out = {"config": cfg.label, "stop": cfg.stop, "target_r": cfg.target_r, "horizon": cfg.horizon,
           "is_production": cfg.is_production, "setups": len(s), "levels_rejected": len(s) - len(ok),
           "entries": len(f), "fill_rate": _f(len(f) / len(ok), 4) if len(ok) else None,
           "stop_dist_atr_p50": _f(ok["stop_dist_atr"].median(), 3),
           "target_dist_atr_p50": _f(((ok["target"] - mid) / ok["atr"]).median(), 3),
           "target_capped_share": _f(ok["capped"].mean(), 4) if len(ok) else None,
           "fails_min_net_rr_share": _f((ok["rr_net"] < PROD_LEVELS.min_net_rr).mean(), 4) if len(ok) else None}
    p = perf(f, ci=True, n_boot=n_boot)
    p.pop("n", None)
    out.update(p)
    out["bootstrap_draws"] = n_boot
    if len(f):
        out.update({"mean_sessions_held": _f(f["sessions_held"].mean(), 2), "gap_stop_rate": _f(f["gap_stop"].mean(), 4),
                    "ambiguous_rate": _f(f["ambiguous"].mean(), 4),
                    "decision_first": f["decision_date"].min(), "decision_last": f["decision_date"].max()})
    return out


def grid_report(book: Book, sims: dict[GridCfg, pd.DataFrame], draws: int, other_boot: int = 2000) -> dict:
    """`other_boot`: p10 bootstrap draws for non-production rows (2000 = metrics.py default; --quick uses fewer)."""
    rows = [config_summary(c, s, book.rows, 2000 if c.is_production else other_boot) for c, s in sims.items()]
    weeks = weeks_of(book.rows["decision_date"])
    cols, diffs = {}, {}
    prod_cfg = next(c for c in sims if c.is_production)
    prod = sims[prod_cfg]
    prod_r = net_at_cost(prod["return_gross"], EVAL_BPS)
    for c, s in sims.items():
        m = s["filled"].to_numpy()
        cols[c.label] = (net_at_cost(s["return_gross"], EVAL_BPS).to_numpy(float)[m], weeks[m])
        if not c.is_production:
            both = m & prod["filled"].to_numpy()
            d = (net_at_cost(s["return_gross"], EVAL_BPS) - prod_r).to_numpy(float)[both]
            diffs[c.label] = (d, weeks[both])
    ranked = [r for r in rows if r.get("mean_net_return_25") is not None]
    best = max(ranked, key=lambda r: r["mean_net_return_25"]) if ranked else None
    production = next(r for r in rows if r["is_production"])
    k = len(rows)
    deflation = {
        "n_configs_tested": k,
        "selection_rule": "best = highest mean net return per trade at 25 bps/side over all tested configurations",
        "note": (f"the best of {k} configurations is optimistically biased (selected on the same data it is scored on); "
                 "Bonferroni ignores the strong correlation between configurations (conservative); the sign-flip "
                 "reality check keeps it"),
        "best_vs_zero": {"bonferroni": bonferroni(best["mean_net_return_25"], best.get("ci_mean_net_return_25") or {}, k)
                         if best else {},
                         "reality_check": sign_flip_max(cols, draws, STUDY_SEED)},
        "best_vs_production": {"reality_check": sign_flip_max(diffs, draws, STUDY_SEED + 1),
                               "null": "no configuration improves on production (paired weekly differences symmetric)"},
        "production_single_test": bonferroni(production.get("mean_net_return_25"),
                                             production.get("ci_mean_net_return_25") or {}, 1),
    }
    return {"n_configs": k, "production": production, "best": best, "deflation": deflation, "rows": rows}


# ---------------------------------------------------------------------------------------------------------------------
# section 1: excursions
# ---------------------------------------------------------------------------------------------------------------------
def trade_paths(book: Book, sim: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Per filled trade: in-trade MFE / MAE (fill bar through exit bar, as execution.simulate measures them), the
    full-horizon high (fill + horizon sessions, ignoring the exit) and the post-exit high. Measured from the RAW fill
    price; R = raw fill - stop (the actual risk taken), ATR = ATR(14) at the decision."""
    f = sim[sim["filled"]].join(book.rows[["symbol", "t", "atr", "decision_date"]])
    recs = []
    for sym, fill_i, exit_i, ep, stop, target, atr in zip(f["symbol"], f["entry_index"], f["exit_index"],
                                                          f["entry_price"], f["stop"], f["target"], f["atr"], strict=True):
        _o, hi, lo, _c = book.bars[sym]
        fi, xi = int(fill_i), int(exit_i)
        end = min(len(hi) - 1, fi + horizon)
        raw = ep / (1 + SLIP)
        risk = raw - stop
        hh, ll = np.nanmax(hi[fi:xi + 1]), np.nanmin(lo[fi:xi + 1])
        full = np.nanmax(hi[fi:end + 1])
        post_seg = hi[xi + 1:end + 1]
        post = np.nanmax(post_seg) if np.isfinite(post_seg).any() else np.nan
        recs.append(((hh - raw) / atr, (ll - raw) / atr, (hh - raw) / risk, (ll - raw) / risk, (full - raw) / risk,
                     (full - target) / risk, (full - target) / atr, post >= target, (post - raw) / risk))
    cols = ["mfe_atr", "mae_atr", "mfe_r", "mae_r", "mfe_r_full_horizon", "beyond_target_r", "beyond_target_atr",
            "post_exit_reached_target", "post_exit_mfe_r"]
    paths = pd.DataFrame(recs, columns=cols, index=f.index)
    return f.join(paths)


def _outcome_group(o: pd.Series) -> pd.Series:
    return o.where(~o.isin(TIME_OUTCOMES), "TIME")


def excursions(book: Book, sim: pd.DataFrame, horizon: int) -> dict:
    p = trade_paths(book, sim, horizon)
    if p.empty:
        return {"n": 0}
    grp = _outcome_group(p["outcome"])
    metrics = ("mfe_atr", "mae_atr", "mfe_r", "mae_r", "mfe_r_full_horizon")
    stops, targets = p[grp == "STOP"], p[grp == "TARGET"]
    post_ok = stops["post_exit_mfe_r"].notna()
    return {
        "n": len(p),
        "definition": "in-trade = fill bar through exit bar (as execution.simulate); full horizon = fill + horizon "
                      "sessions ignoring the exit; from the raw fill price; R = raw fill - stop; ATR(14) at decision",
        "all": {m: _quantiles(p[m]) for m in metrics},
        "by_outcome": {g: {"n": int((grp == g).sum()), **{m: _quantiles(p.loc[grp == g, m]) for m in metrics}}
                       for g in ("TARGET", "STOP", "TIME")},
        "stop_too_tight": {
            "stopped_trades": len(stops),
            "later_reached_target": int(stops["post_exit_reached_target"].sum()),
            "share": _f(stops["post_exit_reached_target"].mean(), 4) if len(stops) else None,
            "later_reached_plus_1r_share": _f((stops.loc[post_ok, "post_exit_mfe_r"] >= 1).mean(), 4)
            if post_ok.any() else None,
            "same_bar_ambiguous_stops": int(stops["ambiguous"].sum()),
            "definition": "stopped trades whose high on a LATER session within fill + horizon reached the original target",
        },
        "target_too_close": {
            "target_trades": len(targets),
            "beyond_target_r": _quantiles(targets["beyond_target_r"]),
            "beyond_target_atr": _quantiles(targets["beyond_target_atr"]),
            "share_beyond_1r": _f((targets["beyond_target_r"] >= 1).mean(), 4) if len(targets) else None,
            "definition": "max high over fill + horizon sessions minus the target, for trades that hit the target",
        },
        "time_to_exit": {
            "sessions_held": {g: _quantiles(p.loc[grp == g, "sessions_held"], 1) for g in ("TARGET", "STOP", "TIME")},
            "histogram": M.time_to_exit_histogram(p),
        },
        "time_exits": {"final_r": _quantiles(p.loc[grp == "TIME", "r_multiple"]),
                       "mfe_r": _quantiles(p.loc[grp == "TIME", "mfe_r"])},
    }


# ---------------------------------------------------------------------------------------------------------------------
# section 2: entry diagnostics
# ---------------------------------------------------------------------------------------------------------------------
def _fill_location(row_o: float, entry_min: float, entry_max: float) -> str:
    if not (entry_min <= row_o <= entry_max):
        return "limit touch (opened above zone)"
    pos = (row_o - entry_min) / (entry_max - entry_min) if entry_max > entry_min else 0.5
    return "open in lower third" if pos < 1 / 3 else ("open in middle third" if pos < 2 / 3 else "open in upper third")


def entry_diagnostics(book: Book, sim: pd.DataFrame) -> dict:
    s = sim[sim["rejected"].isna()].join(book.rows[["symbol", "t", "atr", "decision_date"]])
    f = s[s["filled"]].copy()
    loc, day, vs_mid = [], [], []
    for sym, fi, t, emin, emax, ep, atr in zip(f["symbol"], f["entry_index"], f["t"], f["entry_min"], f["entry_max"],
                                               f["entry_price"], f["atr"], strict=True):
        o = book.bars[sym][0]
        loc.append(_fill_location(o[int(fi)], emin, emax))
        day.append(int(fi) - int(t))
        vs_mid.append((ep / (1 + SLIP) - (emin + emax) / 2) / atr)
    f["fill_location"], f["fill_session"], f["fill_vs_mid_atr"] = loc, day, vs_mid
    reasons: dict[str, int] = {}
    unfilled = s[~s["filled"]]
    for sym, t, emin in zip(unfilled["symbol"], unfilled["t"], unfilled["entry_min"], strict=True):
        o, hi, lo, _c = book.bars[sym]
        days = range(int(t) + 1, min(len(o), int(t) + 1 + PROD_EXEC.entry_valid_sessions))
        kinds = set()
        for d in days:
            if any(math.isnan(x) for x in (o[d], hi[d], lo[d])):
                kinds.add("missing bar")
            elif o[d] < emin:
                kinds.add("opened below zone")
            else:
                kinds.add("opened above zone, never traded back to entry_max")
        key = " + ".join(sorted(kinds)) or "end of data"
        reasons[key] = reasons.get(key, 0) + 1
    by_loc = [{"fill_location": k, "share": _f(len(g) / len(f), 4), **perf(g)} for k, g in f.groupby("fill_location")]
    by_day = [{"fill_session_after_decision": int(k), "share": _f(len(g) / len(f), 4), **perf(g)}
              for k, g in f.groupby("fill_session")]
    stops = f[f["outcome"] == "STOP"]
    gaps = stops[stops["gap_stop"]]
    return {
        "setups": len(s), "entries": len(f), "fill_rate": _f(len(f) / len(s), 4) if len(s) else None,
        "unfilled_reasons": reasons,
        "fill_vs_zone_mid_atr": _quantiles(f["fill_vs_mid_atr"], 3),
        "by_fill_location": by_loc,
        "by_fill_session": by_day,
        "gap_through_stop": {
            "rate_of_entries": _f(f["gap_stop"].mean(), 4) if len(f) else None,
            "share_of_stops": _f(len(gaps) / len(stops), 4) if len(stops) else None,
            "mean_r_gap_stops": _f(gaps["r_multiple"].mean(), 4),
            "mean_r_other_stops": _f(stops.loc[~stops["gap_stop"], "r_multiple"].mean(), 4),
        },
        "ambiguous_same_bar_rate": _f(f["ambiguous"].mean(), 4) if len(f) else None,
    }


def market_entry(book: Book, sim: pd.DataFrame, horizon: int, n_boot: int = 2000) -> dict:
    """Next-open market entry on the same setups with the same stop / target / exits. WRAP: an unbounded zone makes
    execution.simulate fill at the first valid open after the decision (same slippage). Setups that open at or below the
    stop are skipped: simulate would 'exit' at the stop price above the fill, which is not a real outcome."""
    s = sim[sim["rejected"].isna()].join(book.rows[["symbol", "t", "decision_date"]])
    ecfg = replace(PROD_EXEC, horizon_sessions=horizon)
    recs, skipped = [], 0
    for sym, t, stop, target in zip(s["symbol"], s["t"], s["stop"], s["target"], strict=True):
        o, hi, lo, cl = book.bars[sym]
        res = simulate(o, hi, lo, cl, int(t), 0.0, math.inf, stop, target, ecfg)
        if res.filled and o[res.entry_index] <= stop:
            skipped += 1
            recs.append((False, "SKIPPED_OPEN_AT_OR_BELOW_STOP", np.nan, np.nan, stop, np.nan, np.nan, np.nan))
            continue
        recs.append((res.filled, res.outcome, res.entry_price, res.exit_price, stop, res.return_gross, res.return_net,
                     res.r_multiple))
    mk = pd.DataFrame(recs, index=s.index, columns=["filled", "outcome", "entry_price", "exit_price", "stop",
                                                     "return_gross", "return_net", "r_multiple"])
    mk["filled"] = mk["filled"].astype(bool)
    for c in ("entry_price", "exit_price", "return_gross", "return_net", "r_multiple"):
        mk[c] = pd.to_numeric(mk[c], errors="coerce").astype(float)
    mk["decision_date"] = s["decision_date"]
    zone = s[s["filled"]]
    both = s["filled"] & mk["filled"]
    diff = (net_at_cost(mk.loc[both, "return_gross"], EVAL_BPS) - net_at_cost(s.loc[both, "return_gross"], EVAL_BPS))
    out = {"zone_entry": perf(zone, ci=True, n_boot=n_boot), "market_entry": perf(mk[mk["filled"]], ci=True, n_boot=n_boot),
           "skipped_open_at_or_below_stop": skipped}
    if both.any():
        boot = M.block_bootstrap_mean(diff.to_numpy(float), weeks_of(s.loc[both, "decision_date"]), n_boot=n_boot)
        out["paired_market_minus_zone"] = {"n": int(both.sum()), "mean_net_return_25": _f(diff.mean()),
                                           "ci": boot,
                                           "mean_net_r_10": _f((mk.loc[both, "r_multiple"] - s.loc[both, "r_multiple"]).mean(), 4)}
    return out


# ---------------------------------------------------------------------------------------------------------------------
# section 4: selection
# ---------------------------------------------------------------------------------------------------------------------
def _bucket_rows(d: pd.DataFrame, col: str, buckets) -> list[dict]:
    out = []
    for lo, hi, name in buckets:
        g = d[(d[col] >= lo) & (d[col] < hi)]
        row = {"bucket": name, "n": len(g)}
        row.update({"suppressed": True} if len(g) < MIN_CELL else {k: v for k, v in perf(g).items() if k != "n"})
        out.append(row)
    return out


def score_table(d: pd.DataFrame, col: str, draws: int, seed: int, buckets=None) -> dict:
    d = d[d[col].notna() & d["r_multiple"].notna()]
    if len(d) < MIN_CELL:
        return {"n": len(d), "available": False, "reason": f"fewer than {MIN_CELL} scored entries"}
    x, y = d[col].to_numpy(float), d["r_multiple"].to_numpy(float)
    nb = 10 if len(d) >= 200 else 5
    bins = pd.qcut(d[col].rank(method="first"), nb, labels=False)
    table = []
    for b, g in d.groupby(bins):
        table.append({"bin": int(b) + 1, "lo": _f(g[col].min(), 4), "hi": _f(g[col].max(), 4),
                      **perf(g)})
    m = np.array([r["mean_net_r_10"] for r in table], float)
    tr = np.array([r["target_first_rate"] for r in table], float)
    out = {"n": len(d), "available": True,
           "spearman_vs_net_r": _f(spearman(x, y), 4),
           "spearman_ci_weekly_bootstrap": spearman_boot(x, y, weeks_of(d["decision_date"]), draws, seed),
           "spearman_vs_target_first": _f(spearman(x, (d["outcome"] == "TARGET").to_numpy(float)), 4),
           "bins": nb, "table": table,
           "monotonicity": {"steps_up_net_r": int((np.diff(m) > 0).sum()), "steps": nb - 1,
                            "bin_spearman_net_r": _f(spearman(np.arange(nb), m), 4),
                            "bin_spearman_target_rate": _f(spearman(np.arange(nb), tr), 4),
                            "top_minus_bottom_net_r": _f(m[-1] - m[0], 4),
                            "top_minus_bottom_target_rate": _f(tr[-1] - tr[0], 4)}}
    if buckets:
        out["buckets"] = _bucket_rows(d, col, buckets)
    return out


def model_oos(oos: pd.DataFrame, folds, draws: int) -> dict:
    if oos.empty:
        return {"available": False,
                "reason": "walk_forward produced no development out-of-fold predictions (no fold reached "
                          f"min_train={ValidationConfig().min_train} purged training rows)"}
    p, y = oos["p_target"].to_numpy(float), oos["y"].to_numpy(float)
    base = oos["fold"].map({f.fold: f.base_rate for f in folds}).to_numpy(float)
    ref = float(np.mean((base - y) ** 2))
    return {
        "available": True, "n": len(oos), "folds": [f.fold for f in folds],
        "mean_predicted": _f(p.mean(), 4), "observed_rate": _f(y.mean(), 4),
        "brier": _f(M.brier(p, y), 5), "brier_skill_vs_fold_base_rate": _f(1 - M.brier(p, y) / ref, 5) if ref > 0 else None,
        "ece": _f(M.ece(p, y), 5),
        "reliability": M.reliability(p, y),
        "p_target_vs_outcome": score_table(oos, "p_target", draws, STUDY_SEED + 7),
        "quality_on_same_rows_spearman": _f(spearman(oos["quality"], oos["r_multiple"]), 4)
        if oos["quality"].notna().sum() >= MIN_CELL else None,
    }


def selection_report(entries: pd.DataFrame, oos: pd.DataFrame, folds, draws: int, n_boot: int = 2000) -> dict:
    pc = PolicyConfig()
    allowed = entries["regime_allowed"].astype(bool)
    slices = {
        "all development entries": entries,
        "regime allowed for strategy": entries[allowed],
        f"quality >= {pc.min_quality:g} (publication gate)": entries[entries["quality"] >= pc.min_quality],
        f"regime allowed and quality >= {pc.min_quality:g} (live-publishable proxy)":
            entries[allowed & (entries["quality"] >= pc.min_quality)],
        f"quality >= {pc.research_min_quality:g} (research-setup gate)": entries[entries["quality"] >= pc.research_min_quality],
    }
    subs = {c: score_table(entries, c, draws, STUDY_SEED + 3 + i, SUBSCORE_BUCKETS)
            for i, c in enumerate(SUBSCORES) if c in entries}
    tested = 1 + sum(1 for v in subs.values() if v.get("available"))
    return {
        "outcome": "net R per trade at 10 bps/side (production costs); returns at 25 bps",
        "quality": score_table(entries, "quality", draws, STUDY_SEED + 2, QUALITY_BUCKETS),
        "subscores": subs,
        "scores_tested": tested,
        "multiple_testing_note": f"{tested} scores tested against the same outcomes; a Bonferroni 5% two-sided bar is "
                                 f"p < {0.05 / tested:.4f} per score",
        "policy_slices": [{"slice": k, **perf(v, ci=len(v) >= MIN_CELL, n_boot=n_boot)} for k, v in slices.items()],
        "model_oos": model_oos(oos, folds, draws),
    }


# ---------------------------------------------------------------------------------------------------------------------
# section 5: breakdowns
# ---------------------------------------------------------------------------------------------------------------------
def breakdowns(entries: pd.DataFrame) -> dict:
    keys = {"regime": entries["regime"].astype(str),
            "regime_allowed": entries["regime_allowed"].map({True: "allowed", False: "not allowed"}),
            "sector": entries["sector"].astype(str),
            "year": pd.to_datetime(entries["decision_date"]).dt.year.astype(str)}
    out = {"min_cell": MIN_CELL}
    for name, key in keys.items():
        rows = []
        for val, g in entries.groupby(key):
            row = {"cell": str(val), "n": len(g)}
            row.update({"suppressed": True} if len(g) < MIN_CELL else {k: v for k, v in perf(g).items() if k != "n"})
            rows.append(row)
        out[name] = rows
    return out


# ---------------------------------------------------------------------------------------------------------------------
# per strategy / per baseline
# ---------------------------------------------------------------------------------------------------------------------
def _iso(ts) -> str:
    return pd.Timestamp(ts).date().isoformat()


def dev_info(rows: pd.DataFrame, filled: pd.Series, cutoff: pd.Timestamp, exits: pd.Series, embargo: int) -> dict:
    """Window bookkeeping. `embargo_limit` is the production purge boundary (validation.embargo_limit, NYSE sessions):
    the pipeline's final model never trains on a development label that ends on or after it."""
    d = pd.to_datetime(rows["decision_date"])
    ends = pd.to_datetime(exits[filled])
    limit = embargo_limit(cutoff, embargo)
    return {"setups": len(rows), "entries": int(filled.sum()), "decision_first": d.min(), "decision_last": d.max(),
            "distinct_decision_dates": int(d.nunique()), "holdout_cutoff_exclusive": cutoff,
            "label_windows_crossing_cutoff": int((ends >= cutoff).sum()),
            "embargo_limit": limit, "embargo_sessions": embargo,
            "label_windows_ending_after_embargo_limit": int((ends >= limit).sum())}


def check_strategy_consistency(dev: pd.DataFrame, sim: pd.DataFrame) -> dict:
    lv = ["entry_min", "entry_max", "stop", "target"]
    a, b = dev[lv].to_numpy(float), sim[lv].to_numpy(float)
    lv_mismatch = int((~np.isclose(a, b, rtol=0, atol=1e-9)).any(axis=1).sum())
    o_mismatch = int(((dev["filled"].to_numpy() != sim["filled"].to_numpy())
                      | (dev["outcome"].to_numpy() != sim["outcome"].to_numpy())).sum())
    f = dev["filled"].to_numpy()
    r_mismatch = int((~np.isclose(dev["return_net"].to_numpy(float)[f], sim["return_net"].to_numpy(float)[f],
                                  rtol=0, atol=1e-12)).sum())
    # the recomputed ATR / levels must also give the pipeline's own model feature (shared assembler)
    feat_mismatch = int((~np.isclose(dev["stop_dist_atr"].to_numpy(float), sim["stop_dist_atr"].to_numpy(float),
                                     rtol=0, atol=1e-9)).sum())
    return {"levels_mismatch": lv_mismatch, "outcome_mismatch": o_mismatch, "return_mismatch": r_mismatch,
            "stop_dist_atr_feature_mismatch": feat_mismatch,
            "levels_rejected_in_production_config": int(sim["rejected"].notna().sum()),
            "ok": lv_mismatch == o_mismatch == r_mismatch == feat_mismatch == 0 and not sim["rejected"].notna().any()}


def analyse_strategy(sid: str, ev_s: pd.DataFrame, cutoff: pd.Timestamp, vcfg: ValidationConfig, ctxs: Contexts,
                     configs: list[GridCfg], rc_draws: int, sp_draws: int,
                     other_boot: int) -> tuple[dict, Book, pd.DataFrame]:
    t0 = time.perf_counter()
    oos, folds, purged = dev_walk_forward(ev_s, cutoff, vcfg)
    dev = ev_s[ev_s["decision_date"] < cutoff]
    book = strategy_book(sid, dev, ctxs, cutoff)
    sims = run_grid(book, configs)
    prod_cfg = next(c for c in configs if c.is_production)
    sim = sims[prod_cfg]
    entries = book.rows.join(sim)
    entries = entries[entries["filled"]]
    # robustness: only labels that end before the production purge boundary (what the final model may train on)
    purged_entries = entries[pd.to_datetime(dev.loc[entries.index, "exit_date"])
                             < embargo_limit(cutoff, vcfg.embargo_sessions)]
    dev_filled = dev[dev["filled"]]
    reference = {  # straight from the pipeline's own development rows and walk-forward folds
        "entries": len(dev_filled),
        "target_first_rate": _f((dev_filled["outcome"] == "TARGET").mean(), 4),
        "mean_return_net_10": _f(dev_filled["return_net"].mean()),
        "mean_r_multiple_10": _f(dev_filled["r_multiple"].mean(), 4),
        "selection_summary_25bps": selection_summary(dev_filled, EVAL_BPS),
        "walk_forward_dev_folds": [{"fold": f.fold, "train_n": f.train_n, "test_n": f.test_n, "test_start": f.test_start,
                                    "test_end": f.test_end, "brier_skill": f.brier_skill, "ece": f.ece,
                                    "base_rate": f.base_rate} for f in folds],
        "oos_rows": len(oos), "purged_samples_dev_folds_and_final": purged,
    }
    out = {
        "status": "ok",
        "holdout_cutoff": cutoff,
        "dev": {**dev_info(dev, dev["filled"], cutoff, dev["exit_date"], vcfg.embargo_sessions),
                "production_on_purged_dev": perf(purged_entries, ci=len(purged_entries) >= MIN_CELL, n_boot=other_boot)},
        "pipeline_reference": reference,
        "consistency": check_strategy_consistency(dev, sim),
        "excursions": excursions(book, sim, prod_cfg.horizon),
        "entry": {**entry_diagnostics(book, sim),
                  "market_entry_variant": market_entry(book, sim, prod_cfg.horizon, other_boot)},
        "grid": grid_report(book, sims, rc_draws, other_boot),
        "selection": selection_report(entries, oos, folds, sp_draws, other_boot),
        "breakdowns": breakdowns(entries),
        "decision_dates": sorted({_iso(d) for d in dev["decision_date"]}),
    }
    out["seconds"] = round(time.perf_counter() - t0, 1)
    return out, book, entries


def analyse_baseline(name: str, rows: pd.DataFrame, cutoff: pd.Timestamp, ref_id: str, ctxs: Contexts,
                     configs: list[GridCfg], draws: int, other_boot: int) -> dict:
    if rows.empty:
        return {"status": "no_dev_rows", "holdout_cutoff": cutoff}
    book = baseline_book(name, rows, ctxs, cutoff)
    sims = run_grid(book, configs)
    prod_cfg = next(c for c in configs if c.is_production)
    sim = sims[prod_cfg]
    f = rows["filled"].to_numpy()
    mism = int(((rows["filled"].to_numpy() != sim["filled"].to_numpy())
                | (rows["outcome"].to_numpy() != sim["outcome"].to_numpy())).sum())
    rmism = int((~np.isclose(rows["return_net"].to_numpy(float)[f], sim["return_net"].to_numpy(float)[f],
                             rtol=0, atol=1e-12)).sum())
    return {
        "status": "ok", "holdout_cutoff": cutoff, "reference_strategy": ref_id,
        "dev": dev_info(rows, rows["filled"], cutoff, rows["exit_date"], ValidationConfig().embargo_sessions),
        "consistency": {"outcome_mismatch": mism, "return_mismatch": rmism, "ok": mism == rmism == 0},
        "excursions": excursions(book, sim, prod_cfg.horizon),
        "grid": grid_report(book, sims, draws, other_boot),
        "decision_dates": sorted({_iso(d) for d in rows["decision_date"]}),
    }


CAVEATS = (
    "SYNTHETIC DATA ONLY. demo/synthetic.py plants a known volatility-contraction edge and regime-dependent drifts; "
    "every number describes how the method interacts with that generator, not with a real market.",
    "Development window only (decision dates < the pipeline's holdout start per strategy). The last development "
    "setups' outcome windows run on bars after the cutoff (counted as label_windows_crossing_cutoff, and against the "
    "production session-based purge boundary as label_windows_ending_after_embargo_limit); the pipeline's development "
    "out-of-fold rows have the same property. production_on_purged_dev drops them as a robustness check.",
    "Grid variants change rr_net / stop_dist_atr, two model inputs; the out-of-fold model probabilities in the "
    "selection section exist only for production levels.",
    "build_events / baseline generation run inside production code over the full history because the split rule needs "
    "every filled decision date; the study reads only those dates from holdout rows, and walk_forward receives holdout "
    "rows with labels, returns and features blanked.",
    "Grid: min_net_rr is disabled so every configuration re-scores the same setups (fails_min_net_rr_share shows what "
    "the production filter would have removed); the resistance cap applies everywhere and can make the target-R knob "
    "inert (target_capped_share); TARGET_NOT_ABOVE_ENTRY rejections are counted per configuration.",
    "Mean net R is not comparable across stop widths (the R unit changes); compare configurations on net return.",
    "The best grid configuration is selected on the same data it is scored on, so it is optimistically biased; see "
    "the Bonferroni and sign-flip reality-check deflations. Configurations are strongly correlated (same setups).",
    "Per-trade means; overlapping trades are not capital- or concentration-constrained (no portfolio simulation).",
    "Daily bars: a same-bar stop+target touch counts as STOP (conservative); intraday order is unknown; in-trade MFE "
    "includes the fill bar's high, which may print before a limit fill.",
    "Costs: 10 bps/side as simulated by production; 25 bps re-priced from raw fill/exit prices (fills and exits do not "
    "depend on cost in the execution model). No spread, borrow, dividends or taxes.",
    "Fill rates near 100% reflect the generator's tiny overnight gaps (about 0.35% sd); real gaps are larger.",
    "Regime labels are the features' rule-based estimate, not the generator's latent regime. build_events keeps setups "
    "in regimes a strategy does not allow (the live engine only WATCHes them), see the regime_allowed breakdown.",
    "No catalysts in the synthetic fixture, so catalyst strategies (h3, h7) produce no events and g_catalyst is empty.",
    "Strategies with fewer than ValidationConfig.min_train purged training rows per fold have no out-of-fold model "
    "predictions; their model section is reported as unavailable.",
    "Baselines are scored up to the earliest strategy cutoff, so strategy-vs-baseline comparisons use that common "
    "window (baseline_comparison).",
    "Excursion R uses the actual raw fill (not the zone midpoint used to size levels); ATR is ATR(14) at the decision.",
)


def run_study(ids: list[str], quick: bool, draws: int | None = None) -> dict:
    t0 = time.perf_counter()
    universe = dict(QUICK_UNIVERSE) if quick else pipeline_universe()
    draws = draws or (500 if quick else 2000)
    sp_draws = 100 if quick else 500
    other_boot = 300 if quick else 2000
    log(f"loading synthetic market ({universe['stocks_per_sector']}/sector from {universe['start']})")
    panel, bars, _earnings, cats, ds, events, spec = load_inputs(universe["stocks_per_sector"], universe["start"],
                                                                 universe["keep_stocks_per_sector"], tuple(ids))
    if ds != "synthetic_fixture":
        raise SystemExit("level study runs on the synthetic fixture only")
    log(f"events: {len(events)} rows in {time.perf_counter() - t0:.1f}s")
    vcfg = ValidationConfig()
    configs = grid_configs(quick)
    ctxs = Contexts(panel, bars, cats)
    strategies: dict[str, dict] = {}
    cutoffs: dict[str, pd.Timestamp] = {}
    entries_by: dict[str, pd.DataFrame] = {}
    for sid in ids:
        ev_s = events[events["strategy_id"] == sid] if not events.empty else events
        if ev_s.empty:
            strategies[sid] = {"status": "no_events", "detail": "the strategy produced no events on this data"}
            continue
        cutoff = holdout_cutoff(ev_s, vcfg)
        if cutoff is None:
            strategies[sid] = {"status": "no_split", "detail": "fewer than 10 filled decision dates: no holdout split"}
            continue
        if not bool((ev_s["filled"] & (ev_s["decision_date"] < cutoff)).any()):
            strategies[sid] = {"status": "no_dev_entries", "holdout_cutoff": cutoff}
            continue
        cutoffs[sid] = cutoff
        strategies[sid], _book, entries_by[sid] = analyse_strategy(sid, ev_s, cutoff, vcfg, ctxs, configs, draws,
                                                                   sp_draws, other_boot)
        log(f"{sid}: cutoff {cutoff.date()}, {strategies[sid]['dev']['entries']} dev entries, "
            f"{strategies[sid]['seconds']}s")
    baselines: dict[str, dict] = {}
    base_cutoff = min(cutoffs.values()) if cutoffs else None
    # run_training matches baselines to the first strategy (in order) that has events
    ref_id = next((s for s in ids if not events.empty and bool((events["strategy_id"] == s).any())), None)
    if base_cutoff is not None and ref_id is not None:
        tb = time.perf_counter()
        ref = events[(events["strategy_id"] == ref_id) & (events["decision_date"] < base_cutoff)]
        # development-only reference dates and window: the production generator walks dates in order (random picks
        # draw once per date, breakout_basic thins a sorted list), so its development rows are unchanged
        base = baseline_events(panel, bars, replace(spec, end=base_cutoff - pd.Timedelta(days=1)), ref)
        for name, b in base.items():
            if not b.empty and not bool((b["decision_date"] < base_cutoff).all()):
                raise AssertionError(f"baseline {name} produced a row on/after the cutoff")
            baselines[name] = analyse_baseline(name, b, base_cutoff, ref_id, ctxs, configs, draws, other_boot)
        log(f"baselines: {', '.join(f'{k}={len(v)}' for k, v in base.items())} in {time.perf_counter() - tb:.1f}s")
        for sid, e in entries_by.items():
            w = e[e["decision_date"] < base_cutoff]
            strategies[sid]["baseline_comparison"] = {
                "window": f"decision dates < {base_cutoff.date()} (earliest strategy cutoff), production levels",
                "window_end_exclusive": base_cutoff,
                "strategy": perf(w, ci=len(w) >= MIN_CELL, n_boot=other_boot),
                **{name: (b["grid"]["production"] if "grid" in b else {"n": 0}) for name, b in baselines.items()},
            }
    for rep in strategies.values():
        if rep.get("status") == "ok":
            rep["key_findings"] = key_findings(rep)
    runtime = round(time.perf_counter() - t0, 1)
    return {
        "study": STUDY_VERSION,
        "label": SYNTHETIC_LABEL,
        "data_source": ds,
        "is_synthetic": True,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runtime_seconds": runtime,
        "quick": quick,
        "universe": {**universe, "symbols": len(bars), "data_hash": data_hash(bars)},
        "seeds": {"synthetic_market": "demo.synthetic.DEFAULT_SEED (generator default)",
                  "validation": vcfg.seed, "baselines": "dataset.baseline_events default (7)",
                  "p10_block_bootstrap": "metrics.block_bootstrap_mean default seed 11",
                  "study_null_and_spearman": STUDY_SEED},
        "draws": {"p10_bootstrap_production_grid_row": 2000, "p10_bootstrap_everything_else": other_boot,
                  "sign_flip_null": draws, "spearman_bootstrap": sp_draws},
        "strategies_requested": ids,
        "holdout_discipline": {
            "rule": "decision_date < holdout start of research.validation.walk_forward with ValidationConfig() "
                    f"(holdout_fraction={vcfg.holdout_fraction}), per strategy, asserted equal to walk_forward's own",
            "cutoffs": dict(cutoffs.items()),
            "baseline_cutoff": base_cutoff,
        },
        "grid_definition": {
            "stops": [s if s == STRUCTURAL else float(s) for s in dict.fromkeys(c.stop for c in configs)],
            "target_rs": sorted({c.target_r for c in configs}),
            "horizons": sorted({c.horizon for c in configs}),
            "n_configs": len(configs),
            "production": GridCfg(*PRODUCTION).label,
            "structural": "production stop: strategy invalidation hint clamped to [mid - 3 ATR, mid - 1 ATR]",
            "fixed_atr": "stop = zone mid - k x ATR(14)",
            "target": "mid + R x (mid - stop), capped below the prior 120-session high when it sits in between",
            "costs_bps_per_side": [10.0, EVAL_BPS],
        },
        "strategies": strategies,
        "baselines": baselines,
        "caveats": list(CAVEATS),
    }


def key_findings(rep: dict) -> dict:
    ex, g, sel = rep["excursions"], rep["grid"], rep["selection"]
    q = sel["quality"]
    best, prod = g["best"] or {}, g["production"]
    dfl = g["deflation"]
    return {
        "dev_entries": rep["dev"]["entries"],
        "mfe_r_p50": ex["all"]["mfe_r"].get("p50"), "mae_r_p50": ex["all"]["mae_r"].get("p50"),
        "mfe_atr_p50": ex["all"]["mfe_atr"].get("p50"), "mae_atr_p50": ex["all"]["mae_atr"].get("p50"),
        "mfe_r_full_horizon_p50": ex["all"]["mfe_r_full_horizon"].get("p50"),
        "stop_too_tight_share": ex["stop_too_tight"]["share"],
        "target_beyond_r_p50": ex["target_too_close"]["beyond_target_r"].get("p50"),
        "production": {k: prod.get(k) for k in ("config", "target_first_rate", "stop_first_rate", "mean_net_return_25",
                                                "p10_mean_net_return_25", "mean_net_r_10")},
        "best": {k: best.get(k) for k in ("config", "target_first_rate", "stop_first_rate", "mean_net_return_25",
                                          "p10_mean_net_return_25", "mean_net_r_10")},
        "n_configs": g["n_configs"],
        "best_vs_zero_reality_check_p": dfl["best_vs_zero"]["reality_check"].get("p_value"),
        "best_vs_zero_bonferroni_p": dfl["best_vs_zero"]["bonferroni"].get("p_bonferroni"),
        "best_vs_zero_z_deflated": dfl["best_vs_zero"]["bonferroni"].get("z_deflated"),
        "best_vs_production_reality_check_p": dfl["best_vs_production"]["reality_check"].get("p_value"),
        "quality_spearman": q.get("spearman_vs_net_r"), "quality_spearman_ci": q.get("spearman_ci_weekly_bootstrap"),
        "quality_monotonicity": q.get("monotonicity"),
    }


# ---------------------------------------------------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------------------------------------------------
def json_safe(o):
    if isinstance(o, dict):
        return {str(k): json_safe(v) for k, v in o.items()}
    if isinstance(o, list | tuple):
        return [json_safe(v) for v in o]
    if isinstance(o, bool | np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, float | np.floating):
        return float(o) if math.isfinite(float(o)) else None
    if o is pd.NaT:
        return None
    if isinstance(o, pd.Timestamp):
        return o.date().isoformat()
    if isinstance(o, date):
        return o.isoformat()
    return o


def _pct(x, nd: int = 2) -> str:
    return "" if x is None else f"{x * 100:+.{nd}f}%"


def _rate(x) -> str:
    return "" if x is None else f"{x * 100:.1f}%"


def _n(x, nd: int = 2) -> str:
    return "" if x is None else f"{x:.{nd}f}"


def _na(x) -> str:
    """Prose rendering of a value that may be missing."""
    return "n/a" if x is None else str(x)


def _table(headers: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join("" if v is None else str(v) for v in r) + " |" for r in rows]
    return [*out, ""]


def _perf_cells(r: dict) -> list:
    if r.get("suppressed"):
        return ["(n<20)"] * 6
    return [_rate(r.get("target_first_rate")), _rate(r.get("stop_first_rate")), _rate(r.get("time_exit_rate")),
            _n(r.get("mean_net_r_10")), _pct(r.get("mean_net_return_10")), _pct(r.get("mean_net_return_25"))]


PERF_H = ["target-first", "stop-first", "time", "net R @10", "net ret @10", "net ret @25"]


def _grid_row(r: dict) -> list:
    tag = "**PRODUCTION**" if r["is_production"] else ""
    return [r["config"], tag, r["entries"], r["levels_rejected"], _rate(r.get("target_first_rate")),
            _rate(r.get("stop_first_rate")), _rate(r.get("time_exit_rate")), _n(r.get("mean_net_r_10")),
            _n(r.get("mean_net_r_25")), _pct(r.get("mean_net_return_10")), _pct(r.get("mean_net_return_25")),
            _pct(r.get("p10_mean_net_return_25")), _n(r.get("stop_dist_atr_p50")), _rate(r.get("target_capped_share")),
            _rate(r.get("fails_min_net_rr_share"))]


GRID_H = ["config", "", "n", "rej", "target-first", "stop-first", "time", "net R @10", "net R @25", "net ret @10",
          "net ret @25", "p10 ret @25", "stop ATR p50", "target capped", "fails min RR"]


def _grid_md(g: dict, title: str, full: bool) -> list[str]:
    rows = g["rows"]
    d = g["deflation"]
    bz, bp = d["best_vs_zero"], d["best_vs_production"]["reality_check"]
    lines = [f"{title}: {g['n_configs']} configurations tested on the same setups.", ""]
    lines += _table(GRID_H, [_grid_row(g["production"]), _grid_row(g["best"])] if g["best"] else [_grid_row(g["production"])])
    rc, bf = bz["reality_check"], bz["bonferroni"]
    lines += [f"- Best configuration: `{g['best']['config'] if g['best'] else None}`. "
              f"Sign-flip reality check, best vs zero: p = {rc.get('p_value')} "
              f"(null max mean p50 {_pct(rc.get('null_max_p50'), 3)}, p95 {_pct(rc.get('null_max_p95'), 3)}). "
              f"Bonferroni: z = {_na(bf.get('z'))}, p_raw = {_na(bf.get('p_raw_one_sided'))}, p x {bf.get('k')} = "
              f"{_na(bf.get('p_bonferroni'))}, deflated z = "
              f"{_na(bf.get('z_deflated')) if bf.get('p_bonferroni') != 1.0 else 'none (p x K capped at 1)'} "
              f"(z needed at 5%: {_na(bf.get('z_required_5pct'))}).",
              f"- Best vs production (paired, max over {bp.get('configs')} alternatives): best improvement "
              f"`{bp.get('best')}` {_pct(bp.get('observed_max'), 3)} per trade, reality-check p = {bp.get('p_value')}.",
              f"- Production alone (no search): z = {d['production_single_test'].get('z')}, one-sided p = "
              f"{d['production_single_test'].get('p_raw_one_sided')}.", ""]
    hz = max(r["horizon"] for r in rows)
    stops = list(dict.fromkeys(r["config"].split(" ")[0] for r in rows))
    rs = sorted({r["target_r"] for r in rows})
    look = {(r["config"].split(" ")[0], r["target_r"], r["horizon"]): r for r in rows}
    lines += [f"Mean net return per trade at 25 bps (p10 in brackets), horizon {hz}:", ""]
    lines += _table(["stop \\ target R", *[f"{x:g}R" for x in rs]],
                    [[s, *[(f"{_pct(look[(s, x, hz)].get('mean_net_return_25'))} "
                            f"({_pct(look[(s, x, hz)].get('p10_mean_net_return_25'))})") if (s, x, hz) in look else ""
                           for x in rs]] for s in stops])
    lines += [f"Target-first rate, horizon {hz}:", ""]
    lines += _table(["stop \\ target R", *[f"{x:g}R" for x in rs]],
                    [[s, *[_rate(look[(s, x, hz)].get("target_first_rate")) if (s, x, hz) in look else "" for x in rs]]
                     for s in stops])
    if full:
        lines += ["<details><summary>Full grid</summary>", ""]
        lines += _table(GRID_H, [_grid_row(r) for r in rows])
        lines += ["</details>", ""]
    return lines


def _q_row(name: str, q: dict) -> list:
    return [name, q.get("n"), *[_n(q.get(f"p{int(x * 100)}")) for x in QS]]


def _score_md(name: str, s: dict) -> list[str]:
    if not s.get("available"):
        return [f"- `{name}`: not available ({s.get('reason')}).", ""]
    m = s["monotonicity"]
    ci = s.get("spearman_ci_weekly_bootstrap") or {}
    lines = [f"`{name}`: n = {s['n']}, Spearman vs net R = {s['spearman_vs_net_r']} "
             f"(weekly bootstrap 90% CI {ci.get('p5')} to {ci.get('p95')}), vs target-first = "
             f"{s['spearman_vs_target_first']}; {s['bins']} bins, net R rises in {m['steps_up_net_r']}/{m['steps']} "
             f"steps, bin Spearman {m['bin_spearman_net_r']}, top - bottom = {m['top_minus_bottom_net_r']} R / "
             f"{_rate(m['top_minus_bottom_target_rate'])} target-first.", ""]
    lines += _table(["bin", "range", "n", *PERF_H],
                    [[r["bin"], f"{_n(r['lo'], 3)} to {_n(r['hi'], 3)}", r["n"], *_perf_cells(r)] for r in s["table"]])
    if s.get("buckets"):
        lines += _table(["bucket", "n", *PERF_H], [[r["bucket"], r["n"], *_perf_cells(r)] for r in s["buckets"]])
    return lines


def _strategy_md(sid: str, r: dict) -> list[str]:
    lines = [f"## {sid}", ""]
    if r.get("status") != "ok":
        return [*lines, f"Status `{r.get('status')}`: {r.get('detail', '')} (cutoff {r.get('holdout_cutoff')}).", ""]
    dv, c, ref = r["dev"], r["consistency"], r["pipeline_reference"]
    lines += [f"> {SYNTHETIC_LABEL}", "",
              f"- Holdout cutoff (pipeline): **{r['holdout_cutoff']}**; development decisions {dv['decision_first']} to "
              f"{dv['decision_last']} ({dv['distinct_decision_dates']} dates), {dv['setups']} setups, {dv['entries']} "
              f"entries; {dv.get('label_windows_crossing_cutoff')} outcome windows cross the cutoff, "
              f"{dv.get('label_windows_ending_after_embargo_limit')} end on/after the {dv.get('embargo_sessions')}-session "
              f"embargo limit {dv.get('embargo_limit')}; production levels on the purged set: n = "
              f"{dv['production_on_purged_dev'].get('n')}, net ret @25 "
              f"{_pct(dv['production_on_purged_dev'].get('mean_net_return_25'))}.",
              f"- Consistency with the pipeline's own development rows: levels mismatches {c['levels_mismatch']}, "
              f"outcome mismatches {c['outcome_mismatch']}, return mismatches {c['return_mismatch']}, stop_dist_atr "
              f"feature mismatches {c.get('stop_dist_atr_feature_mismatch')} "
              f"({'OK' if c['ok'] else 'MISMATCH'}); walk-forward development folds "
              f"{[f['fold'] for f in ref['walk_forward_dev_folds']]}, {ref['oos_rows']} out-of-fold rows.", ""]
    ex = r["excursions"]
    lines += ["### 1. Excursions (production levels)", "", ex["definition"] + ".", ""]
    qrows = [_q_row(f"all {m}", ex["all"][m]) for m in ex["all"]]
    for g, d in ex["by_outcome"].items():
        qrows += [_q_row(f"{g} {m}", d[m]) for m in ("mfe_r", "mae_r", "mfe_atr", "mae_atr") if d.get(m)]
    lines += _table(["metric", "n", "p10", "p25", "p50", "p75", "p90"], qrows)
    st, tt = ex["stop_too_tight"], ex["target_too_close"]
    lines += [f"- Stop too tight: {st['later_reached_target']}/{st['stopped_trades']} stopped trades "
              f"(**{_rate(st['share'])}**) later reached the original target within the horizon; "
              f"{_rate(st['later_reached_plus_1r_share'])} later reached +1R; {st['same_bar_ambiguous_stops']} stops "
              "were same-bar stop+target touches.",
              f"- Target too close: of {tt['target_trades']} target hits, median further run beyond the target "
              f"**{_n(tt['beyond_target_r'].get('p50'))} R** ({_n(tt['beyond_target_atr'].get('p50'))} ATR); "
              f"{_rate(tt['share_beyond_1r'])} ran >= 1R beyond.",
              f"- Time exits: final R p50 {_n(ex['time_exits']['final_r'].get('p50'))}, in-trade MFE p50 "
              f"{_n(ex['time_exits']['mfe_r'].get('p50'))} R.", ""]
    tte = ex["time_to_exit"]["sessions_held"]
    lines += _table(["outcome", "n", "p10", "p25", "p50", "p75", "p90"], [_q_row(k, v) for k, v in tte.items()])
    e = r["entry"]
    lines += ["### 2. Entry", "",
              f"- Fill rate {_rate(e['fill_rate'])} ({e['entries']}/{e['setups']}); unfilled: {e['unfilled_reasons'] or 'none'}.",
              f"- Gap through stop: {_rate(e['gap_through_stop']['rate_of_entries'])} of entries, "
              f"{_rate(e['gap_through_stop']['share_of_stops'])} of stops; mean R gap stops "
              f"{_na(e['gap_through_stop']['mean_r_gap_stops'])} vs other stops "
              f"{_na(e['gap_through_stop']['mean_r_other_stops'])}. "
              f"Same-bar ambiguous: {_rate(e['ambiguous_same_bar_rate'])}.",
              f"- Fill vs zone mid (ATR): p10 {e['fill_vs_zone_mid_atr'].get('p10')}, p50 "
              f"{e['fill_vs_zone_mid_atr'].get('p50')}, p90 {e['fill_vs_zone_mid_atr'].get('p90')}.", ""]
    lines += _table(["fill location", "n", "share", *PERF_H],
                    [[x["fill_location"], x["n"], _rate(x["share"]), *_perf_cells(x)] for x in e["by_fill_location"]])
    lines += _table(["fill session", "n", "share", *PERF_H],
                    [[x["fill_session_after_decision"], x["n"], _rate(x["share"]), *_perf_cells(x)] for x in e["by_fill_session"]])
    mk = e["market_entry_variant"]
    pm = mk.get("paired_market_minus_zone", {})
    lines += _table(["entry", "n", *PERF_H, "p10 ret @25"],
                    [["zone limit (production)", mk["zone_entry"].get("n"), *_perf_cells(mk["zone_entry"]),
                      _pct(mk["zone_entry"].get("p10_mean_net_return_25"))],
                     ["next-open market", mk["market_entry"].get("n"), *_perf_cells(mk["market_entry"]),
                      _pct(mk["market_entry"].get("p10_mean_net_return_25"))]])
    lines += [f"Paired market - zone on {pm.get('n')} setups filled both ways: {_pct(pm.get('mean_net_return_25'), 3)} "
              f"per trade at 25 bps (bootstrap p10 {_pct((pm.get('ci') or {}).get('p10'), 3)}, p90 "
              f"{_pct((pm.get('ci') or {}).get('p90'), 3)}); {mk['skipped_open_at_or_below_stop']} setups skipped "
              "(opened at/below the stop).", ""]
    lines += ["### 3. Level grid", ""]
    lines += _grid_md(r["grid"], "Same development setups, same execution model", full=True)
    s = r["selection"]
    lines += ["### 4. Selection", "", f"Outcome: {s['outcome']}. {s['multiple_testing_note']}.", ""]
    lines += _score_md("quality", s["quality"])
    for k, v in s["subscores"].items():
        lines += _score_md(k, v)
    lines += _table(["slice", "n", *PERF_H, "p10 ret @25"],
                    [[x["slice"], x["n"], *(_perf_cells(x) if x["n"] else [""] * 6),
                      _pct(x.get("p10_mean_net_return_25"))] for x in s["policy_slices"]])
    mo = s["model_oos"]
    if mo.get("available"):
        lines += [f"Development out-of-fold model P(target first): n = {mo['n']} (folds {mo['folds']}), mean predicted "
                  f"{mo['mean_predicted']} vs observed {mo['observed_rate']}, Brier {mo['brier']}, Brier skill vs fold "
                  f"base rate {mo['brier_skill_vs_fold_base_rate']}, ECE {mo['ece']}; Quality Spearman on the same rows "
                  f"{mo['quality_on_same_rows_spearman']}.", ""]
        lines += _table(["p bin", "n", "mean predicted", "observed"],
                        [[f"{b['bin_lo']:.1f}-{b['bin_hi']:.1f}", b["n"], b["mean_predicted"], b["observed_rate"]]
                         for b in mo["reliability"]])
        lines += _score_md("p_target (out-of-fold)", mo["p_target_vs_outcome"])
    else:
        lines += [f"Development out-of-fold model: not available ({mo['reason']}).", ""]
    lines += ["### 5. Breakdowns (production levels, cells with n < 20 suppressed)", ""]
    for k in ("regime", "regime_allowed", "sector", "year"):
        lines += _table([k, "n", *PERF_H], [[x["cell"], x["n"], *_perf_cells(x)] for x in r["breakdowns"][k]])
    bc = r.get("baseline_comparison")
    if bc:
        lines += ["### 6. Versus baselines (common window, production levels)", "", f"Window: {bc['window']}.", ""]
        brows = [["strategy " + sid, bc["strategy"].get("n"), *_perf_cells(bc["strategy"]),
                  _pct(bc["strategy"].get("p10_mean_net_return_25"))]]
        for name in ("random_qualified", "momentum_20", "breakout_basic"):
            b = bc.get(name) or {}
            brows.append([name, b.get("entries", b.get("n")), *(_perf_cells(b) if b.get("entries") else [""] * 6),
                          _pct(b.get("p10_mean_net_return_25"))])
        lines += _table(["arm", "n", *PERF_H, "p10 ret @25"], brows)
    return lines


def render_md(res: dict) -> str:
    lines = [f"# APEX level and selection study ({res['study']})", "", f"> **{SYNTHETIC_LABEL}.**", "",
             f"- generated {res['generated_at']}, runtime {res['runtime_seconds']} s, quick={res['quick']}",
             f"- universe: synthetic, {res['universe']['stocks_per_sector']} stocks/sector from {res['universe']['start']} "
             f"(kept per sector: {res['universe']['keep_stocks_per_sector'] or 'all'}), "
             f"{res['universe']['symbols']} symbols, data hash `{res['universe']['data_hash'][:16]}`",
             f"- grid: stops {res['grid_definition']['stops']} x target R {res['grid_definition']['target_rs']} x "
             f"horizons {res['grid_definition']['horizons']} = {res['grid_definition']['n_configs']} configurations; "
             f"production = `{res['grid_definition']['production']}`",
             f"- seeds: {res['seeds']}", "",
             "## Holdout discipline", "", res["holdout_discipline"]["rule"] + ".", ""]
    lines += _table(["arm", "holdout cutoff (exclusive)", "status"],
                    [[k, v.get("holdout_cutoff", ""), v.get("status")] for k, v in res["strategies"].items()]
                    + [[f"baseline {k}", v.get("holdout_cutoff"), v.get("status")] for k, v in res["baselines"].items()])
    lines += ["## Key findings (development window)", ""]
    kf_rows = []
    for sid, r in res["strategies"].items():
        k = r.get("key_findings")
        if not k:
            continue
        qm = k["quality_monotonicity"] or {}
        ci = k["quality_spearman_ci"] or {}
        kf_rows.append([sid, k["dev_entries"], _n(k["mfe_r_p50"]), _n(k["mae_r_p50"]), _n(k["mfe_atr_p50"]),
                        _n(k["mae_atr_p50"]), _rate(k["stop_too_tight_share"]), _n(k["target_beyond_r_p50"]),
                        f"{_pct(k['production']['mean_net_return_25'])} ({_pct(k['production']['p10_mean_net_return_25'])})",
                        f"`{k['best']['config']}` {_pct(k['best']['mean_net_return_25'])} "
                        f"({_pct(k['best']['p10_mean_net_return_25'])})",
                        k["best_vs_zero_reality_check_p"], k["best_vs_zero_bonferroni_p"],
                        k["best_vs_production_reality_check_p"],
                        f"{k['quality_spearman']} [{ci.get('p5')}, {ci.get('p95')}]",
                        f"{qm.get('steps_up_net_r')}/{qm.get('steps')}"])
    lines += _table(["strategy", "n", "MFE R p50", "MAE R p50", "MFE ATR p50", "MAE ATR p50", "stop too tight",
                     "beyond target R p50", "production ret @25 (p10)", "best config ret @25 (p10)", "RC p vs 0",
                     "Bonf p vs 0", "RC p vs prod", "Quality Spearman [90% CI]", "decile steps up"], kf_rows)
    for sid, r in res["strategies"].items():
        lines += _strategy_md(sid, r)
    lines += ["## Baselines (same grid, same execution model)", ""]
    for name, b in res["baselines"].items():
        lines += [f"### {name}", ""]
        if b.get("status") != "ok":
            lines += [f"Status `{b.get('status')}`.", ""]
            continue
        dv, ex = b["dev"], b["excursions"]
        lines += [f"Reference strategy {b['reference_strategy']}; decisions {dv['decision_first']} to {dv['decision_last']}, "
                  f"{dv['setups']} setups, {dv['entries']} entries; consistency with production rows: "
                  f"{'OK' if b['consistency']['ok'] else 'MISMATCH'}. Excursions: MFE R p50 "
                  f"{_n(ex['all']['mfe_r'].get('p50'))}, MAE R p50 {_n(ex['all']['mae_r'].get('p50'))}, stop too tight "
                  f"{_rate(ex['stop_too_tight']['share'])}.", ""]
        lines += _grid_md(b["grid"], f"Baseline {name}", full=False)
    lines += ["## Caveats", "", *[f"- {c}" for c in res["caveats"]], ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=".apex-local/level-study")
    ap.add_argument("--strategies", default=None,
                    help="comma-separated strategy ids (default: the pipeline's default-enabled strategies)")
    ap.add_argument("--quick", action="store_true",
                    help="smoke-test scale: 4 stocks/sector from 2022, a 12-configuration grid, fewer bootstrap / "
                         "null draws (the production grid row keeps metrics.py's 2000)")
    a = ap.parse_args(argv)
    ids = [s.strip() for s in a.strategies.split(",") if s.strip()] if a.strategies else \
        [s for s, st in STRATEGIES.items() if st.spec.default_enabled]
    unknown = [s for s in ids if s not in STRATEGIES]
    if unknown:
        ap.error(f"unknown strategies: {', '.join(unknown)} (known: {', '.join(STRATEGIES)})")
    t0 = time.perf_counter()
    res = json_safe(run_study(ids, a.quick))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "level_study.json").write_text(json.dumps(res, indent=2, sort_keys=False) + "\n")
    (out / "level_study.md").write_text(render_md(res))
    summary = {sid: r.get("key_findings", {"status": r.get("status")}) for sid, r in res["strategies"].items()}
    print(json.dumps({"label": res["label"], "cutoffs": res["holdout_discipline"]["cutoffs"],
                      "key_findings": summary}, indent=2))
    print(f"level study: {time.perf_counter() - t0:.1f}s wall -> {out / 'level_study.json'}, {out / 'level_study.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
