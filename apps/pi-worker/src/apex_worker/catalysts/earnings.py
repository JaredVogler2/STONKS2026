"""Earnings calendar: SEC 8-K item 2.02 cadence estimates + manually curated entries.

``earnings_status`` NEVER infers "no earnings" from missing data. It returns:

* ``WITHIN_HORIZON``                 - a confirmed, expected or estimated release window overlaps the horizon
* ``NONE_WITHIN_HORIZON_CONFIRMED``  - a *confirmed* next release lies after the horizon, it is the
                                       immediate next quarter (<= 120 days after the last known release)
                                       and nothing (estimate or otherwise) overlaps the horizon
* ``UNKNOWN``                        - everything else, including no data at all

A before-open (or unknown-timing) release on the session right after the horizon end is treated as
inside the horizon: a position held to the final close would still carry that overnight gap.

Point in time: a release accepted after the regular close (16:00 ET, or an early close) is usable only
for the NEXT session's decisions, never for the same session's close (see ``first_usable_session``).
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import Any

from apex_worker.catalysts.models import (
    AvailabilityMode,
    CatalystRecord,
    CatalystType,
    Direction,
    LegalClassification,
    Source,
    SourceGrade,
    classify_actionable_age,
    et_date_start,
    make_catalyst_id,
    metadata_hash,
    resolve_available_at,
)
from apex_worker.catalysts.sec import FilingMeta
from apex_worker.catalysts.tickers import normalize_symbol
from apex_worker.market.calendar import MarketCalendar, default_calendar
from apex_worker.timeutil import ET, ensure_utc, parse_utc

PARSER_VERSION = "earnings/1.0.0"
MIN_HISTORY_FOR_ESTIMATE = 3
MAX_NEXT_QUARTER_GAP_DAYS = 120
YOY_ANCHOR_DAYS = 364
DEFAULT_ESTIMATE_UNCERTAINTY_DAYS = 10


class EarningsStatus(StrEnum):
    NONE_WITHIN_HORIZON_CONFIRMED = "NONE_WITHIN_HORIZON_CONFIRMED"
    WITHIN_HORIZON = "WITHIN_HORIZON"
    UNKNOWN = "UNKNOWN"


class EntryStatus(StrEnum):
    CONFIRMED = "confirmed"
    EXPECTED = "expected"
    REPORTED = "reported"


class ReleaseTiming(StrEnum):
    BMO = "BMO"  # before market open
    DURING = "DURING"
    AMC = "AMC"  # after market close
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class EarningsEntry:
    symbol: str
    day: date
    status: EntryStatus
    source: str  # manual | sec_8k_cadence | sec_8k_item_2_02
    timing: ReleaseTiming = ReleaseTiming.UNKNOWN
    uncertainty_days: int = 0
    source_url: str | None = None
    known_since: datetime | None = None
    note: str | None = None

    @property
    def window(self) -> tuple[date, date]:
        delta = timedelta(days=self.uncertainty_days)
        return self.day - delta, self.day + delta


@dataclass(frozen=True)
class EarningsStatusDetail:
    status: EarningsStatus
    reason_codes: tuple[str, ...]
    horizon_start: date
    horizon_end: date | None
    entries: tuple[EarningsEntry, ...] = ()


class EarningsCalendar:
    def __init__(self, entries: Iterable[EarningsEntry] = ()):
        self._by_symbol: dict[str, list[EarningsEntry]] = {}
        for e in entries:
            self.add(e)

    def add(self, entry: EarningsEntry) -> None:
        sym = normalize_symbol(entry.symbol)
        if sym is None:
            raise ValueError(f"invalid symbol {entry.symbol!r}")
        self._by_symbol.setdefault(sym, []).append(entry)

    def entries_for(self, symbol: str, as_of: datetime | None = None) -> list[EarningsEntry]:
        """Entries known at ``as_of`` (entries without a known_since are excluded from as-of queries)."""
        items = self._by_symbol.get(normalize_symbol(symbol) or "", [])
        if as_of is not None:
            as_of = ensure_utc(as_of)
            items = [e for e in items if e.known_since is not None and e.known_since <= as_of]
        return sorted(items, key=lambda e: e.day)

    @classmethod
    def from_manual_json(cls, data: Mapping[str, Any] | bytes | str) -> EarningsCalendar:
        raw = json.loads(data) if isinstance(data, bytes | str) else data
        cal = cls()
        for r in raw["entries"]:
            url = r.get("source_url")
            if url is not None and not str(url).startswith("https://"):
                raise ValueError("manual earnings source_url must be https")
            status = EntryStatus(r["status"])
            cal.add(
                EarningsEntry(
                    symbol=r["symbol"],
                    day=date.fromisoformat(r["date"]),
                    status=status,
                    source="manual",
                    timing=ReleaseTiming(r.get("timing", "UNKNOWN")),
                    uncertainty_days=int(
                        r.get("uncertainty_days", 0 if status is EntryStatus.CONFIRMED else 7)
                    ),
                    source_url=url,
                    known_since=parse_utc(r["known_since"]) if r.get("known_since") else None,
                    note=r.get("note"),
                )
            )
        return cal


# ---------------------------------------------------------------------------------------------
# SEC 8-K item 2.02 history
# ---------------------------------------------------------------------------------------------
def _is_earnings_8k(f: FilingMeta) -> bool:
    return f.base_form == "8-K" and "2.02" in f.items


def _timing_for(instant: datetime, cal: MarketCalendar) -> ReleaseTiming:
    local_day = instant.astimezone(ET).date()
    if not cal.is_session(local_day):
        return ReleaseTiming.UNKNOWN
    sess = cal.session(local_day)
    if instant < sess.open_utc:
        return ReleaseTiming.BMO
    if instant < sess.close_utc:
        return ReleaseTiming.DURING
    return ReleaseTiming.AMC


def reported_from_8k(
    symbol: str, filings: Iterable[FilingMeta], cal: MarketCalendar | None = None
) -> list[EarningsEntry]:
    cal = cal or default_calendar()
    out = []
    for f in filings:
        if not _is_earnings_8k(f) or f.is_amendment:
            continue
        accepted = f.published_at
        out.append(
            EarningsEntry(
                symbol=symbol,
                day=accepted.astimezone(ET).date(),
                status=EntryStatus.REPORTED,
                source="sec_8k_item_2_02",
                timing=_timing_for(accepted, cal),
                source_url=f.url,
                known_since=accepted,
            )
        )
    return sorted(out, key=lambda e: e.day)


def estimate_next_from_8k(
    symbol: str, filings: Iterable[FilingMeta], *, cal: MarketCalendar | None = None
) -> EarningsEntry | None:
    """Estimate the next release from item 2.02 history. Labelled ``expected`` with an uncertainty window.

    Prefers a year-over-year anchor (same quarter last year + 364 days); otherwise last + median gap.
    Requires at least three historical releases; returns None (=> UNKNOWN downstream) otherwise.
    """
    reported = reported_from_8k(symbol, filings, cal)
    if len(reported) < MIN_HISTORY_FOR_ESTIMATE:
        return None
    days = [e.day for e in reported]
    gaps = [(b - a).days for a, b in pairwise(days)]
    median_gap = statistics.median(gaps)
    mad = statistics.median(abs(g - median_gap) for g in gaps)
    last = days[-1]
    anchor_target = last + timedelta(days=median_gap) - timedelta(days=YOY_ANCHOR_DAYS)
    anchors = [d for d in days if abs((d - anchor_target).days) <= 21]
    if anchors:
        best = min(anchors, key=lambda d: abs((d - anchor_target).days))
        guess = best + timedelta(days=YOY_ANCHOR_DAYS)
        uncertainty = max(7, int(mad))
        method = "yoy_anchor"
    else:
        guess = last + timedelta(days=median_gap)
        uncertainty = max(DEFAULT_ESTIMATE_UNCERTAINTY_DAYS, int(2 * mad))
        method = "median_cadence"
    return EarningsEntry(
        symbol=symbol,
        day=guess,
        status=EntryStatus.EXPECTED,
        source="sec_8k_cadence",
        uncertainty_days=uncertainty,
        source_url=reported[-1].source_url,
        known_since=reported[-1].known_since,
        note=f"estimate ({method}) from {len(reported)} item 2.02 filings; NOT a confirmed date",
    )


# ---------------------------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------------------------
def explain_earnings_status(
    symbol: str,
    start_session_date: date,
    horizon_sessions: int,
    calendar: EarningsCalendar | None,
    *,
    market_calendar: MarketCalendar | None = None,
    as_of: datetime | None = None,
) -> EarningsStatusDetail:
    cal = market_calendar or default_calendar()
    try:
        end = cal.add_sessions(start_session_date, max(0, horizon_sessions))
    except (ValueError, KeyError) as exc:
        return EarningsStatusDetail(
            EarningsStatus.UNKNOWN,
            ("HORIZON_OUT_OF_CALENDAR", type(exc).__name__.upper()),
            start_session_date,
            None,
        )
    if calendar is None:
        return EarningsStatusDetail(
            EarningsStatus.UNKNOWN, ("NO_EARNINGS_CALENDAR",), start_session_date, end
        )
    entries = calendar.entries_for(symbol, as_of)
    if not entries:
        return EarningsStatusDetail(
            EarningsStatus.UNKNOWN, ("NO_SCHEDULE_DATA_FOR_SYMBOL",), start_session_date, end
        )
    # a before-open (or unknown-timing) release on the session right after the horizon still gaps an
    # end-of-horizon position overnight, so it counts as inside the horizon (conservative)
    next_after_end = cal.next_session(end)

    def _effective_end(e: EarningsEntry) -> date:
        return next_after_end if e.timing in (ReleaseTiming.BMO, ReleaseTiming.UNKNOWN) else end

    overlapping = [
        e for e in entries if e.window[0] <= _effective_end(e) and e.window[1] >= start_session_date
    ]
    if overlapping:
        codes = sorted({f"{e.status.value.upper()}_RELEASE_IN_HORIZON" for e in overlapping})
        if any(e.window[0] > end for e in overlapping):
            codes.append("PRE_OPEN_RELEASE_AFTER_HORIZON_GAP_RISK")
        return EarningsStatusDetail(
            EarningsStatus.WITHIN_HORIZON, tuple(codes), start_session_date, end, tuple(overlapping)
        )
    confirmed_after = [e for e in entries if e.status is EntryStatus.CONFIRMED and e.day > _effective_end(e)]
    past = [
        e
        for e in entries
        if e.day < start_session_date and e.status in (EntryStatus.REPORTED, EntryStatus.CONFIRMED)
    ]
    if confirmed_after and past:
        nxt = confirmed_after[0]
        last = max(e.day for e in past)
        if (nxt.day - last).days <= MAX_NEXT_QUARTER_GAP_DAYS:
            return EarningsStatusDetail(
                EarningsStatus.NONE_WITHIN_HORIZON_CONFIRMED,
                ("CONFIRMED_NEXT_RELEASE_AFTER_HORIZON",),
                start_session_date,
                end,
                (nxt,),
            )
        return EarningsStatusDetail(
            EarningsStatus.UNKNOWN, ("GAP_TO_CONFIRMED_DATE_TOO_LONG",), start_session_date, end
        )
    if confirmed_after:
        return EarningsStatusDetail(
            EarningsStatus.UNKNOWN, ("LAST_RELEASE_UNKNOWN",), start_session_date, end
        )
    return EarningsStatusDetail(
        EarningsStatus.UNKNOWN, ("NO_CONFIRMED_FUTURE_DATE",), start_session_date, end
    )


def earnings_status(
    symbol: str,
    start_session_date: date,
    horizon_sessions: int,
    calendar: EarningsCalendar | None,
    *,
    market_calendar: MarketCalendar | None = None,
    as_of: datetime | None = None,
) -> str:
    """'NONE_WITHIN_HORIZON_CONFIRMED' | 'WITHIN_HORIZON' | 'UNKNOWN' (missing data is ALWAYS UNKNOWN)."""
    detail = explain_earnings_status(
        symbol, start_session_date, horizon_sessions, calendar, market_calendar=market_calendar, as_of=as_of
    )
    return detail.status.value


# ---------------------------------------------------------------------------------------------
# point-in-time session helpers
# ---------------------------------------------------------------------------------------------
def usable_at_close(available_at: datetime, session_day: date, cal: MarketCalendar | None = None) -> bool:
    """May a decision taken at ``session_day``'s close use information available at ``available_at``?"""
    cal = cal or default_calendar()
    return ensure_utc(available_at) <= cal.session(session_day).close_utc


