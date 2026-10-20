"""Internal catalyst record, identity/hash helpers and point-in-time availability rules.

Four distinct instants are tracked for every catalyst:

* ``event_at``      - when the underlying thing happened (transaction date, scheduled release, ...)
* ``published_at``  - when the source made it public (EDGAR acceptance, feed pubDate, disclosure date)
* ``first_seen_at`` - when APEX actually observed it (wall clock at ingest)
* ``available_at``  - the earliest instant the algorithm may use it

``available_at`` semantics:

* LIVE ingestion: ``available_at = first_seen_at`` (never earlier than ``published_at``).
* Historical BACKFILL: ``available_at = published_at + conservative lag`` (default 15 minutes for SEC
  acceptance timestamps, 60 minutes for news). Nothing is ever available before it was published.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any

from apex_worker.contracts import validate
from apex_worker.timeutil import ET, ensure_utc, iso_utc, parse_utc

HEADLINE_MAX = 200
PUBLISHER_MAX = 80
MAX_REASON_CODES = 16
MAX_REGULATORY_FLAGS = 8
SEC_ACCEPTANCE_LAG = timedelta(minutes=15)
NEWS_PUBLICATION_LAG = timedelta(minutes=60)
FRESH_MAX_AGE = timedelta(hours=24)
RECENT_MAX_AGE = timedelta(days=5)

_ID_RE = re.compile(r"^cat_[0-9a-f]{24}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_CODE_CLEAN_RE = re.compile(r"[^A-Z0-9_]+")
_WS_RE = re.compile(r"\s+")


class CatalystType(StrEnum):
    EARNINGS_RELEASE = "earnings_release"
    EARNINGS_SCHEDULED = "earnings_scheduled"
    GUIDANCE_RAISE = "guidance_raise"
    GUIDANCE_CUT = "guidance_cut"
    CONTRACT_AWARD = "contract_award"
    REGULATORY_DECISION = "regulatory_decision"
    MERGER_ACQUISITION = "merger_acquisition"
    BUYBACK = "buyback"
    INSIDER_OPEN_MARKET_PURCHASE = "insider_open_market_purchase"
    INSIDER_SALE = "insider_sale"
    INSIDER_AWARD_OR_EXERCISE = "insider_award_or_exercise"
    DILUTION_OFFERING = "dilution_offering"
    SHELF_REGISTRATION = "shelf_registration"
    REGULATORY_ACTION = "regulatory_action"
    GOVERNMENT_PROCUREMENT = "government_procurement"
    MACRO_RELEASE = "macro_release"
    SECTOR_SHOCK = "sector_shock"
    MANAGEMENT_CHANGE = "management_change"
    OWNERSHIP_13D = "ownership_13d"
    OWNERSHIP_13G = "ownership_13g"
    PERIODIC_REPORT = "periodic_report"
    CURRENT_REPORT_OTHER = "current_report_other"
    POLITICAL_DISCLOSURE = "political_disclosure"
    NEWS_OTHER = "news_other"


class Source(StrEnum):
    SEC_EDGAR = "sec_edgar"
    SENATE_EFD = "senate_efd"
    HOUSE_CLERK = "house_clerk"
    NEWS_RSS = "news_rss"
    FRED = "fred"
    MANUAL = "manual"
    REPLAY = "replay"


class SourceGrade(StrEnum):
    A_OFFICIAL_FILING = "A_official_filing"
    B_OFFICIAL_RELEASE = "B_official_release"
    C_REPUTABLE_WIRE = "C_reputable_wire"
    D_OTHER = "D_other"
    E_UNVERIFIED = "E_unverified"


GRADE_RANK: dict[SourceGrade, int] = {
    SourceGrade.A_OFFICIAL_FILING: 5,
    SourceGrade.B_OFFICIAL_RELEASE: 4,
    SourceGrade.C_REPUTABLE_WIRE: 3,
    SourceGrade.D_OTHER: 2,
    SourceGrade.E_UNVERIFIED: 1,
}


class Direction(StrEnum):
    POTENTIALLY_POSITIVE = "potentially_positive"
    POTENTIALLY_NEGATIVE = "potentially_negative"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class LegalClassification(StrEnum):
    PUBLIC_METADATA_LINK_ONLY = "public_metadata_link_only"
    PUBLIC_DOMAIN_GOVERNMENT = "public_domain_government"
    LICENSED_SUMMARY = "licensed_summary"
    MANUAL_ENTRY = "manual_entry"


class ActionableAge(StrEnum):
    FRESH = "fresh"
    RECENT = "recent"
    STALE = "stale"
    LAGGED_DISCLOSURE = "lagged_disclosure"
    SCHEDULED_FUTURE = "scheduled_future"


class AvailabilityMode(StrEnum):
    LIVE = "live"
    BACKFILL = "backfill"


# ---------------------------------------------------------------------------------------------
# hashing / identity
# ---------------------------------------------------------------------------------------------
def sha256_hex(data: bytes | str) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def jsonable(value: Any) -> Any:
    """Convert Decimals/datetimes/enums/tuples into plain JSON types (Decimal -> exact string)."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return iso_utc(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        items = [jsonable(v) for v in value]
        return sorted(items, key=str) if isinstance(value, set | frozenset) else items
    return value


