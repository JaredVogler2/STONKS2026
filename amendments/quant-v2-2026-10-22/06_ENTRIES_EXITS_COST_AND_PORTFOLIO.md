# Phase P1 — Trade Lifecycle, Execution Geometry, Realistic Costs and Portfolio Optimization

## 1. Separate predictive idea from execution policy

A swing setup can correctly identify a rising stock yet lose money if the order chases highs, the stop is too tight, its payoff is cut at 2R, and a high-spread fill eats ~0.5% round-trip. The inverse is also true: a good exit can make a weak selection rule appear viable only in hindsight. Design a **controlled factorial** but choose a small frozen subset; compare selection conditional on the same execution, then entry/exit policies on the same selection.

Existing code contracts: `signals/levels.py:compute_levels` computes midpoint±ATR entry, 1–3ATR stop with signal invalidation hints, 2R target capped below 120-session high and 10bps screening cost. `research/execution.py:simulate` models 2-session limit validity, daily OHLC, stop-first and 20-session default. `research/realeq/analysis.py` applies 25bps evaluation costs and 10-slot equal-cash books. These must be generalized without changing the historical version1 evidence.

## 2. Entry policy arms — independent of alpha selection

- **E0 existing reference:** next 1–2 sessions limit in close±0.5ATR range, midpoint geometric levels, size at actual fill. Maintain for baseline comparisons.
- **E1 lower-price participation:** place limit below decision close by 0.25–0.75ATR, validity 2 sessions, cancel if market condition invalidated; measure missed winners and same-day sequencing.
- **E2 confirmation stop order:** conditional buy if a causally defined pivot is exceeded on a completed bar with volume filter; actual fill may gap above stop, cap chase at max 0.5ATR; do not assume stop fills at pivot price.
- **E3 breakout/retest:** require postbreakout acceptance or retest over 1–3 sessions, then limit; never use the actual low/high of entry day to place hindsight order.
- Freeze which TWO arms tested per S strategy based on mechanism; no all-variants hindsight leaderboards. Treat no fill as no P&L and opportunity cost, not as winning signal. Partial fills, bid/ask and queue uncertainty flagged rather than invented exact fills.

## 3. Exit policies — archetype-specific hypotheses

- **X0 current 2R** with initial stop between 1–3ATR, target cap below previous 120 high. Keep as historical control.
- **X1 time-and-structural:** take profit or exit at 5/10/15/20 sessions with stop under a causal recent swing structure. Distinguish intraday protective stop from close-based thesis invalidation. Choose H from DEV, not first observed holdout.
- **X2 trailing trend capture:** initial predefined stop, later trailing stop e.g. highest CLOSE through t−1 minus 2–3ATR, effective no earlier than next bar. Never tighten using a high unavailable at the time of executed intraday stop. If 2R target is removed, tail outcomes and loss duration must be restressed.
- **X3 staged sale:** one fraction at +1.5R then rest trails, but independently account for quantity, round-trip costs and partial exits; don't double-count one signal as two independent trades.
- **X4 earnings-risk conditional exit:** exit before known-as-of earnings if user risk mandate prohibits earnings holdings; if earnings UNKNOWN don't pretend absence. Report missed post-earnings gains.
- For mean reversion H4, hypothesize nearer resistance/mean target rather than 2R; test only if a revised evidence mechanism is separately registered.

## 4. Exact economics and risk

- \(r_\mathrm{net}=(P_\mathrm{exit}(1-c_s))/(P_\mathrm{entry}(1+c_b))-1\) for proportional cost; include spread and impact once, with distinct fee components where possible.
- Risk in $ or R uses actual filled purchase, actual stop-market gap risk as stress; stop is not guaranteed. A candidate may pass midpoint R/R and fail worse fill R/R. Compute worst-fill marginal reward/risk BEFORE publishing actionable suggested levels.
- Estimate entry fill chance and expected slippage conditional on intraday market spread, stock dollar volume, gap, participation, time of day, volatile earnings shock. If no empirical quote data, treat cost grid as sensitivity not measured spread.
- Portfolio-level model: each trade's reserved funds, available cash, no >10% individual or >30% sector concentration (illustrative caps to be approved), simultaneous market beta constraints, maximum total risk budget, 10 concurrent slot cap as comparator, versioned margin=none baseline, no leverage. Do not assume endless capital for overlapping signals.
- Confirm no artificial cash creation for same-day sells and buys due to loop order and no dividend double count; property-test accounting cash+positions=equity minus costs.
- Benchmark portfolios with same exposure/turnover and risk budget; show market beta, Sharpe/Sortino with small-sample caveats, max drawdown, ES5, turnover and net active return, 5/10/15/20-session opportunity metrics.
- Cap annualized return metrics when observed under a fraction of year and avoid projecting small N as expected future profits.
- Use predicted economic utility to decide NO TRADE if expected net payoff / downside certainty inadequate.

## 5. Adversarial execution tests

1. Entry limit touched before stop vs after stop, partial/open fill sequencing (P0 `02`).
2. Bid/ask wide, trade last stale, halted stock, no consolidated SIP, split ex-day.
3. Overnight gaps where stop crossed before opening fill; canceled pending order and correct behavior.
4. Same day stop/target touched with unresolved order; report optimistic/pessimistic bounds.
5. Low/high before fill must NOT be included as certain post-fill MFE/MAE.
6. Stop order market vs stop-limit with gap through; max slippage stress.
7. Security corporate action modifies shares, stops and targets proportionately at ex-date.
8. 100 trades same day but only 10 slots: never more than 100% allocated cash, book correctly declines remaining 90, no sector concentration violation, idempotent no duplicates.
9. Stop hit at boundary between regular and extended sessions: policy clear and instrument hours correct.
10. Exit at end of 20 NYSE trading sessions including holidays/half days, no calendar-day off-by-one.
11. Net return once with cost in fill price vs post-applied cost, no duplicate calculation.
12. User A and B with different actual self-reported fills receive different stop/R/R/time assessments; signal-level returns immutable.

## 6. Proposed limited controlled experiment

Preregister *before evaluating*:
- S1: E0 vs E1, X0 vs X1 (only 4 arms; two-level entry/exit factorial, adjusted for family).
- S3: E0 vs E3, X0 vs X2 (only 4 arms).
- S2/S4/S5: own 1 default entry + 1 primary exit, with at most 1 explicitly motivated contrast.
- Report all arms with counts, no fill and net returns, effect paired by original opportunity date and capital slots. Choose DEV winner ONLY and then evaluate on independent future evidence; exclude any trading-selected outcome if entry never filled.
- Screening 25bps and measured/proxy liquidity-conditioned costs are sensitivity, not a free pass from realistic frictions.

**Success:** identifies whether selection, entry or exit contributes a reproducible net benefit, or reports all FAIL with no pretense. Either outcome improves APEX methodology.