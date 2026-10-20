from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apex_worker.catalysts.dedupe import dedupe, jaccard, normalize_headline
from apex_worker.catalysts.entities import EntityResolver
from apex_worker.catalysts.models import CatalystType, Direction, SourceGrade, make_catalyst_id
from apex_worker.catalysts.news import FeedAllowlist, feed_to_catalysts
from apex_worker.contracts import validate

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "catalysts"
NOW = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def rss_records():
    allowlist = FeedAllowlist.from_json((FIX / "news_allowlist.json").read_text())
    resolver = EntityResolver.from_json((FIX / "entity_aliases.json").read_text())
    batch = feed_to_catalysts(
        (FIX / "news_rss_syndicated.xml").read_bytes(),
        allowlist.get("https://feeds.demo-wire.example/markets.rss"),
        resolver,
        first_seen_at=NOW,
        mode="backfill",
        is_demo=True,
    )
    return batch.records


def _guidance(records):
    return [r for r in records if r.catalyst_type is CatalystType.GUIDANCE_RAISE]


def test_headline_republished_by_five_outlets_is_one_cluster(rss_records):
    syndicated = _guidance(rss_records)
    assert len(syndicated) == 5
    assert len({r.publisher for r in syndicated}) == 5
    result = dedupe(syndicated)
    assert len(result.representatives) == 1
    assert len(result.clusters) == 1 and len(result.clusters[0].members) == 5
    rep = result.representatives[0]
    validate("Catalyst", rep.to_contract())
    assert {"MULTI_SOURCE_CONFIRMED", "SOURCE_COUNT_5"} <= set(rep.reason_codes)
    assert rep.parsed["cluster"]["independent_publishers"] == 5


def test_cluster_earliest_available_wins_and_direction_unchanged(rss_records):
    syndicated = _guidance(rss_records)
    rep = dedupe(syndicated).representatives[0]
    assert rep.available_at == min(r.available_at for r in syndicated)
    assert rep.publisher == "Alpha Wire"  # 20:15 UTC, the first outlet
    assert rep.direction is Direction.POTENTIALLY_POSITIVE  # reliability up, direction NOT changed
    assert rep.cluster_id == "clu_" + rep.id[4:]


def test_full_feed_dedupe_keeps_distinct_stories(rss_records):
    result = dedupe(rss_records)
    assert len(result.representatives) == len(rss_records) - 4
    assert len({r.id for r in result.representatives}) == len(result.representatives)


def test_same_publisher_twice_is_not_multi_source(rss_records):
    first = _guidance(rss_records)[0]
    copy = replace(
        first,
        id=make_catalyst_id("news_rss", "https://alpha-wire.example/other-path|alpha wire", "guidance_raise"),
        canonical_url="https://alpha-wire.example/other-path",
        available_at=first.available_at + timedelta(hours=1),
        published_at=first.published_at + timedelta(hours=1),
    )
    rep = dedupe([first, copy]).representatives[0]
    assert "MULTI_SOURCE_CONFIRMED" not in rep.reason_codes and "CLUSTER_SIZE_2" in rep.reason_codes


def test_outside_36h_window_not_clustered(rss_records):
    first = _guidance(rss_records)[0]
    later = replace(
        first,
        id=make_catalyst_id("news_rss", "https://beta-business.example/x|beta", "guidance_raise"),
        publisher="Beta Business News",
        canonical_url="https://beta-business.example/x",
        published_at=first.published_at + timedelta(hours=37),
        available_at=first.available_at + timedelta(hours=37),
    )
    assert len(dedupe([first, later]).representatives) == 2


def test_different_issuers_never_merged(rss_records):
    first = _guidance(rss_records)[0]
    other = replace(
        first,
        id=make_catalyst_id("news_rss", "https://beta-business.example/y|beta", "guidance_raise"),
        symbol="DMBANK2",
        publisher="Beta Business News",
        canonical_url="https://beta-business.example/y",
    )
    assert len(dedupe([first, other]).representatives) == 2


def test_best_grade_kept_on_representative(rss_records):
    a, b = _guidance(rss_records)[:2]
    b = replace(b, source_grade=SourceGrade.A_OFFICIAL_FILING)
    rep = dedupe([a, b]).representatives[0]
    assert rep.source_grade is SourceGrade.A_OFFICIAL_FILING and rep.publisher == a.publisher


def test_identical_ids_collapse_to_earliest(rss_records):
    a = _guidance(rss_records)[0]
    later = replace(a, available_at=a.available_at + timedelta(hours=3))
    result = dedupe([later, a])
    assert len(result.representatives) == 1 and result.representatives[0].available_at == a.available_at


def test_normalization():
    assert normalize_headline(
        "UPDATE 1-Demo X raises guidance - Beta Business News", "Beta Business News"
    ) == ("demo x raises guidance")
    assert jaccard(frozenset("abcd"), frozenset("abce")) == pytest.approx(0.6)
