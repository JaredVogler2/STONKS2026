# PHASE 07 — Signal promotion, probabilities, risk sizing, targets and trade lifecycle

## Mission

Translate research candidates into **selective actionable research alerts** for 5–20 trading-session holds, with explicit validity gates and honest uncertainty. Ensure high score does not become an unearned “high-confidence” label.

## Three-stage decision architecture

1. Eligibility gate: liquid eligible U.S.-listed symbol, tradability/verified eligibility status, no halt/penny/missing price, feed freshness/quality, hard event-risk policy, verified time calendar, allowed long-only strategy, concentration threshold.
2. Predictive forecast: model version with out-of-sample calibration computes competing outcomes conditional on setup: target before stop, stop before target, expiry/no hit; *model uncertainty* separate from estimated event probability. Raw ranking Quality 0–100.
3. Economic policy: calculate R/R and expected expectancy *after trading friction and tails*, benchmark excess vs doing nothing; only promote if conservative bound passes configured rules. If no qualified candidate, NO TRADE.

## Price levels

Define `entry_min / entry_max` based on identified tradable pivot/ATR volatility and execution uncertainty, not random ±1%. Level must be >0 and time-stamped, with max slippage guard. Define:
- `stop_level` from invalidation/support and volatility, not mechanically equal loss tolerance; include overnight gap risk.
- `target_primary` from modeled conditional distribution, resistance, measured extension, or realistic expected positive excursion; optional secondary target.
- `reward_to_risk` = (target - representative entry)/(entry - stop), adjusted for plausible costs, no false precision.
- `expiry`: if no fill within e.g. 1–3 *trading sessions* (config), expire unentered setup; after user entry maximum typical 20 sessions unless specifically risk-reviewed extension.
- `horizon_min / max` from conditional time-to-hit quantiles estimated from historical samples (not guessed); if no calibrated history, mark heuristic and avoid strong language.
- `asof` / `quote data_age`, from clearly labeled IEX single-exchange vs delayed consolidated.

## Forecast recommendation policy defaults — research starting points only

- Overall alert Quality≥80/100 is a **candidate ranking**, not probability.
- Prefer R/R≥2 on risk-adjusted levels; threshold tunable after walk-forward (do not enforce if unrealistically biases selection without tests).
- Only label “historically calibrated” after Phase 06 OOS and minimum sample criteria.
- Consider risk-of-ruin: hypothetical default per-position budget 0.25–0.75% of hypothetical account capital, portfolio open risk cap 2–3%, correlated sector exposure caps; user adjustable and not assigned without explicit capital.
- Maximum concurrent highly correlated signals configurable; no alerts merely to fill quota.
- Flag upside reward that has been consumed by post-event gapping: signals degrade to watch or expire, not recommended chasing.
- If position held during unverified earnings, report gap risk; optional hard veto.

## State changes

- New candidate → vetted qualification → publish with immutable snapshot and versioned revision chain.
- Market evolution → WATCH / ACTIVATE / INVALIDATE / UPGRADE / DOWNGRADE / TARGET_REACHED / TIME_WARNING / EXPIRED with explicit transition cause and idempotency.
- Never mutate old model forecast as if it predicted revised conditions. Show original forecast and new forecast separately.
- Once published, automatic confidence changes must not retroactively alter public hit-rate denominator.
- Breakout failure or volatility regime flip can invalidate a thesis even before price stop, but reasons and evidence timestamps must be traceable.

## Contradictory-evidence checklist in each published signal

Include:
- main technical support, material catalyst, current market regime
- at least one opposing risk (e.g. earnings uncertainty, sector crowding, IEX liquidity limits)
- confidence status / empirical sample count / interval or insufficient data
- exact event predicted and outcome deadline
- type of feed, data recency and whether quote is tradable proxy or delayed
- estimated hold in trading sessions, entry validity timeframe, exit triggers
- optional risk/notional example that NEVER pretends user risk capacity is known.

## Performance accountability

Signal stats measured on ALL published recommendations, not only user-entered winners. Include no-fill/censored cases; separately report theoretical entry and actual self-reported fill outcomes. Backfill false positives and missed alerts; keep registry of model versions to prevent performance selection bias.

## API/dashboard

- `/signals` filters published, expired, invalidated, watch, no-trade reasons; historical full signal cards.
- Lifecycle event timeline with evidence snapshots, as-of and changes.
- Audit record on every transition, persisted then queued to notifications.
- Strict stable serialization and rounding; decimal monetary arithmetic.

## Acceptance

A signal card and structured Discord payload reproduce the same details. In demo, lifecycle changes exactly when replay inputs change; missing or delayed data does not create fresh alerts. Tests include price target stop crossing, horizon expiry on holidays, entry no-fill, overlap same-bar ambiguity, currency/share precision and stop gap.