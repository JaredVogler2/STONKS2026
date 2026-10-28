"""Discovery V3 study `personal-study-4` (docs/discovery-v3/PERSONAL_STUDY_4.md): do the S&P 500 timing methods of
personal-studies 2-3 hold up over a century of US market history?

Their funds exist only since 2006 (SSO) and Alpaca's bars since 2016, so studies 2-3 saw one decade. This study rebuilds
each method from the Kenneth R. French Data Library's daily US market return (Mkt-RF + RF: the CRSP value-weighted index
of US stocks, dividends included) and its T-bill return (RF), from July 1926 to the file's last month:
* the 1x fund (SPY's role, and the benchmark) earns the market return less SPY's fee;
* the 2x fund (SSO's role) earns twice the market's daily return, pays the T-bill rate plus a spread on the borrowed half,
  and a fee;
* T-bills (BIL's role) earn RF less BIL's fee.
Every signal reads the market index at a close. The position changes at the NEXT close (one session late, as the owner
trades the day after an alert); trading at the signal's own close is reported as a sensitivity. Costs are charged on
the traded fraction of the account. Weights are continuous: at a century's scale whole shares do not matter.
Only aggregates and the downloaded file's sha256 leave the runner: no return series is written.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from apex_worker.personal import method as M

FRENCH_DAILY_URL = ("https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
                    "F-F_Research_Data_Factors_daily_CSV.zip")
ASSET = {"SPY": "1x", "SPYM": "1x", "SSO": "2x", "BIL": "bills"}  # the funds' roles in the rebuilt history
STATUS_SOURCE_BLOCKED = "SOURCE_BLOCKED"


@dataclass(frozen=True)
class LongHistoryPlan:
    """personal-study-4: the S&P 500 timing methods on a century of daily US market returns."""

    plan_id: str
    title: str
    candidates: tuple[str, ...]  # personal.method METHODS keys that need only the index and T-bills
    references: tuple[str, ...]  # evaluated alongside, not new trials
    published: tuple[tuple[str, int], ...]  # method -> publication year; "post-publication" starts the next January
    source_url: str = FRENCH_DAILY_URL
    warmup_sessions: int = 252  # the first decision after a year of data: every signal has its history
    index_fee: float = 0.0009  # SPY's expense ratio
    fund_fee: float = 0.0090  # a 2x fund's expense ratio (SSO's is 0.89%)
    financing_spread: float = 0.0050  # over T-bills, on the borrowed half of a 2x fund
    bill_fee: float = 0.0014  # BIL's expense ratio
    lag_sessions: int = 1  # decided at close t, traded at close t + 1
    cost_bps: float = 5.0
    cost_stress_bps: float = 20.0
    decades: tuple[int, ...] = (1930, 1940, 1950, 1960, 1970, 1980, 1990, 2000, 2010)  # the complete ones
    min_decades_ahead: int = 6
    rolling_years: int = 10
    min_rolling_share: float = 0.60
    max_extra_drawdown: float = 0.10
    notes: str = ""

    def to_dict(self) -> dict:
        names = (*self.references, *self.candidates)
        return asdict(self) | {"methods": {k: M.METHODS[k].to_dict() for k in names}}

    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class DailyHistory:
    dates: np.ndarray  # datetime64[D], ascending
    market: np.ndarray  # daily total return of the US market (Mkt-RF + RF), as a fraction
    bills: np.ndarray  # daily T-bill return (RF), as a fraction
    provenance: dict
    data_quality: dict


# ---- data -----------------------------------------------------------------------------------------------------------
def parse_french_daily(raw: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """The daily Fama/French factors file (a zip holding one CSV): dates, market and T-bill returns as fractions, and the
    file's own header notes. Raises ValueError on anything unexpected."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = z.namelist()
            if len(names) != 1 or not names[0].lower().endswith(".csv"):
                raise ValueError(f"expected one CSV in the zip, found {names}")
            text = z.read(names[0]).decode("latin-1")
    except zipfile.BadZipFile:
        raise ValueError("not a zip file") from None
    lines = text.splitlines()
    head = next((i for i, ln in enumerate(lines) if ln.replace(" ", "").lower() == ",mkt-rf,smb,hml,rf"), None)
    if head is None:
        raise ValueError("no ',Mkt-RF,SMB,HML,RF' header")
    rows = []
    for ln in lines[head + 1:]:
        if not ln.strip():
            if rows:
                break
            continue
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) != 5 or not re.fullmatch(r"\d{8}", parts[0]):
            if rows:
                break
            raise ValueError(f"unexpected first data row {ln[:40]!r}")
        rows.append(parts)
    dates = np.array([f"{p[0][:4]}-{p[0][4:6]}-{p[0][6:]}" for p in rows], dtype="datetime64[D]")
    vals = np.array([[float(p[1]), float(p[4])] for p in rows], dtype=float) / 100.0
    notes = " ".join(ln.strip() for ln in lines[:head] if ln.strip())
    return dates, vals[:, 0] + vals[:, 1], vals[:, 1], {"header": notes[:400], "rows": len(rows)}


