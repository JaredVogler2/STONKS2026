"""Strategy, baseline and portfolio evaluation under realeq-protocol-1 (Phase 14 sections C and D).

Inputs are the frozen boundaries plus point-in-time events from research.dataset.build_events (same signals, levels and
execution simulator as production). Simulations run at 0 bps so every stored price is the raw executable level; the
cost grid is applied afterwards with one formula, so a result at 25 bps and at 50 bps describes the same trades.
Missing quantities are None (JSON null), never a fictitious 0.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from apex_worker.features.panel import FeaturePanel
from apex_worker.research import metrics as M
from apex_worker.research.dataset import MODEL_FEATURES, DatasetSpec, build_events, gate_mask
from apex_worker.research.evaluate import PromotionPolicy, evaluate_strategy
from apex_worker.research.execution import ExecConfig, simulate
from apex_worker.research.realeq.protocol import PROTOCOL, Boundaries
from apex_worker.research.training_data import BENCHMARK
from apex_worker.research.validation import ValidationConfig, _fit, _purge, _rows, walk_forward
from apex_worker.signals.context import context_frame
from apex_worker.signals.levels import LevelConfig, Levels, compute_levels
from apex_worker.signals.strategies.library import STRATEGIES

COMPLETED = ("TARGET", "STOP", "TIME")
QUALITY_FEATURES = ("quality", "g_price", "g_sector", "g_volume", "g_volatility", "g_regime", "g_catalyst")
BASELINES = ("momentum_20", "breakout_basic")
T = PROTOCOL["thresholds"]


def _f(x, nd: int = 6):
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else round(v, nd)


def net_at(gross, bps: float) -> np.ndarray:
    c = bps / 1e4
    return (1 + np.asarray(gross, float)) * (1 - c) / (1 + c) - 1


def r_at(entry, exit_, stop, bps: float) -> np.ndarray:
    c = bps / 1e4
    entry, exit_, stop = (np.asarray(v, float) for v in (entry, exit_, stop))
    risk = entry * (1 + c) - stop
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(risk > 0, (exit_ * (1 - c) - entry * (1 + c)) / risk, np.nan)


def weeks_of(dates) -> np.ndarray:
    return pd.to_datetime(pd.Series(dates)).dt.to_period("W").astype(str).to_numpy()


def boot_means(values: np.ndarray, groups: np.ndarray, n_boot: int | None = None, seed: int | None = None) -> np.ndarray:
    """Weekly block bootstrap of the mean (same algorithm and seed as research.metrics.block_bootstrap_mean)."""
    b = PROTOCOL["bootstrap"]
    n_boot, seed = n_boot or b["n_boot"], b["seed"] if seed is None else seed
    rng = np.random.default_rng(seed)
    uniq = np.unique(groups)
    idx_by = [np.flatnonzero(groups == g) for g in uniq]
    out = np.empty(n_boot)
    for i in range(n_boot):
        pick = rng.integers(0, len(uniq), len(uniq))
        out[i] = values[np.concatenate([idx_by[j] for j in pick])].mean()
    return out


def dedup(df: pd.DataFrame, key: str = "strategy_id") -> pd.DataFrame:
    """One pending/open position per (key, symbol): skip a signal until the earlier kept one has exited."""
    if df.empty:
        return df
    keep = []
    for _, g in df.sort_values("decision_date").groupby([key, "symbol"], sort=False):
        busy = pd.Timestamp.min
        for i, d, x in zip(g.index, g["decision_date"], g["exit_date"], strict=True):
            if d > busy:
                keep.append(i)
                busy = x if pd.notna(x) else d
    return df.loc[keep].sort_values(["decision_date", "symbol"], kind="stable")


def completed(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["filled"].astype(bool) & df["outcome"].isin(COMPLETED)] if not df.empty else df


def trade_stats(df: pd.DataFrame, bps: float, boot: bool = True) -> dict:
    """Signals -> fills -> completed outcomes and net economics at `bps` per side."""
    out: dict = {"cost_bps_per_side": bps, "signals": len(df)}
    if df.empty:
        return out | {"filled": 0, "completed": 0, "fill_rate": None, "mean_net": None}
    filled = df[df["filled"].astype(bool)]
    done = completed(df)
    out |= {
        "filled": len(filled),
        "no_fill": int((df["outcome"] == "NO_FILL").sum()),
        "data_end": int((df["outcome"] == "DATA_END").sum()),
        "completed": len(done),
        "fill_rate": _f(len(filled) / len(df), 4),
        "target_first": int((done["outcome"] == "TARGET").sum()),
        "stop_first": int((done["outcome"] == "STOP").sum()),
        "time_exit": int((done["outcome"] == "TIME").sum()),
        "ambiguous_same_bar": int(done["ambiguous"].astype(bool).sum()) if "ambiguous" in done else None,
        "gap_through_stop": int(done["gap_stop"].astype(bool).sum()) if "gap_stop" in done else None,
    }
    if done.empty:
        return out | {"mean_net": None}
    r = net_at(done["return_gross"], bps)
    rr = r_at(done["entry_price"], done["exit_price"], done["stop"], bps)
    rr = rr[np.isfinite(rr)]
    wk = weeks_of(done["decision_date"])
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    nt, ns = out["target_first"], out["stop_first"]
    out |= {
        "mean_net": _f(r.mean()),
        "median_net": _f(np.median(r)),
        "win_rate": _f((r > 0).mean(), 4),
        "mean_r": _f(rr.mean(), 4) if len(rr) else None,
        "median_r": _f(np.median(rr), 4) if len(rr) else None,
        "r_p10": _f(np.quantile(rr, 0.10), 4) if len(rr) else None,
        "expected_shortfall_5": _f(M.expected_shortfall(r)),
        "profit_factor": _f(gains / losses, 3) if len(r) >= 30 and losses > 0 else None,
        "target_before_stop": _f(nt / (nt + ns), 4) if nt + ns else None,
        "mean_sessions_held": _f(done["sessions_held"].astype(float).mean(), 2),
        "mean_mfe": _f(done["mfe"].astype(float).mean()),
        "mean_mae": _f(done["mae"].astype(float).mean()),
        "distinct_dates": int(pd.to_datetime(done["decision_date"]).nunique()),
        "distinct_weeks": len(np.unique(wk)),
        "regimes": int(done["regime"].nunique()) if "regime" in done else None,
    }
    amb = done["ambiguous"].astype(bool).to_numpy() if "ambiguous" in done else np.zeros(len(done), bool)
    out["mean_net_unresolved_excluded"] = _f(r[~amb].mean()) if (~amb).any() else None
    if boot and len(r) >= 2:
        means = boot_means(r, wk)
        out["mean_net_ci"] = {f"p{q}": _f(np.quantile(means, q / 100)) for q in (5, 10, 50, 90, 95)}
        out["p_one_sided"] = _f(float((means <= 0).mean()), 4)
    return out


def group_stats(done: pd.DataFrame, keys: pd.Series, bps: float) -> dict:
    out = {}
    for k, g in done.groupby(keys, observed=True):
        r = net_at(g["return_gross"], bps)
        rr = r_at(g["entry_price"], g["exit_price"], g["stop"], bps)
        out[str(k)] = {"n": len(g), "mean_net": _f(r.mean()), "win_rate": _f((r > 0).mean(), 4),
                       "mean_r": _f(np.nanmean(rr), 4) if np.isfinite(rr).any() else None, "small_sample": len(g) < 10}
    return out


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3:
        return float("nan")
    rx, ry = pd.Series(x).rank().to_numpy(), pd.Series(y).rank().to_numpy()
    if rx.std() == 0 or ry.std() == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def quality_rank_check(done: pd.DataFrame, bps: float, n_boot: int = 1000) -> dict:
    """Spearman(Quality, realized net R) with a weekly-block bootstrap interval (dependence-adjusted)."""
    if len(done) < 10:
        return {"n": len(done), "spearman": None, "ci90": None}
    rr = r_at(done["entry_price"], done["exit_price"], done["stop"], bps)
    ok = np.isfinite(rr) & done["quality"].notna().to_numpy()
    q, rr, wk = done["quality"].to_numpy(float)[ok], rr[ok], weeks_of(done["decision_date"])[ok]
    rho = spearman(q, rr)
    rng = np.random.default_rng(PROTOCOL["bootstrap"]["seed"])
    uniq = np.unique(wk)
    idx_by = [np.flatnonzero(wk == g) for g in uniq]
    boots = []
    for _ in range(n_boot):
        sel = np.concatenate([idx_by[j] for j in rng.integers(0, len(uniq), len(uniq))])
        v = spearman(q[sel], rr[sel])
        if not math.isnan(v):
            boots.append(v)
    ci = [_f(np.quantile(boots, 0.05), 4), _f(np.quantile(boots, 0.95), 4)] if boots else None
    return {"n": int(ok.sum()), "weeks": len(uniq), "spearman": _f(rho, 4), "ci90": ci,
            "positive_and_significant": bool(ci and ci[0] is not None and ci[0] > 0)}


# ---- baselines (same levels-from-ATR, execution and cost rules) ---------------------------------------------
class BaselineFactory:
    def __init__(self, panel: FeaturePanel, bars: dict[str, pd.DataFrame], stocks: list[str], b: Boundaries,
                 sessions: pd.DatetimeIndex):
        self.sessions = sessions
        self.levels, self.exe = LevelConfig(), ExecConfig(cost_bps_per_side=0.0, horizon_sessions=PROTOCOL["primary_horizon"])
        self.start, self.end = pd.Timestamp(b.decision_start), pd.Timestamp(b.holdout_end)
        self.ctx, self.mask, self.arr, self.res = {}, {}, {}, {}
        for s in stocks:
            ctx = context_frame(panel, s)
            self.ctx[s] = ctx
            self.mask[s] = gate_mask(ctx, panel.meta[s], DatasetSpec(strategies=[]).gates, None)
            bb = bars[s].reindex(ctx.index)
            self.arr[s] = tuple(bb[k].to_numpy(float) for k in ("open", "high", "low", "close"))
            self.res[s] = ctx["close"].rolling(120, min_periods=60).max().shift(1).to_numpy(float)
        self.sector = {s: panel.meta[s].sector for s in stocks}
        self._memo: dict[tuple[str, int], dict | None] = {}

    def eligible(self, d: pd.Timestamp) -> list[str]:
        return [s for s, m in self.mask.items() if d in m.index and bool(m.loc[d])]

    def trade(self, s: str, t: int, name: str) -> dict | None:
        key = (s, t)
        if key not in self._memo:
            ctx = self.ctx[s]
            row = ctx.iloc[t]
            lv = compute_levels(float(row["close"]), float(row["atr_14"]), float(row["close"] - 1.5 * row["atr_14"]),
                                self.res[s][t], self.levels)
            if not isinstance(lv, Levels):
                self._memo[key] = None
            else:
                o, h, low, c = self.arr[s]
                r = simulate(o, h, low, c, t, lv.entry_min, lv.entry_max, lv.stop, lv.target, self.exe)
                idx = ctx.index
                self._memo[key] = {
                    "symbol": s, "sector": self.sector[s], "decision_date": idx[t], "regime": row["regime_label"],
                    "filled": r.filled, "outcome": r.outcome, "return_gross": r.return_gross, "stop": lv.stop,
                    "entry_price": r.entry_price, "exit_price": r.exit_price, "sessions_held": r.sessions_held,
                    "mfe": r.mfe, "mae": r.mae, "ambiguous": r.ambiguous_same_bar, "gap_stop": r.gap_through_stop,
                    "entry_date": idx[r.entry_index] if r.entry_index is not None else pd.NaT,
                    "exit_date": idx[r.exit_index] if r.exit_index is not None
                    else idx[min(len(idx) - 1, t + self.levels.entry_valid_sessions)],
                    "dollar_vol_med_20": float(row["dollar_vol_med_20"]), "quality": np.nan,
                }
        rec = self._memo[key]
        return None if rec is None else rec | {"strategy_id": name}

    def momentum_20(self) -> pd.DataFrame:
        days = [d for d in self.sessions if self.start <= d <= self.end][::5]
        rows = []
        for d in days:
            el = self.eligible(d)
            if el:
                best = max(el, key=lambda s: (np.nan_to_num(self.ctx[s].loc[d, "ret_20"], nan=-9), s))
                rec = self.trade(best, self.ctx[best].index.get_loc(d), "momentum_20")
                if rec:
                    rows.append(rec)
        return dedup(pd.DataFrame(rows)) if rows else pd.DataFrame()

    def breakout_basic(self) -> pd.DataFrame:
        rows = []
        for s, ctx in self.ctx.items():
            fire = (ctx["breakout_today"] == 1) & self.mask[s] & (ctx.index >= self.start) & (ctx.index <= self.end)
            for t in np.flatnonzero(fire.to_numpy()):
                rec = self.trade(s, int(t), "breakout_basic")
                if rec:
                    rows.append(rec)
        return dedup(pd.DataFrame(rows)) if rows else pd.DataFrame()

    def matched_random(self, reference: pd.DataFrame, bps: float) -> dict:
        """Same decision dates and count as the strategy's signals, random gate-passing stocks; distribution of means."""
        cfg = PROTOCOL["matched_random"]
        rng = np.random.default_rng(cfg["seed"])
        dates = list(pd.to_datetime(reference["decision_date"]))
        elig = {d: self.eligible(d) for d in set(dates)}
        means = []
        for _ in range(cfg["draws"]):
            rows = []
            for d in dates:
                el = elig[d]
                if not el:
                    continue
                s = el[int(rng.integers(len(el)))]
                rec = self.trade(s, self.ctx[s].index.get_loc(d), "matched_random")
                if rec:
                    rows.append(rec)
            done = completed(pd.DataFrame(rows)) if rows else pd.DataFrame()
            if len(done):
                means.append(float(net_at(done["return_gross"], bps).mean()))
        if not means:
            return {"draws": 0, "median": None, "p95": None}
        a = np.array(means)
        return {"draws": len(a), "median": _f(np.median(a)), "p05": _f(np.quantile(a, 0.05)),
                "p95": _f(np.quantile(a, 0.95)), "means": a}


