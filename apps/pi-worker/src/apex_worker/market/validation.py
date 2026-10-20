"""Price observation validation (levels 0-3) and decision classes.

Level 1: syntactically valid, timestamp sane (not future, not older than allowed, ordered).
Level 2: venue print/quote plausible: positive, bid<=ask, not crossed/locked beyond tolerance, within a band
         of the reference price, not an isolated outlier versus the last accepted print.
Level 3: cross-checked against an appropriately delayed consolidated reference when available.
A hard exit/invalidation alert requires level >= 2 AND freshness; otherwise the message must say
"PRICE UNCERTAIN - CHECK BROKER". Candidate discovery may use delayed data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

MAX_FUTURE_SKEW = timedelta(seconds=5)
OUTLIER_BAND = 0.12  # 12% from reference without corroboration is quarantined
CROSS_TOLERANCE = 0.0005


@dataclass(frozen=True)
class ValidationResult:
    level: int
    flags: tuple[str, ...]
    quarantined: bool

    @property
    def usable_for_exit_alerts(self) -> bool:
        return self.level >= 2 and not self.quarantined


def validate_price(
    *,
    price: float | None,
    bid: float | None,
    ask: float | None,
    ts_exchange: datetime | None,
    ts_received: datetime,
    now: datetime,
    last_accepted_price: float | None,
    last_accepted_ts: datetime | None,
    reference_price: float | None,
    consolidated_reference: float | None = None,
    halted: bool = False,
    volume: int | None = None,
) -> ValidationResult:
    flags: list[str] = []
    if price is None or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        return ValidationResult(0, ("INVALID_PRICE",), True)
    if ts_exchange is None:
        return ValidationResult(0, ("MISSING_TIMESTAMP",), True)
    if ts_exchange > now + MAX_FUTURE_SKEW:
        return ValidationResult(0, ("FUTURE_TIMESTAMP",), True)
    if last_accepted_ts is not None and ts_exchange < last_accepted_ts:
        flags.append("OUT_OF_ORDER")
        return ValidationResult(1, tuple(flags), True)
    level = 1
    q = False
    if halted:
        flags.append("HALTED")
        q = True
    if bid is not None and ask is not None:
        if bid < 0 or ask < 0:
            flags.append("NEGATIVE_QUOTE")
            q = True
        elif bid > 0 and ask > 0:
            if bid > ask * (1 + CROSS_TOLERANCE):
                flags.append("CROSSED_QUOTE")
                q = True
            elif bid == ask:
                flags.append("LOCKED_QUOTE")
        if bid == 0 or ask == 0:
            flags.append("EMPTY_BOOK_SIDE")
    if volume == 0:
        flags.append("ZERO_VOLUME")
    ref = last_accepted_price or reference_price
    if ref:
        move = abs(price / ref - 1)
        if move > OUTLIER_BAND:
            flags.append("OUTLIER_VS_REFERENCE")
            q = True
    if not q:
        level = 2
    if consolidated_reference and not q:
        if abs(price / consolidated_reference - 1) <= 0.01:
            level = 3
        else:
            flags.append("DIVERGES_FROM_CONSOLIDATED")
            level = 2
    return ValidationResult(level, tuple(flags), q)


def staleness_seconds(ts_exchange: datetime | None, now: datetime) -> int | None:
    if ts_exchange is None:
        return None
    return max(0, int((now - ts_exchange).total_seconds()))


def price_decision_class(level: int, staleness: int | None, fresh_sla_seconds: int, market_open: bool) -> str:
    """FRESH_VERIFIED (exit alerts allowed) | FRESH_UNVERIFIED | STALE | UNAVAILABLE."""
    if staleness is None:
        return "UNAVAILABLE"
    if market_open and staleness > fresh_sla_seconds:
        return "STALE"
    if level >= 2:
        return "FRESH_VERIFIED"
    return "FRESH_UNVERIFIED"
