"""realeq-protocol-2 study: S1-S5, legacy comparators, matched controls, DEV-only arm selection, one PRESAMPLE look.

Pipeline (every rule is a value in protocol_v2.PROTOCOL_V2):
  bars -> panel (membership-masked ranks/breadth) -> context -> live gates & membership -> v2 features -> Market
  -> per strategy and arm: signals, levels at 25 bps, simulate_v2, quarantine, de-dup, same-session matched controls
  -> DEV: arm selection (paired mean, generic control) -> PRESAMPLE: conservative block test of the selected arm, Holm
  across S1/S2/S3/S5 -> dispositions; plus mechanism contrasts, books, sensitivities, legacy H rules, baselines and
  engine self-checks. No raw price leaves this module: rows hold returns, flags and indexes only.
"""

from __future__ import annotations

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
from apex_worker.signals.strategies.quant_v2 import QV2_STRATEGIES, S5Config, allocation_returns, s5_weights

P = PROTOCOL_V2
C = P["primary_cost_bps"]
COSTS = P["costs_bps_per_side"]
K = P["controls"]["k"]
MEE = P["economic_min_effect"]["per_signal"]
SEGMENTS = ("PRESAMPLE", "DEV")


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
    out, counts = [], {"triggered": 0, "eligible_in_window": 0}
    lo, hi = b.presample_range[0], b.dev_range[1]
    for s in pre.stocks:
        tf = strat.trigger_frame(pre.frames[s])
        trig = tf["trigger"].to_numpy(bool)
        counts["triggered"] += int(trig[lo : hi + 1].sum())
        if strat.spec.allowed_regimes:
            reg = pre.frames[s]["regime_label"].isin(list(strat.spec.allowed_regimes)).to_numpy(bool)
            trig = trig & reg
        fire = trig & pre.eligible[s]
        hint = tf["invalidation_hint"].to_numpy(float)
        pri = tf["setup_score"].to_numpy(float)
        opp = tf["opportunity"].to_numpy(int) if "opportunity" in tf else np.full(len(trig), -1)
        for t in np.flatnonzero(fire):
            if segment_of(int(t), b) is None:
                continue
            counts["eligible_in_window"] += 1
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
    rows, funnel = [], {"signals": len(sigs), "levels_rejected": 0, "rejected_at_25_passing_at_10": 0, "quarantined": 0,
                        "dedup_skipped": 0, "incomplete_label": 0, "kept": 0}

    def accept(sym: str, tr: TradeV2, t: int) -> bool:
        return not quarantined(sym, t, tr, sessions, q, valid)

    for g in sigs:
        res = simulate_signal(m, g.symbol, g.t, pol, LEVELS, invalidation_hint=g.hint, stop_distance_atr=stop_k)
        if res is None:
            funnel["levels_rejected"] += 1
            close, atr = float(m.c[g.symbol][g.t]), float(m.atr[g.symbol][g.t])
            if pol.exit.uses_target and math.isfinite(close) and math.isfinite(atr) and isinstance(
                    compute_levels(close, atr, g.hint, float(m.resistance[g.symbol][g.t]), LevelConfig(),
                                   stop_distance_atr=stop_k), Levels):
                funnel["rejected_at_25_passing_at_10"] += 1
            continue
        tr = res.trade
        if quarantined(g.symbol, g.t, tr, sessions, q, valid):
            funnel["quarantined"] += 1
            continue
        if g.t <= busy.get(g.symbol, -1):
            funnel["dedup_skipped"] += 1
            continue
        busy[g.symbol] = tr.pess.exit_index if tr.filled else g.t + valid
        v = per_signal_net(tr, pol)
        if v is None:
            funnel["incomplete_label"] += 1
            continue
        funnel["kept"] += 1
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
            row[f"value_{c}"] = per_signal_net(tr, ExecutionPolicy(pol.entry, pol.exit, Costs(c)))
        tt = simulate_signal(m, g.symbol, g.t, pol_tt, LEVELS, invalidation_hint=g.hint, stop_distance_atr=stop_k)
        row["value_tt"] = per_signal_net(tt.trade, pol_tt) if tt is not None else None
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
                        row[f"ctrl_value_{c}"] = float(np.mean(vals)) if vals else None
                    row["ctrl_symbols"] = cs.symbols
                    row["ctrl_trades"] = cs.trades
                    tt_vals = []
                    for s2 in cs.symbols:
                        r2 = simulate_signal(m, s2, g.t, pol_tt, LEVELS, stop_distance_atr=k_atr)
                        if r2 is not None:
                            tt_vals.append(per_signal_net(r2.trade, pol_tt))
                    tt_vals = [x for x in tt_vals if x is not None]
                    row["ctrl_value_tt"] = float(np.mean(tt_vals)) if tt_vals else None
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


