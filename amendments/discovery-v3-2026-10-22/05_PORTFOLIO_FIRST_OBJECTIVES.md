# Portfolio-First Optimization: Net Utility, Opportunity Cost, and Constrained Capital

## Why this workstream comes before complex prediction

APEX can identify stocks with positive average return relative to a bad matched-trading wrapper but still lose to investable alternatives. In the first Quant V2 presample S1 book returned +6.2% with ~47% deployed, exposure-matched SPY/cash +12.7%; S3 held ~6% on average. This is not evidence that an intelligent account would earn positive alpha, let alone maximize growth.

**Unit of evaluation:** the complete portfolio from a frozen starting capital endowment, with cash, open trades, competing signals, expenses, risk, drawdown, no-trade decisions and liquid investable alternatives priced at times available to the decision engine. A raw signal's hit rate is secondary.

## 1. Define precise, owner-understandable objective

There is no mathematically universal "best possible" strategy without an owner's risk and account constraints. Implement a **Pareto frontier** with candidate objectives:
- expected after-cost annualized growth and *distribution* of terminal wealth, with sampling uncertainty;
- maximum/duration of drawdown, volatility, expected shortfall 5%, crash-gap vulnerability and probability of ruin/margin event (leverage=0 default);
- return **relative to contemporaneously investable SPY/cash or ETF/cash under comparable exposure/risk**, not just random S&P stocks;
- net dollars per portfolio-day, return per average capital deployed/day, opportunity cost of uninvested capital;
- trading frequency, turnover, tax-sensitive holding windows where explicitly modeled, owner time/implementation complexity and data API cost;
- repeatability across periods/regimes/cross-sections and scientific reliability (independent sample, confidence, source bias).

A basic decision utility, for research, might be `U = E(log(W_T/W_0)) − λ_ES*ES_5 − λ_DD*MaxDD − λ_TO*Turnover − λ_COMPLEX*OperationalRisk` subject to hard exposure/capital constraints, but log utility and coefficients are **illustrations only**. Pre-register a principal objective and 2–3 owner-visible alternatives; do not tune λ values on final outcome. Report raw interpretable metrics alongside composite; no magic zero-to-100 success score. If risk tolerance is unknown, use scenarios (conservative/balanced/growth) not an assumed personalized financial plan.

## 2. Opportunity set and benchmark invariants

Every session:
- **CASH:** money market/short Treasury proxy with date-known yield and rate assumptions if licensed; if not, explicit 0-yield sensitivity.
- **SPY or comparable broad ETF:** unlevered price or true total-return with dividends consistently accounted for and realistic buy/sell costs.
- **Sector ETFs** if an allowed instrument choice; don't compare a nondividend price-only strategy to a dividend total-return SPY.
- **Current positions:** already paid entry cost, exit incurs cost; can hold, adjust risk, fully/partially exit when policy allows.
- **New candidates:** only market-observable timely proposals from registered strategy. Competing portfolio policies may select none.
- **HEDGED LEG:** *paper accounting* of market beta-neutral return, include hedge rebalancing, dividends, borrow/financing costs and short sale constraints when considering a physically executable trade; if unknown or prohibited, label hypothetical benchmark only. A hedged spread is NOT automatically an executable Robinhood account strategy.

Fix alternative timing: A decision after session t close can only hold/buy at t+1 executable prices, with 15m feed delay/market calendar appropriate. Benchmark exposure is previous session planned exposure; no hindsight matching to realized strategy's same-day exposure at cost-free close. Show both matched exposure and equal volatility/risk benchmarks, plus always invested and always cash.

## 3. Dynamic capital allocation as operations research

Portfolio optimizer inputs: frozen `CandidateEvent` with expected net/return distribution and honest uncertainty; current cash and positions; market/sector/correlation exposures, mandatory reserved-risk buffers, current liquidity, slots, executable target allocation and total cost. Output: proposed research-only allocation or NO TRADE with reason.

