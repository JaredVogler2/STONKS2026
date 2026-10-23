"""Discovery V3 execution-cost study (PR-D; amendments 06 sections B6 and E; docs/discovery-v3/EXECUTION_COST_STUDY.md).

The daily-bar simulator fills entries at the session's OPEN and time exits at its CLOSE, and charges an ASSUMED 25 bps
per side. Pilot 1 found that this assumption decides every result (friction is the binding constraint), so this study
MEASURES, on a registered and seeded sample of real entry sessions, what a retail-sized marketable order would have
paid relative to those two prices:

* the SIP NBBO half-spread at open+1 min, open+5 min, 12:00 ET and close-1 min;
* a marketable BUY at the ask at open+1 min (and +5 min) relative to the daily open;
* a marketable SELL at the bid at close-1 min relative to the daily close;
* the first/last one-minute VWAP relative to the daily open/close (what a patient order near the auction gets).

It is a measurement, not a strategy test: no strategy return is read, nothing is selected on outcomes, and the plan
(sample design, instants, aggregation and the cost-model rule) is registered here BEFORE the run. Only aggregates over
at least `min_cell` events are written; no quote, price or per-event row leaves the runner (provider terms). An
endpoint the account is not entitled to makes the study SOURCE_BLOCKED, never estimated.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from apex_worker.discovery.v3 import agents as A
from apex_worker.research.realeq import study_v2 as S

NY = "America/New_York"


@dataclass(frozen=True)
class CostStudyPlan:
    plan_id: str
    title: str
    seed: int
    years: tuple[int, ...]
    per_cell: int  # random eligible entries per (year, liquidity tercile) cell
    s1_events: int  # random draw of protocol-2 S1 E0X1 DEV fills (the strategy stratum)
    instants: tuple[str, ...] = ("open+60", "open+300", "12:00", "close-60")
    lookback_seconds: tuple[int, ...] = (5, 60)  # latest valid NBBO at or before the instant; widen once if none
    min_cell: int = 5  # aggregates over fewer events are suppressed
    sell_fee_bps: float = 0.3  # regulatory fees on sales (SEC section 31, FINRA TAF), upper bound; commissions 0
    max_requests: int = 2400
    concurrency: int = 4
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


COST_STUDY_1 = CostStudyPlan(
    plan_id="cost-study-1", title="Execution cost at the simulator's open and close prices (U1, DEV dates)",
    seed=20261023, years=(2020, 2021, 2022, 2023, 2024, 2025, 2026), per_cell=8, s1_events=42,
    notes="Registered before any quote is read. Strata: entry year x cross-sectional dollar-volume tercile among the "
    "names eligible at the decision close (random eligible entries), plus a random draw of S1 E0X1 DEV fills. The "
    "proposed cost model is the p90 of the per-side marketable cost per tercile plus fees, rounded up to whole bps; it "
    "is a proposal for LATER registered plans and changes no published result.")

COST_PLANS: dict[str, CostStudyPlan] = {p.plan_id: p for p in (COST_STUDY_1,)}


# ---- cost-v3-2: the measured per-side cost model registered with v3-pilot-2 (BEFORE its run) ------------------------
# Derived from cost-study-1-2026-10-23 (run 39908161943; research-results/discovery-v3/cost-study-1-2026-10-23/), which
# read no strategy return. Expected cost, not a tail (finding C-1): open = ceil(mean marketable buy at open+1 min versus
# the daily open, 20.4, + 0.15 fees); close = ceil(mean half-spread at close-1 min, 1.4, + 0.15); stop exits inside a
# session are NOT measured and keep the conservative 25; SPY sweeps keep 5. Uniform 25 bps stays the stress case.
COST_MODELS: dict[str, dict[str, float]] = {
    "cost-v3-2": {"open": 21.0, "close": 2.0, "stop": 25.0, "etf": 5.0, "stress_uniform": 25.0},
}


@dataclass(frozen=True)
class Event:
    symbol: str
    day: date  # the entry session (decision at the previous close)
    t: int  # protocol-axis index of the entry session
    source: str  # "random" | "S1"
    year: int
    tercile: int  # 1 = least liquid third of the eligible names at the decision close, 3 = most liquid
    gap_bps: float  # open versus the previous close (split-adjusted daily bars; ratios are adjustment-invariant)


def _terciles(dv: np.ndarray, el: np.ndarray) -> np.ndarray:
    """Per-session cross-sectional dollar-volume tercile (1..3) among eligible names; 0 where not eligible."""
    out = np.zeros(dv.shape, dtype=int)
    for t in range(dv.shape[1]):
        idx = np.flatnonzero(el[:, t] & np.isfinite(dv[:, t]))
        if len(idx) < 3:
            continue
        order = idx[np.argsort(dv[idx, t], kind="stable")]
        out[order, t] = 1 + (np.arange(len(order)) * 3) // len(order)
    return out


def draw_sample(pre: S.Prepared, b, q: dict, plan: CostStudyPlan) -> list[Event]:
    """Deterministic given the dataset and the plan: seeded draws without replacement inside each registered cell."""
    lo, hi = b.dev_range
    syms = list(pre.stocks)
    n = len(pre.sessions)
    el = np.vstack([pre.eligible[s] for s in syms])
    dv = np.vstack([pre.frames[s]["dollar_vol_med_20"].to_numpy(float) for s in syms])
    ter = _terciles(dv, el)
    opens = {s: pre.bars[s]["open"].reindex(pre.sessions).to_numpy(float) for s in syms}
    closes = {s: pre.bars[s]["close"].reindex(pre.sessions).to_numpy(float) for s in syms}
    years = pre.sessions.year.to_numpy()

    def event(i: int, e: int, source: str) -> Event:
        s = syms[i]
        gap = (opens[s][e] / closes[s][e - 1] - 1) * 1e4 if e > 0 else float("nan")
        return Event(s, pre.sessions[e].date(), int(e), source, int(years[e]), int(ter[i, e - 1]), round(float(gap), 2))

    rng = np.random.default_rng([plan.seed, 0])
    out: list[Event] = []
    ent = np.arange(lo + 1, min(hi + 1, n - 1) + 1)  # entry sessions; the decision (and the tercile) is at e - 1
    for y in plan.years:
        es = ent[years[ent] == y]
        for k in (1, 2, 3):
            cand = [(i, int(e)) for e in es for i in np.flatnonzero((ter[:, e - 1] == k) & el[:, e - 1])
                    if np.isfinite(opens[syms[i]][e]) and np.isfinite(closes[syms[i]][e - 1])]
            if not cand:
                continue
            pick = rng.choice(len(cand), size=min(plan.per_cell, len(cand)), replace=False)
            out += [event(*cand[j], "random") for j in sorted(pick)]
    if plan.s1_events:
        sigs = A.SELECTORS["s1_leader_pullback"](pre, b, {"seed_name": "S1"})
        rows = S.run_arm("S1", "E0X1", sigs, pre, b, q, controls=False).rows
        fills = rows[rows["filled"].astype(bool)] if len(rows) else rows
        cand = sorted({(syms.index(r.symbol), int(r.entry_index)) for r in fills.itertuples()
                       if lo < int(r.entry_index) < n and r.symbol in syms})
        rng1 = np.random.default_rng([plan.seed, 1])
        if cand:
            pick = rng1.choice(len(cand), size=min(plan.s1_events, len(cand)), replace=False)
            out += [event(*cand[j], "S1") for j in sorted(pick)]
    return out


def sample_sha256(events: list[Event]) -> str:
    rows = sorted((e.symbol, e.day.isoformat(), e.source) for e in events)
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


# ---- measurement (pure parts) -----------------------------------------------------------------------------------------
def instant_utc(cal, day: date, spec: str) -> datetime:
    s = cal.session(day)
    if spec.startswith("open+"):
        return s.open_utc + timedelta(seconds=int(spec[5:]))
    if spec.startswith("close-"):
        return s.close_utc - timedelta(seconds=int(spec[6:]))
    hh, mm = (int(x) for x in spec.split(":"))
    local = pd.Timestamp(year=day.year, month=day.month, day=day.day, hour=hh, minute=mm).tz_localize(NY)
    return local.tz_convert(UTC).to_pydatetime()


def valid_quote(qs: list[dict]) -> dict | None:
    """The first quote (callers request newest first) with positive sizes and a positive, uncrossed, unlocked spread."""
    for x in qs:
        bp, ap = float(x.get("bp") or 0), float(x.get("ap") or 0)
        if bp > 0 and ap > bp and (x.get("bs") or 0) > 0 and (x.get("as") or 0) > 0:
            return x
    return None


def event_metrics(daily_open: float, daily_close: float, quotes: dict[str, dict | None],
                  first_min: dict | None, last_min: dict | None) -> dict:
    """Derived numbers only (bps). Positive cost = worse than the simulator's price for that side."""
    def hs(x):
        return None if x is None else (x["ap"] - x["bp"]) / (x["ap"] + x["bp"]) * 1e4

    def bps(a, b_):
        return None if a is None or b_ is None or not (b_ > 0) else (a / b_ - 1) * 1e4

    m: dict = {f"half_spread_{k}": hs(v) for k, v in quotes.items()}
    o1, o5, c1 = quotes.get("open+60"), quotes.get("open+300"), quotes.get("close-60")
    m["buy_cost_open+60"] = bps(o1["ap"], daily_open) if o1 else None
    m["buy_cost_open+300"] = bps(o5["ap"], daily_open) if o5 else None
    m["sell_cost_close-60"] = -bps(c1["bp"], daily_close) if c1 else None
    m["mid_drift_open+60"] = bps((o1["ap"] + o1["bp"]) / 2, daily_open) if o1 else None
    m["first_minute_vwap_vs_open"] = bps(first_min.get("vwap"), daily_open) if first_min else None
    m["minute_open_vs_daily_open"] = bps(first_min.get("open"), daily_open) if first_min else None
    m["last_minute_vwap_vs_close"] = -bps(last_min.get("vwap"), daily_close) if last_min else None
    b, s = m["buy_cost_open+60"], m["sell_cost_close-60"]
    m["per_side_marketable"] = None if b is None or s is None else (b + s) / 2
    h0, h1 = m.get("half_spread_open+60"), m.get("half_spread_close-60")
    m["per_side_half_spread"] = None if h0 is None or h1 is None else (h0 + h1) / 2
    return {k: (None if v is None or not math.isfinite(v) else round(float(v), 3)) for k, v in m.items()}


