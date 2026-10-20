from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from apex_worker.catalysts.entities import EntityResolver
from apex_worker.catalysts.http import (
    HttpClientConfig,
    PoliteHttpClient,
    TokenBucket,
    UnsafeUrlError,
    check_public_https_url,
)
from apex_worker.catalysts.models import CatalystType, Direction, SourceGrade
from apex_worker.catalysts.news import (
    FeedAllowlist,
    FeedSource,
    NewsFeedClient,
    NotAllowlistedError,
    RejectedItem,
    canonicalize_url,
    feed_to_catalysts,
    item_to_catalyst,
    parse_feed,
    strip_html,
)
from apex_worker.contracts import validate

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "catalysts"
NOW = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)
RSS_URL = "https://feeds.demo-wire.example/markets.rss"
ATOM_URL = "https://newsroom.demo-regulator.example/atom.xml"


@pytest.fixture(scope="module")
def allowlist():
    return FeedAllowlist.from_json((FIX / "news_allowlist.json").read_text())


@pytest.fixture(scope="module")
def resolver():
    return EntityResolver.from_json((FIX / "entity_aliases.json").read_text())


@pytest.fixture(scope="module")
def rss_batch(allowlist, resolver):
    return feed_to_catalysts(
        (FIX / "news_rss_syndicated.xml").read_bytes(),
        allowlist.get(RSS_URL),
        resolver,
        first_seen_at=NOW,
        mode="backfill",
        is_demo=True,
    )


def test_rss_parse_headline_only(allowlist):
    items = parse_feed((FIX / "news_rss_syndicated.xml").read_bytes(), allowlist.get(RSS_URL))
    assert len(items) == 14
    first = items[0]
    assert first.headline.startswith("Demo Semiconductor One Raises")
    assert first.published_at == datetime(2026, 10, 14, 20, 15, tzinfo=UTC)
    assert items[1].published_at == datetime(2026, 10, 14, 20, 40, tzinfo=UTC)  # -0400 offset converted
    assert first.publisher == "Alpha Wire"
    assert not hasattr(first, "description")  # never stored


def test_html_is_stripped_from_titles(rss_batch):
    cfo = next(r for r in rss_batch.records if "CFO" in r.headline)
    assert cfo.headline == "Demo Bank Two CFO to Step Down"
    assert " ".join(strip_html("<b>A</b> &amp; <script>alert(1)</script>B&#39;s").split()) == "A & B's"
    assert strip_html("<em>x</em>") == "x"


def test_canonical_url_strips_tracking():
    url = "https://Alpha-Wire.example/markets/a?utm_source=rss&id=7&utm_medium=feed&fbclid=zz#frag"
    assert canonicalize_url(url) == "https://alpha-wire.example/markets/a?id=7"


def test_rss_catalysts_contract_valid(rss_batch):
    assert len(rss_batch.records) == 11
    for rec in rss_batch.records:
        payload = rec.to_contract()
        validate("Catalyst", payload)
        assert len(payload["headline"]) <= 200
        assert payload["legal_classification"] == "public_metadata_link_only"
        assert payload["parsed"]["full_text_stored"] is False
        assert "utm_" not in payload.get("canonical_url", "")
        assert payload["source"] == "news_rss"


def test_backfill_news_lag_and_missing_pubdate(rss_batch):
    first = next(
        r
        for r in rss_batch.records
        if r.publisher == "Alpha Wire" and r.catalyst_type is CatalystType.GUIDANCE_RAISE
    )
    assert first.available_at == first.published_at + timedelta(minutes=60)
    coo = next(r for r in rss_batch.records if "Chief Operating Officer" in r.headline)
    assert coo.published_at is None and coo.available_at == NOW
    assert "PUBLISHED_AT_MISSING" in coo.reason_codes


def test_unsafe_links_rejected(rss_batch):
    reasons = [r.reason_code for r in rss_batch.rejected]
    assert reasons.count("UNSAFE_OR_NON_HTTPS_LINK") == 2
    assert not any("10.0.0.5" in (r.canonical_url or "") for r in rss_batch.records)


def test_ambiguous_common_word_name_refused(rss_batch, resolver):
    assert any(r.reason_code == "AMBIGUOUS_ENTITY_UNQUALIFIED" for r in rss_batch.rejected)
    assert not any(r.headline.startswith("Beacon Expands") for r in rss_batch.records)
    qualified = next(r for r in rss_batch.records if "Beacon Logistics" in r.headline)
    assert qualified.symbol == "DMLOG3" and qualified.entity_confidence == pytest.approx(0.95)
    assert qualified.catalyst_type is CatalystType.GOVERNMENT_PROCUREMENT
    assert resolver.resolve("Beacon Corp. wins regional award").primary.symbol == "DMLOG3"
    assert resolver.resolve("A beacon of stability for Beacon investors").primary is None


@pytest.mark.parametrize(
    ("headline", "symbol", "method"),
    [
        ("Shares of $DMBIO4 jump premarket", "DMBIO4", "cashtag"),
        ("Demo Bank Two (NASDAQ: DMBANK2) posts results", "DMBANK2", "exchange_qualified"),
        ("NYSE:DMLOG3 extends rally", "DMLOG3", "exchange_qualified"),
        ("Demo Networks Five settles case", "DMNEWNM", "company_name"),
        ("DMSEMI1 shares rise", "DMSEMI1", "bare_ticker"),
    ],
)
def test_entity_resolution_methods(resolver, headline, symbol, method):
    match = resolver.resolve(headline).primary
    assert match is not None and match.symbol == symbol and match.method == method