# ---- capital-constrained portfolio --------------------------------------------------------------------------
def portfolio(trades: pd.DataFrame, closes: dict[str, pd.Series], sessions: pd.DatetimeIndex, spy: pd.Series) -> dict:
    cfg = PROTOCOL["portfolio"]
    c, slots = cfg["cost_bps"] / 1e4, cfg["slots"]
    t = completed(trades)
    if t.empty:
        return {"trades_taken": 0, "trades_skipped_capacity": 0, "cagr": None, "max_drawdown": None}
    t = t.sort_values(["entry_date", "decision_date", "symbol"])
    days = sessions[(sessions >= t["entry_date"].min()) & (sessions <= t["exit_date"].max())]
    by_entry = {d: g for d, g in t.groupby("entry_date")}
    cash, prev_eq, taken, skipped = 1.0, 1.0, 0, 0
    pos: dict[int, tuple[str, float, pd.Timestamp, float]] = {}
    curve, expo, n_open = [], [], []
    for d in days:
        for i, tr in (by_entry[d].iterrows() if d in by_entry else ()):
            if len(pos) >= slots or any(p[0] == tr["symbol"] for p in pos.values()):
                skipped += 1
                continue
            alloc = min(cash, prev_eq / slots)
            if alloc <= 0:
                skipped += 1
                continue
            pos[i] = (tr["symbol"], alloc / (tr["entry_price"] * (1 + c)), tr["exit_date"], tr["exit_price"])
            cash -= alloc
            taken += 1
        n_open.append(len(pos))
        for k, (_s, units, xd, xp) in list(pos.items()):
            if xd == d:
                cash += units * xp * (1 - c)
                del pos[k]
        val = sum(units * float(closes[s].get(d, np.nan)) for s, units, _x, _p in pos.values())
        eq = cash + val
        curve.append(eq)
        expo.append(val / eq if eq > 0 else 0.0)
        prev_eq = eq
    eq = np.array(curve)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    years = len(days) / 252
    sp = spy.reindex(sessions)
    base = sp[sp.index < days[0]].iloc[-1] if (sp.index < days[0]).any() else sp.loc[days[0]]
    spy_ret = float(sp.loc[days[-1]] / base - 1)
    spy_curve = sp.loc[days].to_numpy(float) / base
    spy_peak = np.maximum.accumulate(np.concatenate([[1.0], spy_curve]))[1:]
    avg_expo = float(np.mean(expo))
    return {
        "slots": slots, "cost_bps_per_side": cfg["cost_bps"], "first_session": str(days[0].date()),
        "last_session": str(days[-1].date()), "sessions": len(days), "trades_taken": taken,
        "trades_skipped_capacity": skipped, "total_return": _f(eq[-1] - 1), "cagr": _f(eq[-1] ** (1 / years) - 1) if years > 0 else None,
        "max_drawdown": _f((eq / peak - 1).min(), 4), "avg_exposure": _f(avg_expo, 4), "max_positions": int(max(n_open)),
        "avg_positions": _f(np.mean(n_open), 2), "spy_total_return_same_period": _f(spy_ret),
        "spy_max_drawdown_same_period": _f((spy_curve / spy_peak - 1).min(), 4),
        "spy_exposure_scaled_return": _f(spy_ret * avg_expo),
        "note": "price-only; SPY buy-and-hold has 100% exposure and is not a fair comparator for a swing book",
    }


