# Phase P1 — Five Falsifiable Replacement Strategies (S1–S5)

**Goal:** Replace weak/unsupported H1/H2/H5/H6/H8 assumptions with narrowly specified, economically falsifiable ideas. Nothing below is a proven alpha method. These are executable research specifications with clear negative controls, feature provenance, stop conditions, minimum evidence and resource budgets. Existing H1–H8 are preserved as versioned research comparators.

## Shared execution and registration contract

Each `StrategySpec` must declare:
`strategy_id, version, title, economic_mechanism, null_hypothesis, candidate_universe_at, feature_dependencies_with_asof, entry_trigger, planned_order, risk_stop_hypothesis, candidate_exit_policies, max_hold_NYSE_sessions, disconfirming_evidence, event_blackouts, allowed_regimes, disabled_by_default, provenance, release_profile`.

Implement `trigger_frame`/`evidence` with no look-ahead and use existing `build_panel`, `research/dataset.py`, `signals/levels.py` / new typed policy, one shared `research/execution.py` simulator and `research/realeq/runner.py`. Feature derivations must be identical for backtest, Pi scan and Discord explanation. If noncausal event input unavailable, strategy returns NOT_EVALUABLE (not false 0).

For all:
- First test liquid, point-in-time eligible U.S. stocks / sector ETFs; transparent current-survivor limitation if necessary.
- Signal at regular-session t close after finalized data, executable no earlier than t+1 (with order type clearly specified).
- Net cost baseline 25bps per side with 0/10/50 sensitivity and liquidity-conditioned execution; no negative-cost assumption.
- Primary 5–20 NYSE trading sessions. Candidate weights and holding horizons chosen on DEV, pre-registered before fresh test.
- Compare to raw 20-day momentum, basic 20-day breakout, matched-random with identical levels and exposure, exposure-matched SPY/cash, and closest retired H strategy.
- Test effect on portfolio realized return, max drawdown and sector concentration, not per-trade mean alone.
- Show reject/no-fill rate, calibrated event status, quality rank not probability, missing source flags, earnings unknown and full audit/version.
- Discovery/universe errors, failed data rights or insufficient clusters => NO NEW HIGH-CONVICTION.

## S1 — Leadership Continuation After Controlled Pullback (priority #1; replacement/upgrade H2)

**Rationale:** Raw 20-day momentum benchmark yielded +1.189% mean on just 30 holdout trades (uncertain). H2 RS pullback had −0.037% over 39 trades, but H=5 and H=15 secondary sensitivity was positive (development/holdout-selected, not an independent discovery). Test whether avoiding immediate momentum buying reduces poor entry selection without simply missing the strongest winners.

### Null, alternative and why falsifiable
- H0: conditioning leader rank on a shallow pullback and stabilization adds **no positive incremental net return** versus buying equally liquid sector-matched leaders under identical execution and cost.
- H1: net per-capital-day advantage survives execution, missed winner risk and matched control on new OOS.

### Initial FIXED proposal for DEV-only design (not optimized parameters)
1. Candidate source: PIT common stocks, 20-session traded median dollar-volume ≥$30M and ≥180 prior observations (confirm source).
2. Leader: 20-session and 60-session returns **both above sector-matched 75th percentile**; positive 60-session residual vs SPY and above rising 50-session SMA; avoid recent 20-session >~25% explosive extremes if pre-registered.
3. Pullback: local close declines for 2–5 sessions with peak-to-current ~0.5–1.5× ATR14, but stays above SMA50; lower turnover with clear previous higher-low structure.
4. Confirmation: close returns above previous 1–2-session high OR two-bar stabilization near prior 20-session support; the exact one default must be frozen. No condition from future days.
5. Entry: next-session limit within 0.25ATR of confirmation close or wait for first intraday trigger validated by timestamp; max 2 sessions; no buy if gap above allowed chase cap.
6. Invalidation: beneath causally identified pullback trough less configurable structure buffer, with stop distance between e.g. 1–3ATR as feasibility gate rather than post-hoc force.
7. Exit: primary pre-registered time/structure candidate from `06`, explore H=5/10/20 on DEV (do not elect based on contaminated first holdout); stronger leaders can retain partial trend via tested trailing policy.
8. Risk blocks: earnings window unavailable -> research status flagged (or no-event eligibility for earnings-sensitive variant); cross-sector signal crowding, spread, market risk-off.

