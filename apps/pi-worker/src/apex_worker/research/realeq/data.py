"""Real-history acquisition and the data-quality / bias inventory (Phase 14 section B, before any strategy result).

* Credentials come ONLY from ALPACA_KEY_ID / ALPACA_SECRET_KEY (the workflow maps the `real-equities-research`
  environment secrets onto them). Missing => BLOCKED_ALPACA_ACTIONS_SECRETS_MISSING. There is no synthetic fallback.
* Every provider response is inspected BEFORE normalisation (raw duplicates, invalid rows, zero volume, future dates),
  so the report counts what the vendor actually sent, not what survived cleaning.
* Bars live only in a temporary cache directory for the lifetime of the run; nothing here writes OHLCV to the outputs.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from apex_worker.market.calendar import MarketCalendar, default_calendar
from apex_worker.market.history import HistoryResult, HistorySpec, encode_bars, sync_history
from apex_worker.research.realeq.protocol import PROTOCOL
from apex_worker.research.training_data import BENCHMARK, BIAS_WARNINGS, MEMBERS, SECTOR_ETFS, universe_symbols

STATUS_COMPLETED = "COMPLETED"
STATUS_BLOCKED = "BLOCKED_ALPACA_ACTIONS_SECRETS_MISSING"
STATUS_SOURCE_INVALID = "SOURCE_INVALID"
STATUS_INSUFFICIENT = "INSUFFICIENT_HISTORY"
BLOCKED_BANNER = "BLOCKED — ALPACA_ACTIONS_SECRETS_MISSING"

# public facts used for the partial-history notes (fund inception), not provider data
INCEPTION_NOTES = {
    "XLC": "Communication Services SPDR launched 2018-06-19; the GICS sector itself was created 2018-09-24",
    "XLRE": "Real Estate SPDR launched 2015-10-08",
}


class RunStop(Exception):
    """A run-level stop with a protocol status. The message never contains credential values."""

    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def credentials_from_env(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    env = os.environ if env is None else env
    key = (env.get("ALPACA_KEY_ID") or "").strip()
    secret = (env.get("ALPACA_SECRET_KEY") or "").strip()
    missing = [n for n, v in (("ALPACA_KEY_ID", key), ("ALPACA_SECRET_KEY", secret)) if not v]
    if missing:
        raise RunStop(
            STATUS_BLOCKED,
            f"{BLOCKED_BANNER}: {', '.join(missing)} not set. Configure the `real-equities-research` environment "
            "secrets ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY (see research-results/real-equities/PROTOCOL.md).",
        )
    return key, secret


def symbol_roles() -> dict[str, tuple[str, str | None]]:
    roles: dict[str, tuple[str, str | None]] = {BENCHMARK: ("benchmark", None)}
    for sector, etf in SECTOR_ETFS.items():
        roles[etf] = ("sector_etf", sector)
        for s in MEMBERS[sector]:
            roles[s] = ("candidate_stock", sector)
    return roles


@dataclass
class RawStats:
    rows: int = 0
    duplicate_rows: int = 0
    invalid_rows: int = 0
    zero_volume_rows: int = 0
    first: str | None = None
    last: str | None = None


@dataclass
class InspectingAdapter:
    """Wraps the Alpaca adapter: enforces the protocol feed/adjustment and records raw response statistics."""

    inner: object
    feed: str = "sip"
    adjustment: str = "split"
    raw: dict[str, RawStats] = field(default_factory=dict)
    calls: list[dict] = field(default_factory=list)

    @property
    def requests(self) -> int:
        return int(getattr(self.inner, "requests", 0))

    async def get_daily_bars(self, symbols, start, end, adjustment="split", feed="sip"):
        if feed != self.feed or adjustment != self.adjustment:
            raise RunStop(
                STATUS_SOURCE_INVALID,
                f"protocol requires feed={self.feed} adjustment={self.adjustment}; got feed={feed} adjustment={adjustment}",
            )
        out = await self.inner.get_daily_bars(list(symbols), start, end, adjustment=adjustment, feed=feed)
        self.calls.append({"symbols": len(symbols), "start": str(start), "end": str(end), "feed": feed,
                           "adjustment": adjustment, "returned_symbols": len(out)})
        for sym, df in out.items():
            st = self.raw.setdefault(sym, RawStats())
            if df is None or df.empty:
                continue
            idx = pd.DatetimeIndex(pd.to_datetime(df.index)).normalize()
            px = df[["open", "high", "low", "close"]].astype(float).to_numpy()
            tol = 1e-9 * np.maximum(1.0, np.abs(px[:, 3]))
            bad = (
                ~np.isfinite(px).all(axis=1)
                | (px[:, 2] > px[:, 1] + tol)
                | (px[:, 3] <= 0)
                | (px[:, 0] > px[:, 1] + tol)
                | (px[:, 0] < px[:, 2] - tol)
                | (px[:, 3] > px[:, 1] + tol)
                | (px[:, 3] < px[:, 2] - tol)
            )
            st.rows += len(df)
            st.duplicate_rows += int(idx.duplicated().sum())
            st.invalid_rows += int(bad.sum())
            st.zero_volume_rows += int((df["volume"].astype(float) <= 0).sum())
            lo, hi = str(idx.min().date()), str(idx.max().date())
            st.first = min(st.first or lo, lo)
            st.last = max(st.last or hi, hi)
        return out

    async def get_corporate_actions(self, symbols, start, end):
        fn = getattr(self.inner, "get_corporate_actions", None)
        return await fn(symbols, start, end, types="forward_split,reverse_split") if fn else None

    async def aclose(self) -> None:
        close = getattr(self.inner, "aclose", None)
        if close:
            await close()


@dataclass
class Loaded:
    result: HistoryResult
    inspector: InspectingAdapter
    splits: list[dict] | None  # None = corporate actions unavailable for this plan/endpoint
    end: date
    fetched_at: str
    splits_error: str | None = None


def fetch(
    adapter_factory: Callable[[], object],
    cache_dir: Path,
    spec: HistorySpec,
    *,
    end: date,
    now: datetime,
) -> Loaded:
    """Fresh bounded download (ephemeral cache dir => every symbol is a full, inspected fetch)."""
    from apex_worker.market.adapters.alpaca import AlpacaError

    if spec.feed != PROTOCOL["data"]["feed"] or spec.adjustment != PROTOCOL["data"]["adjustment"]:
        raise RunStop(STATUS_SOURCE_INVALID, f"protocol requires feed=sip adjustment=split, spec has {spec.feed}/{spec.adjustment}")

    async def run():
        insp = InspectingAdapter(adapter_factory(), spec.feed, spec.adjustment)
        try:
            res = await sync_history(insp, cache_dir, spec, end=end, now=now)
            splits, err = None, None
            try:
                start = end - timedelta(days=round(365.25 * spec.years))
                splits = await insp.get_corporate_actions(list(spec.symbols), start, end)
            except (AlpacaError, OSError, ValueError, TypeError) as exc:
                err = f"{type(exc).__name__}: {str(exc)[:120]}"
            return res, insp, splits, err
        finally:
            await insp.aclose()

    try:
        res, insp, splits, err = asyncio.run(run())
    except AlpacaError as exc:
        status = getattr(exc, "status", 0)
        if status in (401, 403):
            why = f"Alpaca rejected the credentials or the plan lacks historical SIP entitlement (HTTP {status})"
        elif status == 429:
            why = "Alpaca rate limit persisted after bounded retries (HTTP 429)"
        else:
            why = f"Alpaca request failed (HTTP {status}): {str(exc)[:160]}"
        raise RunStop(STATUS_SOURCE_INVALID, why) from None
    except OSError as exc:
        raise RunStop(STATUS_SOURCE_INVALID, f"history cache/network error: {type(exc).__name__}") from None
    return Loaded(res, insp, splits, end, now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), err)


def default_spec(years: int, max_requests: int = 300) -> HistorySpec:
    return HistorySpec(symbols=universe_symbols(), years=years, feed="sip", adjustment="split",
                       max_requests=max_requests)


def content_hashes(df: pd.DataFrame) -> dict:
    """sha256 of the deterministic cache encoding (QV2 M0): whole series plus one 16-hex digest per calendar year, so a
    replay whose dataset hash differs can name the symbol and year that changed without publishing any price."""
    years = pd.DatetimeIndex(df.index).year
    return {
        "all": hashlib.sha256(encode_bars(df)).hexdigest(),
        "by_year": {str(y): hashlib.sha256(encode_bars(df[years == y])).hexdigest()[:16] for y in sorted(set(years))},
        "last_session": hashlib.sha256(encode_bars(df.iloc[-1:])).hexdigest()[:16],
    }


def quality_inventory(loaded: Loaded, cal: MarketCalendar | None = None) -> tuple[dict, dict[str, pd.DataFrame]]:
    """(data_quality.json body, usable bars). Raises RunStop when an abort rule fires (the body is attached)."""
    cal = cal or default_calendar()
    rules, excl = PROTOCOL["abort_rules"], PROTOCOL["symbol_exclusion"]
    res, insp = loaded.result, loaded.inspector
    roles = symbol_roles()
    window_start = res.start
    expected_last = cal.previous_session(loaded.end) if not cal.is_session(loaded.end) else loaded.end
    split_by_sym: dict[str, list[str]] = {}
    for a in loaded.splits or []:
        d = a.get("ex_date") or a.get("process_date") or a.get("date")
        if a.get("symbol") and d:
            split_by_sym.setdefault(str(a["symbol"]), []).append(str(d)[:10])
    symbols: list[dict] = []
    usable: dict[str, pd.DataFrame] = {}
    future_rows = 0
    total_raw = total_invalid = 0
    for sym in universe_symbols():
        role, sector = roles[sym]
        raw = insp.raw.get(sym, RawStats())
        total_raw += raw.rows
        total_invalid += raw.invalid_rows
        if raw.last and raw.last > expected_last.isoformat():
            future_rows += 1
        df = res.bars.get(sym)
        entry: dict = {"symbol": sym, "role": role, "sector": sector, "raw_rows": raw.rows,
                       "raw_duplicate_rows": raw.duplicate_rows, "raw_invalid_rows_dropped": raw.invalid_rows,
                       "zero_volume_sessions": raw.zero_volume_rows, "notes": []}
        if sym in INCEPTION_NOTES:
            entry["notes"].append(INCEPTION_NOTES[sym])
        if df is None or df.empty:
            entry.update(status="MISSING", sessions=0, first_session=None, last_session=None)
            symbols.append(entry)
            continue
        days = [d.date() for d in pd.DatetimeIndex(df.index)]
        first, last = days[0], days[-1]
        expected = cal.sessions_between(first, last)
        present = set(days)
        missing = [d for d in expected if d not in present]
        off_cal = [d for d in days if not cal.is_session(d)]
        c = df["close"].to_numpy(float)
        rets = c[1:] / c[:-1] - 1
        big = [str(days[i + 1]) for i in np.flatnonzero(np.abs(rets) > excl["large_move_flag_abs_return"])]
        stale = len(cal.sessions_between(last, expected_last)) - 1
        split_days = sorted(set(split_by_sym.get(sym, [])))
        continuity = None
        if loaded.splits is not None:
            pos = {str(d): i for i, d in enumerate(days)}
            jumps = [abs(rets[pos[d] - 1]) for d in split_days if d in pos and pos[d] > 0]
            continuity = "suspect" if any(j > excl["large_move_flag_abs_return"] for j in jumps) else "ok"
        entry.update(
            sessions=len(days), first_session=str(first), last_session=str(last),
            expected_sessions_in_span=len(expected), missing_sessions=len(missing),
            missing_session_dates=[str(d) for d in missing[:10]], off_calendar_rows=len(off_cal),
            stale_sessions=max(0, stale), late_start=first > window_start + timedelta(days=10),
            large_move_sessions=big[:20], split_events=None if loaded.splits is None else len(split_days),
            split_dates=split_days, split_adjustment_continuity=continuity,
        )
        status = "OK"
        if len(days) < excl["min_sessions"]:
            status = "EXCLUDED_INCOMPLETE_HISTORY"
        elif stale > excl["max_stale_sessions"]:
            status = "EXCLUDED_STALE"
        elif len(expected) and len(missing) / len(expected) > excl["max_missing_session_fraction"]:
            status = "EXCLUDED_MISSING_SESSIONS"
        elif off_cal:
            status = "EXCLUDED_OFF_CALENDAR_ROWS"
        if continuity == "suspect":
            entry["notes"].append("split ex-date shows a >25% jump in split-adjusted closes: adjustment suspect")
        if big:
            entry["notes"].append(f"{len(big)} close-to-close moves above 25% flagged for review (not excluded)")
        entry["status"] = status
        entry["content_hash"] = content_hashes(df)  # hashes of the encoded bars, never the bars: localises vendor drift
        if status == "OK":
            usable[sym] = df
        symbols.append(entry)
    stocks_ok = sum(1 for e in symbols if e["role"] == "candidate_stock" and e["status"] == "OK")
    bench = next(e for e in symbols if e["symbol"] == BENCHMARK)
    body = {
        "schema": "apex-realeq-data-quality-1",
        "provider": "alpaca",
        "feed": loaded.inspector.feed,
        "adjustment": loaded.inspector.adjustment,
        "fetched_at": loaded.fetched_at,
        "requested_window": {"start": str(res.start), "end": str(res.end)},
        "latest_expected_session": str(expected_last),
        "provider_requests": res.requests,
        "provider_calls": loaded.inspector.calls,
        "history_hash": res.history_hash,
        "budget_exhausted": res.budget_exhausted,
        "raw_rows": total_raw,
        "raw_invalid_rows": total_invalid,
        "raw_invalid_fraction": round(total_invalid / total_raw, 6) if total_raw else None,
        "symbols_with_future_bars": future_rows,
        "corporate_actions": "available" if loaded.splits is not None else f"UNAVAILABLE ({loaded.splits_error or 'endpoint/plan'})",
        "counts": {
            "requested": len(universe_symbols()),
            "with_data": sum(1 for e in symbols if e.get("sessions")),
            "usable": len(usable),
            "candidate_stocks_requested": sum(1 for e in symbols if e["role"] == "candidate_stock"),
            "candidate_stocks_usable": stocks_ok,
            "sector_etfs_usable": sum(1 for e in symbols if e["role"] == "sector_etf" and e["status"] == "OK"),
        },
        "symbols": symbols,
        "bias_warnings": [
            *BIAS_WARNINGS,
            "REFERENCE ASSETS: SPY and the 11 sector SPDRs feed relative-strength/sector features and are never traded",
            "UNIVERSE SIZE: 66 large caps in 11 sectors; results describe this sample, not U.S. equities in general",
            "DAILY BARS: intraday order of high/low is unknown; same-bar target+stop is resolved stop-first",
            "SPIN-OFFS: split-only adjustment leaves spin-off / special distributions as price drops (e.g. T 2022, "
            "GE 2023-2024); a trade held across such an ex-date can book a false loss (large moves are flagged)",
        ],
        "abort": None,
    }
    abort = None
    if future_rows:
        abort = (STATUS_SOURCE_INVALID, f"{future_rows} symbol(s) returned bars dated after the latest completed session")
    elif total_raw and total_invalid / total_raw > rules["invalid_raw_row_fraction_max"]:
        abort = (STATUS_SOURCE_INVALID, f"invalid raw rows {total_invalid}/{total_raw} exceed the protocol maximum")
    elif res.budget_exhausted:
        abort = (STATUS_INSUFFICIENT, "provider request budget exhausted before the universe was downloaded")
    elif bench["status"] != "OK" or (bench.get("sessions") or 0) < rules["benchmark_min_sessions"]:
        abort = (STATUS_INSUFFICIENT, f"benchmark {BENCHMARK} missing or shorter than {rules['benchmark_min_sessions']} sessions")
    elif stocks_ok < rules["min_usable_candidate_stocks"]:
        abort = (STATUS_INSUFFICIENT, f"only {stocks_ok} usable candidate stocks (< {rules['min_usable_candidate_stocks']})")
    if abort:
        body["abort"] = {"status": abort[0], "reason": abort[1]}
        err = RunStop(*abort)
        err.body = body  # type: ignore[attr-defined]
        raise err
    return body, usable
