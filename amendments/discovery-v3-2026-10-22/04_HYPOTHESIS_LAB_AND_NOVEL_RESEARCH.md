# V3 Hypothesis Lab — Explore the Full Design Space, Including Original Mechanisms

## Intent

These families are **candidate scientific inquiries, not recommendations, known profitable systems, or a demand to build each**. The coding agent is explicitly invited to propose BETTER mechanisms, replace/recombine these, or conclude that a particular family lacks data/edge. The goal is *best plausible repeatable risk-adjusted net portfolio performance* in a swing-oriented account; raw chart complexity does not imply alpha. Each hypothesis must be falsifiable, affordable and causally observable.

## First pilot — choose a diverse 3–5 families, not a 100-indicator grid

### F1 — Cross-sectional leadership + conditional time horizon (S1 successor)
**Hypothesis:** leadership plus orderly price digestion confers a real return-rank advantage over matched leaders, but fixed stops/20-session exits discard the edge.
- Causal features: 5/20/60-session relative sector return, peer dispersion, ATR, realized volatility, breadth, historical range/price positioning, trend persistence, liquidity.
- Proposed experiment: leader-only vs leader+pullback, same execution/benchmark, then test 5/10/20/40-session or conditional volatility exits as a bounded DEV-only factor study.
- Control: same-date same-sector matched leader under exact same entry/exit; equal-exposure SPY/cash; stock-neutral exposure comparator.
- Failure: selection advantage disappears after market beta adjustment; no full-portfolio benefit; best horizon chosen only from seen holdout.
- Historical note: S1 had paired advantage ~0.325pp but book failed SPY/cash; focus on improving economic utilization, not just extra features.

### F2 — Cross-sectional dispersion / volatility-of-opportunity
**Hypothesis:** stock-selection opportunities are strongest when dispersion of sector-relative returns is high *while systematic volatility and spread remain manageable*; this gates scarce capital rather than forcing trades daily.
- Features: rolling cross-section standard deviation of returns/breadth/correlations, cost proxies, SPY trend and recession-risk proxies available at t; avoid future end-of-day market-wide breadth unavailable intraday.
- Rule idea: forecast whether stock-selection alpha is economically possible in a market state; dynamically choose selective stock signals, SPY/cash or abstention.
- Baselines: unconditioned S1/S3, dispersion-only filter without stock selection, sector-beta matched and no-trade.
- Falsify: higher dispersion simply predicts crash volatility and cost; conditional performance evaporates under conservative spread and real out-of-sample.

### F3 — Lagged industry-information propagation graph
**Novel, optional.** **Hypothesis:** economically linked suppliers/customers/industries respond with delayed returns to shocks, creating a time-limited return asymmetry if relationships and public news are verifiably known.
- Construct point-in-time directed graph from public SEC segment/customer supplier relationships (if disclosed and sourced), SIC/NAICS sector, ETF industry peers and lagged response. Graph nodes are securities and economic exposures, edges include source URL, valid_from/available_at/revision; avoid inferring links from future stock price correlation then pretending causal economic direction.
- A *hypothetical* discovery: producer earnings surprise at T, linked downstream entities lag 1–5 sessions conditional on historical responsiveness and liquidity; buy laggard only after positive verified public evidence, price/spread and market factor adjustment.
- Null: a graph feature adds no incremental matched-risk portfolio utility over sector peers and simple momentum with same costs.
- Tests: degree-preserving randomized edges, time-shuffled event dates, sector-only graph, reverse-direction placebo, cold-start and unseen firms/sectors, data-rights and timestamp audit.
- Block if no reliable point-in-time graph information; do NOT fabricate relationships from LLM world knowledge as-of 2017.

### F4 — Attention / liquidity shock / short-horizon reversal versus continuation
**Hypothesis:** abrupt abnormal-volume/spread/return bursts differ by news timing, closing auction pressure, broad-sector co-movement and market state; continuation vs reversal may be inferable from observable pressure.
- Features if licensed: historical regular-session consolidated dollar volume, opening/closing imbalance when available, intraday realized volatility, spread if source provides, sentiment/time-known news; daily OHLC only variant explicitly distinguishes unknown microstructure.
- Candidate classes: event-driven opening gap that holds vs exhaustion reversal; high-volume final-hour continuation; post-rebalance flow mean reversion.
- Null: no benefit over liquidity/vol-matched gap-and-hold or 1/5-day momentum, after costs and queue fill uncertainty.
- Controls: matched news/no-news days and turnover; no blanket price gapping as bullish.
- Fail if unavailable historical intraday feed or edge eaten by spreads/overnight gaps; do not force paid data subscription.

### F5 — Event timing surprise rather than static sentiment (S4 alternative)
**Hypothesis:** *changes in published expectations* or guidance and differential market underreaction may generate limited drift; announcement text or late filings alone do not. Need genuine `first_public_at` and contemporaneous analyst consensus vintage (if licensed).
- Candidate: clean first earnings surprise+gap retention, disclosure timeliness, ownership/filing novelty, re-rating of near-term earnings and postannouncement liquidity constraints.
- Negative controls: equivalent non-earnings price gaps, shuffled event dates, pre-publication signal (must fail), stale delayed SEC/politician disclosures.
- Critical: never backdate political trade execution as public signal; absence of PIT earnings data → `BLOCKED_SOURCE` not zero alpha.
- If source permits only filings with timestamps, test SEC publication reaction narrowly and describe information incompleteness.