def check_history(dates: np.ndarray, market: np.ndarray, bills: np.ndarray) -> list[str]:
    """What makes the file unusable (empty when it is fine)."""
    bad = []
    if len(dates) < 25_000:
        bad.append(f"only {len(dates)} daily rows")
    if len(dates) and dates[0] != np.datetime64("1926-07-01"):
        bad.append(f"starts {dates[0]}, not 1926-07-01")
    if len(dates) > 1 and not (np.diff(dates.astype("int64")) > 0).all():
        bad.append("dates are not strictly increasing")
    if not (np.isfinite(market).all() and np.isfinite(bills).all()):
        bad.append("non-numeric returns")
    elif len(market) and (np.abs(market).max() > 0.30 or bills.min() < -0.001 or bills.max() > 0.002):
        bad.append("returns outside plausible bounds")
    return bad


def synthetic_history(seed: int = 4, years: int = 100) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """SYNTHETIC daily returns for DEMO runs and tests (never market data): fat-tailed market days, drifting T-bills."""
    rng = np.random.default_rng(seed)
    start = np.datetime64("1926-07-01")
    days = np.arange(start, start + np.timedelta64(int(years * 365.25), "D"), dtype="datetime64[D]")
    days = days[np.is_busday(days)]
    shocks = rng.standard_t(4, len(days)) * 0.0075
    market = 0.0004 + shocks
    bills = np.full(len(days), 0.00012)
    return days, market, bills


def load_history(plan: LongHistoryPlan, source: str, *, fetch: Callable[[str], bytes] | None = None) -> DailyHistory:
    """`source`: "french" downloads the plan's file (one request); "fixture" builds the SYNTHETIC history."""
    from apex_worker.research.realeq.data import STATUS_SOURCE_INVALID, RunStop

    if source == "fixture":
        dates, market, bills = synthetic_history()
        prov = {"data_class": "SYNTHETIC_FIXTURE_DEMO", "provider": "fixture-emulation"}
        notes = {"header": "synthetic", "rows": len(dates)}
        sha = None
    elif source == "french":
        try:
            raw = (fetch or _download)(plan.source_url)
        except Exception as exc:  # the type and an HTTP status only
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise RunStop(STATUS_SOURCE_BLOCKED, f"download failed: {type(exc).__name__}"
                          + (f" {status}" if isinstance(status, int) else "")) from None
        try:
            dates, market, bills, notes = parse_french_daily(raw)
        except ValueError as exc:
            raise RunStop(STATUS_SOURCE_INVALID, f"the daily factors file does not parse: {exc}") from None
        sha = hashlib.sha256(raw).hexdigest()
        prov = {"data_class": "REAL_MARKET_DATA", "provider": "Kenneth R. French Data Library",
                "url": plan.source_url, "file_sha256": sha, "file_bytes": len(raw)}
    else:
        raise ValueError("source must be french or fixture")
    bad = check_history(dates, market, bills)
    if bad:
        raise RunStop(STATUS_SOURCE_INVALID, "; ".join(bad))
    prov |= {"fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "first_day": str(dates[0]),
             "last_day": str(dates[-1]), "file_notes": notes["header"],
             "rights_note": "derived aggregates only; the file is not stored or published"}
    dq = {"rows": len(dates), "first_day": str(dates[0]), "last_day": str(dates[-1]),
          "worst_market_day": float(market.min()), "best_market_day": float(market.max())}
    return DailyHistory(dates, market, bills, prov, dq)


