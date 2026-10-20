"""Headline -> issuer entity resolution (deterministic, dictionary based).

Methods, strongest first:

* exchange-qualified mention ``(NASDAQ: ABC)`` / ``NYSE:ABC``           -> 0.95
* cashtag ``$ABC``                                                    -> 0.90
* unambiguous company name / alias (case-sensitive, word bounded)      -> 0.85
* ambiguous common-word name qualified by a corporate suffix ("Inc.")  -> 0.75
* bare ticker token (only for distinctive tickers: digit or >= 5 chars) -> 0.60

Ambiguous common-word names ("Beacon", "Target", "Apex") are REFUSED unless qualified by a suffix or
by an exchange/cashtag mention of the same issuer. Only symbols in the dictionary resolve; an unknown
cashtag never invents an entity.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from apex_worker.catalysts.tickers import normalize_symbol

CORPORATE_SUFFIX = (
    r"(?:,?\s+(?:Inc\.?|Incorporated|Corp\.?|Corporation|Co\.|Company|Holdings|Group|Ltd\.?|Limited|plc|PLC"
    r"|N\.V\.|S\.A\.|AG|LLC|L\.P\.|LP))"
)
_EXCHANGE_RE = re.compile(
    r"\b(?:NASDAQ|Nasdaq|NasdaqGS|NasdaqGM|NasdaqCM|NYSE(?:\s+American|\s+Arca)?|NYSEAMERICAN|AMEX|Cboe|CBOE|BATS)"
    r"\s*:\s*\$?([A-Z][A-Z0-9]{0,9}(?:\.[A-Z])?)\b"
)
_CASHTAG_RE = re.compile(r"(?<![A-Za-z0-9$])\$([A-Z][A-Z0-9]{0,9}(?:\.[A-Z])?)(?![A-Za-z0-9])")
_BARE_STOPWORDS = frozenset(
    {"CEO", "CFO", "COO", "FDA", "SEC", "DOJ", "FTC", "NYSE", "NASDAQ", "USA", "IPO", "ETF"}
)
CONF_EXCHANGE = 0.95
CONF_CASHTAG = 0.9
CONF_NAME = 0.85
CONF_QUALIFIED_AMBIGUOUS = 0.75
CONF_BARE_TICKER = 0.6
MULTI_ENTITY_PENALTY = 0.1


@dataclass(frozen=True)
class CompanyAlias:
    symbol: str
    names: tuple[str, ...] = ()
    ambiguous_names: tuple[str, ...] = ()
    cik: str | None = None


@dataclass(frozen=True)
class EntityMatch:
    symbol: str
    confidence: float
    method: str
    position: int


@dataclass(frozen=True)
class EntityResolution:
    matches: tuple[EntityMatch, ...]
    rejected: tuple[str, ...] = ()

    @property
    def primary(self) -> EntityMatch | None:
        """Earliest-positioned resolved issuer (the headline subject), penalised if several issuers."""
        if not self.matches:
            return None
        first = min(self.matches, key=lambda m: (m.position, -m.confidence))
        if len(self.matches) > 1:
            return EntityMatch(
                first.symbol,
                round(max(0.0, first.confidence - MULTI_ENTITY_PENALTY), 4),
                first.method,
                first.position,
            )
        return first

    @property
    def others(self) -> tuple[str, ...]:
        p = self.primary
        return tuple(m.symbol for m in self.matches if p is None or m.symbol != p.symbol)


def _word_re(name: str, suffix: bool = False) -> re.Pattern[str]:
    body = re.escape(name)
    return re.compile(r"(?<![A-Za-z0-9])" + body + (CORPORATE_SUFFIX if suffix else "") + r"(?![A-Za-z0-9])")


class EntityResolver:
    def __init__(self, companies: Iterable[CompanyAlias]):
        self._companies: dict[str, CompanyAlias] = {}
        self._name_patterns: list[tuple[re.Pattern[str], str]] = []
        self._ambiguous: list[tuple[re.Pattern[str], re.Pattern[str], str, str]] = []
        for comp in companies:
            sym = normalize_symbol(comp.symbol)
            if sym is None:
                raise ValueError(f"invalid symbol {comp.symbol!r}")
            self._companies[sym] = comp
            for name in sorted(comp.names, key=len, reverse=True):
                self._name_patterns.append((_word_re(name), sym))
            for name in comp.ambiguous_names:
                self._ambiguous.append((_word_re(name), _word_re(name, suffix=True), sym, name))

    @classmethod
    def from_json(cls, data: Mapping[str, Any] | bytes | str) -> EntityResolver:
        raw = json.loads(data) if isinstance(data, bytes | str) else data
        return cls(
            CompanyAlias(
                symbol=c["symbol"],
                names=tuple(c.get("names", ())),
                ambiguous_names=tuple(c.get("ambiguous_names", ())),
                cik=c.get("cik"),
            )
            for c in raw["companies"]
        )

    @property
    def symbols(self) -> frozenset[str]:
        return frozenset(self._companies)

    def cik_for(self, symbol: str) -> str | None:
        comp = self._companies.get(symbol)
        return comp.cik if comp else None

    def resolve(self, headline: str) -> EntityResolution:
        found: dict[str, EntityMatch] = {}
        rejected: list[str] = []

        def add(sym: str, conf: float, method: str, pos: int) -> None:
            prev = found.get(sym)
            if prev is None or conf > prev.confidence:
                found[sym] = EntityMatch(sym, conf, method, min(pos, prev.position) if prev else pos)
            elif pos < prev.position:
                found[sym] = EntityMatch(sym, prev.confidence, prev.method, pos)

        for m in _EXCHANGE_RE.finditer(headline):
            sym = normalize_symbol(m.group(1))
            if sym in self._companies:
                add(sym, CONF_EXCHANGE, "exchange_qualified", m.start())
            else:
                rejected.append("UNKNOWN_EXCHANGE_TICKER")
        for m in _CASHTAG_RE.finditer(headline):
            sym = normalize_symbol(m.group(1))
            if sym in self._companies:
                add(sym, CONF_CASHTAG, "cashtag", m.start())
            else:
                rejected.append("UNKNOWN_CASHTAG")
        for pattern, sym in self._name_patterns:
            m = pattern.search(headline)
            if m:
                add(sym, CONF_NAME, "company_name", m.start())
        for bare, qualified, sym, _name in self._ambiguous:
            q = qualified.search(headline)
            if q:
                add(sym, CONF_QUALIFIED_AMBIGUOUS, "qualified_ambiguous_name", q.start())
                continue
            b = bare.search(headline)
            if b and sym not in found:
                rejected.append("AMBIGUOUS_ENTITY_UNQUALIFIED")
        for sym in self._companies:
            if sym in found or sym in _BARE_STOPWORDS:
                continue
            if not (any(ch.isdigit() for ch in sym) or len(sym) >= 5):
                continue
            m = re.search(r"(?<![A-Za-z0-9$:])" + re.escape(sym) + r"(?![A-Za-z0-9])", headline)
            if m:
                add(sym, CONF_BARE_TICKER, "bare_ticker", m.start())
        return EntityResolution(
            tuple(sorted(found.values(), key=lambda x: x.position)), tuple(dict.fromkeys(rejected))
        )


__all__ = ["CompanyAlias", "EntityMatch", "EntityResolution", "EntityResolver"]
