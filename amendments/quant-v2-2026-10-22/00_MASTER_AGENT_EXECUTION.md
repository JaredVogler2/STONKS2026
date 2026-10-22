# Master Coding-Agent Directive — APEX Quant V2

## 0. Precondition and exact objective

You are the coding agent for `JaredVogler2/STONKS2026`. The owner has authorized **re-assessment, revision and enhancement** of the stock-selection and trading methodology **subject to adversarial testing**. The initial real-equity experiment is complete and found no validated alpha. Improve APEX's scientific correctness, practical expected net returns and interface truthfulness. **Do not claim positive expectancy before an independent, causally correct holdout and forward paper evidence**. No real broker orders or new paid subscriptions.

Before any work: fetch latest `main`, list active branches/PRs and current CI, read all files in this amendment folder and canonical docs. Document any discrepancy between this October 22 snapshot and newer code. Do not force-push, reset another agent's work, merge active work or edit a historical result. Finish tasks even if no candidate becomes profitable; a measured negative result is success for research integrity.

## 1. Work in vertical, independently reviewable slices

### Milestone M0 — Repo audit and baseline snapshot (documentation + repeatable commands)
- Confirm `main` SHA, Python/JS tests, actionlint, locked dependencies and signed data manifest. Read [first research report](../../research-results/real-equities/2026-10-21-fixed78-sip-8y-r2/).
- Reproduce the **ROUND_FLOOR missing import question** against current and original commit; explain how real-equity runner and 674 Python tests apparently succeeded. Probe shadowed names, branch SHA, test path, production import and runtime traceback. If reproducible, fix on tiny PR with regression and **quarantine existing experiment until provenance reconciliation**. Never silently alter its published data.
- Enumerate existing real-market critical defects vs merely theoretical shortcomings. Emit `docs/quant-v2/M0_AUDIT.md` with P0/P1/P2 and supported evidence.

### Milestone M1 — Correct execution and inference (one or two small code PRs)
- Resolve entry-day low/high sequencing, entry-day MFE/MAE, stop/target same-bar policy, realistic fill/limit/partial-fill policy. Add intraday 1–15m replay for selected cases if permitted and affordable; otherwise mark ambiguous intervals and run pessimistic/optimistic/no-event bands without invented path.
- Correct research statistical inference and label semantics; compare unpaired and matched **paired** controls. Fix cost mismatch between `signals/levels.py` 10bps gate and 25bps real test; parameterize and version, don't surreptitiously change historical H1–H8 results.
- Run negative controls and true numerical invariants. Preserve original experiment immutable; output correction audit and new protocol version.

### Milestone M2 — Data sources, survivorship and causal features
- Build coverage-controlled larger tradable stock universe of perhaps 300–1000 eligible names when licensing permits, with point-in-time membership and corporate events; NO fabricated delisted history or current survivorship disguised as causal. Starting 66-stock universe remains a comparator.
- Correct HON 2026-06-29 event and EXC 2022-02-02 split/distribution behavior with verifiable source or quarantine affected windows and report sensitivity. Audit all dividends/distributions and split-adjusted analytics.
- Build exchange-session-calendar-safe field availability timestamps; later event data feeds H3/H7, but no fabricated backdated catalysts. Implement discovery of licensed PIT earnings data and explicit absence semantics.

### Milestone M3 — Pre-register quantitative program BEFORE variant execution
- Record new falsifiable hypotheses S1–S5 and H1–H8 disposition; assign experiments in order, fixed parameter **small bounded grid** if justified; freeze train/validation/holdout/embargo, search budget and baseline. `realeq-protocol-2` is NEW with canonical JSON SHA. New data cannot magically restore pristine status to 2025–2026 holdout already inspected.
- Derive minimum detectable economic effect and effective clusters from development variance, block dependence (4+ weeks) and cost assumptions; do not hard-code 100 correlated trades as adequate proof.
- Define falsification, null, superiority/non-inferiority and multiple testing adjustments before seeing results. Selection from 229 previous configurations obligates data-snooping caution.