def _download(url: str) -> bytes:
    import httpx

    with httpx.Client(timeout=60.0, follow_redirects=True, headers={"User-Agent": "apex-research/1.0"}) as client:
        r = client.get(url)
        r.raise_for_status()
        if len(r.content) > 20_000_000:
            raise ValueError("unexpectedly large file")
        return r.content


# ---- the methods on the rebuilt history -----------------------------------------------------------------------------
def fund_returns(plan: LongHistoryPlan, h: DailyHistory) -> dict[str, np.ndarray]:
    day = 1 / 252
    return {"1x": h.market - plan.index_fee * day,
            "2x": 2 * h.market - h.bills - (plan.financing_spread + plan.fund_fee) * day,
            "bills": h.bills - plan.bill_fee * day,
            "cash": np.zeros(len(h.market))}


def _sma(x: np.ndarray, n: int) -> np.ndarray:
    """Trailing n-session mean at each index (NaN before n sessions)."""
    c = np.concatenate([[0.0], np.cumsum(x)])
    out = np.full(len(x), np.nan)
    out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def _rsi_wilder(level: np.ndarray, days: int) -> np.ndarray:
    d = np.diff(level, prepend=level[0])
    up, down = np.clip(d, 0, None), np.clip(-d, 0, None)
    a = 1.0 / days
    ru, rd = np.empty(len(d)), np.empty(len(d))
    u = v = 0.0
    for i in range(len(d)):
        u, v = (1 - a) * u + a * up[i], (1 - a) * v + a * down[i]
        ru[i], rd[i] = u, v
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(rd == 0, 100.0, 100 - 100 / (1 + ru / rd))


def decisions(spec: M.Spec, h: DailyHistory, first: int) -> dict[int, dict[str, float]]:
    """The method's target weights decided at each close t >= `first` where it would trade (its first decision, a
    scheduled rebalance, or a change of target). The last session decides nothing."""
    level = np.cumprod(1 + h.market)
    n = len(level)
    months = h.dates.astype("datetime64[M]")
    month_end = np.append(months[1:] != months[:-1], False)  # the last session of each month (not the file's last)
    out: dict[int, dict[str, float]] = {}

    def emit_changes(targets: list[dict[str, float]]) -> None:
        prev = None
        for i, tgt in enumerate(targets):
            t = first + i
            if tgt != prev:
                out[t] = tgt
                prev = tgt

    span = range(first, n - 1)
    if isinstance(spec, M.TrendSpec):
        sma = _sma(level, spec.sma)
        on, off = {ASSET[spec.asset]: 1.0}, {ASSET[spec.risk_off]: 1.0}
        emit_changes([on if level[t] > sma[t] else off for t in span])
        return out
    if not isinstance(spec, M.RuleSpec) or spec.kind not in ("hold", "vol-target", "seasonal", "turn-of-month",
                                                              "rsi2") or not set(spec.universe) <= set(ASSET):
        raise ValueError(f"{spec.method_id} needs more than the index and T-bills")
    a, cash = ASSET[spec.assets[0]], ASSET[spec.cash]
    if spec.kind == "hold":
        return {first: {a: 1.0}}
    if spec.kind == "vol-target":
        lr = np.diff(np.log(level), prepend=np.nan)
        k = int(spec.p("lookback"))
        for t in span:
            if t == first or month_end[t]:
                vol = float(np.std(lr[t - k + 1:t + 1], ddof=1) * math.sqrt(252))
                e = min(spec.p("max_exposure"), spec.p("target_vol") / vol) if vol > 0 else spec.p("max_exposure")
                out[t] = {a: e / 2, cash: 1 - e / 2}
        return out
    if spec.kind == "seasonal":
        first_m, last_m = int(spec.p("first_month")), int(spec.p("last_month"))
        nxt_month = (months[1:].astype(int) % 12) + 1  # calendar month (1-12) of the next session
        prev = None
        for t in span:
            if t == first or month_end[t]:
                m = nxt_month[t]
                tgt = {a: 1.0} if (m >= first_m or m <= last_m) else {cash: 1.0}
                if tgt != prev:
                    out[t] = tgt
                    prev = tgt
        return out
    if spec.kind == "turn-of-month":
        before, after = int(spec.p("days_before")), int(spec.p("days_after"))
        nth = np.ones(n, dtype=int)  # each session's position within its month (1 = first)
        for i in range(1, n):
            nth[i] = nth[i - 1] + 1 if months[i] == months[i - 1] else 1
        left = np.zeros(n, dtype=int)  # sessions after it in its month (0 = its month's last)
        for i in range(n - 2, -1, -1):
            left[i] = left[i + 1] + 1 if months[i] == months[i + 1] else 0
        left[-1] = 0
        targets = []
        for t in span:
            nx = t + 1
            on = nth[nx] <= after or (left[nx] < before and nx < n - 1)
            targets.append({a: 1.0} if on else {cash: 1.0})
        emit_changes(targets)
        return out
    if spec.kind == "rsi2":
        n_trend, n_exit = int(spec.p("trend_days")), int(spec.p("exit_days"))
        trend, exit_avg = _sma(level, n_trend), _sma(level, n_exit)
        rsi = _rsi_wilder(level, int(spec.p("rsi_days")))
        holding, targets = False, []
        for t in span:
            if holding:
                holding = level[t] <= exit_avg[t]
            else:
                holding = bool(rsi[t] < spec.p("entry") and level[t] > trend[t])
            targets.append({a: 1.0} if holding else {cash: 1.0})
        emit_changes(targets)
        return out
    raise AssertionError(spec.kind)  # unreachable: the kinds are checked above


