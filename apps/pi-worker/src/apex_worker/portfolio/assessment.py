"""Private, per-member position assessment (research only; never a broker instruction).

Uses ONE shared cached quote per symbol for every member. Evaluates the member's own FIFO cost basis,
the original signal thesis and the MARGINAL reward/risk from the current price (not anchored to entry).
Statuses:
  HOLD_THESIS_INTACT | REVIEW_INVALIDATION | CONSIDER_TRIM | CONSIDER_EXIT | TARGET_TOUCHED_FILL_UNKNOWN |
  TIME_EXIT_REVIEW | ADD_CRITERIA_MET | PRICE_UNCERTAIN | STALE_DATA | MANUAL_WATCH | CLOSED
ADD is only possible inside the original entry zone with a calibrated, on-plan thesis — never because the
position is losing. While ALLOW_PERSONALIZED_GROUP_GUIDANCE is off (default), `compliance_gated` is true
and renderers must use neutral, non-directive language.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from apex_worker.contracts import validate
from apex_worker.market.calendar import MarketCalendar, default_calendar
from apex_worker.market.quote_cache import CachedQuote
from apex_worker.market.validation import price_decision_class
from apex_worker.portfolio.ledger import LedgerState, PositionState, fmt
from apex_worker.timeutil import iso_utc, parse_utc

ASSESSMENT_VERSION = "assess-1"
URGENT = {"REVIEW_INVALIDATION", "CONSIDER_EXIT", "TARGET_TOUCHED_FILL_UNKNOWN", "PRICE_UNCERTAIN"}


@dataclass(frozen=True)
class AssessContext:
    now: datetime
    market_open: bool
    fresh_sla_seconds: int = 120
    regime_label: str = "unknown"
    earnings_status: str = "UNKNOWN"
    release_mode: str = "DEMO"
    allow_personalized_guidance: bool = False
    session_open_price: float | None = None
    previous_status: str | None = None  # last NOTIFIED status, for hysteresis


def _pct(a: float, b: float) -> float | None:
    return None if b == 0 else round(a / b - 1, 6)


def off_plan_reasons(state: LedgerState, signal: dict | None) -> list[str]:
    if not signal or not state.lots:
        return []
    lv = signal["levels"]
    lo, hi = Decimal(lv["entry_min"]), Decimal(lv["entry_max"])
    expires = parse_utc(signal["horizon"]["entry_expires_at"])
    first = sorted(state.lots, key=lambda x: (x.fill_at, x.seq))[0]
    reasons = []
    if not (lo <= first.price <= hi):
        reasons.append("ENTRY_OUTSIDE_SIGNAL_RANGE")
    if first.fill_at > expires:
        reasons.append("ENTRY_AFTER_SIGNAL_EXPIRY")
    if first.fill_at < parse_utc(signal["published_at"] if signal.get("published_at") else signal["created_at"]):
        reasons.append("ENTRY_BEFORE_SIGNAL_PUBLISHED")
    return reasons


def assess(
    *,
    position_id: str,
    symbol: str,
    state: LedgerState,
    signal: dict | None,
    signal_state: str | None,
    quote: CachedQuote | None,
    ctx: AssessContext,
    cal: MarketCalendar | None = None,
) -> dict:
    cal = cal or default_calendar()
    now = ctx.now
    flags: list[str] = []
    reasons: list[str] = []
    out: dict = {
        "position_id": position_id,
        "symbol": symbol,
        "asof_at": iso_utc(now),
        "assessment_version": ASSESSMENT_VERSION,
        "compliance_gated": not ctx.allow_personalized_guidance,
        "total_quantity": fmt(state.total_quantity),
        "avg_cost": fmt(state.avg_cost),
        "realized_pnl": fmt(state.realized_pnl),
        "risk_flags": [],
        "reasons": [],
    }
    off = off_plan_reasons(state, signal)
    out["off_plan"] = bool(off)
    flags += [f"OFF_PLAN_{r}"[:48] for r in off]
    if state.state is PositionState.CLOSED:
        out.update(status="CLOSED", price_status="UNAVAILABLE", reasons=["Position closed in your ledger."])
        return _finish(out, flags)
    if quote is not None:
        out["quote_provenance"] = {
            "provider": quote.provider,
            "feed": quote.feed,
            "latency_class": quote.latency_class,
            "ts_exchange": iso_utc(quote.ts_exchange),
            "ts_received": iso_utc(quote.ts_received),
            "delayed_by_seconds": quote.delayed_by_seconds,
            "is_demo": quote.feed == "demo",
        }
        out["quote_age_seconds"] = quote.age_seconds(now)
    pclass = price_decision_class(
        quote.validation.level if quote else 0,
        quote.age_seconds(now) if quote else None,
        ctx.fresh_sla_seconds,
        ctx.market_open,
    )
    out["price_status"] = pclass
    if state.state is PositionState.WATCHING:
        out["status"] = "MANUAL_WATCH"
        reasons.append("Watching only: no fills recorded.")
        if signal and quote:
            lv = signal["levels"]
            inside = float(lv["entry_min"]) <= quote.price <= float(lv["entry_max"])
            reasons.append(
                "Price is inside the published entry zone." if inside else "Price is outside the published entry zone."
            )
        out["reasons"] = reasons
        return _finish(out, flags)
    if pclass in ("STALE", "UNAVAILABLE"):
        flags.append("FEED_STALE")
        out["status"] = "STALE_DATA"
        reasons.append("No fresh verified price: assessment paused. This is not a sell signal; check your broker.")
        out["reasons"] = reasons
        return _finish(out, flags)
    price = quote.price  # type: ignore[union-attr]
    out["price"] = f"{price:.4f}".rstrip("0").rstrip(".")
    unreal = state.unrealized_pnl(Decimal(str(price)))
    out["unrealized_pnl"] = fmt(unreal)
    avg = float(state.avg_cost)
    out["unrealized_pct"] = _pct(price, avg) if avg else None
    opened_day = cal.session_date_for(state.opened_at) if state.opened_at else None
    today = cal.latest_completed_session(now) if not ctx.market_open else cal.session_date_for(now)
    held = cal.count_sessions_after(opened_day, today) if opened_day else 0
    out["sessions_held"] = held
    stop = target = None
    if signal:
        lv = signal["levels"]
        stop, target = float(lv["stop_level"]), float(lv["target_primary"])
        horizon = int(signal["horizon"]["max_sessions"])
        out["sessions_remaining"] = horizon - held
        out["distance_to_stop_pct"] = _pct(price, stop)
        out["distance_to_target_pct"] = _pct(target, price)
        out["marginal_reward_to_risk"] = round((target - price) / (price - stop), 3) if price > stop else None
        out["original_scenario"] = {
            "signal_id": signal["id"],
            "entry_min": lv["entry_min"],
            "entry_max": lv["entry_max"],
            "stop_level": lv["stop_level"],
            "target_primary": lv["target_primary"],
            "horizon_max_sessions": horizon,
            "confidence_status": signal["confidence"]["status"],
            "p_target_first": None if off else signal["confidence"].get("p_target_first"),
            "note": "Original forecast does not apply to off-plan entries." if off else "Original published scenario.",
        }
    else:
        out["sessions_remaining"] = None
        out["original_scenario"] = {"note": "Manual position: no APEX thesis or levels."}
    regime_bad = ctx.regime_label in ("risk_off", "volatility_shock")
    if regime_bad:
        flags.append("REGIME_RISK_OFF")
    if ctx.earnings_status == "UNKNOWN":
        flags.append("EARNINGS_UNKNOWN")
    elif ctx.earnings_status == "WITHIN_HORIZON":
        flags.append("EARNINGS_WITHIN_HORIZON")
    if out["unrealized_pct"] is not None and out["unrealized_pct"] <= -0.05:
        flags.append("DRAWDOWN_GT_5PCT")
    status = "HOLD_THESIS_INTACT"
    if pclass == "FRESH_UNVERIFIED" and stop is not None and price <= stop:
        status = "PRICE_UNCERTAIN"
        reasons.append("PRICE UNCERTAIN - CHECK BROKER: an unverified print is at/below the invalidation level.")
    elif signal is None:
        status = "MANUAL_WATCH"
        reasons.append("No APEX levels for this manual position; tracking P&L only.")
    else:
        assert stop is not None and target is not None
        gap = ctx.session_open_price is not None and ctx.session_open_price < stop
        if signal_state == "INVALIDATED":
            flags.append("SIGNAL_INVALIDATED")
        if signal_state in ("CLOSED", "EXPIRED"):
            flags.append(f"SIGNAL_{signal_state}")
        if price <= stop:
            flags.append("STOP_BREACHED")
            if gap:
                flags.append("GAP_BELOW_STOP")
        if price >= target:
            flags.append("TARGET_BREACHED")
        elif out["distance_to_target_pct"] is not None and out["distance_to_target_pct"] <= 0.015:
            flags.append("TARGET_NEARING")
        rem = out["sessions_remaining"]
        if rem is not None and rem <= 0:
            flags.append("TIME_EXPIRED")
        elif rem is not None and rem <= 3:
            flags.append("TIME_NEAR_EXPIRY")
        mrr = out["marginal_reward_to_risk"]
        if "STOP_BREACHED" in flags and (gap or regime_bad):
            status = "CONSIDER_EXIT"
            reasons.append(
                "Invalidation level breached"
                + (" on an overnight gap" if gap else "")
                + (" in a risk-off regime" if regime_bad else "")
                + "."
            )
        elif "STOP_BREACHED" in flags or "SIGNAL_INVALIDATED" in flags:
            status = "REVIEW_INVALIDATION"
            reasons.append("The original thesis invalidation condition is met.")
        elif "TARGET_BREACHED" in flags:
            status = "TARGET_TOUCHED_FILL_UNKNOWN"
            reasons.append("Price reached the published target; whether a limit order filled is unknown.")
        elif "TIME_EXPIRED" in flags:
            status = "TIME_EXIT_REVIEW"
            reasons.append("The maximum holding horizon (trading sessions) has elapsed.")
        elif (mrr is not None and mrr < (1.0 if ctx.previous_status == "CONSIDER_TRIM" else 0.75)) or (
            regime_bad and (out["unrealized_pct"] or 0) > 0
        ):
            status = "CONSIDER_TRIM"
            reasons.append(
                f"Remaining reward/risk from the current price is {mrr:.2f}."
                if mrr is not None
                else "Regime turned risk-off while the position is in profit."
            )
        elif (
            signal_state == "ACTIVE"
            and signal["confidence"]["status"] == "CALIBRATED"
            and not off
            and float(signal["levels"]["entry_min"]) <= price <= float(signal["levels"]["entry_max"])
            and mrr is not None
            and mrr >= 2
            and not regime_bad
            and ctx.earnings_status == "NONE_WITHIN_HORIZON_CONFIRMED"
        ):
            status = "ADD_CRITERIA_MET"
            reasons.append(
                "Price is back inside the original entry zone with the calibrated thesis intact "
                "(never an average-down rule)."
            )
        else:
            reasons.append("Thesis intact: above invalidation, below target, within horizon.")
        if off:
            reasons.append("OFF-PLAN ENTRY: reassessment required; the original success probability is not applied.")
        if "TIME_NEAR_EXPIRY" in flags:
            reasons.append(f"{rem} trading session(s) remain before the time exit.")
    out["status"] = status
    out["reasons"] = reasons
    out["revised_scenario"] = {
        "price": out.get("price"),
        "marginal_reward_to_risk": out.get("marginal_reward_to_risk"),
        "sessions_remaining": out.get("sessions_remaining"),
        "regime": ctx.regime_label,
        "earnings_status": ctx.earnings_status,
    }
    return _finish(out, flags)


def _finish(out: dict, flags: list[str]) -> dict:
    out["risk_flags"] = sorted(set(flags))[:16]
    out["reasons"] = [r[:240] for r in out.get("reasons", [])][:8]
    for k in ("unrealized_pct", "distance_to_stop_pct", "distance_to_target_pct", "marginal_reward_to_risk"):
        v = out.get(k)
        if isinstance(v, float) and not math.isfinite(v):
            out[k] = None
    validate("PositionAssessment", out)
    return out


def materiality_key(a: dict) -> str:
    pct = a.get("unrealized_pct")
    bucket = "na" if pct is None else str(math.floor(pct / 0.05))
    raw = f"{a['status']}|{','.join(a['risk_flags'])}|{bucket}|{a.get('price_status')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def should_notify(a: dict, last_sent: dict | None, now: datetime, cooldown_minutes: int = 120) -> bool:
    """Notify on material state change only; urgent states bypass the cooldown. Never spam per 1% move."""
    if a["status"] == "CLOSED":
        return False
    if last_sent is None:
        return a["status"] not in ("HOLD_THESIS_INTACT", "MANUAL_WATCH")
    if materiality_key(a) == last_sent.get("_key"):
        return False
    if a["status"] in URGENT and a["status"] != last_sent.get("status"):
        return True
    if a["status"] == "STALE_DATA" and last_sent.get("status") == "STALE_DATA":
        return False
    last_at = parse_utc(last_sent["asof_at"])
    return (now - last_at).total_seconds() >= cooldown_minutes * 60
