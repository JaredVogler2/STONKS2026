# PHASE 06 — Historical research lab, true event-time backtests and calibration

## Objective

Determine empirically if APEX can produce an economic edge after costs, rather than merely telling a good story. Build a genuinely executable and repeatable backtesting/replay engine capable of comparing 1–4 week strategies against sensible baselines.

## Point-in-time market simulation

At decision timestamp t the engine may ONLY see records with `available_at <= t`, including filings, news, adjusted prices and future metadata. Use fully formed bar rules (no entering at a close and then assuming fills at that same close without mechanism), US exchange calendars and realistic allowed order timing. Store and report feature availability.

For each simulated entry event:
- Define order proposal and eligibility: limit order entry zone with expiry; model whether actually touched using intraday data where available, otherwise use conservative daily bar logic.
- Model entry at next possible executable print / daily open + conservative costs and adverse slippage assumptions; no impossible "perfect low" fills.
- Exit simulation includes barrier targets, stop/invalidation, trailing protection, time stop, overnight gaps, corporate actions, suspension/halt and missed quotes.
- If one OHLC daily bar touches BOTH target and stop, the order is unknown: apply conservative worst-first assumption, report ambiguity rate or require finer granularity, NEVER assume winner first.
- Distinguish signal return vs members' later self-entered fills and benchmark market-relative return.
- Support full 20-session horizon and early partial take-profit if configured.
- Profit and loss uses actual notional, roundtrip friction, spread/slippage and split-adjustment assumptions.
- Handle survivorship bias, delisting, extreme illiquid periods; write a warning when historical symbols were unavailable in data source.

## Validation protocol

1. Split by chronological regime; use rolling walk-forward with training, validation (hyperparameters), untouched out-of-sample test, possibly 2018–2020 / 2021–2022 / 2023–2024 / 2025–2026 depending actual trustworthy history.
2. For overlapping 20-session labels use **purged/embargoed** validation and group/cross-sectional leakage controls; overlaps across symbols and sectors matter. No training on outcomes whose windows extend into test fold.
3. Create fixed pre-registered strategy universe and modest hyperparameter search budgets; log every attempted model and control for false discovery (multiple tests).
4. Never optimize trading signals on a final held-out block and then re-use that block as “unseen.” When updating, roll forward and maintain new future evaluation.
5. Compare against SPY, momentum baseline, breakout baseline, matched liquidity/sector random entry, and always-flat. Benchmark only under comparable risk/time exposure.
6. Use block bootstrap clustered by date/sector and outcome-level uncertainty; count independent trades, distinct dates, sectors, regimes. Report P10/P50/P90 and confidence intervals, not just average.
7. Separately assess execution sensitivity at 0, 10, 25, 50 bps spread/slippage assumptions per side. If edge vanishes under plausible costs, fail the candidate.

## Essential outcome metrics

- number of entries and non-entry/canceled candidates; coverage and fill rate
- average/median gross and net R-multiple, net P&L, excess over benchmark
- win rate defined as **specific outcome**, not loosely any positive exit
- target-before-stop rate by horizon, stop-first probability, time-expired percentage
- max drawdown, downside deviation, expected shortfall, tail loss, volatility
- profit factor only if sample sufficient, turnover, exposure, annualized cost-adjusted return where valid
- MFE/MAE, time to target, optimal hold by conditional evidence, gain giveback
- performance by regime, liquidity, sector, data feed, catalyst family and earnings proximity
- total and rolling stability of sample; historical bias warnings
- classifier log loss, Brier score, reliability diagram and expected calibration error (ECE)
- realized risk by alert category vs forecast.

## Calibration method

Calibrate `P(target hit before stop within H trading sessions | information at t and strategy)` through an interpretable baseline first (regularized logistic, monotonic GAM or competing-risk/survival hazard for target/stop/expiry), then isotonic/Platt if enough samples, with train-calibration-test separation. Account for right censoring and overlapping horizon. Keep separate P(stop first) and P(expire), so implied probabilities form a coherent partition where modeled; a three-outcome probabilistic classifier is acceptable.

Require:
- model registered with data range, sample size, out-of-sample Brier and calibration bins
- uncertainty interval (bootstrap or credible interval) and effective sample size
- minimum history and stability gate chosen transparently in research protocol, e.g. at least 100–200 qualified historical trades across regimes for early display, with "EXPERIMENTAL" until stronger evidence
- when insufficient data, show **UNVALIDATED — no calibrated probability**, never turn Quality 92 into 92% confidence.
- confidence threshold for alerts must be validation-based; if target win rate lower bound weak or results unstable, emit NO TRADE.

## Replay engine

Implement an accelerated deterministic market clock with event queues and ability to replay historical/synthetic scenarios through **the exact same signal lifecycle and Discord notification pipeline** as production. No sleeping actual days. Replay must support member entry/exit commands at specified event times, outages, duplicate events, missed quotes and after-hours news.

Provide replay fixtures:
1. clean momentum winning trade
2. false breakout stop
3. target and stop same daily bar
4. overnight gap through stop
5. earnings surprise adverse
6. IEX last trade unrepresentative
7. signal expires without entry
8. open position near deadline
9. partial exits + adds
10. simultaneous 10 Discord members in same symbol at different cost bases
11. 429 feed failure and stale data
12. bearish market regime risk-off circuit breaker.

## Reporting / QA

Backtest dashboard has trade-list export, equity curve, calibration plot, win/lose separation, parameter version, code commit hash, source-as-of hash and replay control. Make statistical claims from actually computed outputs and reflect uncertainty. Tests compare held-out expected outputs and assert no post-timestamp leakage.

## Acceptance

Demonstrate one or more strategies MAY FAIL (and system reports NO TRADE); produce verified metrics on fixture/replay. No pretend win rate, strategy fitted on all data or fabricated "high confidence" guarantee.