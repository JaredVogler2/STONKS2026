# Scientific Integrity — Sequential Discovery, Falsification, Multiple Testing and Real Forward Evidence

## Main concern

Search breadth and innovative AI hypothesis generation can increase *data snooping*, not discovery. A platform trying hundreds of strategies, horizons, industry conditions and entry-exit rules on 2016–2026 then showing the highest return is structurally likely to produce false positives. Real-data alpha evidence must be earned with tests designed to fail.

Current development/previously exposed history includes:
- first 66-stock protocol-1 research;
- entire U1 2019–2026 DEV and an exposed 2017–2019 backward presample;
- all H1–H8/S1–S5 selected/diagnostic variants and reports; some 2025–2026 holdout viewed repeatedly.
**None of these can become an independent untouched final set by changing its label, seed, or strategy name.**

## 1. Full hypothesis search ledger (immutable versioned source)

For every attempted mechanism/feature/parameter/model family, record BEFORE evaluation:
`trial_id, parent_id, mechanism_family, proposed_by(human/agent), proposed_at, code_sha, strategy_sha, prompt_hash, dataset_hash, asof_source_coverage, observation_window, feature_inputs, primary_estimand, benchmark, execution_policy, estimated real cost, null, falsification, risk_constraints, planned sample requirement, search_round, max_trials, success_rule, reason_trial_chosen`.

AFTER evaluation append:
`run_url, started/finished, outcome_hash, full strategy/control net return distribution, independent_time_blocks, regime coverage, dependence-adjusted uncertainty, model complexity, negative_controls, rejected_reason, next_action, reviewer`.

Track failed experiments, agent discarded ideas, unsuccessful parameter sweeps and learning from contamination. New ideas are descendants of earlier work, not independent novel discoveries for multiplicity. Store results in `research-results/discovery-v3/<trial_id>/` where allowed, register JSON schema and update index. Never publish only the best 3.

## 2. Four evidence tiers

1. `MECHANISM_PROPOSAL` — plausible and falsifiable; no performance evidence.
2. `DEV_DIAGNOSTIC` — uses existing historical data from 2016–2026 already inspected; useful to reject weak ideas, not a claim of statistically confirmed future alpha.
3. `PROSPECTIVE_EXPLORATORY` — immutable future decisions but researcher can see interim outcomes and refine; supports learning but not untouched confirmation of selected final policy.
4. `INDEPENDENT_CONFIRMATORY` — truly registered ahead of future observation, frozen hypotheses/implementation/metrics/decision window, independent sample/time/regimes, blind to outcomes until registered look, sufficiently powered and licensed PIT source.

Underpowered, source-limited and failed statuses orthogonal. One lucky forward paper month does not establish repeatability. A “new sector holdout” within contaminated time is robustness stress, not pristine time evidence.

## 3. Valid statistical estimands and dependent inference

- Primary effect is **incremental daily book utility/return against investable exposure-and-risk matched alternative**, accounting for cash and missing opportunity, not per-filled-trade p-values alone.
- Use paired daily returns/decision blocks aligned in time and preserving market shocks. Samples clustered by date/sector; overlapping 20-session trades mean stock trades are not independent N.
- For block bootstrap/hypothesis testing, pick memory length informed by holding/market dependence; use 20/40/60+ sessions as sensitivity. Avoid assuming normality with 12 blocks and claiming p=0.002.
- Distinguish ordinary bootstrap percentile tail from null-calibrated p-values; test empirical size and power under fat-tail cross-stock factor-shock simulations that mimic book construction and candidate selection. Earlier A1 simulations exceeded nominal false-positive rates (5.8–6.6% when 5% intended); further improvements must be validated against *production-shaped* nulls and alternative estimators, not merely synthetic IID samples.
- Multiple hypotheses and multiple optional looks: choose a documented FWER/FDR procedure based on ALL attempted trial families and adaptivity. Consider deflated Sharpe ratio, White Reality Check / Hansen SPA or false-discovery control only if assumptions and actual implementation validated; no magic correction assumed sufficient.
- If a research agent repeatedly inspects data then changes hypothesis, record as DEV learning. Future trial requires a prospectively new sample/series.
- Formally quantify minimum detectable economically important improvement from volatility and autocorrelation; target 80% power (illustrative) *if feasible*, otherwise state not enough data. Annualized Sharpe on 32 trading signals can be meaningless.
- Report bootstrap interval for **difference vs baseline**, not simply zero absolute return in bull equity market.

