"""Protocol-2 data: bounded fresh SIP download of U1 + references, finality lag, corporate actions, quality inventory.

Same guarantees as protocol-1 (research.realeq.data): fresh ephemeral fetch, SIP feed and split adjustment enforced,
no synthetic fallback, abort rules, per-symbol content hashes (never prices). Differences, all fixed in PROTOCOL_V2:
* fixed first session 2016-01-04 and a finality lag: the last data session is 2 sessions before the latest completed
  session, so late prints and corrections have settled (M0 found vendor revisions within a day of the close);
* corporate actions are fetched for EVERY symbol and page (forward/reverse splits, spin-offs) and drive the quarantine
  of trade windows across a spin-off ex-date, plus the registered known events (HON 2026-06-29, EXC 2022-02-02).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from apex_worker.market.calendar import MarketCalendar, default_calendar
from apex_worker.market.history import HistoryResult, HistorySpec, sync_history
from apex_worker.research.realeq.data import (
    INCEPTION_NOTES,
    STATUS_INSUFFICIENT,
    STATUS_SOURCE_INVALID,
    InspectingAdapter,
    RawStats,
    RunStop,
    content_hashes,
)
from apex_worker.research.realeq.protocol_v2 import PROTOCOL_V2
from apex_worker.research.realeq.universe_v2 import dropped_share_classes, u1_constituents, u1_symbols
from apex_worker.research.training_data import BENCHMARK, SECTOR_ETFS

FIRST = date.fromisoformat(PROTOCOL_V2["data"]["first_session"])
BIAS_WARNINGS_V2 = (
    "SURVIVORSHIP: U1 = current S&P 500 constituents (snapshot 2026-10-22); members that left the index are absent. "
    "Eligibility starts at each member's date_added, which removes later additions from earlier cross-sections but "
    "not the survivor effect; paired same-date controls from the same member set partly cancel it",
    "SECTOR LABELS: current GICS sectors applied to all history (the 2018 communication services reclassification)",
    "EARNINGS UNKNOWN: no point-in-time earnings calendar; S4 is NOT_EVALUABLE and earnings vetoes are not modelled",
    "PRICE-ONLY RETURNS: split-adjusted bars, dividends not modelled (affects absolute, much less paired, returns)",
    "DAILY BARS: intraday order is unknown; ambiguous outcomes carry pessimistic and optimistic legs (exec-v2.0)",
    "PRESAMPLE IS EARLIER, NOT LATER: the evaluation window precedes the design data (backward-time historical "
    "robustness, not forward out-of-sample); forward evidence is still required",
    "PRESAMPLE EXPOSURE: the cancelled A0 run 39829472789 computed and published PRESAMPLE outcomes (job log, job "
    "summary, artifact). The analyst never read them and no design choice responded to them, but the window is not "
    "untouched; read every PRESAMPLE result with that in mind (PROTOCOL_V2.md section 12)",
    "SURVIVORSHIP-LIMITED UNIVERSE: U1 is not an unbiased all-market sample; results do not generalise to delisted or "
    "smaller stocks",
)


def data_end(cal: MarketCalendar, now: datetime, time_end: date | None = None) -> date:
    latest = cal.latest_completed_session(now)
    end = latest
    for _ in range(PROTOCOL_V2["data"]["finality_lag_sessions"]):
        end = cal.previous_session(end)
    return min(end, time_end) if time_end else end


def spec_v2(max_requests: int | None = None) -> HistorySpec:
    years = 11  # covers 2016-01-04 from any end date before 2027; bars are trimmed to the first session afterwards
    return HistorySpec(symbols=u1_symbols(), years=years, feed="sip", adjustment="split",
                       max_requests=max_requests or PROTOCOL_V2["data"]["max_requests"], max_symbols=600)


@dataclass
class LoadedV2:
    result: HistoryResult
    inspector: InspectingAdapter
    actions: list[dict] | None
    end: date
    fetched_at: str
    actions_error: str | None = None


def fetch_v2(adapter_factory: Callable[[], object], cache_dir: Path, spec: HistorySpec, *, end: date,
             now: datetime) -> LoadedV2:
    from apex_worker.market.adapters.alpaca import AlpacaError

    async def run():
        insp = InspectingAdapter(adapter_factory(), spec.feed, spec.adjustment)
        try:
            res = await sync_history(insp, cache_dir, spec, end=end, now=now)
            actions, err = None, None
            fn = getattr(insp.inner, "get_corporate_actions_paged", None)
            if fn is None:
                err = "adapter has no corporate-actions endpoint"
            else:
                try:
                    actions = await fn(list(spec.symbols), FIRST, end, ",".join(PROTOCOL_V2["data"]["corporate_action_types"]))
                    if actions is None:
                        err = "endpoint unsupported for this plan/window"
                except (AlpacaError, OSError, ValueError, TypeError) as exc:
                    err = f"{type(exc).__name__}: {str(exc)[:120]}"
            return res, insp, actions, err
        finally:
            await insp.aclose()

    try:
        res, insp, actions, err = asyncio.run(run())
    except AlpacaError as exc:
        status = getattr(exc, "status", 0)
        why = (f"Alpaca rejected the credentials or the plan lacks historical SIP entitlement (HTTP {status})"
               if status in (401, 403) else f"Alpaca request failed (HTTP {status}): {str(exc)[:160]}")
        raise RunStop(STATUS_SOURCE_INVALID, why) from None
    except OSError as exc:
        raise RunStop(STATUS_SOURCE_INVALID, f"history cache/network error: {type(exc).__name__}") from None
    return LoadedV2(res, insp, actions, end, now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), err)


def _ex_date(a: dict) -> str | None:
    d = a.get("ex_date") or a.get("process_date") or a.get("date")
    return str(d)[:10] if d else None


def quarantine_dates(actions: list[dict] | None) -> dict[str, list[pd.Timestamp]]:
    """Symbol -> ex-dates whose trade windows are excluded: spin-off parents plus the registered known events."""
    out: dict[str, set[str]] = {s: set(v) for s, v in PROTOCOL_V2["quarantine"]["known_events"].items()}
    for a in actions or []:
        if "spin" in str(a.get("type", "")):
            sym = a.get("source_symbol") or a.get("symbol")
            d = _ex_date(a)
            if sym and d:
                out.setdefault(str(sym), set()).add(d)
    return {s: sorted(pd.Timestamp(d) for d in v) for s, v in out.items()}


def roles_v2() -> dict[str, tuple[str, str | None]]:
    roles: dict[str, tuple[str, str | None]] = {BENCHMARK: ("benchmark", None)}
    for sector, etf in SECTOR_ETFS.items():
        roles[etf] = ("sector_etf", sector)
    for c in u1_constituents():
        roles[c.symbol] = ("candidate_stock", c.sector)
    return roles


def quality_inventory_v2(loaded: LoadedV2, cal: MarketCalendar | None = None) -> tuple[dict, dict[str, pd.DataFrame]]:
    cal = cal or default_calendar()
    rules, excl = PROTOCOL_V2["abort_rules"], PROTOCOL_V2["symbol_exclusion"]
    res, insp = loaded.result, loaded.inspector
    roles = roles_v2()
    expected_last = cal.previous_session(loaded.end) if not cal.is_session(loaded.end) else loaded.end
    split_by: dict[str, list[str]] = {}
    spin_by: dict[str, list[str]] = {}
    for a in loaded.actions or []:
        d, kind = _ex_date(a), str(a.get("type", ""))
        sym = a.get("source_symbol") or a.get("symbol")
        if sym and d:
            (spin_by if "spin" in kind else split_by).setdefault(str(sym), []).append(d)
    symbols, usable = [], {}
    future = total_raw = total_invalid = 0
    for sym in u1_symbols():
        role, sector = roles[sym]
        raw = insp.raw.get(sym, RawStats())
        total_raw += raw.rows
        total_invalid += raw.invalid_rows
        if raw.last and raw.last > expected_last.isoformat():
            future += 1
        df = res.bars.get(sym)
        if df is not None and not df.empty:
            df = df[(df.index >= pd.Timestamp(FIRST)) & (df.index <= pd.Timestamp(loaded.end))]
        entry: dict = {"symbol": sym, "role": role, "sector": sector, "raw_rows": raw.rows,
                       "raw_invalid_rows_dropped": raw.invalid_rows, "raw_duplicate_rows": raw.duplicate_rows,
                       "zero_volume_sessions": raw.zero_volume_rows, "notes": []}
        if sym in INCEPTION_NOTES:
            entry["notes"].append(INCEPTION_NOTES[sym])
        if df is None or df.empty:
            entry.update(status="MISSING", sessions=0)
            symbols.append(entry)
            continue
        days = [d.date() for d in pd.DatetimeIndex(df.index)]
        expected = cal.sessions_between(days[0], days[-1])
        present = set(days)
        missing = [d for d in expected if d not in present]
        off_cal = [d for d in days if not cal.is_session(d)]
        c = df["close"].to_numpy(float)
        rets = c[1:] / c[:-1] - 1
        big = [str(days[i + 1]) for i in np.flatnonzero(np.abs(rets) > excl["large_move_flag_abs_return"])]
        stale = len(cal.sessions_between(days[-1], expected_last)) - 1
        entry.update(sessions=len(days), first_session=str(days[0]), last_session=str(days[-1]),
                     missing_sessions=len(missing), off_calendar_rows=len(off_cal), stale_sessions=max(0, stale),
                     large_move_sessions=big[:20], split_dates=sorted(set(split_by.get(sym, []))),
                     spin_off_ex_dates=sorted(set(spin_by.get(sym, []))))
        status = "OK"
        if len(days) < excl["min_sessions"]:
            status = "EXCLUDED_INCOMPLETE_HISTORY"
        elif stale > excl["max_stale_sessions"]:
            status = "EXCLUDED_STALE"
        elif len(expected) and len(missing) / len(expected) > excl["max_missing_session_fraction"]:
            status = "EXCLUDED_MISSING_SESSIONS"
        elif off_cal:
            status = "EXCLUDED_OFF_CALENDAR_ROWS"
        if big:
            entry["notes"].append(f"{len(big)} close-to-close moves above 25% flagged for review (not excluded)")
        entry["status"] = status
        entry["content_hash"] = content_hashes(df)
        if status == "OK":
            usable[sym] = df
        symbols.append(entry)
    stocks_ok = sum(1 for e in symbols if e["role"] == "candidate_stock" and e["status"] == "OK")
    bench = next(e for e in symbols if e["symbol"] == BENCHMARK)
    q = quarantine_dates(loaded.actions)
    body = {
        "schema": "apex-realeq-data-quality-2", "protocol": PROTOCOL_V2["version"], "provider": "alpaca",
        "feed": insp.feed, "adjustment": insp.adjustment, "fetched_at": loaded.fetched_at,
        "requested_window": {"start": str(res.start), "end": str(res.end)}, "first_session_used": str(FIRST),
        "data_end_after_finality_lag": str(loaded.end), "provider_requests": res.requests,
        "provider_calls": len(insp.calls), "history_hash": res.history_hash, "budget_exhausted": res.budget_exhausted,
        "raw_rows": total_raw, "raw_invalid_rows": total_invalid,
        "raw_invalid_fraction": round(total_invalid / total_raw, 6) if total_raw else None,
        "symbols_with_future_bars": future,
        "corporate_actions": {"status": "available" if loaded.actions is not None else
                              f"UNAVAILABLE ({loaded.actions_error or 'endpoint/plan'})",
                              "records": None if loaded.actions is None else len(loaded.actions),
                              "spin_off_parents": sorted(s for s, v in spin_by.items() if v)},
        "quarantine": {s: [str(d.date()) for d in v] for s, v in sorted(q.items())},
        "universe": {"snapshot": PROTOCOL_V2["universe"]["snapshot"], "requested_stocks": len(u1_constituents()),
                     "share_classes_dropped": dropped_share_classes()},
        "counts": {"requested": len(u1_symbols()), "with_data": sum(1 for e in symbols if e.get("sessions")),
                   "usable": len(usable), "candidate_stocks_usable": stocks_ok,
                   "sector_etfs_usable": sum(1 for e in symbols if e["role"] == "sector_etf" and e["status"] == "OK"),
                   "status": {k: sum(1 for e in symbols if e["status"] == k) for k in sorted({e["status"] for e in symbols})}},
        "symbols": symbols, "bias_warnings": list(BIAS_WARNINGS_V2), "abort": None,
    }
    abort = None
    first_bench = pd.Timestamp(bench.get("first_session") or "2100-01-01")
    if future:
        abort = (STATUS_SOURCE_INVALID, f"{future} symbol(s) returned bars dated after the latest completed session")
    elif total_raw and total_invalid / total_raw > rules["invalid_raw_row_fraction_max"]:
        abort = (STATUS_SOURCE_INVALID, f"invalid raw rows {total_invalid}/{total_raw} exceed the protocol maximum")
    elif res.budget_exhausted:
        abort = (STATUS_INSUFFICIENT, "provider request budget exhausted before the universe was downloaded")
    elif bench["status"] != "OK" or first_bench > pd.Timestamp(rules["benchmark_must_start_by"]):
        abort = (STATUS_INSUFFICIENT, f"benchmark {BENCHMARK} missing or starting after {rules['benchmark_must_start_by']}")
    elif stocks_ok < rules["min_usable_stocks"]:
        abort = (STATUS_INSUFFICIENT, f"only {stocks_ok} usable U1 stocks (< {rules['min_usable_stocks']})")
    if abort:
        body["abort"] = {"status": abort[0], "reason": abort[1]}
        err = RunStop(*abort)
        err.body = body  # type: ignore[attr-defined]
        raise err
    return body, usable
