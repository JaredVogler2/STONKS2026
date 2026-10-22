# Phase P1 — Frozen Research Protocol, Valid Hypothesis Testing and Power

## Mission

Our first real equity 2025–2026 holdout has already been inspected, including 229 cost/horizon/variant diagnostics. **It is no longer a pristine arbiter for designs inspired by those results.** Proper research begins with a pre-specified test of future/independent data that is hard to game accidentally.

## 1. Define every estimand before research

For each strategy S, execution policy E and universe U:
- `Signal expected utility` = (probability filled × expected net payoff given fill) minus appropriately allocated opportunity/operational costs; also track abstention.
- `Filled-trade mean net return` = equal-weight or planned risk-weighted returns of fully completed trades, with denominator disclosed.
- `Active capital efficiency` = expected net P&L / invested capital-day, annualization and turnover with calendar consistency. Never compare 5% exposure to 100% SPY without exposure adjustment.
- `Incremental alpha versus control` = matched strategy book minus exposure/risk/sector-appropriate benchmark under identical entry/exit/fees; paired by decision date and instrument eligibility.
- `Drawdown/concentration`, maximum concurrent risk, total return, annualized volatility, tail expected shortfall, stress regime.

Specify whether reward/risk in R uses anticipated stop gap or theoretical entry-stop. Trade count alone is never enough; log distinct weeks, dates, quarters, regimes, sectors and estimated effective samples.

## 2. Design the testing chronology

- `DEV`: historically dated data used to design parameters and debug after first r2 report.
- `INNER_TRAIN`: purged expanding splits inside development for fitting, not test classification.
- `CALIBRATE`: chronologically later, separate calibration from fit and selection.
- `OUTER_VALIDATION`: repeated purged temporal evaluation for candidate shortlist and parameter choice; log all attempted variants.
- `FRESH_EVALUATION`: an actually unseen future interval, independently held-out newly licensed PIT universe **and/or forward paper live**. Held-out symbols in the same 2025–2026 dates are not full temporal independence; do not label them fresh time holdout.
- Keep all test data and manifests immutable; start-of-test only sources time-known then; purge labels overlapping boundaries using XNYS session index and true label exit dates, not weekday offsets.
- Predefine number of looks/optional stopping; every interim review consumes independent validation budget unless an anytime-valid/e-value method is chosen and justified.

## 3. Valid inference for clustered, overlapping trades

- Null H0 is no positive **incremental** net advantage over a matched control; not merely returns > 0 during a SPY +36% bull regime.
- Use decision-date paired strategy-control differences; resample contiguous market-time blocks of e.g. 20/40/60 NYSE sessions as a sensitivity (longer than 20-session outcomes). For sector clustering, preserve date-wise cross-correlations.
- For hypothesis tests: use a NULL-centered studentized moving-block bootstrap of paired book return differentials, or a valid date/sector permuted placebo respecting conditional exchangeability. Prove simulation size/control. Standard percentile bootstrap means ≤0 can be a descriptive signal; DO NOT advertise it as p-value without calibration.
- Compute 90/95% intervals and one-sided interval lower bound, document dependence and effective blocks. If only 15 decision weeks or 1 market regime, report INSUFFICIENT_CLUSTERS even if n=140 trades.
- Minimum holdout cluster count informed by variance and plausible smallest worthwhile edge; test Type-I / power under autocorrelated shocks, fat tails and uneven event dates.
- Correct multiplicity across **all prespecified primary candidates** and any selection informed by 229 variants; control FWER or FDR with full definitions, not merely adjust six p-values when hundreds of branch-selected outcomes influence final choice.
- Include zero-alpha shuffled-label, shifted-future-feature leak trap, known-alpha planted fixture, negative-beta and sector-confounded controls. False positive rate target nominal and CI logged.

## 4. Bounded experiment registry

For each `experiment_id` record:
`idea_id; exact primary hypothesis; null; universe/as-of; feature/data rights; strategy version; allowed parameters; entry/exit policy; horizon; costs; benchmark; train/splits; seed; family of tests; primary effect size; minimum economically meaningful advantage; Type-I target; power estimate; run budget; allowed adaptation policy; decision rule; freeze timestamp; code_sha; dataset_hash`.

