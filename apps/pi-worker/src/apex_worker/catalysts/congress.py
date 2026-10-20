"""Congressional periodic-transaction disclosures: MANUAL IMPORT ONLY.

The official systems (House Clerk financial disclosures, Senate eFD) require interactive steps such
as accepting usage terms, and many reports are PDFs or scanned images. APEX therefore does not scrape
them; automatic collection is DISABLED. An operator may transcribe reports into the CSV format below.

CSV columns (header row required, UTF-8):

    chamber            house | senate
    member_name        as printed on the report
    transaction_date   YYYY-MM-DD (date the trade happened)
    disclosure_date    YYYY-MM-DD (date the report was filed/made public)
    ticker             optional; upper-case exchange symbol
    asset_description  free text as printed on the report
    transaction_type   purchase | sale | exchange
    amount_range       e.g. "$1,001 - $15,000" or "Over $50,000,000"
    source_url         https:// link to the official report (or provider page with provenance)

Point-in-time rules: ``event_at`` = transaction date, ``published_at`` = disclosure date. In backfill the
disclosure is only usable from 00:00 ET on the day AFTER the disclosure date (the publication time of
day is unknown). Never from the transaction date. Reports can legally lag trades by up to 45 days, so
every record is ``lagged_disclosure`` context only - never a trade trigger on its own.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from apex_worker.catalysts.models import (
    ActionableAge,
    AvailabilityMode,
    CatalystRecord,
    CatalystType,
    Direction,
    LegalClassification,
    Source,
    SourceGrade,
    et_date_start,
    et_next_day_start,
    make_catalyst_id,
    metadata_hash,
)
from apex_worker.catalysts.tickers import TickerMap, normalize_symbol
from apex_worker.catalysts.urlsafe import UnsafeUrlError, check_public_https_url, host_of
from apex_worker.timeutil import ensure_utc

PARSER_VERSION = "congress-csv/1.0.0"
REQUIRED_COLUMNS = (
    "chamber",
    "member_name",
    "transaction_date",
    "disclosure_date",
    "ticker",
    "asset_description",
    "transaction_type",
    "amount_range",
    "source_url",
)
TRANSACTION_TYPES = frozenset({"purchase", "sale", "exchange"})
STATUTORY_MAX_LAG_DAYS = 45
MAX_ROWS = 20_000
OFFICIAL_HOSTS = {
    "house": frozenset({"disclosures-clerk.house.gov"}),
    "senate": frozenset({"efdsearch.senate.gov"}),
}
CAPABILITY: dict[str, Any] = {
    "source": "congress_disclosures",
    "automatic": False,
    "status": "DISABLED",
    "manual_import": True,
    "reason": (
        "Automatic collection is DISABLED: the official House Clerk and Senate eFD disclosure systems "
        "require interactive terms acceptance and largely publish PDF/scanned reports, so APEX does not "
        "scrape them. Use the manual CSV import. Reports may lag trades by up to 45 days; they are lagged "
        "thematic context, never real-time politician trades and never a stand-alone trade trigger."
    ),
}
_AMOUNT_RE = re.compile(r"\$?\s*([\d,]+)")


class CongressImportError(ValueError):
    pass


@dataclass(frozen=True)
class RowError:
    row_number: int
    reason_code: str
    message: str


@dataclass(frozen=True)
class CongressImport:
    records: tuple[CatalystRecord, ...]
    errors: tuple[RowError, ...]


def parse_amount_range(text: str) -> tuple[int | None, int | None]:
    nums = [int(n.replace(",", "")) for n in _AMOUNT_RE.findall(text or "") if n.replace(",", "")]
    if not nums:
        return None, None
    if (text or "").strip().lower().startswith("over"):
        return nums[0], None
    return (nums[0], nums[1]) if len(nums) >= 2 else (nums[0], nums[0])


def _source_for(chamber: str, url: str) -> tuple[Source, SourceGrade, str | None]:
    host = host_of(url)
    if host in OFFICIAL_HOSTS[chamber]:
        src = Source.HOUSE_CLERK if chamber == "house" else Source.SENATE_EFD
        return src, SourceGrade.A_OFFICIAL_FILING, None
    return Source.MANUAL, SourceGrade.D_OTHER, "SOURCE_NOT_OFFICIAL_HOST"


def _row_to_record(
    row: dict[str, str],
    *,
    first_seen_at: datetime,
    mode: AvailabilityMode,
    ticker_map: TickerMap | None,
    is_demo: bool,
) -> CatalystRecord:
    chamber = row["chamber"].strip().lower()
    if chamber not in OFFICIAL_HOSTS:
        raise CongressImportError("CHAMBER_INVALID")
    member = row["member_name"].strip()
    if not member:
        raise CongressImportError("MEMBER_MISSING")
    try:
        tx_date = date.fromisoformat(row["transaction_date"].strip())
        disc_date = date.fromisoformat(row["disclosure_date"].strip())
    except ValueError as exc:
        raise CongressImportError("DATE_INVALID") from exc
    if disc_date < tx_date:
        raise CongressImportError("DISCLOSURE_BEFORE_TRANSACTION")
    tx_type = row["transaction_type"].strip().lower()
    if tx_type not in TRANSACTION_TYPES:
        raise CongressImportError("TRANSACTION_TYPE_INVALID")
    url = row["source_url"].strip()
    try:
        check_public_https_url(url)
    except UnsafeUrlError as exc:
        raise CongressImportError("SOURCE_URL_INVALID") from exc

    published = et_date_start(disc_date)
    first_seen_at = ensure_utc(first_seen_at)
    if mode is AvailabilityMode.LIVE:
        if first_seen_at < published:
            raise CongressImportError("DISCLOSURE_DATE_IN_FUTURE")
        available = first_seen_at
    else:
        available = et_next_day_start(disc_date)

    lag_days = (disc_date - tx_date).days
    source, grade, src_reason = _source_for(chamber, url)
    reasons = [
        "CONTEXT_ONLY_NOT_A_TRADE_TRIGGER",
        f"DISCLOSURE_LAG_{lag_days}D",
        "MANUAL_IMPORT",
        "DATE_ONLY_PRECISION",
        f"TX_{tx_type.upper()}",
    ]
    if lag_days > STATUTORY_MAX_LAG_DAYS:
        reasons.append("LATE_DISCLOSURE_GT_45D")
    if src_reason:
        reasons.append(src_reason)

    ticker_raw = row.get("ticker", "").strip()
    symbol = normalize_symbol(ticker_raw) if ticker_raw else None
    entity_conf = 0.75 if symbol else 0.2
    if ticker_raw and symbol is None:
        reasons.append("TICKER_INVALID")
    if symbol and ticker_map is not None:
        resolution = ticker_map.resolve(symbol, tx_date)
        if resolution.cik is None:
            reasons.append("TICKER_UNRESOLVED_ON_TRADE_DATE")
            entity_conf = 0.3
        else:
            entity_conf = 0.85
    if not symbol:
        reasons.append("NO_TICKER_ON_REPORT")

    asset = row.get("asset_description", "").strip()
    amount = row.get("amount_range", "").strip()
    lo, hi = parse_amount_range(amount)
    canonical = {k: (row.get(k) or "").strip() for k in REQUIRED_COLUMNS}
    identity = "|".join(canonical[k] for k in REQUIRED_COLUMNS)
    label = "Senate" if chamber == "senate" else "House"
    headline = (
        f"{label} disclosure: {member} reported {tx_type} of {symbol or asset or 'undisclosed asset'}"
        f" ({amount or 'amount n/a'}); traded {tx_date.isoformat()}, disclosed {disc_date.isoformat()}"
    )
    return CatalystRecord(
        id=make_catalyst_id(source, identity, CatalystType.POLITICAL_DISCLOSURE),
        catalyst_type=CatalystType.POLITICAL_DISCLOSURE,
        source=source,
        headline=headline,
        source_grade=grade,
        direction=Direction.UNKNOWN,
        entity_confidence=entity_conf,
        extraction_confidence=0.7,
        first_seen_at=first_seen_at,
        available_at=available,
        source_hash=metadata_hash(canonical),
        parser_version=PARSER_VERSION,
        legal_classification=LegalClassification.MANUAL_ENTRY,
        is_demo=is_demo,
        symbol=symbol,
        event_at=et_date_start(tx_date),
        published_at=published,
        canonical_url=url,
        publisher=f"{label} disclosure (manual import)",
        actionable_age=ActionableAge.LAGGED_DISCLOSURE,
        reason_codes=tuple(reasons),
        regulatory_flags=("STOCK_ACT_PTR",),
        parsed={
            "chamber": chamber,
            "member_name": member,
            "transaction_type": tx_type,
            "transaction_date": tx_date,
            "disclosure_date": disc_date,
            "disclosure_lag_days": lag_days,
            "asset_description": asset[:200],
            "amount_range": amount,
            "amount_min_usd": lo,
            "amount_max_usd": hi,
            "event_date_precision": "date",
            "published_date_precision": "date",
        },
    )


def import_csv(
    text: str,
    *,
    first_seen_at: datetime,
    mode: AvailabilityMode | str = AvailabilityMode.BACKFILL,
    ticker_map: TickerMap | None = None,
    is_demo: bool = False,
) -> CongressImport:
    """Parse the documented CSV. Bad rows become RowErrors; the import never raises for a single row."""
    mode = AvailabilityMode(mode)
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise CongressImportError(f"CSV missing required columns: {', '.join(missing)}")
    reader.fieldnames = header
    records: list[CatalystRecord] = []
    errors: list[RowError] = []
    seen: set[str] = set()
    for idx, row in enumerate(reader, start=2):
        if idx - 1 > MAX_ROWS:
            errors.append(RowError(idx, "ROW_LIMIT_EXCEEDED", f"only the first {MAX_ROWS} rows are imported"))
            break
        clean = {k: (v or "") for k, v in row.items() if k is not None}
        try:
            rec = _row_to_record(
                clean, first_seen_at=first_seen_at, mode=mode, ticker_map=ticker_map, is_demo=is_demo
            )
        except CongressImportError as exc:
            errors.append(RowError(idx, str(exc).split(":")[0], f"row {idx} rejected: {exc}"))
            continue
        if rec.id in seen:
            errors.append(RowError(idx, "DUPLICATE_ROW", f"row {idx} duplicates an earlier row"))
            continue
        seen.add(rec.id)
        records.append(rec)
    return CongressImport(tuple(records), tuple(errors))


__all__ = [
    "CAPABILITY",
    "PARSER_VERSION",
    "REQUIRED_COLUMNS",
    "CongressImport",
    "CongressImportError",
    "RowError",
    "import_csv",
    "parse_amount_range",
]