async def measure_event(adapter, cal, ev: Event, plan: CostStudyPlan) -> dict:
    """Six-ish provider requests; raw records stay local to this function."""
    rec = {"source": ev.source, "year": ev.year, "tercile": ev.tercile, "gap_bps": ev.gap_bps}
    daily = await adapter.get_daily_bars([ev.symbol], ev.day, ev.day, adjustment="raw", feed="sip")
    d = daily.get(ev.symbol)
    if d is None or not len(d):
        return rec | {"missing": "NO_RAW_DAILY_BAR"}
    o, c = float(d["open"].iloc[0]), float(d["close"].iloc[0])
    sess = cal.session(ev.day)
    mins = (await adapter.get_intraday_bars([ev.symbol], sess.open_utc, sess.close_utc, timeframe="1Min",
                                            feed="sip")).get(ev.symbol)
    first = last = None
    if mins is not None and len(mins):
        ts = mins.index
        if ts[0] == pd.Timestamp(sess.open_utc):
            first = mins.iloc[0].to_dict()
        lm = pd.Timestamp(sess.close_utc - timedelta(minutes=1))
        if lm in ts:
            last = mins.loc[lm].to_dict()
    quotes: dict[str, dict | None] = {}
    for spec in plan.instants:
        at = instant_utc(cal, ev.day, spec)
        got = None
        for lb in plan.lookback_seconds:
            got = valid_quote(await adapter.get_quotes(ev.symbol, at - timedelta(seconds=lb), at))
            if got:
                break
        quotes[spec] = None if got is None else {"ap": float(got["ap"]), "bp": float(got["bp"])}
    return rec | event_metrics(o, c, quotes, first, last)