Candidate mathematical formulations:
- mixed-integer 0/1 selection + allocation; bounded NP-hard knapsack-style under sector, duplicate, liquidity, max exposure and turnover constraints;
- mean-variance/robust covariance or CVaR constrained optimization (with covariance estimated **only from prior data** and shrinkage);
- scenario-based stochastic programming with market/sector shocks and model-uncertainty stress;
- rolling model predictive control / approximate dynamic program for replace-versus-hold when capital bound;
- a simple greedy marginal-value baseline (net expected incremental utility minus cost) that any complex optimizer must beat under identical information/time and computation budget.

Do NOT declare complex MIP/ML superior by theory. Record solver status, objective gap, runtime, infeasibility, tie-break, fallbacks, and compare to equal allocation, simple rank, S1 book, SPY/cash and no-trade. If choice model unable to estimate expected returns robustly, prefer deterministic filters and a robust risk budget.

Constraints:
- cash≥0 after fees; no negative shares, leverage or shorting unless separately approved and modeled;
- per instrument max bound, sector ≤30% and max simultaneous slots=10 as existing baseline; test alternative caps on DEV in preregistered pilot, not hindsight;
- aggregate risk-at-stop and gap-tail loss ceiling, earnings event uncertainty, min dollar volume/spread, concentration/same-correlated-exposure;
- no fictitious reinvestment of unsettled cash if account settlement policy disallows; document T+1 stock settlement if using settled-cash-only modeling.
- price and lot sizing use actual planned execution, not signal midpoint; no fills guaranteed by bar touch.

## 4. Return decomposition and allocation attribution

On historical DEV and future shadow, decompose each period's net active portfolio return into:
- **selection contribution:** fixed allocation and execution, swap chosen security for matched eligible control;
- **timing contribution:** fixed security, compare entry/exit policy to reference with same capital;
- **allocation contribution:** same available candidates and forecasts, optimize vs equal-weight/static rank;
- **market/sector factor contribution:** beta, sector and volatility exposure;
- **cash drag / cash protection:** benchmark returns forgone while flat vs losses avoided during drawdowns;
- **turnover/friction drag:** realized all-in costs, gap/queue uncertainties;
- **interaction term:** when components are nonlinear, report an explicitly allocated interaction or Shapley-style approximation, never claim exact additive identification otherwise.

If S1 selection is good but net book loses because capital is idle or stops harvest losses, test a different policy on DEV while retaining frozen S1 as a control. Do not call historical re-optimization over protocol-2 inspected dates "validated".

## 5. Capacity, risk, and repeatability tests

- 500 concurrent candidates at one open: never exceed available capital, position cap or sector risk.
- Stock A and B with 0.95 correlation cause correlated risk recognition, not independent 10% portfolio bets.
- A crash overnight breaches stop by 7%; simulated loss can exceed -1R and model does not retroactively exit at exact stop.
- Cash/SPY alternative handles partial investment, fees and dividends correctly, not an unrealized fantasy.
- Cap decision horizon and quote latency; skip if data are stale or event publication time unavailable.
- Optimize on DEV with benign fixed expected returns; evaluate on unseen simulation where signal model errors matter, show high-variance optimizer sensitivity and turnover explosion.
- Test missing covariance, missing symbols, new listing, altered sector and halted symbol fails safely.
- Any new optimizer with larger nominal CAGR but enormous drawdown or dependence on one event must be rejected by pre-registered constraints.

## 6. Key deliverables

`docs/discovery-v3/PORTFOLIO_OBJECTIVE.md`: exact primary estimand, reference alternatives, risk constraints and owner trade-off scenarios; tests for book conservation, return decomposition, benchmark fairness; quantitative chart of the **candidate policy frontier** from DEV (clearly exploratory); one real data bounded research run with full source/experiment SHA and NO_TRADE permitted.

**Success means independently comparable economic alternatives with correct capital accounting, NOT forced profit.**