def power_report(dev: dict, pres: dict) -> dict:
    """Minimum detectable effect from DEV variance (projected to the PRESAMPLE n) and from the realised PRESAMPLE SE."""
    def se_max(t: dict) -> float | None:
        ses = [t.get(f"L{L}", {}).get("se") for L in P["inference"]["block_lengths"]]
        return max(ses) if ses and all(x is not None for x in ses) else None

    sd, sp = se_max(dev), se_max(pres)
    nd, npres = dev.get("n") or 0, pres.get("n") or 0
    proj = sd * math.sqrt(nd / npres) if sd is not None and nd and npres else None
    return {"z": Z_MDE, "dev_se": _f(sd), "dev_n": nd, "presample_n": npres,
            "mde_projected_from_dev": _f(Z_MDE * proj) if proj is not None else None,
            "mde_presample_realised": _f(Z_MDE * sp) if sp is not None else None,
            "economic_minimum": MEE}


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


def describe(df: pd.DataFrame, seg: str, a: str = "value", c: str = "ctrl_value") -> dict:
    part = df[df["segment"] == seg] if len(df) else df
    if part.empty:
        return {"signals": 0}
    filled = part[part["filled"].astype(bool)]
    ok = part[a].notna() & part[c].notna() if c in part else part[a].notna()
    d = (part.loc[ok, a] - part.loc[ok, c]) if c in part else pd.Series(dtype=float)
    out = {
        "signals": len(part), "filled": len(filled), "fill_rate": _f(len(filled) / len(part), 4),
        "mean_per_signal_net": _f(part[a].mean()), "mean_per_filled_net": _f(filled[a].mean()) if len(filled) else None,
        "control_mean_per_signal_net": _f(part[c].mean()) if c in part else None,
        "mean_paired": _f(d.mean()) if len(d) else None, "median_paired": _f(d.median()) if len(d) else None,
        "share_paired_positive": _f((d > 0).mean(), 4) if len(d) else None,
        "distinct_sessions": int(part["t"].nunique()), "distinct_symbols": int(part["symbol"].nunique()),
        "regimes": {str(k): int(v) for k, v in part["regime"].value_counts().items()},
        "ambiguous_share_of_fills": _f((filled["ambiguity"] != "NONE").mean(), 4) if len(filled) else None,
        "outcomes": {str(k): int(v) for k, v in part["outcome"].value_counts().items()},
        "mean_sessions_held": _f(filled["held"].astype(float).mean(), 2) if len(filled) else None,
        "mfe_bounds_mean": [_f(filled["mfe_lo"].mean()), _f(filled["mfe_hi"].mean())] if len(filled) else None,
        "mae_bounds_mean": [_f(filled["mae_lo"].mean()), _f(filled["mae_hi"].mean())] if len(filled) else None,
    }
    for cst in COSTS:
        if f"value_{cst}" in part and f"ctrl_value_{cst}" in part:
            dd = part[f"value_{cst}"] - part[f"ctrl_value_{cst}"]
            out[f"mean_paired_{cst}bps"] = _f(dd.mean())
            out[f"mean_per_signal_net_{cst}bps"] = _f(part[f"value_{cst}"].mean())
    if "value_opt" in part and "ctrl_value_opt" in part:
        out["mean_paired_optimistic"] = _f((part["value_opt"] - part["ctrl_value_opt"]).mean())
    if "value_tt" in part and "ctrl_value_tt" in part:
        out["mean_paired_touch_fill_sensitivity"] = _f((part["value_tt"] - part["ctrl_value_tt"]).mean())
    if "u0" in part and c in part:
        u0 = part[part["u0"].astype(bool) & ok]
        out["u0_subset"] = {"signals": len(u0), "mean_paired": _f((u0[a] - u0[c]).mean()) if len(u0) else None}
    return out


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


