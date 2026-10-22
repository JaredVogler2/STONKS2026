"""realeq-protocol-2 study: S1-S5, legacy comparators, matched controls, DEV-only arm selection, one PRESAMPLE look.

Pipeline (every rule is a value in protocol_v2.PROTOCOL_V2):
  bars -> panel (membership-masked ranks/breadth) -> context -> live gates & membership -> v2 features -> Market
  -> per strategy and arm: signals, levels at 25 bps, simulate_v2, quarantine, de-dup, same-session matched controls
  -> DEV: arm selection (paired mean, generic control) -> PRESAMPLE: conservative block test of the selected arm, Holm
  across S1/S2/S3/S5 -> dispositions; plus mechanism contrasts, books, sensitivities, legacy H rules, baselines and
  engine self-checks. No raw price leaves this module: rows hold returns, flags and indexes only.
"""

from __future__ import annotations

import hashlib
import json
import math
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from apex_worker.features.panel import build_panel
from apex_worker.features.quant_v2 import QV2_FEATURE_VERSION, add_v2_features
from apex_worker.research import inference as INF
from apex_worker.research.book import run_book
from apex_worker.research.controls import Market, matched_controls, per_signal_net, simulate_signal, stop_distance_atr
from apex_worker.research.dataset import gate_mask
from apex_worker.research.execution_v2 import (
    EXECUTION_VERSION,
    Costs,
    EntryPolicy,
    ExecutionPolicy,
    ExitPolicy,
    TradeV2,
)
from apex_worker.research.realeq import universe_v2 as U
from apex_worker.research.realeq.protocol_v2 import PROTOCOL_V2, BoundariesV2
from apex_worker.research.training_data import BENCHMARK, SECTOR_ETFS
from apex_worker.signals.candidates import GateConfig
from apex_worker.signals.context import context_frame
from apex_worker.signals.levels import LevelConfig, Levels, compute_levels
from apex_worker.signals.strategies.library import STRATEGIES
from apex_worker.signals.strategies.quant_v2 import (
    QV2_STRATEGIES,
    S5Config,
    allocation_returns,
    retest_sessions,
    s5_weights,
)

P = PROTOCOL_V2
C = P["primary_cost_bps"]
COSTS = P["costs_bps_per_side"]
K = P["controls"]["k"]
MEE = P["economic_min_effect"]["per_signal"]
SEGMENTS = ("PRESAMPLE", "DEV")
EVIDENCE = {"PRESAMPLE": P["chronology"]["presample"]["evidence_type"], "DEV": P["chronology"]["dev"]["role"]}
SMALL_N = 30  # below this a descriptive value is flagged small_sample (reported, never a gate)


def _f(x, nd: int = 6):
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else round(v, nd)


def arm_policy(arm: str, trade_through: int = 0) -> ExecutionPolicy:
    ex = P["execution"]
    entry = "E1" if arm.startswith("E1") else "E0"
    exit_ = arm[-2:]
    return ExecutionPolicy(EntryPolicy(entry, ex["entry_valid_sessions"], ex["e1_below_close_atr"], trade_through),
                           ExitPolicy(exit_, ex["horizon_sessions"], ex["x2_trail_atr"]), Costs(C))


LEVELS = LevelConfig(cost_bps_per_side=C)


# ---- preparation ------------------------------------------------------------------------------------------------------
@dataclass
class Prepared:
    sessions: pd.DatetimeIndex
    frames: dict[str, pd.DataFrame]
    market: Market
    eligible: dict[str, np.ndarray]
    etf_features: dict[str, pd.DataFrame]
    regime: pd.Series
    bars: dict[str, pd.DataFrame]
    stocks: list[str]
    sector: dict[str, str | None]
    member_counts: dict[str, int] = field(default_factory=dict)
    open_sessions: np.ndarray | None = None  # sessions on which the live gates admit at least one stock


def prepare(bars: dict[str, pd.DataFrame], progress: Callable[[str], None] = lambda _m: None) -> Prepared:
    meta_all = U.u1_meta()
    meta = {s: m for s, m in meta_all.items() if s in bars}
    sessions = pd.DatetimeIndex(bars[BENCHMARK].index)
    stocks = sorted(s for s, m in meta.items() if m.asset_kind == "stock")
    member = U.membership(sessions, stocks)
    etfs = {k: v for k, v in SECTOR_ETFS.items() if v in bars}
    panel = build_panel(bars, meta, BENCHMARK, etfs, membership=member)
    progress(f"panel: {len(panel.features)} symbols, {len(sessions)} sessions")
    gates = GateConfig()
    ctx, elig = {}, {}
    for s in stocks:
        c = context_frame(panel, s)
        ctx[s] = c
        g = gate_mask(c, meta[s], gates, None).reindex(sessions).fillna(False).astype(bool)
        elig[s] = g & member[s].reindex(sessions).fillna(False).astype(bool)
    sector = {s: meta[s].sector for s in stocks}
    lows = {s: bars[s]["low"].astype(float) for s in stocks}
    highs = {s: bars[s]["high"].astype(float) for s in stocks}
    frames = add_v2_features(ctx, elig, sector, sessions, bars[BENCHMARK]["close"].astype(float),
                             {sec: bars[etf]["close"].astype(float) for sec, etf in etfs.items()}, lows, highs)
    progress("v2 features computed")
    market = Market.from_frames(sessions, bars, frames, elig, sector)
    counts = {str(y): int(np.sum([elig[s][(sessions.year == y)].any() for s in stocks])) for y in sorted(set(sessions.year))}
    el = {s: elig[s].to_numpy(bool) for s in stocks}
    open_ = np.any(np.vstack(list(el.values())), axis=0) if el else np.zeros(len(sessions), bool)
    return Prepared(sessions, frames, market, el, {etf: panel.features[etf].astype(float) for etf in etfs.values()},
                    panel.regime["label"].reindex(sessions), bars, stocks, sector, counts, open_)


def segment_of(t: int, b: BoundariesV2) -> str | None:
    if b.presample_range[0] <= t <= b.presample_range[1]:
        return "PRESAMPLE"
    if b.dev_range[0] <= t <= b.dev_range[1]:
        return "DEV"
    return None


def _seed(*parts) -> int:
    return zlib.crc32("|".join(map(str, parts)).encode()) ^ P["controls"]["seed"]


# ---- signals ------------------------------------------------------------------------------------------------------------
@dataclass
class Signal:
    symbol: str
    t: int
    hint: float | None
    priority: float
    opportunity: int = -1


def signals_for(strat, pre: Prepared, b: BoundariesV2) -> tuple[list[Signal], dict]:
    """Signals on PRESAMPLE and DEV decision sessions. Counts are per segment, so an unselected arm's PRESAMPLE
    counts can be withheld."""
    out = []
    counts = {"triggered": dict.fromkeys(SEGMENTS, 0), "eligible_in_window": dict.fromkeys(SEGMENTS, 0)}
    for s in pre.stocks:
        tf = strat.trigger_frame(pre.frames[s])
        trig = tf["trigger"].to_numpy(bool)
        for seg in SEGMENTS:
            lo, hi = seg_range(b, seg)
            counts["triggered"][seg] += int(trig[lo : hi + 1].sum())
        if strat.spec.allowed_regimes:
            reg = pre.frames[s]["regime_label"].isin(list(strat.spec.allowed_regimes)).to_numpy(bool)
            trig = trig & reg
        fire = trig & pre.eligible[s]
        hint = tf["invalidation_hint"].to_numpy(float)
        pri = tf["setup_score"].to_numpy(float)
        opp = tf["opportunity"].to_numpy(int) if "opportunity" in tf else np.full(len(trig), -1)
        for t in np.flatnonzero(fire):
            seg = segment_of(int(t), b)
            if seg is None:
                continue
            counts["eligible_in_window"][seg] += 1
            out.append(Signal(s, int(t), None if not math.isfinite(hint[t]) else float(hint[t]),
                              float(pri[t]) if math.isfinite(pri[t]) else 0.0, int(opp[t])))
    out.sort(key=lambda g: (g.t, g.symbol))
    return out, counts


def quarantined(symbol: str, t: int, tr: TradeV2, sessions: pd.DatetimeIndex, q: dict[str, list[pd.Timestamp]],
                valid: int) -> bool:
    dates = q.get(symbol)
    if not dates:
        return False
    end = tr.pess.exit_index if tr.filled and tr.pess else min(len(sessions) - 1, t + valid)
    a, z = sessions[t], sessions[end]
    return any(a <= d <= z for d in dates)


@dataclass
class ArmRun:
    rows: pd.DataFrame
    funnel: dict


