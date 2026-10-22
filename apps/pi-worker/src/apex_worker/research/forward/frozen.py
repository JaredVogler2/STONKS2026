"""Frozen confirmatory lane: the realeq-protocol-2 (A1) arms locked by the official run, recorded prospectively.

Series `qv2a1-frozen-v1`. Arms (the selection lock 017070e1…): S1 E0X1, S2 E0X1, S3 C_X0 and S5 R10. The registered
forward evaluation is protocol-2's own (Holm over S1, S2, S3, S5), so all four locked arms are recorded; Discovery V3
names S1 and S3 as the arms of primary interest. Nothing here is re-implemented: triggers, gates, levels, order plans,
control strata, control seeds and the execution engine are the study's functions (`study_v2`, `controls`,
`execution_v2`), and `tests/test_forward_shadow_parity.py` proves on a fixture that the recorded decisions plus the
matured outcomes reproduce the study's rows exactly.

Decision (after the close of t, before any outcome; `decide_at`):
* the full U1 funnel at t: one gate code per member (or data status), and per arm: triggered, regime-filtered,
  ineligible, signals, level rejections;
* per signal: unitless trigger features, the order plan (limit, open guard, stop, target) at 25 bps, the stop distance
  in ATR, a beta estimate, a short hash of the decision-session bar, and the ORDERED matched-control reserve: the
  stratum, the seeded draw order and each candidate's own plan, i.e. everything `matched_controls` uses that is known at
  t. Which reserve candidates become the K = 10 controls depends on later data (a corporate-action quarantine or an
  incomplete label replaces one), exactly as in the study, and is resolved at maturity;
* S5: the target weights on rebalance sessions (every 10th session of the protocol axis) and the regime label.

Outcome (`mature`, after t + 22 sessions and the 2-session finality lag): the study's per-signal labelling applied to the
frozen plans, in session order: quarantine, de-duplication (one position per arm and symbol until its pessimistic exit),
incomplete labels, per-signal net at 0/10/25/50 bps on both legs, touch-fill sensitivity, the K controls, the paired
value, and paper market-relative legs (hypothetical: no borrow, financing or hedge cost). S5: the session t+1 return of
the forward S5 book (from cash at the first forward rebalance) and its exposure-matched SPY/cash benchmark.

Session axis: positions on the protocol axis (0 = 2016-01-04). The history window starts at WINDOW_ANCHOR (axis
position 2070, a multiple of 10), so the S2 (every 5th) and S5 (every 10th) rebalance phases and the control seeds equal
the study's. Rows hold returns, flags, plans and hashes only; no raw OHLCV.
"""

from __future__ import annotations

import hashlib
import json
import math
import zlib
from dataclasses import dataclass, field
from datetime import date
from functools import cached_property
from pathlib import Path

import numpy as np
import pandas as pd

from apex_worker.research.controls import control_stratum, per_signal_net, plan_signal, stop_distance_atr
from apex_worker.research.execution_v2 import EXECUTION_VERSION, Costs, ExecutionPolicy, OrderPlan, simulate_v2
from apex_worker.research.forward import windows as W
from apex_worker.research.forward.records import canonical, clean
from apex_worker.research.inference import INFERENCE_VERSION
from apex_worker.research.realeq import study_v2 as S
from apex_worker.research.realeq import universe_v2 as U
from apex_worker.research.realeq.protocol_v2 import FORWARD_START, PROTOCOL_V2, protocol_v2_sha256
from apex_worker.research.training_data import BENCHMARK
from apex_worker.signals.candidates import EXCLUDED_INSTRUMENTS, GateConfig
from apex_worker.signals.levels import LevelConfig, Levels, compute_levels
from apex_worker.signals.strategies.quant_v2 import (
    QV2_STRATEGIES,
    QV2_STRATEGY_VERSION,
    S5Config,
    allocation_returns,
    s5_weights,
)

SERIES_ID = "qv2a1-frozen-v1"
LANE = "FROZEN_CONFIRMATORY"
RECORDER_VERSION = "forward-shadow-1"
DECISIONS_FROM = date.fromisoformat(FORWARD_START)  # PROTOCOL_V2.chronology.forward_shadow.decisions_from
PINNED_PROTOCOL_SHA256 = "33bd4e99ecaf06e8fbe0312e2892a0e382d39a632c28280bb66dbd4f5d05475d"  # A1
WINDOW_ANCHOR = date(2024, 3, 26)
WINDOW_ANCHOR_INDEX = 2070  # position on the protocol axis; a multiple of 10 (S2 every 5th, S5 every 10th session)
ARMS: tuple[tuple[str, str, str], ...] = (("S1", "E0X1", "s1_leader_pullback"), ("S2", "E0X1", "s2_residual_momentum"),
                                          ("S3", "C_X0", "s3c_breakout_retest"))
