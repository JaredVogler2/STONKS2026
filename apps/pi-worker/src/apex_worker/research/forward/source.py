"""Market data for the forward shadow: the protocol-2 Alpaca SIP path (`data_v2`) with forward windows.

* DECISION data for session t: daily bars from WINDOW_ANCHOR through t for U1 + SPY + the 11 sector ETFs, corporate
  actions over the window, and protocol-2's quality inventory (impossible bars removed, SIP volume check, benchmark
  completeness, minimum usable stocks). Bar t is the post-close bar (observed at least 90 minutes after the close); a
  missing or partial bar t is SOURCE_PENDING (retry later), never filled in.
* OUTCOME data: bars from the earliest pending decision through the final data end (latest completed session minus
  the 2-session finality lag) plus corporate actions for the quarantine.
* No synthetic fallback: without keys the source raises CREDENTIALS_MISSING; an entitlement, feed or integrity problem
  is SOURCE_INVALID. Bars live in a temporary directory and never leave the runner.
"""

from __future__ import annotations

import math
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from apex_worker.market.calendar import MarketCalendar
from apex_worker.market.history import HistorySpec
from apex_worker.research.dataset import data_hash
from apex_worker.research.forward.frozen import WINDOW_ANCHOR, WINDOW_ANCHOR_INDEX
from apex_worker.research.forward.windows import session_index
from apex_worker.research.realeq.data import STATUS_BLOCKED, STATUS_INSUFFICIENT, STATUS_SOURCE_INVALID, RunStop
from apex_worker.research.realeq.data_v2 import _impossible, fetch_v2, quality_inventory_v2, quarantine_dates
from apex_worker.research.realeq.protocol_v2 import PROTOCOL_V2
from apex_worker.research.realeq.universe_v2 import u1_symbols
from apex_worker.research.training_data import BENCHMARK, SECTOR_ETFS

MIN_BAR_T_COVERAGE = 0.95  # share of usable stocks that must already have bar t
MIN_SPY_VOLUME_RATIO = 0.2  # bar t's SPY volume vs its 20-session median: below this the bar is still forming
MAX_REQUESTS = PROTOCOL_V2["data"]["max_requests"]


class SourceUnavailable(Exception):
    """`status`: SOURCE_PENDING (retry later), RATE_LIMITED, CREDENTIALS_MISSING, SOURCE_INVALID, INSUFFICIENT_HISTORY."""

    def __init__(self, status: str, reason: str):
        super().__init__(f"{status}: {reason}")
        self.status, self.reason = status, reason


@dataclass
class DecisionData:
    day: date
    bars: dict[str, pd.DataFrame]
    meta: dict


@dataclass
class OutcomeBars:
    sessions: pd.DatetimeIndex
    bars: dict[str, pd.DataFrame]
    quarantine: dict[str, list[pd.Timestamp]]
    meta: dict = field(default_factory=dict)


def _map_stop(stop: RunStop) -> SourceUnavailable:
    reason = str(stop.reason) if hasattr(stop, "reason") else str(stop)
    status = getattr(stop, "status", STATUS_SOURCE_INVALID)
    if status == STATUS_BLOCKED:
        return SourceUnavailable("CREDENTIALS_MISSING", reason)
    if "HTTP 429" in reason:
        return SourceUnavailable("RATE_LIMITED", reason)
    if status == STATUS_INSUFFICIENT:
        return SourceUnavailable("INSUFFICIENT_HISTORY", reason)
    return SourceUnavailable("SOURCE_INVALID", reason)


def window_quarantine(actions: list[dict] | None, bars: dict[str, pd.DataFrame]) -> dict[str, list[pd.Timestamp]]:
    """protocol-2's quarantine for an outcome window: spin-off ex-dates, the registered known events and split
    ex-dates whose split-adjusted close still jumps by more than the large-move threshold (data_v2's A1 rule)."""
    thr = PROTOCOL_V2["symbol_exclusion"]["large_move_flag_abs_return"]
    suspect: dict[str, list[str]] = {}
    for a in actions or []:
        if "split" not in str(a.get("type", "")):
            continue
        sym, d = a.get("source_symbol") or a.get("symbol"), str(a.get("ex_date") or a.get("process_date") or "")[:10]
        df = bars.get(str(sym))
        if not sym or not d or df is None or pd.Timestamp(d) not in df.index:
            continue
        i = df.index.get_loc(pd.Timestamp(d))
        if i > 0 and abs(float(df["close"].iloc[i]) / float(df["close"].iloc[i - 1]) - 1) > thr:
            suspect.setdefault(str(sym), []).append(d)
    return quarantine_dates(actions, suspect)


