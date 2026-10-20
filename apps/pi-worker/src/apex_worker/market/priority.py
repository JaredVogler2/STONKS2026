"""Bounded live-stream slot allocation (Alpaca Basic: 30 WebSocket symbols; APEX uses <= 25 by default).

Priority tiers: open member positions > published active signals > high-quality candidates > watchlist.
Positions get reserved slots; remaining slots are filled by weighted score with a per-sector cap so one
theme cannot monopolize the stream. Replacement is incremental (minimal subscribe/unsubscribe churn).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

TIER_WEIGHT = {"position": 1000.0, "signal": 500.0, "candidate": 100.0, "watchlist": 10.0}


@dataclass(frozen=True)
class SlotRequest:
    symbol: str
    tier: str  # position | signal | candidate | watchlist
    score: float = 0.0
    sector: str | None = None


@dataclass(frozen=True)
class SlotPlan:
    selected: tuple[str, ...]
    subscribe: tuple[str, ...]
    unsubscribe: tuple[str, ...]
    dropped: tuple[str, ...]
    reserved_for_positions: int


def plan_slots(requests: list[SlotRequest], current: set[str], max_slots: int = 25, sector_cap_share: float = 0.4
               ) -> SlotPlan:
    best: dict[str, SlotRequest] = {}
    for r in requests:
        prev = best.get(r.symbol)
        if prev is None or TIER_WEIGHT[r.tier] + r.score > TIER_WEIGHT[prev.tier] + prev.score:
            best[r.symbol] = r
    ranked = sorted(best.values(), key=lambda r: (-(TIER_WEIGHT[r.tier] + r.score), r.symbol))
    positions = [r for r in ranked if r.tier == "position"]
    selected: list[str] = [r.symbol for r in positions[:max_slots]]
    per_sector: Counter = Counter()
    cap = max(1, int(max_slots * sector_cap_share))
    for r in ranked:
        if len(selected) >= max_slots:
            break
        if r.symbol in selected:
            continue
        if r.tier in ("candidate", "watchlist") and r.sector and per_sector[r.sector] >= cap:
            continue
        selected.append(r.symbol)
        if r.sector:
            per_sector[r.sector] += 1
    sel = set(selected)
    dropped = tuple(sorted(s for s in best if s not in sel))
    return SlotPlan(tuple(selected), tuple(sorted(sel - current)), tuple(sorted(current - sel)), dropped,
                    min(len(positions), max_slots))