S5_ARM = "R10"
PRIMARY_INTEREST = ("S1", "S3")
K = PROTOCOL_V2["controls"]["k"]
CONTROL_RESERVE = 3 * K  # draw-order candidates frozen per signal; the first K that survive quarantine become controls
MIN_STRATUM = PROTOCOL_V2["controls"]["min_stratum"]
VALID = PROTOCOL_V2["execution"]["entry_valid_sessions"]
COSTS = PROTOCOL_V2["costs_bps_per_side"]
C = PROTOCOL_V2["primary_cost_bps"]
TT = PROTOCOL_V2["execution"]["touch_fill_sensitivity_trade_through_ticks"]
BETA_LOOKBACK = 252
FEATURES = {  # unitless trigger inputs recorded per signal (prices are never recorded)
    "S1": ("v2_peak_age_10", "v2_leader_at_peak", "v2_pullback_atr", "dist_sma50_atr", "sma50_slope_20",
           "v2_sector_pct_ret_60"),
    "S2": ("v2_xs_pct_resid", "v2_resid_score_20", "ret_20", "v2_sector_breadth", "gap_1"),
    "S3": ("rel_volume_1", "squeeze_pct_120", "base_length", "base_depth_pct", "rs_bench_20", "rs_sector_20",
           "dist_sma50_atr"),
}
# The selection lock exactly as committed in research-results/real-equities/qv2-20261022-s1-s5-u1-presample/
# strategy_metrics.json; its sha256 is recomputed (here and from that file when present) before anything is recorded.
LOCK_SHA256 = "017070e1654680bb32f7570e9b869d9e35007c562a920d0d9236bd453a878833"
LOCK_BODY = {
    "segment": "DEV",
    "rule": PROTOCOL_V2["arm_selection"]["criterion"],
    "chosen_arms": {"S1": "E0X1", "S2": "E0X1", "S3": "C_X0", "S5": "R10"},
    "dev_selection_t": {"S1": {"E0X0": -0.082972, "E0X1": 0.429736, "E1X0": -1.298434, "E1X1": -1.250465},
                        "S2": {"E0X0": -1.894808, "E0X1": -1.037587},
                        "S3": {"A_X0": -2.052186, "A_X2": -3.172889, "C_X0": 0.179065, "C_X2": -0.667864},
                        "S5": {"R10": -0.847934, "R5": -1.855084}},
    "protocol_sha256": PINNED_PROTOCOL_SHA256,
    "manifest_sha256": "be792db3eca3ce7999d45644ff46e83cc671f636d675f602eeb07fc210b91997",
}
LOCK_FILE = ("research-results/real-equities/qv2-20261022-s1-s5-u1-presample/strategy_metrics.json")
REPO_ROOT = Path(__file__).resolve().parents[6]


class FrozenLaneInvalid(Exception):
    """The frozen lane cannot be reconstructed exactly (protocol, lock, arms or behaviour changed): fail closed."""


