"""Ticker <-> SEC CIK mapping with explicit historical alias spans.

``company_tickers.json`` (https://www.sec.gov/files/company_tickers.json) is a *current* snapshot:
``{"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}``. It says nothing about the
past, so historical ticker changes and ticker *reuse* are described by an explicit alias table of
``{ticker, cik, valid_from, valid_to}`` spans. Lookups for a date covered by spans use the spans only;
a date in a gap between two owners of a ticker resolves to ``None`` (unknown), never to whichever
company holds the ticker today. Two spans for one ticker that overlap with different CIKs are rejected.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")


class TickerMapError(ValueError):
    pass


def normalize_cik(value: object) -> str:
    """10-digit zero-padded CIK string."""
    digits = re.sub(r"\D", "", str(value).strip())
    if not digits or len(digits) > 10:
        raise TickerMapError(f"invalid CIK {value!r}")
    return digits.zfill(10)


def normalize_symbol(value: str | None) -> str | None:
    """Upper-case symbol in the contract format (SEC class separators '-' / '/' become '.')."""
    if not value:
        return None
    sym = value.strip().upper().replace("-", ".").replace("/", ".")
    return sym if SYMBOL_RE.match(sym) else None


@dataclass(frozen=True)
class TickerSpan:
    ticker: str
    cik: str
    valid_from: date | None = None
    valid_to: date | None = None  # inclusive
    title: str | None = None
    note: str | None = None

    def covers(self, day: date) -> bool:
        return (self.valid_from is None or self.valid_from <= day) and (
            self.valid_to is None or day <= self.valid_to
        )

    def overlaps(self, other: TickerSpan) -> bool:
        lo_a, hi_a = self.valid_from or date.min, self.valid_to or date.max
        lo_b, hi_b = other.valid_from or date.min, other.valid_to or date.max
        return lo_a <= hi_b and lo_b <= hi_a


@dataclass(frozen=True)
class TickerResolution:
    ticker: str
    cik: str | None
    title: str | None
    confidence: float
    reason: str


class TickerMap:
    def __init__(
        self,
        current: Mapping[str, tuple[str, str | None]] | None = None,
        spans: Iterable[TickerSpan] = (),
        *,
        snapshot_date: date | None = None,
    ):
        self._current: dict[str, tuple[str, str | None]] = {}
        for ticker, (cik, title) in (current or {}).items():
            sym = normalize_symbol(ticker)
            if sym:
                self._current[sym] = (normalize_cik(cik), title)
        self.snapshot_date = snapshot_date
        self._spans_by_ticker: dict[str, list[TickerSpan]] = {}
        self._spans_by_cik: dict[str, list[TickerSpan]] = {}
        for given in spans:
            sym = normalize_symbol(given.ticker)
            if sym is None:
                raise TickerMapError(f"invalid ticker in alias table: {given.ticker!r}")
            span = TickerSpan(
                sym, normalize_cik(given.cik), given.valid_from, given.valid_to, given.title, given.note
            )
            if span.valid_from and span.valid_to and span.valid_to < span.valid_from:
                raise TickerMapError(f"{sym}: valid_to before valid_from")
            for other in self._spans_by_ticker.get(sym, []):
                if other.cik != span.cik and other.overlaps(span):
                    raise TickerMapError(
                        f"{sym}: overlapping spans map to different CIKs ({other.cik}, {span.cik})"
                    )
            self._spans_by_ticker.setdefault(sym, []).append(span)
            self._spans_by_cik.setdefault(span.cik, []).append(span)

    # -- loading -------------------------------------------------------------------------------
    @classmethod
    def load(
        cls,
        company_tickers: Mapping[str, Any] | bytes | str,
        aliases: Iterable[Mapping[str, Any]] | Mapping[str, Any] | bytes | str | None = None,
        *,
        snapshot_date: date | None = None,
    ) -> TickerMap:
        data = json.loads(company_tickers) if isinstance(company_tickers, bytes | str) else company_tickers
        rows = data.values() if isinstance(data, Mapping) else data
        current: dict[str, tuple[str, str | None]] = {}
        for row in rows:
            try:
                ticker, cik = str(row["ticker"]), row["cik_str"]
            except (KeyError, TypeError) as exc:
                raise TickerMapError(f"bad company_tickers row: {row!r}") from exc
            current[ticker] = (normalize_cik(cik), row.get("title"))
        alias_rows: Iterable[Mapping[str, Any]] = ()
        if aliases is not None:
            parsed = json.loads(aliases) if isinstance(aliases, bytes | str) else aliases
            alias_rows = parsed.get("spans", []) if isinstance(parsed, Mapping) else parsed
        spans = [
            TickerSpan(
                ticker=str(r["ticker"]),
                cik=str(r["cik"]),
                valid_from=date.fromisoformat(r["valid_from"]) if r.get("valid_from") else None,
                valid_to=date.fromisoformat(r["valid_to"]) if r.get("valid_to") else None,
                title=r.get("title"),
                note=r.get("note"),
            )
            for r in alias_rows
        ]
        return cls(current, spans, snapshot_date=snapshot_date)

    # -- lookups -------------------------------------------------------------------------------
    def resolve(self, ticker: str, on: date | None = None) -> TickerResolution:
        sym = normalize_symbol(ticker)
        if sym is None:
            return TickerResolution(ticker, None, None, 0.0, "INVALID_SYMBOL")
        spans = self._spans_by_ticker.get(sym, [])
        if spans:
            if on is None:
                current = self._current.get(sym)
                if current:
                    return TickerResolution(sym, current[0], current[1], 0.95, "CURRENT_SNAPSHOT")
                open_spans = [s for s in spans if s.valid_to is None]
                if len(open_spans) == 1:
                    return TickerResolution(
                        sym, open_spans[0].cik, open_spans[0].title, 0.9, "OPEN_ALIAS_SPAN"
                    )
                return TickerResolution(sym, None, None, 0.0, "NO_CURRENT_MAPPING")
            hits = [s for s in spans if s.covers(on)]
            if len(hits) == 1:
                return TickerResolution(sym, hits[0].cik, hits[0].title, 0.99, "ALIAS_SPAN")
            return TickerResolution(sym, None, None, 0.0, "TICKER_UNASSIGNED_ON_DATE")
        current = self._current.get(sym)
        if current is None:
            return TickerResolution(sym, None, None, 0.0, "UNKNOWN_TICKER")
        if on is not None and self.snapshot_date is not None and on > self.snapshot_date:
            return TickerResolution(sym, current[0], current[1], 0.8, "AFTER_SNAPSHOT_ASSUMED_STABLE")
        reason = "CURRENT_SNAPSHOT" if on is None else "NO_HISTORY_ASSUMED_STABLE"
        return TickerResolution(sym, current[0], current[1], 0.95 if on is None else 0.85, reason)

    def cik_for(self, ticker: str, on: date | None = None) -> str | None:
        return self.resolve(ticker, on).cik

    def symbol_for(self, cik: str, on: date | None = None) -> str | None:
        """Ticker of ``cik`` on ``on`` (None when it had no ticker then / is unknown)."""
        key = normalize_cik(cik)
        spans = self._spans_by_cik.get(key, [])
        if on is not None and spans:
            hits = [s for s in spans if s.covers(on)]
            return hits[0].ticker if len(hits) == 1 else None
        currents = sorted(t for t, (c, _) in self._current.items() if c == key)
        if on is None:
            return currents[0] if currents else None
        # no history for this CIK: assume its current ticker(s) applied, but never claim a ticker that
        # the alias table says belonged to a different company on that date
        for t in currents:
            owner = self.resolve(t, on).cik
            if owner == key:
                return t
        return None

    def title_for(self, cik: str) -> str | None:
        key = normalize_cik(cik)
        for c, title in self._current.values():
            if c == key and title:
                return title
        for span in self._spans_by_cik.get(key, []):
            if span.title:
                return span.title
        return None

    def known_symbols(self) -> frozenset[str]:
        return frozenset(self._current) | frozenset(self._spans_by_ticker)


__all__ = [
    "TickerMap",
    "TickerMapError",
    "TickerResolution",
    "TickerSpan",
    "normalize_cik",
    "normalize_symbol",
]
