# V3 Architecture — A General Strategy Discovery and Evaluation System

## 1. Goals and hard system boundaries

Build a *modular, auditable research platform* capable of systematically proposing, implementing, rejecting or forwarding distinct investment hypotheses. APEX currently has mature source code under `apps/pi-worker/src/apex_worker/research/realeq/` and `features/`; reuse it. Don't fork a second Alpaca adapter, market calendar, database, Discord process or execution simulator. New functionality belongs in a carefully separated namespace such as `apex_worker.discovery.v3`; adjust exact module placement after inspecting project imports.

Key system invariants:
- Training/DEV research and live scanning use identical causal feature calculation on the same as-of snapshot.
- A hypothetical “strategy” is a complete trade policy, not merely a boolean signal: universe, information availability, selection, execution, position management, cash management, exit, allocation and competing opportunity.
- Scanning one account with limited capital yields a prioritized **portfolio decision** or NO TRADE, not a bag of unrelated opportunities.
- Every experiment has an immutable manifest and a distinct research/confirmatory status; no winner promoted by inspection of contaminated dates.
- A scientific failure is a first-class output with enough provenance to reproduce and identify why.
- No strategy generator can execute its own proposed code with production credentials, no arbitrary shell injection from LLM hypothesis text, no automated release of tested alpha.

## 2. Shared contracts (Python dataclasses + versioned JSON schema + TypeScript where surfaced)

### MarketSnapshot
`snapshot_id, universe_id, asof_utc, observed_at_utc, market_session_id, provider, venue/feed, clock_basis, entitlements, symbol_availability, point_in_time_membership, quote_quality, corporate_action_revision, benchmark_prices, source_hashes, dataset_hash, feature_schema_hash`.
No future publishing timestamp or revised data can appear in a past snapshot. Preserve separate field `available_at_utc` for each feature and event.

### HypothesisSpec
`hypothesis_id, semantic_version, parent_hypothesis, economic_mechanism, competing_explanation, null_hypothesis, falsification_test, discovery_timestamp, author_type(human/agent), prediction_target, minimum_economic_effect, horizon_candidates, permitted_universe, required_inputs, causal_availability, liquidity_requirements, primary_policy, control_policy, risk_constraints, family_id, tested_variant_count, baseline_ids, allowed_experiment_budget, release_mode=RESEARCH_ONLY`.

Proposals must include an economic reason. “Mix RSI 19 with MA 61” without a mechanistic rationale is not sufficient. Also accept a compelling *counterfactual* rule: for example “Do NOT use the original limit-stop wrapper; use SPY/cash allocation” as a candidate hypothesis.

### CandidateEvent
`symbol, event_time, knowledge_cutoff, candidate_features, proposed_trade, invalidation, risk_estimate, expected_holding_sessions, strategy_id, evidence_refs, rejection_codes, counterfactual_group_id`. Output NO TRADE reason when required inputs absent.

### ExecutionPolicy
Separate typed `entry policy`, `fill model`, `stop/thesis exit`, `target or trend exit`, `forced timeout`, `cost model`, `horizon`, and `order-existence time`. Inputs/outputs from existing `execution_v2.py`. A limit touched by a daily low is not a guaranteed fill and same-bar path unknown. Include pessimistic and optimistic bounds and conservative primary outcome; optional intraday reconciliation only with authorized data.

### PortfolioPolicy
`capital, reserve_cash, allowed_instruments, account constraints, sector/max_symbol/risk limits, max_exposure, leverage=0 default, diversification/correlation, risk_budget, order priority, market_filter, holding/turnover cost, benchmark_choice, cash_yield_assumption, volatility_target, objective_definition`. Hedge leg can be virtual comparator if no short execution; do not pass it as broker instructions.

### EvaluationRecord
`experiment_id, protocol_sha, source_sha, data_hash, prior_attempts, preregistered_hypothesis, all_configurations, train/validation/evaluation window, reviewer_status, trade_funnel, economic_results, independent_clusters, statistical_test, uncertainty, factor_attribution, discovered_failure_modes, adverse_controls, release_verdict, forward_status, machine_published_at`.