@dataclass
class LongRun:
    equity: np.ndarray  # at each close from `first`
    bench: np.ndarray
    turnover: float  # traded fraction of the account, summed
    switches: int  # fills that changed the holdings


def simulate(targets: dict[int, dict[str, float]], rets: dict[str, np.ndarray], first: int, lag: int,
             cost_bps: float) -> LongRun:
    """$1 in cash at the close `first`; each decision fills at the close `lag` sessions later. The benchmark buys the
    1x fund at the method's first fill."""
    n = len(rets["1x"])
    cost = cost_bps / 1e4
    fills = {t + lag: w for t, w in targets.items() if t + lag < n}
    start = min(fills)
    hold = {"cash": 1.0}
    eq, bench = np.empty(n - first), np.empty(n - first)
    b = 1.0
    turnover, switches = 0.0, 0
    for t in range(first, n):
        if t > first:
            for k in hold:
                hold[k] *= 1 + rets[k][t]
            if t > start:
                b *= 1 + rets["1x"][t]
        e = sum(hold.values())
        if t in fills:
            new = {k: w * e for k, w in fills[t].items() if w > 0}
            traded = sum(abs(new.get(k, 0.0) - hold.get(k, 0.0)) for k in {*new, *hold} - {"cash"})  # fund trades
            if traded > 1e-12 * e:
                switches += 1
                turnover += traded / e
                e_after = e - traded * cost
                hold = {k: v * e_after / e for k, v in new.items()}
        eq[t - first], bench[t - first] = sum(hold.values()), b
    return LongRun(eq, bench, turnover, switches)


# ---- metrics --------------------------------------------------------------------------------------------------------
def _drawdown(x: np.ndarray) -> float:
    return float((x / np.maximum.accumulate(x) - 1).min())


def _years(d0: np.datetime64, d1: np.datetime64) -> float:
    return float((d1 - d0).astype(int)) / 365.25


def _excess(eq: np.ndarray, bench: np.ndarray, i: int, j: int, years: float) -> float:
    return (math.log(eq[j] / eq[i]) - math.log(bench[j] / bench[i])) / years


