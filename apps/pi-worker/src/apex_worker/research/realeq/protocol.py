"""Frozen experiment protocol `realeq-protocol-1` (research-results/real-equities/PROTOCOL.md is the human copy).

Everything a result depends on is fixed here BEFORE any real data is inspected: sample construction, chronological
boundaries, execution and cost rules, the primary configuration per hypothesis, baselines and the disposition rules.
Changing any value is a NEW protocol version (bump PROTOCOL_VERSION and the PROTOCOL.md header); results produced
under different versions are never pooled or compared as if they were one experiment.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date

import pandas as pd

from apex_worker.research.training_data import SECTOR_ETFS
from apex_worker.research.validation import embargo_limit

PROTOCOL_VERSION = "realeq-protocol-1"
EXPERIMENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}[a-z0-9]$")
YEARS_BOUNDS = (3, 10)  # the pinned NYSE calendar starts 2016-01-01; at least warm-up + development + holdout
_SECTORS = list(SECTOR_ETFS)

PROTOCOL: dict = {
    "version": PROTOCOL_VERSION,
    "data": {
        "provider": "alpaca",
        "feed": "sip",  # consolidated volume; IEX-only would skew volume/liquidity features (never a silent fallback)
        "adjustment": "split",  # price-only returns: dividends are NOT modelled
        "timeframe": "1Day",
        "universe": "fixed78",
        "years_default": 8,
        "years_bounds": list(YEARS_BOUNDS),
        "traded_assets": "66 member stocks only; SPY and the 11 sector SPDRs are reference series, never traded",
    },
    "abort_rules": {
        "credentials_missing": "BLOCKED_ALPACA_ACTIONS_SECRETS_MISSING (no synthetic fallback)",
        "auth_or_entitlement_error": "SOURCE_INVALID",
        "rate_limited_after_retries": "SOURCE_INVALID",
        "feed_not_sip": "SOURCE_INVALID",
        "provider_bar_after_latest_completed_session": "SOURCE_INVALID",
        "invalid_raw_row_fraction_max": 0.001,
        "request_budget_exhausted": "INSUFFICIENT_HISTORY",
        "benchmark_min_sessions": 504,
        "min_usable_candidate_stocks": 60,
    },
    "symbol_exclusion": {
        "min_sessions": 260,
        "max_missing_session_fraction": 0.02,
        "max_stale_sessions": 3,
        "large_move_flag_abs_return": 0.25,  # flagged for review (possible unadjusted split), not excluded
    },
    "split": {
        "warmup_sessions": 252,
        "holdout_fraction": 0.2,  # last 20% of decision sessions: evaluated once per pre-registered configuration
        "embargo_sessions": 5,  # NYSE sessions; plus purging of every label window that reaches the test block
        "walk_forward_folds": 4,  # expanding, inside development only
        "calibration_fraction": 0.25,  # last 25% of each training window (isotonic)
        "threshold_selection": "development out-of-sample predictions only",
        "label_complete_sessions": 22,  # entry window (2) + longest horizon (20): later decisions have no full label
    },
    "robustness_splits": {
        "chronological": "untouched final holdout (unseen most recent sessions)",
        "sector_groups": {"A": _SECTORS[0::2], "B": _SECTORS[1::2]},
        "sector_method": "rule expectancy per group on the holdout; model trained on one group's development "
        "events, scored on the other group's holdout events",
    },
    "execution": {
        "decision": "close of session t (+15 min); features use bars <= t only",
        "entry": "limit over [entry_min, entry_max] valid sessions t+1..t+2; open in zone -> open; open above zone "
        "and low <= entry_max -> entry_max; open below zone -> no fill that session; never the decision close",
        "fill_session": "only the stop can trigger on the fill session",
        "gap_through_stop": "exit at the (worse) open",
        "gap_above_target": "exit at the open",
        "same_bar_target_and_stop": "STOP first (worst-first), counted; UNRESOLVED sensitivity excludes those trades",
        "time_exit": "close of fill + H sessions",
        "risk_cap": "stop distance clamped to [1, 3] ATR; minimum net reward/risk 1.5 (signals.levels)",
        "dedup": "one pending/open position per (strategy, symbol): a signal is skipped until the earlier one exits",
    },
    "horizons": [5, 10, 15, 20],
    "primary_horizon": 20,
    "costs_bps_per_side": [0, 10, 25, 50],
    "primary_cost_bps": 25,
    "returns": {
        "net": "(1 + gross) * (1 - c) / (1 + c) - 1, c = bps per side",
        "r_multiple": "(exit * (1 - c) - entry * (1 + c)) / (entry * (1 + c) - stop)",
    },
    "rule_definition": "every qualified, regime-allowed, de-duplicated signal of the registered plugin "
    "(live WATCH/ACTIVE logic without earnings veto and catalysts, which CI cannot observe point-in-time)",
    "model_definition": "existing competing-risk model + promotion-policy-2 gates (research.evaluate) on the same "
    "frozen holdout, trained on all filled events as the production pipeline does",
    "bootstrap": {"block": "decision week", "n_boot": 2000, "seed": 11},
    "matched_random": {"draws": 50, "seed": 20261021, "levels": "generic stop hint close - 1.5 ATR"},
    "portfolio": {"slots": 10, "sizing": "equity / slots at entry; skipped when all slots are used",
                  "horizon": 20, "cost_bps": 25, "marking": "daily close"},
    "ablations": {
        "h1_vcp_breakout": ["all_regimes", "quality_top_half", "model_without_quality_features"],
        "h2_rs_pullback": ["all_regimes", "quality_top_half", "model_without_quality_features"],
        "h4_oversold_recovery": ["all_regimes", "quality_top_half", "model_without_quality_features"],
    },
    "dispositions": {
        "NOT_EVALUABLE_MISSING_INPUT": "plugin requires point-in-time catalysts (none in CI); no events manufactured",
        "INSUFFICIENT_HISTORY": "holdout completed rule trades < 30 or development completed rule trades < 100",
        "VALIDATED_REAL_OOS": "holdout n >= 100, >= 26 decision weeks, >= 2 regimes; mean net @25bps > 0 with "
        "bootstrap p10 > 0; mean net @50bps > 0; Holm-adjusted one-sided p < 0.10 across evaluable hypotheses; "
        "mean above the matched-random 95th percentile; mean > 0 in both sector groups; forecast not UNVALIDATED",
        "FAILED_REAL_OOS": "holdout n >= 100 and (bootstrap p90 of mean net @25bps < 0 or mean <= matched-random median)",
        "PROMISING_NEEDS_FORWARD_TEST": "otherwise: mean net @25bps > 0 and above the matched-random median, and "
        "(bootstrap p10 > 0 or holdout n < 100)",
        "UNVALIDATED": "everything else (inconclusive)",
        "aggregate": "NO_TRADE unless at least one VALIDATED_REAL_OOS; live alerts stay disabled either way",
    },
    "thresholds": {
        "min_holdout_trades": 30,
        "min_development_trades": 100,
        "adequate_holdout_trades": 100,
        "min_holdout_weeks": 26,
        "min_regimes": 2,
        "holm_alpha": 0.10,
        "random_quantile_validated": 0.95,
    },
}


def protocol_sha256(protocol: dict | None = None) -> str:
    body = json.dumps(protocol or PROTOCOL, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode()).hexdigest()


def sanitize_experiment_id(raw: str) -> str:
    """Lower-case slug used as a directory name; anything else is rejected (no path tricks, no shell metacharacters)."""
    eid = (raw or "").strip().lower()
    if not EXPERIMENT_ID_RE.match(eid) or "--" in eid:
        raise ValueError("experiment_id must be 4-64 chars of a-z, 0-9 and single hyphens, e.g. 2026-10-fixed78-sip-8y")
    return eid


def check_years(years: int) -> int:
    lo, hi = YEARS_BOUNDS
    if not lo <= int(years) <= hi:
        raise ValueError(f"years must be within {lo}..{hi}")
    return int(years)


@dataclass(frozen=True)
class Boundaries:
    data_start: str
    data_end: str
    decision_start: str
    development_end: str
    embargo_limit: str  # training label windows must END before this session (purge + 5-session embargo)
    holdout_start: str
    holdout_end: str  # last decision session with a complete label window
    decision_sessions: int
    development_sessions: int
    holdout_sessions: int
    label_unavailable_sessions: int

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def holdout_ts(self) -> pd.Timestamp:
        return pd.Timestamp(self.holdout_start)


def compute_boundaries(sessions: list[date] | pd.DatetimeIndex, time_start: date | None = None) -> Boundaries:
    """Chronological development / embargo / untouched holdout from the ACTUAL benchmark sessions (not guessed)."""
    days = [pd.Timestamp(d).normalize() for d in sessions]
    sp = PROTOCOL["split"]
    if len(days) < sp["warmup_sessions"] + sp["label_complete_sessions"] + 100:
        raise ValueError(f"only {len(days)} sessions: too short for warm-up, development and holdout")
    first = sp["warmup_sessions"]
    if time_start is not None:
        first = max(first, next((i for i, d in enumerate(days) if d >= pd.Timestamp(time_start)), len(days)))
    last = len(days) - 1 - sp["label_complete_sessions"]
    decision = days[first : last + 1]
    if len(decision) < 100:
        raise ValueError("decision window shorter than 100 sessions")
    h = int(len(decision) * (1 - sp["holdout_fraction"]))
    holdout_start = decision[h]
    return Boundaries(
        data_start=str(days[0].date()),
        data_end=str(days[-1].date()),
        decision_start=str(decision[0].date()),
        development_end=str(decision[h - 1].date()),
        embargo_limit=str(embargo_limit(holdout_start, sp["embargo_sessions"]).date()),
        holdout_start=str(holdout_start.date()),
        holdout_end=str(decision[-1].date()),
        decision_sessions=len(decision),
        development_sessions=h,
        holdout_sessions=len(decision) - h,
        label_unavailable_sessions=len(days) - 1 - last,
    )