# ---- aggregation --------------------------------------------------------------------------------------------------------
METRICS = ("half_spread_open+60", "half_spread_open+300", "half_spread_12:00", "half_spread_close-60",
           "buy_cost_open+60", "buy_cost_open+300", "sell_cost_close-60", "mid_drift_open+60",
           "first_minute_vwap_vs_open", "minute_open_vs_daily_open", "last_minute_vwap_vs_close",
           "per_side_marketable", "per_side_half_spread")


def _stats(x: np.ndarray, min_cell: int) -> dict:
    x = x[np.isfinite(x)]
    if len(x) < min_cell:
        return {"n": len(x), "suppressed": True}
    return {"n": len(x), "mean": round(float(x.mean()), 3), "p50": round(float(np.percentile(x, 50)), 3),
            "p75": round(float(np.percentile(x, 75)), 3), "p90": round(float(np.percentile(x, 90)), 3)}


def _boot_ci(x: np.ndarray, seed: int, reps: int = 2000) -> list[float] | None:
    x = x[np.isfinite(x)]
    if len(x) < 5:
        return None
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(reps, len(x)), replace=True).mean(axis=1)
    return [round(float(np.percentile(means, 2.5)), 3), round(float(np.percentile(means, 97.5)), 3)]


def _gap_bucket(g: float) -> str:
    a = abs(g) if math.isfinite(g) else float("nan")
    return "unknown" if not math.isfinite(a) else "<1%" if a < 100 else "1-3%" if a < 300 else ">=3%"