## 3. Modules (sketch, adapt to real repo)

- `discovery/registry.py` append-only typed registry & parent/child provenance, versioned hypotheses.
- `discovery/features.py` view of shared feature panel with as-of strict missingness and feature coverage, no new historical hindsight.
- `discovery/proposals.py` proposal generator interface; deterministic templates first, optional LLM research assistant later. LLM proposes typed text/code review patches *not production-released model*.
- `discovery/runner.py` budgeted offline DEV trials using existing real SIP loader, separate fixture tests, caches only if allowed; work quota/owner approvals.
- `discovery/policies.py` entry/stop/target/time/capital policy composition; a tested candidate cannot implicitly change policy based on future performance.
- `discovery/book.py` reusable portfolio evaluator and constraints; compare existing book.py, add independent invariants rather than replacing blindly.
- `discovery/stats.py` vector return curves, cluster stats, paired controls, family-wise search adjustment and threshold decisions.
- `discovery/selection.py` owner-review research shortlist based on pre-registered objective, risk and statistical limits, not top in-sample CAGR.
- `discovery/report.py` compact normal/negative-control/contribution reports, no proprietary raw price table in git.
- `discovery/forward.py` read-only source of future locked decision/outcome data; separate confirmatory lane immutable as described in 02.

## 4. Candidate-policy compositionality

A discovery experiment may vary one axis (selection, entry, exit, allocation), but must keep the others fixed and record this. Experiment design:
- selection fixed, exit variation → test wrapper contribution;
- entry/exit fixed, selection variation → test stock selection;
- fixed opportunity list, allocation policy variation → test capital scheduling and diversification;
- fixed all but market context → test conditional regime exposure;
- zero-trade and benchmark controls retain identical calendar/capital book.

For a novel idea, preregister at least a simple economic counterfactual that could falsify the proposed mechanism. Evaluate incremental portfolio utility and turnover—not just per-filled-trade P&L.

## 5. Discovery search controls

Budget 3–5 **mechanistically diverse** primary experiments per initial pilot, with 1 fixed primary configuration each (plus up to 2 documented mechanism ablations on DEV only). No unconstrained automated Bayesian tuning on contaminated history followed by fake p-values. Each new proposal consumes a tracked hypothesis family and search budget. Include tests for “model chose 200 variants, returned only winner,” which must invalidate result if trials omitted.

Avoid train/test re-use by storing dataset lineage, previous result access, prompt/log metadata and trial ancestry. The system cannot truly prove researchers did not see outcomes, but it can make all system-generated evaluations auditable. A frozen experimental protocol and complete attempt log are required before truly fresh prospective observations.

## 6. Output economics

Measure for each proposed full policy:
- implementable net compound portfolio total return and annualized growth only when horizon supports it;
- maximum drawdown, 95% expected shortfall, realised downside volatility, average capital deployed and cash-days;
- turnover, costs, slippage bounds, percent unfilled, median duration, risk concentration, factor beta and stress performance;
- incremental risk-adjusted/utility outcome against SPY/cash and same-risk/market-beta alternatives, not only random symbol baseline;
- profit per calendar and capital-day, not only per executed trade;
- repeatability across nonoverlapping periods, independent market regimes, asset breadth and source revisions;
- model/data complexity and operational cost.

**Status:** `PROPOSED→REGISTERED→DEV_TESTED→REJECTED|RESEARCH_SHORTLIST→FORWARD_SHADOW→INDEPENDENTLY_REPLICATED→OWNER_REVIEW_REQUIRED`. A status transition requires evidence, but no status automatically enables personal/group trade advice.

## 7. Acceptance

A CLI and a GitHub-hosted standard-runner workflow can register candidate hypotheses, run a bounded dev experiment on actual Alpaca historical SIP bars with immutable manifests, and publish all attempted mechanisms under an isolated aggregate research result directory without source price bars, secret leakage or production mutation. Deterministic fixtures prove stock selection, entry, exits and allocation have disentangled attribution. Rejected hypotheses leave inspectable records. Unvalidated models never publish live signal percentages.