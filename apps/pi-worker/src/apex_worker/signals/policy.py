"""Signal promotion policy: candidate -> (maybe) published research alert.

Three stages:
 1. eligibility (already enforced by CandidateEngine gates; only PASS candidates arrive here)
 2. forecast: the champion model for the strategy, ONLY if its signed manifest is valid, unexpired and
    compatible. Otherwise confidence is UNVALIDATED and no probability is shown.
 3. economics: net reward/risk after costs, expected value from the calibrated partition, concentration.
Tiers:
  HIGH_CONVICTION  -> #high-conviction-alerts; requires CALIBRATED model, p >= frozen threshold, EV > 0
  RESEARCH_SETUP   -> #market-intelligence only, labelled UNVALIDATED/EXPERIMENTAL, never "high confidence"
If nothing qualifies, the outcome is NO TRADE with reasons. No alerts are created to fill a quota.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from apex_worker.contracts import validate
from apex_worker.market.calendar import MarketCalendar, default_calendar
from apex_worker.models.store import ActiveModel
from apex_worker.research.dataset import MODEL_FEATURES
from apex_worker.signals.levels import LEVELS_VERSION, LevelConfig, Levels, compute_levels, dec_str
from apex_worker.signals.lifecycle import signal_id_for
from apex_worker.signals.model_features import assemble
from apex_worker.timeutil import iso_utc, parse_utc

POLICY_VERSION = "policy-1"


@dataclass(frozen=True)
class PolicyConfig:
    min_quality: float = 70.0
    max_new_per_session: int = 3
    max_new_per_sector: int = 1
    max_open_per_sector: int = 2
    levels: LevelConfig = LevelConfig()
    allow_experimental_high_conviction: bool = False
    publish_research_setups: bool = True
    research_min_quality: float = 75.0
    research_max_per_session: int = 1


@dataclass
class PolicyDecision:
    candidate_id: str
    symbol: str
    outcome: str  # HIGH_CONVICTION | RESEARCH_SETUP | REJECTED
    reasons: list[str]
    signal: dict | None = None


@dataclass
class PolicyOutcome:
    session: str
    decisions: list[PolicyDecision] = field(default_factory=list)
    no_trade_reason: str | None = None

    @property
    def signals(self) -> list[dict]:
        return [d.signal for d in self.decisions if d.signal]


def model_row(cand: dict, lv: Levels) -> dict | None:
    """The live model input vector, built by the same assembler as the training rows (APEX-001).

    None when the candidate does not carry the unrounded row inputs (built by an older engine): no forecast is better
    than one computed on an imputed vector."""
    i = cand["_internal"]
    inputs = i.get("model_inputs")
    if inputs is None:
        return None
    return assemble(inputs, i.get("setup_score"), cand["quality_score"], cand.get("group_scores") or {}, lv,
                    i.get("earnings_status") == "UNKNOWN")


def confidence_block(model: ActiveModel | None, row: dict | None, now_iso: str, release_mode: str) -> tuple[dict, dict | None]:
    if model is None:
        return {
            "status": "UNVALIDATED",
            "label": "UNVALIDATED - no calibrated probability",
            "model_data_source": "none",
        }, None
    man = model.manifest
    ev = man.get("evaluation", {})
    status = ev.get("confidence_status", "UNVALIDATED")
    if man.get("validation_expires_at", "") <= now_iso:
        status = "UNVALIDATED"
    if man.get("data_source") == "synthetic_fixture" and release_mode != "DEMO":
        status = "UNVALIDATED"
    if status == "UNVALIDATED":
        return {
            "status": "UNVALIDATED",
            "label": "UNVALIDATED - model gates not met",
            "model_data_source": man.get("data_source", "none"),
            "calibration_version": man["model_id"],
        }, None
    if row is None:  # fail closed: never forecast on a vector that could differ from training (APEX-001)
        return {
            "status": "UNVALIDATED",
            "label": "UNVALIDATED - model input vector unavailable",
            "model_data_source": man.get("data_source", "none"),
            "calibration_version": man["model_id"],
        }, None
    p = model.model.predict([{k: row.get(k) for k in MODEL_FEATURES}])[0]
    n = int(ev.get("holdout_n") or 0)
    # APEX-002: no per-alert interval. A binomial band from the holdout count treats a fitted per-stock forecast as an
    # iid sample proportion and ignores parameter, calibration, dependence and regime uncertainty, so it would be false
    # precision. Intervals stay unset (and are not rendered) until a block-bootstrap interval with measured coverage on
    # held-out data exists; sample_count is the plain holdout size, not an effective sample size.
    label = ("Calibrated estimate" if status == "CALIBRATED" else "EXPERIMENTAL estimate") + (
        " (interval not established)"
    ) + (" (SYNTHETIC DEMO MODEL)" if man.get("data_source") == "synthetic_fixture" else "")
    conf = {
        "status": status,
        "label": label[:80],
        "p_target_first": round(float(p[0]), 4),
        "p_stop_first": round(float(p[1]), 4),
        "p_expire": round(float(p[2]), 4),
        "sample_count": n,
        "calibration_version": man["model_id"],
        "model_data_source": man.get("data_source", "none"),
    }
    return conf, {"threshold": float(ev.get("threshold_p_target") or 1.0), "p": p}


def immutable_hash(signal: dict) -> str:
    body = {k: v for k, v in signal.items() if k not in ("state", "immutable_hash", "published_at")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class SignalPolicy:
    def __init__(self, config: PolicyConfig | None = None, calendar: MarketCalendar | None = None):
        self.cfg = config or PolicyConfig()
        self.cal = calendar or default_calendar()

    def decide(
        self,
        scan,
        *,
        models: dict[str, ActiveModel],
        release_mode: str,
        quotes: dict[str, dict],
        open_signals: list[dict],
        now: datetime,
        compliance: dict[str, bool] | None = None,
    ) -> PolicyOutcome:
        out = PolicyOutcome(scan.session)
        now_iso = iso_utc(now)
        is_demo = release_mode == "DEMO"
        open_by_sector: dict[str, int] = {}
        for s in open_signals:
            sec = (s.get("risk") or {}).get("sector")
            open_by_sector[sec] = open_by_sector.get(sec, 0) + 1
        new_by_sector: dict[str, int] = {}
        open_symbols = {s.get("symbol") for s in open_signals}
        published = 0
        research_published = 0
        asof = parse_utc(scan.asof_at)
        session_day = self.cal.latest_completed_session(asof)
        expiry_day = self.cal.add_sessions(session_day, self.cfg.levels.entry_valid_sessions)
        entry_expires = self.cal.session(expiry_day).close_utc
        for cand in scan.candidates:
            reasons: list[str] = []
            internal = cand["_internal"]
            if cand["gate_status"] != "PASS":
                out.decisions.append(
                    PolicyDecision(
                        cand["id"],
                        cand["symbol"],
                        "REJECTED",
                        ["GATE_" + cand["gate_status"], *cand["reason_codes"][:4]],
                    )
                )
                continue
            if (cand["quality_score"] or 0) < self.cfg.min_quality:
                out.decisions.append(
                    PolicyDecision(cand["id"], cand["symbol"], "REJECTED", ["QUALITY_BELOW_THRESHOLD"])
                )
                continue
            if cand["symbol"] in open_symbols:
                out.decisions.append(PolicyDecision(cand["id"], cand["symbol"], "REJECTED", ["OPEN_SIGNAL_EXISTS"]))
                continue
            lv = compute_levels(
                internal["close"],
                internal["atr"],
                internal.get("invalidation_hint"),
                internal.get("resistance"),
                self.cfg.levels,
            )
            if not isinstance(lv, Levels):
                out.decisions.append(PolicyDecision(cand["id"], cand["symbol"], "REJECTED", [lv.code]))
                continue
            sector = internal.get("sector")
            if open_by_sector.get(sector, 0) + new_by_sector.get(sector, 0) >= self.cfg.max_open_per_sector:
                out.decisions.append(PolicyDecision(cand["id"], cand["symbol"], "REJECTED", ["SECTOR_EXPOSURE_CAP"]))
                continue
            if new_by_sector.get(sector, 0) >= self.cfg.max_new_per_sector:
                out.decisions.append(PolicyDecision(cand["id"], cand["symbol"], "REJECTED", ["SECTOR_DAILY_CAP"]))
                continue
            if published >= self.cfg.max_new_per_session:
                out.decisions.append(PolicyDecision(cand["id"], cand["symbol"], "REJECTED", ["SESSION_ALERT_CAP"]))
                continue
            row = model_row(cand, lv)
            conf, fc = confidence_block(models.get(cand["strategy_id"]), row, now_iso, release_mode)
            tier = "RESEARCH_SETUP"
            if fc is not None:
                p = fc["p"]
                cost = 2 * self.cfg.levels.cost_bps_per_side / 1e4 * lv.mid
                ev = p[0] * (lv.target - lv.mid) - p[1] * (lv.mid - lv.stop) - cost
                allowed = conf["status"] == "CALIBRATED" or (self.cfg.allow_experimental_high_conviction and is_demo)
                if allowed and p[0] >= fc["threshold"] and ev > 0:
                    tier = "HIGH_CONVICTION"
                else:
                    reasons.append(
                        "FORECAST_BELOW_THRESHOLD"
                        if p[0] < fc["threshold"]
                        else ("NEGATIVE_EXPECTED_VALUE" if ev <= 0 else "MODEL_NOT_CALIBRATED")
                    )
            else:
                reasons.append("MODEL_UNVALIDATED")
            if tier == "RESEARCH_SETUP":
                if not self.cfg.publish_research_setups:
                    out.decisions.append(PolicyDecision(cand["id"], cand["symbol"], "REJECTED", reasons))
                    continue
                if (cand["quality_score"] or 0) < self.cfg.research_min_quality:
                    out.decisions.append(
                        PolicyDecision(
                            cand["id"], cand["symbol"], "REJECTED", [*reasons, "RESEARCH_QUALITY_BELOW_THRESHOLD"]
                        )
                    )
                    continue
                if research_published >= self.cfg.research_max_per_session:
                    out.decisions.append(
                        PolicyDecision(cand["id"], cand["symbol"], "REJECTED", [*reasons, "RESEARCH_SESSION_CAP"])
                    )
                    continue
                research_published += 1
            sig = self._build_signal(
                cand,
                lv,
                conf,
                scan,
                quotes.get(cand["symbol"]),
                release_mode,
                entry_expires,
                now,
                tier,
                compliance or {},
            )
            out.decisions.append(PolicyDecision(cand["id"], cand["symbol"], tier, reasons, sig))
            published += 1
            open_symbols.add(cand["symbol"])
            new_by_sector[sector] = new_by_sector.get(sector, 0) + 1
        if not out.signals:
            out.no_trade_reason = scan.no_trade_reason or "NO_CANDIDATE_PASSED_POLICY"
        return out

    def _build_signal(
        self,
        cand: dict,
        lv: Levels,
        conf: dict,
        scan,
        quote: dict | None,
        release_mode: str,
        entry_expires: datetime,
        now: datetime,
        tier: str,
        compliance: dict[str, bool],
    ) -> dict:
        internal = cand["_internal"]
        is_demo = release_mode == "DEMO"
        sid = signal_id_for(cand["symbol"], cand["strategy_id"], scan.session, POLICY_VERSION)
        pos = cand["evidence"]["positive"][:5]
        fillers = [
            {
                "code": "STRATEGY_SETUP",
                "group": "model",
                "text": f"{cand['strategy_id']} setup trigger fired",
                "asof_at": scan.asof_at,
            },
            {
                "code": "QUALITY_RANK",
                "group": "model",
                "text": f"Quality rank {cand['quality_score']:.0f}/100 (ranking only)",
                "asof_at": scan.asof_at,
            },
        ]
        pos = [*pos, *fillers][: max(2, len(pos))]
        neg = cand["evidence"]["negative"][:6] or [
            {
                "code": "UNKNOWN_RISKS",
                "group": "model",
                "text": "Model cannot observe all risks",
                "asof_at": scan.asof_at,
            }
        ]
        if quote:
            q_price = quote["price"]
            prov = quote["provenance"]
            age = quote.get("staleness_seconds", 0)
        else:
            q_price = dec_str(internal["close"])
            prov = {
                "provider": "replay" if is_demo else "alpaca",
                "feed": "demo" if is_demo else "sip",
                "latency_class": "replay" if is_demo else "historical",
                "ts_received": scan.asof_at,
                "is_demo": is_demo,
            }
            age = max(0, int((now - parse_utc(scan.asof_at)).total_seconds()))
        risk_note = "Overnight gaps can exceed the stop; the stop is not a guaranteed exit price."
        earnings = internal.get("earnings_status", "UNKNOWN")
        signal = {
            "id": sid,
            "revision": 1,
            "symbol": cand["symbol"],
            "side": "LONG",
            "state": "PUBLISHED",
            "created_at": iso_utc(now),
            "asof_at": scan.asof_at,
            "published_at": iso_utc(now),
            "candidate_id": cand["id"],
            "strategy_id": cand["strategy_id"],
            "strategy_version": cand["strategy_version"],
            "model_version": conf.get("calibration_version", "none"),
            "policy_version": f"{POLICY_VERSION}+{LEVELS_VERSION}",
            "feature_version": "apex-features-1",
            "quality_score": float(cand["quality_score"]),
            "levels": {
                "entry_min": dec_str(lv.entry_min),
                "entry_max": dec_str(lv.entry_max),
                "stop_level": dec_str(lv.stop),
                "target_primary": dec_str(lv.target),
                "reward_to_risk": dec_str(lv.reward_to_risk_net),
                "max_slippage_bps": int(self.cfg.levels.cost_bps_per_side * 2),
                "level_basis": list(lv.basis)[:6],
            },
            "horizon": {
                "min_sessions": 3,
                "max_sessions": self.cfg.levels.horizon_max_sessions,
                "entry_expires_at": iso_utc(entry_expires),
                "entry_valid_sessions": self.cfg.levels.entry_valid_sessions,
                "basis": "HEURISTIC" if conf["status"] == "UNVALIDATED" else "CALIBRATED_TIME_TO_HIT",
            },
            "event_definition": {
                "type": "TARGET_BEFORE_STOP",
                "horizon_sessions": self.cfg.levels.horizon_max_sessions,
                "measured_from": "ENTRY_FILL",
                "same_bar_rule": "STOP_FIRST",
            },
            "confidence": conf,
            "quote": {
                "price": q_price,
                "provenance": prov,
                "age_seconds": int(age),
                "is_tradable_proxy": bool(prov.get("latency_class") == "live" and prov.get("feed") == "sip"),
            },
            "thesis": {
                "summary": (
                    f"{'HIGH-CONVICTION' if tier == 'HIGH_CONVICTION' else 'Research setup'}: "
                    f"{cand['strategy_id'].split('_', 1)[1].replace('_', ' ')} in {cand['symbol']}"
                )[:400],
                "reasons": pos,
                "counter_evidence": neg,
                "regime": {
                    "label": scan.regime.get("label", "unknown"),
                    "probabilities": scan.regime.get("probabilities", {}),
                    "version": "regime-rules-1",
                },
                "catalysts": [
                    {
                        "catalyst_id": c["id"],
                        "catalyst_type": c["catalyst_type"],
                        "headline": c["headline"][:200],
                        "available_at": c["available_at"],
                        "direction": c["direction"],
                        **({"source_url": c["canonical_url"]} if c.get("canonical_url") else {}),
                    }
                    for c in internal.get("catalysts", [])[:6]
                ],
            },
            "risk": {
                "cost_bps_per_side": self.cfg.levels.cost_bps_per_side,
                "earnings_status": earnings,
                "tradability_status": internal.get("tradability_status", "unknown"),
                "gap_risk_note": risk_note,
                "sector": internal.get("sector") or "unknown",
                "notional_example": "Illustration only: risking 0.5% of a hypothetical account on the entry-to-stop "
                "distance. APEX does not know your capital or risk tolerance.",
            },
            "release_mode": release_mode,
            "is_demo": is_demo,
        }
        if internal.get("name"):
            signal["company_name"] = internal["name"][:120]
        if lv.target_secondary:
            signal["levels"]["target_secondary"] = dec_str(lv.target_secondary)
        if not compliance.get("ALLOW_EXTERNAL_MARKET_DATA_REDISPLAY") and not is_demo:
            signal["quote"]["is_tradable_proxy"] = False
        signal["immutable_hash"] = immutable_hash(signal)
        validate("TradeSignal", signal)
        return signal