def halves(df: pd.DataFrame, b: BoundariesV2) -> list[float | None]:
    lo, hi = b.presample_range
    mid = (lo + hi) // 2
    part = df[(df["segment"] == "PRESAMPLE") & df["ctrl_value"].notna()] if len(df) else df
    if part.empty:
        return [None, None]
    d = part["value"] - part["ctrl_value"]
    return [_f(d[part["t"] <= mid].mean()) if (part["t"] <= mid).any() else None,
            _f(d[part["t"] > mid].mean()) if (part["t"] > mid).any() else None]


# ---- dispositions ---------------------------------------------------------------------------------------------------
def classify_v2(primary: dict, holm_p: float | None, desc: dict, dev_mean: float | None, half: list,
                opt_primary: dict | None, kind: str = "signal") -> tuple[str, list[str]]:
    s = primary["summary"]
    mee = MEE if kind == "signal" else P["economic_min_effect"]["s5_daily_active"]
    if s["status"] != "OK":
        return "INCONCLUSIVE", [f"{s['status']}: fewer than {P['inference']['min_blocks']} non-empty blocks at some "
                                "block length; no p-value, so neither FAILED nor VALIDATED"]
    mean = s["mean"]
    regs = desc.get("regimes") or {}
    tot = sum(regs.values()) or 1
    checks = {
        "holm_p_below_alpha": holm_p is not None and holm_p < P["inference"]["alpha_one_sided"],
        "mean_at_least_economic_minimum": mean is not None and mean >= mee,
        "absolute_mean_positive_25bps": (desc.get("mean_per_signal_net") or 0) > 0 if kind == "signal" else True,
        "paired_positive_50bps": (desc.get("mean_paired_50bps") or -1) > 0 if kind == "signal" else True,
        "touch_fill_sensitivity_positive": (desc.get("mean_paired_touch_fill_sensitivity") or -1) > 0
        if kind == "signal" else True,
        "both_halves_positive": all(h is not None and h > 0 for h in half),
        "two_regimes_15pct": sum(v / tot >= 0.15 for v in regs.values()) >= 2 if kind == "signal" else True,
        "dev_mean_positive": dev_mean is not None and dev_mean > 0,
        "ambiguity_at_most_25pct": (desc.get("ambiguous_share_of_fills") or 0) <= 0.25 if kind == "signal" else True,
    }
    failed = [k for k, v in checks.items() if not v]
    if not failed:
        return "RULE_VALIDATED_RESEARCH_OOS", ["every frozen gate passed on the untouched PRESAMPLE (research only; the "
                                               "forward shadow is still required)"]
    if failed == ["ambiguity_at_most_25pct"]:
        return "UNCERTAINTY_TOO_HIGH", ["ambiguous trades exceed 25% of fills"]
    so = (opt_primary or {}).get("summary") or {}
    legs_differ = so.get("mean") is not None and mean is not None and abs(so["mean"] - mean) > 1e-12
    if kind == "signal" and legs_differ and so.get("status") == "OK" and set(failed) <= {
            "holm_p_below_alpha", "mean_at_least_economic_minimum"}:
        p_pess = s.get("p_one_sided_max")
        mult = (holm_p / p_pess) if holm_p and p_pess else len(P["inference"]["primary_family"])
        holm_opt = min(1.0, (so.get("p_one_sided_max") or 1.0) * mult)
        if holm_opt < P["inference"]["alpha_one_sided"] and so["mean"] >= mee:
            return "UNCERTAINTY_TOO_HIGH", ["the optimistic leg passes the gates the pessimistic leg fails (same Holm "
                                            "multiplier); daily bars cannot resolve the ambiguous trades"]
    up = s.get("upper_95_max")
    if mean is not None and mean <= 0 and up is not None and up < mee:
        return "FAILED_WITH_POWER", [f"mean {mean:.5f} <= 0 and every one-sided 95% upper bound < {mee}"]
    if mean is not None and mean > 0 and checks["absolute_mean_positive_25bps"]:
        return "RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD", [f"failed gates: {', '.join(failed)}"]
    return "INCONCLUSIVE", [f"failed gates: {', '.join(failed)}"]


