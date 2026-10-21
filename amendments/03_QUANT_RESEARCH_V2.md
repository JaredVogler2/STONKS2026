# Amendment 03 — Quantitative Research and Predictive Edge V2

**Goal:** Discover whether APEX has a robust, executable positive expectancy after risk/friction, NOT manufacture profitable synthetic backtests. Any claimed “high confidence” must be reproducible from point-in-time historical market data and prospective paper trading.

## 1. Baseline hypothesis and why current result matters

The PR reports all ordinary H1–H6 and H8 UNVALIDATED on a designed synthetic market. This is evidence of a functioning rejection gate, not evidence that all such strategies fail in real equities. The planted-alpha experiment only proves some distinguishable synthetic signal can be learned. Do not reweight or relax the model until empirical real history supports it.

## 2. Data acquisition research plan (license/legal gated)

Build a source matrix by instrument, venue, timestamp, history length, adjustments, corporate actions, news/filings publication history, deleted symbols, ticker changes, availability at decision and redistribution terms. Use free Alpaca Basic within feed/rights and rate caps, official SEC documents, allowed public economic series, and user-supplied validated datasets only if authorized. Do not scrape Robinhood, fabricate delisted history, or assume IEX-only prints represent SIP.

Historical universe: choose a stable, defensibly documented period and multiple sectors/regimes; record current-universe survivorship bias explicitly. Implement `point_in_time_universe(symbol,date)` when licensed sources permit. If historical delisted names cannot be obtained free, add worst-case delisting/censoring sensitivity, sector/size matched checks and a big red “SURVIVORSHIP LIMITED” warning. Do not pretend corrections solve inaccessible data.

Archive raw source provenance and point-in-time snapshots efficiently: partitioned local Parquet at Pi or permissible object store; small canonical hashes/metrics in Postgres. GitHub Actions runners must reconstruct immutable validated datasets from approved source/registry—not need laptop or private Pi SSH.

## 3. Research protocol (freeze BEFORE next holdout)

Pre-register test design (strategy IDs, parameter ranges, number of experiments, universe, period, chronological split, validation windows, transaction costs, comparator, outcome label). Amendments based on current observed holdout outcomes must NOT reuse that holdout as pristine. Establish a new embargoed OOS block or prospective forward holdout, and freeze prior decisions.

Outcomes: target-before-stop within H = 5, 10, 15, 20 NYSE sessions; stop-before-target; no-fill; time-expire; censored; gap stop; target/stop touched same bar. Track actual economic net expectancy of trades that filled, plus signal-level no-fill rate and confidence in both. Use realistic next permissible entry; conservative same-bar worst-first or finer bars; no “perfect low” fill. Volume/trading venue, spreads, partial market sessions and slippage are explicit.

Group dependence: overlapping positions and same-day sector moves reduce effective independent sample size. Use time/sector block bootstrap or hierarchical clustering and separate per-regime analyses. Report confidence bands' coverage, not just narrower numbers. For market-wide crisis dates, bootstrap contiguous time blocks. No precise confidence bands based solely on iid `sqrt(p(1-p)/n)` per conditional prediction.

Use purged, embargoed rolling chronological train→validation→test with exact exchange session windows. Isotonic calibration needs sufficient support; consider simpler Platt/regularized multinomial and compare. Three outcomes target/stop/expire must remain a coherent probability partition, and right-censoring must be handled not silently mapped to losing cases.

## 4. Strategy-by-strategy falsifiable experiments

### H1 — Volatility contraction breakout
Test whether base compression *adds* OOS prediction after controlling for momentum, sector strength, volume, market trend and overnight gap. Compare with simple 20-day breakout and quality-matched breakout without squeeze. If edge vanishes at 25–50 bps/side, mark fragile.

### H2 — Relative strength pullback
Evaluate sector-neutral relative performance vs absolute momentum, pullback depth/length vs execution feasibility and response to high-volatility regime. Avoid learning only sector momentum exposure.

### H3 — Post-event drift
Use actual first-publication time and earliest executable session; news cluster novelty, earnings guidance vs surprise, value already consumed by gap and delayed disclosure. Compare matched abnormal volume/gap without catalyst. Avoid assuming a news item instantly available across providers.

