# Protocol-2 Experiment Registration and Publication Template

This file is a FORMAT AND TASK CHECKLIST. It is not an already executed experiment. The coding agent must create a new frozen machine-readable `research-results/real-equities/PROTOCOL_V2.md` and `apps/pi-worker/src/apex_worker/research/realeq/protocol_v2.py` (names may vary by architecture) **BEFORE** inspecting any newly reserved evaluation outcomes. Existing protocol-1 remains immutable and executable.

## 1. Required registration fields

~~~yaml
id: realeq-v2-YYYYMMDD-<bounded-slug>
experiment_status: DESIGN_DRAFT # FROZEN / DEV_COMPLETE / EVALUATED / BLOCKED
registered_at: null
registered_by: coding_agent_with_owner_review
hypothesis_family: [S1, S2, S3, S4, S5] # individual candidate ID on each run
primary_test_question: "Does net active utility beat matched executable control?"
null_hypothesis: "Incremental paired mean net utility <= 0"
causal_mechanism: "<price/catalyst/flow explanation; not promise>"
universe:
  id: "<U0|U1|U2>"
  point_in_time_status: "<VERIFIED|SURVIVORSHIP_LIMITED|BLOCKED>"
  min_liquidity: "<snapshot-defined threshold>"
  excluded_instruments: "<ETF/ADR/OTC and rationale>"
data:
  provider: Alpaca
  feed: sip
  adjusted: split
  dataset_hash: "<record after retrieval, protocol frozen before outcomes>"
  source_rights: "<verified terms>"
  first_available_at: "<each column in provenance map>"
chronology:
  known_old_holdout: "2025-05-02..2026-09-21 (CONTAMINATED_FOR_NEW_DESIGN)"
  development_dates: "<fixed>"
  validation_dates: "<fixed>"
  fresh_evaluation_dates: "<future or new source; never claimed until truly fresh>"
  purge_sessions: "<label overlap plus extra explicit embargo>"
strategy_version: S1-v0.1
feature_schema_hash: "<SHA256>"
entry_policy: "<E0 or E1 etc>"
exit_policy: "<X0 or X1 etc>"
horizon_sessions: 20
costs_bps_each_side_primary: 25
cost_stress_each_side: [0,10,50]
benchmark:
  primary: "same-date/sector/liquidity/volatility matched executable control"
  secondary: "simple momentum, SPY/cash exposure matched, flat"
positions:
  max_slots: 10
  leverage: false
  concentration_limits: "<predeclared>"
primary_metric: "paired net active return per capital-day at equalized exposure"
economic_min_effect: "<predeclared minimum effect worth pursuing>"
uncertainty:
  resampling: "null-centered/studentized market-time blocks"
  block_sensitivity_sessions: [20,40,60]
  independent_regime_coverage: "<requirement based on power>"
  type_i_validation: "<simulation output, preflight>"
multiplicity:
  family: "<S1-S5 and any selected variants>"
  correction: "<justified algorithm>"
research_budget:
  github_runner_minutes_max: 45
  alpaca_requests_max: 300
  grid_arms_max: 12
source_sha: "<commit pinned before run>"
protocol_sha256: "<canonical hash pinned before outcomes>"
mode: RESEARCH_ONLY
production_promotion_allowed: false
group_actionable_allowed: false
~~~

Agent must validate the template fields with typed schema; descriptive placeholders cannot masquerade as executable config. Use code-based canonical JSON manifest for finalized frozen protocol.

## 2. Results directory schema and required content

`research-results/real-equities/<experiment_id>/`:
- `manifest.json` — frozen a priori protocol/source SHA/feature versions, actual data timestamps only as provenance, binary reproducibility and independent evaluation identity.
- `data_quality.json` — per-sector/symbol aggregate coverage, PIT status, corporate action anomalies and rights warnings, no raw prices.
- `metrics.json` — each legacy H1–H8 and S1–S5 (even not evaluable), event counts at gate stages, net expected utility and underlying samples, hypothesis p if validated method, intervals/clusters/power, OOS statuses.
- `portfolio.json` — equity-cash accounting, slots, exposure, turnover, net benchmark differential, tail and capital-day.
- `ablation.json` — all preregistered arms, return difference vs paired control, failed attempts and confidence intervals.
- `error_sensitivity.json` — entry-day path assumptions, corporate actions, cost/spread, sector bias and sample power variation.
- `comparisons.md` — honest narrative with legible tables, strategy selection vs order execution attribution; no dramatic KPI without denominators.
- `recommendations.md` — keep/revise/retire/blocked, empirical evidence and next experiment; no promotion request without release evidence.
- `execution_log.md` — GitHub Actions URL and conclusion, commit SHA, source rights, exact command, runner minutes/API requests and errors (never secrets).
- `review.md` — independent audit checklist with source and outstanding defects.

If constraints require fewer than these ten files, agent may consolidate but must retain all named fields and an index. All files compact (aggregate research metrics), no raw licensed bars or private member transaction records.

## 3. Falsification card for every new strategy

`Research idea: S# / Version####  
**Rationale:** precise mispricing mechanism, intended 5–20-session holding period.  
**Evidence status:** UNTESTED / DEV_ONLY / INDEPENDENT_OOS / FORWARD_SHADOW.  
**Null:** same-policy matched control equals or beats candidate after costs.  
**Unobserved source limitations:** ...  
**Main confounders:** sector, SPY bull market, liquidity, earnings, survivorship, missing fill.  
**Parameters fixed before experiment:** ...  
**Primary metric and economic minimum:** ...  
**Independent clusters/effective power:** ...  
**Counterfactual controls:** ...  
**Rejection condition:** ...  
**Success condition (research-only):** ...  
**Data hash/source SHA/Actions URL:** ...  
**Negative evidence:** ...  
**Next action:** keep unvalidated / extend data / abandon after power / shadow. `

## 4. Changelog, decisions and data integrity

- 2026-10-21: First real SIP fixed78 run `realeq-protocol-1`, aggregate NO TRADE, 0 validated. r2 result immutable.
- 2026-10-22: Quant V2 remediation prompts proposed (documentation only), research findings QV2-001–014, strategies S1–S5 require preregistration and independent validation.
- New implementation items get decision log row: `date / task / why / original evidence / protocol change / owner approval / PR / test / Actions URL / economics / status`.
- Do not mark PROTOCOL_V2 frozen before actual file hashes, rules and independent evaluation windows are fixed.
- Do not present “real dataset downloaded” as “real alpha tested,” “favorable holdout diagnostic” as future OOS, or “engineering tests passed” as profitable product.

## 5. Code review templates

Every implementation PR:
- objective, issue/QV2 ID, changed contracts/modules, prior behavior
- failing regression first, minimum patch, new unit/property tests
- source and protocol sha before and after, source timing proof
- economic test vs benchmark and controlled costs on allowed development, if applicable
- security/data entitlements/no vendor bars/no private lots
- operational quota estimate and rollback plan
- actual CI + experiment run conclusion
- blocked stage and next reviewer action.

This keeps all outcomes centralized and comparable without modifying earlier truth.