# ---- S5 ----------------------------------------------------------------------------------------------------------------
def s5_study(pre: Prepared, b: BoundariesV2) -> dict:
    sp = P["strategies"]["S5"]
    etf_syms = {etf: pre.etf_features[etf] for etf in pre.etf_features}
    opens = {s: pre.bars[s]["open"].astype(float) for s in etf_syms} | {BENCHMARK: pre.bars[BENCHMARK]["open"].astype(float)}
    closes = {s: pre.bars[s]["close"].astype(float) for s in etf_syms} | {BENCHMARK: pre.bars[BENCHMARK]["close"].astype(float)}
    spy_ret = closes[BENCHMARK].reindex(pre.sessions).pct_change().fillna(0.0)
    out: dict = {"arms": {}}
    series: dict[str, dict] = {}
    for arm in sp["arms"]:
        cfg = S5Config(rebalance=int(arm[1:]), top_n=sp["top_n"])
        w = s5_weights(etf_syms, pre.regime, pre.sessions, cfg)
        r, x = allocation_returns(w, opens, closes, C)
        bench = x.shift(1).fillna(0.0) * spy_ret  # exposure-matched SPY/cash: yesterday's exposure, cash yield 0
        ew_w = s5_weights(etf_syms, pre.regime, pre.sessions, cfg, equal_weight=True)
        ew, _ = allocation_returns(ew_w, opens, closes, C)
        series[arm] = {"r": r, "x": x, "bench": bench, "ew": ew}
        out["arms"][arm] = {}
        for seg in SEGMENTS:
            lo, hi = seg_range(b, seg)
            sl = slice(lo + 1, hi + 1)
            act = (r.iloc[sl] - bench.iloc[sl]).to_numpy(float)
            test = block_decision(act, np.arange(len(act)), len(act))
            eq = np.cumprod(1 + r.iloc[sl].to_numpy(float))
            beq = np.cumprod(1 + bench.iloc[sl].to_numpy(float))
            eweq = np.cumprod(1 + ew.iloc[sl].to_numpy(float))
            spyeq = np.cumprod(1 + spy_ret.iloc[sl].to_numpy(float))

            def dd(e):
                return _f((e / np.maximum.accumulate(e) - 1).min(), 4) if len(e) else None

            out["arms"][arm][seg] = {
                "sessions": len(act), "mean_daily_active_vs_exposure_matched_spy": _f(act.mean(), 7),
                "total_return": _f(eq[-1] - 1) if len(eq) else None, "max_drawdown": dd(eq),
                "exposure_matched_spy_total_return": _f(beq[-1] - 1) if len(beq) else None,
                "equal_weight_sectors_total_return": _f(eweq[-1] - 1) if len(eweq) else None,
                "equal_weight_sectors_max_drawdown": dd(eweq), "spy_total_return": _f(spyeq[-1] - 1) if len(spyeq) else None,
                "spy_max_drawdown": dd(spyeq), "avg_exposure": _f(x.iloc[sl].mean(), 4),
                "mean_daily_vs_equal_weight": _f((r.iloc[sl] - ew.iloc[sl]).mean(), 7), "test": test,
                "halves": [_f(act[: len(act) // 2].mean(), 7), _f(act[len(act) // 2 :].mean(), 7)],
            }
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
        end = int(min(len(pre.sessions) - 1, part["exit_index"].max()))
        trades = part[["symbol", "sector", "entry_index", "exit_index", "entry_price", "exit_price", "priority"]]
        s_book = run_book(trades, closes, len(pre.sessions), bk["cost_bps"], bk["slots"], bk["sector_cap"], spy, lo, end)
        ctrl_books = []
        seg_rows = df[df["segment"] == seg]
        recs = list(zip(seg_rows["ctrl_trades"], seg_rows["ctrl_symbols"], seg_rows["priority"], strict=True)) \
            if "ctrl_trades" in seg_rows else []
        for j in range(K):
            rows = [{"symbol": syms[j], "sector": pre.sector.get(syms[j]), "entry_index": ts[j].entry_index,
                     "exit_index": ts[j].pess.exit_index, "entry_price": ts[j].entry_price,
                     "exit_price": ts[j].pess.exit_price, "priority": pri}
                    for ts, syms, pri in recs if ts and j < len(ts) and ts[j].filled]
            if rows:
                ctrl_books.append(run_book(pd.DataFrame(rows), closes, len(pre.sessions), bk["cost_bps"], bk["slots"],
                                           bk["sector_cap"], spy, lo, end))
        sm = s_book.summary()
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


def run_study(pre: Prepared, b: BoundariesV2, quarantine: dict[str, list[pd.Timestamp]],
              progress: Callable[[str], None] = lambda _m: None) -> StudyResult:
    global OPEN  # noqa: PLW0603 - one study at a time; the axis is reset on every call
    OPEN = pre.open_sessions
    configs: list[dict] = []
    summary_rows: list[dict] = []
    strategies: dict[str, dict] = {}
    runs: dict[str, dict[str, ArmRun]] = {}
    spec = P["strategies"]

    def log_cfg(name, arm, seg, role):
        configs.append({"name": name, "arm": arm, "segment": seg, "cost_bps": C, "role": role})

    # ---- S1, S2, S3 --------------------------------------------------------------------------------------------
    plan = {
        "S1": [("s1_leader_pullback", spec["S1"]["arms"])],
        "S2": [("s2_residual_momentum", spec["S2"]["arms"])],
        "S3": [("s3a_breakout_immediate", [a for a in spec["S3"]["arms"] if a.startswith("A_")]),
               ("s3c_breakout_retest", [a for a in spec["S3"]["arms"] if a.startswith("C_")])],
    }
    for sid, parts in plan.items():
        runs[sid] = {}
        funnels = {}
        for plugin, arms in parts:
            strat = QV2_STRATEGIES[plugin]
            sigs, counts = signals_for(strat, pre, b)
            pools = None
            if sid in ("S1", "S2"):
                pools = {"mechanism": _pool_matrix(strat.mechanism_pool, pre)}
            for arm in arms:
                r = run_arm(sid, arm, sigs, pre, b, quarantine, pools)
                r.funnel |= counts
                runs[sid][arm] = r
                funnels[arm] = r.funnel
                log_cfg(sid, arm, "DEV", "arm")
                progress(f"{sid} {arm}: kept {r.funnel['kept']} of {len(sigs)} signals")
        # DEV-only arm selection; PRESAMPLE rows of every other arm are discarded unseen
        default = spec[sid]["default_arm"]
        dev = {arm: paired_test(r.rows, b, "DEV") for arm, r in runs[sid].items()}
        ok = {a: dev_t(t) for a, t in dev.items() if dev_t(t) is not None}
        order = spec[sid]["arms"]
        chosen = max(ok, key=lambda a: (ok[a], -order.index(a))) if ok else default
        strategies[sid] = {"title": spec[sid]["title"], "arms": {}, "funnel": funnels}
        for arm, r in runs[sid].items():
            if arm != chosen and len(r.rows):
                r.rows = r.rows[r.rows["segment"] == "DEV"]
            strategies[sid]["arms"][arm] = {"DEV": describe(r.rows, "DEV") | {"test": dev[arm],
                                                                              "role": "DEV_CONTAMINATED diagnostic"},
                                            "PRESAMPLE": describe(r.rows, "PRESAMPLE") if arm == chosen else
                                            {"withheld": "not the DEV-selected arm: never evaluated on PRESAMPLE"}}
            summary_rows += [x for x in _summary_rows(sid, arm, r.rows) if arm == chosen or x["segment"] == "DEV"]
        strategies[sid]["selection"] = {"segment": "DEV", "chosen_arm": chosen, "dev_t": {a: _f(v, 4) for a, v in ok.items()},
                                        "dev_means": {a: _f(t["summary"]["mean"]) for a, t in dev.items()},
                                        "rule": P["arm_selection"]["criterion"], "fallback_to_default": not ok}
        r = runs[sid][chosen]
        prim = paired_test(r.rows, b, "PRESAMPLE")
        prim_opt = paired_test(r.rows, b, "PRESAMPLE", "value_opt", "ctrl_value_opt")
        log_cfg(sid, chosen, "PRESAMPLE", "primary")
        strategies[sid]["primary"] = {"arm": chosen, "segment": "PRESAMPLE", "test": prim, "optimistic_leg": prim_opt,
                                      "describe": describe(r.rows, "PRESAMPLE"), "halves": halves(r.rows, b),
                                      "breakdowns": breakdowns(r.rows, "PRESAMPLE", pre.sessions),
                                      "dev_mean_paired": dev[chosen]["summary"]["mean"],
                                      "power": power_report(dev[chosen], prim)}
        strategies[sid]["dev_breakdowns"] = breakdowns(r.rows, "DEV", pre.sessions)
        strategies[sid]["books"] = books(r.rows, pre, b)
        # mechanism contrasts (secondary, descriptive)
        mech = {}
        if sid in ("S1", "S2") and "mech_mechanism_value" in r.rows:
            for seg in SEGMENTS:
                mech[seg] = paired_test(r.rows, b, seg, "value", "mech_mechanism_value")["summary"] | {
                    "pool": spec[sid]["mechanism_pool"]}
        if sid == "S3":
            mech = s3_opportunity_contrast(pre, b, quarantine, chosen.split("_")[-1])
        strategies[sid]["mechanism"] = mech
        progress(f"{sid}: chose {chosen}; PRESAMPLE {prim['summary']}")
    # ---- S4 -------------------------------------------------------------------------------------------------------
    strategies["S4"] = {"title": spec["S4"]["title"], "disposition": "NOT_EVALUABLE_MISSING_INPUT",
                        "disposition_reasons": [spec["S4"]["reason"]], "signals": 0}
    # ---- S5 -------------------------------------------------------------------------------------------------------
    s5 = s5_study(pre, b)
    dev5 = {a: v["DEV"]["test"]["summary"] for a, v in s5["arms"].items()}
    ok5 = {a: dev_t(v["DEV"]["test"]) for a, v in s5["arms"].items() if dev_t(v["DEV"]["test"]) is not None}
    order5 = spec["S5"]["arms"]
    chosen5 = max(ok5, key=lambda a: (ok5[a], -order5.index(a))) if ok5 else spec["S5"]["default_arm"]
    for a in order5:
        for seg in SEGMENTS:
            log_cfg("S5", a, seg, "arm")
    log_cfg("S5", chosen5, "PRESAMPLE", "primary")
    strategies["S5"] = {"title": spec["S5"]["title"], "arms": s5["arms"],
                        "selection": {"segment": "DEV", "chosen_arm": chosen5, "dev_t": {a: _f(v, 4) for a, v in ok5.items()},
                                      "fallback_to_default": not ok5},
                        "primary": {"arm": chosen5, "segment": "PRESAMPLE", "test": s5["arms"][chosen5]["PRESAMPLE"]["test"],
                                    "describe": {k: v for k, v in s5["arms"][chosen5]["PRESAMPLE"].items() if k != "test"},
                                    "halves": s5["arms"][chosen5]["PRESAMPLE"]["halves"],
                                    "dev_mean": dev5[chosen5]["mean"]}}
    # ---- multiplicity and dispositions ------------------------------------------------------------------------------
    fam = P["inference"]["primary_family"]
    pv = {sid: strategies[sid]["primary"]["test"]["summary"]["p_one_sided_max"] for sid in fam}
    adj = INF.holm(pv)
    for sid in fam:
        st = strategies[sid]
        st["holm_adjusted_p"] = _f(adj[sid], 5)
        prim = st["primary"]
        if sid == "S5":
            desc = prim["describe"]
            st["disposition"], st["disposition_reasons"] = classify_v2(prim["test"], adj[sid], desc, prim["dev_mean"],
                                                                       prim["halves"], None, kind="s5")
        else:
            st["disposition"], st["disposition_reasons"] = classify_v2(prim["test"], adj[sid], prim["describe"],
                                                                       prim["dev_mean_paired"], prim["halves"],
                                                                       prim["optimistic_leg"])
    # ---- legacy comparators ----------------------------------------------------------------------------------------
    legacy, lp = {}, {}
    for sid in P["legacy"]["rules"]:
        strat = STRATEGIES[sid]
        sigs, counts = signals_for(strat, pre, b)
        r = run_arm(sid, "E0X0", sigs, pre, b, quarantine)
        r.funnel |= counts
        summary_rows += _summary_rows(sid, "E0X0", r.rows)
        for seg in SEGMENTS:
            log_cfg(sid, "E0X0", seg, "legacy")
        prim = paired_test(r.rows, b, "PRESAMPLE")
        dev = paired_test(r.rows, b, "DEV")["summary"]
        legacy[sid] = {"title": strat.spec.title, "version": strat.spec.version, "funnel": r.funnel,
                       "PRESAMPLE": describe(r.rows, "PRESAMPLE") | {"test": prim}, "DEV": describe(r.rows, "DEV"),
                       "dev_mean_paired": dev["mean"], "halves": halves(r.rows, b),
                       "optimistic": paired_test(r.rows, b, "PRESAMPLE", "value_opt", "ctrl_value_opt")}
        lp[sid] = prim["summary"]["p_one_sided_max"]
        progress(f"legacy {sid}: kept {r.funnel['kept']}")
    ladj = INF.holm(lp)
    for sid, row in legacy.items():
        row["holm_adjusted_p_legacy_family"] = _f(ladj[sid], 5)
        row["disposition"], row["disposition_reasons"] = classify_v2(row["PRESAMPLE"]["test"], ladj[sid], row["PRESAMPLE"],
                                                                     row["dev_mean_paired"], row["halves"], row["optimistic"])
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
        baselines[name] = {seg: describe(r.rows, seg) for seg in SEGMENTS}
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
        "dispositions": {sid: strategies[sid]["disposition"] for sid in ("S1", "S2", "S3", "S4", "S5")},
        "legacy_dispositions": {sid: v["disposition"] for sid, v in legacy.items()},
        "live_alerts": "DISABLED (research only; no promotion, no group signals, no orders)",
        "engine_self_checks_passed": engine_ok,
    }
    mult = {"primary_family": fam, "method": "Holm", "raw_conservative_p": {k: _f(v, 5) for k, v in pv.items()},
            "holm_adjusted_p": {k: _f(v, 5) for k, v in adj.items()},
            "legacy_family": {k: _f(v, 5) for k, v in ladj.items()},
            "note": "arms were selected on DEV only; PRESAMPLE was looked at once, for the selected arms"}
    return StudyResult(strategies, legacy, baselines, checks, aggregate, mult, configs, summary_rows,
                       {"eligible_stocks_by_year": pre.member_counts})


def s3_opportunity_contrast(pre: Prepared, b: BoundariesV2, q: dict, exit_arm: str) -> dict:
    """Per qualified breakout: retest-arm value minus immediate-arm value, same exit (missed retest = 0)."""
    a_sigs, _ = signals_for(QV2_STRATEGIES["s3a_breakout_immediate"], pre, b)
    c_sigs, _ = signals_for(QV2_STRATEGIES["s3c_breakout_retest"], pre, b)
    pol = arm_policy(f"E0{exit_arm}")
    valid = P["execution"]["entry_valid_sessions"]
    cval = {}
    for g in c_sigs:
        res = simulate_signal(pre.market, g.symbol, g.t, pol, LEVELS, invalidation_hint=g.hint)
        if res is not None and not quarantined(g.symbol, g.t, res.trade, pre.sessions, q, valid):
            v = per_signal_net(res.trade, pol)
            if v is not None:
                cval[(g.symbol, g.opportunity)] = v
    rows = []
    for g in a_sigs:
        res = simulate_signal(pre.market, g.symbol, g.t, pol, LEVELS, invalidation_hint=g.hint)
        if res is None or quarantined(g.symbol, g.t, res.trade, pre.sessions, q, valid):
            continue
        va = per_signal_net(res.trade, pol)
        if va is None:
            continue
        vc = cval.get((g.symbol, g.t), 0.0)
        rows.append({"t": g.t, "segment": segment_of(g.t, b), "value": vc, "ctrl_value": va,
                     "retested": (g.symbol, g.t) in cval})
    df = pd.DataFrame(rows)
    out = {"exit_arm": exit_arm, "definition": "retest arm minus immediate arm per opportunity; no retest = 0"}
    for seg in SEGMENTS:
        part = df[df["segment"] == seg] if len(df) else df
        out[seg] = paired_test(df, b, seg)["summary"] | {
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
        out[name] = {"signals": len(sigs), "kept": r.funnel["kept"], "test": test}
    p_trap = out["lookahead_trap"]["test"]["p_one_sided_max"]
    out["lookahead_trap"]["detected"] = p_trap is not None and p_trap < 0.001
    p_pl = out["placebo"]["test"]["p_one_sided_max"]
    out["placebo"]["warning"] = p_pl is not None and p_pl < 0.01
    out["note"] = "the trap reads the future on purpose and is never a strategy; its only role is to prove detection"
    out["versions"] = {"execution": EXECUTION_VERSION, "features": QV2_FEATURE_VERSION, "inference": INF.INFERENCE_VERSION}
    return out
