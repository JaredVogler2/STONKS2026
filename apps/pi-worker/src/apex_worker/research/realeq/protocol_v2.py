"""Frozen experiment protocol `realeq-protocol-2` (research-results/real-equities/PROTOCOL_V2.md is the human copy).

Registered BEFORE any protocol-2 outcome exists. Every value a result depends on is fixed here: universe, data window,
chronology (development vs untouched evaluation), strategies S1-S5 and their arms, the DEV-only arm-selection rule,
execution and costs, matched controls, inference, multiplicity, dispositions and the run budget. Changing any value is
a new protocol version. `realeq-protocol-1` (protocol.py) is unchanged and remains the default of the runner.

Chronology rationale. The r2 experiment used data from 2018-10-22 and evaluated outcomes for decisions from 2019-10-23,
and its 2025-05-02..2026-09-21 holdout shaped the S1-S5 ideas. Decisions and labels before 2019-10-23 were never
inspected. Protocol-2 therefore:
* selects each strategy's arm on DEV (2019-10-23 onward, CONTAMINATED: development and diagnostics only);
* evaluates the selected arm ONCE on PRESAMPLE (decisions from 2017 whose label windows end before 2019-10-23),
  untouched by any earlier outcome. It is earlier in time, not later, and is labelled as such;
* registers a FORWARD shadow window (decisions after the freeze) that a later run evaluates under this same protocol.

Amendment A1 (2026-10-22, before any protocol-2 outcome was read): the pre-run adversarial review found defects in the
frozen implementation and two places where the human copy and this machine copy disagreed. A1 fixes them and is the
version every protocol-2 evaluation uses; the A0 hash stays on record. See PROTOCOL_V2.md section 12.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

import pandas as pd

from apex_worker.research.realeq.universe_v2 import U2_STATUS, snapshot_sha256

PROTOCOL_V2_VERSION = "realeq-protocol-2"
PROTOCOL_V2_A0_SHA256 = "a15b4e92013657ab2fdef79cf860a3b257fcf4517649f035113dadb914444c9f"  # as frozen before A1
PRESAMPLE_LABEL_END = "2019-10-23"  # first r2 decision session: every PRESAMPLE label window ends before it
DEV_START = "2019-10-23"
FORWARD_START = "2026-10-23"

PROTOCOL_V2: dict = {
    "version": PROTOCOL_V2_VERSION,
    "registered": "2026-10-22, before any protocol-2 outcome was computed on market data",
    "amendment": {
        "id": "A1", "date": "2026-10-22", "supersedes_sha256": PROTOCOL_V2_A0_SHA256,
        "timing": "issued before any protocol-2 outcome informed it. The analyst never saw an A0 outcome value: of the A0 "
        "run (Actions run 39829472789, cancelled after its study step had finished) only file names, sizes, hashes and a "
        "redacted structural scan (line counts, key presence) were examined",
        "a0_exposure": "the A0 run COMPUTED PRESAMPLE outcomes (selected arms of S1/S2/S3 and both S5 arms), PRINTED the "
        "S1-S3 PRESAMPLE summaries in its job log, SAVED them in its artifact and DISPLAYED the primary table in its job "
        "summary. Whether anyone viewed them cannot be determined from the GitHub API. PRESAMPLE is therefore recorded as "
        "EXPOSED (outcomes published, unread by the analyst), not as untouched",
        "cause": "pre-run adversarial review: unselected arms simulated on PRESAMPLE before being discarded (S1-S3) or "
        "published (S5); S5 benchmark mis-timed on rebalance days; Holm dropped members without a p-value; the "
        "UNCERTAINTY rule differed between the copies; "
        "FAILED_WITH_POWER ignored the optimistic leg; touch-fill sensitivity covered buy limits only; a delayed PRESAMPLE "
        "time exit could reach DEV; the S3 retest of an older breakout could be hidden by a newer one; S5 had no cost, "
        "regime or MDE gates",
        "thresholds_changed": "none (alpha, block lengths, minimum blocks, economic minimums, costs and arms unchanged)",
    },
    "mode": "RESEARCH_ONLY",
    "production_promotion_allowed": False,
    "group_actionable_allowed": False,
    "data": {
        "provider": "alpaca",
        "feed": "sip",
        "adjustment": "split",
        "timeframe": "1Day",
        "first_session": "2016-01-04",
        "finality_lag_sessions": 2,
        "corporate_action_types": ["forward_split", "reverse_split", "spin_off"],
        "max_requests": 300,
        "returns": "price-only (dividends not modelled), as protocol-1",
    },
    "universe": {
        "id": "U1",
        "snapshot": "sp500-current-2026-10-22",
        "snapshot_sha256": snapshot_sha256(),
        "classification": "SURVIVORSHIP_LIMITED_CURRENT_CONSTITUENTS",
        "membership_rule": "a stock is a member at t only if its date_added <= t; ranks and breadth use members only",
        "share_classes": "one listing per SEC CIK (first in snapshot order)",
        "references": "SPY and the 11 SPDR sector ETFs; S1-S3 and legacy rules never trade them; S5 trades the ETFs",
        "comparator": "U0 = the fixed66 stocks, reported as a descriptive subset",
        "u2_point_in_time": U2_STATUS,
        "gates": "live GateConfig defaults (price >= 10, median dollar volume >= 30M, avg volume >= 1M shares, >= 180 "
        "bars, extension <= 3.5 ATR, risk-off breaker 0.6); earnings unknown (no point-in-time calendar)",
    },
    "abort_rules": {
        "credentials_missing": "BLOCKED_ALPACA_ACTIONS_SECRETS_MISSING (no synthetic fallback)",
        "auth_or_entitlement_error": "SOURCE_INVALID",
        "feed_not_sip": "SOURCE_INVALID",
        "provider_bar_after_latest_completed_session": "SOURCE_INVALID",
        "invalid_raw_row_fraction_max": 0.001,
        "request_budget_exhausted": "INSUFFICIENT_HISTORY",
        "benchmark_must_start_by": "2016-01-08",
        "min_usable_stocks": 350,
    },
    "symbol_exclusion": {"min_sessions": 260, "max_missing_session_fraction": 0.02, "max_stale_sessions": 3,
                         "large_move_flag_abs_return": 0.25},
    "quarantine": {
        "rule": "a trade (strategy or control) whose window [decision session, exit session] contains a spin-off ex-date "
        "of its symbol is excluded; a control is replaced by the next candidate",
        "known_events": {"HON": ["2026-06-29"], "EXC": ["2022-02-02"]},
    },
    "chronology": {
        "warmup_sessions": 252,
        "label_sessions": 22,
        "presample": {"role": "EVALUATION_PRESAMPLE_EXPOSED_A0_UNREAD_BY_ANALYST (untouched by protocol-1 and by design; "
                      "A0 run 39829472789 published outcomes on 2026-10-22, see amendment.a0_exposure)",
                      "evidence_type": "PRESAMPLE_BACKWARD_TIME_HISTORICAL_ROBUSTNESS (earlier in time than the design "
                      "data; not forward out-of-sample; A0 outcomes published unread)",
                      "decisions": "from the first session with 252 prior sessions to the last whose decision + 22 "
                      f"sessions is before {PRESAMPLE_LABEL_END}",
                      "label_exclusion": f"a PRESAMPLE trade or control whose exit session (either leg) is on or after "
                      f"{DEV_START} is excluded (a delayed time exit could otherwise reach DEV)"},
        "dev": {"role": "DEV_CONTAMINATED", "decisions": f"from {DEV_START} to data end - 22 sessions",
                "use": "arm selection, variance and minimum-detectable-effect estimates, diagnostics; never a verdict"},
        "known_old_holdout": "2025-05-02..2026-09-21 (inside DEV; CONTAMINATED_FOR_NEW_DESIGN)",
        "forward_shadow": {"decisions_from": FORWARD_START, "earliest_evaluation": "2027-04-30",
                           "status": "REGISTERED_NOT_RUN"},
    },
    "execution": {
        "engine": "exec-v2.0 (research.execution_v2)",
        "primary_leg": "pessimistic",
        "entry_valid_sessions": 2,
        "horizon_sessions": 20,
        "entry_arms": {"E0": "limit at entry_max, open guard entry_min (protocol-1 zone)",
                       "E1": "limit 0.5 ATR below the decision close, open guard one tick above the stop"},
        "exit_arms": {"X0": "stop + target (2R, capped below the 120-session high) + time exit",
                      "X1": "stop + time exit", "X2": "trailing stop: highest close through d-1 minus 3 ATR(d-1)"},
        "e1_below_close_atr": 0.5,
        "x2_trail_atr": 3.0,
        "touch_fill_sensitivity_trade_through_ticks": 1,
        "touch_fill_sensitivity_scope": "the price must trade through by 1 tick for resting buy limits AND target sell "
        "limits; the re-simulated windows are quarantined like the primary ones",
        "levels": "signals.levels.compute_levels at the PRIMARY cost (25 bps). X0 arms: target capped below the prior "
        "120-session high and screened at min_net_rr 1.5. X1/X2 arms have no target: only the stop must be valid",
        "dedup": "one pending/open position per (strategy, arm, symbol) until the pessimistic exit",
    },
    "costs_bps_per_side": [0, 10, 25, 50],
    "primary_cost_bps": 25,
    "strategies": {
        "S1": {"title": "Leadership continuation after a controlled pullback", "arms": ["E0X0", "E0X1", "E1X0", "E1X1"],
               "default_arm": "E0X0", "mechanism_pool": "leaders (S1 leader condition at t, any pullback state)"},
        "S2": {"title": "Market/sector residual momentum", "arms": ["E0X1", "E0X0"], "default_arm": "E0X1",
               "rebalance_every_sessions": 5, "mechanism_pool": "raw 20-session momentum, same filters"},
        "S3": {"title": "Volatility-contraction breakout: immediate vs retest", "arms": ["A_X0", "A_X2", "C_X0", "C_X2"],
               "default_arm": "A_X0", "mechanism": "per opportunity: retest arm minus immediate arm, same exit",
               "retest_rule": "each qualified breakout b is judged on its own window b+1..b+3 (first qualifying "
               "session); when two opportunities retest on one session the signal carries the more recent breakout",
               "mechanism_scope": "DEV only (A1 isolation): the contrast needs both entry arms, so its PRESAMPLE half "
               "would evaluate an unselected arm and is withheld; descriptive, never a gate; an opportunity whose retest "
               "lies outside the breakout's segment or whose trade is quarantined or label-incomplete is excluded, not "
               "counted as a miss"},
        "S4": {"title": "First-publication earnings/guidance continuation", "status": "NOT_EVALUABLE_MISSING_INPUT",
               "reason": "no verified point-in-time first-publication earnings source; nothing is backdated"},
        "S5": {"title": "Sector-ETF trend allocation with cash abstention", "arms": ["R10", "R5"], "default_arm": "R10",
               "top_n": 3, "benchmark": "exposure-matched SPY/cash: per session (1 + prior-close exposure x SPY "
               "overnight return) x (1 + post-open exposure x SPY open-to-close return) - 1; cash yield 0; no cost",
               "mechanism": "equal-weight 11 sector ETFs",
               "presample": "only the DEV-selected arm is computed on PRESAMPLE",
               "gates": "Holm p < 0.05; mean daily active >= +1 bp; mean daily net > 0 at 25 bps; mean daily active > 0 "
               "at 50 bps; positive in both PRESAMPLE halves; >= 2 regime labels with >= 15% of invested sessions; DEV "
               "mean > 0. Exempt: touch-fill and daily-bar ambiguity (market orders at the open)",
               "risk_off_check": "regime abstention is evaluated at rebalance sessions only",
               "price_only_bias": "dividends are not modelled: a yield gap of about 1%/yr between the held ETFs and SPY "
               "moves the daily active return by about 0.4 bp x exposure (economic minimum: +1 bp/day)"},
    },
    "legacy": {"rules": ["h1_vcp_breakout", "h2_rs_pullback", "h4_oversold_recovery", "h5_sector_rotation",
                         "h6_gap_and_hold", "h8_turn_of_month"],
               "not_evaluable": ["h3_post_event_drift", "h7_catalyst_compression"],
               "arm": "E0X0", "family": "legacy comparators (own Holm family; never affect S statuses)"},
    "controls": {"k": 10, "seed": 20261022, "min_stratum": 5,
                 "hierarchy": ["sector x volatility tercile", "sector", "volatility tercile", "pool"],
                 "geometry": "signal's stop distance in ATR (live 1-3 ATR clamp), same target rule and policy",
                 "value": "per-signal net return at the primary cost; a no-fill counts 0 for strategy and control"},
    "arm_selection": {"segment": "DEV", "criterion": "largest DEV studentized paired difference (pessimistic, 25 bps, "
                      "vs the generic matched control; the smaller t of L = 20 and 40); arms whose DEV test is not OK "
                      "are not eligible; none eligible -> the default arm; ties -> earlier in the arm list",
                      "presample": "only the selected arm decides; other arms are not used for any status",
                      "isolation": "A1: (1) signals from causal features; (2) every registered S1-S5 arm evaluated on DEV "
                      "only; (3) the frozen rule picks one arm per strategy and the choice is hashed (selection lock, "
                      "logged before any PRESAMPLE outcome); (4) PRESAMPLE is simulated for the locked arms only. An "
                      "unselected arm is never simulated on PRESAMPLE, so no PRESAMPLE outcome of it exists to reach a "
                      "metric, log or artifact. S5 simulates each segment from the rebalance row preceding it. Legacy "
                      "rules, baselines and engine self-checks are single configurations and run after the lock"},
    "inference": {"method": "inference-v2.0: studentized, null-centred circular block bootstrap on the session index",
                  "block_lengths": [20, 40], "sensitivity_block_length": 60, "n_boot": 4999, "seed": 11,
                  "min_blocks": 12,
                  "decision_statistic": "conservative: largest p and smallest lower bound over L = 20 and 40 (the "
                  "Bartlett adjacent-block term covers dependence beyond the 22-session label); L = 60 is reported as "
                  "a sensitivity because a 684-session window has at most 12 such blocks",
                  "time_axis": "per segment, the sessions on which the live gates admit at least one stock (the "
                  "market-wide risk-off breaker closes all stocks; a closed market is not missing evidence); S5 uses "
                  "every session",
                  "alpha_one_sided": 0.05, "primary_family": ["S1", "S2", "S3", "S5"], "multiplicity": "Holm",
                  "multiplicity_missing_p": "a family member without a p-value enters Holm with p = 1, so the family "
                  "size stays 4 (the same rule applies to the legacy family)",
                  "calibration": "bench/qv2_p2_inference_battery.py (L = 20, 40; axes 480-1700 sessions) and "
                  "docs/quant-v2/M1_ENGINE.md (L = 20, 40, 60)"},
    "economic_min_effect": {"per_signal": 0.003, "s5_daily_active": 0.0001,
                            "note": "+0.30 pp per 20-session signal (paired); +1 bp per day for S5"},
    "dispositions": {
        "RULE_VALIDATED_RESEARCH_OOS": "PRESAMPLE, selected arm: clusters OK; Holm p < 0.05; mean >= economic minimum; "
        "absolute per-signal mean > 0 at 25 bps; paired mean > 0 at 50 bps and with touch-fill sensitivity; positive in "
        "both PRESAMPLE halves; >= 2 regime labels with >= 15% of signals each; DEV paired mean > 0; ambiguous trades "
        "<= 25% of fills. Research only: the forward shadow is still required",
        "UNCERTAINTY_TOO_HIGH": "every validation gate passes on the optimistic leg only, or ambiguous trades > 25% of fills. "
        "A1 reading (this machine copy governs): clusters OK and not validated, and either ambiguous trades > 25% of fills "
        "(whatever the other gates), or the legs differ and every validation gate passes on the optimistic leg, with its "
        "Holm p recomputed by entering the optimistic p in the family",
        "FAILED_WITH_POWER": "clusters OK, mean <= 0, and the largest one-sided 95% upper bound < the economic minimum; "
        "when the legs differ, the optimistic leg must meet the same two conditions (A1)",
        "RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD": "clusters OK, paired mean > 0 and absolute mean > 0, not validated",
        "INCONCLUSIVE": "everything else, including INSUFFICIENT_CLUSTERS",
        "NOT_EVALUABLE_MISSING_INPUT": "required point-in-time input does not exist (S4, H3, H7)",
        "RESEARCH_ENGINE_INVALID": "an engine self-check failed; no strategy status is reported",
        "aggregate": "NO_TRADE unless at least one RULE_VALIDATED_RESEARCH_OOS; even then RESEARCH_ONLY with live alerts "
        "disabled until the forward shadow and the release gates pass",
    },
    "books": {"slots": 10, "sector_cap": 0.30, "cost_bps": 25, "benchmark": "exposure-matched SPY/cash (cash yield 0)",
              "benchmark_approximation": "SPY close-to-close with the previous close's exposure; intraday entries and "
              "exits are not timed (descriptive only)", "control_book_window": "every book runs to the latest exit of "
              "the strategy and control trades of the segment"},
    "engine_self_checks": {
        "lookahead_trap": "a deliberately cheating rule (each session, the eligible member with the top realized forward "
        "20-session return; E0X1, 2 ATR stop) must give conservative p < 0.001 on PRESAMPLE; it is never a strategy",
        "placebo": "each session one random eligible member, same execution: reported; p < 0.01 raises a warning",
    },
    "research_budget": {"github_runner_minutes_max": 45, "alpaca_requests_max": 300, "grid_arms_max": 12},
    "minimum_detectable_effect": "reported per strategy: (z(1 - 0.05/4) + z(0.80)) x SE, with SE from DEV projected to the "
    "PRESAMPLE sample size and with the realised PRESAMPLE SE (largest over the decision block lengths); S5 per day",
    "reporting": "descriptive economics (win/loss distribution, exit shares, gross and net per fill, net per initial "
    "signal, books, concentration, independent counts) never change a disposition; values on fewer than 30 trades are "
    "flagged small_sample",
    "versions": {"execution": "exec-v2.0", "inference": "inference-v2.0", "features": "qv2-features-1",
                 "strategies": "0.1.1", "levels": "levels-1"},
}


def protocol_v2_sha256(protocol: dict | None = None) -> str:
    body = json.dumps(protocol or PROTOCOL_V2, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode()).hexdigest()


@dataclass(frozen=True)
class BoundariesV2:
    data_start: str
    data_end: str
    presample_start: str
    presample_end: str
    dev_start: str
    dev_end: str
    presample_range: tuple[int, int]  # inclusive session indexes on the benchmark index
    dev_range: tuple[int, int]
    sessions: int

    def to_dict(self) -> dict:
        d = asdict(self)
        d["presample_range"], d["dev_range"] = list(self.presample_range), list(self.dev_range)
        return d


def compute_boundaries_v2(sessions: pd.DatetimeIndex) -> BoundariesV2:
    days = pd.DatetimeIndex(sessions).normalize()
    ch = PROTOCOL_V2["chronology"]
    warm, label = ch["warmup_sessions"], ch["label_sessions"]
    if len(days) < warm + 2 * label + 200:
        raise ValueError(f"only {len(days)} sessions: too short for warm-up, PRESAMPLE and DEV")
    cut = int(days.searchsorted(pd.Timestamp(PRESAMPLE_LABEL_END)))  # index of the first DEV session
    ps_lo, ps_hi = warm, cut - label - 1  # decision + label (22) <= cut - 1: every label ends before DEV
    dev_lo, dev_hi = cut, len(days) - 1 - label
    if ps_hi - ps_lo < 100 or dev_hi - dev_lo < 100:
        raise ValueError("PRESAMPLE or DEV shorter than 100 decision sessions")
    return BoundariesV2(
        data_start=str(days[0].date()), data_end=str(days[-1].date()),
        presample_start=str(days[ps_lo].date()), presample_end=str(days[ps_hi].date()),
        dev_start=str(days[dev_lo].date()), dev_end=str(days[dev_hi].date()),
        presample_range=(ps_lo, ps_hi), dev_range=(dev_lo, dev_hi), sessions=len(days),
    )