### Confounds and experiments
- Strong winners may not pull back; report missed upside from no fill vs unconditional momentum.
- Factor momentum vs alpha: sector-neutral benchmark/alpha; don't claim positive trend market as selection skill.
- Rank uses only eligible securities known at t; no today-survivors retrospective ranks.
- Ablations: leader only; leader+pullback; leader+confirmation, SAME exit and identical dates where possible.
- Falsify if paired net return or exposure-adjusted utility does not beat leader-only after costs; if improvement appears only on 2025–26 already inspected, class EXPLORATORY only.

## S2 — Market/Sector Residual Momentum and Capital-Efficient Rotation (priority #2; H5 replacement)

**Mechanism:** Trend persistence after removing market and sector beta could identify genuine stock-level leadership rather than simply moving into rising sectors. H5 original rank acceleration produced ~0 net at 25bps, and strong sector-group disagreement.

**H0:** residual ranking does not produce a higher economic return than raw rank, or simply inherits leverage/sector bets.

### Versioned feature calculations
- For each eligible stock, trailing **~126 trading sessions** of daily excess returns, regress stock return on SPY return and its sector ETF return using ONLY prior sessions. Use robust, shrinkage/ridge fit if ill-conditioned or sector highly correlated with SPY. Fit and scaling as-of each t, never full sample.
- Calculate 20-session cumulative **out-of-sample-style residual score** from fitted betas estimated through day t−20 (to avoid using the same window to fit and score); subtract estimated contemporaneous factor contribution over t−19..t. Distinguish raw residual price approximation from true alpha; document.
- Complement residual rank with past 60-session persistence and sector-relative rank, measured only in eligible PIT cohort.
- Freeze candidate rule: top-quintile residual among eligible stock peers, raw 20-day momentum >0, nonnegative sector breadth and avoid outlier spread/earnings-related jump; record score contributions.
- Entry every 5th session OR on 2-session retest condition as two separately pre-registered arms; choose DEV-only. Stop at structural support/ATR and primary time exit 10 or 20 sessions based on DEV, not old holdout.
- Risk constraints: sector ≤30% of exposure, single stock ≤10%, market beta and correlation budget, positions cap and same-symbol duplicate exclusion.

### Controls
- H5 rank-acceleration rule; simple equal-weight sector ETFs; raw top-20-day momentum stocks; sector-neutral ranks WITHOUT regression; random matched sector/volatility/liquidity.
- Factor attribution of strategy book before and after model (market/sector beta, active selection return, turnover), not raw average return.
- Test instability in beta estimation (63/126/252-day lookbacks as named sensitivity, not cherry-picked winner). Exclude false signal where R2 poor or model singular.
- Falsify if incremental net active return ≤0 after matched costs or fragile across sectors/regimes with high turnover. No promotion merely due to lower correlation with SPY.

## S3 — Volatility-Contraction Breakout Followed by Acceptance/Retest (priority #3; H1 upgrade)

**Mechanism:** A breakout from a tight base may initially whipsaw; pivot acceptance/retest can distinguish continuation, although waiting may miss big winners. H1 n16 is too small to conclude failure.

**Primary design hypothesis**: confirm 20-session pivot breakout with low prior 10–20-session realized-volatility percentile, prior ≤15% base drawdown, relative volume ≥1.3 on breakout (SIP consolidated where entitled), price above rising 50-session SMA, and positive sector-adjusted RS. All computed using t data after close.
- Arm A **immediate breakout**: existing H1 execution/levels, versioned benchmark.
- Arm B **acceptance**: close above pivot for two completed sessions without breach >e.g. 0.5 ATR below pivot, then order next market day at allowed limit.
- Arm C **retest**: within 1–3 sessions after breakout, low revisits pivot±0.5ATR, and closes back above pivot before limit order next day.
- These arms are mutually exclusive or separate matched-signal experiments; fix priority rule in advance so researcher cannot choose whichever got a profitable trade.
- Stop below actual pivot/retest support with volatility buffer; target policies include pre-registered time/trailing and 2R baseline.
- Record missed runaway breakouts, no-fill/late entry and time-to-positive MFE, as well as stop rate. The economic denominator is initial qualified signals + fills, not just winners that successfully retest.
- Null: retest/acceptance arm has no incremental paired net gain relative to immediate entry after including missed moves.
- Falsify if waiting reduces attainable net expected return or only works in one bull-period; H1's favorable small-sample sensitivity not enough.

