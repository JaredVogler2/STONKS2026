# Recommendations — `qv2-20261022-s1-s5-u1-presample`

**Aggregate: NO_TRADE.** No live alerts, promotion or orders under any outcome.

* **S1 — Leadership continuation after a controlled pullback**: RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD → KEEP AS CANDIDATE — forward shadow; no alerts
* **S2 — Market/sector residual momentum**: FAILED_WITH_POWER → RETIRE — powered evidence against a worthwhile edge
* **S3 — Volatility-contraction breakout: immediate vs retest**: RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD → KEEP AS CANDIDATE — forward shadow; no alerts
* **S4 — First-publication earnings/guidance continuation**: NOT_EVALUABLE_MISSING_INPUT → BLOCKED — needs a verified point-in-time event source
* **S5 — Sector-ETF trend allocation with cash abstention**: FAILED_WITH_POWER → RETIRE — powered evidence against a worthwhile edge

Legacy comparators (a second Holm family: across both families the chance of at least one false positive can approach 2α, so a legacy status is never confirmatory and never promotes anything):

* h1_vcp_breakout: INCONCLUSIVE → EXTEND DATA — do not use; more independent clusters (forward shadow) are needed
* h2_rs_pullback: INCONCLUSIVE → EXTEND DATA — do not use; more independent clusters (forward shadow) are needed
* h4_oversold_recovery: FAILED_WITH_POWER → RETIRE — powered evidence against a worthwhile edge
* h5_sector_rotation: INCONCLUSIVE → EXTEND DATA — do not use; more independent clusters (forward shadow) are needed
* h6_gap_and_hold: RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD → LEGACY COMPARATOR ONLY — not a registered S hypothesis; at most a candidate for its own future registration
* h8_turn_of_month: FAILED_WITH_POWER → RETIRE — powered evidence against a worthwhile edge
* h3_post_event_drift: NOT_EVALUABLE_MISSING_INPUT → BLOCKED — needs a verified point-in-time event source
* h7_catalyst_compression: NOT_EVALUABLE_MISSING_INPUT → BLOCKED — needs a verified point-in-time event source

Next experiments (each needs its own registration):

* Forward shadow from 2026-10-23 under this protocol; earliest evaluation 2027-04-30.
* A licensed point-in-time membership source (U2) would remove the survivorship limitation.
* A verified first-publication earnings source would make S4 evaluable.


## Limitations

* SURVIVORSHIP: U1 = current S&P 500 constituents (snapshot 2026-10-22); members that left the index are absent. Eligibility starts at each member's date_added, which removes later additions from earlier cross-sections but not the survivor effect; paired same-date controls from the same member set partly cancel it
* SECTOR LABELS: current GICS sectors applied to all history (the 2018 communication services reclassification)
* EARNINGS UNKNOWN: no point-in-time earnings calendar; S4 is NOT_EVALUABLE and earnings vetoes are not modelled
* PRICE-ONLY RETURNS: split-adjusted bars, dividends not modelled (affects absolute, much less paired, returns)
* DAILY BARS: intraday order is unknown; ambiguous outcomes carry pessimistic and optimistic legs (exec-v2.1); a missing session while held is an ambiguity too
* PRESAMPLE IS EARLIER, NOT LATER: the evaluation window precedes the design data (backward-time historical robustness, not forward out-of-sample); forward evidence is still required
* PRESAMPLE EXPOSURE: the cancelled A0 run 39829472789 computed and published PRESAMPLE outcomes (job log, job summary, artifact). The analyst never read them and no design choice responded to them, but the window is not untouched; read every PRESAMPLE result with that in mind (PROTOCOL_V2.md section 12)
* SURVIVORSHIP-LIMITED UNIVERSE: U1 is not an unbiased all-market sample; results do not generalise to delisted or smaller stocks
* INFERENCE SIZE: on production-shaped synthetic nulls the decision rule (with the A1 Student-t floor) rejects 5.8-6.6% at nominal 5% and 1.6-1.9% at the Holm first step (nominal 1.25%); dependence beyond the block length (slowly varying factor premia) is not fully covered, so a Holm p between 0.03 and 0.05 is borderline evidence
* LARGE MINIMUM DETECTABLE EFFECT: on production-shaped synthetic panels the PRESAMPLE window detects only about +1.5-2 pp per signal at 80% power (5-7x the +0.30 pp economic minimum); a true edge at the economic minimum will most likely read INCONCLUSIVE, and any VALIDATED effect size is likely inflated (winner's curse)
* SPLIT ADJUSTMENT: 2 symbol(s) show a >25% jump on a split ex-date; those trade windows are quarantined
* CORPORATE ACTIONS BY CURRENT TICKER: a spin-off by a parent under a former ticker may be missing from the quarantine; dividends and other distributions are not modelled

