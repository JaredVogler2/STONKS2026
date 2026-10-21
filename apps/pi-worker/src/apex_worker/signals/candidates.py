"""Candidate engine: hard gates -> hypothesis triggers -> regime matching -> evidence groups -> concentration.

Deterministic: the same panel, session, catalysts and configuration always produce the same ranked list
(ties broken by symbol). Output candidates are SignalCandidate contract dicts with positive AND negative
evidence and machine-readable reason codes. An empty candidate list is a valid, honest NO TRADE outcome.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from apex_worker.contracts import validate
from apex_worker.features.compute import FEATURE_CATALOG, FEATURE_VERSION, integrity_hash
from apex_worker.features.panel import FeaturePanel, sector_context
from apex_worker.features.regime import REGIME_STATES
from apex_worker.market.calendar import MarketCalendar, default_calendar
from apex_worker.signals.context import context_frame, decision_instant
from apex_worker.signals.model_features import row_inputs
from apex_worker.signals.quality import QUALITY_VERSION, quality
from apex_worker.signals.strategies.library import Strategy, enabled_strategies
from apex_worker.timeutil import iso_utc

EXCLUDED_INSTRUMENTS = {"warrant", "preferred", "unit", "right"}


@dataclass(frozen=True)
class GateConfig:
    min_price: float = 10.0
    min_dollar_volume: float = 30e6
    min_avg_volume: float = 1e6
    min_history: int = 180
    max_extension_atr: float = 3.5
    risk_off_breaker: float = 0.6
    earnings_policy: str = "veto_within_horizon"  # or "caution_only"
    max_per_sector: int = 3
    horizon_sessions: int = 20
    allow_unknown_tradability: bool = False


@dataclass
class ScanStats:
    universe_size: int = 0
    eligible: int = 0
    scanned: int = 0
    triggered: int = 0
    vetoes: Counter = field(default_factory=Counter)

    def to_dict(self) -> dict:
        return {
            "universe_size": self.universe_size,
            "eligible": self.eligible,
            "scanned": self.scanned,
            "triggered": self.triggered,
            "vetoes": dict(sorted(self.vetoes.items())),
        }


@dataclass
class ScanResult:
    session: str
    asof_at: str
    candidates: list[dict]
    stats: ScanStats
    regime: dict
    no_trade_reason: str | None
    sectors: list[dict] = field(default_factory=list)


def candidate_id(symbol: str, strategy_id: str, session: str) -> str:
    return "cand_" + hashlib.sha256(f"{symbol}|{strategy_id}|{session}".encode()).hexdigest()[:24]


def _num(row, key) -> float | None:
    v = row.get(key)
    if v is None:
        return None
    v = float(v)
    return None if math.isnan(v) else v


def gate_row(
    row, meta, cfg: GateConfig, latest_bar_session: str | None, session: str, earnings: str
) -> tuple[str, list[str]]:
    """Returns (status, codes): VETO codes block, WATCH codes allow monitoring but never publication."""
    veto: list[str] = []
    watch: list[str] = []
    if meta is None or meta.asset_kind == "benchmark":
        return "VETO", ["NOT_TRADABLE_UNIVERSE"]
    if meta.exchange == "OTC":
        veto.append("OTC_EXCLUDED")
    if meta.instrument in EXCLUDED_INSTRUMENTS:
        veto.append(f"{meta.instrument.upper()}_EXCLUDED")
    if meta.leveraged:
        veto.append("LEVERAGED_ETF_EXCLUDED")
    if latest_bar_session != session:
        veto.append("STALE_OR_MISSING_BAR")
    close = _num(row, "close")
    if close is None or close < cfg.min_price:
        veto.append("PRICE_BELOW_MIN")
    dv = _num(row, "dollar_vol_med_20")
    if dv is None or dv < cfg.min_dollar_volume:
        veto.append("LOW_DOLLAR_VOLUME")
    av = _num(row, "avg_vol_20")
    if av is None or av < cfg.min_avg_volume:
        veto.append("LOW_SHARE_VOLUME")
    bars = _num(row, "bars_available")
    if bars is None or bars < cfg.min_history:
        veto.append("INSUFFICIENT_HISTORY")
    ext = _num(row, "dist_sma20_atr")
    if ext is not None and ext > cfg.max_extension_atr:
        veto.append("OVEREXTENDED")
    p_off = (_num(row, "regime_p_risk_off") or 0) + (_num(row, "regime_p_volatility_shock") or 0)
    if p_off >= cfg.risk_off_breaker:
        veto.append("RISK_OFF_CIRCUIT_BREAKER")
    if earnings == "WITHIN_HORIZON" and cfg.earnings_policy == "veto_within_horizon":
        veto.append("EARNINGS_WITHIN_HORIZON")
    if meta.tradability_status == "unknown" and not cfg.allow_unknown_tradability:
        watch.append("TRADABILITY_UNKNOWN")
    if meta.tradability_status == "excluded":
        veto.append("TRADABILITY_EXCLUDED")
    if veto:
        return "VETO", veto + watch
    if watch:
        return "WATCH", watch
    return "PASS", []


def _fmt(v: float | None, unit: str = "", pct: bool = False) -> str:
    if v is None:
        return "n/a"
    return f"{v * 100:+.1f}%" if pct else f"{v:.2f}{unit}"


def build_evidence(
    row,
    groups: dict,
    penalties: dict,
    strategy: Strategy,
    earnings: str,
    regime_label: str,
    asof: str,
    catalysts: list[dict],
) -> tuple[list[dict], list[dict]]:
    pos: list[dict] = []
    neg: list[dict] = []

    def add(lst, code, group, text, value=None, unit=None, url=None):
        item = {"code": code, "group": group, "text": text[:240], "value": value, "asof_at": asof}
        if unit:
            item["unit"] = unit
        if url:
            item["source_url"] = url
        lst.append(item)

    rs = _num(row, "rs_bench_20")
    if rs is not None:
        add(
            pos if rs > 0 else neg,
            "RELATIVE_STRENGTH_20D",
            "sector",
            f"20-session return vs benchmark {_fmt(rs, pct=True)} (log)",
            round(rs, 6),
            "log_return",
        )
    sq = _num(row, "squeeze_prev")
    if sq is not None and sq <= 0.4:
        add(
            pos,
            "VOLATILITY_COMPRESSION",
            "volatility",
            f"Prior-session Bollinger width at the {sq * 100:.0f}th percentile of 120 sessions",
            round(sq, 4),
            "percentile",
        )
    rv = _num(row, "rel_volume_1")
    if rv is not None:
        add(
            pos if rv >= 1.3 else neg,
            "RELATIVE_VOLUME",
            "volume",
            f"Volume {rv:.2f}x the prior 20-session average (same feed; IEX volume is not total market volume)",
            round(rv, 4),
            "ratio",
        )
    tr = _num(row, "trend_slope_50")
    if tr is not None:
        add(
            pos if tr > 0 else neg,
            "TREND_SLOPE_50D",
            "price",
            f"50-session trend slope {tr:+.3f} ATR-normalized units (momentum measures counted once, not as independent votes)",
            round(tr, 6),
            "atr_units",
        )
    p_label = regime_label or "unknown"
    add(
        pos if p_label in ("trending_up", "rebound") else neg,
        f"REGIME_{p_label.upper()}",
        "regime",
        f"Market regime estimate: {p_label.replace('_', ' ')} (rule-based probabilities, not a forecast)",
    )
    for c in catalysts[:2]:
        lst = pos if c.get("direction") == "potentially_positive" else neg
        add(
            lst,
            f"CATALYST_{c['catalyst_type'].upper()}"[:64],
            "catalyst",
            f"{c['headline'][:150]} (available {c['available_at']})",
            None,
            None,
            c.get("canonical_url"),
        )
    if earnings == "UNKNOWN":
        add(
            neg,
            "EARNINGS_DATE_UNKNOWN",
            "event_risk",
            "No verified earnings date: earnings inside the holding window cannot be ruled out",
        )
    elif earnings == "WITHIN_HORIZON":
        add(neg, "EARNINGS_WITHIN_HORIZON", "event_risk", "Scheduled earnings fall inside the holding window: gap risk")
    for code, pts in penalties.items():
        if code != "EARNINGS_DATE_UNKNOWN":
            add(
                neg,
                code,
                "liquidity" if code == "THIN_LIQUIDITY" else "price",
                f"Penalty {pts:.1f} points: {code.replace('_', ' ').lower()}",
                pts,
                "quality_points",
            )
    if not neg:
        add(neg, "STRATEGY_FAILURE_MODE", "model", f"Known failure mode: {strategy.spec.failure_modes[0]}")
    return pos[:16], neg[:16]


class CandidateEngine:
    def __init__(
        self,
        strategies: list[Strategy] | None = None,
        gates: GateConfig | None = None,
        calendar: MarketCalendar | None = None,
        is_demo: bool = True,
    ):
        self.strategies = strategies if strategies is not None else enabled_strategies()
        self.gates = gates or GateConfig()
        self.cal = calendar or default_calendar()
        self.is_demo = is_demo
        self._ctx_cache: dict[tuple, pd.DataFrame] = {}
        self._trig_cache: dict[tuple, pd.DataFrame] = {}

    def _context(self, panel: FeaturePanel, symbol: str, cats: list[dict]) -> pd.DataFrame:
        sym_cats = tuple(sorted(c["id"] for c in cats if c.get("symbol") == symbol))
        key = (panel.token, symbol, sym_cats)
        if self._ctx_cache and next(iter(self._ctx_cache))[0] != panel.token:
            self._ctx_cache.clear()  # a new panel: never serve frames built from older data
            self._trig_cache.clear()
        if key not in self._ctx_cache:
            self._ctx_cache[key] = context_frame(panel, symbol, cats, self.cal)
        return self._ctx_cache[key]

    def _triggers(self, panel: FeaturePanel, symbol: str, strat: Strategy, ctx: pd.DataFrame) -> pd.DataFrame:
        # triggers are causal (row t reads only rows <= t), so one full-frame evaluation serves every session
        # ctx objects stay referenced by _ctx_cache for this panel token, so id(ctx) cannot be recycled here
        key = (panel.token, symbol, strat.strategy_id, id(ctx))
        if key not in self._trig_cache:
            self._trig_cache[key] = strat.trigger_frame(ctx)
        return self._trig_cache[key]

    def scan(
        self,
        panel: FeaturePanel,
        session: pd.Timestamp,
        catalysts: Iterable[dict] = (),
        earnings_status: Callable[[str, str, int], str] | None = None,
        created_at: datetime | None = None,
    ) -> ScanResult:
        session = pd.Timestamp(session)
        sday = session.date().isoformat()
        asof = decision_instant(session, self.cal)
        asof_iso = iso_utc(asof)
        created = iso_utc(created_at or asof)
        cats = list(catalysts)
        stats = ScanStats(universe_size=len(panel.features))
        reg_row = panel.regime.loc[session] if session in panel.regime.index else None
        regime = {"label": "unknown", "probabilities": {}}
        if reg_row is not None and isinstance(reg_row.get("label"), str):
            regime = {
                "label": reg_row["label"],
                "probabilities": {s: round(float(reg_row[s]), 4) for s in REGIME_STATES},
            }
        out: list[dict] = []
        for symbol in sorted(panel.features):
            meta = panel.meta.get(symbol)
            f = panel.features[symbol]
            if session not in f.index:
                if meta is not None and meta.asset_kind != "benchmark":
                    stats.vetoes["STALE_OR_MISSING_BAR"] += 1
                continue
            ctx = self._context(panel, symbol, cats)
            row = ctx.loc[session].to_dict()
            earnings = earnings_status(symbol, sday, self.gates.horizon_sessions) if earnings_status else "UNKNOWN"
            latest = f.index[f.index <= session][-1].date().isoformat()
            status, codes = gate_row(row, meta, self.gates, latest, sday, earnings)
            if status == "VETO":
                for c in codes:
                    stats.vetoes[c] += 1
                continue
            stats.eligible += 1
            stats.scanned += 1
            sym_cats = sorted(
                [c for c in cats if c.get("symbol") == symbol and c["available_at"] <= asof_iso],
                key=lambda c: c["available_at"],
                reverse=True,
            )
            for strat in self.strategies:
                if strat.spec.requires_catalysts and not sym_cats:
                    continue
                trig = self._triggers(panel, symbol, strat, ctx)
                if session not in trig.index or not bool(trig.loc[session, "trigger"]):
                    continue
                stats.triggered += 1
                setup = float(trig.loc[session, "setup_score"])
                q = quality(row, None if math.isnan(setup) else setup, earnings)
                reasons = list(codes)
                cand_status = status
                if regime["label"] not in strat.spec.allowed_regimes:
                    reasons.append("REGIME_NOT_ALLOWED_FOR_STRATEGY")
                    cand_status = "WATCH"
                if q.score is None:
                    reasons.append("INSUFFICIENT_EVIDENCE_COVERAGE")
                    cand_status = "WATCH"
                reasons += [f"PENALTY_{k}" for k in q.penalties]
                pos, neg = build_evidence(
                    row, q.groups, q.penalties, strat, earnings, regime["label"], asof_iso, sym_cats
                )
                values = {
                    k: (
                        None
                        if row.get(k) is None or (isinstance(row.get(k), float) and math.isnan(row[k]))
                        else round(float(row[k]), 8)
                    )
                    for k in FEATURE_CATALOG
                }
                cand = {
                    "id": candidate_id(symbol, strat.strategy_id, sday),
                    "symbol": symbol,
                    "created_at": created,
                    "asof_at": asof_iso,
                    "strategy_id": strat.strategy_id,
                    "strategy_version": strat.spec.version,
                    "quality_score": q.score,
                    "gate_status": cand_status,
                    "reason_codes": sorted(set(reasons))[:32],
                    "evidence": {"positive": pos, "negative": neg},
                    "group_scores": q.groups,
                    "regime": regime["label"],
                    "feature_snapshot_hash": integrity_hash(
                        symbol, asof_iso, values, panel.bar_fingerprints.get(symbol, "")
                    ),
                    "is_demo": self.is_demo,
                }
                validate("SignalCandidate", cand)
                cand["_internal"] = {
                    "features": values,
                    "model_inputs": row_inputs(row),  # unrounded, for the shared model-vector assembler
                    "setup_score": setup,
                    "coverage": q.coverage,
                    "penalties": q.penalties,
                    "pivot": _num(trig.loc[session], "pivot"),
                    "invalidation_hint": _num(trig.loc[session], "invalidation_hint"),
                    "sector": meta.sector if meta else None,
                    "earnings_status": earnings,
                    "tradability_status": meta.tradability_status if meta else "unknown",
                    "quality_version": QUALITY_VERSION,
                    "regime_probabilities": regime["probabilities"],
                    "catalysts": sym_cats[:4],
                    "name": meta.name if meta else None,
                    "resistance": _num(
                        {
                            "r": ctx["close"].loc[:session].iloc[-121:-1].max()
                            if len(ctx.loc[:session]) > 60
                            else float("nan")
                        },
                        "r",
                    ),
                    "close": _num(row, "close"),
                    "atr": _num(row, "atr_14"),
                }
                out.append(cand)
        out.sort(key=lambda c: (-(c["quality_score"] or -1), c["symbol"], c["strategy_id"]))
        # one candidate per symbol (best strategy), then sector concentration penalty
        seen: set[str] = set()
        dedup: list[dict] = []
        for c in out:
            if c["symbol"] in seen:
                continue
            seen.add(c["symbol"])
            dedup.append(c)
        per_sector: Counter = Counter()
        for c in dedup:
            sec = c["_internal"]["sector"]
            if c["gate_status"] == "PASS":
                per_sector[sec] += 1
                if per_sector[sec] > self.gates.max_per_sector:
                    c["gate_status"] = "WATCH"
                    c["reason_codes"] = sorted(set(c["reason_codes"]) | {"SECTOR_CROWDING"})
        no_trade = None
        if not any(c["gate_status"] == "PASS" for c in dedup):
            top = stats.vetoes.most_common(1)
            no_trade = "NO_QUALIFIED_CANDIDATE" + (f" (most common veto: {top[0][0]})" if top else "")
        return ScanResult(sday, asof_iso, dedup, stats, regime, no_trade, sector_context(panel, session))


def feature_snapshots(scan: ScanResult, provider: str, feed: str, limit: int = 12) -> list[dict]:
    """FeatureSnapshot payloads for the scan's best candidates (raw values behind the evidence; NULL = insufficient
    history). The integrity hash is the candidate's feature_snapshot_hash, so the server copy is verifiable."""
    out = []
    for c in scan.candidates[:limit]:
        feats = (c.get("_internal") or {}).get("features")
        if not feats:
            continue
        snap = {
            "symbol": c["symbol"], "asof_at": c["asof_at"], "session_date": scan.session, "feature_version": FEATURE_VERSION,
            "features": feats, "sources": [{"provider": provider, "feed": feed, "latest_bar_at": c["asof_at"]}],
            "integrity_hash": c["feature_snapshot_hash"], "is_demo": bool(c.get("is_demo")),
        }
        validate("FeatureSnapshot", snap)
        out.append(snap)
    return out
