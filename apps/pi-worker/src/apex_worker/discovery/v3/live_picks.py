"""Decision and labelling logic of `v3x-live-picks-v1`: the live engine's own end-of-day picks, recorded prospectively
(picks/README.md). Research only: nothing is posted, ordered or alerted, and nothing here changes the live engine.

Decision (after the close of t, from bars through t only; `decide`):
* the live worker's end-of-day path with its default configuration: a feature panel over the last 320 sessions, the
  candidate engine (gates, the enabled H1-H7 triggers, quality, sector crowding) and the signal policy (levels, caps,
  tiers). No model is loaded, so every setup is UNVALIDATED and only research setups exist (quality >= 75, one a
  session). There is no catalyst feed (the worker has none), and earnings come from the SEC view as of the decision
  instant;
* the picks still open when the worker scans (their own lifecycle replayed on bars through t - 1, as the worker
  evaluates its signals after the scan) count toward the policy's caps exactly as published signals do;
* the PICK is what the worker would post in group mode. QUALIFIED setups are those it would also post if it posted up to
  five a session: a larger sample of the engine's top setups, recorded the same way but never called picks;
* every plan is stored in units of the decision close (no prices), with its stop distance in ATR and an ordered reserve
  of matched controls: gate-eligible stocks of the same session, the protocol-2 stratum hierarchy (sector x volatility
  tercile -> sector -> tercile -> pool; research/controls.py), the plan's own stop distance and a seeded draw order. All
  of it is fixed before any outcome exists.

Label (`label`, once the decision's 22-session window is final): the live lifecycle's own rules (research.execution
.simulate, as signals.monitor applies them: entry inside the zone within 2 sessions, stop first on an ambiguous bar, gap
exits at the open, time exit after 20 sessions, 10 bps a side) applied to the plan scaled by the final close of t, the
first K controls whose labels are complete and not quarantined (a no-fill counts as 0: no capital was deployed), and SPY
and the sector ETF from the entry session's open to the exit session's close.
"""

from __future__ import annotations

import hashlib
import json
import math
import zlib
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import date

import numpy as np
import pandas as pd

from apex_worker.features.compute import FEATURE_CATALOG, FEATURE_VERSION
from apex_worker.features.panel import LIVE_LOOKBACK_SESSIONS, AssetMeta, build_panel
from apex_worker.market.calendar import MarketCalendar
from apex_worker.research.controls import MATCH_LEVELS
from apex_worker.research.execution import ExecConfig, TradeOutcome, simulate
from apex_worker.research.realeq.universe_v2 import u1_meta
from apex_worker.research.training_data import BENCHMARK, SECTOR_ETFS
from apex_worker.signals.candidates import CandidateEngine, GateConfig, gate_row
from apex_worker.signals.context import DECISION_LAG, context_frame, decision_instant
from apex_worker.signals.levels import LEVELS_VERSION, Levels, compute_levels
from apex_worker.signals.policy import POLICY_VERSION, PolicyConfig, SignalPolicy
from apex_worker.signals.quality import QUALITY_VERSION
from apex_worker.signals.strategies.library import Strategy, enabled_strategies

LIVE_PICKS_VERSION = "live-picks-1"
LOOKBACK = LIVE_LOOKBACK_SESSIONS  # the worker's bounded panel (320 sessions)
RELEASE_MODE = "PERSONAL_RESEARCH"  # the policy decides as in every mode; the lane posts nothing
QUALIFIED_MAX = 5  # setups a session the policy would post without its one-a-session research cap
K = 10  # controls per plan (protocol-2's K)
RESERVE = 3 * K  # draw-order candidates frozen per plan; the first K with complete labels become the controls
MIN_STRATUM = 5
CONTROL_SALT = "v3x-live-picks-controls-1"
GATES = GateConfig()
POLICY = PolicyConfig()
LEVELS = POLICY.levels
EXEC = ExecConfig(cost_bps_per_side=LEVELS.cost_bps_per_side, entry_valid_sessions=LEVELS.entry_valid_sessions,
                  horizon_sessions=LEVELS.horizon_max_sessions)
LIFETIME_SESSIONS = EXEC.entry_valid_sessions + EXEC.horizon_sessions  # a plan is resolved 22 sessions after t
PICK, QUALIFIED = "pick", "qualified"


def _strategies() -> list[Strategy]:
    return enabled_strategies()