def run_arm(name: str, arm: str, sigs: list[Signal], pre: Prepared, b: BoundariesV2, q: dict, pools: dict | None = None,
            stop_k: float | None = None, controls: bool = True, self_check: bool = False) -> ArmRun:
    """Simulate, quarantine, de-dup and pair one strategy arm. `pools` = {name: bool matrix} of mechanism pools."""
    pol, pol_tt = arm_policy(arm), arm_policy(arm, P["execution"]["touch_fill_sensitivity_trade_through_ticks"])
    valid = P["execution"]["entry_valid_sessions"]
    m, sessions = pre.market, pre.sessions
    same_day: dict[int, set[str]] = {}
    for g in sigs:
        same_day.setdefault(g.t, set()).add(g.symbol)
    busy: dict[str, int] = {}
    rows = []
    funnel = {seg: {"signals": 0, "levels_rejected": 0, "rejected_at_25_passing_at_10": 0, "quarantined": 0,
                    "label_crosses_dev": 0, "dedup_skipped": 0, "incomplete_label": 0, "kept": 0} for seg in SEGMENTS}
    dev0 = b.dev_range[0]

    def crosses(tr: TradeV2, t: int) -> bool:
        """A PRESAMPLE trade must exit before the first DEV session (a delayed time exit could reach it)."""
        return segment_of(t, b) == "PRESAMPLE" and tr.filled and max(tr.pess.exit_index, tr.opt.exit_index) >= dev0

    def accept(sym: str, tr: TradeV2, t: int) -> bool:
        return not quarantined(sym, t, tr, sessions, q, valid) and not crosses(tr, t)

    for g in sigs:
        fs = funnel[segment_of(g.t, b)]
        fs["signals"] += 1
        res = simulate_signal(m, g.symbol, g.t, pol, LEVELS, invalidation_hint=g.hint, stop_distance_atr=stop_k)
        if res is None:
            fs["levels_rejected"] += 1
            close, atr = float(m.c[g.symbol][g.t]), float(m.atr[g.symbol][g.t])
            if pol.exit.uses_target and math.isfinite(close) and math.isfinite(atr) and isinstance(
                    compute_levels(close, atr, g.hint, float(m.resistance[g.symbol][g.t]), LevelConfig(),
                                   stop_distance_atr=stop_k), Levels):
                fs["rejected_at_25_passing_at_10"] += 1
            continue
        tr = res.trade
        if quarantined(g.symbol, g.t, tr, sessions, q, valid):
            fs["quarantined"] += 1
            continue
        if crosses(tr, g.t):
            fs["label_crosses_dev"] += 1
            continue
        if g.t <= busy.get(g.symbol, -1):
            fs["dedup_skipped"] += 1
            continue
        busy[g.symbol] = tr.pess.exit_index if tr.filled else g.t + valid
        v = per_signal_net(tr, pol)
        if v is None:
            fs["incomplete_label"] += 1
            continue
        fs["kept"] += 1
        atr = float(m.atr[g.symbol][g.t])
        k_atr = stop_distance_atr(res.levels, atr)
        row = {"symbol": g.symbol, "sector": pre.sector.get(g.symbol), "t": g.t, "segment": segment_of(g.t, b),
               "regime": str(pre.frames[g.symbol]["regime_label"].iloc[g.t]), "priority": g.priority,
               "opportunity": g.opportunity, "u0": g.symbol in U0, "filled": tr.filled, "fill_kind": tr.fill_kind,
               "ambiguity": tr.ambiguity, "outcome": tr.pess.outcome if tr.filled else tr.status,
               "outcome_opt": tr.opt.outcome if tr.filled else tr.status, "k_atr": k_atr,
               "held": tr.sessions_held() if tr.filled else None, "value": v, "value_opt": per_signal_net(tr, pol, "opt"),
               "mfe_lo": tr.mfe_bounds[0] if tr.filled else None, "mfe_hi": tr.mfe_bounds[1] if tr.filled else None,
               "mae_lo": tr.mae_bounds[0] if tr.filled else None, "mae_hi": tr.mae_bounds[1] if tr.filled else None,
               "entry_index": tr.entry_index, "exit_index": tr.pess.exit_index if tr.filled else None,
               "entry_price": tr.entry_price, "exit_price": tr.pess.exit_price if tr.filled else None}
        for c in COSTS:
            cc = ExecutionPolicy(pol.entry, pol.exit, Costs(c))
            row[f"value_{c}"], row[f"value_opt_{c}"] = per_signal_net(tr, cc), per_signal_net(tr, cc, "opt")
        tt = simulate_signal(m, g.symbol, g.t, pol_tt, LEVELS, invalidation_hint=g.hint, stop_distance_atr=stop_k)
        tt_ok = tt is not None and accept(g.symbol, tt.trade, g.t)  # the re-simulated window is quarantined too
        row["value_tt"] = per_signal_net(tt.trade, pol_tt) if tt_ok else None
        row["value_tt_opt"] = per_signal_net(tt.trade, pol_tt, "opt") if tt_ok else None
        if controls:
            excl = frozenset(same_day.get(g.t, ()))
            for pool_name, pool in [("generic", None), *((pools or {}).items())]:
                rng = np.random.default_rng(_seed(name, arm, pool_name, g.symbol, g.t))
                cs = matched_controls(m, g.symbol, g.t, k_atr, pol, LEVELS, rng, k=K, pool=pool, exclude=excl,
                                      min_stratum=P["controls"]["min_stratum"], accept=lambda s, tr2, t=g.t: accept(s, tr2, t))
                pre_ = "ctrl" if pool_name == "generic" else f"mech_{pool_name}"
                row[f"{pre_}_value"], row[f"{pre_}_value_opt"] = cs.value_pess, cs.value_opt
                row[f"{pre_}_n"], row[f"{pre_}_match"] = cs.n, cs.match
                if pool_name == "generic":
                    for c in COSTS:
                        cc = ExecutionPolicy(pol.entry, pol.exit, Costs(c))
                        vals = [per_signal_net(x, cc) for x in cs.trades]
                        vals_o = [per_signal_net(x, cc, "opt") for x in cs.trades]
                        row[f"ctrl_value_{c}"] = float(np.mean(vals)) if vals else None
                        row[f"ctrl_value_opt_{c}"] = float(np.mean(vals_o)) if vals_o else None
                    row["ctrl_symbols"] = cs.symbols
                    row["ctrl_trades"] = cs.trades
                    tt_vals, tt_vals_o = [], []
                    for s2 in cs.symbols:
                        r2 = simulate_signal(m, s2, g.t, pol_tt, LEVELS, stop_distance_atr=k_atr)
                        if r2 is not None and accept(s2, r2.trade, g.t):
                            tt_vals.append(per_signal_net(r2.trade, pol_tt))
                            tt_vals_o.append(per_signal_net(r2.trade, pol_tt, "opt"))
                    tt_vals = [x for x in tt_vals if x is not None]
                    tt_vals_o = [x for x in tt_vals_o if x is not None]
                    row["ctrl_value_tt"] = float(np.mean(tt_vals)) if tt_vals else None
                    row["ctrl_value_tt_opt"] = float(np.mean(tt_vals_o)) if tt_vals_o else None
        rows.append(row)
    df = pd.DataFrame(rows)
    if self_check:
        funnel["self_check"] = True
    return ArmRun(df, funnel)


U0 = frozenset(U.u0_stocks())


# ---- inference helpers ----------------------------------------------------------------------------------------------
def block_decision(values, idx, sessions: int, n_boot: int | None = None) -> dict:
    """The protocol-2 decision statistic (conservative over the decision block lengths) plus the L=60 sensitivity."""
    inf = P["inference"]
    nb = n_boot or inf["n_boot"]
    res = INF.block_tests(values, idx, sessions, lengths=tuple(inf["block_lengths"]), n_boot=nb, seed=inf["seed"],
                          min_blocks=inf["min_blocks"])
    ups = [res[f"L{L}"]["ci90"][1] for L in inf["block_lengths"] if res[f"L{L}"]["ci90"]]
    res["summary"]["upper_95_max"] = max(ups) if len(ups) == len(inf["block_lengths"]) else None
    sens = INF.block_test(values, idx, sessions, block_len=inf["sensitivity_block_length"], n_boot=nb, seed=inf["seed"],
                          min_blocks=inf["min_blocks"])
    res["sensitivity"] = {f"L{inf['sensitivity_block_length']}": sens.to_dict()}
    return res


def dev_t(test: dict) -> float | None:
    """Selection score: the SMALLEST studentized statistic over the decision block lengths (None if not testable)."""
    if test["summary"]["status"] != "OK":
        return None
    ts = [test[f"L{L}"]["t"] for L in P["inference"]["block_lengths"]]
    return min(ts) if all(t is not None for t in ts) else None


Z_MDE = 2.2414 + 0.8416  # one-sided alpha 0.05 / 4 (Holm's first step over the primary family) and 80% power


def power_report(dev: dict, pres: dict, mee: float = MEE) -> dict:
    """Minimum detectable effect from DEV variance (projected to the PRESAMPLE n) and from the realised PRESAMPLE SE."""
    def se_max(t: dict) -> float | None:
        ses = [t.get(f"L{L}", {}).get("se") for L in P["inference"]["block_lengths"]]
        return max(ses) if ses and all(x is not None for x in ses) else None

    sd, sp = se_max(dev), se_max(pres)
    nd, npres = dev.get("n") or 0, pres.get("n") or 0
    proj = sd * math.sqrt(nd / npres) if sd is not None and nd and npres else None
    mde = Z_MDE * sp if sp is not None else None
    return {"z": Z_MDE, "dev_se": _f(sd, 8), "dev_n": nd, "presample_n": npres,
            "mde_projected_from_dev": _f(Z_MDE * proj, 7) if proj is not None else None,
            "mde_presample_realised": _f(mde, 7), "economic_minimum": mee,
            "underpowered_for_economic_minimum": None if mde is None else bool(mde > mee),
            "note": "an MDE above the economic minimum means a true edge of that size would most likely read "
            "INCONCLUSIVE, not VALIDATED"}


