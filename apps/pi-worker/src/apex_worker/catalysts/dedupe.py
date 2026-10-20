"""Cluster syndicated / republished items into one evidence cluster.

Two news items join a cluster when, within a 36 hour window, they share a canonical URL, share the
same normalized-headline text hash, or have normalized-headline token Jaccard >= 0.8 (and do not name
different issuers). Each cluster yields ONE representative: the member with the earliest
``available_at`` (so the cluster is never usable before its first member was). Several independent
publishers raise *source reliability* (``MULTI_SOURCE_CONFIRMED`` + ``SOURCE_COUNT_<n>``) but never
change the direction. Non-news records (SEC filings, disclosures) are only de-duplicated by id.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from apex_worker.catalysts.models import GRADE_RANK, CatalystRecord, Source, sha256_hex

WINDOW = timedelta(hours=36)
JACCARD_THRESHOLD = 0.8
_PREFIX_RE = re.compile(
    r"^\s*(?:update\s*\d*\s*[-:]|breaking\s*[-:]|exclusive\s*[-:]|corrected\s*[-:])\s*", re.I
)
_SUFFIX_SPLIT_RE = re.compile(r"\s+[-|–—]\s+")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CLUSTERABLE = frozenset({Source.NEWS_RSS, Source.REPLAY})


def normalize_headline(headline: str, publisher: str | None = None) -> str:
    text = _PREFIX_RE.sub("", headline or "")
    parts = _SUFFIX_SPLIT_RE.split(text)
    if len(parts) > 1:
        tail = parts[-1].strip().lower()
        if (publisher and tail == publisher.strip().lower()) or (
            len(tail.split()) <= 4 and len(parts[0].split()) >= 4
        ):
            text = " - ".join(parts[:-1])
    return " ".join(_TOKEN_RE.findall(text.lower()))


def headline_tokens(normalized: str) -> frozenset[str]:
    return frozenset(normalized.split())


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _ref_time(rec: CatalystRecord) -> datetime:
    return rec.published_at or rec.available_at


@dataclass
class Cluster:
    cluster_id: str
    members: list[CatalystRecord]
    representative: CatalystRecord
    publishers: list[str] = field(default_factory=list)

    @property
    def independent_sources(self) -> int:
        return len(self.publishers)


@dataclass
class DedupeResult:
    representatives: list[CatalystRecord]
    clusters: list[Cluster]


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _collapse_ids(records: Iterable[CatalystRecord]) -> list[CatalystRecord]:
    best: dict[str, CatalystRecord] = {}
    for rec in records:
        prev = best.get(rec.id)
        if prev is None or rec.available_at < prev.available_at:
            best[rec.id] = rec
    return list(best.values())


def _same_story(a: CatalystRecord, b: CatalystRecord, na: str, nb: str) -> bool:
    if a.symbol and b.symbol and a.symbol != b.symbol:
        return False
    if abs(_ref_time(a) - _ref_time(b)) > WINDOW:
        return False
    if a.canonical_url and a.canonical_url == b.canonical_url:
        return True
    if na and na == nb:
        return True
    return jaccard(headline_tokens(na), headline_tokens(nb)) >= JACCARD_THRESHOLD


def dedupe(records: Iterable[CatalystRecord]) -> DedupeResult:
    unique = sorted(_collapse_ids(records), key=lambda r: (r.available_at, r.id))
    news_idx = [i for i, r in enumerate(unique) if r.source in _CLUSTERABLE]
    norm = {i: normalize_headline(unique[i].headline, unique[i].publisher) for i in news_idx}
    uf = _UnionFind(len(unique))
    by_time = sorted(news_idx, key=lambda i: _ref_time(unique[i]))
    for pos, i in enumerate(by_time):
        for j in by_time[pos + 1 :]:
            if _ref_time(unique[j]) - _ref_time(unique[i]) > WINDOW:
                break
            if _same_story(unique[i], unique[j], norm[i], norm[j]):
                uf.union(i, j)
    groups: dict[int, list[int]] = {}
    for i in range(len(unique)):
        groups.setdefault(uf.find(i), []).append(i)

    reps: list[CatalystRecord] = []
    clusters: list[Cluster] = []
    for idxs in groups.values():
        members = sorted(
            (unique[i] for i in idxs), key=lambda r: (r.available_at, -GRADE_RANK[r.source_grade], r.id)
        )
        first = members[0]
        cluster_id = "clu_" + first.id[4:]
        publishers = sorted({(m.publisher or m.source.value).strip().lower() for m in members})
        best_grade = max((m.source_grade for m in members), key=lambda g: GRADE_RANK[g])
        reasons: list[str] = []
        parsed = dict(first.parsed)
        if len(members) > 1:
            parsed["cluster"] = {
                "size": len(members),
                "independent_publishers": len(publishers),
                "publishers": publishers[:20],
                "member_ids": [m.id for m in members][:50],
                "text_hash": sha256_hex(norm.get(idxs[0], first.headline)),
            }
            if len(publishers) >= 2:
                reasons += ["MULTI_SOURCE_CONFIRMED", f"SOURCE_COUNT_{len(publishers)}"]
            reasons.append(f"CLUSTER_SIZE_{len(members)}")
        rep = replace(
            first,
            cluster_id=cluster_id,
            source_grade=best_grade,
            reason_codes=(*reasons, *first.reason_codes),
            parsed=parsed,
        )
        reps.append(rep)
        clusters.append(Cluster(cluster_id, members, rep, publishers))
    reps.sort(key=lambda r: (r.available_at, r.id))
    return DedupeResult(reps, clusters)


__all__ = [
    "JACCARD_THRESHOLD",
    "WINDOW",
    "Cluster",
    "DedupeResult",
    "dedupe",
    "jaccard",
    "normalize_headline",
]
