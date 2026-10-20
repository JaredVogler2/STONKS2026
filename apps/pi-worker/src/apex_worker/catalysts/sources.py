"""Capability-gated catalyst source adapters used by :class:`CatalystPipeline`.

Every adapter reports ENABLED/DISABLED with a human-readable reason. Disabled sources are skipped and
shown as disabled - never faked. Adapters accept pre-fetched payloads (fixtures / replay / manual
imports) or a live client; live clients are only constructed when their requirements are met.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from apex_worker.catalysts import congress
from apex_worker.catalysts.earnings import EarningsCalendar, EntryStatus, entry_to_catalyst
from apex_worker.catalysts.entities import EntityResolver
from apex_worker.catalysts.form4 import PARSER_VERSION as FORM4_PARSER_VERSION
from apex_worker.catalysts.form4 import form4_to_catalyst, parse_form4
from apex_worker.catalysts.http import (
    FetchError,
    PoliteHttpClient,
    QuarantineRecord,
    UnsafeUrlError,
    UserAgentError,
    quarantine_record,
    safe_parse,
)
from apex_worker.catalysts.macro import FredClient, MacroReleaseEntry, release_to_catalyst
from apex_worker.catalysts.models import AvailabilityMode, CatalystRecord
from apex_worker.catalysts.news import (
    FeedAllowlist,
    FeedSource,
    NewsFeedClient,
    NotAllowlistedError,
    feed_to_catalysts,
)
from apex_worker.catalysts.sec import (
    PARSER_VERSION as SEC_PARSER_VERSION,
)
from apex_worker.catalysts.sec import (
    FilingMeta,
    SecEdgarClient,
    SubmissionsDoc,
    parse_submissions,
    raw_document_url,
    submissions_to_catalysts,
)
from apex_worker.catalysts.tickers import TickerMap


class SourceState(StrEnum):
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"


@dataclass(frozen=True)
class Capability:
    source: str
    status: SourceState
    reason: str
    automatic: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status.value,
            "reason": self.reason,
            "automatic": self.automatic,
        }


@dataclass
class CollectContext:
    first_seen_at: datetime
    mode: AvailabilityMode
    is_demo: bool
    ticker_map: TickerMap | None = None


@dataclass
class SourceBatch:
    records: list[CatalystRecord] = field(default_factory=list)
    quarantined: list[QuarantineRecord] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class CatalystSource(Protocol):
    name: str

    def capability(self) -> Capability: ...

    def collect(self, ctx: CollectContext) -> SourceBatch: ...


# ---------------------------------------------------------------------------------------------
# SEC EDGAR
# ---------------------------------------------------------------------------------------------
class SecFilingsSource:
    name = "sec_edgar"

    def __init__(
        self,
        *,
        submissions: Sequence[bytes] = (),
        form4_documents: Mapping[str, bytes] | None = None,
        client: SecEdgarClient | None = None,
        ciks: Iterable[str] = (),
        disabled_reason: str | None = None,
    ):
        self.submissions = list(submissions)
        self.form4_documents = dict(form4_documents or {})
        self.client = client
        self.ciks = list(ciks)
        self.disabled_reason = disabled_reason

    @classmethod
    def live(
        cls,
        http_factory: Callable[[str | None], PoliteHttpClient],
        user_agent: str | None,
        ciks: Iterable[str],
    ) -> SecFilingsSource:
        """Build a live source; a missing/invalid User-Agent yields a DISABLED source, not an error."""
        try:
            client = SecEdgarClient(http_factory(user_agent))
        except UserAgentError as exc:
            return cls(disabled_reason=str(exc))
        return cls(client=client, ciks=ciks)

    def capability(self) -> Capability:
        if self.disabled_reason:
            return Capability(self.name, SourceState.DISABLED, self.disabled_reason, automatic=False)
        if self.client is not None and self.ciks:
            return Capability(
                self.name, SourceState.ENABLED, "live EDGAR submissions (rate-limited)", automatic=True
            )
        if self.submissions:
            return Capability(
                self.name,
                SourceState.ENABLED,
                "offline submissions payloads (replay/fixture)",
                automatic=False,
            )
        return Capability(
            self.name,
            SourceState.DISABLED,
            "SEC requires a descriptive User-Agent with contact email (APEX_SEC_USER_AGENT) "
            "and a watchlist of CIKs",
            automatic=False,
        )

    def _payloads(self, batch: SourceBatch, now: datetime) -> list[tuple[str, bytes]]:
        out = [(f"fixture:submissions[{i}]", p) for i, p in enumerate(self.submissions)]
        if self.client is not None:
            for cik in self.ciks:
                try:
                    res = self.client.submissions(cik)
                except (FetchError, UnsafeUrlError) as exc:
                    batch.quarantined.append(
                        quarantine_record(
                            source=self.name,
                            url=getattr(exc, "url", cik),
                            reason_code=getattr(exc, "reason_code", "FETCH_FAILED"),
                            error=exc,
                            now=now,
                            http_status=getattr(exc, "status", None),
                        )
                    )
                    continue
                out.append((res.url, res.content))
        return out

    def _form4_bytes(self, meta: FilingMeta, batch: SourceBatch, now: datetime) -> bytes | None:
        if meta.accession_number in self.form4_documents:
            return self.form4_documents[meta.accession_number]
        if self.client is None:
            batch.notes.append(f"FORM4_DETAIL_UNAVAILABLE:{meta.accession_number}")
            return None
        try:
            return self.client.document(meta).content
        except (FetchError, UnsafeUrlError, ValueError) as exc:
            batch.quarantined.append(
                quarantine_record(
                    source=self.name,
                    url=raw_document_url(meta.cik, meta.accession_number, meta.primary_document or ""),
                    reason_code=getattr(exc, "reason_code", "FETCH_FAILED"),
                    error=exc,
                    now=now,
                    http_status=getattr(exc, "status", None),
                )
            )
            return None

    def collect(self, ctx: CollectContext) -> SourceBatch:
        batch = SourceBatch()
        now = ctx.first_seen_at
        for url, payload in self._payloads(batch, now):
            doc = safe_parse(
                payload,
                parse_submissions,
                source=self.name,
                url=url,
                now=now,
                parser_version=SEC_PARSER_VERSION,
            )
            if isinstance(doc, QuarantineRecord):
                batch.quarantined.append(doc)
                continue
            if not isinstance(doc, SubmissionsDoc):  # defensive: parser contract
                continue
            batch.notes.extend(f"ROW_SKIPPED:{e}" for e in doc.errors)
            records, pending = submissions_to_catalysts(
                doc, first_seen_at=now, mode=ctx.mode, ticker_map=ctx.ticker_map, is_demo=ctx.is_demo
            )
            batch.records.extend(records)
            for meta in pending:
                raw = self._form4_bytes(meta, batch, now)
                if raw is None:
                    continue
                form4_url = raw_document_url(
                    meta.cik, meta.accession_number, meta.primary_document or "form4.xml"
                )
                parsed = safe_parse(
                    raw,
                    parse_form4,
                    source=self.name,
                    url=form4_url,
                    now=now,
                    parser_version=FORM4_PARSER_VERSION,
                )
                if isinstance(parsed, QuarantineRecord):
                    batch.quarantined.append(parsed)
                    continue
                rec = form4_to_catalyst(
                    parsed,
                    raw=raw,
                    filing=meta,
                    first_seen_at=now,
                    mode=ctx.mode,
                    ticker_map=ctx.ticker_map,
                    is_demo=ctx.is_demo,
                )
                if rec is not None:
                    batch.records.append(rec)
        return batch


# ---------------------------------------------------------------------------------------------
# news
# ---------------------------------------------------------------------------------------------
class NewsSource:
    name = "news_rss"

    def __init__(
        self,
        allowlist: FeedAllowlist,
        resolver: EntityResolver,
        *,
        payloads: Mapping[str, bytes] | None = None,
        client: NewsFeedClient | None = None,
    ):
        self.allowlist = allowlist
        self.resolver = resolver
        self.payloads = dict(payloads or {})
        self.client = client

    def capability(self) -> Capability:
        if len(self.allowlist) == 0:
            return Capability(
                self.name, SourceState.DISABLED, "no license-reviewed feeds on the allowlist", automatic=False
            )
        if self.client is not None:
            return Capability(
                self.name,
                SourceState.ENABLED,
                f"{len(self.allowlist)} allowlisted feeds (live)",
                automatic=True,
            )
        if self.payloads:
            return Capability(
                self.name, SourceState.ENABLED, "offline feed payloads (replay/fixture)", automatic=False
            )
        return Capability(
            self.name, SourceState.DISABLED, "no feed client or payloads configured", automatic=False
        )

    def collect(self, ctx: CollectContext) -> SourceBatch:
        batch = SourceBatch()
        now = ctx.first_seen_at
        for url, content in self.payloads.items():
            try:
                feed = self.allowlist.get(url)
            except NotAllowlistedError as exc:
                batch.quarantined.append(
                    quarantine_record(
                        source=self.name, url=url, reason_code="NOT_ALLOWLISTED", error=exc, now=now
                    )
                )
                continue
            self._ingest(content, feed, ctx, batch)
        if self.client is not None:
            for feed in self.allowlist:
                if feed.url in self.payloads:
                    continue
                try:
                    _, res = self.client.fetch(feed.url)
                except (FetchError, UnsafeUrlError, NotAllowlistedError) as exc:
                    batch.quarantined.append(
                        quarantine_record(
                            source=self.name,
                            url=feed.url,
                            reason_code=getattr(exc, "reason_code", "FETCH_FAILED"),
                            error=exc,
                            now=now,
                            http_status=getattr(exc, "status", None),
                        )
                    )
                    continue
                self._ingest(res.content, feed, ctx, batch)
        return batch

    def _ingest(self, content: bytes, feed: FeedSource, ctx: CollectContext, batch: SourceBatch) -> None:
        nb = feed_to_catalysts(
            content, feed, self.resolver, first_seen_at=ctx.first_seen_at, mode=ctx.mode, is_demo=ctx.is_demo
        )
        batch.records.extend(nb.records)
        batch.quarantined.extend(nb.quarantined)
        batch.notes.extend(f"REJECTED:{r.reason_code}" for r in nb.rejected)


# ---------------------------------------------------------------------------------------------
# congress (manual import only)
# ---------------------------------------------------------------------------------------------
class CongressManualSource:
    name = "congress_manual"

    def __init__(self, csv_texts: Sequence[str] = ()):
        self.csv_texts = list(csv_texts)

    def capability(self) -> Capability:
        reason = congress.CAPABILITY["reason"]
        if self.csv_texts:
            return Capability(
                self.name, SourceState.ENABLED, "manual CSV import only. " + reason, automatic=False
            )
        return Capability(self.name, SourceState.DISABLED, reason, automatic=False)

    def collect(self, ctx: CollectContext) -> SourceBatch:
        batch = SourceBatch()
        for i, text in enumerate(self.csv_texts):
            try:
                result = congress.import_csv(
                    text,
                    first_seen_at=ctx.first_seen_at,
                    mode=ctx.mode,
                    ticker_map=ctx.ticker_map,
                    is_demo=ctx.is_demo,
                )
            except congress.CongressImportError as exc:
                batch.quarantined.append(
                    quarantine_record(
                        source=self.name,
                        url=f"manual-import:{i}",
                        reason_code="CSV_REJECTED",
                        error=exc,
                        now=ctx.first_seen_at,
                    )
                )
                continue
            batch.records.extend(result.records)
            for err in result.errors:
                batch.quarantined.append(
                    quarantine_record(
                        source=self.name,
                        url=f"manual-import:{i}:row-{err.row_number}",
                        reason_code=err.reason_code,
                        error=err.message,
                        now=ctx.first_seen_at,
                        parser_version=congress.PARSER_VERSION,
                    )
                )
        return batch


# ---------------------------------------------------------------------------------------------
# earnings / macro
# ---------------------------------------------------------------------------------------------
class EarningsCalendarSource:
    name = "earnings_calendar"

    def __init__(self, calendar: EarningsCalendar | None, symbols: Iterable[str] = ()):
        self.calendar = calendar
        self.symbols = list(symbols)

    def capability(self) -> Capability:
        if self.calendar is None or not self.symbols:
            return Capability(
                self.name,
                SourceState.DISABLED,
                "no free reliable earnings calendar configured; "
                "earnings status is UNKNOWN (never 'no earnings')",
                automatic=False,
            )
        return Capability(
            self.name,
            SourceState.ENABLED,
            "manual IR entries + SEC 8-K item 2.02 cadence estimates",
            automatic=False,
        )

    def collect(self, ctx: CollectContext) -> SourceBatch:
        batch = SourceBatch()
        if self.calendar is None:
            return batch
        for sym in self.symbols:
            for entry in self.calendar.entries_for(sym):
                if entry.status is EntryStatus.REPORTED:
                    continue
                batch.records.append(
                    entry_to_catalyst(
                        entry, first_seen_at=ctx.first_seen_at, mode=ctx.mode, is_demo=ctx.is_demo
                    )
                )
        return batch


class MacroCalendarSource:
    name = "macro_calendar"

    def __init__(self, releases: Sequence[MacroReleaseEntry] = ()):
        self.releases = list(releases)

    def capability(self) -> Capability:
        if not self.releases:
            return Capability(
                self.name, SourceState.DISABLED, "no release calendar entries configured", automatic=False
            )
        return Capability(
            self.name, SourceState.ENABLED, "manually curated official release calendar", automatic=False
        )

    def collect(self, ctx: CollectContext) -> SourceBatch:
        batch = SourceBatch()
        for entry in self.releases:
            try:
                batch.records.append(
                    release_to_catalyst(
                        entry, first_seen_at=ctx.first_seen_at, mode=ctx.mode, is_demo=ctx.is_demo
                    )
                )
            except ValueError as exc:
                batch.quarantined.append(
                    quarantine_record(
                        source=self.name,
                        url=entry.source_url,
                        reason_code="RELEASE_REJECTED",
                        error=exc,
                        now=ctx.first_seen_at,
                    )
                )
        return batch


class FredSeriesSource:
    """Capability holder for FRED/ALFRED vintages (features, not catalysts): emits no records."""

    name = "fred"

    def __init__(self, client: FredClient):
        self.client = client

    def capability(self) -> Capability:
        cap = self.client.capability()
        return Capability(
            self.name, SourceState(cap["status"]), cap["reason"], automatic=bool(cap["automatic"])
        )

    def collect(self, ctx: CollectContext) -> SourceBatch:
        return SourceBatch(notes=["FRED vintages are served as point-in-time features via VintageSeries"])


__all__ = [
    "Capability",
    "CatalystSource",
    "CollectContext",
    "CongressManualSource",
    "EarningsCalendarSource",
    "FredSeriesSource",
    "MacroCalendarSource",
    "NewsSource",
    "SecFilingsSource",
    "SourceBatch",
    "SourceState",
]