def seg_range(b: BoundariesV2, seg: str) -> tuple[int, int]:
    return b.presample_range if seg == "PRESAMPLE" else b.dev_range


OPEN: np.ndarray | None = None  # set by run_study: the gate-open session mask that defines the test's time axis


def paired_test(df: pd.DataFrame, b: BoundariesV2, seg: str, a: str = "value", c: str = "ctrl_value") -> dict:
    """Block test of value - control over the segment. Time axis: the segment's GATE-OPEN sessions (a market-wide
    risk-off breaker that closes every stock is not missing evidence). Signals only occur on open sessions."""
    lo, hi = seg_range(b, seg)
    part = df[(df["segment"] == seg)] if len(df) else df
    if part.empty or c not in part:
        return {"n": 0, "summary": {"status": "EMPTY", "mean": None, "p_one_sided_max": None, "lower_95_min": None}}
    ok = part[a].notna() & part[c].notna()
    d = (part.loc[ok, a] - part.loc[ok, c]).to_numpy(float)
    t = part.loc[ok, "t"].to_numpy(int)
    open_ = OPEN[lo : hi + 1] if OPEN is not None else np.ones(hi - lo + 1, bool)
    open_ = open_.copy()
    open_[t - lo] = True  # defensive: a signal's own session is always on the axis
    pos = np.cumsum(open_) - 1
    res = block_decision(d, pos[t - lo], int(open_.sum()))
    res["time_axis"] = {"segment_sessions": hi - lo + 1, "gate_open_sessions": int(open_.sum())}
    res["n"] = int(ok.sum())
    res["dropped_missing_control"] = int((~ok).sum())
    return res


def _dist(v: pd.Series) -> dict:
    """Win/loss distribution of per-trade returns. Wins > 0, losses < 0; flags samples below SMALL_N."""
    v = pd.to_numeric(v, errors="coerce").dropna()
    if v.empty:
        return {"n": 0, "value": "UNAVAILABLE_NO_TRADES"}
    w, lo = v[v > 0], v[v < 0]
    return {"n": len(v), "mean": _f(v.mean()), "median": _f(v.median()), "p10": _f(v.quantile(0.1)),
            "p90": _f(v.quantile(0.9)), "win_rate": _f(len(w) / len(v), 4), "avg_win": _f(w.mean()) if len(w) else None,
            "avg_loss": _f(lo.mean()) if len(lo) else None,
            "payoff_ratio": _f(w.mean() / -lo.mean(), 3) if len(w) and len(lo) else None,
            "small_sample": len(v) < SMALL_N}


def _shares(x: pd.Series, total: int) -> dict:
    return {str(k): {"n": int(v), "share": _f(v / total, 4)} for k, v in x.value_counts().items()} if total else {}


def describe(df: pd.DataFrame, seg: str, a: str = "value", c: str = "ctrl_value", initial: int | None = None) -> dict:
    """Descriptive economics of one segment (never a gate except the fields classify_v2 names)."""
    part = df[df["segment"] == seg] if len(df) else df
    if part.empty:
        return {"signals": 0, "evidence_type": EVIDENCE[seg]}
    filled = part[part["filled"].astype(bool)]
    ok = part[a].notna() & part[c].notna() if c in part else part[a].notna()
    d = (part.loc[ok, a] - part.loc[ok, c]) if c in part else pd.Series(dtype=float)
    sec = part["sector"].fillna("unknown").value_counts(normalize=True)
    out = {
        "evidence_type": EVIDENCE[seg],
        "signals": len(part), "filled": len(filled), "fill_rate": _f(len(filled) / len(part), 4),
        "mean_per_signal_net": _f(part[a].mean()), "mean_per_filled_net": _f(filled[a].mean()) if len(filled) else None,
        "control_mean_per_signal_net": _f(part[c].mean()) if c in part else None,
        "mean_paired": _f(d.mean()) if len(d) else None, "median_paired": _f(d.median()) if len(d) else None,
        "share_paired_positive": _f((d > 0).mean(), 4) if len(d) else None,
        "distinct_sessions": int(part["t"].nunique()), "distinct_symbols": int(part["symbol"].nunique()),
        "regimes": {str(k): int(v) for k, v in part["regime"].value_counts().items()},
        "ambiguous_share_of_fills": _f((filled["ambiguity"] != "NONE").mean(), 4) if len(filled) else None,
        "ambiguity_types": {str(k): int(v) for k, v in filled["ambiguity"].value_counts().items()} if len(filled) else {},
        "outcomes": {str(k): int(v) for k, v in part["outcome"].value_counts().items()},
        "exit_shares_of_fills": {"pessimistic": _shares(filled["outcome"], len(filled)),
                                 "optimistic": _shares(filled["outcome_opt"], len(filled))} if len(filled) else {},
        "no_fill_share_of_signals": _f((part["outcome"] == "NO_FILL").mean(), 4),
        "exit_outcomes_resolved": ({**{str(k): int(v) for k, v in
                                       filled.loc[filled["ambiguity"] == "NONE", "outcome"].value_counts().items()},
                                    "UNRESOLVED_DAILY_BAR_AMBIGUITY": int((filled["ambiguity"] != "NONE").sum())}
                                   if len(filled) else {}),
        "mean_sessions_held": _f(filled["held"].astype(float).mean(), 2) if len(filled) else None,
        "mfe_bounds_mean": [_f(filled["mfe_lo"].mean()), _f(filled["mfe_hi"].mean())] if len(filled) else None,
        "mae_bounds_mean": [_f(filled["mae_lo"].mean()), _f(filled["mae_hi"].mean())] if len(filled) else None,
        "per_filled_trade": {"net_25bps": _dist(filled[a]) if len(filled) else _dist(pd.Series(dtype=float)),
                             "gross_0bps": _dist(filled["value_0"]) if len(filled) and "value_0" in filled
                             else _dist(pd.Series(dtype=float))},
        "sector_concentration": {"sectors": len(sec), "top_sector": str(sec.index[0]),
                                 "top_share": _f(sec.iloc[0], 4), "hhi": _f(float((sec**2).sum()), 4)},
        "small_sample": len(part) < SMALL_N,
    }
    if initial is not None:
        out["initial_signals"] = initial
        out["net_per_initial_signal"] = _f(part[a].sum() / initial) if initial else None
        out["net_per_initial_signal_note"] = "levels-rejected, quarantined and de-duplicated signals count 0"
    if "value_opt" in part:
        out["mean_per_signal_net_optimistic"] = _f(part["value_opt"].mean())
    for cst in COSTS:
        if f"value_{cst}" in part and f"ctrl_value_{cst}" in part:
            dd = part[f"value_{cst}"] - part[f"ctrl_value_{cst}"]
            out[f"mean_paired_{cst}bps"] = _f(dd.mean())
            out[f"mean_per_signal_net_{cst}bps"] = _f(part[f"value_{cst}"].mean())
            out[f"mean_per_filled_net_{cst}bps"] = _f(filled[f"value_{cst}"].mean()) if len(filled) else None
        if f"value_opt_{cst}" in part and f"ctrl_value_opt_{cst}" in part:
            out[f"mean_paired_{cst}bps_optimistic"] = _f((part[f"value_opt_{cst}"] - part[f"ctrl_value_opt_{cst}"]).mean())
    if "value_opt" in part and "ctrl_value_opt" in part:
        out["mean_paired_optimistic"] = _f((part["value_opt"] - part["ctrl_value_opt"]).mean())
    if "value_tt" in part and "ctrl_value_tt" in part:
        out["mean_paired_touch_fill_sensitivity"] = _f((part["value_tt"] - part["ctrl_value_tt"]).mean())
    if "value_tt_opt" in part and "ctrl_value_tt_opt" in part:
        out["mean_paired_touch_fill_sensitivity_optimistic"] = _f((part["value_tt_opt"] - part["ctrl_value_tt_opt"]).mean())
    if "u0" in part and c in part:
        u0 = part[part["u0"].astype(bool) & ok]
        out["u0_subset"] = {"signals": len(u0), "mean_paired": _f((u0[a] - u0[c]).mean()) if len(u0) else None,
                            "small_sample": len(u0) < SMALL_N}
    return out


def market_exposure(df: pd.DataFrame, pre: Prepared, seg: str) -> dict:
    """Descriptive market exposure of filled trades: OLS of the trade's net return on SPY's close-to-close return over
    the same window (decision close to exit close), plus how concentrated signals are in time. The window is an
    approximation (entries and exits are intraday); it never enters a gate."""
    part = df[(df["segment"] == seg) & df["filled"].astype(bool)] if len(df) else df
    out: dict = {"segment": seg, "note": "approximate window (decision close to exit close); descriptive"}
    if len(df) and (df["segment"] == seg).any():
        per = df[df["segment"] == seg]["t"].value_counts()
        k = max(1, math.ceil(0.05 * len(per)))
        out["signals_on_top_5pct_sessions_share"] = _f(per.sort_values(ascending=False).iloc[:k].sum() / per.sum(), 4)
        out["max_signals_one_session"] = int(per.max())
    if len(part) < 10:
        return out | {"beta": None, "value": "INSUFFICIENT_TRADES" if len(part) else "UNAVAILABLE_NO_TRADES"}
    spy = pre.bars[BENCHMARK]["close"].reindex(pre.sessions).ffill().to_numpy(float)
    t, e = part["t"].to_numpy(int), part["exit_index"].astype(int).to_numpy()
    m = spy[e] / spy[t] - 1
    y = part["value"].to_numpy(float)
    ok = np.isfinite(m) & np.isfinite(y)
    if ok.sum() < 10 or float(np.var(m[ok])) <= 0:
        return out | {"beta": None, "value": "INSUFFICIENT_TRADES"}
    beta = float(np.cov(m[ok], y[ok], ddof=1)[0, 1] / np.var(m[ok], ddof=1))
    alpha = float(y[ok].mean() - beta * m[ok].mean())
    return out | {"trades": int(ok.sum()), "beta": _f(beta, 3), "alpha_per_trade": _f(alpha),
                  "mean_spy_window_return": _f(m[ok].mean()), "corr": _f(float(np.corrcoef(m[ok], y[ok])[0, 1]), 3)}