Search constraints (initial suggestion to amend only before freeze):
- Main strategy S1/S2/S3/S4/S5 one locked default each.
- Up to 2–3 explicitly distinct mechanistic contrasts per strategy if research budget permits, chosen on DEV exclusively.
- Two common exit policies as a limited factorial for priority S1/S3, not all 5×20 exits with cherry-pick.
- Cost stress {0,10,25,50}bps/side is descriptive, not a separate “winner election”.
- If new features require complex ML after weak simpler baselines, defer ML and record NO_EDGE.

## 5. Fair comparators, ranking and selection

- Compare each primary rule to: (1) same-date same-execution matched-random eligible stock, (2) raw momentum/breakout under same policy, (3) sector-neutral momentum where appropriate, (4) exposure-matched SPY/cash, (5) always flat.
- Use random samples matched on liquidity, volatility, sector, risk regime and signal count, and same dedup and slot constraints; random if no matched assets available -> DISCARD with reason.
- Compare both per-filled-trade and constrained equity book including skips, cash and capital concentration. Use daily portfolio valuation with cash, splits/distributions and marked exits; risk-adjusted comparison must not grant unlimited leverage.
- Strategy claiming calibrated probability must outperform unconditional class rate in genuinely unseen data; report Brier and proper scoring, reliability by bins/horizon and subgroups. Any confidence interval must be about an identified estimand and empirically checked.
- Evaluate risk-adjusted utility loss due to wrong NO TRADE / false positive and sensitivity to overly conservative 25bps assumption.

## 6. Clear promotion and failure decisions

Label separately:
- `MECHANIC_VALID`: simulator/data/causality tested, no predictive claim.
- `RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD`: pre-specified rule net advantage positive with uncertainty still broad or sample too small.
- `RULE_VALIDATED_RESEARCH_OOS`: significant and economically positive vs fair control with independent clusters and new evaluation evidence; STILL not live-gate by itself.
- `FORECAST_CALIBRATED_OOS`: proper probability model independent tests pass, regardless whether rule validated.
- `FAILED_WITH_POWER`: sufficiently powered economically inferior result, across meaningful regimes.
- `INCONCLUSIVE`: small sample, dependencies or mixed regimes. Avoid strong retire assertions.
- `NOT_EVALUABLE_MISSING_INPUT`, `DATA_RIGHTS_BLOCKED`, `RESEARCH_ENGINE_INVALID`.
- `LIVE_GROUP_DISABLED` is a separate compliance/permissions/ops flag never inferred from backtest performance.

**No fixed 95% confidence one-number magic.** Present point estimate + effect size + uncertainty + real evidence maturity + time range in dashboard.

## 7. Research integrity reference notes (not implied success)

- Jegadeesh & Titman (1993), `Returns to Buying Winners and Selling Losers`, https://doi.org/10.1111/j.1540-6261.1993.tb04702.x — documented historical medium-horizon momentum; the paper's 3–12-month trade horizon is NOT proof a 5–20-session APEX strategy will work.
- Novy-Marx (2012), `Is Momentum Really Momentum?`, https://doi.org/10.1016/j.jfineco.2011.05.003 — differentiates intermediate from immediate past returns, relevant to residual-momentum experiments.
- Frazzini, Israel & Moskowitz, `Trading Costs of Asset Pricing Anomalies`, https://pages.stern.nyu.edu/~afrazzin/pdf/Trading%20Cost%20of%20Asset%20Pricing%20Anomalies%20-%20Frazzini%2C%20Israel%20and%20Moskowitz.pdf — institutional trading costs context; not a free personal retail spread assumption.
- Bailey, Borwein, López de Prado & Zhu, `The Probability of Backtest Overfitting`, https://escholarship.org/uc/item/4w1110bb — warns against implicit search and backtest selection.

## 8. Acceptance

New `PROTOCOL_V2.md` and machine `PROTOCOL_VERSION = "realeq-protocol-2"` SHA-locked before new evaluation. Test no future data affects past decisions, correct independent calibration, correct Type-I under simulation, benchmark fairness, minimum clusters, experiment attempted ledger and no cherry-picked commit.

Even if all proposed strategies fail: publish complete failed variants, compare to benchmark, keep NO TRADE and record what the system learned.