### H4 — Oversold recovery
Separate pullbacks in genuine ongoing uptrends from falling knives; test sector/market downside shock, next-day gap execution, terminal loss. Stress stops vs 20-session thesis.

### H5 — Sector rotation
Test breadth/sector-change factor against sector momentum + stock momentum baseline; stratify leadership duration and avoid relying on look-ahead annual sector rankings.

### H6 — Gap-and-hold
Test next-session executable price, gap exhaustion, volume source parity, earnings-driven gaps, realized reversal and overnight stop gaps.

### H7 — Catalyst + compression concurrence
H7 EXISTS in strategy library; do NOT “build H7 from scratch.” Diagnose why absent from PR's synthetic summary: no event fixtures, no trigger, gate filters, no filled trades, or sample threshold. Implement per-stage count diagnostics and a time-aware deterministic H7 replay fixture. Then test factorial ablation: H1-only, catalyst-only, H1×catalyst intersection, matched random controls. Require incremental OOS benefit to claim synergy, plus multiple-testing correction.

### H8 — Seasonality (default disabled)
Potential calendar anomalies are mined heavily. Test turn-of-month vs matched market day, rolling regimes, macro release confounds, spreads and current persistence. Keep prior weak/disabled absent convincing OOS evidence.

## 5. Additional exploratory families — do not automatically ship

- Conditional target/time competition via cause-specific hazard model rather than static 20-day classifier.
- Relative downside resilience after correlated sector shock, momentum acceleration not exhausted, intraday trend efficiency from IEX-only feed with consistent source.
- Catalyst-event surprise magnitude calibrated to expected news rather than sentiment polarity.
- Sector graph spillovers and supplier/customer links using lawful public data.
- Adaptive thresholds by volatility/breadth regime **only** if holdout improvement survives extra model complexity.
- Loss-aversion and trailing-stop regimes are execution policy experiments, not predictive alpha.
- Research on capital efficiency: expected return per unit risk/exposure-days, not raw hit rate; avoid optimizing toward high hit rate with poor payoffs.

For each new idea create `research/experiments/<id>.md` including reason, null hypothesis, causal mechanism, expected predictor availability, falsification rule, costs, sample size, baselines, search-budget limit and promotion gate. No unlimited parameter sweep.

## 6. Score versus probability and value

- Quality 0–100 = internal rank and explanation, never 0–100% confidence.
- Calibrated probability = explicitly defined event and horizon, holdout model version, effective sample and status.
- Uncertainty range must cover a defensibly tested estimate, or be omitted.
- Expected dollar/R outcome after costs, gap and no-fill; bounds for downside and utility relative to benchmark.
- Publishing high-confidence requires meaningful evidence of positive **economic expectancy**, not merely accurate labels or Brier skill.
- Signal frequency matters; measure enough independent opportunities for reliable estimate. A model with positive OOS average on six trades is not robust.

## 7. Forward paper shadow program

Preserve all candidate signals including rejected and no-fill ones with timestamps. Run 60–90 trading sessions (or a sufficiently sized preregistered period) in PAPER_LIVE_DATA when permitted; do not promise 60–90 days will produce sufficient events. Lock strategy versions; log every decision and what quote was available, missed fills and actual latency.

Report weekly: total qualified events, fill/no-fill, net return after modeled friction, forecast calibration, Brier vs base, win/loss, max adverse excursion, expected shortfall, sector/date concentration, drift and model expiration. Do not delete unfavorable history.

## 8. Research implementation & acceptance

Implement versioned `research/experiments`, durable event dataset IDs, reproducibility hashes, abstention/no-trade metrics, structured calibration reports, explicit source and survivorship limitations. Add leakage trap fixtures, label permutation controls, correlated-cluster uncertainty test, exchange holiday purge, real feed/source consistent validation and regime holdout. GitHub Actions automated training must generate signed non-executable model only if gates pass; Pi verifies before activation. Cost budget prevents search runaway.

Acceptance: independent reader can reproduce every claimed experiment from frozen versions, and app visibly reports UNVALIDATED/NO TRADE when evidence does not meet standard. Aim for real evidence, not alerts per day.