def independent_counts(desc: dict, test: dict) -> dict:
    """What the sample is worth: distinct decision sessions, non-empty blocks per length, sectors and regimes."""
    blocks = {k: (test.get(k) or {}).get("nonempty_blocks") for k in (f"L{L}" for L in P["inference"]["block_lengths"])}
    for k, v in (test.get("sensitivity") or {}).items():
        blocks[k] = v.get("nonempty_blocks")
    return {"signals": desc.get("signals", 0), "distinct_sessions": desc.get("distinct_sessions"),
            "non_empty_blocks": blocks, "min_blocks_required": P["inference"]["min_blocks"],
            "sectors": (desc.get("sector_concentration") or {}).get("sectors"),
            "regimes": len(desc.get("regimes") or {})}


def breakdowns(df: pd.DataFrame, seg: str, sessions: pd.DatetimeIndex) -> dict:
    part = df[(df["segment"] == seg) & df["ctrl_value"].notna()] if len(df) else df
    if part.empty:
        return {}
    d = part["value"] - part["ctrl_value"]
    yrs = pd.Series(sessions[part["t"].to_numpy(int)].year, index=part.index)
    out = {}
    for key, grp in (("year", yrs), ("sector", part["sector"]), ("regime", part["regime"])):
        out[key] = {str(k): {"n": len(v), "mean_paired": _f(v.mean()), "small_sample": len(v) < 30}
                    for k, v in d.groupby(grp)}
    return out


def halves(df: pd.DataFrame, b: BoundariesV2, a: str = "value", c: str = "ctrl_value") -> list[float | None]:
    lo, hi = b.presample_range
    mid = (lo + hi) // 2
    part = df[(df["segment"] == "PRESAMPLE") & df[c].notna() & df[a].notna()] if len(df) else df
    if part.empty:
        return [None, None]
    d = part[a] - part[c]
    return [_f(d[part["t"] <= mid].mean()) if (part["t"] <= mid).any() else None,
            _f(d[part["t"] > mid].mean()) if (part["t"] > mid).any() else None]


# ---- dispositions ---------------------------------------------------------------------------------------------------
def _regimes_ok(regs: dict) -> bool:
    tot = sum(regs.values())
    return tot > 0 and sum(v / tot >= 0.15 for v in regs.values()) >= 2


def validation_gates(mean: float | None, holm_p: float | None, desc: dict, dev_mean: float | None, half: list,
                     kind: str = "signal", leg: str = "pess") -> dict[str, bool]:
    """The frozen validation gates (amendment A1) for one leg. Ambiguity is leg-independent and is checked separately.
    S5 (kind 's5') trades at the open with market orders: touch-fill and daily-bar ambiguity do not apply to it."""
    alpha = P["inference"]["alpha_one_sided"]
    sfx = "" if leg == "pess" else "_optimistic"
    common = {
        "holm_p_below_alpha": holm_p is not None and holm_p < alpha,
        "both_halves_positive": all(h is not None and h > 0 for h in half),
        "dev_mean_positive": dev_mean is not None and dev_mean > 0,
    }
    if kind == "s5":
        return {**common,
                "mean_at_least_economic_minimum": mean is not None and mean >= P["economic_min_effect"]["s5_daily_active"],
                "absolute_mean_positive_25bps": (desc.get("mean_daily_net") or 0) > 0,
                "active_positive_50bps": (desc.get("mean_daily_active_50bps") or -1) > 0,
                "two_regimes_15pct": _regimes_ok(desc.get("invested_sessions_by_regime") or {})}
    return {**common,
            "mean_at_least_economic_minimum": mean is not None and mean >= MEE,
            "absolute_mean_positive_25bps": (desc.get(f"mean_per_signal_net{sfx}") or 0) > 0,
            "paired_positive_50bps": (desc.get(f"mean_paired_50bps{sfx}") or -1) > 0,
            "touch_fill_sensitivity_positive": (desc.get(f"mean_paired_touch_fill_sensitivity{sfx}") or -1) > 0,
            "two_regimes_15pct": _regimes_ok(desc.get("regimes") or {})}


def classify_v2(primary: dict, holm_p: float | None, desc: dict, dev_mean: float | None, half: list,
                opt_primary: dict | None, kind: str = "signal", holm_opt: float | None = None,
                half_opt: list | None = None, dev_mean_opt: float | None = None) -> tuple[str, list[str]]:
    """Disposition of one primary hypothesis (PROTOCOL_V2['dispositions'], amendment A1). Order:
    clusters -> VALIDATED (every gate, pessimistic leg) -> UNCERTAINTY (ambiguous > 25% of fills, or the optimistic leg
    passes every gate with its own Holm p) -> FAILED_WITH_POWER (both legs, when they differ) -> PROMISING ->
    INCONCLUSIVE."""
    s = primary["summary"]
    mee = MEE if kind == "signal" else P["economic_min_effect"]["s5_daily_active"]
    if s["status"] != "OK":
        return "INCONCLUSIVE", [f"{s['status']}: fewer than {P['inference']['min_blocks']} non-empty blocks at some "
                                "block length; no p-value, so neither FAILED nor VALIDATED"]
    mean = s["mean"]
    gates = validation_gates(mean, holm_p, desc, dev_mean, half, kind)
    amb = (desc.get("ambiguous_share_of_fills") or 0) > 0.25 if kind == "signal" else False
    failed = [k for k, v in gates.items() if not v] + (["ambiguity_at_most_25pct"] if amb else [])
    if not failed:
        return "RULE_VALIDATED_RESEARCH_OOS", ["every frozen gate passed on PRESAMPLE (backward-time historical "
                                               "robustness, research only; the forward shadow is still required)"]
    if amb:
        return "UNCERTAINTY_TOO_HIGH", [f"ambiguous trades are {desc['ambiguous_share_of_fills']:.1%} of fills (> 25%): "
                                        "daily bars cannot order these events, so no pessimistic-leg verdict is "
                                        f"decisive; failed gates: {', '.join(failed)}"]
    so = (opt_primary or {}).get("summary") or {}
    legs_differ = kind == "signal" and so.get("mean") is not None and mean is not None and abs(so["mean"] - mean) > 1e-12
    if legs_differ and so.get("status") == "OK":
        g_opt = validation_gates(so["mean"], holm_opt, desc, dev_mean_opt, half_opt or [None, None], kind, "opt")
        if all(g_opt.values()):
            return "UNCERTAINTY_TOO_HIGH", ["every validation gate passes on the optimistic leg (its own Holm p "
                                            f"{holm_opt:.4f}) but not on the pessimistic leg; daily bars cannot "
                                            "resolve the ambiguous trades"]
    up = s.get("upper_95_max")
    if mean is not None and mean <= 0 and up is not None and up < mee:
        up_o = so.get("upper_95_max")
        if not legs_differ or (so.get("mean") is not None and so["mean"] <= 0 and up_o is not None and up_o < mee):
            return "FAILED_WITH_POWER", [f"mean {mean:.5f} <= 0 and every one-sided 95% upper bound < {mee}"
                                         + (" on both execution legs" if legs_differ else "")]
        failed.append("optimistic_leg_not_failed_with_power")
    if mean is not None and mean > 0 and gates["absolute_mean_positive_25bps"]:
        return "RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD", [f"failed gates: {', '.join(failed)}"]
    return "INCONCLUSIVE", [f"failed gates: {', '.join(failed)}"]


# ---- S5 ----------------------------------------------------------------------------------------------------------------
def _dd(e: np.ndarray) -> float | None:
    return _f((e / np.maximum.accumulate(e) - 1).min(), 4) if len(e) else None


def _ann(r: np.ndarray) -> float | None:
    """Annualised mean / SD of daily returns (Sharpe without a risk-free rate; descriptive)."""
    sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
    return _f(float(np.mean(r)) / sd * math.sqrt(252), 3) if sd > 0 else None


