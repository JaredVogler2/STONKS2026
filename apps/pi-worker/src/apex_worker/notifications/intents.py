"""Notification intent builders (transactional outbox payloads). event_key makes delivery idempotent."""

from __future__ import annotations

from datetime import datetime

from apex_worker.contracts import validate
from apex_worker.portfolio.assessment import materiality_key
from apex_worker.timeutil import iso_utc

CHANNEL_FOR_TIER = {"HIGH_CONVICTION": "high_conviction_alerts", "RESEARCH_SETUP": "market_intelligence"}


def _intent(
    event_key: str,
    kind: str,
    route: dict,
    priority: str,
    payload: dict,
    now: datetime,
    is_demo: bool,
    source_event_at: str | None = None,
) -> dict:
    intent = {
        "event_key": event_key[:200],
        "kind": kind,
        "route": route,
        "priority": priority,
        "payload": payload,
        "created_at": iso_utc(now),
        "is_demo": is_demo,
    }
    if source_event_at:
        intent["source_event_at"] = source_event_at
    validate("NotificationIntent", intent)
    return intent


def new_signal(signal: dict, tier: str, now: datetime) -> dict:
    return _intent(
        f"NEW_SIGNAL:{signal['id']}:r{signal['revision']}",
        "NEW_SIGNAL",
        {"type": "CHANNEL", "channel_key": CHANNEL_FOR_TIER[tier]},
        "HIGH" if tier == "HIGH_CONVICTION" else "NORMAL",
        {"tier": tier, "signal": signal},
        now,
        signal["is_demo"],
        signal.get("published_at"),
    )


def signal_event(signal: dict, event: dict, now: datetime) -> dict | None:
    et = event["event_type"]
    if et in ("CLOSED", "TARGET_REACHED", "STOP_HIT", "EXPIRED", "INVALIDATED"):
        kind, channel, prio = "SIGNAL_CLOSED", "exit_alerts", "HIGH"
    elif et in ("ACTIVATED", "TIME_WARNING", "UPGRADED", "DOWNGRADED", "INTRADAY_TARGET_TOUCH", "INTRADAY_STOP_TOUCH"):
        kind, channel, prio = "SIGNAL_UPDATE", "active_trades", "NORMAL"
    else:
        return None  # STALE_DATA etc. go to system status, not member channels
    payload = {
        "signal_id": signal["id"],
        "symbol": signal["symbol"],
        "event": event,
        "levels": signal["levels"],
        "is_demo": signal["is_demo"],
        "release_mode": signal["release_mode"],
    }
    return _intent(
        f"SIGEVT:{event['dedupe_key']}",
        kind,
        {"type": "CHANNEL", "channel_key": channel},
        prio,
        payload,
        now,
        signal["is_demo"],
        event["event_at"],
    )


def position_update(user_id: str, discord_user_id: str | None, assessment: dict, now: datetime, is_demo: bool) -> dict:
    key = f"POS:{user_id}:{assessment['position_id']}:{assessment['assessment_version']}:{materiality_key(assessment)}"
    route = {"type": "DM", "user_id": user_id}
    if discord_user_id:
        route["discord_user_id"] = discord_user_id
    prio = "URGENT" if assessment["status"] in ("REVIEW_INVALIDATION", "CONSIDER_EXIT", "PRICE_UNCERTAIN") else "NORMAL"
    return _intent(key, "POSITION_UPDATE", route, prio, {"assessment": assessment}, now, is_demo, assessment["asof_at"])


def system_alert(
    kind: str,
    title: str,
    detail: str,
    action: str,
    now: datetime,
    is_demo: bool,
    bucket: str,
    last_good_at: str | None = None,
    severity: str = "HIGH",
) -> dict:
    payload = {
        "alert": kind,
        "title": title[:120],
        "detail": detail[:600],
        "action": action[:400],
        "last_good_at": last_good_at,
    }
    return _intent(
        f"SYS:{kind}:{bucket}",
        "SYSTEM_ALERT",
        {"type": "CHANNEL", "channel_key": "system_status"},
        severity,
        payload,
        now,
        is_demo,
    )


def performance_report(stats: dict, period: str, now: datetime, is_demo: bool) -> dict:
    return _intent(
        f"PERF:{period}",
        "PERFORMANCE_REPORT",
        {"type": "CHANNEL", "channel_key": "performance_reports"},
        "LOW",
        {"period": period, "stats": stats},
        now,
        is_demo,
    )