def first_usable_session(available_at: datetime, cal: MarketCalendar | None = None) -> date:
    """First session whose close-time decision may use the information (after-hours -> next session)."""
    cal = cal or default_calendar()
    available_at = ensure_utc(available_at)
    day = available_at.astimezone(ET).date()
    if cal.is_session(day) and available_at <= cal.session(day).close_utc:
        return day
    return cal.next_session(day)


# ---------------------------------------------------------------------------------------------
# manual entries -> earnings_scheduled catalysts
# ---------------------------------------------------------------------------------------------
def entry_to_catalyst(
    entry: EarningsEntry,
    *,
    first_seen_at: datetime,
    mode: AvailabilityMode | str = AvailabilityMode.BACKFILL,
    is_demo: bool = False,
) -> CatalystRecord:
    if entry.status is EntryStatus.REPORTED:
        raise ValueError("reported releases are earnings_release catalysts from the SEC module")
    available = resolve_available_at(
        mode, first_seen_at=first_seen_at, published_at=entry.known_since, lag=timedelta(0)
    )
    event_at = et_date_start(entry.day)
    reasons = [
        "CONFIRMED_DATE" if entry.status is EntryStatus.CONFIRMED else "EXPECTED_DATE_ESTIMATE",
        f"TIMING_{entry.timing.value}",
    ]
    if entry.uncertainty_days:
        reasons.append(f"UNCERTAINTY_PM_{entry.uncertainty_days}D")
    if entry.known_since is None:
        reasons.append("SCHEDULE_KNOWN_SINCE_UNRECORDED")
    source = Source.MANUAL if entry.source == "manual" else Source.SEC_EDGAR
    url = entry.source_url if entry.source_url and entry.source_url.startswith("https://") else None
    label = "confirmed" if entry.status is EntryStatus.CONFIRMED else "expected (estimate)"
    return CatalystRecord(
        id=make_catalyst_id(
            source, f"{entry.symbol}|{entry.day.isoformat()}|{entry.source}", CatalystType.EARNINGS_SCHEDULED
        ),
        catalyst_type=CatalystType.EARNINGS_SCHEDULED,
        source=source,
        headline=f"{entry.symbol} earnings {label}: {entry.day.isoformat()} {entry.timing.value}",
        source_grade=SourceGrade.B_OFFICIAL_RELEASE
        if entry.status is EntryStatus.CONFIRMED and url
        else SourceGrade.E_UNVERIFIED,
        direction=Direction.UNKNOWN,
        entity_confidence=0.95,
        extraction_confidence=0.9 if entry.status is EntryStatus.CONFIRMED else 0.5,
        first_seen_at=first_seen_at,
        available_at=available,
        source_hash=metadata_hash(
            {"symbol": entry.symbol, "day": entry.day, "status": entry.status, "url": url}
        ),
        parser_version=PARSER_VERSION,
        legal_classification=LegalClassification.MANUAL_ENTRY,
        is_demo=is_demo,
        symbol=normalize_symbol(entry.symbol),
        event_at=event_at,
        published_at=entry.known_since,
        canonical_url=url,
        publisher="Company IR (manual)" if entry.source == "manual" else "SEC EDGAR cadence estimate",
        actionable_age=classify_actionable_age(
            available_at=available, published_at=entry.known_since, event_at=event_at
        ),
        reason_codes=tuple(reasons),
        parsed={
            "status": entry.status.value,
            "timing": entry.timing.value,
            "uncertainty_days": entry.uncertainty_days,
            "event_date_precision": "date",
            "note": (entry.note or "")[:200],
        },
    )


__all__ = [
    "EarningsCalendar",
    "EarningsEntry",
    "EarningsStatus",
    "EarningsStatusDetail",
    "EntryStatus",
    "ReleaseTiming",
    "earnings_status",
    "entry_to_catalyst",
    "estimate_next_from_8k",
    "explain_earnings_status",
    "first_usable_session",
    "reported_from_8k",
    "usable_at_close",
]
