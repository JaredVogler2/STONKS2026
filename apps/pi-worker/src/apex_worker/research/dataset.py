"""Point-in-time labeled event dataset for strategies and baselines.

For every session t and symbol, the hypothesis plugins see only features available at t's decision
instant (close + 15 min). Levels come from signals.levels (same code as live). Outcomes are simulated
with research.execution on bars strictly AFTER t. Each row records its label window [entry, exit] so
validation can purge/embargo overlapping samples.

Prices are split-adjusted; returns are price-only (dividends not modeled) — documented limitation.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from apex_worker.features.panel import FeaturePanel
from apex_worker.features.regime import REGIME_STATES
from apex_worker.research.execution import ExecConfig, simulate
from apex_worker.signals.candidates import EXCLUDED_INSTRUMENTS, GateConfig
from apex_worker.signals.context import context_frame
from apex_worker.signals.levels import LevelConfig, Levels, compute_levels
from apex_worker.signals.quality import quality
from apex_worker.signals.strategies.library import Strategy

MODEL_FEATURES = (
    "setup_score", "quality", "g_price", "g_sector", "g_volume", "g_volatility", "g_regime", "g_catalyst",
    "atr_pct_14", "rs_bench_20", "rel_volume_1", "squeeze_prev", "dist_sma20_atr", "regime_p_trending_up",
    "regime_p_risk_off", "rr_net", "stop_dist_atr", "earnings_unknown",
)


@dataclass
class DatasetSpec:
    strategies: list[Strategy]
    gates: GateConfig = GateConfig()
    levels: LevelConfig = LevelConfig()
    execution: ExecConfig = ExecConfig()
    start: pd.Timestamp | None = None
    end: pd.Timestamp | None = None


def gate_mask(ctx: pd.DataFrame, meta, cfg: GateConfig, earnings_within: pd.Series | None) -> pd.Series:
    if meta is None or meta.asset_kind == "benchmark" or meta.exchange == "OTC" or meta.leveraged \
            or meta.instrument in EXCLUDED_INSTRUMENTS or meta.tradability_status in ("excluded",):
        return pd.Series(False, index=ctx.index)
    ok = ((ctx["close"] >= cfg.min_price) & (ctx["dollar_vol_med_20"] >= cfg.min_dollar_volume)
          & (ctx["avg_vol_20"] >= cfg.min_avg_volume) & (ctx["bars_available"] >= cfg.min_history)
          & ~(ctx["dist_sma20_atr"] > cfg.max_extension_atr)
          & ((ctx["regime_p_risk_off"].fillna(0) + ctx["regime_p_volatility_shock"].fillna(0)) < cfg.risk_off_breaker))
    if earnings_within is not None and cfg.earnings_policy == "veto_within_horizon":
        ok &= ~earnings_within.reindex(ctx.index).fillna(False).astype(bool)
    return ok.fillna(False)


def earnings_within_series(index: pd.DatetimeIndex, earnings_dates: Iterable[str] | None, horizon: int) -> pd.Series | None:
    """True at t if a CONFIRMED earnings session lies in (t, t + horizon sessions]. None = schedule unknown."""
    if earnings_dates is None:
        return None
    pos = np.searchsorted(index.values, pd.to_datetime(sorted(earnings_dates)).values)
    flag = np.zeros(len(index), dtype=bool)
    for p in pos:
        lo = max(0, p - horizon)
        flag[lo:p] = True  # decision sessions t with p in (t, t+horizon]
    return pd.Series(flag, index=index)


def _row_features(row: pd.Series, setup: float, q, lv: Levels, earnings_unknown: bool) -> dict:
    atr = float(row["atr_14"])
    return {
        "setup_score": setup, "quality": q.score, **{f"g_{k}": v for k, v in q.groups.items()},
        "atr_pct_14": row.get("atr_pct_14"), "rs_bench_20": row.get("rs_bench_20"), "rel_volume_1": row.get("rel_volume_1"),
        "squeeze_prev": row.get("squeeze_prev"), "dist_sma20_atr": row.get("dist_sma20_atr"),
        "regime_p_trending_up": row.get("regime_p_trending_up"), "regime_p_risk_off": row.get("regime_p_risk_off"),
        "rr_net": lv.reward_to_risk_net, "stop_dist_atr": (lv.mid - lv.stop) / atr if atr > 0 else None,
        "earnings_unknown": 1.0 if earnings_unknown else 0.0,
    }


def build_events(panel: FeaturePanel, bars: dict[str, pd.DataFrame], spec: DatasetSpec,
                 catalysts: Iterable[dict] = (), earnings: dict[str, list[str]] | None = None,
                 symbols: list[str] | None = None, progress: Callable[[str], None] | None = None) -> pd.DataFrame:
    cats = list(catalysts)
    rows: list[dict] = []
    for symbol in sorted(symbols or panel.features):
        meta = panel.meta.get(symbol)
        if meta is None or meta.asset_kind == "benchmark":
            continue
        ctx = context_frame(panel, symbol, cats)
        idx = pd.DatetimeIndex(ctx.index)
        ew = earnings_within_series(idx, (earnings or {}).get(symbol), spec.gates.horizon_sessions) \
            if earnings is not None else None
        ok = gate_mask(ctx, meta, spec.gates, ew)
        window = pd.Series(True, index=idx)
        if spec.start is not None:
            window &= idx >= spec.start
        if spec.end is not None:
            window &= idx <= spec.end
        b = bars[symbol].reindex(idx)
        o, h, low, c = (b[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        resistance = ctx["close"].rolling(120, min_periods=60).max().shift(1)
        for strat in spec.strategies:
            if strat.spec.requires_catalysts and not any(x.get("symbol") == symbol for x in cats):
                continue
            trig = strat.trigger_frame(ctx)
            fire = trig["trigger"] & ok & window
            regime_ok = ctx["regime_label"].isin(list(strat.spec.allowed_regimes))
            for t in np.flatnonzero(fire.to_numpy()):
                row = ctx.iloc[t]
                setup = float(trig["setup_score"].iloc[t])
                earnings_unknown = ew is None
                q = quality(row.to_dict(), None if np.isnan(setup) else setup, "UNKNOWN" if earnings_unknown else
                            "NONE_WITHIN_HORIZON_CONFIRMED")
                if q.score is None:
                    continue
                lv = compute_levels(float(row["close"]), float(row["atr_14"]), trig["invalidation_hint"].iloc[t],
                                    resistance.iloc[t], spec.levels)
                if not isinstance(lv, Levels):
                    continue
                res = simulate(o, h, low, c, t, lv.entry_min, lv.entry_max, lv.stop, lv.target, spec.execution)
                rec = {
                    "symbol": symbol, "sector": meta.sector, "strategy_id": strat.strategy_id,
                    "decision_date": idx[t], "decision_index": t, "regime": row["regime_label"],
                    "regime_allowed": bool(regime_ok.iloc[t]), "entry_min": lv.entry_min, "entry_max": lv.entry_max,
                    "stop": lv.stop, "target": lv.target, "filled": res.filled, "outcome": res.outcome,
                    "entry_date": idx[res.entry_index] if res.entry_index is not None else pd.NaT,
                    "exit_date": idx[res.exit_index] if res.exit_index is not None else idx[min(len(idx) - 1, t + spec.levels.entry_valid_sessions)],
                    "return_net": res.return_net, "return_gross": res.return_gross, "r_multiple": res.r_multiple,
                    "mfe": res.mfe, "mae": res.mae, "sessions_held": res.sessions_held, "ambiguous": res.ambiguous_same_bar, "gap_stop": res.gap_through_stop,
                    **_row_features(row, setup, q, lv, earnings_unknown),
                }
                rows.append(rec)
        if progress:
            progress(symbol)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["decision_date", "symbol", "strategy_id"]).reset_index(drop=True)
    return df


def baseline_events(panel: FeaturePanel, bars: dict[str, pd.DataFrame], spec: DatasetSpec, reference: pd.DataFrame,
                    seed: int = 7) -> dict[str, pd.DataFrame]:
    """Matched baselines evaluated with the SAME levels and execution rules.

    - random_qualified: for every reference event date, a random gate-passing symbol on that date
    - momentum_20: top cross-sectional 20-session return each reference date
    - breakout_basic: any gate-passing close above the prior 20-session high (no other filters)
    """
    rng = np.random.default_rng(seed)
    ctxs = {s: context_frame(panel, s) for s in panel.features
            if panel.meta.get(s) and panel.meta[s].asset_kind != "benchmark"}
    masks = {s: gate_mask(ctx, panel.meta[s], spec.gates, None) for s, ctx in ctxs.items()}
    out: dict[str, list[dict]] = {"random_qualified": [], "momentum_20": [], "breakout_basic": []}
    dates = sorted(set(reference["decision_date"])) if not reference.empty else []

    def run(symbol: str, t: int, name: str) -> None:
        ctx = ctxs[symbol]
        row = ctx.iloc[t]
        lv = compute_levels(float(row["close"]), float(row["atr_14"]), float(row["close"] - 1.5 * row["atr_14"]),
                            ctx["close"].rolling(120, min_periods=60).max().shift(1).iloc[t], spec.levels)
        if not isinstance(lv, Levels):
            return
        b = bars[symbol].reindex(ctx.index)
        res = simulate(*(b[k].to_numpy(float) for k in ("open", "high", "low", "close")), t, lv.entry_min, lv.entry_max,
                       lv.stop, lv.target, spec.execution)
        idx = ctx.index
        out[name].append({"symbol": symbol, "decision_date": idx[t], "filled": res.filled, "outcome": res.outcome,
                          "return_net": res.return_net, "return_gross": res.return_gross, "r_multiple": res.r_multiple,
                          "entry_date": idx[res.entry_index] if res.entry_index is not None else pd.NaT,
                          "exit_date": idx[res.exit_index] if res.exit_index is not None else pd.NaT})

    for d in dates:
        eligible = [s for s, m in masks.items() if d in m.index and bool(m.loc[d])]
        if not eligible:
            continue
        pick = eligible[int(rng.integers(len(eligible)))]
        run(pick, ctxs[pick].index.get_loc(d), "random_qualified")
        best = max(eligible, key=lambda s: (np.nan_to_num(ctxs[s].loc[d, "ret_20"], nan=-9), s))
        run(best, ctxs[best].index.get_loc(d), "momentum_20")
    for s, ctx in ctxs.items():
        fire = (ctx["breakout_today"] == 1) & masks[s]
        if spec.start is not None:
            fire &= ctx.index >= spec.start
        if spec.end is not None:
            fire &= ctx.index <= spec.end
        for t in np.flatnonzero(fire.to_numpy())[::3]:  # thin to keep comparable sample sizes
            run(s, int(t), "breakout_basic")
    return {k: pd.DataFrame(v) for k, v in out.items()}


def benchmark_window_returns(bench: pd.DataFrame, events: pd.DataFrame) -> pd.Series:
    """Benchmark buy-and-hold return over each event's actual holding window (for excess return)."""
    c = bench["close"]
    vals = []
    for _, e in events.iterrows():
        if not e["filled"] or pd.isna(e["entry_date"]) or pd.isna(e["exit_date"]):
            vals.append(np.nan)
            continue
        prev = c.loc[:e["entry_date"]]
        start = prev.iloc[-2] if len(prev) > 1 else prev.iloc[-1]
        vals.append(float(c.loc[e["exit_date"]] / start - 1))
    return pd.Series(vals, index=events.index)


def data_hash(bars: dict[str, pd.DataFrame]) -> str:
    h = hashlib.sha256()
    for s in sorted(bars):
        h.update(s.encode())
        h.update(np.ascontiguousarray(bars[s][["open", "high", "low", "close", "volume"]].to_numpy(float)).tobytes())
    return h.hexdigest()


REGIME_COLUMNS = tuple(f"regime_p_{s}" for s in REGIME_STATES)