ENGINE = {  # what the decisions depend on, by version; any change needs a new series (the fingerprint also pins it)
    "feature_version": FEATURE_VERSION, "feature_catalog": sorted(FEATURE_CATALOG), "quality_version": QUALITY_VERSION,
    "levels_version": LEVELS_VERSION, "policy_version": POLICY_VERSION, "decision_lag_minutes": 15,
    "strategies": {s.strategy_id: s.spec.version for s in _strategies()},
}
ENGINE_SCHEMA_SHA256 = hashlib.sha256(json.dumps(ENGINE, sort_keys=True).encode()).hexdigest()
PARAMETERS = {
    "engine": "the live worker's end-of-day path (runtime.engine.ApexEngine.end_of_day), default configuration",
    "gates": asdict(GATES), "policy": asdict(POLICY), "strategies": sorted(ENGINE["strategies"]),
    "models": "none: every setup is UNVALIDATED, so only research setups exist",
    "catalysts": "none (the worker has no catalyst feed)", "lookback_sessions": LOOKBACK, "benchmark": BENCHMARK,
    "sector_etfs": dict(SECTOR_ETFS),
    "open_picks": "picks open at the worker's scan (lifecycle through t - 1) count toward the caps as published signals",
    "qualified_max_per_session": QUALIFIED_MAX,
    "controls": {"k": K, "reserve": RESERVE, "min_stratum": MIN_STRATUM, "hierarchy": list(MATCH_LEVELS),
                 "pool": "stocks whose live gate is PASS at t, minus the session's picks and qualified setups",
                 "levels": "the plan's stop distance in ATR; the same target rule, resistance cap and net reward/risk floor",
                 "no_fill": 0.0, "seed": CONTROL_SALT},
    "execution": {"simulator": "research.execution.simulate (signals.monitor's rules)", **asdict(EXEC),
                  "same_bar_rule": "STOP_FIRST", "plan_scaled_by": "the final split-adjusted close of t"},
    "benchmarks": "SPY and the sector ETF, from the entry session's open to the exit session's close",
    "earnings": "SEC 8-K item 2.02 history as of the decision instant; WITHIN_HORIZON vetoes, missing data is UNKNOWN",
    "units": "plans in units of the decision close; no prices are recorded",
}


def meta() -> dict[str, AssetMeta]:
    """U1 stocks, SPY and the sector ETFs, as the worker labels them (sector from the S&P 500 snapshot)."""
    return u1_meta()


def _r(x: float | None) -> float | None:
    return None if x is None or not math.isfinite(float(x)) else round(float(x), 6)


def plan_of(lv: Levels, close: float, atr: float) -> dict:
    """The plan in units of the decision close (never prices) and its stop distance in ATR."""
    return {"entry_min": _r(lv.entry_min / close), "entry_max": _r(lv.entry_max / close), "stop": _r(lv.stop / close),
            "target": _r(lv.target / close),
            "target_secondary": _r(lv.target_secondary / close) if lv.target_secondary else None,
            "reward_to_risk_net": lv.reward_to_risk_net, "stop_atr": _r((lv.mid - lv.stop) / atr)}


def levels_at(plan: dict, close: float) -> tuple[float, float, float, float]:
    return (plan["entry_min"] * close, plan["entry_max"] * close, plan["stop"] * close, plan["target"] * close)


def _ohlc(df: pd.DataFrame) -> tuple[np.ndarray, ...]:
    return tuple(df[k].to_numpy(float) for k in ("open", "high", "low", "close"))


def _replay(df: pd.DataFrame, day: pd.Timestamp, plan: dict) -> TradeOutcome | None:
    """The plan decided at `day` replayed through the last row of `df` (the live lifecycle's rules)."""
    if day not in df.index:
        return None
    i0 = df.index.get_loc(day)
    o, h, low, c = _ohlc(df)
    if not (math.isfinite(c[i0]) and c[i0] > 0):
        return None
    return simulate(o, h, low, c, i0, *levels_at(plan, c[i0]), EXEC)