def aggregate(rows: list[dict], plan: CostStudyPlan) -> dict:
    ok = [r for r in rows if "missing" not in r]
    df = pd.DataFrame(ok) if ok else pd.DataFrame(columns=["source", "year", "tercile", "gap_bps", *METRICS])
    for k in METRICS:
        if k not in df:
            df[k] = np.nan
    df["gap_bucket"] = [_gap_bucket(g) for g in df["gap_bps"]] if len(df) else []
    groups: dict[str, pd.DataFrame] = {"all": df, "source=random": df[df["source"] == "random"],
                                       "source=S1": df[df["source"] == "S1"]}
    rnd = groups["source=random"]
    for k in (1, 2, 3):
        groups[f"tercile={k}"] = rnd[rnd["tercile"] == k]
    for y in plan.years:
        groups[f"year={y}"] = rnd[rnd["year"] == y]
    for gb in ("<1%", "1-3%", ">=3%"):
        groups[f"gap={gb}"] = df[df["gap_bucket"] == gb]
    out = {g: {m: _stats(d[m].to_numpy(float), plan.min_cell) for m in METRICS} for g, d in groups.items()}
    fee_side = plan.sell_fee_bps / 2
    allx = df["per_side_marketable"].to_numpy(float)
    model = {"cost_model_id": "cost-v3-1 (PROPOSED; not applied to any published result)",
             "rule": "ceil(p90(per-side marketable cost) + sell fee / 2) per liquidity tercile and overall, in bps",
             "per_tercile_bps": {}, "overall_bps": None}
    for k in (1, 2, 3):
        x = groups[f"tercile={k}"]["per_side_marketable"].to_numpy(float)
        x = x[np.isfinite(x)]
        model["per_tercile_bps"][str(k)] = (math.ceil(float(np.percentile(x, 90)) + fee_side)
                                            if len(x) >= plan.min_cell else None)
    fin = allx[np.isfinite(allx)]
    if len(fin) >= plan.min_cell:
        model["overall_bps"] = math.ceil(float(np.percentile(fin, 90)) + fee_side)
    assumption = {"assumed_bps_per_side": 25.0,
                  "share_of_events_above_25bps": round(float((fin > 25).mean()), 4) if len(fin) else None,
                  "mean_per_side_marketable_bps": round(float(fin.mean()) + fee_side, 3) if len(fin) else None,
                  "mean_ci95_bootstrap": _boot_ci(fin + fee_side, plan.seed)}
    return {"groups": out, "cost_model_proposal": model, "assumption_check": assumption}


# ---- the study ----------------------------------------------------------------------------------------------------------
@dataclass
class StudyOutcome:
    status: str  # COMPLETED | SOURCE_BLOCKED | BUDGET_EXHAUSTED
    rows: list[dict] = field(default_factory=list)
    reason: str | None = None
    requests: int = 0


async def measure_all(adapter, cal, events: list[Event], plan: CostStudyPlan,
                      progress: Callable[[str], None] = print) -> StudyOutcome:
    from apex_worker.market.adapters.alpaca import AlpacaBudgetExhausted, AlpacaError

    adapter.max_requests = plan.max_requests
    sem = asyncio.Semaphore(plan.concurrency)
    rows: list[dict | None] = [None] * len(events)
    stop: list[StudyOutcome] = []

    async def one(i: int, ev: Event) -> None:
        if stop:
            return
        async with sem:
            if stop:
                return
            try:
                rows[i] = await measure_event(adapter, cal, ev, plan)
            except AlpacaBudgetExhausted:
                stop.append(StudyOutcome("BUDGET_EXHAUSTED", reason=f"request cap {plan.max_requests} reached"))
            except AlpacaError as exc:
                if exc.status in (401, 403):
                    stop.append(StudyOutcome("SOURCE_BLOCKED", reason=f"provider refused ({exc.status}): the account "
                                             "is not entitled to historical SIP quotes or intraday bars"))
                else:
                    rows[i] = {"source": ev.source, "year": ev.year, "tercile": ev.tercile, "gap_bps": ev.gap_bps,
                               "missing": f"PROVIDER_ERROR_{exc.status}"}
            if (i + 1) % 25 == 0:
                progress(f"measured {i + 1}/{len(events)} events ({adapter.requests} requests)")

    await asyncio.gather(*(one(i, ev) for i, ev in enumerate(events)))
    if stop:
        return StudyOutcome(stop[0].status, [r for r in rows if r], stop[0].reason, adapter.requests)
    return StudyOutcome("COMPLETED", [r for r in rows if r], None, adapter.requests)


