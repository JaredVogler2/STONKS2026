# Phase P1/P2 — Replacement Ranking, Probabilistic Outcome Model and Economic Decision Engine

## Why replace the current one-number score

The first genuine real market test showed almost no relationship between current quality rating and realized net R for H5 (Spearman ~0), and negative unseen Brier skill for H5 and H8 forecasts. A 100-point signal score is not a 100% chance of being right. Scores useful for explaining evidence may have no trading value. The app must stop treating “a high number” as action justification.

Implement three independent, versioned components with strict monotone data-availability constraints. Do not substitute black-box ML for data integrity.

## A. Opportunity ranker (selection across all current eligible candidates)

Input: as-of causal `FeatureSnapshot` + current eligible universe + trading-capacity/market context. Output:
`rank_value` normalized order statistic or predicted ordinal target,
`rank_validity`, `basis`, `contributing_factors`, `missingness`, `last_model_trained_at`,
`market_and_sector_exposures`, `rank_version`, `uncertainty`.

Tests:
- First compare simple rank by 20-session raw momentum, 60-session momentum, sector-relative momentum and S2 residual return.
- Linear regularized ridge/elastic-net ranker optional only after development has enough independent trades; tree/boosted ranker only if truly improves OOS vs simple rank after cost and multiple testing.
- Target should be forward **net risk-adjusted outcome** conditional on a fixed executable policy, or paired excess return vs matched control. Do not train on raw close-to-close when live orders are limit/stop dependent unless explicitly separate ranking target.
- Signals near identical ranks must not generate false precise ordering; show confidence group where justified.
- Test monotonic feature drift and score stability after splits, holidays and event updates. No cross-sectional rank computed using future members.
- Loss functions handle censored/no-fill trades; don't optimize success-only filled winners.
- Compare top decile and top 20% quality vs all, net book outcomes and portfolio concentration. Do not claim improved ρ on historical observed holdout as final evidence.

**Gate:** ranker can be hidden or “descriptive evidence only” without empirically positive prospective investment utility.

## B. Conditional outcome distribution (one policy, explicit horizon and timestamp)

The current model predicts competing target, stop and time outcomes. Evolve toward a **distribution**:
- `P(fill before expiry | signal, order)`;
- conditional on fill: `P(target before stop by H)`, `P(stop first by H)`, `P(expire by H)`, with exact policies, proper sum-to-one and interval semantics;
- return outcome quantiles and tail/shortfall estimates net of costs; gap losses and unfillable stops preserved.
- Use calibrated probabilities only with sufficient **independent** time/sector samples and genuine OOS; record calibration model version and forecast expiry. No aggregate n-based Bernoulli uncertainty for a single fitted conditional prediction.
- Predicting win/loss may be auxiliary, never primary economic objective; compare net expected R and actual positive-outcome payoff sizes.
- Fit baseline constant conditional rates + regularized logistic/multinomial, competing risks/survival models only if enough data. Algorithm complexity must be justified by predictive improvement, not appearance.
- Validate multiclass log loss, Brier skill vs baseline, calibration plots with bin sample counts, reliability by horizon/regime, expected return bias, tail coverage; use conformal or bootstrap intervals only when assumptions and empirical calibration justify.
- Train on research policy **population** matching live-qualified/filled and regime status; if full-candidate predictive model used, make conditional selection probabilities explicit. Avoid training on duplicate overlapping per-symbol events and claiming 500 independent bets.
- Handle in-sample/isotonic small sample instability; if little data, use shrinkage and mark forecasts `INSUFFICIENT_CALIBRATION`.
- Forward predictions immutable: store published as-of and actual later outcome; corrections create versioned amendment, never rewrite original probability.

## C. Economic policy gate — NO TRADE is a first-class action

Inputs: strategy rule, candidate rank, calibrated probabilities if available, conditional R/payoff distribution, entry execution probability, available risk budget, current market beta/sector occupancy, fee/slippage model, alternate cash/ETF opportunity and permitted legal/market-data mode.

At minimum evaluate:

`EV_per_signal = P(fill) × [P(T|fill)×E(net_R_T) + P(S|fill)×E(net_R_S) + P(E|fill)×E(net_R_E)] − explicitly modeled signal-level overhead/opportunity cost`.

All net R components must already include execution cost, else subtract once; never double charge. For portfolio, prefer simulation of capital-constrained multi-signal book with conflicts and holding capital-days rather than summing independent EV.

Selection policy:
1. data source and model/feature versions **VALID** and current;
2. source live/DEMO/licensing and user's release profile allow informational notification;
3. candidate passes liquidity/tradability/earnings/catalyst known/unknown policy;
4. downside expected shortfall, gap risk, market regime and portfolio concentration fit budget;
5. predicted economic advantage over exposure-matched alternative exceeds pre-registered margin given uncertainty and conservative fill assumptions;
6. calibrated point probabilities displayed only when separate empirical gate passes; deterministic rule economics may remain `RESEARCH_ONLY` if forecast unvalidated.
7. If any gate fails: explicit `NO_TRADE` with reason IDs e.g. `UNVALIDATED_EDGE, STALE_PRICE, PIT_DATA_UNAVAILABLE, OUTSIDE_ENTRY, NEGATIVE_NET_UTILITY, MODEL_EXPIRED, SECTOR_RISK, ALREADY_HELD, INCOMPLETE_COST_MODEL`.

No command “Buy $X, add shares, sell now” sent to other members without independently reviewed licensing/adviser authorizations and account-specific suitability requirements; user-owned tracking stays separate.

## D. Adaptive retraining, concept drift and audit

- GitHub Actions scheduled train with immutable source SHA/dataset hash/feature schema; no owner laptop. Production model registry signed and Pi atomically verifies/warm-switches. Do NOT auto-promote based on Brier improvement alone if net expected utility worsens.
- Track monthly/week/sector stability (source only where permitted); recalibrate based on drift only when enough new matured outcomes and frozen criteria. Underpowered drift gets `NOT_ENOUGH_LABELS`, not automatic new model.
- Maintain champion vs challenger shadow predictions for exact same observations; cluster bootstrap **paired** incremental book utility; use delayed matured labels correctly (20+ sessions after signal).
- Model expiry or stale unvalidated result prevents new HIGH_CONVICTION, but not general informational research/market uptime notices.
- Admin sees full trace: `candidate→eligibility→rank→forecast maturity→net EV→policy action→Discord outbox version`. No fake green status.

## E. Required product/API contracts

`OpportunityAssessment`:
- candidate/symbol/research_strategy_id and version
- publication timestamp and source feed/as-of/known_at
- opportunity_rank (unit-less percentile) and rank_status
- model_event_probability {definition,horizon,point,status,calibration_version,independent_samples,uncertainty_method}
- scenario_payoffs/entry_fill/expected_net_R and policy assumptions
- macro/sector exposure, maximum downside tolerance
- decision {mode,reason_codes,allowed_action}
- immutable evidence snapshot and signal ID

Define corresponding Python dataclass, JSON schema and TypeScript validator in `packages/contracts`; fail unknown fields appropriately and use frontend/Discord same payload. No raw Alpaca data or private member lots in shared group response.

## Acceptance

On future genuine OOS, rank should show incremental net risk-adjusted advantage vs simple momentum **or be disabled as non-predictive**. Proper probability model must demonstrate independent calibration/skill or display UNVALIDATED, no percent confidence. Economic decision must preserve capital budget and abstention, never manufacture positions or auto-trade.