def window_stats(run: LongRun, dates: np.ndarray, plan: LongHistoryPlan, pub_year: int | None) -> dict:
    from apex_worker.personal import backtest as B

    eq, bench, d = run.equity, run.bench, dates
    yrs = _years(d[0], d[-1])
    g, gb = math.log(eq[-1] / eq[0]) / yrs, math.log(bench[-1] / bench[0]) / yrs
    years = d.astype("datetime64[Y]").astype(int) + 1970
    months = d.astype("datetime64[M]")
    m_end = np.append(np.nonzero(months[1:] != months[:-1])[0], len(d) - 1)
    mv, mb = eq[m_end], bench[m_end]
    active = (mv[1:] / mv[:-1]) - (mb[1:] / mb[:-1])
    t = B._hac_t(active)
    span = 12 * plan.rolling_years
    roll = (mv[span:] / mv[:-span]) - (mb[span:] / mb[:-span])
    decade_rows = {}
    for dec in plan.decades:
        inside = np.nonzero((years >= dec) & (years <= dec + 9))[0]
        if len(inside) == 0 or inside[0] == 0:
            decade_rows[f"{dec}s"] = None
            continue
        i, j = inside[0] - 1, inside[-1]
        decade_rows[f"{dec}s"] = {"excess_log_growth_per_year": _excess(eq, bench, i, j, _years(d[i], d[j])),
                                  "method_growth": float(eq[j] / eq[i] - 1), "market_growth": float(bench[j] / bench[i] - 1)}
    post = None
    if pub_year is not None:
        after = np.nonzero(years >= pub_year + 1)[0]
        if len(after) > 1 and after[0] > 0:
            i, j = after[0] - 1, len(d) - 1
            post = {"from": str(d[after[0]]), "years": _years(d[i], d[j]),
                    "excess_log_growth_per_year": _excess(eq, bench, i, j, _years(d[i], d[j]))}
            if i > 0:
                post["before_excess_log_growth_per_year"] = _excess(eq, bench, 0, i, _years(d[0], d[i]))
    y_end = np.append(np.nonzero(years[1:] != years[:-1])[0], len(d) - 1)
    yv, yb = np.concatenate([[eq[0]], eq[y_end]]), np.concatenate([[bench[0]], bench[y_end]])
    yearly = (yv[1:] / yv[:-1] - 1) - (yb[1:] / yb[:-1] - 1)
    daily = eq[1:] / eq[:-1] - 1
    return {"years": yrs, "end_value_per_dollar": float(eq[-1] / eq[0]), "market_end_value_per_dollar":
            float(bench[-1] / bench[0]), "log_growth_per_year": g, "market_log_growth_per_year": gb,
            "excess_log_growth_per_year": g - gb, "cagr": math.exp(g) - 1, "market_cagr": math.exp(gb) - 1,
            "max_drawdown": _drawdown(eq), "market_max_drawdown": _drawdown(bench),
            "worst_day": float(daily.min()), "worst_year_vs_market": float(yearly.min()),
            "best_year_vs_market": float(yearly.max()),
            "monthly_active": {"months": len(active), "mean": float(active.mean()), "hac_t": t,
                               "one_sided_p": B._p_one_sided(t)},
            f"rolling_{plan.rolling_years}y": {"windows": len(roll), "beat_share": float((roll > 0).mean()),
                                               "median_excess": float(np.median(roll)), "worst_excess": float(roll.min())},
            "decades": decade_rows, "post_publication": post,
            "switches_per_year": run.switches / yrs, "turnover_per_year": run.turnover / yrs}


