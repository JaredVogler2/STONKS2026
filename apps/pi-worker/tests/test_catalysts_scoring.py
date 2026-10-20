from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apex_worker.catalysts.form4 import form4_to_catalyst, parse_form4
from apex_worker.catalysts.models import CatalystType, Direction, SourceGrade, make_catalyst_id
from apex_worker.catalysts.scoring import ScoringConfig, apply_score, impact_score
from apex_worker.contracts import validate

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "catalysts"
PUB = datetime(2026, 10, 9, 22, 12, 44, tzinfo=UTC)


def _form4(name):
    raw = (FIX / name).read_bytes()
    return form4_to_catalyst(
        parse_form4(raw),
        raw=raw,
        accession_number="0009990101-26-000007",
        published_at=PUB,
        first_seen_at=PUB,
    )


@pytest.fixture
def purchase():
    return _form4("form4_open_market_purchase.xml")


def test_score_bounded_and_not_a_probability(purchase):
    res = impact_score(purchase, decision_time=purchase.available_at)
    assert 0 <= res.score <= 100
    assert res.is_probability is False
    assert "NOT a probability" in res.components["note"]


def test_award_scores_far_below_open_market_purchase(purchase):
    award = _form4("form4_award_code_a.xml")
    t = purchase.available_at
    assert impact_score(award, decision_time=t).score < impact_score(purchase, decision_time=t).score / 5


def test_novelty_decays_with_half_life(purchase):
    cfg = ScoringConfig(half_life_hours=24)
    fresh = impact_score(purchase, decision_time=purchase.available_at, config=cfg).score
    day_old = impact_score(
        purchase, decision_time=purchase.available_at + timedelta(hours=24), config=cfg
    ).score
    assert day_old == pytest.approx(fresh / 2, abs=0.1)
    old = impact_score(purchase, decision_time=purchase.available_at + timedelta(days=10), config=cfg)
    assert "NOVELTY_DECAYED" in old.reason_codes


def test_large_realized_gap_reduces_remaining_opportunity(purchase):
    t = purchase.available_at
    base = impact_score(purchase, decision_time=t).score
    small = impact_score(purchase, decision_time=t, pre_event_return=0.02)
    gap = impact_score(purchase, decision_time=t, pre_event_return=0.15)
    assert small.score == base and "PRICE_ALREADY_REACTED" not in small.reason_codes
    assert gap.score < base * 0.5
    assert {"PRICE_ALREADY_REACTED", "REACTION_ALIGNED_WITH_DIRECTION"} <= set(gap.reason_codes)
    against = impact_score(purchase, decision_time=t, pre_event_return=-0.12)
    assert "REACTION_OPPOSES_DIRECTION" in against.reason_codes


def test_scoring_before_availability_is_refused(purchase):
    with pytest.raises(ValueError, match="point-in-time"):
        impact_score(purchase, decision_time=purchase.available_at - timedelta(seconds=1))


def test_multi_source_is_reliability_bonus_not_direction(purchase):
    news = replace(
        purchase,
        id=make_catalyst_id("news_rss", "https://x.example/a|x", "guidance_raise"),
        catalyst_type=CatalystType.GUIDANCE_RAISE,
        source_grade=SourceGrade.C_REPUTABLE_WIRE,
        parsed={},
    )
    confirmed = news.add_reason_codes("MULTI_SOURCE_CONFIRMED")
    t = news.available_at
    assert impact_score(confirmed, decision_time=t).score > impact_score(news, decision_time=t).score
    assert confirmed.direction is news.direction is Direction.POTENTIALLY_POSITIVE


def test_apply_score_is_contract_valid(purchase):
    scored = apply_score(
        purchase, decision_time=purchase.available_at + timedelta(hours=1), pre_event_return=0.2
    )
    payload = scored.to_contract()
    validate("Catalyst", payload)
    assert payload["impact_score"] == scored.impact_score
    assert payload["reason_codes"][0] == "PRICE_ALREADY_REACTED"
    assert payload["parsed"]["impact_components"]["remaining_opportunity"] < 1