def run_cost_study(plan: CostStudyPlan, data, adapter_factory, out_dir: Path, *, experiment_id: str, cal=None,
                   progress: Callable[[str], None] = print, code_sha: str | None = None) -> dict:
    import os

    from apex_worker.market.calendar import default_calendar

    cal = cal or default_calendar()
    out_dir.mkdir(parents=True, exist_ok=True)
    S.OPEN = data.pre.open_sessions  # the S1 stratum runs the protocol's arm, whose test axis is the gate-open mask
    events = draw_sample(data.pre, data.b, data.quarantine, plan)
    design = {"strata": {f"{e.source}:{e.year}:{e.tercile}": 0 for e in events}}
    for e in events:
        design["strata"][f"{e.source}:{e.year}:{e.tercile}"] += 1
    head = {"schema": "apex-discovery-v3-cost-study-1", "experiment_id": experiment_id, "plan": plan.to_dict(),
            "plan_sha256": plan.sha256(), "code_sha": code_sha or os.environ.get("GITHUB_SHA"),
            "provenance": data.provenance.__dict__, "sample": {"events": len(events), "sample_sha256":
                                                               sample_sha256(events), **design},
            "research_only": True, "frozen_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    progress(f"sample frozen: {len(events)} events, sha256 {head['sample']['sample_sha256'][:16]}")

    async def go() -> StudyOutcome:
        adapter = adapter_factory()
        try:
            return await measure_all(adapter, cal, events, plan, progress)
        finally:
            await adapter.aclose()

    res = asyncio.run(go())
    missing: dict[str, int] = {}
    for r in res.rows:
        if "missing" in r:
            missing[r["missing"]] = missing.get(r["missing"], 0) + 1
    body = head | {"status": res.status, "reason": res.reason, "provider_requests": res.requests,
                   "coverage": {"attempted": len(events), "returned": len(res.rows),
                                "measured": sum("missing" not in r for r in res.rows), "missing": missing}}
    if res.status == "COMPLETED":
        body |= aggregate(res.rows, plan)
    body["publication"] = ("aggregates over >= min_cell events only; no quote, price or per-event row is stored or "
                           "published (provider terms)")
    (out_dir / "cost_study.json").write_text(json.dumps(body, indent=1, sort_keys=True, default=str))
    (out_dir / "COST_STUDY.md").write_text(summary_md(body))
    return {"status": res.status, "events": len(events), "measured": body["coverage"]["measured"],
            "requests": res.requests, "cost_model_proposal": body.get("cost_model_proposal")}


def summary_md(body: dict) -> str:
    lines = [f"# Execution cost study `{body['plan']['plan_id']}` ({body['experiment_id']})", "",
             f"Status: **{body['status']}**" + (f" ({body['reason']})" if body.get("reason") else ""),
             f"Events: {body['coverage']['measured']} measured of {body['coverage']['attempted']}; provider requests "
             f"{body['provider_requests']}. All values in bps; positive cost = worse than the simulator's price.", ""]
    if body["status"] != "COMPLETED":
        return "\n".join(lines) + "\n"
    cols = ("half_spread_open+60", "half_spread_close-60", "buy_cost_open+60", "sell_cost_close-60",
            "per_side_marketable", "first_minute_vwap_vs_open")
    lines += ["| Group | n | " + " | ".join(f"{c} p50 / p90" for c in cols) + " |",
              "|---|---|" + "---|" * len(cols)]
    for g, ms in body["groups"].items():
        n = ms["per_side_marketable"]["n"]
        cells = []
        for c in cols:
            s = ms[c]
            cells.append("suppressed" if s.get("suppressed") else f"{s['p50']:+.1f} / {s['p90']:+.1f}")
        lines.append(f"| {g} | {n} | " + " | ".join(cells) + " |")
    a, m = body["assumption_check"], body["cost_model_proposal"]
    lines += ["", f"Mean per-side marketable cost incl. fees: {a['mean_per_side_marketable_bps']} bps "
              f"(95% bootstrap CI {a['mean_ci95_bootstrap']}); share of events above the assumed 25 bps: "
              f"{a['share_of_events_above_25bps']}.",
              f"Proposed `cost-v3-1` (p90 + fees, ceil): overall {m['overall_bps']} bps; by tercile "
              f"{m['per_tercile_bps']} (1 = least liquid). A proposal for later registered plans only."]
    return "\n".join(lines) + "\n"