def pick_status(df: pd.DataFrame | None, day: str, plan: dict) -> dict:
    """A pick's lifecycle on the bars given: PUBLISHED (entry window open), ACTIVE (filled, no exit yet) or closed."""
    res = _replay(df, pd.Timestamp(day), plan) if df is not None else None
    if res is None:
        return {"status": "NO_DATA", "open": True}  # the worker keeps a signal without bars in its state
    if not res.filled:
        is_open = res.outcome == "DATA_END"
        return {"status": "PUBLISHED" if is_open else "EXPIRED", "open": is_open}
    out = {"entry_session": df.index[res.entry_index].date().isoformat(), "sessions_held": len(df) - 1 - res.entry_index}
    if res.outcome == "DATA_END":  # still held: return to the last close, after the modeled entry cost only
        last = float(df["close"].iloc[-1])
        return {"status": "ACTIVE", "open": True, **out, "return_to_date": _r(last / res.entry_price - 1)}
    return {"status": res.outcome, "open": False, **out, "sessions_held": res.sessions_held,
            "return_net": _r(res.return_net)}


def open_picks(earlier: list[tuple[str, dict]], bars: dict[str, pd.DataFrame], day: date,
               cal: MarketCalendar) -> list[dict]:
    """Earlier picks that may still be open at t: their status as the worker sees it at its scan (bars through t - 1)
    and with bar t. Older picks are resolved by construction (22 sessions) and skipped."""
    t = pd.Timestamp(day)
    out = []
    for session, e in earlier:
        if cal.count_sessions_after(date.fromisoformat(session), day) > LIFETIME_SESSIONS:
            continue
        df = bars.get(e["symbol"])
        before = df[df.index < t] if df is not None else None
        scan = pick_status(before, session, e["plan"])
        now = pick_status(df, session, e["plan"])
        out.append({"session": session, "symbol": e["symbol"], "sector": e["sector"], "open_at_scan": scan["open"],
                    "status_at_scan": scan["status"], "status_t": now["status"],
                    **{k: v for k, v in now.items() if k not in ("status", "open")}})
    return out


# ---- matched controls (fixed at decision time) -------------------------------------------------------------------------
def stock_table(panel, session: pd.Timestamp, cal: MarketCalendar,
                earnings_status: Callable[[str, str, int], str] | None) -> dict[str, dict]:
    """Every U1 stock's live gate at t and the inputs a control's levels need (the scan's own context rows)."""
    sday = session.date().isoformat()
    out: dict[str, dict] = {}
    for sym in sorted(panel.features):
        m = panel.meta.get(sym)
        f = panel.features[sym]
        if m is None or m.asset_kind != "stock" or session not in f.index:
            continue
        ctx = context_frame(panel, sym, [], cal)
        row = ctx.loc[session].to_dict()
        latest = f.index[f.index <= session][-1].date().isoformat()
        earnings = earnings_status(sym, sday, GATES.horizon_sessions) if earnings_status else "UNKNOWN"
        status, _codes = gate_row(row, m, GATES, latest, sday, earnings)
        past = ctx["close"].loc[:session]
        out[sym] = {"status": status, "sector": m.sector, "close": float(row["close"]), "atr": float(row["atr_14"]),
                    "atr_pct": float(row["atr_pct_14"]),
                    "resistance": float(past.iloc[-121:-1].max()) if len(past) > 60 else float("nan")}
    return out


def _terciles(table: dict[str, dict]) -> dict[str, int]:
    """Volatility terciles (atr_pct_14) among the gate-eligible stocks of the session, as research/controls.py forms them."""
    syms = [s for s in sorted(table) if table[s]["status"] == "PASS" and math.isfinite(table[s]["atr_pct"])]
    if len(syms) < 3:
        return {}
    r = pd.Series([table[s]["atr_pct"] for s in syms]).rank(method="first", pct=True).to_numpy()
    return {s: min(int(x * 3 - 1e-9), 2) for s, x in zip(syms, r, strict=True)}


def control_reserve(table: dict[str, dict], terc: dict[str, int], symbol: str, sector: str | None, stop_atr: float,
                    exclude: set[str], day: str) -> dict:
    """The ordered reserve of a plan's controls: first stratum with >= MIN_STRATUM candidates, seeded draw order, each
    candidate's own levels at the plan's stop distance (a rejected one is skipped). Fixed before any outcome exists."""
    pool = [s for s in sorted(table) if table[s]["status"] == "PASS" and s not in exclude and s != symbol]
    mine = terc.get(symbol, -1)
    same_sec = {s for s in pool if sector is not None and table[s]["sector"] == sector}
    same_vol = {s for s in pool if mine >= 0 and terc.get(s) == mine}
    stratum, cands = "none", []
    for name, keep in zip(MATCH_LEVELS, (same_sec & same_vol, same_sec, same_vol, set(pool)), strict=True):
        if len(keep) >= MIN_STRATUM:
            stratum, cands = name, sorted(keep)
            break
    rng = np.random.default_rng(zlib.crc32(f"{CONTROL_SALT}|{day}|{symbol}".encode()))
    reserve = []
    for j in rng.permutation(len(cands)):
        row = table[cands[j]]
        lv = compute_levels(row["close"], row["atr"], None, row["resistance"], LEVELS, stop_distance_atr=stop_atr)
        if isinstance(lv, Levels):
            p = plan_of(lv, row["close"], row["atr"])
            reserve.append([cands[j], p["entry_min"], p["entry_max"], p["stop"], p["target"]])
            if len(reserve) >= RESERVE:
                break
    return {"stratum": stratum, "candidates": len(cands), "reserve": reserve}


