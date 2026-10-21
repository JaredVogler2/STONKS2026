"""Decision table of the private per-member position assessment (assess-1).

Every status branch, the trim hysteresis, the add rule, the price-quality gates and the notification policy are pinned
here, so refinements of exit/trim methodology are deliberate, versioned changes (bump ASSESSMENT_VERSION with them).
Reference plan: entry zone 100.00-102.00, stop 96.50, target 110.00, 15-session horizon; member bought 10 @ 101.00.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apex_worker.discord_bot.samples import sample_signal
from apex_worker.market.quote_cache import CachedQuote
from apex_worker.market.validation import ValidationResult
from apex_worker.portfolio.assessment import ASSESSMENT_VERSION, AssessContext, assess, materiality_key, should_notify
from apex_worker.portfolio.ledger import EventType, Fill, LedgerEvent, replay

PUBLISHED = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)  # Tue 10:00 ET
BOUGHT = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)  # Wed 11:00 ET, market open, 1 session held


def _ledger(*events: tuple[str, str, str]):
    """events: (type, price, qty) applied in order."""
    out = []
    for i, (kind, price, qty) in enumerate(events, start=1):
        fill = None if kind == "WATCH" else Fill(Decimal(price), Decimal(qty), Decimal(0), BOUGHT + timedelta(minutes=i))
        out.append(LedgerEvent(f"e{i}", i, EventType(kind), fill))
    return replay(out)


HELD = _ledger(("OPEN", "101.00", "10"))


def _quote(price: float, *, age_s: int = 10, level: int = 2, now: datetime = NOW) -> CachedQuote:
    return CachedQuote("DMSEMI1", price, None, None, "replay", "demo", "replay", now - timedelta(seconds=age_s), now, 0,
                       ValidationResult(level, (), False))


def _signal(**over):
    sig = sample_signal(PUBLISHED)
    sig.update(over)
    return sig


def _run(price: float | None = None, *, state=HELD, signal="default", signal_state="ACTIVE", quote=None, **ctx):
    sig = _signal() if signal == "default" else signal
    q = quote if quote is not None else (_quote(price) if price is not None else None)
    c = AssessContext(now=ctx.pop("now", NOW), market_open=ctx.pop("market_open", True), **ctx)
    return assess(position_id="9a5e1f0c-3b7d-4e21-8a6f-0d2c4b9e7a11", symbol="DMSEMI1", state=state, signal=sig,
                  signal_state=signal_state, quote=q, ctx=c)


def test_version_and_default_compliance_gate():
    a = _run(104.0)
    assert ASSESSMENT_VERSION == "assess-1"
    assert a["compliance_gated"] is True  # ALLOW_PERSONALIZED_GROUP_GUIDANCE off by default: neutral wording only


def test_thesis_intact_reports_marginal_reward_to_risk_from_the_current_price():
    a = _run(104.0)
    assert a["status"] == "HOLD_THESIS_INTACT"
    assert a["marginal_reward_to_risk"] == 0.8  # (110 - 104) / (104 - 96.5): not anchored to the entry
    assert a["distance_to_stop_pct"] == pytest.approx(104 / 96.5 - 1, abs=1e-6)
    assert a["sessions_held"] == 1 and a["sessions_remaining"] == 14
    assert a["unrealized_pnl"] == "30" and a["price_status"] == "FRESH_VERIFIED"


def test_trim_when_remaining_reward_to_risk_drops_below_075():
    a = _run(105.0)  # 5 / 8.5 = 0.588
    assert a["status"] == "CONSIDER_TRIM"
    assert "0.59" in a["reasons"][0]


def test_trim_hysteresis_needs_one_r_to_clear():
    # 0.8 >= 0.75 would be HOLD from scratch, but after a CONSIDER_TRIM notice the bar to clear is 1.0
    assert _run(104.0, previous_status="CONSIDER_TRIM")["status"] == "CONSIDER_TRIM"
    assert _run(103.0, previous_status="CONSIDER_TRIM")["status"] == "HOLD_THESIS_INTACT"  # 7 / 6.5 = 1.08


def test_target_nearing_is_flagged_within_one_and_a_half_percent():
    a = _run(108.5)
    assert "TARGET_NEARING" in a["risk_flags"]
    assert a["status"] == "CONSIDER_TRIM"  # 1.5 / 12 remaining R


def test_target_touched_cannot_assume_a_fill():
    a = _run(110.5)
    assert a["status"] == "TARGET_TOUCHED_FILL_UNKNOWN" and "TARGET_BREACHED" in a["risk_flags"]


@pytest.mark.parametrize(
    ("ctx", "status", "flag"),
    [
        ({}, "REVIEW_INVALIDATION", None),
        ({"session_open_price": 95.0}, "CONSIDER_EXIT", "GAP_BELOW_STOP"),
        ({"regime_label": "risk_off"}, "CONSIDER_EXIT", "REGIME_RISK_OFF"),
        ({"regime_label": "volatility_shock"}, "CONSIDER_EXIT", "REGIME_RISK_OFF"),
    ],
)
def test_stop_breach_escalates_only_on_a_gap_or_a_bad_regime(ctx, status, flag):
    a = _run(96.0, **ctx)
    assert a["status"] == status and "STOP_BREACHED" in a["risk_flags"]
    assert "DRAWDOWN_GT_5PCT" not in a["risk_flags"]  # 96 / 101 - 1 = -4.95 %: just inside the -5 % flag
    if flag:
        assert flag in a["risk_flags"]


def test_drawdown_flag_from_minus_five_percent():
    assert "DRAWDOWN_GT_5PCT" in _run(95.9)["risk_flags"]


def test_invalidated_signal_needs_review_even_above_the_stop():
    a = _run(100.0, signal_state="INVALIDATED")
    assert a["status"] == "REVIEW_INVALIDATION" and "SIGNAL_INVALIDATED" in a["risk_flags"]


def test_time_exit_after_the_horizon_and_a_warning_before_it():
    late = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)  # 17 sessions after the entry day
    a = _run(quote=_quote(104.0, now=late), now=late)
    assert a["status"] == "TIME_EXIT_REVIEW" and "TIME_EXPIRED" in a["risk_flags"]
    near = datetime(2026, 10, 30, 15, 0, tzinfo=UTC)  # 13 sessions held, 2 remaining
    b = _run(quote=_quote(103.0, now=near), now=near)
    assert b["status"] == "HOLD_THESIS_INTACT" and "TIME_NEAR_EXPIRY" in b["risk_flags"]
    assert any("2 trading session(s) remain" in r for r in b["reasons"])


def test_stale_or_missing_prices_pause_the_assessment():
    stale = _run(quote=_quote(90.0, age_s=300))  # older than the 120 s SLA while the market is open
    assert stale["status"] == "STALE_DATA" and "FEED_STALE" in stale["risk_flags"]
    missing = _run(None)
    assert missing["status"] == "STALE_DATA" and missing["price_status"] == "UNAVAILABLE"
    closed_market = _run(quote=_quote(104.0, age_s=6 * 3600), market_open=False)  # last close is fine after hours
    assert closed_market["status"] == "HOLD_THESIS_INTACT"


def test_an_unverified_print_at_the_stop_is_uncertain_not_an_exit():
    a = _run(quote=_quote(96.0, level=1))
    assert a["status"] == "PRICE_UNCERTAIN" and a["price_status"] == "FRESH_UNVERIFIED"
    assert _run(quote=_quote(104.0, level=1))["status"] == "HOLD_THESIS_INTACT"


def test_add_requires_a_calibrated_on_plan_thesis_back_in_the_zone():
    calibrated = _signal(confidence={"status": "CALIBRATED", "label": "Calibrated", "model_data_source": "alpaca",
                                     "p_target_first": 0.58})
    ok = dict(earnings_status="NONE_WITHIN_HORIZON_CONFIRMED")
    a = _run(101.0, signal=calibrated, **ok)  # 9 / 4.5 = 2.0 R remaining
    assert a["status"] == "ADD_CRITERIA_MET"
    assert _run(101.0, **ok)["status"] == "HOLD_THESIS_INTACT"  # UNVALIDATED: never an add
    assert _run(101.0, signal=calibrated)["status"] == "HOLD_THESIS_INTACT"  # earnings not confirmed clear
    assert _run(101.0, signal=calibrated, regime_label="risk_off", **ok)["status"] != "ADD_CRITERIA_MET"
    assert _run(101.0, signal=calibrated, signal_state="PUBLISHED", **ok)["status"] == "HOLD_THESIS_INTACT"


def test_off_plan_entries_lose_the_original_forecast():
    off = _ledger(("OPEN", "103.50", "10"))  # bought above the published zone
    a = _run(104.0, state=off)
    assert a["off_plan"] is True and "OFF_PLAN_ENTRY_OUTSIDE_SIGNAL_RANGE" in a["risk_flags"]
    assert a["original_scenario"]["p_target_first"] is None
    assert any("OFF-PLAN" in r for r in a["reasons"])


def test_closed_watching_and_manual_positions():
    closed = _ledger(("OPEN", "101.00", "10"), ("CLOSE", "106.00", "10"))
    assert _run(106.0, state=closed)["status"] == "CLOSED"
    watching = _ledger(("WATCH", "0", "0"))
    w = _run(101.0, state=watching)
    assert w["status"] == "MANUAL_WATCH" and "inside the published entry zone" in w["reasons"][1]
    manual = _run(104.0, signal=None, signal_state=None)
    assert manual["status"] == "MANUAL_WATCH" and manual["sessions_remaining"] is None


def test_notifications_fire_on_material_changes_only():
    hold = _run(104.0)
    assert should_notify(hold, None, NOW) is False  # nothing to say yet
    review = _run(96.0)
    assert should_notify(review, None, NOW) is True
    sent = {**review, "_key": materiality_key(review)}
    assert should_notify(_run(96.0), sent, NOW) is False  # same material state: no repeat
    exit_ = _run(96.0, session_open_price=95.0)
    assert should_notify(exit_, sent, NOW + timedelta(minutes=1)) is True  # urgent change bypasses the cooldown
    trim = _run(105.0)
    trim_sent = {**trim, "_key": "other"}
    assert should_notify(_run(104.0), trim_sent, NOW + timedelta(minutes=30)) is False  # within the 120 min cooldown
    assert should_notify(_run(104.0), trim_sent, NOW + timedelta(minutes=121)) is True
    stale = _run(quote=_quote(90.0, age_s=300))
    assert should_notify(stale, {**stale, "_key": "x"}, NOW + timedelta(hours=5)) is False  # stale -> stale is silent
