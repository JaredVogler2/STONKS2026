"""Deterministic, clearly DEMO-labelled sample payloads for `apex bot --dry-run` and the bot's tests.

These are NOT market data: fictional DM* tickers, synthetic prices, UNVALIDATED confidence.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

from apex_worker.contracts import validate
from apex_worker.timeutil import iso_utc

GAP_NOTE = "Overnight gaps can exceed the stop; the stop is not a guaranteed exit price."


def sample_signal_id(symbol: str, seed: str = "sample") -> str:
    return "sig_" + hashlib.sha256(f"{symbol}|{seed}|discord-sample".encode()).hexdigest()[:24]


def sample_signal(now: datetime, *, symbol: str = "DMSEMI1", seed: str = "sample", entry_min: str = "100.00",
                  entry_max: str = "102.00", stop: str = "96.50", target: str = "110.00", state: str = "PUBLISHED",
                  long_text: bool = False) -> dict:
    asof = now - timedelta(minutes=15)
    reason_text = ("Relative strength vs sector ETF in the top decile over 20 sessions " * (4 if long_text else 1))[:240]
    sig = {
        "id": sample_signal_id(symbol, seed), "revision": 1, "symbol": symbol,
        "company_name": f"Demo company {symbol} (DEMO)", "side": "LONG", "state": state,
        "created_at": iso_utc(now), "asof_at": iso_utc(asof), "published_at": iso_utc(now),
        "strategy_id": "rs_breakout_demo", "strategy_version": "1.0.0", "model_version": "none",
        "policy_version": "policy-demo+levels-demo", "feature_version": "apex-features-1", "quality_score": 78.0,
        "levels": {"entry_min": entry_min, "entry_max": entry_max, "stop_level": stop, "target_primary": target,
                   "reward_to_risk": "1.8", "max_slippage_bps": 20, "level_basis": ["ATR(14) x 1.5 stop", "prior high target"]},
        "horizon": {"min_sessions": 3, "max_sessions": 15, "entry_expires_at": iso_utc(now + timedelta(days=2)),
                    "entry_valid_sessions": 2, "basis": "HEURISTIC"},
        "event_definition": {"type": "TARGET_BEFORE_STOP", "horizon_sessions": 15, "measured_from": "ENTRY_FILL",
                             "same_bar_rule": "STOP_FIRST"},
        "confidence": {"status": "UNVALIDATED", "label": "Unvalidated", "model_data_source": "none"},
        "quote": {"price": "101.20", "age_seconds": 900, "is_tradable_proxy": False,
                  "provenance": {"provider": "replay", "feed": "demo", "latency_class": "replay",
                                 "ts_received": iso_utc(asof), "is_demo": True}},
        "thesis": {
            "summary": f"Research setup (DEMO): relative-strength breakout in {symbol}",
            "reasons": [
                {"code": "RS_SECTOR_TOP_DECILE", "group": "sector", "text": reason_text, "value": 0.91, "unit": "pctile"},
                {"code": "VOLUME_PARTICIPATION", "group": "volume", "text": "Volume 1.8x its 50-session median on the "
                                                                               "breakout session", "value": 1.8, "unit": "x"},
                {"code": "VOL_COMPRESSION", "group": "volatility", "text": "10-session range in the lowest quintile of the "
                                                                           "past year before expansion"},
            ],
            "counter_evidence": [
                {"code": "EARNINGS_UNKNOWN", "group": "event_risk", "text": "Earnings date unknown - treat as possible "
                                                                            "within horizon"},
            ],
            "regime": {"label": "risk_on", "version": "regime-rules-1"},
        },
        "risk": {"cost_bps_per_side": 5.0, "earnings_status": "UNKNOWN", "tradability_status": "unknown",
                 "gap_risk_note": GAP_NOTE},
        "release_mode": "DEMO", "is_demo": True,
    }
    sig["immutable_hash"] = hashlib.sha256(json.dumps(sig, sort_keys=True).encode()).hexdigest()
    validate("TradeSignal", sig)
    return sig


def sample_assessment(position_id: str, symbol: str, now: datetime, *, status: str = "REVIEW_INVALIDATION",
                      version: int = 1) -> dict:
    return {
        "position_id": position_id, "symbol": symbol, "asof_at": iso_utc(now), "assessment_version": f"v{version}",
        "status": status, "price_status": "FRESH_VERIFIED", "price": "97.10", "quote_age_seconds": 120,
        "quote_provenance": {"provider": "replay", "feed": "demo", "latency_class": "replay", "ts_received": iso_utc(now),
                             "is_demo": True},
        "total_quantity": "20", "avg_cost": "101", "unrealized_pnl": "-78", "unrealized_pct": -0.0386,
        "distance_to_stop_pct": -0.006, "distance_to_target_pct": 0.133, "sessions_held": 2, "sessions_remaining": 13,
        "risk_flags": ["NEAR_STOP"], "reasons": ["Price is within 1% of the published stop (DEMO)"],
        "compliance_gated": True,
    }
