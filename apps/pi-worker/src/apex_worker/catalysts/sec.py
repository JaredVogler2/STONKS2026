"""SEC EDGAR submissions parsing and form -> catalyst mapping (metadata + link only).

Input format: https://data.sec.gov/submissions/CIK##########.json with column arrays under
``filings.recent`` (accessionNumber, filingDate, reportDate, acceptanceDateTime, form, items,
primaryDocument, primaryDocDescription, size, ...).

Timestamps: the submissions API renders ``acceptanceDateTime`` in UTC, e.g. ``2026-07-30T20:30:28.000Z`` for a
filing whose EDGAR index page shows it accepted ``2026-07-30 16:30:28`` (US Eastern). A value with a UTC designator is
read as UTC; the raw header format ``20260306164512`` (the SGML ``ACCEPTANCE-DATETIME``) and a naive value are the
US Eastern wall clock, localized to America/New_York (DST-aware) before converting to UTC.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from apex_worker.catalysts.http import FetchResult, PoliteHttpClient, UserAgentError, is_sec_url
from apex_worker.catalysts.models import (
    SEC_ACCEPTANCE_LAG,
    AvailabilityMode,
    CatalystRecord,
    CatalystType,
    Direction,
    LegalClassification,
    Source,
    SourceGrade,
    classify_actionable_age,
    make_catalyst_id,
    metadata_hash,
    reason_code,
    resolve_available_at,
)
from apex_worker.catalysts.tickers import TickerMap, normalize_cik, normalize_symbol
from apex_worker.timeutil import ET, ensure_utc

PARSER_VERSION = "sec-submissions/1.0.0"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_LAST_ACCEPTANCE_ET = time(22, 0)
STALE_DISCOVERY_AGE = timedelta(days=3)
_ACCESSION_RE = re.compile(r"^(\d{10})-?(\d{2})-?(\d{6})$")
_ACCEPT_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]00:?00)?$"
)
_ACCEPT_RAW_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$")
_XSL_PREFIX_RE = re.compile(r"^xsl[^/]+/", re.IGNORECASE)


class SubmissionsParseError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------------------------
def normalize_accession(value: str) -> str:
    m = _ACCESSION_RE.match(str(value).strip())
    if not m:
        raise SubmissionsParseError(f"invalid accession number {value!r}")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def parse_acceptance_datetime(value: str) -> datetime:
    """EDGAR acceptance -> aware UTC instant.

    * ISO with a UTC designator (the submissions API, e.g. ``2026-07-30T20:30:28.000Z``) is UTC. EDGAR's filing index
      shows that filing (Apple's 8-K 0000320193-26-000018) as accepted ``2026-07-30 16:30:28`` Eastern, and the same
      4:30 pm release prints ``21:30`` in winter (``2019-01-29T21:30:18.000Z``, EST).
    * A naive ISO value or the raw header format ``20260306164512`` (the SGML ``ACCEPTANCE-DATETIME``) is the US Eastern
      wall clock; the ambiguous fall-back hour resolves to the *later* instant (fold=1): conservative for availability.
    """
    raw = str(value).strip()
    m = _ACCEPT_ISO_RE.match(raw) or _ACCEPT_RAW_RE.match(raw)
    if not m:
        raise SubmissionsParseError(f"unrecognised acceptanceDateTime {value!r}")
    y, mo, d, hh, mi, ss = (int(g) for g in m.groups())
    if raw.endswith(("Z", "+00:00", "+0000", "-00:00", "-0000")):
        return datetime(y, mo, d, hh, mi, ss, tzinfo=UTC)
    return datetime(y, mo, d, hh, mi, ss, tzinfo=ET, fold=1).astimezone(UTC)


def archive_cik(cik: str) -> str:
    return str(int(normalize_cik(cik)))


def filing_url(cik: str, accession: str, primary_document: str | None) -> str:
    acc = normalize_accession(accession)
    folder = f"{ARCHIVES_BASE}/{archive_cik(cik)}/{acc.replace('-', '')}"
    if primary_document:
        doc = primary_document.strip().lstrip("/")
        if ".." in doc or doc.startswith(("http:", "https:")):
            raise SubmissionsParseError(f"suspicious primaryDocument {primary_document!r}")
        return f"{folder}/{doc}"
    return f"{folder}/{acc}-index.htm"


def raw_document_url(cik: str, accession: str, primary_document: str) -> str:
    """URL of the raw document (drops EDGAR's ``xslF345X05/`` rendering prefix used for Form 4 XML)."""
    return filing_url(cik, accession, _XSL_PREFIX_RE.sub("", primary_document))


# ---------------------------------------------------------------------------------------------
# submissions
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class FilingMeta:
    cik: str
    company_name: str
    accession_number: str
    form: str
    filing_date: date
    acceptance_at: datetime | None
    primary_document: str | None = None
    primary_doc_description: str | None = None
    report_date: date | None = None
    items: tuple[str, ...] = ()
    size: int | None = None
    tickers: tuple[str, ...] = ()
    row: Mapping[str, Any] = field(default_factory=dict, compare=False)

    @property
    def url(self) -> str:
        return filing_url(self.cik, self.accession_number, self.primary_document)

    @property
    def base_form(self) -> str:
        return self.form.upper().removesuffix("/A").strip()

    @property
    def is_amendment(self) -> bool:
        return self.form.upper().endswith("/A")

    @property
    def published_at(self) -> datetime:
        """Acceptance instant; when missing, the latest possible EDGAR acceptance on the filing date."""
        if self.acceptance_at is not None:
            return self.acceptance_at
        return datetime.combine(self.filing_date, EDGAR_LAST_ACCEPTANCE_ET, ET).astimezone(UTC)


@dataclass(frozen=True)
class SubmissionsDoc:
    cik: str
    name: str
    tickers: tuple[str, ...]
    exchanges: tuple[str, ...]
    filings: tuple[FilingMeta, ...]
    errors: tuple[str, ...] = ()


def _col(recent: Mapping[str, Any], key: str, n: int) -> list[Any]:
    values = recent.get(key)
    if values is None:
        return [None] * n
    if not isinstance(values, list) or len(values) != n:
        raise SubmissionsParseError(f"filings.recent.{key} length mismatch")
    return values


def parse_submissions(data: bytes | str | Mapping[str, Any]) -> SubmissionsDoc:
    """Parse a submissions JSON document. Bad rows are skipped and reported in ``errors``."""
    try:
        doc = json.loads(data) if isinstance(data, bytes | str) else data
    except json.JSONDecodeError as exc:
        raise SubmissionsParseError(f"submissions JSON is not valid JSON: {exc}") from exc
    if not isinstance(doc, Mapping) or "cik" not in doc or "filings" not in doc:
        raise SubmissionsParseError("not an EDGAR submissions document (missing cik/filings)")
    cik = normalize_cik(doc["cik"])
    name = str(doc.get("name") or "").strip() or f"CIK {cik}"
    tickers = tuple(t for t in (normalize_symbol(x) for x in doc.get("tickers") or []) if t)
    recent = (doc.get("filings") or {}).get("recent")
    if not isinstance(recent, Mapping) or "accessionNumber" not in recent:
        raise SubmissionsParseError("filings.recent.accessionNumber missing")
    n = len(recent["accessionNumber"])
    cols = {
        k: _col(recent, k, n)
        for k in (
            "accessionNumber",
            "filingDate",
            "reportDate",
            "acceptanceDateTime",
            "form",
            "items",
            "primaryDocument",
            "primaryDocDescription",
            "size",
        )
    }
    filings: list[FilingMeta] = []
    errors: list[str] = []
    for i in range(n):
        row = {k: v[i] for k, v in cols.items()}
        try:
            acceptance = (
                parse_acceptance_datetime(row["acceptanceDateTime"]) if row["acceptanceDateTime"] else None
            )
            items_raw = row["items"] or ""
            filings.append(
                FilingMeta(
                    cik=cik,
                    company_name=name,
                    accession_number=normalize_accession(row["accessionNumber"]),
                    form=str(row["form"] or "").strip(),
                    filing_date=date.fromisoformat(row["filingDate"]),
                    acceptance_at=acceptance,
                    primary_document=(row["primaryDocument"] or None),
                    primary_doc_description=(row["primaryDocDescription"] or None),
                    report_date=date.fromisoformat(row["reportDate"]) if row["reportDate"] else None,
                    items=tuple(x.strip() for x in str(items_raw).split(",") if x.strip()),
                    size=int(row["size"]) if row["size"] not in (None, "") else None,
                    tickers=tickers,
                    row=row,
                )
            )
        except (SubmissionsParseError, ValueError, TypeError) as exc:
            errors.append(f"row {i}: {exc}")
    return SubmissionsDoc(
        cik, name, tickers, tuple(doc.get("exchanges") or ()), tuple(filings), tuple(errors)
    )


# ---------------------------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class FormClassification:
    catalyst_type: CatalystType | None
    direction: Direction
    reason_codes: tuple[str, ...]
    description: str
    needs_detail: bool = False
    regulatory_flags: tuple[str, ...] = ()


_D = Direction
_T = CatalystType
ITEM_RULES: dict[str, tuple[CatalystType, Direction, str, str]] = {
    "1.01": (
        _T.CURRENT_REPORT_OTHER,
        _D.UNKNOWN,
        "ITEM_1_01_MATERIAL_AGREEMENT",
        "Entry into a Material Definitive Agreement",
    ),
    "1.02": (
        _T.CURRENT_REPORT_OTHER,
        _D.UNKNOWN,
        "ITEM_1_02_AGREEMENT_TERMINATED",
        "Termination of a Material Definitive Agreement",
    ),
    "1.03": (
        _T.CURRENT_REPORT_OTHER,
        _D.POTENTIALLY_NEGATIVE,
        "ITEM_1_03_BANKRUPTCY_RECEIVERSHIP",
        "Bankruptcy or Receivership",
    ),
    "1.05": (
        _T.CURRENT_REPORT_OTHER,
        _D.POTENTIALLY_NEGATIVE,
        "ITEM_1_05_CYBERSECURITY_INCIDENT",
        "Material Cybersecurity Incident",
    ),
    "2.01": (
        _T.MERGER_ACQUISITION,
        _D.UNKNOWN,
        "ITEM_2_01_ACQUISITION_DISPOSITION_DONE",
        "Completion of Acquisition or Disposition of Assets",
    ),
    "2.02": (
        _T.EARNINGS_RELEASE,
        _D.UNKNOWN,
        "ITEM_2_02_RESULTS_OF_OPERATIONS",
        "Results of Operations and Financial Condition",
    ),
    "2.03": (
        _T.CURRENT_REPORT_OTHER,
        _D.UNKNOWN,
        "ITEM_2_03_DIRECT_FINANCIAL_OBLIGATION",
        "Creation of a Direct Financial Obligation",
    ),
    "2.05": (
        _T.CURRENT_REPORT_OTHER,
        _D.UNKNOWN,
        "ITEM_2_05_EXIT_OR_DISPOSAL_COSTS",
        "Costs Associated with Exit or Disposal Activities",
    ),
    "2.06": (
        _T.CURRENT_REPORT_OTHER,
        _D.POTENTIALLY_NEGATIVE,
        "ITEM_2_06_MATERIAL_IMPAIRMENT",
        "Material Impairments",
    ),
    "3.01": (
        _T.CURRENT_REPORT_OTHER,
        _D.POTENTIALLY_NEGATIVE,
        "ITEM_3_01_LISTING_DEFICIENCY",
        "Notice of Delisting or Listing Rule Failure",
    ),
    "3.02": (
        _T.DILUTION_OFFERING,
        _D.POTENTIALLY_NEGATIVE,
        "ITEM_3_02_UNREGISTERED_EQUITY_SALE",
        "Unregistered Sales of Equity Securities",
    ),
    "4.01": (
        _T.CURRENT_REPORT_OTHER,
        _D.UNKNOWN,
        "ITEM_4_01_AUDITOR_CHANGE",
        "Changes in Registrant's Certifying Accountant",
    ),
    "4.02": (
        _T.CURRENT_REPORT_OTHER,
        _D.POTENTIALLY_NEGATIVE,
        "ITEM_4_02_NON_RELIANCE",
        "Non-Reliance on Previously Issued Financial Statements",
    ),
    "5.02": (
        _T.MANAGEMENT_CHANGE,
        _D.UNKNOWN,
        "ITEM_5_02_OFFICER_DIRECTOR_CHANGE",
        "Departure/Election of Directors or Officers",
    ),
    "5.07": (
        _T.CURRENT_REPORT_OTHER,
        _D.UNKNOWN,
        "ITEM_5_07_SHAREHOLDER_VOTE",
        "Submission of Matters to a Vote of Security Holders",
    ),
    "7.01": (_T.CURRENT_REPORT_OTHER, _D.UNKNOWN, "ITEM_7_01_REG_FD_DISCLOSURE", "Regulation FD Disclosure"),
    "8.01": (_T.CURRENT_REPORT_OTHER, _D.UNKNOWN, "ITEM_8_01_OTHER_EVENTS", "Other Events"),
}
ITEM_PRIORITY = (
    "2.02", "1.03", "4.02", "1.05", "2.01", "3.02", "1.01", "5.02", "3.01", "2.06", "1.02", "2.05", "2.03",
    "4.01", "5.07", "7.01", "8.01",
)  # fmt: skip
FORM_RULES: dict[str, tuple[CatalystType, Direction, tuple[str, ...], str]] = {
    "10-Q": (_T.PERIODIC_REPORT, _D.UNKNOWN, ("PERIODIC_REPORT_10Q",), "Quarterly report"),
    "10-K": (_T.PERIODIC_REPORT, _D.UNKNOWN, ("PERIODIC_REPORT_10K",), "Annual report"),
    "S-3": (
        _T.SHELF_REGISTRATION,
        _D.UNKNOWN,
        ("SHELF_CAPACITY_NOT_AN_OFFERING", "POTENTIAL_FUTURE_DILUTION"),
        "Shelf registration",
    ),
    "S-3ASR": (
        _T.SHELF_REGISTRATION,
        _D.UNKNOWN,
        ("SHELF_CAPACITY_NOT_AN_OFFERING", "POTENTIAL_FUTURE_DILUTION", "AUTOMATIC_SHELF_WKSI"),
        "Automatic shelf registration",
    ),
    "SC 13D": (
        _T.OWNERSHIP_13D,
        _D.UNKNOWN,
        ("BENEFICIAL_OWNER_GT_5PCT", "ACTIVE_INTENT_POSSIBLE"),
        "Schedule 13D",
    ),
    "SCHEDULE 13D": (
        _T.OWNERSHIP_13D,
        _D.UNKNOWN,
        ("BENEFICIAL_OWNER_GT_5PCT", "ACTIVE_INTENT_POSSIBLE"),
        "Schedule 13D",
    ),
    "SC 13G": (_T.OWNERSHIP_13G, _D.UNKNOWN, ("BENEFICIAL_OWNER_GT_5PCT", "PASSIVE_HOLDER"), "Schedule 13G"),
    "SCHEDULE 13G": (
        _T.OWNERSHIP_13G,
        _D.UNKNOWN,
        ("BENEFICIAL_OWNER_GT_5PCT", "PASSIVE_HOLDER"),
        "Schedule 13G",
    ),
    "144": (
        _T.INSIDER_SALE,
        _D.UNKNOWN,
        ("FORM_144_NOTICE_NOT_EXECUTION",),
        "Notice of proposed sale (Form 144)",
    ),
}


def classify_8k_items(items: Iterable[str]) -> FormClassification:
    codes = [i.strip() for i in items if i and i.strip()]
    known = [c for c in ITEM_PRIORITY if c in codes]
    reasons = [reason_code("ITEM", c.replace(".", "_")) for c in codes if c not in ITEM_RULES and c != "9.01"]
    if not known:
        desc = "Current report" + (f" (items {', '.join(codes)})" if codes else "")
        return FormClassification(_T.CURRENT_REPORT_OTHER, _D.UNKNOWN, ("ITEMS_UNMAPPED", *reasons), desc)
    primary = known[0]
    ctype, direction, code, desc = ITEM_RULES[primary]
    for other in known[1:]:
        reasons.append(ITEM_RULES[other][2])
    if any(ITEM_RULES[o][1] is _D.POTENTIALLY_NEGATIVE for o in known[1:]) and direction is _D.UNKNOWN:
        reasons.append("SECONDARY_ITEM_POTENTIALLY_NEGATIVE")
    if primary == "2.02":
        reasons.append("SURPRISE_VS_EXPECTATIONS_UNKNOWN")
    return FormClassification(ctype, direction, (code, *reasons), f"Item {primary} {desc}")


def classify_form(form: str, items: Iterable[str] = ()) -> FormClassification:
    raw = form.upper().strip()
    base = raw.removesuffix("/A").strip()
    amended = ("AMENDED_FILING",) if raw.endswith("/A") else ()
    if base == "4":
        # metadata alone cannot distinguish an open-market buy from an award: type comes from the XML
        return FormClassification(
            None, _D.UNKNOWN, ("FORM4_DETAIL_REQUIRED", *amended), "Form 4", needs_detail=True
        )
    if base == "8-K":
        c = classify_8k_items(items)
        return FormClassification(c.catalyst_type, c.direction, (*c.reason_codes, *amended), c.description)
    if base.startswith("424B"):
        return FormClassification(
            _T.DILUTION_OFFERING,
            _D.UNKNOWN,
            ("PROSPECTUS_SECURITY_TYPE_UNVERIFIED", "POSSIBLE_DILUTION", reason_code("FORM", base), *amended),
            f"Prospectus ({base})",
        )
    rule = FORM_RULES.get(base)
    if rule is None:
        return FormClassification(None, _D.UNKNOWN, ("FORM_NOT_MAPPED",), base)
    ctype, direction, codes, desc = rule
    return FormClassification(ctype, direction, (*codes, *amended), desc)


# ---------------------------------------------------------------------------------------------
# catalysts
# ---------------------------------------------------------------------------------------------
def resolve_symbol(
    cik: str, on: date, tickers: tuple[str, ...], ticker_map: TickerMap | None
) -> tuple[str | None, float, str]:
    """(symbol, entity_confidence, reason) for a filer CIK at a point in time; never mixes entities."""
    if ticker_map is not None:
        sym = ticker_map.symbol_for(cik, on)
        if sym:
            return sym, 0.99, "SYMBOL_FROM_TICKER_MAP"
        return None, 0.95, "NO_SYMBOL_ON_FILING_DATE"
    if tickers:
        return tickers[0], 0.9, "SYMBOL_FROM_CURRENT_SUBMISSIONS"
    return None, 0.95, "NO_SYMBOL_KNOWN"


def filing_to_catalyst(
    meta: FilingMeta,
    *,
    first_seen_at: datetime,
    mode: AvailabilityMode | str = AvailabilityMode.BACKFILL,
    lag: timedelta = SEC_ACCEPTANCE_LAG,
    ticker_map: TickerMap | None = None,
    is_demo: bool = False,
) -> CatalystRecord | None:
    """Metadata-only catalyst for one filing; None for unmapped forms or Form 4 (needs XML detail)."""
    cls = classify_form(meta.form, meta.items)
    if cls.catalyst_type is None or cls.needs_detail:
        return None
    published = meta.published_at
    available = resolve_available_at(mode, first_seen_at=first_seen_at, published_at=published, lag=lag)
    reasons = list(cls.reason_codes)
    if meta.acceptance_at is None:
        reasons.append("ACCEPTANCE_TIME_MISSING_CONSERVATIVE")
    age = classify_actionable_age(available_at=available, published_at=published, event_at=None)
    if (
        AvailabilityMode(mode) is AvailabilityMode.LIVE
        and ensure_utc(first_seen_at) - published > STALE_DISCOVERY_AGE
    ):
        reasons.append("STALE_ON_DISCOVERY")
    symbol, entity_conf, entity_reason = resolve_symbol(
        meta.cik, published.astimezone(ET).date(), meta.tickers, ticker_map
    )
    reasons.append(entity_reason)
    period = meta.report_date if meta.report_date else None
    headline = f"{meta.company_name} filed {meta.form}: {cls.description}"
    return CatalystRecord(
        id=make_catalyst_id(Source.SEC_EDGAR, meta.accession_number, cls.catalyst_type),
        catalyst_type=cls.catalyst_type,
        source=Source.SEC_EDGAR,
        headline=headline,
        source_grade=SourceGrade.A_OFFICIAL_FILING,
        direction=cls.direction,
        entity_confidence=entity_conf,
        extraction_confidence=0.95,
        first_seen_at=first_seen_at,
        available_at=available,
        source_hash=metadata_hash(dict(meta.row) or {"accession": meta.accession_number, "form": meta.form}),
        parser_version=PARSER_VERSION,
        legal_classification=LegalClassification.PUBLIC_METADATA_LINK_ONLY,
        is_demo=is_demo,
        symbol=symbol,
        cik=meta.cik,
        event_at=None,
        published_at=published,
        canonical_url=meta.url,
        accession_number=meta.accession_number,
        publisher="SEC EDGAR",
        actionable_age=age,
        reason_codes=tuple(reasons),
        regulatory_flags=(reason_code("FORM", meta.base_form),),
        parsed={
            "form": meta.form,
            "items": list(meta.items),
            "filing_date": meta.filing_date,
            "report_date": period,
            "primary_document": meta.primary_document,
            "acceptance_time_zone_assumed": "America/New_York",
        },
    )


def submissions_to_catalysts(
    doc: SubmissionsDoc,
    *,
    first_seen_at: datetime,
    mode: AvailabilityMode | str = AvailabilityMode.BACKFILL,
    lag: timedelta = SEC_ACCEPTANCE_LAG,
    ticker_map: TickerMap | None = None,
    is_demo: bool = False,
    since: date | None = None,
) -> tuple[list[CatalystRecord], list[FilingMeta]]:
    """(metadata catalysts, filings that need document detail such as Form 4 XML)."""
    records: list[CatalystRecord] = []
    pending: list[FilingMeta] = []
    for meta in doc.filings:
        if since is not None and meta.filing_date < since:
            continue
        if classify_form(meta.form, meta.items).needs_detail:
            pending.append(meta)
            continue
        rec = filing_to_catalyst(
            meta, first_seen_at=first_seen_at, mode=mode, lag=lag, ticker_map=ticker_map, is_demo=is_demo
        )
        if rec is not None:
            records.append(rec)
    return records, pending


# ---------------------------------------------------------------------------------------------
# live client (tested with httpx.MockTransport only)
# ---------------------------------------------------------------------------------------------
class SecEdgarClient:
    def __init__(self, http: PoliteHttpClient):
        if not http.config.sec_compliant:
            raise UserAgentError(
                "SEC requires a descriptive User-Agent with contact email; use HttpClientConfig.for_sec"
            )
        self.http = http

    def submissions(self, cik: str) -> FetchResult:
        return self.http.get(SUBMISSIONS_URL.format(cik=normalize_cik(cik)), accept="application/json")

    def company_tickers(self) -> FetchResult:
        return self.http.get(COMPANY_TICKERS_URL, accept="application/json")

    def document(self, meta: FilingMeta, *, raw: bool = True) -> FetchResult:
        if not meta.primary_document:
            raise SubmissionsParseError(f"{meta.accession_number} has no primaryDocument")
        url = raw_document_url(meta.cik, meta.accession_number, meta.primary_document) if raw else meta.url
        if not is_sec_url(url):
            raise SubmissionsParseError("refusing to fetch a non-SEC document URL")
        return self.http.get(url)


__all__ = [
    "COMPANY_TICKERS_URL",
    "ITEM_RULES",
    "PARSER_VERSION",
    "SUBMISSIONS_URL",
    "FilingMeta",
    "FormClassification",
    "SecEdgarClient",
    "SubmissionsDoc",
    "SubmissionsParseError",
    "classify_8k_items",
    "classify_form",
    "filing_to_catalyst",
    "filing_url",
    "normalize_accession",
    "parse_acceptance_datetime",
    "parse_submissions",
    "raw_document_url",
    "resolve_symbol",
    "submissions_to_catalysts",
]