def canonical_json(obj: Any) -> bytes:
    return json.dumps(jsonable(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def metadata_hash(obj: Any) -> str:
    """sha256 over canonical JSON of the metadata actually used (when there are no raw bytes)."""
    return sha256_hex(canonical_json(obj))


def make_catalyst_id(source: str | Source, identity: str, catalyst_type: str | CatalystType) -> str:
    """Stable id: ``cat_`` + first 24 hex of sha256(source | identity | type)."""
    src = Source(source).value
    ctype = CatalystType(catalyst_type).value
    if not identity:
        raise ValueError("catalyst identity must be non-empty")
    return "cat_" + sha256_hex(f"{src}|{identity}|{ctype}")[:24]


def reason_code(*parts: object) -> str:
    """Build a contract-safe reason code (``^[A-Z0-9_]{2,64}$``) from free-form parts."""
    text = "_".join(str(p) for p in parts if p is not None and str(p) != "")
    code = _CODE_CLEAN_RE.sub("_", text.upper()).strip("_")
    code = re.sub(r"_+", "_", code)[:64].rstrip("_")
    if len(code) < 2:
        raise ValueError(f"cannot build reason code from {parts!r}")
    return code


def normalize_codes(codes: Iterable[str], limit: int, overflow_code: str | None) -> tuple[str, ...]:
    out: list[str] = []
    for c in codes:
        rc = reason_code(c)
        if rc not in out:
            out.append(rc)
    if len(out) > limit:
        out = [*out[: limit - 1], overflow_code] if overflow_code else out[:limit]
    return tuple(out)


def clean_text(text: str, limit: int) -> str:
    """NFKC-normalize, drop control characters, collapse whitespace, truncate with an ellipsis."""
    text = unicodedata.normalize("NFKC", text or "")
    text = "".join(ch if unicodedata.category(ch)[0] != "C" else " " for ch in text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


# ---------------------------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------------------------
def et_date_start(day: date) -> datetime:
    """00:00 America/New_York on ``day`` as a UTC instant (DST-aware)."""
    return datetime.combine(day, time(0, 0), ET).astimezone(UTC)


def et_next_day_start(day: date) -> datetime:
    """00:00 ET on the day after ``day``: conservative availability for date-only publications."""
    return et_date_start(day + timedelta(days=1))


def resolve_available_at(
    mode: AvailabilityMode | str,
    *,
    first_seen_at: datetime,
    published_at: datetime | None,
    lag: timedelta,
) -> datetime:
    """Earliest instant the algorithm may use a record.

    LIVE: the moment APEX saw it (never earlier than publication). BACKFILL: publication + lag; with no
    publication time we can only claim the first-seen instant (which keeps it out of historical tests).
    """
    mode = AvailabilityMode(mode)
    first_seen_at = ensure_utc(first_seen_at)
    if lag < timedelta(0):
        raise ValueError("availability lag cannot be negative")
    if published_at is None:
        return first_seen_at
    published_at = ensure_utc(published_at)
    if mode is AvailabilityMode.LIVE:
        return max(first_seen_at, published_at)
    return published_at + lag


def classify_actionable_age(
    *,
    available_at: datetime,
    published_at: datetime | None,
    event_at: datetime | None,
    lagged_disclosure: bool = False,
) -> ActionableAge:
    """Age category of the information at the moment it becomes available."""
    if lagged_disclosure:
        return ActionableAge.LAGGED_DISCLOSURE
    if event_at is not None and ensure_utc(event_at) > ensure_utc(available_at):
        return ActionableAge.SCHEDULED_FUTURE
    ref = published_at or event_at
    if ref is None:
        return ActionableAge.FRESH
    age = ensure_utc(available_at) - ensure_utc(ref)
    if age <= FRESH_MAX_AGE:
        return ActionableAge.FRESH
    if age <= RECENT_MAX_AGE:
        return ActionableAge.RECENT
    return ActionableAge.STALE


# ---------------------------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class CatalystRecord:
    id: str
    catalyst_type: CatalystType
    source: Source
    headline: str
    source_grade: SourceGrade
    direction: Direction
    entity_confidence: float
    extraction_confidence: float
    first_seen_at: datetime
    available_at: datetime
    source_hash: str
    parser_version: str
    legal_classification: LegalClassification
    is_demo: bool
    symbol: str | None = None
    cik: str | None = None
    event_at: datetime | None = None
    published_at: datetime | None = None
    canonical_url: str | None = None
    accession_number: str | None = None
    publisher: str | None = None
    impact_score: float | None = None
    actionable_age: ActionableAge | None = None
    cluster_id: str | None = None
    revision_of: str | None = None
    reason_codes: tuple[str, ...] = ()
    regulatory_flags: tuple[str, ...] = ()
    parsed: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        def _set(name: str, value: Any) -> None:
            object.__setattr__(self, name, value)

        if not _ID_RE.match(self.id):
            raise ValueError(f"invalid catalyst id {self.id!r}")
        if not _HASH_RE.match(self.source_hash):
            raise ValueError("source_hash must be a sha256 hex digest")
        _set("catalyst_type", CatalystType(self.catalyst_type))
        _set("source", Source(self.source))
        _set("source_grade", SourceGrade(self.source_grade))
        _set("direction", Direction(self.direction))
        _set("legal_classification", LegalClassification(self.legal_classification))
        if self.actionable_age is not None:
            _set("actionable_age", ActionableAge(self.actionable_age))
        for name in ("first_seen_at", "available_at", "event_at", "published_at"):
            value = getattr(self, name)
            if value is not None:
                _set(name, ensure_utc(value))
        for name in ("entity_confidence", "extraction_confidence"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be within [0, 1]")
            _set(name, round(value, 4))
        if self.impact_score is not None and not 0.0 <= float(self.impact_score) <= 100.0:
            raise ValueError("impact_score must be within [0, 100] (it is NOT a probability)")
        if self.published_at is not None and self.available_at < self.published_at:
            raise ValueError("available_at cannot precede published_at (point-in-time violation)")
        _set("headline", clean_text(self.headline, HEADLINE_MAX))
        if not self.headline:
            raise ValueError("headline must be non-empty")
        if self.publisher is not None:
            _set("publisher", clean_text(self.publisher, PUBLISHER_MAX) or None)
        _set("reason_codes", normalize_codes(self.reason_codes, MAX_REASON_CODES, "REASON_CODES_TRUNCATED"))
        _set("regulatory_flags", normalize_codes(self.regulatory_flags, MAX_REGULATORY_FLAGS, None))

    # -- convenience ---------------------------------------------------------------------------
    def with_changes(self, **changes: Any) -> CatalystRecord:
        return replace(self, **changes)

    def add_reason_codes(self, *codes: str) -> CatalystRecord:
        return replace(self, reason_codes=(*self.reason_codes, *codes))

    def is_available(self, decision_time: datetime) -> bool:
        return self.available_at <= ensure_utc(decision_time)

    # -- contract ------------------------------------------------------------------------------
    def to_contract(self, *, check: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "catalyst_type": self.catalyst_type.value,
            "source": self.source.value,
            "first_seen_at": iso_utc(self.first_seen_at),
            "available_at": iso_utc(self.available_at),
            "headline": self.headline,
            "source_grade": self.source_grade.value,
            "direction": self.direction.value,
            "entity_confidence": self.entity_confidence,
            "extraction_confidence": self.extraction_confidence,
            "reason_codes": list(self.reason_codes),
            "source_hash": self.source_hash,
            "parser_version": self.parser_version,
            "legal_classification": self.legal_classification.value,
            "is_demo": self.is_demo,
        }
        optional: dict[str, Any] = {
            "symbol": self.symbol,
            "cik": self.cik,
            "event_at": iso_utc(self.event_at) if self.event_at else None,
            "published_at": iso_utc(self.published_at) if self.published_at else None,
            "canonical_url": self.canonical_url,
            "accession_number": self.accession_number,
            "publisher": self.publisher,
            "impact_score": self.impact_score,
            "actionable_age": self.actionable_age.value if self.actionable_age else None,
            "cluster_id": self.cluster_id,
            "revision_of": self.revision_of,
        }
        payload.update({k: v for k, v in optional.items() if v is not None})
        if self.regulatory_flags:
            payload["regulatory_flags"] = list(self.regulatory_flags)
        if self.parsed:
            payload["parsed"] = jsonable(self.parsed)
        if check:
            validate("Catalyst", payload)
        return payload

    @classmethod
    def from_contract(cls, payload: Mapping[str, Any]) -> CatalystRecord:
        def _ts(key: str) -> datetime | None:
            value = payload.get(key)
            return parse_utc(value) if value else None

        return cls(
            id=payload["id"],
            catalyst_type=CatalystType(payload["catalyst_type"]),
            source=Source(payload["source"]),
            headline=payload["headline"],
            source_grade=SourceGrade(payload["source_grade"]),
            direction=Direction(payload["direction"]),
            entity_confidence=payload["entity_confidence"],
            extraction_confidence=payload["extraction_confidence"],
            first_seen_at=parse_utc(payload["first_seen_at"]),
            available_at=parse_utc(payload["available_at"]),
            source_hash=payload["source_hash"],
            parser_version=payload["parser_version"],
            legal_classification=LegalClassification(payload["legal_classification"]),
            is_demo=bool(payload["is_demo"]),
            symbol=payload.get("symbol"),
            cik=payload.get("cik"),
            event_at=_ts("event_at"),
            published_at=_ts("published_at"),
            canonical_url=payload.get("canonical_url"),
            accession_number=payload.get("accession_number"),
            publisher=payload.get("publisher"),
            impact_score=payload.get("impact_score"),
            actionable_age=ActionableAge(payload["actionable_age"])
            if payload.get("actionable_age")
            else None,
            cluster_id=payload.get("cluster_id"),
            revision_of=payload.get("revision_of"),
            reason_codes=tuple(payload.get("reason_codes", ())),
            regulatory_flags=tuple(payload.get("regulatory_flags", ())),
            parsed=dict(payload.get("parsed", {})),
        )


def available_at_of(record: CatalystRecord | Mapping[str, Any]) -> datetime:
    if isinstance(record, CatalystRecord):
        return record.available_at
    return parse_utc(record["available_at"])


def is_available(record: CatalystRecord | Mapping[str, Any], decision_time: datetime) -> bool:
    """True only when the record was usable at ``decision_time`` (``available_at <= decision_time``)."""
    return available_at_of(record) <= ensure_utc(decision_time)


__all__ = [
    "GRADE_RANK",
    "NEWS_PUBLICATION_LAG",
    "SEC_ACCEPTANCE_LAG",
    "ActionableAge",
    "AvailabilityMode",
    "CatalystRecord",
    "CatalystType",
    "Direction",
    "LegalClassification",
    "Source",
    "SourceGrade",
    "available_at_of",
    "canonical_json",
    "classify_actionable_age",
    "clean_text",
    "et_date_start",
    "et_next_day_start",
    "is_available",
    "jsonable",
    "make_catalyst_id",
    "metadata_hash",
    "reason_code",
    "resolve_available_at",
    "sha256_hex",
]
