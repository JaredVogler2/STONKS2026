"""Discovery V3 studies `personal-study-1` to `personal-study-3` (docs/discovery-v3/PERSONAL_STUDY_1.md, _2.md, _3.md):
which whole-share method should a $1000 account follow, and does it beat SPY? (`personal-study-4`, the century
replication, lives in long_history.py and is registered here with the others.)

personal-study-2 (EtfStudyPlan, below) tests rules published before the DEV window, with their published parameters, on
split- and dividend-adjusted bars of the few ETFs they trade (total returns, SPY's too). Its rule and its window are
personal-study-1's. personal-study-3 (BatteryPlan, `run_battery`) puts ten more published rules and the adopted one
through a battery: a FULL window from 2017 and its two halves, rolling 1- and 3-year windows, a block-bootstrap
interval and cost stress. The rest of this docstring describes personal-study-1.

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
import itertools
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from apex_worker.discovery.v3.long_history import PERSONAL_STUDY_4, LongHistoryPlan
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



@dataclass(frozen=True)
class BatteryPlan:
    """personal-study-3: more published rules, each through the same battery of tests on two windows."""

    plan_id: str
    title: str
    candidates: tuple[str, ...]  # the new trials (personal.method METHODS keys)
    references: tuple[str, ...]  # evaluated alongside, not new trials (the method already adopted)
    symbols: tuple[str, ...]  # the daily bars read, the benchmark first
    adjustment: str = "all"
    history_start: str = "2016-01-04"
    full_first: str = "2017-01-31"  # the first decision of the FULL window: a year of history for every rule
    initial_cash: float = 1000.0
    cost_bps: float = 5.0
    cost_stress_bps: tuple[float, ...] = (0.0, 20.0)
    max_extra_drawdown: float = 0.10
    rolling_months: tuple[int, ...] = (12, 36)
    min_rolling36_share: float = 0.60
    bootstrap_resamples: int = 5000
    bootstrap_block_months: int = 6
    bootstrap_seed: int = 20261028
    notes: str = ""

    def to_dict(self) -> dict:
        names = (*self.references, *self.candidates)
        return asdict(self) | {"methods": {k: M.METHODS[k].to_dict() for k in names}}

    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# personal-study-3 (sha 4b67f1db...cac0, commit dd343be) stopped SOURCE_INVALID before evaluating anything: SPLG's bars
# end on 2025-10-30 because the fund's ticker became SPYM on 2025-10-31. personal-study-3b is the same study with SPYM.
PERSONAL_STUDY_3B = BatteryPlan(
    plan_id="personal-study-3b",
    title="Ten more published methods for a $1000 whole-share account vs SPY: full 2017-2026 and DEV windows, rolling "
          "1- and 3-year windows, block-bootstrap intervals, cost stress (total returns)",
    candidates=("sso-hold", "voltarget-sso", "gtaa5", "vaa-g4", "adm", "hfea", "halloween-sso", "tom-sso", "rsi2-sso",
                "mtum-hold"),
    references=("sso-trend200",),
    symbols=(BENCHMARK, "SPYM", "SSO", "UPRO", "TMF", "BIL", "EFA", "EEM", "AGG", "LQD", "IEF", "SHY", "TLT", "VNQ",
             "DBC", "SCZ", "MTUM"),
    notes="Amended before any evaluation: personal-study-3 stopped SOURCE_INVALID because SPLG's bars end on 2025-10-30 "
    "(the fund's ticker became SPYM on 2025-10-31); this plan reads SPYM instead and is otherwise unchanged. "
    "Registered before the run. Each rule is a published one with its published parameters (sources in "
    "personal/method.py RULES); nothing is fitted. The FULL window runs from the first decision 2017-01-31 to the last "
    "DEV session; its halves split at its middle session, each half a fresh $1000. BEATS_SPY_REPEATEDLY needs all of: "
    "excess log growth over SPY above 0 on the FULL window and on each half; FULL maximum drawdown at most 10 points "
    "worse than SPY's; ahead of SPY in at least 60% of rolling 36-month windows; FULL excess still above 0 at 20 bps a "
    "side. BEATS_SPY_PARTLY: FULL excess above 0 but another test failed. Otherwise DOES_NOT_BEAT_SPY. The DEV window "
    "(first decision 2019-10-22, personal-studies 1-2) is reported, not tested. The recommended direction is the "
    "BEATS_SPY_REPEATEDLY method with the largest FULL excess, the adopted sso-trend200 listed first so it wins ties; "
    "if none qualifies the report says so. The 90% stationary-bootstrap interval of the FULL annual excess and Holm "
    "over the ten new trials (FULL monthly active returns) are reported, not tested. DEV_CONTAMINATED: the forward "
    "record is the test.")

PERSONAL_PLANS: dict[str, PersonalStudyPlan | EtfStudyPlan | BatteryPlan | LongHistoryPlan] = {
    p.plan_id: p for p in (PERSONAL_STUDY_1, PERSONAL_STUDY_2, PERSONAL_STUDY_3B, PERSONAL_STUDY_4)}


# Funds tracking the same index as another in a plan. SPYM was SPLG until 2025-10-31: its history before then comes from
# the provider's symbol mapping, which `twin_check` holds to SPY's moves.
TWINS = {"SPYM": BENCHMARK}
TWIN_DRIFT_SESSIONS = 21
TWIN_MAX_DRIFT = 0.015
TWIN_MIN_CORRELATION = 0.98


def twin_check(a: pd.Series, b: pd.Series, top: int = 5) -> dict:
    """Does close series `a` move with its twin `b`? A wrongly mapped or spliced history shifts `a`'s level against
    `b` for good, so the test is the largest move of their log-level gap between two points 21 sessions apart (each
    the median of 5 sessions, so one or two bad prints do not count): at most 1.5 points, with a daily-return
    correlation of at least 0.98. A bad closing print moves one day and reverses the next: it shows among the largest
    daily gaps (reported with the next day's gap) but not as drift."""
    import numpy as np

    both = pd.concat([np.log(a.astype(float)).diff(), np.log(b.astype(float)).diff()], axis=1, join="inner").dropna()
    gap = both.iloc[:, 0] - both.iloc[:, 1]
    level = gap.cumsum().rolling(5, center=True).median()
    drift = (level - level.shift(TWIN_DRIFT_SESSIONS)).abs()
    corr = float(both.corr().iloc[0, 1])
    nxt = gap.shift(-1)
    largest = [{"day": d.date().isoformat(), "gap": float(gap[d]),
                "next_day_gap": None if pd.isna(nxt[d]) else float(nxt[d])} for d in gap.abs().nlargest(top).index]
    max_drift = float(drift.max()) if len(drift.dropna()) else 0.0
    return {"correlation": corr, "max_daily_return_gap": float(gap.abs().max()), "max_drift": max_drift,
            "max_drift_ends": drift.idxmax().date().isoformat() if len(drift.dropna()) else None,
            "largest_daily_gaps": largest,
            "consistent": corr >= TWIN_MIN_CORRELATION and max_drift <= TWIN_MAX_DRIFT}


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
    rets = {BENCHMARK: base, "SPYM": base, "SSO": 2 * base - 0.00004, "UPRO": 3 * base - 0.00006,
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


def load_etf_data(plan: EtfStudyPlan | BatteryPlan, source: str, *, now: datetime | None = None, env: dict | None = None,
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
    twins = {}
    for sym, twin in TWINS.items():  # a renamed fund's history must move with a fund that tracks the same index
        if sym in plan.symbols and twin in plan.symbols:
            twins[sym] = twin_check(bars[sym]["close"], bars[twin]["close"]) | {"twin": twin}
            if not twins[sym]["consistent"]:
                t = twins[sym]
                stop = RunStop(STATUS_SOURCE_INVALID, f"{sym} does not track {twin} (correlation {t['correlation']:.3f}, "
                               f"largest {TWIN_DRIFT_SESSIONS}-session drift {100 * t['max_drift']:.1f} points): its "
                               "history may be mapped or spliced wrongly")
                stop.diagnostics = {"symbols": quality, "twins": twins}  # written by the CLI for review
                raise stop
    b = compute_boundaries_v2(bars[BENCHMARK].index)
    spy_days = bars[BENCHMARK].index
    d0, d1 = b.dev_range
    prov = {"data_class": data_class, "provider": provider, "feed": "sip", "adjustment": plan.adjustment,
            "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "window_start": str(start),
            "window_end": str(end), "dataset_hash": data_hash(bars),
            "rights_note": "derived aggregates only; no vendor OHLCV is stored or published"}
    dq = {"symbols": quality, "data_end_after_finality_lag": str(end), "provider_requests_total": requests,
          "twins": twins}
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


def stationary_bootstrap(x, resamples: int, mean_block: int, seed: int) -> tuple[float, float] | None:
    """5% and 95% quantiles of the annualized mean of monthly series `x` under the stationary bootstrap (Politis &
    Romano 1994): each step starts a new block at a random month with probability 1/`mean_block`, else takes the next
    month (wrapping around), so blocks have geometric lengths with mean `mean_block`."""
    import numpy as np

    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 12:
        return None
    rng = np.random.default_rng(seed)
    jump = rng.random((resamples, n)) < 1.0 / mean_block
    fresh = rng.integers(n, size=(resamples, n))
    idx = np.empty((resamples, n), dtype=int)
    idx[:, 0] = fresh[:, 0]
    for j in range(1, n):
        idx[:, j] = np.where(jump[:, j], fresh[:, j], (idx[:, j - 1] + 1) % n)
    means = x[idx].mean(axis=1) * 12
    return float(np.quantile(means, 0.05)), float(np.quantile(means, 0.95))


def rolling_vs_spy(method: dict[str, float], spy: dict[str, float], months: int) -> dict:
    """Every `months`-month window of the month-end values: the share in which the method grew more than SPY, and the
    median and worst difference in growth."""
    import statistics

    keys = sorted(set(method) & set(spy))
    diffs = [method[b] / method[a] - spy[b] / spy[a] for a, b in zip(keys, keys[months:], strict=False)]
    if not diffs:
        return {"windows": 0, "beat_share": None, "median_excess": None, "worst_excess": None}
    return {"windows": len(diffs), "beat_share": sum(d > 0 for d in diffs) / len(diffs),
            "median_excess": statistics.median(diffs), "worst_excess": min(diffs)}


DEV_FIELDS = ("end_value", "end_value_spy", "cagr", "spy_cagr", "excess_log_growth_per_year", "max_drawdown",
              "spy_max_drawdown", "rolling_12m_beat_share", "years")


def run_battery(plan: BatteryPlan, data: EtfData, out_dir: Path, *, experiment_id: str,
                progress: Callable[[str], None] = print, code_sha: str | None = None) -> dict:
    """personal-study-3: every candidate and reference through the registered battery. Writes personal_study.json and
    PERSONAL_STUDY.md (aggregates only)."""
    from datetime import date as date_cls

    out_dir.mkdir(parents=True, exist_ok=True)
    cal = default_calendar()
    full_first, dev_first, last = date_cls.fromisoformat(plan.full_first), data.first, data.last
    span = cal.sessions_between(full_first, last)
    mid = span[len(span) // 2]  # the halves: decisions full_first → mid, then mid → last, $1000 each
    windows = {"full": (full_first, last), "first_half": (full_first, mid), "second_half": (mid, last),
               "dev": (dev_first, last)}
    rows = {}
    for k in (*plan.references, *plan.candidates):
        spec = M.METHODS[k]
        progress(f"simulating {k}")
        runs = {w: run_method(spec, data.bars, a, b, plan.cost_bps, plan.initial_cash) for w, (a, b) in windows.items()}
        full = runs["full"]
        stress = {f"{c:g}bps": run_method(spec, data.bars, full_first, last, c, plan.initial_cash)[
            "excess_log_growth_per_year"] for c in plan.cost_stress_bps}
        mv, sv = full["monthly_values"]["method"], full["monthly_values"]["spy"]
        keys = sorted(set(mv) & set(sv))
        active = [mv[b] / mv[a] - sv[b] / sv[a] for a, b in itertools.pairwise(keys)]
        boot = stationary_bootstrap(active, plan.bootstrap_resamples, plan.bootstrap_block_months,
                                    plan.bootstrap_seed)
        rolling = {f"{m}m": rolling_vs_spy(mv, sv, m) for m in plan.rolling_months}
        r36 = rolling.get("36m", {}).get("beat_share")
        tests = {"full_excess_positive": full["excess_log_growth_per_year"] > 0,
                 "first_half_excess_positive": runs["first_half"]["excess_log_growth_per_year"] > 0,
                 "second_half_excess_positive": runs["second_half"]["excess_log_growth_per_year"] > 0,
                 "drawdown_within_10_points": full["max_drawdown"] >= full["spy_max_drawdown"] - plan.max_extra_drawdown,
                 "rolling36_share_at_least_60pct": r36 is not None and r36 >= plan.min_rolling36_share,
                 "positive_at_20bps": stress.get("20bps", -1.0) > 0}
        if all(tests.values()):
            disposition = "BEATS_SPY_REPEATEDLY"
        elif tests["full_excess_positive"]:
            disposition = "BEATS_SPY_PARTLY"
        else:
            disposition = "DOES_NOT_BEAT_SPY"
        rows[k] = {"role": "reference" if k in plan.references else "trial", "title": spec.title,
                   "source": getattr(spec, "source", ""), "full": full,
                   **{w: {f: runs[w][f] for f in DEV_FIELDS} for w in ("first_half", "second_half", "dev")},
                   "cost_stress_full_excess": stress, "rolling_full": rolling,
                   "bootstrap_full_annual_excess_90": boot, "tests": tests, "disposition": disposition}
    holm = _holm({k: r["full"]["monthly_active"]["one_sided_p"] for k, r in rows.items() if r["role"] == "trial"})
    for k, r in rows.items():
        r["full"]["monthly_active"]["holm_p"] = holm.get(k)
    order = [*plan.references, *plan.candidates]
    qualified = [k for k in order if rows[k]["disposition"] == "BEATS_SPY_REPEATEDLY"]
    pick = max(qualified, key=lambda k: (rows[k]["full"]["excess_log_growth_per_year"], -order.index(k))) \
        if qualified else None
    body = {"schema": "apex-discovery-v3-personal-study-3", "experiment_id": experiment_id, "plan": plan.to_dict(),
            "plan_sha256": plan.sha256(), "code_sha": code_sha or os.environ.get("GITHUB_SHA"),
            "provenance": data.provenance, "research_only": True, "status": "COMPLETED", "returns": "total",
            "windows": {w: {"first_decision": a.isoformat(), "last_session": b.isoformat()}
                        for w, (a, b) in windows.items()} | {"label": "DEV_CONTAMINATED", "tier": "DEV_DIAGNOSTIC"},
            "benchmark": "SPY bought with the same $1000 at the first fill open, fractional, frictionless, total return",
            "methods": rows, "recommendation": {"method": pick, "qualified": qualified, "rule": plan.notes},
            "publication": "aggregates and the ETFs' names only: no price is written",
            "finished_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (out_dir / "personal_study.json").write_text(json.dumps(body, indent=1, sort_keys=True, default=str))
    (out_dir / "PERSONAL_STUDY.md").write_text(battery_md(body))
    return {"status": body["status"], "recommendation": body["recommendation"],
            "full_excess": {k: r["full"]["excess_log_growth_per_year"] for k, r in rows.items()}}


def battery_md(body: dict) -> str:
    w = body["windows"]
    rows = sorted(body["methods"].items(), key=lambda kv: -kv[1]["full"]["excess_log_growth_per_year"])
    rec = body["recommendation"]
    lines = [f"# Personal method study `{body['plan']['plan_id']}` ({body['experiment_id']})", "",
             f"Status **{body['status']}**. FULL window: decisions {w['full']['first_decision']} → "
             f"{w['full']['last_session']}, in halves split at {w['second_half']['first_decision']}; DEV window from "
             f"{w['dev']['first_decision']} ({w['label']}). $1000, whole shares, fills at the next open, "
             f"{body['plan']['cost_bps']:g} bps a side; SPY: the same $1000, fractional and frictionless. Total returns "
             "(splits and dividends), SPY's too.", "",
             f"**Recommended direction: `{rec['method']}`.**" if rec["method"] else
             "**No method beat SPY repeatedly under the registered rule.**", "",
             "| Method | Role | $1000 → (FULL) | SPY → | Growth / yr | Excess / yr FULL | 1st half | 2nd half | DEV | "
             "Max drawdown (SPY) | Ahead in 36-month windows | 90% bootstrap, excess / yr | Excess at 20 bps | "
             "Trades / yr | Holm p | Disposition |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, r in rows:
        f = r["full"]
        boot = r["bootstrap_full_annual_excess_90"]
        r36 = r["rolling_full"].get("36m", {}).get("beat_share")
        holm = f["monthly_active"].get("holm_p")
        lines.append(
            f"| {k} | {r['role']} | ${f['end_value']:,.0f} | ${f['end_value_spy']:,.0f} | {_pct(f['cagr'])} | "
            f"{_pct(f['excess_log_growth_per_year'])} | {_pct(r['first_half']['excess_log_growth_per_year'])} | "
            f"{_pct(r['second_half']['excess_log_growth_per_year'])} | {_pct(r['dev']['excess_log_growth_per_year'])} | "
            f"{_pct(f['max_drawdown'], 0)} ({_pct(f['spy_max_drawdown'], 0)}) | "
            f"{'—' if r36 is None else f'{100 * r36:.0f}%'} | "
            f"{'—' if boot is None else f'{_pct(boot[0])} to {_pct(boot[1])}'} | "
            f"{_pct(r['cost_stress_full_excess'].get('20bps'))} | {f['trades']['per_year']:.1f} | "
            f"{'—' if holm is None else f'{holm:.3f}'} | {r['disposition']} |")
    lines += ["", "Tests failed (FULL window unless named):", ""]
    for k, r in rows:
        failed = [t for t, ok in r["tests"].items() if not ok]
        lines.append(f"- `{k}`: {', '.join(failed) if failed else 'none'}")
    lines += ["", "Calendar years, FULL window (method vs SPY):", ""]
    for k, r in rows:
        yrs = " · ".join(f"{y} {_pct(v['method'], 0)} vs {_pct(v['spy'], 0)}" for y, v in
                         r["full"]["calendar_years"].items())
        lines.append(f"- `{k}`: {yrs}")
    lines += ["", "DEV_CONTAMINATED diagnostics, never validation: the forward record of the alerts is the test."]
    return "\n".join(lines) + "\n"


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


__all__ = ["PERSONAL_PLANS", "PERSONAL_STUDY_1", "PERSONAL_STUDY_2", "PERSONAL_STUDY_3B", "BatteryPlan", "EtfData",
           "EtfStudyPlan", "PersonalStudyPlan", "battery_md", "load_etf_data", "rolling_vs_spy", "run_battery",
           "run_etf_study", "run_method", "run_personal_study", "stationary_bootstrap", "summary_md",
           "synthetic_etf_bars"]