def run_long_history(plan: LongHistoryPlan, data: DailyHistory, out_dir: Path, *, experiment_id: str,
                     progress: Callable[[str], None] = print, code_sha: str | None = None) -> dict:
    """personal-study-4: every method on the rebuilt century. Writes personal_study.json and PERSONAL_STUDY.md
    (aggregates only)."""
    from apex_worker.discovery.v3.personal_study import _holm

    out_dir.mkdir(parents=True, exist_ok=True)
    rets = fund_returns(plan, data)
    first = plan.warmup_sessions
    pub = dict(plan.published)
    rows = {}
    for k in (*plan.references, *plan.candidates):
        spec = M.METHODS[k]
        progress(f"simulating {k}")
        targets = decisions(spec, data, first)
        main = simulate(targets, rets, first, plan.lag_sessions, plan.cost_bps)
        stats = window_stats(main, data.dates[first:], plan, pub.get(k))
        same_close = window_stats(simulate(targets, rets, first, 0, plan.cost_bps), data.dates[first:], plan, None)
        stress = window_stats(simulate(targets, rets, first, plan.lag_sessions, plan.cost_stress_bps),
                              data.dates[first:], plan, None)
        ahead = sum(1 for v in stats["decades"].values() if v and v["excess_log_growth_per_year"] > 0)
        roll = stats[f"rolling_{plan.rolling_years}y"]["beat_share"]
        post = stats["post_publication"]
        tests = {"full_excess_positive": stats["excess_log_growth_per_year"] > 0,
                 f"ahead_in_{plan.min_decades_ahead}_of_{len(plan.decades)}_decades": ahead >= plan.min_decades_ahead,
                 "drawdown_within_10_points": stats["max_drawdown"] >= stats["market_max_drawdown"] -
                 plan.max_extra_drawdown,
                 f"rolling_{plan.rolling_years}y_share_at_least_60pct": roll >= plan.min_rolling_share,
                 "post_publication_excess_positive": post is not None and post["excess_log_growth_per_year"] > 0,
                 f"positive_at_{plan.cost_stress_bps:g}bps": stress["excess_log_growth_per_year"] > 0}
        if all(tests.values()):
            disposition = "HOLDS_UP"
        elif tests["full_excess_positive"]:
            disposition = "HOLDS_UP_PARTLY"
        else:
            disposition = "DOES_NOT_HOLD_UP"
        rows[k] = {"role": "reference" if k in plan.references else "replication", "title": spec.title,
                   "source": getattr(spec, "source", ""), "published": pub.get(k), "full": stats,
                   "decades_ahead": ahead, "same_close_excess_log_growth_per_year": same_close[
                       "excess_log_growth_per_year"],
                   "cost_stress_excess_log_growth_per_year": stress["excess_log_growth_per_year"],
                   "tests": tests, "disposition": disposition}
    holm = _holm({k: r["full"]["monthly_active"]["one_sided_p"] for k, r in rows.items() if r["role"] != "reference"})
    for k, r in rows.items():
        r["full"]["monthly_active"]["holm_p"] = holm.get(k)
    first_day = data.dates[first]
    body = {"schema": "apex-discovery-v3-personal-study-4", "experiment_id": experiment_id, "plan": plan.to_dict(),
            "plan_sha256": plan.sha256(), "code_sha": code_sha or os.environ.get("GITHUB_SHA"),
            "provenance": data.provenance, "research_only": True, "status": "COMPLETED", "returns": "total",
            "window": {"first_decision": str(first_day), "last_session": str(data.dates[-1]),
                       "label": "KNOWN_HISTORY", "tier": "ROBUSTNESS_REPLICATION"},
            "benchmark": "the 1x fund (the US market less SPY's fee) bought at the method's first fill",
            "methods": rows, "rule": plan.notes,
            "publication": "aggregates only: no return series and not the downloaded file",
            "finished_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (out_dir / "personal_study.json").write_text(json.dumps(body, indent=1, sort_keys=True, default=str))
    (out_dir / "PERSONAL_STUDY.md").write_text(long_history_md(body))
    return {"status": body["status"], "dispositions": {k: r["disposition"] for k, r in rows.items()},
            "full_excess": {k: r["full"]["excess_log_growth_per_year"] for k, r in rows.items()}}


def _pct(x: float | None, d: int = 1) -> str:
    return "—" if x is None else f"{100 * x:+.{d}f}%"


def _money(x: float) -> str:
    return f"${x:,.0f}" if x < 1e7 else f"${x:,.3g}"


def long_history_md(body: dict) -> str:
    w, plan = body["window"], body["plan"]
    rows = sorted(body["methods"].items(), key=lambda kv: -kv[1]["full"]["excess_log_growth_per_year"])
    ry = plan["rolling_years"]
    lines = [f"# Personal method study `{plan['plan_id']}` ({body['experiment_id']})", "",
             f"Status **{body['status']}**. Daily US market history ({body['provenance'].get('provider')}), first "
             f"decision {w['first_decision']}, last session {w['last_session']}. Decided at a close, traded at the "
             f"close {plan['lag_sessions']} session later, {plan['cost_bps']:g} bps a side; the benchmark is the 1x "
             "fund (the market less SPY's fee). Total returns. A robustness replication on well-known history, "
             "not validation.", "",
             f"| Method | Role | $1000 → | Market → | Growth / yr | Excess / yr | Decades ahead | Max drawdown "
             f"(market) | Worst day | Ahead in {ry}-year windows | Post-publication excess / yr | Same-close excess | "
             f"Excess at {plan['cost_stress_bps']:g} bps | Switches / yr | Disposition |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, r in rows:
        f = r["full"]
        post = f["post_publication"]
        lines.append(
            f"| {k} | {r['role']} | {_money(1000 * f['end_value_per_dollar'])} | "
            f"{_money(1000 * f['market_end_value_per_dollar'])} | {_pct(f['cagr'])} | "
            f"{_pct(f['excess_log_growth_per_year'])} | {r['decades_ahead']} of {len(plan['decades'])} | "
            f"{_pct(f['max_drawdown'], 0)} ({_pct(f['market_max_drawdown'], 0)}) | {_pct(f['worst_day'], 0)} | "
            f"{100 * f[f'rolling_{ry}y']['beat_share']:.0f}% | "
            f"{'—' if post is None else _pct(post['excess_log_growth_per_year']) + ' since ' + post['from'][:4]} | "
            f"{_pct(r['same_close_excess_log_growth_per_year'])} | {_pct(r['cost_stress_excess_log_growth_per_year'])} | "
            f"{f['switches_per_year']:.1f} | {r['disposition']} |")
    lines += ["", "Excess log growth per year over the market, by decade:", "",
              "| Method | " + " | ".join(f"{d}s" for d in plan["decades"]) + " |",
              "|---|" + "---|" * len(plan["decades"])]
    for k, r in rows:
        cells = [_pct(v["excess_log_growth_per_year"]) if v else "—" for v in r["full"]["decades"].values()]
        lines.append(f"| {k} | " + " | ".join(cells) + " |")
    lines += ["", "Tests failed:", ""]
    for k, r in rows:
        failed = [t for t, ok in r["tests"].items() if not ok]
        lines.append(f"- `{k}`: {', '.join(failed) if failed else 'none'}")
    lines += ["", "KNOWN_HISTORY: these decades are well studied (the rules were found on parts of them), so a pass is "
              "evidence of robustness, not of future returns. The forward record of the alerts is the test."]
    return "\n".join(lines) + "\n"


PERSONAL_STUDY_4 = LongHistoryPlan(
    plan_id="personal-study-4",
    title="The S&P 500 timing methods on a century of daily US market returns (1926-2026): decades, rolling 10-year "
          "windows, drawdowns and post-publication returns",
    candidates=("sso-hold", "voltarget-sso", "halloween-sso", "tom-sso", "rsi2-sso"),
    references=("sso-trend200", "spy-trend200"),
    published=(("sso-trend200", 2016), ("spy-trend200", 2016), ("sso-hold", 2016), ("voltarget-sso", 2017),
               ("halloween-sso", 2002), ("tom-sso", 1988), ("rsi2-sso", 2008)),
    notes="Registered before the run. A robustness replication of personal-study-3's S&P 500 timing methods and the "
    "adopted sso-trend200 (with its 1x twin) on the Kenneth R. French Data Library's daily market and T-bill returns "
    "since July 1926: the methods are unchanged, nothing is fitted, and no new trial is counted. HOLDS_UP needs all of: "
    "excess log growth over the market above 0 over the whole history; ahead of the market in at least 6 of the 9 "
    "complete decades (1930s-2010s); maximum drawdown at most 10 points worse than the market's; ahead in at least 60% "
    "of rolling 10-year windows; excess above 0 after its publication (from the January after the year listed); and "
    "excess above 0 at 20 bps a side. HOLDS_UP_PARTLY: whole-history excess above 0 but another test failed. Otherwise "
    "DOES_NOT_HOLD_UP. Trading at the signal's own close, Holm over the five replications and the calendar-year "
    "extremes are reported, not tested. KNOWN_HISTORY: the forward record of the alerts is the test.")


__all__ = ["ASSET", "FRENCH_DAILY_URL", "PERSONAL_STUDY_4", "DailyHistory", "LongHistoryPlan", "LongRun",
           "check_history", "decisions", "fund_returns", "load_history", "long_history_md", "parse_french_daily",
           "run_long_history", "simulate", "synthetic_history", "window_stats"]
