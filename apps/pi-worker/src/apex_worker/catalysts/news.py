"""Allowlisted RSS 2.0 / Atom news feeds -> headline-only catalysts.

Stored per item: headline (<= 200 chars, HTML stripped), publisher, canonical URL (utm_* and click-id
tracking parameters removed), published_at, first_seen_at, entity + confidence. Article bodies and
descriptions are never stored. Only allowlisted https feeds are fetched; item links must be https on a
public host name (IP literals, localhost and private ranges are rejected - SSRF safety).
"""

from __future__ import annotations

import html
import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree.ElementTree import Element, tostring

from apex_worker.catalysts.entities import EntityResolver
from apex_worker.catalysts.http import (
    FetchResult,
    PoliteHttpClient,
    QuarantineRecord,
    Resolver,
    UnsafeUrlError,
    check_public_https_url,
    host_of,
    quarantine_record,
)
from apex_worker.catalysts.models import (
    NEWS_PUBLICATION_LAG,
    AvailabilityMode,
    CatalystRecord,
    CatalystType,
    LegalClassification,
    Source,
    SourceGrade,
    classify_actionable_age,
    clean_text,
    make_catalyst_id,
    resolve_available_at,
    sha256_hex,
)
from apex_worker.catalysts.taxonomy import TAXONOMY_VERSION, classify_headline
from apex_worker.catalysts.xmlsafe import child, children, local_name, parse_xml, text_of
from apex_worker.timeutil import ensure_utc

PARSER_VERSION = "news-feed/1.0.0"
TRACKING_PARAMS = frozenset(
    {"fbclid", "gclid", "dclid", "mc_cid", "mc_eid", "igshid", "yclid", "msclkid", "_hsenc"}
)
MAX_ITEMS_PER_FEED = 500
FUTURE_TOLERANCE = timedelta(minutes=5)


class FeedParseError(ValueError):
    pass


class NotAllowlistedError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------
# allowlist
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class FeedSource:
    feed_id: str
    publisher: str
    url: str
    license_note: str
    source_grade: SourceGrade
    link_hosts: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        check_public_https_url(self.url)
        object.__setattr__(self, "source_grade", SourceGrade(self.source_grade))
        object.__setattr__(self, "link_hosts", frozenset(h.lower() for h in self.link_hosts))
        if not self.license_note or not self.license_note.strip():
            raise ValueError(f"feed {self.feed_id}: a license/usage note is required before allowlisting")
        if not self.publisher.strip() or len(self.publisher) > 80:
            raise ValueError(f"feed {self.feed_id}: publisher must be 1-80 characters")


class FeedAllowlist:
    def __init__(self, sources: Iterable[FeedSource]):
        self._by_url: dict[str, FeedSource] = {}
        for src in sources:
            self._by_url[canonicalize_url(src.url)] = src

    @classmethod
    def from_json(cls, data: Mapping[str, Any] | bytes | str) -> FeedAllowlist:
        raw = json.loads(data) if isinstance(data, bytes | str) else data
        return cls(
            FeedSource(
                feed_id=f["feed_id"],
                publisher=f["publisher"],
                url=f["url"],
                license_note=f["license_note"],
                source_grade=SourceGrade(f["source_grade"]),
                link_hosts=frozenset(f.get("link_hosts", ())),
            )
            for f in raw["feeds"]
        )

    def get(self, url: str) -> FeedSource:
        try:
            check_public_https_url(url)
        except UnsafeUrlError as exc:
            raise NotAllowlistedError(f"feed URL rejected: {exc}") from exc
        src = self._by_url.get(canonicalize_url(url))
        if src is None:
            raise NotAllowlistedError(f"feed not on allowlist: {url}")
        return src

    def __iter__(self) -> Iterator[FeedSource]:
        return iter(self._by_url.values())

    def __len__(self) -> int:
        return len(self._by_url)


# ---------------------------------------------------------------------------------------------
# text / URL helpers
# ---------------------------------------------------------------------------------------------
class _Stripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def strip_html(text: str | None) -> str:
    """Remove tags/scripts and decode entities (handles HTML escaped inside XML titles)."""
    if not text:
        return ""
    parser = _Stripper()
    parser.feed(text)
    parser.close()
    return html.unescape(" ".join(parser.parts))


