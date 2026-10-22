# Recommendations — `2026-10-21-fixed78-sip-8y-r2`

**Aggregate: NO_TRADE.** DISABLED (research only; no promotion, no group signals, no orders).

## Per hypothesis

| Hypothesis | Disposition | Recommendation | Evidence |
|---|---|---|---|
| h1_vcp_breakout | INSUFFICIENT_HISTORY | NOT EVALUABLE YET — too few trades in this universe/window | holdout n=16, mean net -0.144%, p10 -2.103%, Holm p=None; completed rule trades: holdout 16 (minimum 30), development 54 (minimum 100) |
| h2_rs_pullback | INSUFFICIENT_HISTORY | NOT EVALUABLE YET — too few trades in this universe/window | holdout n=39, mean net -0.037%, p10 -1.491%, Holm p=1.0; completed rule trades: holdout 39 (minimum 30), development 98 (minimum 100) |
| h3_post_event_drift | NOT_EVALUABLE_MISSING_INPUT | NOT EVALUABLE — needs point-in-time catalyst history | requires catalysts available_at <= decision time; CI has no point-in-time catalyst history; not failed alpha: no event can exist without that input |
| h4_oversold_recovery | INSUFFICIENT_HISTORY | NOT EVALUABLE YET — too few trades in this universe/window | holdout n=7, mean net -1.095%, p10 -3.590%, Holm p=None; completed rule trades: holdout 7 (minimum 30), development 34 (minimum 100) |
| h5_sector_rotation | UNVALIDATED | MODIFY / RE-TEST — inconclusive; do not use | holdout n=225, mean net 0.034%, p10 -0.555%, Holm p=1.0; inconclusive; failed checks: p10_pos_25, mean_pos_50, holm, beats_random_p95, both_sectors_pos, forecast_ok |
| h6_gap_and_hold | INSUFFICIENT_HISTORY | NOT EVALUABLE YET — too few trades in this universe/window | holdout n=11, mean net -2.092%, p10 -4.139%, Holm p=None; completed rule trades: holdout 11 (minimum 30), development 37 (minimum 100) |
| h7_catalyst_compression | NOT_EVALUABLE_MISSING_INPUT | NOT EVALUABLE — needs point-in-time catalyst history | requires catalysts available_at <= decision time; CI has no point-in-time catalyst history; not failed alpha: no event can exist without that input |
| h8_turn_of_month | FAILED_REAL_OOS | MODIFY OR RETIRE — redesign entry/exit before re-testing under a new protocol version | holdout n=140, mean net 0.129%, p10 -0.650%, Holm p=1.0; mean net @25bps not above the matched-random median; failed checks: weeks, regimes, p10_pos_25, mean_pos_50, holm, beats_random_p95, forecast_ok |

## Prioritised next experiments (ranked by evidence; each is a new pre-registered protocol version)

1. **Data integrity first.** No symbol was excluded. Review flagged >25% daily moves for ORCL, COP, SLB, EOG, HON, EXC, FCX, SPG, NFLX. Add a point-in-time earnings calendar before trusting earnings-sensitive results.
2. **Entry/stop/target mechanics.** Same-bar ambiguity is small. Stops hit first in most trades for h4_oversold_recovery, h5_sector_rotation, h6_gap_and_hold, h8_turn_of_month: test wider ATR stops / later entries as a pre-registered variant. Compare H=5..20 results only as sensitivity; the disposition uses H=20.
3. **Ranking calibration.** Quality does not significantly rank realized net R for h1_vcp_breakout, h2_rs_pullback, h4_oversold_recovery, h5_sector_rotation: do not present Quality as a confidence proxy; re-fit the score on development data only. Probability forecasts stay hidden unless the frozen calibration gates pass.
4. **Catalysts and regimes (later).** H3/H7 need point-in-time catalyst history (available_at <= decision); do not reconstruct catalysts after the fact or use delayed congressional disclosures as if known at execution. Regime-conditional variants only after the base rules are economically positive.

No untested H1/H2/H3 weighting changes are recommended from this run. Do not lower any gate to graduate a model; keep live/group high-confidence alerts disabled.