class MarketSource:
    """`adapter_factory()` returns an Alpaca adapter (the real one, or the in-process fixture emulation for DEMO runs).
    It may raise RunStop(BLOCKED) when credentials are missing."""

    def __init__(self, adapter_factory: Callable[[], object], cal: MarketCalendar, *, data_class: str = "REAL_MARKET_DATA",
                 provider: str = "alpaca", min_usable_stocks: int | None = None, max_requests: int = MAX_REQUESTS):
        if min_usable_stocks is not None and data_class == "REAL_MARKET_DATA":
            raise ValueError("the usable-stock minimum can be overridden for synthetic fixture runs only")
        self.factory, self.cal, self.data_class, self.provider = adapter_factory, cal, data_class, provider
        self.min_usable, self.max_requests = min_usable_stocks, max_requests
        self.requests_used = 0

    def _spec(self, symbols: tuple[str, ...], start: date, end: date) -> HistorySpec:
        years = max(1, math.ceil((end - start).days / 365.25))  # the fetch starts on or before `start`; trimmed later
        return HistorySpec(symbols=symbols, years=min(years, 15), feed="sip", adjustment="split",
                           max_requests=self.max_requests, max_symbols=600)

    def _fetch(self, symbols: tuple[str, ...], start: date, end: date, now: datetime):
        try:
            with tempfile.TemporaryDirectory(prefix="apex-forward-") as tmp:
                loaded = fetch_v2(self.factory, Path(tmp), self._spec(symbols, start, end), end=end, now=now,
                                  actions_start=start)
        except RunStop as stop:
            raise _map_stop(stop) from None
        self.requests_used += int(loaded.requests_total or 0)
        return loaded

    def decision_data(self, day: date, now: datetime) -> DecisionData:
        loaded = self._fetch(u1_symbols(), WINDOW_ANCHOR, day, now)
        try:
            dq, usable = quality_inventory_v2(loaded, self.cal, first=WINDOW_ANCHOR,
                                              benchmark_start_by=(WINDOW_ANCHOR + timedelta(days=7)).isoformat(),
                                              min_usable_stocks=self.min_usable)
        except RunStop as stop:
            raise _map_stop(stop) from None
        spy = usable.get(BENCHMARK)
        t = pd.Timestamp(day)
        if spy is None or spy.index.max() < t:
            raise SourceUnavailable("SOURCE_PENDING", f"the {day} SPY daily bar is not published yet")
        expected = [pd.Timestamp(d) for d in self.cal.sessions_between(WINDOW_ANCHOR, day)]
        if list(spy.index) != expected:
            raise SourceUnavailable("SOURCE_INVALID", "SPY's sessions differ from the XNYS calendar over the window "
                                    "(an unscheduled closure or a calendar/data mismatch): the protocol axis is unknown")
        if session_index(self.cal, spy.index[0].date()) != WINDOW_ANCHOR_INDEX:
            raise SourceUnavailable("SOURCE_INVALID", "the window does not start at the registered anchor session")
        vol = spy["volume"].astype(float)
        med = float(vol.iloc[-21:-1].median()) if len(vol) > 21 else float("nan")
        ratio = float(vol.iloc[-1]) / med if med and math.isfinite(med) else float("nan")
        if not (ratio >= MIN_SPY_VOLUME_RATIO):
            raise SourceUnavailable("SOURCE_PENDING", f"SPY volume on {day} is {ratio:.2f}x its 20-session median: "
                                    "the daily bar looks incomplete")
        stocks = [s for s, e in ((e["symbol"], e) for e in dq["symbols"]) if e["role"] == "candidate_stock"
                  and e["status"] == "OK"]
        with_t = sum(1 for s in stocks if t in usable[s].index)
        cover = with_t / len(stocks) if stocks else 0.0
        if cover < MIN_BAR_T_COVERAGE:
            raise SourceUnavailable("SOURCE_PENDING", f"only {with_t}/{len(stocks)} usable stocks have a {day} bar")
        bars = dict(usable)
        meta = {
            "data_class": self.data_class, "provider": self.provider, "feed": dq["feed"], "adjustment": dq["adjustment"],
            "timeframe": "1Day", "fetched_at": dq["fetched_at"], "provider_requests": dq["provider_requests_total"],
            "history_hash": dq["history_hash"], "dataset_hash": data_hash(bars),
            "window": {"start": WINDOW_ANCHOR.isoformat(), "end": day.isoformat(), "sessions": len(spy)},
            "decision_bar": "post-close bar of t, preliminary (revisions are reported at maturity)",
            "spy_volume_ratio_t": round(ratio, 3), "bar_t_coverage": round(cover, 4),
            "counts": dq["counts"], "corporate_actions": {k: dq["corporate_actions"].get(k) for k in
                                                          ("status", "records", "split_adjustment_suspect")},
            "excluded_symbols": {e["symbol"]: e["status"] for e in dq["symbols"] if e["status"] != "OK"},
            "entitlement": "Alpaca Basic: historical SIP older than 15 minutes" if self.data_class == "REAL_MARKET_DATA"
            else "none (synthetic fixture)",
        }
        return DecisionData(day, bars, meta)

    def outcome_data(self, start: date, end: date, now: datetime, symbols: tuple[str, ...] | None = None) -> OutcomeBars:
        syms = symbols or u1_symbols()
        loaded = self._fetch(syms, start, end, now)
        if loaded.actions is None:
            raise SourceUnavailable("SOURCE_INVALID", f"corporate actions unavailable ({loaded.actions_error}): the "
                                    "protocol's quarantine cannot run, so no outcome is labelled")
        bars: dict[str, pd.DataFrame] = {}
        for sym, raw in loaded.result.bars.items():
            if raw is None or raw.empty:
                continue
            df = raw[(raw.index >= pd.Timestamp(start)) & (raw.index <= pd.Timestamp(end))]
            bars[sym] = df[~_impossible(df)] if len(df) else df
        sessions = pd.DatetimeIndex([pd.Timestamp(d) for d in self.cal.sessions_between(start, end)])
        spy = bars.get(BENCHMARK)
        if spy is None or list(spy.index) != list(sessions):
            raise SourceUnavailable("SOURCE_INVALID", "SPY is incomplete over the outcome window")
        meta = {"data_class": self.data_class, "provider": self.provider, "fetched_at": loaded.fetched_at,
                "provider_requests": loaded.requests_total, "data_end": end.isoformat(),
                "finality_lag_sessions": PROTOCOL_V2["data"]["finality_lag_sessions"],
                "corporate_action_records": len(loaded.actions), "history_hash": loaded.result.history_hash}
        return OutcomeBars(sessions, bars, window_quarantine(loaded.actions, bars), meta)

    @staticmethod
    def s5_symbols() -> tuple[str, ...]:
        return (BENCHMARK, *sorted(SECTOR_ETFS.values()))