### Milestone M4 — Replacement strategies implementation
- Implement **S1** controlled pullback of liquid market/sector leaders; **S2** sector- and market-residual strength; **S3** breakout confirmation/retest; **S4** publication-time-correct earnings/guidance continuation; **S5** liquid sector ETF trend allocation. Detailed formulations and adversarial controls in `05_REPLACEMENT_STRATEGIES_S1_S5.md`.
- Preserve H1–H8 as immutable, versioned baselines. H3/H7 remain NOT_EVALUABLE until genuine historical catalysts exist; H8 stays default disabled, H4/H6 research-only pending new evidence.
- Every new strategy must implement typed spec, feature provenance/missingness, trigger, evidence and disconfirming evidence, null-veto and version, common execution interface and test parity. No duplicate shadow code.

### Milestone M5 — Multiple execution policies and economic selection
- Cross five strategies with a **pre-registered limited** entry/exit experiment (don't test all combinations blindly), quantify commissions, spread, fill bias, gaps, beta/sector exposure, slot limits and concentration.
- Separate Opportunity Ranking, Conditional Outcome Model and Net Expected Utility Decision Engine. Drop/mark unvalidated the current arbitrary Quality rank as a chance-of-success proxy; test ranking versus realized net R and exposure-matched simple baseline.
- Net reward/risk, stop vs thesis invalidation, probability of no fill, exposure budget and max acceptable loss must be explicit. NO TRADE is always allowed.

### Milestone M6 — Independent evaluation and prospective shadow mode
- Use genuinely new OOS data, properly held-out entities and/or prospective shadow; never simply change random seed on used holdout and call it new evidence. Run proof that synthetic permutation destroys signal. Record all variants and all failed runs.
- Economically validated rule and calibrated probability predictor have SEPARATE gates. Do not require a fancy model to publish a research statistic, but don't show calibrated p without actually validated calibration.
- Produce new `research-results/real-equities/<run-id>/` manifest, quality, metrics, variants, uncertainty, controls, portfolio and explicit keep/revise/retire statuses, reviewed PR, updated index. Source data/license constraints intact.

### Milestone M7 — Product integration, safety and operational handoff
- Update admin research lab/status/Discord payload to show source, horizon, model/strategy version, uncertainty truth, profile, old H1–H8/experimental S1–S5 and actual economics; dangerous controls gated.
- Demonstrate member A/B private positions, Pi cloud model retrieval and rollback, GitHub hosted experiment, no laptop, reasonable quota.
- Produce `docs/quant-v2/RELEASE_DECISION.md`: critical defects, tests, actual CI links, data rights/owner configuration and precise release profile. Never bypass group-actionable legal/data review.

## 2. Interface contracts & minimal implementation policy

Use `apps/pi-worker/src/apex_worker/research/realeq/` for study orchestration, `research/execution.py` for simulator, `signals/levels.py`, `signals/strategies/library.py`, `features/panel.py`, `research/dataset.py`, `research/validation.py`, `research/evaluate.py`, `research/metrics.py`, `.github/workflows/apex-real-equities-research.yml` and relevant `apps/web` routes. Verify path names first. Shared feature/level policy in research and live; never create separate untested “V2” calculations that drift from the Pi.

For source refactor use small typed interfaces: `MarketObservation`, `FeatureSnapshot`, `CandidateEvent`, `ExecutionPolicy`, `TradePathAmbiguity`, `Costs`, `ModelVersion` and `ResearchResult`. Include as-of/available-at feed stamps and default safe missingness. Changes must be paired with migrations, cross-language contract tests, reset-free compatibility, feature flags and rollback.

## 3. Limitations and constraints

- Only standard GitHub-hosted jobs within remaining **account-wide** free minutes, not always-on cloud research; Pi stays long-lived scanner/bot. Consider static safe job `timeout-minutes`, no excessive 500x parameter sweeps and provider API cap.
- Research SIP historical data older than API entitlement delay; never republish raw vendor OHLCV/trades in public repo/Discord or assume derived redistribution is allowed.
- Vercel Hobby eligibility and personalized advice to group require terms/regulatory review. No broker execution or Robinhood credential harvesting.
- No all-day laptop requirement, no routine owner terminal commands, no forced paid training platform.
- Hindsight insight does not equal evidence; no AI agent cherry-picking.

## 4. Progress report schema (every milestone)

`M# | source SHA | changed paths | confirmed bugs with test | changes implemented | tests actual command/result | model experiment protocol/hash and Actions URL (if any) | economic takeaway | no-trade/live mode | minutes/API/storage budget | unresolved gate | next PR`.

**Acceptance:** agent produces ACTUAL coded changes and tests in follow-up PRs, but no new live stock advice unless all release and data/authorization gates have been independently satisfied.