def buy_and_hold(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    s = series[(series.index >= start) & (series.index <= end)].astype(float)
    if len(s) < 2:
        return {"total_return": None}
    eq = s / s.iloc[0]
    years = len(s) / 252
    return {"first_session": str(s.index[0].date()), "last_session": str(s.index[-1].date()), "sessions": len(s),
            "total_return": _f(eq.iloc[-1] - 1), "cagr": _f(eq.iloc[-1] ** (1 / years) - 1),
            "max_drawdown": _f((eq / eq.cummax() - 1).min(), 4)}


# ---- model (existing pipeline) and robustness ------------------------------------------------------------------
def model_block(ev: pd.DataFrame, sid: str, b: Boundaries, bias: list[str], data_class: str) -> tuple[dict, dict]:
    """(model report, extras). Runs research.evaluate.evaluate_strategy on the frozen holdout boundary."""
    source = "synthetic_fixture" if data_class != "REAL_MARKET_DATA" else "historical_market_data"
    rep, wf = evaluate_strategy(ev, sid, MODEL_FEATURES, source, policy=PromotionPolicy(), holdout_start=b.holdout_ts,
                                bias_warnings=bias)
    d = rep.to_dict()
    keep = {k: d[k] for k in ("confidence_status", "status_reasons", "gates", "holdout", "threshold",
                              "selected_holdout", "cost_sensitivity", "walk_forward", "shuffled_control",
                              "regime_breakdown", "policy_version")}
    extra: dict = {}
    hold = wf.holdout
    if rep.threshold is not None and not hold.empty and "p_target" in hold:
        sel = hold["p_target"] >= rep.threshold
        tgt = hold["outcome"] == "TARGET"
        extra["precision_target"] = _f((tgt & sel).sum() / sel.sum(), 4) if sel.sum() else None
        extra["recall_target"] = _f((tgt & sel).sum() / tgt.sum(), 4) if tgt.sum() else None
    filled = ev[ev["filled"].astype(bool)]
    dev = filled[filled["decision_date"] < b.holdout_ts]
    tr = _purge(dev, b.holdout_ts, PROTOCOL["split"]["embargo_sessions"])
    extra["samples"] = {"development_filled": len(dev), "final_training_after_purge": len(tr),
                        "final_fit": int(len(tr) * (1 - PROTOCOL["split"]["calibration_fraction"])),
                        "final_calibration": len(tr) - int(len(tr) * (1 - PROTOCOL["split"]["calibration_fraction"])),
                        "holdout_filled": int((filled["decision_date"] >= b.holdout_ts).sum())}
    return keep, extra


def model_without_quality(ev: pd.DataFrame, b: Boundaries) -> dict:
    feats = tuple(f for f in MODEL_FEATURES if f not in QUALITY_FEATURES)
    wf = walk_forward(ev, feats, ValidationConfig(), holdout_start=b.holdout_ts)
    h = wf.holdout_metrics
    return {"features": len(feats), "holdout_n": h.get("n"), "brier_skill": h.get("brier_skill"), "ece": h.get("ece")}


def sector_cross_model(ev: pd.DataFrame, b: Boundaries) -> dict:
    groups = PROTOCOL["robustness_splits"]["sector_groups"]
    filled = ev[ev["filled"].astype(bool)].sort_values("decision_date")
    dev = filled[filled["decision_date"] < b.holdout_ts]
    hold = filled[filled["decision_date"] >= b.holdout_ts]
    cfg = ValidationConfig()
    out = {}
    for train_g, test_g in (("A", "B"), ("B", "A")):
        tr = _purge(dev[dev["sector"].isin(groups[train_g])], b.holdout_ts, cfg.embargo_sessions)
        te = hold[hold["sector"].isin(groups[test_g])]
        key = f"train_{train_g}_test_{test_g}"
        if len(tr) < cfg.min_train or len(te) < 30:
            out[key] = {"train_n": len(tr), "test_n": len(te), "brier_skill": None, "reason": "sample too small"}
            continue
        model = _fit(tr, MODEL_FEATURES, cfg, {"sector_group": train_g})
        p = model.predict(_rows(te, MODEL_FEATURES))[:, 0]
        y = (te["outcome"] == "TARGET").to_numpy(float)
        base = float((tr["outcome"] == "TARGET").mean())
        out[key] = {"train_n": len(tr), "test_n": len(te), "brier_skill": _f(M.brier_skill(p, y, base), 5),
                    "ece": _f(M.ece(p, y), 5)}
    return out


# ---- dispositions --------------------------------------------------------------------------------------------
def holm(pvals: dict[str, float]) -> dict[str, float]:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m, run, out = len(items), 0.0, {}
    for i, (k, p) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[k] = round(run, 4)
    return out


def classify(row: dict, holm_p: float | None, demo: bool) -> tuple[str, list[str]]:
    """Frozen disposition rules (PROTOCOL['dispositions']). Inputs are the primary configuration only."""
    if row["requires_catalysts"]:
        return "NOT_EVALUABLE_MISSING_INPUT", ["not failed alpha: no event can exist without that input"]
    hold, dev = row["holdout"], row["development"]
    n, n_dev = hold.get("completed", 0), dev.get("completed", 0)
    if n < T["min_holdout_trades"] or n_dev < T["min_development_trades"]:
        return "INSUFFICIENT_HISTORY", [f"completed rule trades: holdout {n} (minimum {T['min_holdout_trades']}), "
                                        f"development {n_dev} (minimum {T['min_development_trades']})"]
    mean, ci = hold.get("mean_net"), hold.get("mean_net_ci") or {}
    rnd, sect = row["matched_random"], row["sector_rule"]
    reasons: list[str] = []
    checks = {
        "adequate_n": n >= T["adequate_holdout_trades"],
        "weeks": (hold.get("distinct_weeks") or 0) >= T["min_holdout_weeks"],
        "regimes": (hold.get("regimes") or 0) >= T["min_regimes"],
        "mean_pos_25": mean is not None and mean > 0,
        "p10_pos_25": (ci.get("p10") or -1) > 0,
        "mean_pos_50": (row["holdout_50bps_mean"] or -1) > 0,
        "holm": holm_p is not None and holm_p < T["holm_alpha"],
        "beats_random_p95": rnd.get("p95") is not None and mean is not None and mean > rnd["p95"],
        "both_sectors_pos": all((sect.get(g) or {}).get("mean_net") is not None and sect[g]["mean_net"] > 0 for g in ("A", "B")),
        "forecast_ok": row["forecast_status"] != "UNVALIDATED",
    }
    failed = [k for k, v in checks.items() if not v]
    if not failed and not demo:
        return "VALIDATED_REAL_OOS", ["every frozen validation check passed (research only; forward test still required)"]
    if checks["adequate_n"] and ((ci.get("p90") is not None and ci["p90"] < 0)
                                 or (rnd.get("median") is not None and mean is not None and mean <= rnd["median"])):
        why = "bootstrap p90 of mean net @25bps < 0" if (ci.get("p90") is not None and ci["p90"] < 0) else \
            "mean net @25bps not above the matched-random median"
        return "FAILED_REAL_OOS", [why, f"failed checks: {', '.join(failed) or 'none'}"]
    if checks["mean_pos_25"] and rnd.get("median") is not None and mean > rnd["median"] and (checks["p10_pos_25"] or n < 100):
        reasons = [f"failed checks: {', '.join(failed)}"] + (["synthetic fixture: never validated"] if demo else [])
        return ("UNVALIDATED" if demo else "PROMISING_NEEDS_FORWARD_TEST"), reasons
    return "UNVALIDATED", [f"inconclusive; failed checks: {', '.join(failed)}"]


def recommendation(disposition: str) -> str:
    return {
        "VALIDATED_REAL_OOS": "KEEP (research) — forward paper test before any live use",
        "PROMISING_NEEDS_FORWARD_TEST": "KEEP AS CANDIDATE — forward paper test; no live alerts",
        "FAILED_REAL_OOS": "MODIFY OR RETIRE — redesign entry/exit before re-testing under a new protocol version",
        "UNVALIDATED": "MODIFY / RE-TEST — inconclusive; do not use",
        "INSUFFICIENT_HISTORY": "NOT EVALUABLE YET — too few trades in this universe/window",
        "NOT_EVALUABLE_MISSING_INPUT": "NOT EVALUABLE — needs point-in-time catalyst history",
    }[disposition]


@dataclass
class Analysis:
    strategies: dict
    baselines: dict
    portfolios: dict
    aggregate: dict
    funnel: dict
    trades_rows: list[dict]
    configurations: list[dict]


def run_analysis(
    panel: FeaturePanel,
    bars: dict[str, pd.DataFrame],
    stocks: list[str],
    b: Boundaries,
    bias: list[str],
    data_class: str,
    progress: Callable[[str], None] = lambda _m: None,
) -> Analysis:
    demo = data_class != "REAL_MARKET_DATA"
    costs, horizons, H, C = PROTOCOL["costs_bps_per_side"], PROTOCOL["horizons"], PROTOCOL["primary_horizon"], PROTOCOL["primary_cost_bps"]
    hs = b.holdout_ts
    events: dict[int, pd.DataFrame] = {}
    funnel: dict[str, dict[str, int]] = {}
    for h in horizons:
        spec = DatasetSpec(strategies=list(STRATEGIES.values()), execution=ExecConfig(cost_bps_per_side=0.0, horizon_sessions=h),
                           start=pd.Timestamp(b.decision_start), end=pd.Timestamp(b.holdout_end))
        events[h] = build_events(panel, bars, spec, symbols=stocks, funnel=funnel if h == H else None)
        progress(f"events H={h}: {len(events[h])}")
    seg = lambda df: np.where(df["decision_date"] < hs, "development", "holdout")  # noqa: E731
    closes = {s: bars[s]["close"].astype(float) for s in stocks}
    sessions = pd.DatetimeIndex(bars[BENCHMARK].index)
    spy = bars[BENCHMARK]["close"].astype(float)
    for s in stocks:
        closes[s] = closes[s].reindex(sessions).ffill()
    base = BaselineFactory(panel, bars, stocks, b, sessions)
    rows_csv: list[dict] = []
    configs: list[dict] = []

    def csv_rows(name: str, kind: str, df: pd.DataFrame, horizon: int) -> None:
        for sg in ("development", "holdout"):
            part = df[seg(df) == sg] if not df.empty else df
            for c in costs:
                st = trade_stats(part, c, boot=False)
                rows_csv.append({"name": name, "kind": kind, "segment": sg, "horizon": horizon, "cost_bps_per_side": c,
                                 **{k: st.get(k) for k in ("signals", "filled", "no_fill", "completed", "target_first",
                                                           "stop_first", "time_exit", "ambiguous_same_bar", "gap_through_stop",
                                                           "mean_net", "median_net", "win_rate", "mean_r")}})
                configs.append({"name": name, "variant": kind, "horizon": horizon, "cost_bps": c, "segment": sg})

    out: dict[str, dict] = {}
    for sid, st in STRATEGIES.items():
        spec = st.spec
        evh = {h: e[e["strategy_id"] == sid] if not e.empty else e for h, e in events.items()}
        ev = evh[H]
        row: dict = {"strategy_id": sid, "title": spec.title, "version": spec.version, "hypothesis": spec.hypothesis,
                     "default_enabled": spec.default_enabled, "weak_prior": spec.weak_prior,
                     "requires_catalysts": spec.requires_catalysts, "allowed_regimes": list(spec.allowed_regimes)}
        f = funnel.get(sid, {"triggered": 0, "gate_eligible": 0, "quality_scored": 0, "levels_valid": 0})
        rule_all = dedup(ev[ev["regime_allowed"].astype(bool)]) if len(ev) else ev
        row["funnel"] = {**f, "regime_allowed": int(ev["regime_allowed"].astype(bool).sum()) if len(ev) else 0,
                         "rule_after_dedup": len(rule_all),
                         "filled": int(rule_all["filled"].astype(bool).sum()) if len(rule_all) else 0,
                         "completed_horizon": len(completed(rule_all)) if len(rule_all) else 0,
                         "label_unavailable_sessions": b.label_unavailable_sessions}
        if spec.requires_catalysts or ev.empty:
            row |= {"development": trade_stats(pd.DataFrame(), C), "holdout": trade_stats(pd.DataFrame(), C),
                    "forecast_status": None, "matched_random": {}, "sector_rule": {}, "holdout_50bps_mean": None}
            if spec.requires_catalysts:
                row["not_evaluable_reason"] = "requires catalysts available_at <= decision time; CI has no point-in-time catalyst history"
            out[sid] = row
            progress(f"{sid}: no events")
            continue
        dev, hold = rule_all[seg(rule_all) == "development"], rule_all[seg(rule_all) == "holdout"]
        row["development"] = trade_stats(dev, C)
        row["holdout"] = trade_stats(hold, C)
        row["holdout_cost_grid"] = {f"{c}bps": trade_stats(hold, c, boot=c != C) for c in costs}
        row["holdout_50bps_mean"] = row["holdout_cost_grid"]["50bps"].get("mean_net")
        row["holdout_horizons"] = {}
        for h in horizons:
            ruleh = dedup(evh[h][evh[h]["regime_allowed"].astype(bool)]) if len(evh[h]) else evh[h]
            row["holdout_horizons"][f"H{h}"] = trade_stats(ruleh[seg(ruleh) == "holdout"] if len(ruleh) else ruleh, C, boot=False)
            csv_rows(sid, "rule", ruleh, h)
        hd = completed(hold)
        if len(hd):
            dv = completed(dev)
            edges = np.quantile(dv["dollar_vol_med_20"], [0.25, 0.5, 0.75]) if len(dv) >= 20 else None
            liq = pd.Series(np.searchsorted(edges, hd["dollar_vol_med_20"]) + 1, index=hd.index).map(lambda q: f"Q{q}") \
                if edges is not None else pd.Series("n/a", index=hd.index)
            row["holdout_breakdowns"] = {
                "regime": group_stats(hd, hd["regime"], C), "sector": group_stats(hd, hd["sector"], C),
                "liquidity_quartile_dev_edges": group_stats(hd, liq, C),
                "year": group_stats(hd, pd.to_datetime(hd["decision_date"]).dt.year, C),
                "exit_type": group_stats(hd, hd["outcome"], C),
                "earnings_unknown": group_stats(hd, hd["earnings_unknown"].map(lambda v: "UNKNOWN" if v else "KNOWN"), C),
            }
            dd = completed(dev)
            row["development_breakdowns"] = {"year": group_stats(dd, pd.to_datetime(dd["decision_date"]).dt.year, C)} if len(dd) else {}
        row["quality_rank"] = {"holdout": quality_rank_check(hd, C), "development": quality_rank_check(completed(dev), C)}
        row["spy_same_windows"] = _spy_same_windows(hd, spy, C)
        groups = PROTOCOL["robustness_splits"]["sector_groups"]
        row["sector_rule"] = {g: trade_stats(hold[hold["sector"].isin(groups[g])], C, boot=False) for g in ("A", "B")}
        rnd = base.matched_random(hold, C) if len(hold) else {"draws": 0, "median": None, "p95": None}
        means = rnd.pop("means", None)
        if means is not None and row["holdout"].get("mean_net") is not None:
            rnd["strategy_percentile"] = _f(float((means < row["holdout"]["mean_net"]).mean()), 4)
        row["matched_random"] = rnd
        configs.append({"name": sid, "variant": "matched_random", "horizon": H, "cost_bps": C, "segment": "holdout"})
        model, extra = model_block(ev, sid, b, bias, data_class)
        row["model"] = model | extra
        row["forecast_status"] = model["confidence_status"]
        configs.append({"name": sid, "variant": "model", "horizon": H, "cost_bps": PROTOCOL["primary_cost_bps"], "segment": "holdout"})
        row["sector_cross_model"] = sector_cross_model(ev, b)
        if sid in PROTOCOL["ablations"]:
            allr = dedup(ev)
            qcut = float(np.median(dev["quality"])) if len(dev) else None
            qtop = hold[hold["quality"] >= qcut] if qcut is not None else hold.iloc[0:0]
            row["ablations"] = {
                "all_regimes": trade_stats(allr[seg(allr) == "holdout"], C, boot=False),
                "quality_top_half": {"dev_median_quality": _f(qcut, 3), **trade_stats(qtop, C, boot=False)},
                "model_without_quality_features": model_without_quality(ev, b),
                "model_full_features": {"brier_skill": model["holdout"].get("brier_skill"), "ece": model["holdout"].get("ece")},
            }
            for v in ("all_regimes", "quality_top_half", "model_without_quality_features"):
                configs.append({"name": sid, "variant": v, "horizon": H, "cost_bps": C, "segment": "holdout"})
        row["portfolio"] = portfolio(hold, closes, sessions, spy)
        out[sid] = row
        progress(f"{sid}: holdout completed {row['holdout'].get('completed')} mean_net {row['holdout'].get('mean_net')}")

    # baselines (same levels-from-ATR / execution / costs; primary horizon)
    bl: dict[str, dict] = {}
    for name, df in (("momentum_20", base.momentum_20()), ("breakout_basic", base.breakout_basic())):
        hold = df[seg(df) == "holdout"] if len(df) else df
        bl[name] = {"development": trade_stats(df[seg(df) == "development"] if len(df) else df, C),
                    "holdout": trade_stats(hold, C),
                    "holdout_cost_grid": {f"{c}bps": trade_stats(hold, c, boot=False) for c in costs},
                    "portfolio": portfolio(hold, closes, sessions, spy)}
        csv_rows(name, "baseline", df, H)
    bl["spy_buy_and_hold"] = {"development": buy_and_hold(spy, pd.Timestamp(b.decision_start), hs - pd.Timedelta(days=1)),
                              "holdout": buy_and_hold(spy, hs, pd.Timestamp(b.data_end)),
                              "note": "100% exposure, price-only; NOT a fair comparator for a low-frequency swing system"}
    bl["always_flat"] = {"holdout": {"mean_net": 0.0, "note": "no trades"}}
    progress("baselines done")

    pv = {sid: r["holdout"]["p_one_sided"] for sid, r in out.items()
          if not r["requires_catalysts"] and r["holdout"].get("p_one_sided") is not None
          and r["holdout"].get("completed", 0) >= T["min_holdout_trades"]}
    adj = holm(pv)
    for sid, r in out.items():
        r["holm_adjusted_p"] = adj.get(sid)
        r["disposition"], r["disposition_reasons"] = classify(r, adj.get(sid), demo)
        r["recommendation"] = recommendation(r["disposition"])
    validated = [s for s, r in out.items() if r["disposition"] == "VALIDATED_REAL_OOS"]
    aggregate = {
        "conclusion": "RESEARCH_EDGE_CANDIDATES" if validated else "NO_TRADE",
        "validated": validated,
        "dispositions": {s: r["disposition"] for s, r in out.items()},
        "live_alerts": "DISABLED (research only; no promotion, no group signals, no orders)",
        "multiple_testing": f"Holm adjustment over {len(pv)} evaluable hypotheses at the primary configuration",
    }
    return Analysis(out, bl, {s: r.get("portfolio") for s, r in out.items()}, aggregate, funnel, rows_csv, configs)


def _spy_same_windows(done: pd.DataFrame, spy: pd.Series, bps: float) -> dict:
    if done.empty:
        return {"n": 0, "mean_excess_net": None}
    vals = []
    for e, x in zip(done["entry_date"], done["exit_date"], strict=True):
        before = spy[spy.index < e]  # close of the session before entry -> close of the exit session
        start = before.iloc[-1] if len(before) else spy.asof(e)
        vals.append(float(spy.asof(x) / start - 1))
    sp = np.array(vals)
    r = net_at(done["return_gross"], bps)
    return {"n": len(sp), "mean_spy_same_window": _f(sp.mean()), "mean_excess_net": _f((r - sp).mean()),
            "share_beating_spy": _f(float((r > sp).mean()), 4)}
