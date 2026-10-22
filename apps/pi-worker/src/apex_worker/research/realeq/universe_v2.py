"""Protocol-2 universes. U1: the pinned S&P 500 current-constituents snapshot; U0: the fixed66 comparator.

U1 is SURVIVORSHIP_LIMITED_CURRENT_CONSTITUENTS (amendment 03 section 2):
* rows come from `universe_sp500_2026_10_22.json` (Wikipedia, CC BY-SA 4.0, raw sha256 recorded in the file);
* one listing per issuer (SEC CIK): the first in snapshot order, so share-class twins (GOOG, FOX, NWS) never act
  as each other's controls;
* a stock is a MEMBER at session t only if its `date_added` <= t. That removes the strongest look-ahead of a current list
  (names added because they rose later), but former members that left the index are still missing. Survivorship bias
  remains and is reported; paired same-date controls drawn from the same member set partly cancel it.
U2 (a verified point-in-time membership with delisted securities) has no free licensed source: BLOCKED_PIT_UNIVERSE.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd

from apex_worker.features.panel import AssetMeta
from apex_worker.research.training_data import BENCHMARK, MEMBERS, SECTOR_ETFS

SNAPSHOT_FILE = Path(__file__).with_name("universe_sp500_2026_10_22.json")
U2_STATUS = ("BLOCKED_PIT_UNIVERSE: no freely licensed, verified point-in-time S&P 500 membership with persistent "
             "security ids and delisted price history is available to this repository")


@dataclass(frozen=True)
class Constituent:
    symbol: str
    sector: str
    date_added: str
    cik: str
    name: str


@lru_cache(maxsize=1)
def snapshot() -> dict:
    return json.loads(SNAPSHOT_FILE.read_text())


def snapshot_sha256() -> str:
    return hashlib.sha256(SNAPSHOT_FILE.read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def u1_constituents() -> tuple[Constituent, ...]:
    seen: set[str] = set()
    out = []
    for sym, sector, added, cik, name in snapshot()["rows"]:
        if cik in seen:
            continue
        seen.add(cik)
        out.append(Constituent(sym, sector, added, cik, name))
    return tuple(out)


def dropped_share_classes() -> list[str]:
    kept = {c.symbol for c in u1_constituents()}
    return [r[0] for r in snapshot()["rows"] if r[0] not in kept]


def u1_stocks() -> tuple[str, ...]:
    return tuple(c.symbol for c in u1_constituents())


def u0_stocks() -> tuple[str, ...]:
    return tuple(s for sec in SECTOR_ETFS for s in MEMBERS[sec])


def reference_symbols() -> tuple[str, ...]:
    return (BENCHMARK, *SECTOR_ETFS.values())


def u1_symbols() -> tuple[str, ...]:
    """Everything protocol-2 downloads: references first, then the U1 stocks."""
    return (*reference_symbols(), *u1_stocks())


def u1_meta() -> dict[str, AssetMeta]:
    meta = {BENCHMARK: AssetMeta(BENCHMARK, None, "benchmark", exchange="ARCA", instrument="etf")}
    for sector, etf in SECTOR_ETFS.items():
        meta[etf] = AssetMeta(etf, sector, "etf", exchange="ARCA", instrument="etf", tradability_status="likely_eligible")
    for c in u1_constituents():
        meta[c.symbol] = AssetMeta(c.symbol, c.sector, "stock", instrument="common", name=c.name,
                                   tradability_status="likely_eligible")
    return meta


def membership(index: pd.DatetimeIndex, symbols: list[str] | None = None) -> dict[str, pd.Series]:
    """Boolean member-at-t series per U1 stock on `index` (date_added <= t)."""
    added = {c.symbol: pd.Timestamp(c.date_added) for c in u1_constituents()}
    syms = symbols if symbols is not None else list(added)
    return {s: pd.Series(index >= added[s], index=index) for s in syms if s in added}