def bars_through(frames: dict[str, pd.DataFrame], cal: MarketCalendar, now: datetime,
                 delay: timedelta = timedelta(minutes=16)) -> dict[str, pd.DataFrame]:
    """Fixture helper: the sessions a provider could have served at `now` (a daily bar exists once its session closed
    and the 15-minute SIP restriction passed). Used by tests and the DEMO smoke run; never market data."""
    last = None
    for d in reversed(cal.sessions_between(date(2016, 1, 1), min(cal.session_date_for(now), cal.last_day))):
        if cal.session(d).close_utc + delay <= now:
            last = d
            break
    if last is None:
        return {s: df.iloc[:0] for s, df in frames.items()}
    return {s: df[df.index <= pd.Timestamp(last)] for s, df in frames.items()}


def drop_session(frames: dict[str, pd.DataFrame], day: date, symbols: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Fixture helper: remove one session's bar (provider outage or late publication)."""
    ts = pd.Timestamp(day)
    return {s: (df[df.index != ts] if symbols is None or s in symbols else df) for s, df in frames.items()}


def scale_volume(frames: dict[str, pd.DataFrame], day: date, symbol: str, factor: float) -> dict[str, pd.DataFrame]:
    out = dict(frames)
    df = out[symbol].copy()
    ts = pd.Timestamp(day)
    if ts in df.index:
        df.loc[ts, "volume"] = float(df.loc[ts, "volume"]) * factor
    out[symbol] = df
    return out