def _lock_sha(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def check_frozen_inputs(repo_root: Path | None = REPO_ROOT) -> dict:
    """Protocol hash, selection lock and arms must be exactly the registered ones; otherwise nothing is recorded."""
    problems = []
    proto = protocol_v2_sha256()
    if proto != PINNED_PROTOCOL_SHA256:
        problems.append(f"protocol-2 sha256 {proto} != registered A1 {PINNED_PROTOCOL_SHA256}")
    if _lock_sha(LOCK_BODY) != LOCK_SHA256:
        problems.append("embedded selection lock does not hash to the registered lock sha256")
    chosen = {sid: arm for sid, arm, _ in ARMS} | {"S5": S5_ARM}
    if chosen != LOCK_BODY["chosen_arms"]:
        problems.append(f"recorded arms {chosen} != locked arms {LOCK_BODY['chosen_arms']}")
    committed = None
    path = (repo_root / LOCK_FILE) if repo_root else None
    if path is not None and path.exists():
        lk = json.loads(path.read_text())["selection_lock"]
        committed = _lock_sha({k: lk[k] for k in LOCK_BODY})
        if committed != LOCK_SHA256 or lk.get("lock_sha256") != LOCK_SHA256:
            problems.append("the committed official-run selection lock no longer hashes to the registered sha256")
    if problems:
        raise FrozenLaneInvalid("; ".join(problems))
    return {"protocol_sha256": proto, "lock_sha256": LOCK_SHA256, "lock_verified_against_committed_artifact":
            committed is not None}


def series_config(fingerprint: str | None = None) -> dict:
    """Everything that defines the series. A change here (or in decide behaviour, via the fingerprint) needs a new
    series id; records of this series are never re-run under different code."""
    return clean({
        "series_id": SERIES_ID, "lane": LANE, "recorder_version": RECORDER_VERSION, "decisions_from": DECISIONS_FROM,
        "mode": "RESEARCH_ONLY", "orders": "none", "alerts": "none", "group_actionable": False, "promotion": "none",
        "protocol": {"version": PROTOCOL_V2["version"], "amendment": PROTOCOL_V2["amendment"]["id"],
                     "sha256": PINNED_PROTOCOL_SHA256},
        "selection_lock": {"sha256": LOCK_SHA256, "chosen_arms": LOCK_BODY["chosen_arms"], "source": LOCK_FILE},
        "arms": [f"{sid}:{arm}" for sid, arm, _ in ARMS] + [f"S5:{S5_ARM}"],
        "registered_family": PROTOCOL_V2["inference"]["primary_family"], "primary_interest_v3": list(PRIMARY_INTEREST),
        "versions": {"execution": EXECUTION_VERSION, "strategies": QV2_STRATEGY_VERSION,
                     "features": S.QV2_FEATURE_VERSION, "inference": INFERENCE_VERSION},
        "levels": {"cost_bps_per_side": C, **{k: getattr(S.LEVELS, k) for k in ("min_net_rr", "target_r_multiple",
                                                                              "stop_min_atr", "stop_max_atr")}},
        "controls": {"k": K, "seed": PROTOCOL_V2["controls"]["seed"], "min_stratum": MIN_STRATUM,
                     "reserve": CONTROL_RESERVE, "pool": "generic (every gate-eligible member)"},
        "axis": {"first_session": W.FIRST_SESSION, "window_anchor": WINDOW_ANCHOR,
                 "window_anchor_index": WINDOW_ANCHOR_INDEX, "calendar": "XNYS (exchange_calendars)"},
        "windows": {"finality_buffer_minutes": int(W.FINALITY_BUFFER.total_seconds() // 60),
                    "deadline": "next session open", "label_sessions": W.LABEL_SESSIONS,
                    "finality_lag_sessions": W.FINALITY_LAG, "extra_sessions_for_unresolved": W.EXTRA_SESSIONS},
        "universe": {"id": "U1", "snapshot_sha256": U.snapshot_sha256(), "membership": "date_added <= t",
                     "limitation": "current S&P 500 survivors (2026-10-22): later index changes are not captured"},
        "source": {"provider": "alpaca", "feed": "sip", "adjustment": "split", "timeframe": "1Day",
                   "decision_bar": "post-close bar of t (preliminary); revisions are reported at maturity"},
        "golden_fingerprint": fingerprint,
    })


def config_sha256(cfg: dict) -> str:
    return hashlib.sha256(canonical(cfg)).hexdigest()


# ---- helpers ------------------------------------------------------------------------------------------------------------
def _r(x, nd: int = 6):
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


def _bar_hash(bars: pd.DataFrame | None, day: pd.Timestamp) -> str | None:
    if bars is None or day not in bars.index:
        return None
    row = bars.loc[day, ["open", "high", "low", "close", "volume"]].astype(float).to_numpy()
    return hashlib.sha256(np.round(row, 6).tobytes()).hexdigest()[:16]


def _seed(sid: str, arm: str, symbol: str, t_abs: int) -> int:
    return zlib.crc32("|".join(map(str, (sid, arm, "generic", symbol, t_abs))).encode()) ^ PROTOCOL_V2["controls"]["seed"]


assert _seed("S1", "E0X1", "AAPL", 5) == S._seed("S1", "E0X1", "generic", "AAPL", 5)  # the study's seed function


def _plan(p: OrderPlan) -> list:
    return [_r(p.limit, 4), _r(p.min_open, 4), _r(p.stop, 4), _r(p.target, 4) if p.target is not None else None]


def _unplan(v: list) -> OrderPlan:
    return OrderPlan(float(v[0]), float(v[1]), float(v[2]), None if v[3] is None else float(v[3]))


GATES = GateConfig()
_ADDED = {c.symbol: pd.Timestamp(c.date_added) for c in U.u1_constituents()}


def gate_code(pre: S.Prepared, sym: str, t: int, meta) -> str:
    """Why a stock is or is not eligible at t: membership, then gate_mask's conditions in its order. Asserted against
    the study's mask, so the recorded funnel can never disagree with the decision."""
    f = pre.frames[sym]
    day = pre.sessions[t]
    if (meta is None or meta.asset_kind == "benchmark" or meta.exchange == "OTC" or meta.leveraged
            or meta.instrument in EXCLUDED_INSTRUMENTS or meta.tradability_status == "excluded"):
        code = "META_EXCLUDED"
    elif sym not in _ADDED or _ADDED[sym] > day:
        code = "NOT_MEMBER"
    elif not math.isfinite(float(f["close"].iloc[t])):
        code = "NO_BAR_T"
    else:
        row = f.iloc[t]

        def bad(col, ok):
            v = float(row[col]) if col in row and row[col] is not None else math.nan
            return not ok(v)

        if bad("close", lambda v: v >= GATES.min_price):
            code = "PRICE"
        elif bad("dollar_vol_med_20", lambda v: v >= GATES.min_dollar_volume):
            code = "DOLLAR_VOLUME"
        elif bad("avg_vol_20", lambda v: v >= GATES.min_avg_volume):
            code = "AVG_VOLUME"
        elif bad("bars_available", lambda v: v >= GATES.min_history):
            code = "HISTORY"
        elif float(row["dist_sma20_atr"]) > GATES.max_extension_atr:
            code = "EXTENSION"
        elif (float(np.nan_to_num(row.get("regime_p_risk_off", 0.0)))
              + float(np.nan_to_num(row.get("regime_p_volatility_shock", 0.0)))) >= GATES.risk_off_breaker:
            code = "RISK_OFF_BREAKER"
        else:
            code = "PASS"
    elig = bool(pre.eligible[sym][t])
    if (code == "PASS") != elig:
        raise FrozenLaneInvalid(f"gate reconstruction disagrees with the gate mask for {sym} at {day.date()}: {code}")
    return code


def _beta(pre: S.Prepared, sym: str, t: int) -> float | None:
    lo = max(0, t - BETA_LOOKBACK)
    c = pre.bars[sym]["close"].reindex(pre.sessions).astype(float).to_numpy()[lo : t + 1]
    b = pre.bars[BENCHMARK]["close"].reindex(pre.sessions).astype(float).to_numpy()[lo : t + 1]
    rs, rb = np.diff(np.log(c)), np.diff(np.log(b))
    ok = np.isfinite(rs) & np.isfinite(rb)
    if ok.sum() < 120 or np.var(rb[ok]) <= 0:
        return None
    return _r(np.cov(rs[ok], rb[ok])[0, 1] / np.var(rb[ok], ddof=1), 4)


# ---- decisions ----------------------------------------------------------------------------------------------------------
@dataclass
class FrozenContext:
    """A prepared window plus lazily computed trigger frames (each row depends on rows <= itself only)."""

    pre: S.Prepared
    first_index: int  # protocol-axis position of pre.sessions[0]
    _tf: dict = field(default_factory=dict, repr=False)

    def trigger(self, plugin: str, symbol: str) -> pd.DataFrame:
        key = (plugin, symbol)
        if key not in self._tf:
            self._tf[key] = QV2_STRATEGIES[plugin].trigger_frame(self.pre.frames[symbol])
        return self._tf[key]

    @cached_property
    def s5(self) -> pd.DataFrame:
        cfg = S5Config(rebalance=int(S5_ARM[1:]), top_n=PROTOCOL_V2["strategies"]["S5"]["top_n"])
        return s5_weights(self.pre.etf_features, self.pre.regime, self.pre.sessions, cfg)

    @cached_property
    def meta(self) -> dict:
        return U.u1_meta()


def _arm(ctx: FrozenContext, sid: str, arm: str, plugin: str, t: int, t_abs: int, held: set[str]) -> dict:
    pre, m = ctx.pre, ctx.pre.market
    strat = QV2_STRATEGIES[plugin]
    pol = S.arm_policy(arm)
    allowed = set(strat.spec.allowed_regimes or ())
    funnel = {"triggered": 0, "regime_filtered": 0, "not_gate_eligible": 0, "signals": 0, "levels_rejected": 0,
              "rejected_at_25_passing_at_10": 0, "planned": 0}
    raw = []
    for s in pre.stocks:
        tf = ctx.trigger(plugin, s)
        if not bool(tf["trigger"].iloc[t]):
            continue
        funnel["triggered"] += 1
        if allowed and str(pre.frames[s]["regime_label"].iloc[t]) not in allowed:
            funnel["regime_filtered"] += 1
            continue
        if not pre.eligible[s][t]:
            funnel["not_gate_eligible"] += 1
            continue
        hint = float(tf["invalidation_hint"].iloc[t])
        pri = float(tf["setup_score"].iloc[t])
        opp = int(tf["opportunity"].iloc[t]) if "opportunity" in tf else -1
        raw.append((s, hint if math.isfinite(hint) else None, pri if math.isfinite(pri) else 0.0, opp))
    funnel["signals"] = len(raw)
    same_day = frozenset(s for s, *_ in raw)
    signals = []
    for s, hint, pri, opp in sorted(raw):
        f = pre.frames[s].iloc[t]
        entry = {"symbol": s, "sector": pre.sector.get(s), "priority": _r(pri),
                 "regime": str(pre.frames[s]["regime_label"].iloc[t]),
                 "features": {k: _r(f.get(k)) for k in FEATURES[sid] if k in f},
                 "bar_sha16": _bar_hash(pre.bars.get(s), pre.sessions[t]), "held_in_shadow": s in held}
        if opp >= 0:
            entry["opportunity_session"] = pre.sessions[opp].date().isoformat()
        got = plan_signal(m, s, t, pol, S.LEVELS, invalidation_hint=hint)
        if got is None:
            funnel["levels_rejected"] += 1
            close, atr = float(m.c[s][t]), float(m.atr[s][t])
            loose = (pol.exit.uses_target and math.isfinite(close) and math.isfinite(atr)
                     and isinstance(compute_levels(close, atr, hint, float(m.resistance[s][t]), LevelConfig()), Levels))
            funnel["rejected_at_25_passing_at_10"] += int(bool(loose))
            signals.append(entry | {"status": "LEVELS_REJECTED"})
            continue
        lv, plan = got
        atr = float(m.atr[s][t])
        k_atr = stop_distance_atr(lv, atr)
        match, chosen = control_stratum(m, s, t, None, same_day, MIN_STRATUM)
        rng = np.random.default_rng(_seed(sid, arm, s, t_abs))
        ordered, rejected = [], 0
        for j in rng.permutation(len(chosen)):
            cs = m.symbols[chosen[j]]
            g2 = plan_signal(m, cs, t, pol, S.LEVELS, stop_distance_atr=k_atr)
            if g2 is None:
                rejected += 1
                continue
            ordered.append([cs, *_plan(g2[1])])
            if len(ordered) >= CONTROL_RESERVE:
                break
        funnel["planned"] += 1
        signals.append(entry | {
            "status": "PLANNED", "plan": _plan(plan), "k_atr": _r(k_atr), "atr_pct": _r(atr / float(m.c[s][t])),
            "net_rr": _r(lv.reward_to_risk_net, 4), "beta_252": _beta(pre, s, t),
            "order": {"type": "buy limit (paper)", "valid_sessions": VALID, "entry": pol.entry.arm, "exit": pol.exit.arm,
                      "horizon_sessions": pol.exit.horizon_sessions},
            "controls": {"match": match, "stratum": len(chosen), "levels_rejected": rejected,
                         "reserve": ordered,
                         "reserve_bar_sha16": {c[0]: _bar_hash(pre.bars.get(c[0]), pre.sessions[t]) for c in ordered}},
        })
    rebalance = None
    if sid == "S2":
        rebalance = t_abs % PROTOCOL_V2["strategies"]["S2"]["rebalance_every_sessions"] == 0
    return {"arm": arm, "strategy_plugin": plugin, "funnel": funnel, "signals": signals,
            "status": "SIGNALS" if funnel["planned"] else "NO_TRADE"} | (
        {"rebalance_session": rebalance} if rebalance is not None else {})


def decide_at(ctx: FrozenContext, t: int, held: dict[str, set[str]] | None = None) -> dict:
    """The frozen lane's decision for session index t of the window (bars and features through t only)."""
    pre = ctx.pre
    t_abs = ctx.first_index + t
    day = pre.sessions[t].date()
    codes = {s: gate_code(pre, s, t, ctx.meta.get(s)) for s in pre.stocks}
    counts: dict[str, int] = {}
    for c in codes.values():
        counts[c] = counts.get(c, 0) + 1
    reg_row = pre.frames[pre.stocks[0]].iloc[t] if pre.stocks else pd.Series(dtype=float)
    regime = {"label": str(pre.regime.iloc[t]),
              "p": {k[9:]: _r(reg_row[k], 4) for k in reg_row.index if str(k).startswith("regime_p_")}}
    arms = {f"{sid}:{arm}": _arm(ctx, sid, arm, plugin, t, t_abs, (held or {}).get(f"{sid}:{arm}", set()))
            for sid, arm, plugin in ARMS}
    w = ctx.s5.iloc[t]
    reb = t_abs % int(S5_ARM[1:]) == 0
    if reb != (not w.isna().all()):
        raise FrozenLaneInvalid(f"S5 rebalance phase disagrees with the protocol axis at {day} (index {t_abs})")
    arms[f"S5:{S5_ARM}"] = {"arm": S5_ARM, "rebalance_session": reb, "regime_label": regime["label"],
                            "weights": {k: _r(v, 6) for k, v in w.items()} if reb else None,
                            "status": ("REBALANCE" if reb else "HOLD")}
    planned = sum(a["funnel"]["planned"] for k, a in arms.items() if not k.startswith("S5"))
    summary = " · ".join(f"{k} {a['funnel']['planned']} planned" if "funnel" in a else
                         f"{k} {'rebalance' if a['rebalance_session'] else 'hold'}" for k, a in arms.items())
    return clean({
        "decision_session": day, "session_index": t_abs, "regime": regime,
        "universe": {"members": sum(1 for c in codes.values() if c != "NOT_MEMBER"), "gate_eligible": counts.get("PASS", 0),
                     "gates_open": counts.get("PASS", 0) > 0, "codes": dict(sorted(counts.items())),
                     "by_symbol": dict(sorted(codes.items()))},
        "arms": arms, "decision": "SHADOW_PLANS" if planned else "NO_TRADE",
        "paper_capital": {"book": "protocol-2 equal-slot book (research.book.run_book: 10 slots, sector cap 30%, "
                          "size = equity / slots, priority order); applied to these records at evaluation",
                          "orders_placed": False},
        "summary": f"{'NO_TRADE' if not planned else f'{planned} paper plan(s)'} · {summary}",
    })


def held_from_ledger(decisions: list[dict], ctx: FrozenContext, t: int) -> dict[str, set[str]]:
    """Causal 'already in the paper book' flags at t: an earlier planned signal of the same arm whose order or position
    is still live through session t (simulated on bars through t). Informational; the registered de-duplication is
    applied at maturity, exactly as in the study."""
    m = ctx.pre.market
    out: dict[str, set[str]] = {}
    first = ctx.first_index
    for d in decisions:
        body = d["body"]
        t0 = int(body["session_index"]) - first
        if not 0 <= t0 < t or t - t0 > W.LABEL_SESSIONS + W.EXTRA_SESSIONS:
            continue
        for key, a in body["arms"].items():
            if key.startswith("S5"):
                continue
            pol = S.arm_policy(a["arm"])
            for sg in a["signals"]:
                if sg.get("status") != "PLANNED" or not m.has(sg["symbol"]):
                    continue
                s = sg["symbol"]
                sl = slice(0, t + 1)
                tr = simulate_v2(m.o[s][sl], m.h[s][sl], m.low[s][sl], m.c[s][sl], t0, _unplan(sg["plan"]), pol, None,
                                 m.valid[s][sl])
                live = (tr.status == "DATA_END") or (tr.filled and "ENDED_BEFORE_HORIZON" in tr.flags) or (
                    tr.filled and tr.pess.exit_index >= t) or (not tr.filled and t0 + VALID >= t)
                if live:
                    out.setdefault(key, set()).add(s)
    return out


# ---- outcomes -----------------------------------------------------------------------------------------------------------
@dataclass
class OutcomeData:
    """Bars from the decision session t (local index 0) to the final data end, and the quarantine dates."""

    sessions: pd.DatetimeIndex
    bars: dict[str, pd.DataFrame]
    quarantine: dict[str, list[pd.Timestamp]]
    data_end: date

    def arrays(self, sym: str):
        df = self.bars.get(sym)
        if df is None:
            nan = np.full(len(self.sessions), np.nan)
            return nan, nan, nan, nan, np.zeros(len(self.sessions), bool)
        al = df.reindex(self.sessions)
        o, h, lo, c = (al[k].astype(float).to_numpy() for k in ("open", "high", "low", "close"))
        return o, h, lo, c, np.isfinite(o) & np.isfinite(h) & np.isfinite(lo) & np.isfinite(c)


def _sim(od: OutcomeData, sym: str, plan: OrderPlan, pol: ExecutionPolicy):
    o, h, lo, c, ok = od.arrays(sym)
    return simulate_v2(o, h, lo, c, 0, plan, pol, None, ok)


def _vals(tr, pol) -> dict:
    out = {"value": per_signal_net(tr, pol), "value_opt": per_signal_net(tr, pol, "opt")}
    for c in COSTS:
        cc = ExecutionPolicy(pol.entry, pol.exit, Costs(c))
        out[f"value_{c}"], out[f"value_opt_{c}"] = per_signal_net(tr, cc), per_signal_net(tr, cc, "opt")
    return out


def _spy_leg(od: OutcomeData, tr) -> float | None:
    if not tr.filled:
        return None
    o, _h, _lo, c, _ok = od.arrays(BENCHMARK)
    a = o[tr.entry_index]
    b = o[tr.pess.exit_index] if tr.pess.gap else c[tr.pess.exit_index]
    return _r(b / a - 1) if math.isfinite(a) and math.isfinite(b) and a > 0 else None


def _accept(sym: str, tr, od: OutcomeData) -> bool:
    return not S.quarantined(sym, 0, tr, od.sessions, od.quarantine, VALID)


def _resolved(tr) -> bool:
    return tr.status not in ("DATA_END",) and not (tr.filled and "ENDED_BEFORE_HORIZON" in tr.flags)


def unresolved(decision: dict, od: OutcomeData) -> int:
    """Planned trades whose label is not yet complete on this data (they may need a few more sessions)."""
    n = 0
    for key, a in decision["body"]["arms"].items():
        if key.startswith("S5"):
            continue
        pol = S.arm_policy(a["arm"])
        for sg in a["signals"]:
            if sg.get("status") == "PLANNED" and not _resolved(_sim(od, sg["symbol"], _unplan(sg["plan"]), pol)):
                n += 1
    return n


def mature(decision: dict, od: OutcomeData, busy: dict[str, int]) -> dict:
    """Label one decision with the study's rules. `busy` (arm|symbol -> last busy protocol index) is updated in place;
    outcomes must be computed in decision order, as the study iterates signals in time order."""
    body = decision["body"]
    t_abs = int(body["session_index"])
    arms_out = {}
    revisions = []
    for key, a in body["arms"].items():
        if key.startswith("S5"):
            continue
        pol, pol_tt = S.arm_policy(a["arm"]), S.arm_policy(a["arm"], TT)
        rows, counts = [], {"planned": 0, "kept": 0, "quarantined": 0, "dedup_skipped": 0, "incomplete_label": 0}
        for sg in a["signals"]:
            if sg.get("status") != "PLANNED":
                continue
            counts["planned"] += 1
            s, plan = sg["symbol"], _unplan(sg["plan"])
            now_hash = _bar_hash(od.bars.get(s), od.sessions[0])
            if now_hash != sg.get("bar_sha16"):
                revisions.append({"arm": key, "symbol": s, "decision_bar_sha16": sg.get("bar_sha16"),
                                  "final_bar_sha16": now_hash})
            tr = _sim(od, s, plan, pol)
            row = {"symbol": s}
            bk = f"{key}|{s}"
            if not _accept(s, tr, od):
                counts["quarantined"] += 1
                rows.append(row | {"status": "QUARANTINED"})
                continue
            if t_abs <= busy.get(bk, -1):
                counts["dedup_skipped"] += 1
                rows.append(row | {"status": "DEDUP_SKIPPED"})
                continue
            busy[bk] = t_abs + (tr.pess.exit_index if tr.filled else VALID)
            v = _vals(tr, pol)
            if v["value"] is None:
                counts["incomplete_label"] += 1
                rows.append(row | {"status": "INCOMPLETE_LABEL", "trade_status": tr.status, "flags": list(tr.flags)})
                continue
            counts["kept"] += 1
            tt = _sim(od, s, plan, pol_tt)
            tt_ok = _accept(s, tt, od)
            # controls: the frozen draw order; the first K that survive quarantine and have a complete label
            cvals, ctr, csyms = [], [], []
            for cand in sg["controls"]["reserve"]:
                if len(cvals) >= K:
                    break
                cs, cplan = cand[0], _unplan(cand[1:])
                tr2 = _sim(od, cs, cplan, pol)
                if not _accept(cs, tr2, od) or per_signal_net(tr2, pol) is None:
                    continue
                cvals.append(_vals(tr2, pol))
                ctr.append(tr2)
                csyms.append((cs, cplan))
                h2 = _bar_hash(od.bars.get(cs), od.sessions[0])
                if h2 != sg["controls"]["reserve_bar_sha16"].get(cs):
                    revisions.append({"arm": key, "symbol": cs, "role": "control",
                                      "decision_bar_sha16": sg["controls"]["reserve_bar_sha16"].get(cs),
                                      "final_bar_sha16": h2})
            ctrl = {"n": len(cvals), "symbols": [x[0] for x in csyms], "match": sg["controls"]["match"],
                    "reserve_exhausted": len(cvals) < K and len(sg["controls"]["reserve"]) >= CONTROL_RESERVE}
            if cvals:
                for fld in cvals[0]:
                    xs = [c[fld] for c in cvals if c[fld] is not None]
                    ctrl[fld] = _r(np.mean(xs)) if xs else None
                fills = [x for x in ctr if x.filled]
                ctrl["fill_rate"] = _r(len(fills) / len(ctr), 4)
                ctrl["ambiguous_share"] = _r(sum(x.ambiguity != "NONE" for x in fills) / len(fills), 4) if fills else None
                tt_c, tt_co = [], []
                for cs, cplan in csyms:
                    r2 = _sim(od, cs, cplan, pol_tt)
                    if _accept(cs, r2, od):
                        tt_c.append(per_signal_net(r2, pol_tt))
                        tt_co.append(per_signal_net(r2, pol_tt, "opt"))
                tt_c, tt_co = [x for x in tt_c if x is not None], [x for x in tt_co if x is not None]
                ctrl["value_tt"] = _r(np.mean(tt_c)) if tt_c else None
                ctrl["value_tt_opt"] = _r(np.mean(tt_co)) if tt_co else None
            gross = _r(tr.pess.exit_price / tr.entry_price - 1) if tr.filled else None
            spy = _spy_leg(od, tr)
            beta = sg.get("beta_252")
            rows.append(row | {k: _r(x) for k, x in v.items()} | {
                "status": "KEPT", "filled": tr.filled, "fill_kind": tr.fill_kind, "ambiguity": tr.ambiguity,
                "outcome": tr.pess.outcome if tr.filled else tr.status,
                "outcome_opt": tr.opt.outcome if tr.filled else tr.status, "flags": list(tr.flags),
                "entry_session": od.sessions[tr.entry_index].date().isoformat() if tr.filled else None,
                "exit_session": od.sessions[tr.pess.exit_index].date().isoformat() if tr.filled else None,
                "held_sessions": tr.sessions_held() if tr.filled else None, "gross_return_pess": gross,
                "value_tt": _r(per_signal_net(tt, pol_tt)) if tt_ok else None,
                "value_tt_opt": _r(per_signal_net(tt, pol_tt, "opt")) if tt_ok else None,
                "ctrl": ctrl, "paired": _r(v["value"] - ctrl["value"]) if ctrl.get("value") is not None else None,
                "paired_opt": _r(v["value_opt"] - ctrl["value_opt"]) if ctrl.get("value_opt") is not None
                and v["value_opt"] is not None else None,
                "paper_market_leg": {"spy_return_same_window": spy,
                                     "minus_spy": _r(gross - spy) if gross is not None and spy is not None else None,
                                     "minus_beta_spy": _r(gross - beta * spy) if None not in (gross, spy, beta) else None,
                                     "note": "HYPOTHETICAL paper hedge: no borrow, financing, dividend or hedge cost"},
            })
        arms_out[key] = {"arm": a["arm"], "counts": counts, "signals": rows}
    return {"arms": arms_out, "revisions": revisions}


def s5_outcomes(decisions: list[dict], od5: OutcomeData, first_index: int, sessions_wanted: list[date]) -> dict[str, dict]:
    """Forward S5 book (from cash at the first recorded forward rebalance) and its exposure-matched SPY/cash benchmark;
    returns {decision_session: the t+1 row}. `first_index` is the protocol-axis position of od5.sessions[0]. A rebalance
    session without a DECISION keeps the drifted book and is flagged."""
    idx = od5.sessions
    rebal_every = int(S5_ARM[1:])
    is_rebalance = {d: (first_index + i) % rebal_every == 0 for i, d in enumerate(idx)}
    cols = sorted({k for d in decisions for k in (d["body"]["arms"][f"S5:{S5_ARM}"].get("weights") or {})})
    w = pd.DataFrame(np.nan, index=idx, columns=cols)
    recorded = {}
    for d in decisions:
        a = d["body"]["arms"][f"S5:{S5_ARM}"]
        day = pd.Timestamp(d["body"]["decision_session"])
        recorded[day] = a["rebalance_session"]
        if a["rebalance_session"] and day in w.index and a.get("weights"):
            w.loc[day] = [a["weights"].get(c, 0.0) for c in cols]
    out: dict[str, dict] = {}
    started = w.notna().any(axis=1)
    if not cols or not started.any():
        return {d.isoformat(): {"book_started": False, "note": "no forward S5 rebalance recorded yet: the book is in cash"}
                for d in sessions_wanted}
    first = started.idxmax()
    w.loc[w.index < first] = np.nan
    opens = {s: od5.bars[s]["open"].astype(float) for s in [*cols, BENCHMARK] if s in od5.bars}
    closes = {s: od5.bars[s]["close"].astype(float) for s in [*cols, BENCHMARK] if s in od5.bars}
    r, x, xo = allocation_returns(w, opens, closes, C, with_open_exposure=True)
    c = closes[BENCHMARK].reindex(idx).ffill()
    o = opens[BENCHMARK].reindex(idx).fillna(c.shift(1))
    night, dayr = (o / c.shift(1) - 1).fillna(0.0), (c / o - 1).fillna(0.0)
    bench = (1 + x.shift(1).fillna(0.0) * night) * (1 + xo * dayr) - 1
    for d in sessions_wanted:
        ts = pd.Timestamp(d)
        if ts not in idx or idx.get_loc(ts) + 1 >= len(idx):
            continue
        i = idx.get_loc(ts) + 1
        if ts < first:
            out[d.isoformat()] = {"book_started": False, "session": idx[i].date().isoformat(), "net": 0.0,
                                  "exposure_open": 0.0, "bench": 0.0, "active": 0.0}
            continue
        last_reb = max((k for k, v in recorded.items() if v and k <= ts), default=None)
        missed = any(is_rebalance[k] and k not in recorded for k in idx[(idx > (last_reb or first)) & (idx <= ts)])
        out[d.isoformat()] = {"book_started": True, "session": idx[i].date().isoformat(), "net": _r(r.iloc[i], 8),
                              "exposure_open": _r(xo.iloc[i], 6), "exposure_close": _r(x.iloc[i], 6),
                              "bench": _r(bench.iloc[i], 8), "active": _r(r.iloc[i] - bench.iloc[i], 8),
                              "weights_from": last_reb.date().isoformat() if last_reb is not None else None,
                              "rebalance_missed": bool(missed)}
    return out


# ---- golden behaviour fingerprint -------------------------------------------------------------------------------------
GOLDEN = {"per_sector": 5, "seed": 23, "start": date(2023, 1, 3), "end": date(2024, 9, 30), "sessions": 80}
# sha256 of the golden decisions (rounded to 4 dp). Pinned by tests/test_forward_shadow_parity.py and checked by every
# recording run: a behaviour change in any function the frozen lane calls changes it and stops the series (fail closed).
PINNED_FINGERPRINT = "3d8e3b5abfcb5ee5a3f5d3120e0515129010d440667a3d0167bcc75065243083"


def _round_all(x, nd: int = 4):
    if isinstance(x, dict):
        return {k: _round_all(v, nd) for k, v in x.items()}
    if isinstance(x, list):
        return [_round_all(v, nd) for v in x]
    if isinstance(x, float):
        return round(x, nd)
    return x


def golden_decisions() -> list[dict]:
    """Decisions of the frozen logic on a fixed synthetic fixture (never market data), last GOLDEN['sessions'] days."""
    from apex_worker.research.realeq.fixture_v2 import factor_frames_v2

    bars = factor_frames_v2(per_sector=GOLDEN["per_sector"], seed=GOLDEN["seed"], start=GOLDEN["start"],
                            end=GOLDEN["end"])
    ctx = FrozenContext(S.prepare(bars), 0)
    n = len(ctx.pre.sessions)
    return [decide_at(ctx, t) for t in range(n - GOLDEN["sessions"], n)]


def golden_fingerprint() -> str:
    return hashlib.sha256(canonical(_round_all(golden_decisions()))).hexdigest()