## S4 — First-Publication Earnings/Guidance Reaction Continuation (priority #4; H3/H7 research unlock)

**BLOCKED until point-in-time event source is verified.** Evidence from historic drift literature at longer horizons does not prove a 5–20-day retail edge. Event time and gaps matter heavily.

### Inputs & entity resolution
`issuer_id` linking stock on valid date; `event_type=earnings/guidance_revision`; `scheduled_released_at` vs actual `first_public_at` and `provider_observed_at`; normalized surprise/qualitative materiality only if available_at; earnings/guidance transcript timestamps and revisions. Announcement outside hours must map to first executable regular-session decision and position entry. No hindsight value including current revised earnings surprise as historical vintage.

### Mechanistic rule
- Positive surprise/guidance classification pre-registered and available at/after disclosure. If no reliable magnitude, use purely observed postannouncement reaction and no false “beat estimates” factor.
- Announcement first tradable reaction 2–12% (candidate bounds only), relative volume ≥2.0, close in upper portion of session and next 1–2 completed sessions retain ≥75% of initial gap or stay above midpoint; do not buy announcement jump.
- Enter following next permissible session within volatility limit; explicit hold 5–15 sessions and earnings-gap risk.
- Differentiate pure announcement momentum from 1–2-day price continuation that would occur without earnings: matched same-size non-event gaps, H6 gap-and-hold, same sector and earnings week.
- Falsify if incremental net drift absent after timing, gap/spread and matching; otherwise classify forward-pending until independent years.
- H7 catalyst+compression becomes a **factorial interaction** test with H1 breakout; report pure breakout, pure catalyst, conjunction and incremental interaction net utility; absent events => NOT_EVALUABLE.

### Constraints
Do not backdate SEC Form 4 insider buys, political disclosure or headlines; public first-observed time is binding. Never treat unverified news model text as a reliable causal score; store negative evidence. No copyrighted full articles in repo.

## S5 — Liquid Sector-ETF Trend Allocation / Cash Abstention (priority #5; research alternative)

**Mechanism:** if individual stock selection lacks demonstrated edge, a low-turnover, defensible ETF/cash allocation might be more economically tractable. But sector strength alone is not proof it beats equal-weight or SPY/cash.

- Eligible: 11 liquid sector ETFs with validated history and split/dividend treatment; record fund expense ratio and actual tradability.
- Every 5 or 10 NYSE sessions, rank prior ~60-session *risk-adjusted* return excluding current day's incomplete bar; require positive medium-trend (e.g. ETF > SMA50 and SPY non-risk-off). Choose at most 2–3 sector ETFs, cap concentration, or abstain in cash according to frozen rule.
- Initial holding policy 10–20 sessions; re-evaluate only at rebalance, avoid multiple same-day flips; portfolio trades consume turnover/cost correctly.
- Baselines: buy/hold SPY, matched exposure SPY/cash, equal-weight 11 sector ETFs rebalanced same frequency, H5 stock rotation matched risk/fees. No false claim if strategy wins only by having lower market beta in a crash.
- Distinct primary objective: downside-adjusted active utility at risk budget, not raw return. Test volatility drag, correlation, turnover, ETF dividend total return and opportunity cost of cash.
- Falsify if no statistically/economically meaningful improvement over SPY/cash or EW sector ETF benchmark across regimes.

## H1–H8 disposition interface

- Keep H1/H2 as immutable comparators while S1/S3 undergo new research.
- H5 current stock rotation disabled for new alerts pending S2 test. Preserve old results.
- H4 oversold bounce and H6 gap chase remain **disabled** until fundamentally new preregistered hypotheses/data and sufficient independent examples. An 11-trade H6 sample cannot establish universal failure.
- H8 remove from primary scanner (leave historical plugin and disabled flag) until a new event-date mechanism independently beats matched control, not because existing outcome was barely positive.
- H3/H7 require event data; preserve NOT_EVALUABLE rather than using 0% hit rate.

## Strategy acceptance / coding-agent output

Each strategy's PR MUST include: causal, versioned typed spec; 10 positive/negative synthetic scenario tests; missing/corrupt input behavior; no future leakage test; live-research feature parity; paired matched control; trade, year, week, sector and regime counts; all attempted parameter variants; portfolio and cost metrics; exact script/workflow command; source SHA and manifest; independent period result or `NOT_VALIDATED_YET`.

**No tuning on 2025–2026 holdout to choose preferred thresholds. No new LIVE_GROUP flag.**