# ---- the decision ----------------------------------------------------------------------------------------------------
def _entry(kind: str, cand: dict, decision) -> dict:
    i = cand["_internal"]
    lv = compute_levels(i["close"], i["atr"], i.get("invalidation_hint"), i.get("resistance"), LEVELS)
    if not isinstance(lv, Levels):  # the policy posted it, so its levels exist: anything else is a bug, never a record
        raise RuntimeError(f"levels of {cand['symbol']} differ from the policy's")
    return {"kind": kind, "symbol": cand["symbol"], "name": i.get("name"), "sector": i.get("sector") or "unknown",
            "strategy_id": cand["strategy_id"], "strategy_version": cand["strategy_version"],
            "quality": cand["quality_score"], "tier": decision.outcome,
            "confidence": decision.signal["confidence"]["status"], "earnings_status": i.get("earnings_status"),
            "regime": cand["regime"],
            "evidence": {"positive": [e["code"] for e in cand["evidence"]["positive"]],
                         "negative": [e["code"] for e in cand["evidence"]["negative"]]},
            "plan": plan_of(lv, i["close"], i["atr"]) | {"entry_valid_sessions": EXEC.entry_valid_sessions,
                                                         "horizon_sessions": EXEC.horizon_sessions,
                                                         "cost_bps_per_side": EXEC.cost_bps_per_side}}