### F6 — Optimal stopping / opportunity replacement (portfolio economics)
**Novel policy hypothesis:** value of holding a stock changes when a **better** opportunity appears; a fixed 20-day exit and 2R target may be economically irrational when capital slots are scarce.
- At each completed trading session decide among HOLD, RISK_EXIT, TAKE_PROFIT, REPLACE_WITH_CANDIDATE, CASH/SPY, conditional on observable state and pre-committed costs.
- Initial constrained formulation: dynamic programming or model predictive control / approximate rollout with causal forecasts; state=(current positions, mark-to-market, remaining capital, correlated risks, model uncertainty, competing candidates, transaction costs). Objective one-day/20-day expected after-cost utility with terminal value penalty.
- No oracle optimization using realized future returns; compare to simple fixed exit and fixed rebalancing. Approximations must be trained only in DEV, penalized for search.
- Falsify: replacement churn costs dominate; model cannot reliably rank incremental post-cost value. Report how often optimizer declines trades.
- Do NOT accidentally turn research into a live discretionary recommendation engine without separate compliance gate.

### F7 — Conditional holding-horizon mixture
**Hypothesis:** different causal setups have different edge decay curves; adaptive holding horizon might improve capital efficiency over arbitrary 20 sessions.
- Construct label curves for H=1/3/5/10/15/20/40/60 trading days with overlapping-label purging, realistic feasible next-session entry, market-adjusted return and cost per potential exit.
- Predict hazard of thesis invalidation and expected marginal continuation as of each session. Use shrinkage/simple hazards first; tree/boosted models only if genuinely superior to fixed horizon.
- Null: no incremental net return per capital-day over a fixed H, after strategy family and multiple testing accounted.
- Survival label must not assume target hit sequence from daily OHLC, censor/gap handled. Longer holding times may violate user's swing preference; include 1–4-week policy comparator and report improved horizon separately for owner approval.

### F8 — Relative-value / equity-neutral pairs *as research only*
**Hypothesis:** related liquid equities exhibit transitory dislocations that mean-revert after industry/market controls.
- Candidate formation: verifiable related business/sector plus rolling **past-only** residual cointegration/spread stability; no hindsight pair selection over full history.
- Actions: paper long/short spread, but actual owner account may not support shorting or borrow; prioritize long-only “buy underperformer versus substitute” portfolio and virtual beta hedge as analysis. If short required, mark NONEXECUTABLE/OUT_OF_SCOPE without broker validation.
- Risk: crowded pair blowups, borrow, dividend, corporate action, asymmetric legs, hard-to-borrow, news regime shifts; cost model doubles; not a free market-neutral lunch.
- Baselines: sector ETF/cash; one-leg long; random same-sector pairs.

### F9 — Structural regime model that can abstain
**Hypothesis:** rather than predicting future market direction precisely, classify moments when expected trade alpha distribution is unfavorable and default to a cheaper benchmark.
- Causal features: price breadth/dispersion, realized SPY volatility, volume and correlation, sector breadth, yield/credit/macro as-of source if license.
- Evaluate simple deterministic trend/breadth mask first, hidden Markov/regime-switching only as contender with online filtering (smoothed full-history state is look-ahead).
- Economically evaluate missed bull days, crash loss, transaction cost and alternative SPY/cash; regime labels themselves not treated as truth or validation.

### F10 — Novel decision discovery by structured search
Allow Claude Code to formulate an original mechanism from failed trade explanations, **but only after** converting it to a 1–2 page typed hypothesis registration with economic rationale, falsifiable null, causal data, execution, negative controls, baseline and search budget. Examples: “sector leadership transitions only when network centrality-adjusted breadth changes,” “capital velocity from marginal opportunity hazard,” or “short-lived informational pressure propagation.” These are prompts for exploration, not established anomalies.

## Prioritization, not unlimited research

At the start score candidate proposals by:
- plausibility of an exploitable **mechanism** and alternative explanations;
- data availability and PIT integrity;
- economic capacity/expected liquidity and scalable opportunity count;
- out-of-sample statistical power and independent observations;
- realistic spread/slippage/fees and potential gain size;
- ability to prove false with a low-cost negative control;
- portfolio diversification benefit versus benchmark;
- likelihood of surviving stress without expensive special data;
- complexity/implementation hours.

Start with a low-cost, informative pilot: F1 wrapper diagnosis, F2 opportunity-state gating, F6 capital replacement and one novel F3/F4 if reliable data exist. This is a recommendation to prioritize information gain, not an order to prove them best. The agent may replace selection if stronger reasoning arises, but must document alternative and why.

## Common falsification report

For each hypothesis write `mechanism; prior evidence; null; universe/data as-of; why forecasts matter; execution and cash alternatives; sample size effective blocks; primary net incremental utility; transaction costs; factor attribution; adverse negative controls; regime/year outliers; data snooping budget; all tried variants; RETAIN/REJECT/BLOCKED; what would change conclusion`. Every claim tied to real runs, exact source commit and provider coverage.

**No automatic trading claims:** until future held-out confirmation, research-only.