def test_unknown_cashtag_never_invents_entity(resolver):
    res = resolver.resolve("$ZZZZ surges on takeover chatter")
    assert res.primary is None and "UNKNOWN_CASHTAG" in res.rejected


def test_multi_entity_headline_penalised(resolver):
    res = resolver.resolve("Demo Semiconductor One to acquire Demo Bio Four")
    assert res.primary.symbol == "DMSEMI1" and res.primary.confidence < 0.85
    assert res.others == ("DMBIO4",)


def test_atom_parse_and_classify(allowlist, resolver):
    batch = feed_to_catalysts(
        (FIX / "news_atom.xml").read_bytes(),
        allowlist.get(ATOM_URL),
        resolver,
        first_seen_at=NOW,
        mode="backfill",
    )
    assert len(batch.records) == 5 and not batch.rejected
    by_type = {r.catalyst_type: r for r in batch.records}
    crl = next(r for r in batch.records if "Complete Response Letter" in r.headline)
    assert crl.direction is Direction.POTENTIALLY_NEGATIVE and "<em>" not in crl.headline
    assert crl.published_at == datetime(2026, 10, 1, 0, 10, tzinfo=UTC)
    assert by_type[CatalystType.GUIDANCE_CUT].reason_codes[-1] == "PUBLISHED_FROM_UPDATED"
    assert all(r.source_grade is SourceGrade.B_OFFICIAL_RELEASE for r in batch.records)


def test_non_feed_document_quarantined(allowlist, resolver):
    batch = feed_to_catalysts(
        (FIX / "quarantine_weird_html_in_xml.xml").read_bytes(),
        allowlist.get(RSS_URL),
        resolver,
        first_seen_at=NOW,
    )
    assert not batch.records and len(batch.quarantined) == 1
    assert batch.quarantined[0].reason_code == "PARSE_ERROR"


def test_allowlist_rejects_unlisted_or_unsafe(allowlist):
    with pytest.raises(NotAllowlistedError):
        allowlist.get("https://not-on-list.example/rss")
    with pytest.raises(NotAllowlistedError):
        allowlist.get("http://feeds.demo-wire.example/markets.rss")
    assert allowlist.get(RSS_URL + "?utm_source=x").feed_id == "demo-wire-aggregator"


def test_feed_source_requires_https_public_host_and_license():
    with pytest.raises(UnsafeUrlError):
        FeedSource("x", "X", "https://192.168.1.10/rss", "ok", SourceGrade.D_OTHER)
    with pytest.raises(ValueError, match="license"):
        FeedSource("x", "X", "https://feeds.x.example/rss", " ", SourceGrade.D_OTHER)


def test_link_host_restriction(resolver):
    feed = FeedSource(
        "r",
        "R",
        "https://feeds.r.example/rss",
        "fixture",
        SourceGrade.C_REPUTABLE_WIRE,
        frozenset({"r.example"}),
    )
    items = parse_feed((FIX / "news_rss_syndicated.xml").read_bytes(), feed)
    out = item_to_catalyst(items[0], feed, resolver, first_seen_at=NOW)
    assert isinstance(out, RejectedItem) and out.reason_code == "LINK_HOST_NOT_ALLOWLISTED"


def test_future_dated_item_flagged_and_not_available_early(allowlist, resolver):
    xml = (
        b'<rss version="2.0"><channel><item><title>Demo Bank Two Announces Share Repurchase Program</title>'
        b"<link>https://beta-business.example/n/1</link>"
        b"<pubDate>Fri, 23 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>"
    )
    rec = feed_to_catalysts(xml, allowlist.get(RSS_URL), resolver, first_seen_at=NOW, mode="live").records[0]
    assert "PUBLISHED_AT_IN_FUTURE" in rec.reason_codes
    assert rec.available_at == rec.published_at  # never usable before its own publication time


def test_news_client_fetches_only_allowlisted_feeds(allowlist):
    payload = (FIX / "news_rss_syndicated.xml").read_bytes()
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=payload)

    class NoSleep:
        def monotonic(self):
            return 0.0

        def sleep(self, s):
            pass

        def now(self):
            return NOW

    http = PoliteHttpClient(
        HttpClientConfig.for_public("APEX feed reader (fixture) ops@example.com"),
        transport=httpx.MockTransport(handler),
        bucket=TokenBucket(1.0, time_source=NoSleep()),
        time_source=NoSleep(),
        rng=random.Random(3),
        url_guard=check_public_https_url,
    )
    client = NewsFeedClient(http, allowlist, dns_resolver=lambda host: ["93.184.216.34"])
    feed, result = client.fetch(RSS_URL)
    assert feed.feed_id == "demo-wire-aggregator" and result.content == payload
    with pytest.raises(NotAllowlistedError):
        client.fetch("https://elsewhere.example/rss")
    rebinding = NewsFeedClient(http, allowlist, dns_resolver=lambda host: ["127.0.0.1"])
    with pytest.raises(UnsafeUrlError):
        rebinding.fetch(RSS_URL)
    assert seen == [RSS_URL]