## 4. Data leakage and dependence traps

Tests must fail if:
- a feature from t+1 enters decision at t; earnings/SEC release first public after decision; current S&P survivors used as history without limitation.
- sectors or factor betas fitted over future years then used to explain historical choices.
- model hyperparameters tuned on the presample and same presample presented as untouched.
- exit learned from future realized MFE/MAE or stock chosen because it survived delisting.
- target and stop both hit within one OHLC bar but market path guessed in strategy's favor.
- selected symbols share same market shock but confidence based on #trades rather than #decision clusters.
- random baseline has different entry or holding policy, leverage, costs or chance of fill.
- market regime model uses whole-series smoothing rather than online filtering.
- auto-discovered trial is missing from search log, or “novel” strategy simply renames a previously tried configuration.
- prospective decision is backfilled after label became known.

## 5. Multi-agent research architecture (optional, no runaway compute)

Agents may propose structured mechanisms, critique data causality, design falsification controls and compare economics, but cannot override registered test results. A coordinator selects small bounded batch before evaluation on DEV, validates schema/test plan, and runs gated tasks in trusted GitHub Actions. A critic independently attempts to disprove each before any further budget. No one-shot agent “run for 48h until profitable”, no paid token service forced. External agent-generated code reviewed/tested in PR without production secrets or market execution permissions.

Information-value selection may prioritize experiments with:
- a likely answer even if hypothesis fails,
- independent observable mechanism,
- large enough opportunity count for power,
- low cost/complexity,
- strong risk-adjusted economic upside if true,
- useful causal negative controls,
- portfolio diversification benefit.

## 6. Decision/disposition framework

`REJECTED_DEV`: underperforms meaningful control or relies on unavailable data/implausible fills.
`PROMISING_EXPLORATORY`: positive DEV but effect uncertain/search-biased.
`SHADOW_ONLY_REGISTERED`: forward future immutable decisions before observed labels, no promotion.
`INSUFFICIENT_FORWARD_POWER`: no independent blocks/years/regimes; do not lower confidence standard.
`CONFIRMED_NET_ECONOMIC_VALUE`: preregistered future test outperforms appropriate alternatives after realistic friction/risk with family-wise uncertainty.
`REPLICATION_REQUIRED`: positive forward evidence but no second independent environment/universe.
`NO_TRADE`: no qualified net benefit.
`SOURCE_BLOCKED`, `SIMULATOR_INVALID`, `COMPLIANCE_BLOCKED`: explicit gates.

A financially interesting discovery can still not meet live data rights or group advice rules. Never collapse statuses into a single misleading green “validated” badge.

## 7. Mandatory research deliverables

- `docs/discovery-v3/RESEARCH_PROTOCOL.md` with windows, evidence tiers, all tests, trial budget and moment when frozen.
- `research-results/discovery-v3/TRIAL_INDEX.md` covering every attempted idea including negative.
- `docs/discovery-v3/STATISTICAL_SIZE_POWER.md` with actual simulation commands/output and limitations.
- Prospective shadow chain and separate exploratory V3 series (02) with release statuses and external timestamp proof.
- Model-selection test comparing complex learner to simple static benchmark across time without p-hacking.

**A research program that rejects nine apparently profitable backtests and preserves one genuinely credible candidate is stronger than a program that always finds a winner.**