"""Form 4 (ownershipDocument XML) parsing and insider-transaction classification.

Classification rules (deterministic, deliberately conservative):

* ``P`` open-market purchase -> ``insider_open_market_purchase``; ``potentially_positive`` ONLY when the
  shares were acquired (``A``), a price is present and the trade is not flagged as a Rule 10b5-1 plan.
* ``S`` open-market sale -> ``insider_sale``; planned 10b5-1 sales are ``unknown`` (PLANNED_10B5_1),
  exercise-and-sell is ``mixed``, only a clearly discretionary (plan box explicitly unchecked, large,
  >=25% of holdings) sale is ``potentially_negative``; otherwise ``mixed``.
* ``A`` award, ``M``/``X`` exercise, ``F`` tax withholding, ``G`` gift, ``C`` conversion and the other
  non-market codes -> ``insider_award_or_exercise`` with direction ``unknown``. Never bullish.

All transactions in one filing aggregate into one catalyst. ``event_at`` is the (earliest) transaction
date; ``published_at`` is the EDGAR acceptance time of the filing. They are never conflated.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

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
    et_date_start,
    make_catalyst_id,
    reason_code,
    resolve_available_at,
    sha256_hex,
)
from apex_worker.catalysts.sec import FilingMeta
from apex_worker.catalysts.tickers import TickerMap, normalize_cik, normalize_symbol
from apex_worker.catalysts.xmlsafe import child, children, local_name, parse_xml, path_text, text_of
from apex_worker.timeutil import ET

PARSER_VERSION = "form4/1.0.0"
MATERIAL_NOTIONAL = Decimal(100_000)
LARGE_NOTIONAL = Decimal(1_000_000)
DISCRETIONARY_SALE_FRACTION = Decimal("0.25")
_10B5_RE = re.compile(r"10\s*b\s*[-‐‑‒–]?\s*5\s*[-‐‑‒–]?\s*1", re.IGNORECASE)
_CEO_RE = re.compile(r"\b(chief executive|ceo)\b", re.IGNORECASE)
_CFO_RE = re.compile(r"\b(chief financial|cfo)\b", re.IGNORECASE)
CODE_REASONS = {
    "A": "AWARD_CODE_A",
    "M": "OPTION_EXERCISE_CODE_M",
    "X": "OPTION_EXERCISE_CODE_X",
    "F": "TAX_WITHHOLDING_CODE_F",
    "G": "GIFT_CODE_G",
    "C": "CONVERSION_CODE_C",
    "D": "DISPOSITION_TO_ISSUER_CODE_D",
    "J": "OTHER_ACQ_DISP_CODE_J",
    "W": "WILL_OR_DESCENT_CODE_W",
    "I": "DISCRETIONARY_PLAN_CODE_I",
    "K": "EQUITY_SWAP_CODE_K",
    "L": "SMALL_ACQUISITION_CODE_L",
    "U": "TENDER_DISPOSITION_CODE_U",
    "V": "VOLUNTARY_REPORT_CODE_V",
    "Z": "VOTING_TRUST_CODE_Z",
    "E": "DERIVATIVE_EXPIRY_CODE_E",
    "H": "DERIVATIVE_EXPIRY_CODE_H",
    "O": "OTM_EXERCISE_CODE_O",
}
COMPENSATION_CODES = frozenset({"A", "M", "X", "F"})
EXERCISE_CODES = frozenset({"M", "X", "C", "O"})


class Form4ParseError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------
# document model
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ReportingOwner:
    cik: str | None
    name: str
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool
    is_other: bool
    officer_title: str | None

    def role_codes(self) -> list[str]:
        codes = []
        if self.is_officer:
            codes.append("ROLE_OFFICER")
            if self.officer_title and _CEO_RE.search(self.officer_title):
                codes.append("ROLE_CEO")
            if self.officer_title and _CFO_RE.search(self.officer_title):
                codes.append("ROLE_CFO")
        if self.is_director:
            codes.append("ROLE_DIRECTOR")
        if self.is_ten_percent_owner:
            codes.append("ROLE_TEN_PERCENT_OWNER")
        if self.is_other and not codes:
            codes.append("ROLE_OTHER")
        return codes

    def role_label(self) -> str:
        parts = []
        if self.is_officer:
            parts.append(self.officer_title or "Officer")
        if self.is_director:
            parts.append("Director")
        if self.is_ten_percent_owner:
            parts.append("10% owner")
        return ", ".join(parts) or "Other"


@dataclass(frozen=True)
class Form4Transaction:
    table: str  # "non_derivative" | "derivative"
    security_title: str
    transaction_date: date
    code: str
    shares: Decimal | None
    price: Decimal | None
    acquired_disposed: str | None
    shares_owned_after: Decimal | None
    direct_indirect: str | None
    nature_of_ownership: str | None
    footnote_ids: tuple[str, ...]
    equity_swap: bool
    planned_10b5_1: bool

    @property
    def notional(self) -> Decimal | None:
        if self.shares is None or self.price is None:
            return None
        return (self.shares * self.price).quantize(Decimal("0.01"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "security_title": self.security_title,
            "transaction_date": self.transaction_date,
            "code": self.code,
            "shares": self.shares,
            "price": self.price,
            "notional": self.notional,
            "acquired_disposed": self.acquired_disposed,
            "shares_owned_after": self.shares_owned_after,
            "direct_indirect": self.direct_indirect,
            "nature_of_ownership": self.nature_of_ownership,
            "footnote_ids": list(self.footnote_ids),
            "planned_10b5_1": self.planned_10b5_1,
        }


@dataclass(frozen=True)
class Form4Document:
    document_type: str
    schema_version: str | None
    period_of_report: date | None
    issuer_cik: str
    issuer_name: str
    issuer_symbol: str | None
    owners: tuple[ReportingOwner, ...]
    transactions: tuple[Form4Transaction, ...]
    footnotes: dict[str, str] = field(default_factory=dict)
    aff10b5one: bool | None = None


# ---------------------------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------------------------
def _bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "y", "yes"}


def _decimal(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    try:
        out = Decimal(value.strip().replace(",", "").replace("$", ""))
    except InvalidOperation as exc:
        raise Form4ParseError(f"non-numeric amount {value!r}") from exc
    if not out.is_finite() or out < 0:
        raise Form4ParseError(f"invalid amount {value!r}")
    return out


def _date(value: str | None, what: str) -> date:
    if not value:
        raise Form4ParseError(f"missing {what}")
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError as exc:
        raise Form4ParseError(f"invalid {what} {value!r}") from exc


def _footnote_ids(elem: Any) -> list[str]:
    return [fn.get("id", "") for fn in elem.iter() if local_name(fn.tag) == "footnoteId" and fn.get("id")]


def _parse_transaction(
    elem: Any, table: str, footnotes: dict[str, str], doc_flag: bool | None
) -> Form4Transaction:
    coding = child(elem, "transactionCoding")
    code = (path_text(coding, "transactionCode") or "").strip().upper()
    if not code:
        raise Form4ParseError("transaction without transactionCode")
    amounts = child(elem, "transactionAmounts")
    fids = tuple(dict.fromkeys(_footnote_ids(elem)))
    cites_plan = any(_10B5_RE.search(footnotes.get(f, "")) for f in fids)
    planned = cites_plan or (doc_flag is True and code in {"P", "S"} and not _any_plan_footnote(footnotes))
    return Form4Transaction(
        table=table,
        security_title=path_text(elem, "securityTitle", "value") or "",
        transaction_date=_date(path_text(elem, "transactionDate", "value"), "transactionDate"),
        code=code,
        shares=_decimal(path_text(amounts, "transactionShares", "value")),
        price=_decimal(path_text(amounts, "transactionPricePerShare", "value")),
        acquired_disposed=(path_text(amounts, "transactionAcquiredDisposedCode", "value") or "").upper()
        or None,
        shares_owned_after=_decimal(
            path_text(elem, "postTransactionAmounts", "sharesOwnedFollowingTransaction", "value")
        ),
        direct_indirect=(
            path_text(elem, "ownershipNature", "directOrIndirectOwnership", "value") or ""
        ).upper()
        or None,
        nature_of_ownership=path_text(elem, "ownershipNature", "natureOfOwnership", "value"),
        footnote_ids=fids,
        equity_swap=_bool(path_text(coding, "equitySwapInvolved")),
        planned_10b5_1=planned,
    )


def _any_plan_footnote(footnotes: dict[str, str]) -> bool:
    return any(_10B5_RE.search(t) for t in footnotes.values())


def parse_form4(content: bytes | str) -> Form4Document:
    """Parse an ownershipDocument. Raises Form4ParseError/ValueError for anything malformed."""
    try:
        root = parse_xml(content)
    except ValueError as exc:
        raise Form4ParseError(str(exc)) from exc
    if local_name(root.tag) != "ownershipDocument":
        raise Form4ParseError(f"root element is <{local_name(root.tag)}>, not <ownershipDocument>")
    doc_type = (path_text(root, "documentType") or "").strip()
    if doc_type not in {"4", "4/A"}:
        raise Form4ParseError(f"documentType {doc_type!r} is not a Form 4")
    footnotes = {
        fn.get("id", ""): (text_of(fn) or "")
        for fn in children(child(root, "footnotes"), "footnote")
        if fn.get("id")
    }
    flag_text = path_text(root, "aff10b5One")
    doc_flag = None if flag_text is None else _bool(flag_text)
    issuer = child(root, "issuer")
    issuer_cik = path_text(issuer, "issuerCik")
    if not issuer_cik:
        raise Form4ParseError("issuerCik missing")
    owners = []
    for ro in children(root, "reportingOwner"):
        rel = child(ro, "reportingOwnerRelationship")
        owners.append(
            ReportingOwner(
                cik=path_text(ro, "reportingOwnerId", "rptOwnerCik"),
                name=path_text(ro, "reportingOwnerId", "rptOwnerName") or "Unknown reporting owner",
                is_director=_bool(path_text(rel, "isDirector")),
                is_officer=_bool(path_text(rel, "isOfficer")),
                is_ten_percent_owner=_bool(path_text(rel, "isTenPercentOwner")),
                is_other=_bool(path_text(rel, "isOther")),
                officer_title=path_text(rel, "officerTitle"),
            )
        )
    if not owners:
        raise Form4ParseError("no reportingOwner")
    txs: list[Form4Transaction] = []
    for t in children(child(root, "nonDerivativeTable"), "nonDerivativeTransaction"):
        txs.append(_parse_transaction(t, "non_derivative", footnotes, doc_flag))
    for t in children(child(root, "derivativeTable"), "derivativeTransaction"):
        txs.append(_parse_transaction(t, "derivative", footnotes, doc_flag))
    period = path_text(root, "periodOfReport")
    return Form4Document(
        document_type=doc_type,
        schema_version=path_text(root, "schemaVersion"),
        period_of_report=_date(period, "periodOfReport") if period else None,
        issuer_cik=normalize_cik(issuer_cik),
        issuer_name=path_text(issuer, "issuerName") or f"CIK {issuer_cik}",
        issuer_symbol=normalize_symbol(path_text(issuer, "issuerTradingSymbol")),
        owners=tuple(owners),
        transactions=tuple(txs),
        footnotes=footnotes,
        aff10b5one=doc_flag,
    )


# ---------------------------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Form4Classification:
    catalyst_type: CatalystType
    direction: Direction
    reason_codes: tuple[str, ...]
    materiality: str  # "high" | "medium" | "low"
    totals: dict[str, Any]


def _sum(values: list[Decimal | None]) -> Decimal | None:
    if not values or any(v is None for v in values):
        return None
    return sum((v for v in values if v is not None), Decimal(0))


def _notional_codes(total: Decimal | None) -> tuple[list[str], str]:
    if total is None:
        return ["NOTIONAL_UNKNOWN"], "low"
    if total >= LARGE_NOTIONAL:
        return ["NOTIONAL_GE_1M", "NOTIONAL_GE_100K"], "high"
    if total >= MATERIAL_NOTIONAL:
        return ["NOTIONAL_GE_100K"], "medium"
    return ["NOTIONAL_LT_100K"], "low"


def classify_form4(doc: Form4Document) -> Form4Classification | None:
    """Aggregate every transaction in the filing into a single classification (None if no transactions)."""
    if not doc.transactions:
        return None
    nd = [t for t in doc.transactions if t.table == "non_derivative"]
    codes = Counter(t.code for t in doc.transactions)
    purchases = [t for t in nd if t.code == "P"]
    sales = [t for t in nd if t.code == "S"]
    roles = sorted({c for o in doc.owners for c in o.role_codes()})
    reasons: list[str] = []
    totals: dict[str, Any] = {
        "codes": dict(sorted(codes.items())),
        "purchase_shares": _sum([t.shares for t in purchases]) if purchases else Decimal(0),
        "purchase_notional": _sum([t.notional for t in purchases]) if purchases else Decimal(0),
        "sale_shares": _sum([t.shares for t in sales]) if sales else Decimal(0),
        "sale_notional": _sum([t.notional for t in sales]) if sales else Decimal(0),
        "transaction_count": len(doc.transactions),
    }
    if any(t.direct_indirect == "I" for t in doc.transactions):
        reasons.append("INDIRECT_OWNERSHIP_PRESENT")
    plan_any = any(t.planned_10b5_1 for t in purchases + sales)

    if purchases:
        ctype = CatalystType.INSIDER_OPEN_MARKET_PURCHASE
        notional_codes, materiality = _notional_codes(totals["purchase_notional"])
        reasons = ["OPEN_MARKET_PURCHASE_CODE_P", *notional_codes, *roles, *reasons]
        if any(t.acquired_disposed != "A" for t in purchases):
            direction = Direction.UNKNOWN
            reasons.append("INCONSISTENT_ACQUIRED_DISPOSED_CODE")
        elif all(t.planned_10b5_1 for t in purchases):
            direction = Direction.UNKNOWN
            reasons.append("PLANNED_10B5_1")
        elif totals["purchase_notional"] is None or totals["purchase_notional"] == 0:
            direction = Direction.UNKNOWN
            reasons.append("PRICE_MISSING")
        else:
            direction = Direction.POTENTIALLY_POSITIVE
            if plan_any:
                reasons.append("PARTIALLY_PLANNED_10B5_1")
        if sales:
            direction = Direction.MIXED
            reasons.append("PURCHASES_AND_SALES_SAME_FILING")
        return Form4Classification(ctype, direction, tuple(reasons), materiality, totals)

    if sales:
        ctype = CatalystType.INSIDER_SALE
        notional_codes, materiality = _notional_codes(totals["sale_notional"])
        reasons = ["OPEN_MARKET_SALE_CODE_S", *notional_codes, *roles, *reasons]
        final_after = sales[-1].shares_owned_after
        sold = totals["sale_shares"] or Decimal(0)
        fraction = None
        if final_after is not None and sold > 0:
            fraction = (sold / (final_after + sold)).quantize(Decimal("0.0001"))
            totals["sold_fraction_of_holdings"] = fraction
        if all(t.planned_10b5_1 for t in sales):
            direction = Direction.UNKNOWN
            reasons.append("PLANNED_10B5_1")
        elif any(codes.get(c) for c in EXERCISE_CODES):
            direction = Direction.MIXED
            reasons.append("SALE_AFTER_EXERCISE")
        elif (
            doc.aff10b5one is False
            and fraction is not None
            and fraction >= DISCRETIONARY_SALE_FRACTION
            and (totals["sale_notional"] or 0) >= LARGE_NOTIONAL
        ):
            direction = Direction.POTENTIALLY_NEGATIVE
            reasons += ["DISCRETIONARY_SALE", "SOLD_GE_25PCT_OF_HOLDINGS"]
        else:
            direction = Direction.MIXED
            reasons.append("DISCRETION_UNCLEAR")
            if plan_any:
                reasons.append("PARTIALLY_PLANNED_10B5_1")
        return Form4Classification(ctype, direction, tuple(reasons), materiality, totals)

    code_reasons = [CODE_REASONS.get(c, reason_code("CODE", c)) for c in sorted(codes)]
    extra = ["NON_DISCRETIONARY_COMPENSATION"] if set(codes) & COMPENSATION_CODES else []
    return Form4Classification(
        CatalystType.INSIDER_AWARD_OR_EXERCISE,
        Direction.UNKNOWN,
        ("NOT_OPEN_MARKET", *code_reasons, *extra, *roles, *reasons),
        "low",
        totals,
    )


def _business_days_between(start: date, end: date) -> int:
    """Weekdays in (start, end]; an approximation (exchange holidays ignored) used only for lag flags."""
    days, cur = 0, start
    while cur < end:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


def _fmt_money(value: Decimal | None) -> str:
    return "n/a" if value is None else f"${value:,.0f}"


def _headline(doc: Form4Document, cls: Form4Classification, symbol: str | None) -> str:
    owner = doc.owners[0]
    who = f"{owner.name} ({owner.role_label()})" + (f" +{len(doc.owners) - 1}" if len(doc.owners) > 1 else "")
    sym = symbol or doc.issuer_name
    if cls.catalyst_type is CatalystType.INSIDER_OPEN_MARKET_PURCHASE:
        what = (
            f"open-market purchase of {cls.totals['purchase_shares'] or 0:,.0f} {sym} shares "
            f"(~{_fmt_money(cls.totals['purchase_notional'])})"
        )
    elif cls.catalyst_type is CatalystType.INSIDER_SALE:
        notional = _fmt_money(cls.totals["sale_notional"])
        what = f"sale of {cls.totals['sale_shares'] or 0:,.0f} {sym} shares (~{notional})"
    else:
        codes = ", ".join(f"{c}x{n}" for c, n in cls.totals["codes"].items())
        what = f"non-open-market {sym} award/exercise activity (codes {codes})"
    plan = " under 10b5-1 plan" if "PLANNED_10B5_1" in cls.reason_codes else ""
    return f"Form 4: {who} {what}{plan}"


def form4_to_catalyst(
    doc: Form4Document,
    *,
    raw: bytes,
    first_seen_at: datetime,
    filing: FilingMeta | None = None,
    accession_number: str | None = None,
    published_at: datetime | None = None,
    mode: AvailabilityMode | str = AvailabilityMode.BACKFILL,
    lag: timedelta = SEC_ACCEPTANCE_LAG,
    ticker_map: TickerMap | None = None,
    is_demo: bool = False,
) -> CatalystRecord | None:
    """One catalyst per Form 4 filing. ``filing`` supplies acceptance time, accession and URL."""
    cls = classify_form4(doc)
    if cls is None:
        return None
    accession = filing.accession_number if filing else accession_number
    if not accession:
        raise Form4ParseError("accession number required to identify a Form 4 catalyst")
    published = filing.published_at if filing else published_at
    if published is None:
        raise Form4ParseError(
            "filing acceptance time (published_at) required; transaction date is not publication"
        )
    available = resolve_available_at(mode, first_seen_at=first_seen_at, published_at=published, lag=lag)
    tx_dates = sorted({t.transaction_date for t in doc.transactions})
    event_at = et_date_start(tx_dates[0])
    reasons = list(cls.reason_codes)
    filed_day = published.astimezone(ET).date()
    lag_bd = _business_days_between(tx_dates[0], filed_day)
    lag_days = (filed_day - tx_dates[0]).days
    if lag_days >= 0:
        reasons.append(f"TRANSACTION_TO_FILING_LAG_{lag_days}D")
    else:
        reasons.append("TRANSACTION_DATE_AFTER_FILING")
    if lag_bd > 2:
        reasons.append("LATE_FILING_GT_2_BUSINESS_DAYS")
    entity_conf = 0.99
    symbol = doc.issuer_symbol
    if filing is not None and normalize_cik(filing.cik) != doc.issuer_cik:
        reasons.append("ISSUER_CIK_MISMATCH")
        entity_conf = 0.5
    if ticker_map is not None:
        mapped = ticker_map.symbol_for(doc.issuer_cik, tx_dates[0])
        if symbol and mapped and mapped != symbol:
            reasons.append("SYMBOL_CIK_MISMATCH")
            entity_conf = min(entity_conf, 0.6)
            symbol = mapped
        elif symbol is None:
            symbol = mapped
    if doc.document_type == "4/A":
        reasons.append("AMENDED_FILING")
    url = filing.url if filing else None
    return CatalystRecord(
        id=make_catalyst_id(Source.SEC_EDGAR, accession, cls.catalyst_type),
        catalyst_type=cls.catalyst_type,
        source=Source.SEC_EDGAR,
        headline=_headline(doc, cls, symbol),
        source_grade=SourceGrade.A_OFFICIAL_FILING,
        direction=cls.direction,
        entity_confidence=entity_conf,
        extraction_confidence=0.95,
        first_seen_at=first_seen_at,
        available_at=available,
        source_hash=sha256_hex(raw),
        parser_version=PARSER_VERSION,
        legal_classification=LegalClassification.PUBLIC_METADATA_LINK_ONLY,
        is_demo=is_demo,
        symbol=symbol,
        cik=doc.issuer_cik,
        event_at=event_at,
        published_at=published,
        canonical_url=url,
        accession_number=accession,
        publisher="SEC EDGAR",
        actionable_age=classify_actionable_age(
            available_at=available, published_at=published, event_at=event_at
        ),
        reason_codes=tuple(reasons),
        regulatory_flags=("FORM_4", "SECTION_16_INSIDER"),
        parsed={
            "materiality": cls.materiality,
            "event_date_precision": "date",
            "transaction_dates": tx_dates,
            "owners": [
                {"cik": o.cik, "name": o.name, "roles": o.role_codes(), "officer_title": o.officer_title}
                for o in doc.owners
            ],
            "aff10b5One": doc.aff10b5one,
            "totals": cls.totals,
            "transactions": [t.to_dict() for t in doc.transactions],
        },
    )


__all__ = [
    "PARSER_VERSION",
    "Form4Classification",
    "Form4Document",
    "Form4ParseError",
    "Form4Transaction",
    "ReportingOwner",
    "classify_form4",
    "form4_to_catalyst",
    "parse_form4",
]