def canonicalize_url(url: str) -> str:
    """Lower-case scheme/host, drop fragment, default port and utm_*/click-id tracking parameters."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    netloc = host if parts.port in (None, 443, 80) else f"{host}:{parts.port}"
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query), ""))


def _parse_date(value: str | None, *, rfc822: bool) -> tuple[datetime | None, str | None]:
    if not value:
        return None, None
    value = value.strip()
    try:
        parsed = (
            parsedate_to_datetime(value) if rfc822 else datetime.fromisoformat(value.replace("Z", "+00:00"))
        )
    except (TypeError, ValueError, IndexError):
        try:  # some RSS feeds use ISO dates, some Atom feeds use RFC 822
            parsed = (
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                if rfc822
                else parsedate_to_datetime(value)
            )
        except (TypeError, ValueError, IndexError):
            return None, "PUBLISHED_AT_UNPARSEABLE"
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC), "PUBLISHED_TZ_ASSUMED_UTC"
    return parsed.astimezone(UTC), None


# ---------------------------------------------------------------------------------------------
# feed parsing
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class FeedItem:
    headline: str
    link: str | None
    guid: str | None
    published_at: datetime | None
    publisher: str
    raw_hash: str
    notes: tuple[str, ...] = ()


def _atom_link(entry: Element) -> str | None:
    links = children(entry, "link")
    for ln in links:
        if ln.get("rel", "alternate") == "alternate" and ln.get("href"):
            return ln.get("href")
    return links[0].get("href") if links and links[0].get("href") else None


def _rss_item(item: Element, feed: FeedSource) -> FeedItem:
    notes: list[str] = []
    published, note = _parse_date(text_of(child(item, "pubDate")), rfc822=True)
    if published is None:
        published, note2 = _parse_date(text_of(child(item, "date")), rfc822=False)  # dc:date
        note = note or note2
    if note:
        notes.append(note)
    src = child(item, "source")
    publisher = clean_text(text_of(src) or feed.publisher, 80)
    return FeedItem(
        headline=clean_text(strip_html(text_of(child(item, "title"))), 200),
        link=text_of(child(item, "link")),
        guid=text_of(child(item, "guid")),
        published_at=published,
        publisher=publisher,
        raw_hash=sha256_hex(tostring(item)),
        notes=tuple(notes),
    )


def _atom_entry(entry: Element, feed: FeedSource) -> FeedItem:
    notes: list[str] = []
    published, note = _parse_date(text_of(child(entry, "published")), rfc822=False)
    if published is None:
        published, note = _parse_date(text_of(child(entry, "updated")), rfc822=False)
        if published is not None:
            notes.append("PUBLISHED_FROM_UPDATED")
    if note:
        notes.append(note)
    author = text_of(child(child(entry, "source"), "title"))
    return FeedItem(
        headline=clean_text(strip_html(text_of(child(entry, "title"))), 200),
        link=_atom_link(entry),
        guid=text_of(child(entry, "id")),
        published_at=published,
        publisher=clean_text(author or feed.publisher, 80),
        raw_hash=sha256_hex(tostring(entry)),
        notes=tuple(notes),
    )


def parse_feed(content: bytes, feed: FeedSource) -> list[FeedItem]:
    """Parse RSS 2.0 or Atom 1.0 bytes. Raises FeedParseError for anything else (e.g. an HTML page)."""
    try:
        root = parse_xml(content)
    except ValueError as exc:
        raise FeedParseError(str(exc)) from exc
    kind = local_name(root.tag)
    if kind == "rss":
        channel = child(root, "channel")
        if channel is None:
            raise FeedParseError("RSS document without <channel>")
        elems, build = children(channel, "item"), _rss_item
    elif kind == "feed":
        elems, build = children(root, "entry"), _atom_entry
    else:
        raise FeedParseError(f"unsupported feed root <{kind}> (expected rss or Atom feed)")
    return [build(e, feed) for e in elems[:MAX_ITEMS_PER_FEED]]


# ---------------------------------------------------------------------------------------------
# items -> catalysts
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class RejectedItem:
    reason_code: str
    headline: str
    link: str | None


@dataclass
class NewsBatch:
    records: list[CatalystRecord] = field(default_factory=list)
    rejected: list[RejectedItem] = field(default_factory=list)
    quarantined: list[QuarantineRecord] = field(default_factory=list)


def _safe_link(link: str | None, feed: FeedSource) -> tuple[str | None, str | None]:
    if not link:
        return None, None
    try:
        check_public_https_url(link)
    except UnsafeUrlError:
        return None, "UNSAFE_OR_NON_HTTPS_LINK"
    canonical = canonicalize_url(link)
    if feed.link_hosts and host_of(canonical) not in feed.link_hosts:
        return None, "LINK_HOST_NOT_ALLOWLISTED"
    if len(canonical) > 500:
        return None, "LINK_TOO_LONG"
    return canonical, None


def item_to_catalyst(
    item: FeedItem,
    feed: FeedSource,
    resolver: EntityResolver,
    *,
    first_seen_at: datetime,
    mode: AvailabilityMode | str = AvailabilityMode.LIVE,
    lag: timedelta = NEWS_PUBLICATION_LAG,
    is_demo: bool = False,
) -> CatalystRecord | RejectedItem:
    if not item.headline:
        return RejectedItem("EMPTY_HEADLINE", "", item.link)
    canonical, link_problem = _safe_link(item.link, feed)
    if link_problem:
        return RejectedItem(link_problem, item.headline, None)
    entity = resolver.resolve(item.headline)
    tax = classify_headline(item.headline)
    primary = entity.primary
    if primary is None and tax.catalyst_type in {CatalystType.NEWS_OTHER}:
        reason = entity.rejected[0] if entity.rejected else "NO_ENTITY_NO_CATEGORY"
        return RejectedItem(reason, item.headline, canonical)
    first_seen_at = ensure_utc(first_seen_at)
    reasons = list(tax.reason_codes) + list(item.notes) + list(entity.rejected)
    published = item.published_at
    if published is not None and published > first_seen_at + FUTURE_TOLERANCE:
        reasons.append("PUBLISHED_AT_IN_FUTURE")
    if published is None:
        reasons.append("PUBLISHED_AT_MISSING")
    available = resolve_available_at(mode, first_seen_at=first_seen_at, published_at=published, lag=lag)
    if primary is None:
        reasons.append("NO_ISSUER_RESOLVED")
    elif len(entity.matches) > 1:
        reasons.append("MULTI_ENTITY_HEADLINE")
    identity = f"{canonical or item.guid or sha256_hex(item.headline)}|{item.publisher.lower()}"
    return CatalystRecord(
        id=make_catalyst_id(Source.NEWS_RSS, identity, tax.catalyst_type),
        catalyst_type=tax.catalyst_type,
        source=Source.NEWS_RSS,
        headline=item.headline,
        source_grade=feed.source_grade,
        direction=tax.direction,
        entity_confidence=primary.confidence if primary else 0.0,
        extraction_confidence=tax.extraction_confidence,
        first_seen_at=first_seen_at,
        available_at=available,
        source_hash=item.raw_hash,
        parser_version=PARSER_VERSION,
        legal_classification=LegalClassification.PUBLIC_METADATA_LINK_ONLY,
        is_demo=is_demo,
        symbol=primary.symbol if primary else None,
        cik=resolver.cik_for(primary.symbol) if primary else None,
        published_at=published,
        canonical_url=canonical,
        publisher=item.publisher,
        actionable_age=classify_actionable_age(available_at=available, published_at=published, event_at=None),
        reason_codes=tuple(reasons),
        parsed={
            "feed_id": feed.feed_id,
            "guid": item.guid[:200] if item.guid else None,
            "entity_method": primary.method if primary else None,
            "other_entities": list(entity.others),
            "taxonomy_rule": tax.rule,
            "taxonomy_version": TAXONOMY_VERSION,
            "full_text_stored": False,
        },
    )


def feed_to_catalysts(
    content: bytes,
    feed: FeedSource,
    resolver: EntityResolver,
    *,
    first_seen_at: datetime,
    mode: AvailabilityMode | str = AvailabilityMode.LIVE,
    lag: timedelta = NEWS_PUBLICATION_LAG,
    is_demo: bool = False,
) -> NewsBatch:
    """Parse a feed document; parse failures are quarantined (never raised)."""
    batch = NewsBatch()
    try:
        items = parse_feed(content, feed)
    except (FeedParseError, ValueError) as exc:
        batch.quarantined.append(
            quarantine_record(
                source="news_rss",
                url=feed.url,
                reason_code="PARSE_ERROR",
                error=exc,
                now=first_seen_at,
                content=content,
                parser_version=PARSER_VERSION,
            )
        )
        return batch
    for item in items:
        out = item_to_catalyst(
            item, feed, resolver, first_seen_at=first_seen_at, mode=mode, lag=lag, is_demo=is_demo
        )
        if isinstance(out, RejectedItem):
            batch.rejected.append(out)
        else:
            batch.records.append(out)
    return batch


# ---------------------------------------------------------------------------------------------
# live client (tested with httpx.MockTransport only)
# ---------------------------------------------------------------------------------------------
class NewsFeedClient:
    """Fetches ONLY allowlisted https feeds; with a DNS resolver, private resolutions are refused."""

    def __init__(
        self, http: PoliteHttpClient, allowlist: FeedAllowlist, *, dns_resolver: Resolver | None = None
    ):
        self.http = http
        self.allowlist = allowlist
        self.dns_resolver = dns_resolver

    def fetch(self, feed_url: str) -> tuple[FeedSource, FetchResult]:
        feed = self.allowlist.get(feed_url)
        check_public_https_url(feed.url, resolver=self.dns_resolver)
        result = self.http.get(
            feed.url, accept="application/rss+xml, application/atom+xml, application/xml;q=0.9"
        )
        return feed, result


__all__ = [
    "PARSER_VERSION",
    "FeedAllowlist",
    "FeedItem",
    "FeedParseError",
    "FeedSource",
    "NewsBatch",
    "NewsFeedClient",
    "NotAllowlistedError",
    "RejectedItem",
    "canonicalize_url",
    "feed_to_catalysts",
    "item_to_catalyst",
    "parse_feed",
    "strip_html",
]