class S5Data:
    """Inputs of the S5 study: ETF opens/closes, SPY overnight and intraday returns."""

    def __init__(self, pre: Prepared):
        self.etf = {etf: pre.etf_features[etf] for etf in pre.etf_features}
        syms = [*self.etf, BENCHMARK]
        self.opens = {s: pre.bars[s]["open"].astype(float) for s in syms}
        self.closes = {s: pre.bars[s]["close"].astype(float) for s in syms}
        c = self.closes[BENCHMARK].reindex(pre.sessions).ffill()
        o = self.opens[BENCHMARK].reindex(pre.sessions).fillna(c.shift(1))
        self.spy_night = (o / c.shift(1) - 1).fillna(0.0)
        self.spy_day = (c / o - 1).fillna(0.0)
        self.spy_ret = c.pct_change().fillna(0.0)

    def series(self, pre: Prepared, arm: str, cost: float, equal_weight: bool = False, start: int = 0):
        """(net return, end-of-day exposure, post-open exposure, exposure-matched SPY/cash). The benchmark holds SPY
        overnight with the prior close's exposure and from the open to the close with the post-rebalance exposure (A1);
        it pays no cost. Decision rows before `start` are dropped: the book starts from cash at the rebalance row
        `start`, so no return before start + 1 is ever computed (A1 isolation; see s5_start)."""
        cfg = S5Config(rebalance=int(arm[1:]), top_n=P["strategies"]["S5"]["top_n"])
        w = s5_weights(self.etf, pre.regime, pre.sessions, cfg, equal_weight=equal_weight)
        if start > 0:
            w.iloc[:start] = np.nan
        r, x, xo = allocation_returns(w, self.opens, self.closes, cost, with_open_exposure=True)
        bench = (1 + x.shift(1).fillna(0.0) * self.spy_night) * (1 + xo * self.spy_day) - 1
        return r, x, xo, bench


