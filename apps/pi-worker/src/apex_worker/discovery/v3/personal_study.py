"""Discovery V3 studies `personal-study-1` and `personal-study-2` (docs/discovery-v3/PERSONAL_STUDY_1.md, _2.md): which
whole-share method should a $1000 account follow, and did it beat SPY on the DEV window?

personal-study-2 (EtfStudyPlan, below) tests rules published before the DEV window, with their published parameters, on
split- and dividend-adjusted bars of the few ETFs they trade (total returns, SPY's too). Its rule and its window are
personal-study-1's; the rest of this docstring describes personal-study-1.

The owner trades by hand at Ally Invest, which has no fractional shares, starting with $1000. Two methods that a small
whole-share account can follow were registered before this run (personal/method.py):
* `sector-mom-3`: the top 3 Select Sector SPDR ETFs by 6-month return, rebalanced monthly, kept while in the top 5;
* `sector-mom-3-abs`: the same, with an absolute-momentum filter (only positive 6-month returns; the rest in cash).
Sector ETFs existed throughout the window, so unlike the U1 stock studies there is no survivorship bias in the universe.

Each method is simulated with whole shares from $1000 (personal/backtest.py): decisions at the close, fills at the next
open, 5 bps a side (0 and 20 in sensitivity), cash at 0%. It is compared with SPY bought with the same $1000 at the same
open, fractional and frictionless. Price returns for both.

The registered rule: a method BEATS_SPY_ON_DEV when its log growth exceeds SPY's and its maximum drawdown is at most 10
points worse than SPY's. The personal method is the one that beats SPY with the larger excess (a tie: the simpler,
`sector-mom-3`). If neither does, the report says so and the better one is run as an EXPERIMENT, labelled as such.
DEV_CONTAMINATED: a diagnostic, never validation; the forward record of the alerts is the test. Holm runs over the two.
Only aggregates and the ETFs' own names leave the runner: no price is written.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from apex_worker.market.calendar import default_calendar
from apex_worker.personal import backtest as B
from apex_worker.personal import method as M
from apex_worker.research.training_data import BENCHMARK


@dataclass(frozen=True)
class PersonalStudyPlan:
    plan_id: str
    title: str
    candidates: tuple[str, ...]  # personal.method METHODS keys, the simpler first (it wins ties)
    initial_cash: float = 1000.0
    cost_bps: float = 5.0
    cost_sensitivity_bps: tuple[float, ...] = (0.0, 20.0)
    max_extra_drawdown: float = 0.10
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self) | {"methods": {k: M.METHODS[k].to_dict() for k in self.candidates}}

    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


PERSONAL_STUDY_1 = PersonalStudyPlan(
    plan_id="personal-study-1",
    title="Whole-share methods for a $1000 account: monthly sector-ETF momentum vs SPY (DEV dates)",
    candidates=("sector-mom-3", "sector-mom-3-abs"),
    notes="Registered before the run. Rule: BEATS_SPY_ON_DEV = log growth above SPY's and maximum drawdown at most 10 "
    "points worse than SPY's, at 5 bps a side. The personal method is the beating candidate with the larger excess "
    "(tie: sector-mom-3); if none beats SPY, the better candidate runs as an EXPERIMENT and the report says that no "
    "method beat the index. DEV_CONTAMINATED diagnostics: the forward record of the alerts is the test.")



@dataclass(frozen=True)
class EtfStudyPlan:
    plan_id: str
    title: str
    candidates: tuple[str, ...]  # personal.method METHODS keys, the simpler first (it wins ties)
    symbols: tuple[str, ...]  # the daily bars read, the benchmark first
    adjustment: str = "all"  # splits and dividends: total returns for every asset and for SPY
    history_start: str = "2016-01-04"  # protocol-2's first session: a year of history before the first decision
    initial_cash: float = 1000.0
    cost_bps: float = 5.0
    cost_sensitivity_bps: tuple[float, ...] = (0.0, 20.0)
    max_extra_drawdown: float = 0.10
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self) | {"methods": {k: M.METHODS[k].to_dict() for k in self.candidates}}

    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


PERSONAL_STUDY_2 = EtfStudyPlan(
    plan_id="personal-study-2",
    title="Published rules for a $1000 account: the 200-day trend filter on 1x/2x/3x S&P 500 funds and Dual Momentum "
          "vs SPY (DEV dates, total returns)",
    candidates=("spy-trend200", "sso-trend200", "upro-trend200", "gem"),
    symbols=(BENCHMARK, "SSO", "UPRO", "EFA", "AGG", "BIL"),
    notes="Registered before the run, after personal-study-1 found that no sector rotation beat SPY. Each rule was "
    "published before the DEV window with the parameters used here (Gayed & Bilello 2016; Antonacci 2014); nothing is "
    "fitted. Rule as personal-study-1: BEATS_SPY_ON_DEV = log growth above SPY's and maximum drawdown at most 10 points "
    "worse than SPY's, at 5 bps a side, on total returns for both. The personal method is the beating candidate with "
    "the larger excess (tie: the earlier listed); if none beats SPY, the better candidate runs as an EXPERIMENT and the "
    "report says that no method beat the index. DEV_CONTAMINATED: the author knew how leveraged equity exposure fared "
    "in 2019-2026, so a pass is weak evidence; the forward record of the alerts is the test.")

PERSONAL_PLANS: dict[str, PersonalStudyPlan | EtfStudyPlan] = {p.plan_id: p for p in (PERSONAL_STUDY_1,
                                                                                       PERSONAL_STUDY_2)}


def _holm(p: dict[str, float | None]) -> dict[str, float | None]:
    items = sorted((v, k) for k, v in p.items() if v is not None)
    out: dict[str, float | None] = dict.fromkeys(p)
    running, m = 0.0, len(items)
    for i, (v, k) in enumerate(items):
        running = max(running, min(1.0, (m - i) * v))
        out[k] = running
    return out


def run_method(spec: M.MethodSpec, bars: dict[str, pd.DataFrame], first: object, last: object, cost_bps: float,
               initial_cash: float) -> dict:
    cal = default_calendar()
    sim = B.simulate(spec, bars, first, last, cal, initial_cash=initial_cash, cost_bps=cost_bps)
    bench = B.spy_benchmark(bars, sim.equity.index, initial_cash)
    m = B.metrics(sim.equity, bench)
    held = pd.Series([t["symbol"] for t in sim.trades if t["action"] in M.BUYS]).value_counts()
    months = max(1, len(sim.decisions))
    buys = sum(1 for t in sim.trades if t["action"] in M.BUYS)
    sells = sum(1 for t in sim.trades if t["action"] in M.SELLS)
    return m | {"trades": {"buys": buys, "sells": sells, "per_year": (buys + sells) / m["years"],
                           "cost_paid": sim.costs_paid, "cost_paid_per_year": sim.costs_paid / m["years"]},
                "decisions": len(sim.decisions), "mean_cash_share": float((sim.cash / sim.equity).mean()),
                "mean_positions": float(sim.positions.mean()),
                "buys_by_etf": {s: int(n) for s, n in held.items()}, "months_decided": months,
                "monthly_values": {"method": {d.strftime("%Y-%m"): round(float(v), 2) for d, v in
                                              sim.equity.resample("ME").last().items()},
                                   "spy": {d.strftime("%Y-%m"): round(float(v), 2) for d, v in
                                           bench.resample("ME").last().items()}}}


def run_personal_study(plan: PersonalStudyPlan, data, out_dir: Path, *, experiment_id: str,
                       progress: Callable[[str], None] = print, code_sha: str | None = None) -> dict:
    """`data`: pipeline.Loaded. Writes personal_study.json and PERSONAL_STUDY.md (aggregates only)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pre = data.pre
    d0, d1 = data.b.dev_range
    first, last = pre.sessions[d0 - 1].date(), pre.sessions[d1].date()  # the agents' convention: decide the close before
    bars = {s: pre.bars[s] for s in (BENCHMARK, *M.SECTOR_MOM_3.universe) if s in pre.bars}
    rows = {}
    for k in plan.candidates:
        spec = M.METHODS[k]
        progress(f"simulating {k}")
        main = run_method(spec, bars, first, last, plan.cost_bps, plan.initial_cash)
        sens = {f"{c:g}bps": {f: run_method(spec, bars, first, last, c, plan.initial_cash)[f]
                              for f in ("log_growth_per_year", "excess_log_growth_per_year", "end_value")}
                for c in plan.cost_sensitivity_bps}
        beats = (main["excess_log_growth_per_year"] > 0
                 and main["max_drawdown"] >= main["spy_max_drawdown"] - plan.max_extra_drawdown)
        rows[k] = main | {"cost_sensitivity": sens, "beats_spy_on_dev": beats,
                          "disposition": "BEATS_SPY_ON_DEV" if beats else "DOES_NOT_BEAT_SPY_ON_DEV"}
    holm = _holm({k: r["monthly_active"]["one_sided_p"] for k, r in rows.items()})
    for k in rows:
        rows[k]["monthly_active"]["holm_p"] = holm[k]
    winners = [k for k in plan.candidates if rows[k]["beats_spy_on_dev"]]
    if winners:
        chosen = max(winners, key=lambda k: (rows[k]["excess_log_growth_per_year"], -plan.candidates.index(k)))
        status = "ADOPTED"
    else:
        chosen = max(plan.candidates, key=lambda k: (rows[k]["excess_log_growth_per_year"],
                                                      -plan.candidates.index(k)))
        status = "EXPERIMENT"
    body = {"schema": "apex-discovery-v3-personal-study-1", "experiment_id": experiment_id, "plan": plan.to_dict(),
            "plan_sha256": plan.sha256(), "code_sha": code_sha or os.environ.get("GITHUB_SHA"),
            "provenance": data.provenance.__dict__, "research_only": True, "status": "COMPLETED",
            "window": {"first_decision": first.isoformat(), "last_session": last.isoformat(),
                       "label": "DEV_CONTAMINATED", "tier": "DEV_DIAGNOSTIC"},
            "benchmark": "SPY bought with the same $1000 at the first fill open, fractional, frictionless, price return",
            "methods": rows, "decision": {"personal_method": chosen, "status": status,
                                          "rule": plan.notes},
            "publication": "aggregates and the ETFs' names only: no price is written",
            "finished_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (out_dir / "personal_study.json").write_text(json.dumps(body, indent=1, sort_keys=True, default=str))
    (out_dir / "PERSONAL_STUDY.md").write_text(summary_md(body))
    return {"status": body["status"], "decision": body["decision"],
            "excess": {k: r["excess_log_growth_per_year"] for k, r in rows.items()}}


@dataclass
class EtfData:
    bars: dict[str, pd.DataFrame]
    first: object  # date: the first decision (the close before the first DEV session)
    last: object  # date: the last DEV session
    provenance: dict
    data_quality: dict


def synthetic_etf_bars(symbols: tuple[str, ...], start, end, seed: int = 11) -> dict[str, pd.DataFrame]:
    """SYNTHETIC bars for DEMO runs and tests (never market data): leveraged funds compound 2x/3x the index's daily
    return, T-bills drift, bonds wander."""
    import numpy as np

    cal = default_calendar()
    days = cal.sessions_between(start, end)
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    rng = np.random.default_rng(seed)
    base = 0.0004 + 0.011 * rng.standard_normal(len(days))
    rets = {BENCHMARK: base, "SSO": 2 * base - 0.00004, "UPRO": 3 * base - 0.00006,
            "EFA": 0.8 * base + 0.006 * rng.standard_normal(len(days)) - 0.0001,
            "AGG": 0.00008 + 0.003 * rng.standard_normal(len(days)), "BIL": np.full(len(days), 0.00012)}
    out = {}
    for i, sym in enumerate(symbols):
        r = rets.get(sym, 0.0003 + 0.01 * rng.standard_normal(len(days)))
        close = (50.0 + 10 * i) * np.exp(np.cumsum(r))
        opn = np.concatenate([[close[0]], close[:-1]]) * (1 + 0.001 * rng.standard_normal(len(days)))
        out[sym] = pd.DataFrame({"open": opn, "high": np.maximum(opn, close) * 1.002,
                                 "low": np.minimum(opn, close) * 0.998, "close": close,
                                 "volume": np.full(len(days), 1e6)}, index=idx)
    return out


def load_etf_data(plan: EtfStudyPlan, source: str, *, now: datetime | None = None, env: dict | None = None,
                  max_requests: int | None = 40) -> EtfData:
    """The plan's daily bars: Alpaca historical SIP (bounded requests, protocol-2's finality lag), or the synthetic
    DEMO fixture. The window is protocol-2's DEV range on SPY's sessions, as in personal-study-1."""
    from datetime import date as date_cls

    from apex_worker.research.dataset import data_hash
    from apex_worker.research.realeq.data import STATUS_SOURCE_INVALID, RunStop, credentials_from_env
    from apex_worker.research.realeq.data_v2 import data_end
    from apex_worker.research.realeq.protocol_v2 import compute_boundaries_v2

    cal = default_calendar()
    start = date_cls.fromisoformat(plan.history_start)
    requests = 0
    if source == "fixture":
        from apex_worker.research.realeq.fixture import FIXTURE_NOW

        now = FIXTURE_NOW
        end = data_end(cal, now)
        bars = synthetic_etf_bars(plan.symbols, start, end)
        data_class, provider = "SYNTHETIC_FIXTURE_DEMO", "fixture-emulation"
    elif source == "alpaca":
        import asyncio

        from apex_worker.market.adapters.alpaca import AlpacaAdapter

        key, secret = credentials_from_env(os.environ if env is None else env)
        now = now or datetime.now(UTC)
        end = data_end(cal, now)

        async def fetch():
            ad = AlpacaAdapter(key, secret)
            ad.max_requests = max_requests
            try:
                got = await ad.get_daily_bars(list(plan.symbols), start, end, adjustment=plan.adjustment, feed="sip")
            finally:
                await ad.aclose()
            return got, ad.requests

        bars, requests = asyncio.run(fetch())
        data_class, provider = "REAL_MARKET_DATA", "alpaca"
    else:
        raise ValueError("source must be alpaca or fixture")
    sessions = [pd.Timestamp(d) for d in cal.sessions_between(start, end)]
    quality = {}
    for sym in plan.symbols:
        df = bars.get(sym)
        df = df[(df.index >= sessions[0]) & (df.index <= sessions[-1])] if df is not None else None
        n = 0 if df is None else int(df.index.isin(sessions).sum())
        bad = 0 if df is None else int((df[["open", "close"]].isna() | (df[["open", "close"]] <= 0)).any(axis=1).sum())
        quality[sym] = {"sessions": n, "expected": len(sessions), "invalid_rows": bad}
        if df is not None:
            bars[sym] = df
    short = [s for s, q in quality.items() if q["sessions"] < 0.99 * q["expected"] or q["invalid_rows"]]
    if short:
        raise RunStop(STATUS_SOURCE_INVALID, f"incomplete or invalid daily bars for {', '.join(short)}")
    b = compute_boundaries_v2(bars[BENCHMARK].index)
    spy_days = bars[BENCHMARK].index
    d0, d1 = b.dev_range
    prov = {"data_class": data_class, "provider": provider, "feed": "sip", "adjustment": plan.adjustment,
            "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "window_start": str(start),
            "window_end": str(end), "dataset_hash": data_hash(bars),
            "rights_note": "derived aggregates only; no vendor OHLCV is stored or published"}
    dq = {"symbols": quality, "data_end_after_finality_lag": str(end), "provider_requests_total": requests}
    return EtfData(bars, spy_days[d0 - 1].date(), spy_days[d1].date(), prov, dq)


def run_etf_study(plan: EtfStudyPlan, data: EtfData, out_dir: Path, *, experiment_id: str,
                  progress: Callable[[str], None] = print, code_sha: str | None = None) -> dict:
    """personal-study-2: the registered rule on the plan's bars. Writes personal_study.json and PERSONAL_STUDY.md."""
    out_dir.mkdir(parents=True, exist_ok=True)
    first, last = data.first, data.last
    rows = {}
    for k in plan.candidates:
        spec = M.METHODS[k]
        progress(f"simulating {k}")
        main = run_method(spec, data.bars, first, last, plan.cost_bps, plan.initial_cash)
        sens = {f"{c:g}bps": {f: run_method(spec, data.bars, first, last, c, plan.initial_cash)[f]
                              for f in ("log_growth_per_year", "excess_log_growth_per_year", "end_value")}
                for c in plan.cost_sensitivity_bps}
        beats = (main["excess_log_growth_per_year"] > 0
                 and main["max_drawdown"] >= main["spy_max_drawdown"] - plan.max_extra_drawdown)
        rows[k] = main | {"cost_sensitivity": sens, "beats_spy_on_dev": beats,
                          "disposition": "BEATS_SPY_ON_DEV" if beats else "DOES_NOT_BEAT_SPY_ON_DEV"}
    holm = _holm({k: r["monthly_active"]["one_sided_p"] for k, r in rows.items()})
    for k in rows:
        rows[k]["monthly_active"]["holm_p"] = holm[k]
    winners = [k for k in plan.candidates if rows[k]["beats_spy_on_dev"]]
    pool = winners or list(plan.candidates)
    chosen = max(pool, key=lambda k: (rows[k]["excess_log_growth_per_year"], -plan.candidates.index(k)))
    body = {"schema": "apex-discovery-v3-personal-study-2", "experiment_id": experiment_id, "plan": plan.to_dict(),
            "plan_sha256": plan.sha256(), "code_sha": code_sha or os.environ.get("GITHUB_SHA"),
            "provenance": data.provenance, "research_only": True, "status": "COMPLETED",
            "returns": "total", "returns_label": "Total returns (splits and dividends), SPY's too.",
            "window": {"first_decision": first.isoformat(), "last_session": last.isoformat(),
                       "label": "DEV_CONTAMINATED", "tier": "DEV_DIAGNOSTIC"},
            "benchmark": "SPY bought with the same $1000 at the first fill open, fractional, frictionless, total return",
            "methods": rows, "decision": {"personal_method": chosen, "status": "ADOPTED" if winners else "EXPERIMENT",
                                          "rule": plan.notes},
            "publication": "aggregates and the ETFs' names only: no price is written",
            "finished_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (out_dir / "personal_study.json").write_text(json.dumps(body, indent=1, sort_keys=True, default=str))
    (out_dir / "PERSONAL_STUDY.md").write_text(summary_md(body))
    return {"status": body["status"], "decision": body["decision"],
            "excess": {k: r["excess_log_growth_per_year"] for k, r in rows.items()}}


def _pct(x: float | None, d: int = 1) -> str:
    return "—" if x is None else f"{100 * x:+.{d}f}%"


def summary_md(body: dict) -> str:
    w, dec = body["window"], body["decision"]
    lines = [f"# Personal method study `{body['plan']['plan_id']}` ({body['experiment_id']})", "",
             f"Status **{body['status']}**: decisions {w['first_decision']} → {w['last_session']} ({w['label']}, "
             f"{w['tier']}). $1000, whole shares, fills at the next open, {body['plan']['cost_bps']:g} bps a side; "
             f"SPY: the same $1000, fractional and frictionless. {body.get('returns_label', 'Price returns.')}", "",
             f"**Personal method: `{dec['personal_method']}` ({dec['status']}).**", "",
             "| Method | End value | SPY end value | Growth / yr | SPY / yr | Excess / yr | Max drawdown | SPY drawdown | "
             "Trades / yr | Cash | Holm p | Disposition |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, r in body["methods"].items():
        holm = r["monthly_active"]["holm_p"]
        lines.append(f"| {k} | ${r['end_value']:,.0f} | ${r['end_value_spy']:,.0f} | {_pct(r['cagr'])} | "
                     f"{_pct(r['spy_cagr'])} | {_pct(r['excess_log_growth_per_year'])} | {_pct(r['max_drawdown'])} | "
                     f"{_pct(r['spy_max_drawdown'])} | {r['trades']['per_year']:.1f} | {100 * r['mean_cash_share']:.0f}% | "
                     f"{'—' if holm is None else f'{holm:.3f}'} | {r['disposition']} |")
    lines += ["", "Calendar years (method vs SPY):", ""]
    for k, r in body["methods"].items():
        yrs = " · ".join(f"{y} {_pct(v['method'], 0)} vs {_pct(v['spy'], 0)}" for y, v in r["calendar_years"].items())
        lines.append(f"- `{k}`: {yrs}")
    lines += ["", "Cost sensitivity (excess growth per year vs SPY):", ""]
    for k, r in body["methods"].items():
        lines.append(f"- `{k}`: " + " · ".join(f"{c} {_pct(v['excess_log_growth_per_year'])}"
                                                 for c, v in r["cost_sensitivity"].items()))
    lines += ["", "DEV_CONTAMINATED diagnostics, never validation: the forward record of the alerts is the test."]
    return "\n".join(lines) + "\n"


__all__ = ["PERSONAL_PLANS", "PERSONAL_STUDY_1", "PERSONAL_STUDY_2", "EtfData", "EtfStudyPlan", "PersonalStudyPlan",
           "load_etf_data", "run_etf_study", "run_method", "run_personal_study", "summary_md", "synthetic_etf_bars"]