def decide(bars: dict[str, pd.DataFrame], day: date, earlier: list[tuple[str, dict]],
           earnings_status: Callable[[str, str, int], str] | None, cal: MarketCalendar,
           asset_meta: dict[str, AssetMeta] | None = None, strategies: list[Strategy] | None = None) -> dict:
    """The decision body for session `day` (see the module docstring). `earlier`: (session, plan entry) of every earlier
    pick. Deterministic: the same bars, earlier picks and earnings view always give the same body."""
    t = pd.Timestamp(day)
    bars = {s: df[df.index <= t] for s, df in bars.items()}  # the panel keeps the last 320 sessions of what it gets
    md = asset_meta or meta()
    panel = build_panel(bars, {s: m for s, m in md.items() if s in bars}, BENCHMARK, dict(SECTOR_ETFS), lookback=LOOKBACK)
    now = decision_instant(t, cal)
    scan = CandidateEngine(strategies if strategies is not None else _strategies(), GATES, cal, is_demo=False).scan(
        panel, t, (), earnings_status, created_at=now)
    opened = open_picks(earlier, bars, day, cal)
    open_signals = [{"symbol": p["symbol"], "risk": {"sector": p["sector"]}} for p in opened if p["open_at_scan"]]

    def run(cfg: PolicyConfig):
        return SignalPolicy(cfg, cal).decide(scan, models={}, release_mode=RELEASE_MODE, quotes={},
                                             open_signals=open_signals, now=now, compliance={})

    posted = run(POLICY)
    wide = run(replace(POLICY, research_max_per_session=QUALIFIED_MAX,
                       max_new_per_session=max(QUALIFIED_MAX, POLICY.max_new_per_session)))
    by_id = {c["id"]: c for c in scan.candidates}
    picks = [(d, by_id[d.candidate_id]) for d in posted.decisions if d.signal]
    pick_ids = {d.candidate_id for d, _ in picks}
    wide_signals = [d for d in wide.decisions if d.signal]
    if not pick_ids <= {d.candidate_id for d in wide_signals}:
        raise RuntimeError("the wider policy pass does not contain the pick")  # the caps only ever widen
    qualified = [(d, by_id[d.candidate_id]) for d in wide_signals if d.candidate_id not in pick_ids]
    plans = [_entry(PICK, c, d) for d, c in picks] + [_entry(QUALIFIED, c, d) for d, c in qualified]
    if plans:
        table = stock_table(panel, t, cal, earnings_status)
        terc = _terciles(table)
        exclude = {p["symbol"] for p in plans}
        for p in plans:
            p["controls"] = control_reserve(table, terc, p["symbol"], None if p["sector"] == "unknown" else p["sector"],
                                            p["plan"]["stop_atr"], exclude, day.isoformat())
    decided = {d.candidate_id: d for d in posted.decisions}
    candidates = [{"symbol": c["symbol"], "strategy_id": c["strategy_id"], "quality": c["quality_score"],
                   "gate": c["gate_status"], "outcome": "PICK" if c["id"] in pick_ids else "QUALIFIED"
                   if any(c["id"] == d.candidate_id for d, _ in qualified) else "REJECTED",
                   "reasons": (decided[c["id"]].reasons if c["id"] in decided else [])[:4]}
                  for c in scan.candidates[:25]]
    if picks:
        p = plans[0]
        summary = (f"PICK {p['symbol']} ({p['strategy_id']}, quality {p['quality']:.0f}, {p['confidence']}): entry "
                   f"{_pct(p['plan']['entry_min'])} to {_pct(p['plan']['entry_max'])} of the close for "
                   f"{EXEC.entry_valid_sessions} sessions, stop {_pct(p['plan']['stop'])}, target "
                   f"{_pct(p['plan']['target'])}, at most {EXEC.horizon_sessions} sessions")
    else:
        summary = f"NO PICK ({posted.no_trade_reason})"
    if qualified:
        summary += f"; also qualified: {', '.join(e['symbol'] for e in plans if e['kind'] == QUALIFIED)}"
    return {"plans": plans, "candidates": candidates, "open_picks": opened,
            "no_trade_reason": None if picks else posted.no_trade_reason,
            "scan": scan.stats.to_dict() | {"regime": scan.regime, "decision_instant": scan.asof_at},
            "engine": {"version": LIVE_PICKS_VERSION, "schema_sha256": ENGINE_SCHEMA_SHA256,
                       "decision_lag_minutes": int(DECISION_LAG.total_seconds() // 60)},
            "summary": summary + ". Research only: an unvalidated engine's would-be pick, not advice."}


def _pct(ratio: float) -> str:
    return f"{(ratio - 1) * 100:+.1f}%"


# ---- labels ----------------------------------------------------------------------------------------------------------
def _quarantined(q: dict[str, list], sym: str, lo: pd.Timestamp, hi: pd.Timestamp) -> bool:
    return any(lo < pd.Timestamp(x) <= hi for x in q.get(sym, []))


def _sim(frames: dict[str, pd.DataFrame], sessions: pd.DatetimeIndex, sym: str, t: pd.Timestamp,
         plan: dict) -> tuple[TradeOutcome, pd.DatetimeIndex] | None:
    df = frames.get(sym)
    if df is None or t not in df.index:
        return None
    axis = sessions[sessions >= t]
    al = df.reindex(axis)  # one row per session: a missing bar is NaN and skipped, never shifts the horizon
    c0 = float(al["close"].iloc[0])
    if not (math.isfinite(c0) and c0 > 0):
        return None
    return simulate(*_ohlc(al), 0, *levels_at(plan, c0), EXEC), axis


def _span(frames: dict, axis: pd.DatetimeIndex, sym: str, i: int, j: int) -> float | None:
    df = frames.get(sym)
    if df is None:
        return None
    al = df.reindex(axis)
    a, b = float(al["open"].iloc[i]), float(al["close"].iloc[j])
    return _r(b / a - 1) if math.isfinite(a) and math.isfinite(b) and a > 0 else None


def _net0(res: TradeOutcome) -> float:
    return float(res.return_net) if res.filled else 0.0


def label_plan(e: dict, day: str, sessions: pd.DatetimeIndex, frames: dict[str, pd.DataFrame],
               quarantine: dict[str, list]) -> dict:
    t = pd.Timestamp(day)
    got = _sim(frames, sessions, e["symbol"], t, e["plan"])
    head = {"kind": e["kind"], "symbol": e["symbol"], "strategy_id": e["strategy_id"], "quality": e["quality"]}
    if got is None:
        return head | {"outcome": "NO_DATA", "complete": False}
    res, axis = got
    flags = list(res.flags)
    if res.filled and _quarantined(quarantine, e["symbol"], t, axis[res.exit_index]):
        flags.append("QUARANTINED")
    complete = res.outcome != "DATA_END" and "QUARANTINED" not in flags
    out = head | {"outcome": res.outcome, "filled": res.filled, "complete": complete, "net": _r(_net0(res)),
                  "flags": flags}
    if res.filled:
        etf = SECTOR_ETFS.get(e["sector"])
        out |= {"entry_session": axis[res.entry_index].date().isoformat(),
                "exit_session": axis[res.exit_index].date().isoformat(), "sessions_held": res.sessions_held,
                "gross": _r(res.return_gross), "r_multiple": res.r_multiple, "mfe": _r(res.mfe), "mae": _r(res.mae),
                "gap_through_stop": res.gap_through_stop, "ambiguous_same_bar": res.ambiguous_same_bar,
                "spy": _span(frames, axis, BENCHMARK, res.entry_index, res.exit_index),
                "sector_etf": etf, "sector_etf_return": _span(frames, axis, etf, res.entry_index, res.exit_index)
                if etf else None}
        out["vs_spy"] = _r(res.return_net - out["spy"]) if out["spy"] is not None else None
    ctrl = label_controls(e.get("controls") or {}, t, sessions, frames, quarantine)
    out["controls"] = ctrl
    out["vs_controls"] = (_r(_net0(res) - ctrl["mean_net"]) if complete and ctrl["mean_net"] is not None else None)
    return out


def label_controls(controls: dict, t: pd.Timestamp, sessions: pd.DatetimeIndex, frames: dict[str, pd.DataFrame],
                   quarantine: dict[str, list]) -> dict:
    """The first K reserve candidates whose labels are complete and not quarantined (protocol-2's convention)."""
    vals, syms, fills, skipped = [], [], 0, 0
    for sym, emin, emax, stop, target in controls.get("reserve") or []:
        if len(vals) >= K:
            break
        got = _sim(frames, sessions, sym, t, {"entry_min": emin, "entry_max": emax, "stop": stop, "target": target})
        if got is None:
            skipped += 1
            continue
        res, axis = got
        if res.outcome == "DATA_END" or (res.filled and _quarantined(quarantine, sym, t, axis[res.exit_index])):
            skipped += 1
            continue
        vals.append(_net0(res))
        syms.append(sym)
        fills += res.filled
    return {"stratum": controls.get("stratum"), "n": len(vals), "fills": fills, "skipped": skipped,
            "mean_net": _r(float(np.mean(vals))) if vals else None, "symbols": syms}


def label(body: dict, day: str, sessions: pd.DatetimeIndex, frames: dict[str, pd.DataFrame],
          quarantine: dict[str, list]) -> dict:
    """The outcome body of one matured decision: every plan's label, its controls and benchmarks."""
    plans = [label_plan(e, day, sessions, frames, quarantine) for e in body.get("plans") or []]
    t = pd.Timestamp(day)
    axis = sessions[sessions >= t]
    spy = None
    if len(axis) > LIFETIME_SESSIONS and BENCHMARK in frames:
        c = frames[BENCHMARK]["close"].reindex(axis)
        spy = _r(float(c.iloc[LIFETIME_SESSIONS]) / float(c.iloc[0]) - 1)
    parts = []
    for p in plans:
        if p["outcome"] == "NO_DATA" or p.get("net") is None:
            parts.append(f"{p['symbol']} ({p['kind']}): {p['outcome']}, no label")
            continue
        ctrl = p["controls"]
        parts.append(f"{p['symbol']} ({p['kind']}): {p['outcome']} {p['net'] * 100:+.2f}% net"
                     + (f" in {p['sessions_held']} sessions" if p.get("filled") else "")
                     + (f"; controls {ctrl['mean_net'] * 100:+.2f}% (n {ctrl['n']})" if ctrl["mean_net"] is not None
                        else "")
                     + (f"; SPY {p['spy'] * 100:+.2f}%" if p.get("spy") is not None else ""))
    summary = "; ".join(parts) if parts else (
        "no pick that session" + (f"; SPY {spy * 100:+.2f}% over the next {LIFETIME_SESSIONS} sessions" if spy is not None
                                  else ""))
    return {"plans": plans, "spy_window": spy, "summary": summary + ". Research only."}


__all__ = ["ENGINE", "ENGINE_SCHEMA_SHA256", "PARAMETERS", "PICK", "QUALIFIED", "RESERVE", "K", "decide", "label",
           "label_controls", "label_plan", "levels_at", "meta", "open_picks", "pick_status", "plan_of"]