def s5_start(arm: str, lo: int) -> int:
    """The last rebalance row before the segment's first evaluated return (lo + 1). Weights are decided only on rows
    t % R == 0 and fully replace the book, so a book started from cash at this row is identical to the full-history
    book from row start + 1 on (the only difference, the first rebalance's traded weight, falls on start + 1 <= lo)."""
    r = int(arm[1:])
    return max(0, (lo - 1) // r * r)


def s5_segment(pre: Prepared, b: BoundariesV2, data: S5Data, arm: str, seg: str, full: bool) -> dict:
    """One S5 arm on one segment. `full` adds the cost grid, regimes, mechanism and benchmark comparisons. Only the
    segment's own sessions (from the preceding rebalance row) are simulated, so evaluating an arm on DEV computes no
    PRESAMPLE return, and PRESAMPLE is simulated only for the locked arm."""
    lo, hi = seg_range(b, seg)
    sl = slice(lo + 1, hi + 1)
    st = s5_start(arm, lo)
    r, x, xo, bench = data.series(pre, arm, C, start=st)
    act = (r.iloc[sl] - bench.iloc[sl]).to_numpy(float)
    test = block_decision(act, np.arange(len(act)), len(act))
    test["n"] = len(act)
    eq = np.cumprod(1 + r.iloc[sl].to_numpy(float))
    beq = np.cumprod(1 + bench.iloc[sl].to_numpy(float))
    out = {"evidence_type": EVIDENCE[seg], "sessions": len(act),
           "mean_daily_active_vs_exposure_matched_spy": _f(act.mean(), 7),
           "mean_daily_net": _f(r.iloc[sl].mean(), 7), "total_return": _f(eq[-1] - 1) if len(eq) else None,
           "max_drawdown": _dd(eq), "exposure_matched_spy_total_return": _f(beq[-1] - 1) if len(beq) else None,
           "exposure_matched_spy_max_drawdown": _dd(beq), "avg_exposure": _f(x.iloc[sl].mean(), 4),
           "sharpe_annualised": _ann(r.iloc[sl].to_numpy(float)), "information_ratio_vs_exposure_matched_spy": _ann(act),
           "test": test, "halves": [_f(act[: len(act) // 2].mean(), 7), _f(act[len(act) // 2 :].mean(), 7)]}
    if not full:
        return out
    grid = {}
    for cst in COSTS:
        rc = r if cst == C else data.series(pre, arm, cst, start=st)[0]
        a = (rc.iloc[sl] - bench.iloc[sl]).to_numpy(float)
        grid[f"{cst}bps"] = {"mean_daily_active": _f(a.mean(), 7), "mean_daily_net": _f(rc.iloc[sl].mean(), 7),
                             "total_return": _f(np.prod(1 + rc.iloc[sl].to_numpy(float)) - 1)}
    out["cost_grid"] = grid
    out["mean_daily_active_50bps"] = grid["50bps"]["mean_daily_active"]
    invested = xo.iloc[sl].to_numpy(float) > 0  # holding ETFs after the session's open
    reg = pre.regime.reindex(pre.sessions).astype(str).iloc[sl].to_numpy()
    out["invested_sessions"] = int(invested.sum())
    out["invested_sessions_by_regime"] = {str(k): int(v) for k, v in pd.Series(reg[invested]).value_counts().items()}
    turnover_w = s5_weights(data.etf, pre.regime, pre.sessions,
                            S5Config(rebalance=int(arm[1:]), top_n=P["strategies"]["S5"]["top_n"]))
    out["rebalance_sessions"] = int((~turnover_w.iloc[lo : hi + 1].isna().all(axis=1)).sum())
    ew = data.series(pre, arm, C, equal_weight=True, start=st)[0]
    eweq = np.cumprod(1 + ew.iloc[sl].to_numpy(float))
    spyeq = np.cumprod(1 + data.spy_ret.iloc[sl].to_numpy(float))  # SPY's own path (a reference, not an outcome)
    out |= {"equal_weight_sectors_total_return": _f(eweq[-1] - 1) if len(eweq) else None,
            "equal_weight_sectors_max_drawdown": _dd(eweq), "spy_total_return": _f(spyeq[-1] - 1) if len(spyeq) else None,
            "spy_max_drawdown": _dd(spyeq), "mean_daily_vs_equal_weight": _f((r.iloc[sl] - ew.iloc[sl]).mean(), 7)}
    return out


# ---- the study ------------------------------------------------------------------------------------------------------
@dataclass
class StudyResult:
    strategies: dict
    legacy: dict
    baselines: dict
    self_checks: dict
    aggregate: dict
    multiplicity: dict
    configurations: list[dict]
    summary_rows: list[dict]
    coverage: dict
    selection_lock: dict = field(default_factory=dict)


def _pool_matrix(fn, pre: Prepared) -> np.ndarray:
    return np.vstack([fn(pre.frames[s]).reindex(pre.sessions).fillna(False).to_numpy(bool) for s in pre.market.symbols])


def _summary_rows(name: str, arm: str, df: pd.DataFrame) -> list[dict]:
    out = []
    for seg in SEGMENTS:
        part = df[df["segment"] == seg] if len(df) else df
        for cst in COSTS:
            col, cc = f"value_{cst}", f"ctrl_value_{cst}"
            out.append({"name": name, "arm": arm, "segment": seg, "cost_bps_per_side": cst, "signals": len(part),
                        "filled": int(part["filled"].sum()) if len(part) else 0,
                        "mean_per_signal_net": _f(part[col].mean()) if len(part) else None,
                        "control_mean_per_signal_net": _f(part[cc].mean()) if len(part) and cc in part else None,
                        "mean_paired": _f((part[col] - part[cc]).mean()) if len(part) and cc in part else None})
    return out


def books(df: pd.DataFrame, pre: Prepared, b: BoundariesV2) -> dict:
    out = {}
    closes = {s: pre.market.c[s] for s in pre.market.symbols}
    spy = pre.bars[BENCHMARK]["close"].reindex(pre.sessions).to_numpy(float)
    bk = P["books"]
    for seg in SEGMENTS:
        lo, _hi = seg_range(b, seg)
        part = df[(df["segment"] == seg) & df["filled"].astype(bool)] if len(df) else df
        if part.empty:
            out[seg] = {"trades_taken": 0}
            continue
        seg_rows = df[df["segment"] == seg]
        recs = list(zip(seg_rows["ctrl_trades"], seg_rows["ctrl_symbols"], seg_rows["priority"], strict=True)) \
            if "ctrl_trades" in seg_rows else []
        ctrl_exit = max((tr.pess.exit_index for ts, _s, _p in recs if ts for tr in ts if tr.filled), default=-1)
        end = int(min(len(pre.sessions) - 1, max(part["exit_index"].max(), ctrl_exit)))  # every book sees every exit
        trades = part[["symbol", "sector", "entry_index", "exit_index", "entry_price", "exit_price", "priority"]]
        s_book = run_book(trades, closes, len(pre.sessions), bk["cost_bps"], bk["slots"], bk["sector_cap"], spy, lo, end)
        ctrl_books = []
        for j in range(K):
            rows = [{"symbol": syms[j], "sector": pre.sector.get(syms[j]), "entry_index": ts[j].entry_index,
                     "exit_index": ts[j].pess.exit_index, "entry_price": ts[j].entry_price,
                     "exit_price": ts[j].pess.exit_price, "priority": pri}
                    for ts, syms, pri in recs if ts and j < len(ts) and ts[j].filled]
            if rows:
                ctrl_books.append(run_book(pd.DataFrame(rows), closes, len(pre.sessions), bk["cost_bps"], bk["slots"],
                                           bk["sector_cap"], spy, lo, end))
        sm = s_book.summary() | {"evidence_type": EVIDENCE[seg]}
        sr_all = np.diff(np.concatenate([[1.0], s_book.equity])) / np.concatenate([[1.0], s_book.equity[:-1]])
        sm["sharpe_annualised"] = _ann(sr_all)
        if s_book.bench_equity is not None and len(s_book.bench_equity) == len(s_book.equity):
            be = s_book.bench_equity
            br = np.diff(np.concatenate([[1.0], be])) / np.concatenate([[1.0], be[:-1]])
            sm["information_ratio_vs_exposure_matched_spy"] = _ann(sr_all - br)
        sm["note"] = ("descriptive; the exposure-matched SPY/cash curve holds SPY close-to-close with the previous "
                      "close's exposure (intraday entries and exits are not timed)")
        if ctrl_books:
            n = min(len(s_book.equity), *(len(c.equity) for c in ctrl_books))
            sr = np.diff(np.concatenate([[1.0], s_book.equity[:n]])) / np.concatenate([[1.0], s_book.equity[: n - 1]])
            cr = np.mean([np.diff(np.concatenate([[1.0], c.equity[:n]])) / np.concatenate([[1.0], c.equity[: n - 1]])
                          for c in ctrl_books], axis=0)
            act = sr - cr
            sm["control_books"] = len(ctrl_books)
            sm["control_mean_total_return"] = _f(np.mean([c.total_return for c in ctrl_books]))
            sm["control_mean_max_drawdown"] = _f(np.mean([c.max_drawdown for c in ctrl_books]))
            sm["daily_active_vs_control_books"] = block_decision(act, np.arange(len(act)), len(act), n_boot=999)["summary"]
        out[seg] = sm
    return out


def _kept(funnel: dict) -> int:
    return sum(funnel[seg]["kept"] for seg in SEGMENTS if seg in funnel)


def _holm_full(pv: dict[str, float | None]) -> tuple[dict, dict]:
    """Holm over the WHOLE registered family: a member without a p-value enters with p = 1 (A1), so it still counts
    in the multiplier. Returns (adjusted p for every member, adjusted p reported: None where no p exists)."""
    full = INF.holm({k: (1.0 if v is None else v) for k, v in pv.items()})
    return full, {k: (full[k] if pv[k] is not None else None) for k in pv}


def _withhold(funnel: dict) -> dict:
    """An unselected arm keeps its DEV funnel only."""
    return {"DEV": funnel["DEV"], "PRESAMPLE": "withheld: not the DEV-selected arm"}


def _merge_runs(dev: ArmRun, pres: ArmRun) -> ArmRun:
    """The locked arm's DEV run and its later PRESAMPLE run as one ArmRun (identical to a single run over both: every
    PRESAMPLE trade exits before DEV starts, so de-duplication never links the segments)."""
    parts = [x for x in (pres.rows, dev.rows) if len(x)]
    rows = pd.concat(parts, ignore_index=True).sort_values(["t", "symbol"], kind="stable").reset_index(drop=True) \
        if parts else dev.rows
    return ArmRun(rows, {"PRESAMPLE": pres.funnel["PRESAMPLE"], "DEV": dev.funnel["DEV"]})


def selection_lock(chosen: dict[str, str], dev_scores: dict[str, dict], manifest_sha256: str | None) -> dict:
    """The DEV-only arm choice, hashed BEFORE any PRESAMPLE outcome of any S strategy exists (A1 isolation)."""
    from datetime import UTC, datetime

    from apex_worker.research.realeq.protocol_v2 import protocol_v2_sha256

    body = {"segment": "DEV", "rule": P["arm_selection"]["criterion"], "chosen_arms": dict(chosen),
            "dev_selection_t": {sid: {a: _f(v, 6) for a, v in sc.items()} for sid, sc in dev_scores.items()},
            "protocol_sha256": protocol_v2_sha256(), "manifest_sha256": manifest_sha256}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return body | {"lock_sha256": hashlib.sha256(canon.encode()).hexdigest(),
                   "locked_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "guarantee": "computed from DEV outcomes only; no PRESAMPLE outcome of any S1-S5 arm existed when "
                   "it was taken, and PRESAMPLE is then evaluated for these arms only"}


def _choose(scores: dict[str, float], order: list[str], default: str) -> str:
    return max(scores, key=lambda a: (scores[a], -order.index(a))) if scores else default


WITHHELD = {"withheld": "not the DEV-selected arm: never evaluated on PRESAMPLE"}


def run_study(pre: Prepared, b: BoundariesV2, quarantine: dict[str, list[pd.Timestamp]],
              progress: Callable[[str], None] = lambda _m: None, on_lock: Callable[[dict], None] = lambda _l: None,
              manifest_sha256: str | None = None) -> StudyResult:
    """Three phases (A1 isolation): (1) signals from causal features only; (2) every registered arm on DEV, the frozen
    selection rule, and the selection LOCK; (3) PRESAMPLE for the locked arms only, then legacy rules, baselines and
    engine self-checks (single configurations, nothing to select)."""
    global OPEN  # noqa: PLW0603 - one study at a time; the axis is reset on every call
    OPEN = pre.open_sessions
    configs: list[dict] = []
    summary_rows: list[dict] = []
    strategies: dict[str, dict] = {}
    spec = P["strategies"]

    def log_cfg(name, arm, seg, role):
        configs.append({"name": name, "arm": arm, "segment": seg, "cost_bps": C, "role": role})

    # ---- phase 1: signals (features only; no outcome is simulated here) -------------------------------------------
    plan = {
        "S1": [("s1_leader_pullback", spec["S1"]["arms"])],
        "S2": [("s2_residual_momentum", spec["S2"]["arms"])],
        "S3": [("s3a_breakout_immediate", [a for a in spec["S3"]["arms"] if a.startswith("A_")]),
               ("s3c_breakout_retest", [a for a in spec["S3"]["arms"] if a.startswith("C_")])],
    }
    inputs: dict[str, dict[str, tuple]] = {sid: {} for sid in plan}
    for sid, parts in plan.items():
        for plugin, arms in parts:
            strat = QV2_STRATEGIES[plugin]
            sigs, counts = signals_for(strat, pre, b)
            by_seg = {seg: [g for g in sigs if segment_of(g.t, b) == seg] for seg in SEGMENTS}
            pools = {"mechanism": _pool_matrix(strat.mechanism_pool, pre)} if sid in ("S1", "S2") else None
            for arm in arms:
                inputs[sid][arm] = (by_seg, counts, pools)
    # ---- phase 2: DEV only, every registered arm; then the frozen selection rule and the lock --------------------
    dev_runs: dict[str, dict[str, ArmRun]] = {sid: {} for sid in plan}
    dev_tests: dict[str, dict[str, dict]] = {sid: {} for sid in plan}
    scores: dict[str, dict[str, float]] = {}
    chosen: dict[str, str] = {}
    for sid in plan:
        for arm, (by_seg, counts, pools) in inputs[sid].items():
            r = run_arm(sid, arm, by_seg["DEV"], pre, b, quarantine, pools)
            r.funnel["DEV"] |= {"triggered": counts["triggered"]["DEV"], "eligible_in_window": counts["eligible_in_window"]["DEV"]}
            dev_runs[sid][arm] = r
            dev_tests[sid][arm] = paired_test(r.rows, b, "DEV")
            log_cfg(sid, arm, "DEV", "arm")
            progress(f"{sid} {arm}: DEV kept {r.funnel['DEV']['kept']} of {r.funnel['DEV']['signals']} signals")
        scores[sid] = {a: dev_t(t) for a, t in dev_tests[sid].items() if dev_t(t) is not None}
        chosen[sid] = _choose(scores[sid], spec[sid]["arms"], spec[sid]["default_arm"])
    data5 = S5Data(pre)
    order5 = spec["S5"]["arms"]
    dev5 = {a: s5_segment(pre, b, data5, a, "DEV", full=False) for a in order5}
    for a in order5:
        log_cfg("S5", a, "DEV", "arm")
    scores["S5"] = {a: dev_t(v["test"]) for a, v in dev5.items() if dev_t(v["test"]) is not None}
    chosen["S5"] = _choose(scores["S5"], order5, spec["S5"]["default_arm"])
    lock = selection_lock(chosen, scores, manifest_sha256)
    on_lock(lock)
    progress(f"DEV-only selection locked before any PRESAMPLE outcome: {lock['chosen_arms']} "
             f"(lock sha256 {lock['lock_sha256'][:12]})")
    # ---- phase 3: PRESAMPLE, locked arms only -----------------------------------------------------------------------
    for sid in plan:
        ch = chosen[sid]
        by_seg, counts, pools = inputs[sid][ch]
        pres = run_arm(sid, ch, by_seg["PRESAMPLE"], pre, b, quarantine, pools)
        pres.funnel["PRESAMPLE"] |= {"triggered": counts["triggered"]["PRESAMPLE"],
                                     "eligible_in_window": counts["eligible_in_window"]["PRESAMPLE"]}
        r = _merge_runs(dev_runs[sid][ch], pres)
        dev = dev_tests[sid]
        strategies[sid] = {"title": spec[sid]["title"], "arms": {},
                           "funnel": {a: (r.funnel if a == ch else _withhold(x.funnel)) for a, x in dev_runs[sid].items()}}
        for arm, x in dev_runs[sid].items():
            rows = r.rows if arm == ch else x.rows
            strategies[sid]["arms"][arm] = {"DEV": describe(rows, "DEV", initial=x.funnel["DEV"]["signals"]) | {
                "test": dev[arm], "role": "DEV_CONTAMINATED diagnostic"},
                                            "PRESAMPLE": describe(rows, "PRESAMPLE") if arm == ch else WITHHELD}
            summary_rows += [y for y in _summary_rows(sid, arm, rows) if arm == ch or y["segment"] == "DEV"]
        strategies[sid]["selection"] = {"segment": "DEV", "chosen_arm": ch, "dev_t": {a: _f(v, 4) for a, v in scores[sid].items()},
                                        "dev_means": {a: _f(t["summary"]["mean"]) for a, t in dev.items()},
                                        "rule": P["arm_selection"]["criterion"], "fallback_to_default": not scores[sid],
                                        "lock_sha256": lock["lock_sha256"]}
        prim = paired_test(r.rows, b, "PRESAMPLE")
        prim_opt = paired_test(r.rows, b, "PRESAMPLE", "value_opt", "ctrl_value_opt")
        log_cfg(sid, ch, "PRESAMPLE", "primary")
        desc = describe(r.rows, "PRESAMPLE", initial=r.funnel["PRESAMPLE"]["signals"])
        dev_desc = strategies[sid]["arms"][ch]["DEV"]
        strategies[sid]["primary"] = {"arm": ch, "segment": "PRESAMPLE", "evidence_type": EVIDENCE["PRESAMPLE"],
                                      "test": prim, "optimistic_leg": prim_opt, "describe": desc,
                                      "halves": halves(r.rows, b),
                                      "halves_optimistic": halves(r.rows, b, "value_opt", "ctrl_value_opt"),
                                      "breakdowns": breakdowns(r.rows, "PRESAMPLE", pre.sessions),
                                      "dev_mean_paired": dev[ch]["summary"]["mean"],
                                      "dev_mean_paired_optimistic": dev_desc.get("mean_paired_optimistic"),
                                      "independent_counts": independent_counts(desc, prim),
                                      "market_exposure": market_exposure(r.rows, pre, "PRESAMPLE"),
                                      "power": power_report(dev[ch], prim)}
        strategies[sid]["dev_breakdowns"] = breakdowns(r.rows, "DEV", pre.sessions)
        strategies[sid]["dev_market_exposure"] = market_exposure(r.rows, pre, "DEV")
        strategies[sid]["books"] = books(r.rows, pre, b)
        # mechanism contrasts (secondary, descriptive)
        mech = {}
        if sid in ("S1", "S2") and "mech_mechanism_value" in r.rows:
            for seg in SEGMENTS:
                mech[seg] = paired_test(r.rows, b, seg, "value", "mech_mechanism_value")["summary"] | {
                    "pool": spec[sid]["mechanism_pool"]}
        if sid == "S3":
            mech = s3_opportunity_contrast(pre, b, quarantine, ch.split("_")[-1])
        strategies[sid]["mechanism"] = mech
        progress(f"{sid}: PRESAMPLE evaluated once, for the locked arm only")
    # ---- S4 -------------------------------------------------------------------------------------------------------
    strategies["S4"] = {"title": spec["S4"]["title"], "disposition": "NOT_EVALUABLE_MISSING_INPUT",
                        "disposition_reasons": [spec["S4"]["reason"]], "signals": 0}
    # ---- S5: PRESAMPLE for the locked arm only ------------------------------------------------------------------------
    chosen5 = chosen["S5"]
    dev5[chosen5] = s5_segment(pre, b, data5, chosen5, "DEV", full=True)
    pres5 = s5_segment(pre, b, data5, chosen5, "PRESAMPLE", full=True)
    log_cfg("S5", chosen5, "PRESAMPLE", "primary")
    strategies["S5"] = {"title": spec["S5"]["title"],
                        "arms": {a: {"DEV": dev5[a], "PRESAMPLE": pres5 if a == chosen5 else WITHHELD} for a in order5},
                        "selection": {"segment": "DEV", "chosen_arm": chosen5,
                                      "dev_t": {a: _f(v, 4) for a, v in scores["S5"].items()},
                                      "rule": P["arm_selection"]["criterion"], "fallback_to_default": not scores["S5"],
                                      "lock_sha256": lock["lock_sha256"]},
                        "primary": {"arm": chosen5, "segment": "PRESAMPLE", "evidence_type": EVIDENCE["PRESAMPLE"],
                                    "test": pres5["test"], "describe": {k: v for k, v in pres5.items() if k != "test"},
                                    "halves": pres5["halves"], "dev_mean": dev5[chosen5]["test"]["summary"]["mean"],
                                    "power": power_report(dev5[chosen5]["test"], pres5["test"],
                                                          P["economic_min_effect"]["s5_daily_active"])}}
    progress("S5: PRESAMPLE evaluated once, for the locked arm only")
    # ---- multiplicity and dispositions ------------------------------------------------------------------------------
    fam = P["inference"]["primary_family"]
    pv = {sid: strategies[sid]["primary"]["test"]["summary"]["p_one_sided_max"] for sid in fam}
    _adj_full, adj = _holm_full(pv)
    for sid in fam:
        st = strategies[sid]
        st["holm_adjusted_p"] = _f(adj[sid], 5)
        prim = st["primary"]
        if sid == "S5":
            st["disposition"], st["disposition_reasons"] = classify_v2(prim["test"], adj[sid], prim["describe"],
                                                                       prim["dev_mean"], prim["halves"], None, kind="s5")
            continue
        p_opt = prim["optimistic_leg"]["summary"].get("p_one_sided_max")
        holm_opt = _holm_full(pv | {sid: p_opt})[1][sid] if p_opt is not None else None
        prim["holm_adjusted_p_optimistic"] = _f(holm_opt, 5)
        st["disposition"], st["disposition_reasons"] = classify_v2(
            prim["test"], adj[sid], prim["describe"], prim["dev_mean_paired"], prim["halves"], prim["optimistic_leg"],
            holm_opt=holm_opt, half_opt=prim["halves_optimistic"], dev_mean_opt=prim["dev_mean_paired_optimistic"])
    # ---- legacy comparators ----------------------------------------------------------------------------------------
    legacy, lp, lpo = {}, {}, {}
    for sid in P["legacy"]["rules"]:
        strat = STRATEGIES[sid]
        sigs, counts = signals_for(strat, pre, b)
        r = run_arm(sid, "E0X0", sigs, pre, b, quarantine)
        for seg in SEGMENTS:
            r.funnel[seg] |= {"triggered": counts["triggered"][seg], "eligible_in_window": counts["eligible_in_window"][seg]}
        summary_rows += _summary_rows(sid, "E0X0", r.rows)
        for seg in SEGMENTS:
            log_cfg(sid, "E0X0", seg, "legacy")
        prim = paired_test(r.rows, b, "PRESAMPLE")
        dev = paired_test(r.rows, b, "DEV")["summary"]
        desc = describe(r.rows, "PRESAMPLE", initial=r.funnel["PRESAMPLE"]["signals"])
        dev_desc = describe(r.rows, "DEV", initial=r.funnel["DEV"]["signals"])
        legacy[sid] = {"title": strat.spec.title, "version": strat.spec.version, "funnel": r.funnel,
                       "PRESAMPLE": desc | {"test": prim}, "DEV": dev_desc | {"test_summary": dev},
                       "dev_mean_paired": dev["mean"], "dev_mean_paired_optimistic": dev_desc.get("mean_paired_optimistic"),
                       "halves": halves(r.rows, b), "halves_optimistic": halves(r.rows, b, "value_opt", "ctrl_value_opt"),
                       "optimistic": paired_test(r.rows, b, "PRESAMPLE", "value_opt", "ctrl_value_opt"),
                       "independent_counts": independent_counts(desc, prim)}
        lp[sid] = prim["summary"]["p_one_sided_max"]
        lpo[sid] = legacy[sid]["optimistic"]["summary"].get("p_one_sided_max")
        progress(f"legacy {sid}: kept {_kept(r.funnel)}")
    _lfull, ladj = _holm_full(lp)
    for sid, row in legacy.items():
        row["holm_adjusted_p_legacy_family"] = _f(ladj[sid], 5)
        holm_opt = _holm_full(lp | {sid: lpo[sid]})[1][sid] if lpo[sid] is not None else None
        row["disposition"], row["disposition_reasons"] = classify_v2(
            row["PRESAMPLE"]["test"], ladj[sid], row["PRESAMPLE"], row["dev_mean_paired"], row["halves"],
            row["optimistic"], holm_opt=holm_opt, half_opt=row["halves_optimistic"],
            dev_mean_opt=row["dev_mean_paired_optimistic"])
    for sid in P["legacy"]["not_evaluable"]:
        legacy[sid] = {"title": STRATEGIES[sid].spec.title, "disposition": "NOT_EVALUABLE_MISSING_INPUT",
                       "disposition_reasons": ["requires point-in-time catalysts; none exist in CI"]}
    # ---- baselines (descriptive) ----------------------------------------------------------------------------------
    baselines = {}
    for name, sigs in (("momentum_20", baseline_momentum(pre, b)), ("breakout_basic", baseline_breakout(pre, b))):
        r = run_arm(name, "E0X0", sigs, pre, b, quarantine, stop_k=1.5)
        summary_rows += _summary_rows(name, "E0X0", r.rows)
        for seg in SEGMENTS:
            log_cfg(name, "E0X0", seg, "baseline")
        baselines[name] = {seg: describe(r.rows, seg, initial=r.funnel[seg]["signals"]) | {
            "paired_test_descriptive": paired_test(r.rows, b, seg)["summary"]} for seg in SEGMENTS}
        baselines[name]["note"] = "descriptive comparator, not a registered hypothesis (no Holm family, no disposition)"
        baselines[name]["books"] = books(r.rows, pre, b)
    baselines["spy_buy_and_hold"] = spy_hold(pre, b)
    baselines["always_flat"] = {"mean_per_signal_net": 0.0, "note": "no trades"}
    # ---- engine self-checks ------------------------------------------------------------------------------------------
    checks = self_checks(pre, b, quarantine)
    engine_ok = checks["lookahead_trap"]["detected"]
    if not engine_ok:
        for sid in fam:
            strategies[sid]["disposition"] = "RESEARCH_ENGINE_INVALID"
            strategies[sid]["disposition_reasons"] = ["the look-ahead trap was not detected: the engine cannot be trusted"]
    validated = [sid for sid in fam if strategies[sid]["disposition"] == "RULE_VALIDATED_RESEARCH_OOS"]
    aggregate = {
        "conclusion": "RESEARCH_CANDIDATES_FORWARD_SHADOW_REQUIRED" if validated else "NO_TRADE",
        "validated": validated,
        "evidence_type": EVIDENCE["PRESAMPLE"],
        "dispositions": {sid: strategies[sid]["disposition"] for sid in ("S1", "S2", "S3", "S4", "S5")},
        "legacy_dispositions": {sid: v["disposition"] for sid, v in legacy.items()},
        "live_alerts": "DISABLED (research only; no promotion, no group signals, no orders)",
        "engine_self_checks_passed": engine_ok,
    }
    mult = {"primary_family": fam, "method": "Holm", "raw_conservative_p": {k: _f(v, 5) for k, v in pv.items()},
            "holm_adjusted_p": {k: _f(v, 5) for k, v in adj.items()},
            "family_size_used": len(fam), "missing_p_rule": "a member without a p-value enters Holm with p = 1 (A1)",
            "legacy_family": {k: _f(v, 5) for k, v in ladj.items()},
            "note": "arms were selected on DEV only; PRESAMPLE was looked at once, for the selected arms"}
    return StudyResult(strategies, legacy, baselines, checks, aggregate, mult, configs, summary_rows,
                       {"eligible_stocks_by_year": pre.member_counts}, lock)


def s3_opportunity_contrast(pre: Prepared, b: BoundariesV2, q: dict, exit_arm: str) -> dict:
    """Per qualified breakout b (an arm-A signal): the retest-arm value minus the immediate-arm value, same exit.

    DEV only (A1 isolation): the contrast needs both entry arms, and one of them is not the locked arm, so its
    PRESAMPLE half is withheld rather than computed. Descriptive, never a gate. A missed retest counts 0. An
    opportunity is EXCLUDED, not counted as a miss, when its retest session lies outside the breakout's segment, or
    when either arm's trade is quarantined or has an incomplete label. No de-duplication: every qualified breakout is
    its own opportunity."""
    strat_c = QV2_STRATEGIES["s3c_breakout_retest"]
    a_sigs = [g for g in signals_for(QV2_STRATEGIES["s3a_breakout_immediate"], pre, b)[0] if segment_of(g.t, b) == "DEV"]
    pol = arm_policy(f"E0{exit_arm}")
    valid = P["execution"]["entry_valid_sessions"]
    dev0 = b.dev_range[0]
    retests = {s: retest_sessions(pre.frames[s]) for s in sorted({g.symbol for g in a_sigs})}
    allowed = list(strat_c.spec.allowed_regimes or ())

    def value(sym: str, t: int, hint: float | None) -> tuple[str, float | None]:
        res = simulate_signal(pre.market, sym, t, pol, LEVELS, invalidation_hint=hint)
        if res is None:
            return "NO_TRADE", 0.0
        tr = res.trade
        if quarantined(sym, t, tr, pre.sessions, q, valid) or (
                segment_of(t, b) == "PRESAMPLE" and tr.filled and max(tr.pess.exit_index, tr.opt.exit_index) >= dev0):
            return "EXCLUDED", None
        v = per_signal_net(tr, pol)
        return ("OK", v) if v is not None else ("EXCLUDED", None)

    rows, excluded = [], {"immediate_not_tradable": 0, "retest_outside_segment": 0, "excluded_window": 0}
    for g in a_sigs:
        ka, va = value(g.symbol, g.t, g.hint)
        if ka != "OK":
            excluded["immediate_not_tradable" if ka == "NO_TRADE" else "excluded_window"] += 1
            continue
        vc, retested = 0.0, False
        rt = retests[g.symbol].get(g.t)
        if rt is not None:
            tc, hc = rt
            if segment_of(tc, b) != segment_of(g.t, b):
                excluded["retest_outside_segment"] += 1
                continue
            fires = pre.eligible[g.symbol][tc] and (
                not allowed or str(pre.frames[g.symbol]["regime_label"].iloc[tc]) in allowed)
            if fires:
                kc, v = value(g.symbol, tc, hc)
                if kc == "EXCLUDED":
                    excluded["excluded_window"] += 1
                    continue
                vc, retested = float(v), kc == "OK"
        rows.append({"t": g.t, "segment": segment_of(g.t, b), "value": vc, "ctrl_value": va, "retested": retested})
    df = pd.DataFrame(rows)
    out = {"exit_arm": exit_arm, "definition": "retest arm minus immediate arm per opportunity; no retest = 0",
           "excluded": excluded, "segment_evidence": EVIDENCE["DEV"],
           "PRESAMPLE": {"withheld": "needs the unselected entry arm on PRESAMPLE; not computed (A1 isolation)"}}
    part = df[df["segment"] == "DEV"] if len(df) else df
    out["DEV"] = paired_test(df, b, "DEV")["summary"] | {
        "opportunities": len(part), "retest_share": _f(part["retested"].mean(), 4) if len(part) else None}
    return out


def baseline_momentum(pre: Prepared, b: BoundariesV2) -> list[Signal]:
    """Every 5th session: the eligible stock with the highest 20-session return (ties -> later symbol, as before)."""
    syms = pre.stocks
    r20 = np.vstack([pre.frames[s]["ret_20"].to_numpy(float) for s in syms])
    el = np.vstack([pre.eligible[s] for s in syms]) & np.isfinite(r20)
    out = []
    for t in range(b.presample_range[0], b.dev_range[1] + 1, 5):
        if segment_of(t, b) is None or not el[:, t].any():
            continue
        cand = np.flatnonzero(el[:, t])
        best = max(cand, key=lambda i: (r20[i, t], syms[i]))
        out.append(Signal(syms[best], t, None, 0.0))
    return out


def baseline_breakout(pre: Prepared, b: BoundariesV2) -> list[Signal]:
    out = []
    for s in pre.stocks:
        fire = (pre.frames[s]["breakout_today"] == 1).to_numpy(bool) & pre.eligible[s]
        out += [Signal(s, int(t), None, 0.0) for t in np.flatnonzero(fire) if segment_of(int(t), b)]
    return sorted(out, key=lambda g: (g.t, g.symbol))


def spy_hold(pre: Prepared, b: BoundariesV2) -> dict:
    spy = pre.bars[BENCHMARK]["close"].reindex(pre.sessions).astype(float)
    out = {}
    for seg in SEGMENTS:
        lo, hi = seg_range(b, seg)
        s = spy.iloc[lo : hi + 1]
        e = s / s.iloc[0]
        out[seg] = {"total_return": _f(e.iloc[-1] - 1), "max_drawdown": _f((e / e.cummax() - 1).min(), 4),
                    "note": "100% exposure, price-only; not a fair comparator for a swing book"}
    return out


def self_checks(pre: Prepared, b: BoundariesV2, q: dict) -> dict:
    """Look-ahead trap (must be detected) and placebo (should not be), both on PRESAMPLE, generic geometry 2 ATR."""
    lo, hi = b.presample_range
    trap, placebo = [], []
    rng = np.random.default_rng(P["controls"]["seed"])
    for t in range(lo, hi + 1):
        el = [s for s in pre.stocks if pre.eligible[s][t]]
        if not el:
            continue
        fwd = {s: pre.market.c[s][min(len(pre.sessions) - 1, t + 20)] / pre.market.c[s][t] for s in el}
        fwd = {s: v for s, v in fwd.items() if math.isfinite(v)}
        if fwd:
            trap.append(Signal(max(fwd, key=lambda s: (fwd[s], s)), t, None, 0.0))  # CHEATING BY CONSTRUCTION
        placebo.append(Signal(el[int(rng.integers(len(el)))], t, None, 0.0))
    out = {}
    for name, sigs in (("lookahead_trap", trap), ("placebo", placebo)):
        r = run_arm(f"selfcheck_{name}", "E0X1", sigs, pre, b, q, stop_k=2.0, self_check=True)
        test = paired_test(r.rows, b, "PRESAMPLE")["summary"]
        out[name] = {"signals": len(sigs), "kept": _kept(r.funnel), "test": test}
        if name == "placebo":  # a random eligible member per session: the matched-random selection baseline
            out[name]["describe"] = describe(r.rows, "PRESAMPLE", initial=r.funnel["PRESAMPLE"]["signals"])
    p_trap = out["lookahead_trap"]["test"]["p_one_sided_max"]
    out["lookahead_trap"]["detected"] = p_trap is not None and p_trap < 0.001
    p_pl = out["placebo"]["test"]["p_one_sided_max"]
    out["placebo"]["warning"] = p_pl is not None and p_pl < 0.01
    out["note"] = "the trap reads the future on purpose and is never a strategy; its only role is to prove detection"
    out["versions"] = {"execution": EXECUTION_VERSION, "features": QV2_FEATURE_VERSION, "inference": INF.INFERENCE_VERSION}
    return out
