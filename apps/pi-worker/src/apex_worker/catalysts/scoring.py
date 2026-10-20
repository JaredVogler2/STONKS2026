"""Catalyst impact score 0-100 - a deterministic *attention/priority* score, explicitly NOT a probability.

score = 100 x type_weight x grade_weight x direction_clarity x materiality x novelty x remaining_opportunity

* novelty decays exponentially with age since ``available_at`` (configurable half-life)
* remaining_opportunity drops when the price already reacted (|pre-event return| above a threshold),
  adding ``PRICE_ALREADY_REACTED``; e.g. a +15% gap leaves much less remaining opportunity
* multi-source confirmation is a reliability bonus, capped; it never changes direction
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from apex_worker.catalysts.models import ActionableAge, CatalystRecord, CatalystType, Direction, SourceGrade
from apex_worker.timeutil import ensure_utc

T = CatalystType
DEFAULT_TYPE_WEIGHTS: dict[CatalystType, float] = {
    T.EARNINGS_RELEASE: 0.8,
    T.EARNINGS_SCHEDULED: 0.5,
    T.GUIDANCE_RAISE: 0.85,
    T.GUIDANCE_CUT: 0.85,
    T.CONTRACT_AWARD: 0.6,
    T.GOVERNMENT_PROCUREMENT: 0.6,
    T.REGULATORY_DECISION: 0.9,
    T.MERGER_ACQUISITION: 0.9,
    T.BUYBACK: 0.5,
    T.INSIDER_OPEN_MARKET_PURCHASE: 0.6,
    T.INSIDER_SALE: 0.3,
    T.INSIDER_AWARD_OR_EXERCISE: 0.05,
    T.DILUTION_OFFERING: 0.7,
    T.SHELF_REGISTRATION: 0.3,
    T.REGULATORY_ACTION: 0.7,
    T.MACRO_RELEASE: 0.4,
    T.SECTOR_SHOCK: 0.5,
    T.MANAGEMENT_CHANGE: 0.5,
    T.OWNERSHIP_13D: 0.5,
    T.OWNERSHIP_13G: 0.2,
    T.PERIODIC_REPORT: 0.3,
    T.CURRENT_REPORT_OTHER: 0.25,
    T.POLITICAL_DISCLOSURE: 0.1,
    T.NEWS_OTHER: 0.1,
}
DEFAULT_GRADE_WEIGHTS: dict[SourceGrade, float] = {
    SourceGrade.A_OFFICIAL_FILING: 1.0,
    SourceGrade.B_OFFICIAL_RELEASE: 0.9,
    SourceGrade.C_REPUTABLE_WIRE: 0.75,
    SourceGrade.D_OTHER: 0.5,
    SourceGrade.E_UNVERIFIED: 0.25,
}
DIRECTION_CLARITY = {
    Direction.POTENTIALLY_POSITIVE: 1.0,
    Direction.POTENTIALLY_NEGATIVE: 1.0,
    Direction.MIXED: 0.8,
    Direction.UNKNOWN: 0.7,
}
MATERIALITY = {"high": 1.25, "medium": 1.0, "low": 0.6}


@dataclass(frozen=True)
class ScoringConfig:
    half_life_hours: float = 72.0
    reaction_threshold: float = 0.05  # |pre-event return| at which remaining opportunity starts shrinking
    reaction_full_decay: float = 0.15  # additional move over the threshold that removes most opportunity
    reaction_floor: float = 0.15
    multi_source_bonus: float = 0.1
    type_weights: dict[CatalystType, float] = field(default_factory=lambda: dict(DEFAULT_TYPE_WEIGHTS))
    grade_weights: dict[SourceGrade, float] = field(default_factory=lambda: dict(DEFAULT_GRADE_WEIGHTS))

    def __post_init__(self) -> None:
        if self.half_life_hours <= 0 or self.reaction_full_decay <= 0:
            raise ValueError("half-life and reaction decay must be positive")


@dataclass(frozen=True)
class ScoreResult:
    score: float
    reason_codes: tuple[str, ...]
    components: dict[str, Any]
    is_probability: bool = False  # always False: this is a ranking aid, not a win probability


def impact_score(
    record: CatalystRecord,
    *,
    decision_time: datetime,
    pre_event_return: float | None = None,
    config: ScoringConfig | None = None,
) -> ScoreResult:
    cfg = config or ScoringConfig()
    decision_time = ensure_utc(decision_time)
    if record.available_at > decision_time:
        raise ValueError("cannot score a catalyst before it is available (point-in-time violation)")
    reasons: list[str] = []
    type_w = cfg.type_weights.get(record.catalyst_type, 0.1)
    grade_w = cfg.grade_weights[record.source_grade]
    clarity = DIRECTION_CLARITY[record.direction]
    materiality = MATERIALITY.get(str(record.parsed.get("materiality", "medium")), 1.0)
    if record.actionable_age is ActionableAge.SCHEDULED_FUTURE:
        novelty = 1.0
    else:
        age_h = (decision_time - record.available_at).total_seconds() / 3600.0
        novelty = 0.5 ** (age_h / cfg.half_life_hours)
        if novelty < 0.25:
            reasons.append("NOVELTY_DECAYED")
    reliability = 1.0
    if "MULTI_SOURCE_CONFIRMED" in record.reason_codes:
        reliability += cfg.multi_source_bonus
    remaining = 1.0
    if pre_event_return is not None:
        move = abs(pre_event_return)
        if move >= cfg.reaction_threshold:
            remaining = max(
                cfg.reaction_floor, 1.0 - (move - cfg.reaction_threshold) / cfg.reaction_full_decay
            )
            reasons.append("PRICE_ALREADY_REACTED")
            sign = 1 if pre_event_return > 0 else -1
            aligned = (record.direction is Direction.POTENTIALLY_POSITIVE and sign > 0) or (
                record.direction is Direction.POTENTIALLY_NEGATIVE and sign < 0
            )
            if aligned:
                reasons.append("REACTION_ALIGNED_WITH_DIRECTION")
            elif record.direction in (Direction.POTENTIALLY_POSITIVE, Direction.POTENTIALLY_NEGATIVE):
                reasons.append("REACTION_OPPOSES_DIRECTION")
    raw = 100.0 * type_w * grade_w * clarity * materiality * novelty * reliability * remaining
    score = round(min(100.0, max(0.0, raw)), 1)
    components = {
        "type_weight": type_w,
        "grade_weight": grade_w,
        "direction_clarity": clarity,
        "materiality": materiality,
        "novelty": round(novelty, 4),
        "reliability": reliability,
        "remaining_opportunity": round(remaining, 4),
        "pre_event_return": pre_event_return,
        "note": "impact_score is a priority score, NOT a probability of a winning trade",
    }
    return ScoreResult(score, tuple(reasons), components)


def apply_score(
    record: CatalystRecord,
    *,
    decision_time: datetime,
    pre_event_return: float | None = None,
    config: ScoringConfig | None = None,
) -> CatalystRecord:
    result = impact_score(
        record, decision_time=decision_time, pre_event_return=pre_event_return, config=config
    )
    parsed = {**record.parsed, "impact_components": result.components}
    return replace(
        record,
        impact_score=result.score,
        reason_codes=(*result.reason_codes, *record.reason_codes),
        parsed=parsed,
    )


__all__ = [
    "DEFAULT_GRADE_WEIGHTS",
    "DEFAULT_TYPE_WEIGHTS",
    "ScoreResult",
    "ScoringConfig",
    "apply_score",
    "impact_score",
]
