"""Sector exposure caps count names without a sector map as one bucket, live and for already-open signals.

Live Alpaca assets carry no sector (nothing writes sectors.json yet), so every candidate is sectorless. Open signals
store that bucket as risk.sector == "unknown"; new candidates must be counted under the same key, otherwise the
open-per-sector cap never sees the open ones.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from apex_worker.signals.policy import PolicyConfig, SignalPolicy

NOW = datetime(2026, 10, 14, 20, 15, tzinfo=UTC)


def _cand(symbol: str, sector: str | None, quality: float = 82.0) -> dict:
    return {
        "id": f"cand_{symbol.lower()}", "symbol": symbol, "gate_status": "PASS", "reason_codes": [], "quality_score": quality,
        "strategy_id": "h1_vcp_breakout",
        "_internal": {"sector": sector, "close": 50.0, "atr": 1.5, "invalidation_hint": 47.0, "resistance": None},
    }


def _decide(cands, open_signals):
    scan = SimpleNamespace(session="2026-10-14", asof_at="2026-10-14T20:15:00Z", candidates=cands, no_trade_reason=None)
    return SignalPolicy(PolicyConfig()).decide(scan, models={}, release_mode="DEMO", quotes={},
                                               open_signals=open_signals, now=NOW)


def test_open_sectorless_signals_count_against_a_sectorless_candidate():
    open_signals = [{"symbol": s, "risk": {"sector": "unknown"}} for s in ("DMOPEN1", "DMOPEN2")]
    (d,) = _decide([_cand("DMNEW1", None)], open_signals).decisions
    assert d.outcome == "REJECTED" and d.reasons == ["SECTOR_EXPOSURE_CAP"]


def test_open_signals_in_another_sector_do_not_block():
    open_signals = [{"symbol": s, "risk": {"sector": "technology"}} for s in ("DMOPEN1", "DMOPEN2")]
    # quality 72 passes the 70 bar and the caps, then stops at the research-setup bar (75) before a signal is built
    (d,) = _decide([_cand("DMNEW1", None, quality=72.0)], open_signals).decisions
    assert d.outcome == "REJECTED" and "RESEARCH_QUALITY_BELOW_THRESHOLD" in d.reasons
    assert "SECTOR_EXPOSURE_CAP" not in d.reasons
