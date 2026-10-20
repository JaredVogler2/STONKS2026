# PHASE 04 — Novel technical-factor research and candidate engine

## Mission

Implement a serious, deterministic, auditable feature/signal factory for 5–20 **trading-session long swings**. Do NOT merely calculate RSI/MACD and give an arbitrary weighted buy percentage. Technical indicators are measurements; their predictive contribution must be demonstrated later by walk-forward validation.

## Core engineered features

**Momentum and relative strength**
- 5/10/20/60-session total returns using split-aware historical bars, relative performance to SPY/sector benchmark, trend slope, cross-sectional percentile, recency of 20/60-day highs, multi-horizon consistency vs overextension.
- Price vs 20/50/200 session baselines, slope, separation in ATR units, adverse distance from pivot and lagged breakout retention.
- Intraday close location value and overnight/regular-session decomposition where provider supports comparable sessions.

**Participation, liquidity and compression**
- Dollar turnover, relative volume vs 20-session baseline on same data feed and time-of-day, abnormal volume z-score, price × volume impact proxy.
- ATR and realized volatility at 5/10/20 sessions; Parkinson/Garman–Klass estimators (when justified); volatility squeeze percentile and volatility expansion with confirmation.
- Realized range, gap size/distribution, turnover asymmetry, accumulation proxies while explicitly **not** claiming institutional buying can be inferred from candle volume alone.
- Volume/liquidity and spread penalties; low-liquidity names are vetoed regardless of exciting momentum.

**Structure / trend persistence**
- Consolidation base length and depth, pivot validity, failed breakout counts, distance to local supply/resistance, drawdown vs recent peak.
- Trend efficiency ratio, autocorrelation at horizons, 5–20-day forward excursion historically (features never computed from future).
- Detect nonstationarity and regime-dependent feature sign changes.

**Regime and market context**
- Broad SPY/QQQ/IWM trend and realized vol, breadth, distribution days, sector-relative strength, cross-asset risk context only with legally available data.
- Assign regime probabilities rather than one bullish flag, e.g. trending-up, choppy-neutral, risk-off, rebound, volatility shock. A hidden Markov model is optional and must beat simple baselines out-of-sample.
- Correlation/crowding penalty if all candidates are semiconductors or rare earths.

## Hypothesis families (candidates, not assured profitable strategies)

H1. Volatility contraction → expansion after base formation, confirmed by relative participation and positive sector regime.  
H2. Multi-timeframe relative-strength continuation after shallow pullback to rising support.  
H3. Post-event drift only when source release time and premarket gaps allow a realistic entry; compare to placebo.  
H4. Oversold-to-recovery mean reversion only in qualifying up/sideways regime, avoid collapsing momentum traps.  
H5. Gradual leadership rotation among sectors: cross-sectional strength improvement + liquidity + market breadth.  
H6. Gap-and-hold with intraday stabilization; avoid assuming immediate open execution at official print.  
H7. Catalyst + compression concurrence; explicitly test whether intersection beats each feature alone.  
H8. Close-to-close seasonality with strict holdout and multiple-testing penalty; treat as weak prior until proven.

Implement signal generators as plugins with `strategy_id`, `version`, `feature_dependencies`, `eligibility_rules`, `hypothesis_description`, `scoring_contract`, `failure_modes` and `citations`; every candidate yields explainable positive + negative evidence.

## Custom composite architecture

Rather than hand-picked weighted average alone, begin with:
1. **Hard eligibility and freshness gates**;
2. **Feature candidate generation**;
3. **Regime matching**;
4. **Independent evidence groups** (price, volume, sector, catalyst, volatility);
5. **Concentration and ambiguity penalty**;
6. **Historical outcome model** trained in Phase 06 and calibrated in Phase 07.

Compute internal QUALITY (0–100 ranking), not a probability. Feature family dependencies must be recognized; e.g. RSI, MACD and short-term returns are correlated and NOT three independent confirmations.

No signal if there is an impending earnings surprise risk without an accurately sourced event schedule and acceptable policy; when earnings calendar data is unknown, apply configured conservative caution, never label “no earnings soon” without evidence.

## Algorithm integrity

- Vectorized feature calculations using bars known as of decision timestamp; emit feature version + data hash.
- Null/insufficient history features are typed NULL with confidence penalty or veto, not replaced by invented zero.
- Strict market-split and dividend handling, documented total-return assumptions.
- Same inputs and seed yield same candidate ranking.
- Performance budget on Pi 4 GB: batch over ≥500 symbols without exceeding practical memory, avoid holding entire full-history panel in RAM, cache incremental rolling windows.
- Session-aware recalculation only when input observations mature; handle partial daily bar as provisional.

## CLI/dashboard and tests

- `apex features build --date DATE --mode fixture`; output ranked candidates and each contributing measurement.
- Strategy explorer page: factor contributions, correlations, regime, validation status and current candidate disqualifications.
- Property-based tests on synthetic flat/up/down/volatile/illiquid price sequences; no divide-by-zero, negative price, invalid ATR, future leakage or split artifacts.
- 12 deterministic toy scenarios with expected rank direction and disqualification codes.
- Compare against baselines to be evaluated in Phase 06: SPY passive, random qualified entry, pure 20-day momentum and basic breakout.

## Acceptance

Every displayed score can be recomputed from stored features. No fabricated confidence; all factor scores are versioned and sourced.