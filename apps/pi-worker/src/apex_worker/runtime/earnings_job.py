"""Nightly SEC earnings calendar for the live worker (APEX-011: until now every live candidate had UNKNOWN earnings).

For each universe stock with an SEC CIK, one request (the company's submissions, `filings.recent`) gives its 8-K item
2.02 history; `catalysts.earnings` turns that into reported releases plus a next-release estimate with an uncertainty
window. The candidate engine's earnings gate then vetoes a setup whose holding window overlaps a release window
(WITHIN_HORIZON). Everything else stays UNKNOWN: an estimate never confirms "no earnings", only a confirmed date could.

* CIKs come from the S&P 500 snapshot (research/realeq/universe_sp500_2026_10_22.json), then the SEC's current
  company_tickers.json for the rest. A stock without a CIK stays UNKNOWN.
* About one request per stock at 5 a second (the SEC allows 10), run off the event loop once a night.
* A stock whose request fails keeps its previous entries; nothing is ever inferred from a failure.
* Without SEC_USER_AGENT nothing is fetched and every status stays UNKNOWN, as before.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path

from apex_worker.catalysts.earnings import (
    EarningsCalendar,
    EarningsEntry,
    EntryStatus,
    ReleaseTiming,
    earnings_status,
    estimate_next_from_8k,
    reported_from_8k,
)
from apex_worker.catalysts.http import FetchError, PoliteHttpClient
from apex_worker.catalysts.sec import COMPANY_TICKERS_URL
from apex_worker.catalysts.tickers import TickerMap, normalize_cik
from apex_worker.market.calendar import MarketCalendar
from apex_worker.research.realeq.data import RunStop
from apex_worker.research.realeq.universe_v2 import snapshot
from apex_worker.research.sec_history import company_filings
from apex_worker.timeutil import iso_utc, parse_utc

EARNINGS_FILE = "earnings.json"
HISTORY_DAYS = 400  # five quarters, enough for the year-over-year anchor; `filings.recent` holds at least a year
KEEP_REPORTED = 6


def snapshot_ciks() -> dict[str, str]:
    """S&P 500 symbol -> CIK from the pinned snapshot, every share class included (no request)."""
    return {sym: normalize_cik(cik) for sym, _sector, _added, cik, _name in snapshot()["rows"] if cik}


def resolve_ciks(symbols: Iterable[str], http: PoliteHttpClient | None) -> dict[str, str]:
    """CIKs for `symbols`: the snapshot first, then one company_tickers.json request for the rest (if any)."""
    known = snapshot_ciks()
    out = {s: known[s] for s in symbols if s in known}
    rest = [s for s in symbols if s not in out]
    if rest and http is not None:
        try:
            tmap = TickerMap.load(http.get(COMPANY_TICKERS_URL, accept="application/json").content)
        except (FetchError, ValueError):  # TickerMapError is a ValueError
            return out
        out.update({s: c for s in rest if (c := tmap.cik_for(s)) is not None})
    return out


def _entry_json(e: EarningsEntry) -> dict:
    return {"day": e.day.isoformat(), "status": e.status.value, "source": e.source, "timing": e.timing.value,
            "uncertainty_days": e.uncertainty_days, "known_since": iso_utc(e.known_since) if e.known_since else None,
            "note": e.note}


def _entry(symbol: str, d: Mapping) -> EarningsEntry:
    return EarningsEntry(symbol=symbol, day=date.fromisoformat(d["day"]), status=EntryStatus(d["status"]),
                         source=str(d["source"]), timing=ReleaseTiming(d.get("timing", "UNKNOWN")),
                         uncertainty_days=int(d.get("uncertainty_days") or 0),
                         known_since=parse_utc(d["known_since"]) if d.get("known_since") else None, note=d.get("note"))


def refresh_earnings(symbols: Iterable[str], ciks: Mapping[str, str], http: PoliteHttpClient, cal: MarketCalendar,
                     now: datetime, previous: Mapping | None = None, as_of: datetime | None = None) -> dict:
    """One submissions request per stock with a CIK; returns the JSON document `save_earnings` writes.

    `as_of` (the picks lane): only filings EDGAR accepted by then count, so the releases and the next-release estimate
    are the ones known at that instant (a release filed a minute later leaves the earlier estimate in place)."""
    prev = (previous or {}).get("symbols") or {}
    start, end = now.date() - timedelta(days=HISTORY_DAYS), now.date()
    by_symbol: dict[str, dict] = {}
    failed, no_cik = [], []
    for sym in sorted(set(symbols)):
        cik = ciks.get(sym)
        if not cik:
            no_cik.append(sym)
            continue
        try:
            filings, _n = company_filings(http, cik, start, end)
        except RunStop:
            failed.append(sym)
            if sym in prev:
                by_symbol[sym] = prev[sym]
            continue
        if as_of is not None:
            filings = [f for f in filings if f.published_at <= as_of]
        reported = reported_from_8k(sym, filings, cal)
        estimate = estimate_next_from_8k(sym, filings, cal=cal)
        by_symbol[sym] = {"reported": [_entry_json(e) for e in reported[-KEEP_REPORTED:]],
                          "estimate": _entry_json(estimate) if estimate else None}
    return {"generated_at": iso_utc(now), "symbols": by_symbol, "failed": failed, "no_cik": no_cik}


def save_earnings(state_dir: Path, data: Mapping) -> Path:
    path = state_dir / EARNINGS_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True))
    os.replace(tmp, path)
    return path


def load_earnings(state_dir: Path) -> dict:
    try:
        data = json.loads((state_dir / EARNINGS_FILE).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class EarningsBook:
    """The calendar the candidate engine asks (`status`); replaced in place when the nightly refresh lands."""

    def __init__(self, cal: MarketCalendar, data: Mapping | None = None, clock=None):
        self.cal, self.clock = cal, clock
        self.data: dict = {}
        self.calendar: EarningsCalendar | None = None
        if data:
            self.replace(data)

    def replace(self, data: Mapping) -> None:
        entries = []
        for sym, row in (data.get("symbols") or {}).items():
            entries += [_entry(sym, e) for e in row.get("reported") or []]
            if row.get("estimate"):
                entries.append(_entry(sym, row["estimate"]))
        self.data, self.calendar = dict(data), EarningsCalendar(entries)

    def status(self, symbol: str, session_day: str, horizon_sessions: int) -> str:
        """`signals.candidates` callback: WITHIN_HORIZON | NONE_WITHIN_HORIZON_CONFIRMED | UNKNOWN."""
        as_of = self.clock() if self.clock else None
        return earnings_status(symbol, date.fromisoformat(session_day), horizon_sessions, self.calendar,
                               market_calendar=self.cal, as_of=as_of)

    def coverage(self) -> dict:
        rows = self.data.get("symbols") or {}
        return {"generated_at": self.data.get("generated_at"), "symbols": len(rows),
                "with_estimate": sum(1 for r in rows.values() if r.get("estimate")),
                "failed": len(self.data.get("failed") or []), "no_cik": len(self.data.get("no_cik") or [])}
