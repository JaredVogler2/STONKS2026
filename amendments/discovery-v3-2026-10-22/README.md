# APEX Discovery V3 — Portfolio-First Strategy Invention and Prospective Validation

**Owner mandate:** Discover the strongest **repeatable, realizable, risk-constrained economic performance** available for a 1–4-week swing-oriented account, without assuming that any previously published indicator, chart pattern, machine-learning algorithm, exit policy, asset universe or holding horizon is optimal. Expand the design space when empirical evidence warrants. Novel testable strategies and combinations are encouraged. **No strategy is currently validated for live trading.**

**Status:** Documentation-only development brief created 2026-10-22 on independent branch. Not a code change, deployed system, promise of profit, or authorization for group actionable trading. Do not overwrite earlier real-equity research results.

## Verified starting point

Repository: `JaredVogler2/STONKS2026`; `main` reviewed at `cd135bde7c61a72731b30f2063ccacb317bc93d3` (refresh before implementation). Quant V2 real-data experiment `qv2-20261022-s1-s5-u1-presample` was published and PR #16 merged. [Decision report](../../research-results/real-equities/qv2-20261022-s1-s5-u1-presample/QUANT_V2_DECISION_REPORT.md). [Real GitHub Actions run 39849016753](https://github.com/JaredVogler2/STONKS2026/actions/runs/39849016753).

U1 = current S&P 500 membership snapshot, historically membership-gated but **survivorship limited**; 495 usable stock histories; 1,334,342 daily SIP bars across 507 usable symbols including ETFs and SPY; 143 provider requests; ~6 minutes runner study. Real presample 2017-01-03 → 2019-09-20 (earlier than design window, and exposed in cancelled A0), not a pristine prospective holdout. Multiple-testing adjusted: NO_TRADE. S1 controlled leader pullback +0.325 percentage points vs matched-random per signal, Holm p=0.230, strategy book +6.2% vs exposure-matched SPY/cash +12.7%. S3 breakout retest +0.767 pp paired, Holm p=0.266, n≈36, book +1.8% with ~6% exposure. S2 failed with power; S5 sector-ETF allocation failed vs SPY/cash even pre-cost; H4/H8 failed; H3/H7/S4 missing credible point-in-time event source. The historical swing-stop/20-session trading wrapper damages economic results even for matched random stocks. S1/S3 are research signals, NOT approved live edges.

**Unresolved critical difference:** APEX can currently score technical setups and retrospectively measure per-trade economics. That does NOT mean it can independently invent, prioritize, and validate alternative decision policies which allocate finite capital over time. Current forward shadow is a PLAN only (`docs/quant-v2/FORWARD_SHADOW_PLAN.md`). The first job must make that real and time-stamped starting the next possible session, with no past backfill labeled prospective.

## Reader guide (all implementation requirements)

1. [00_MASTER_AGENT_MISSION.md](00_MASTER_AGENT_MISSION.md) — exact Claude Code mission, work sequence, non-goals and stop rules.
2. [01_BASELINE_AND_FAILURE_ANALYSIS.md](01_BASELINE_AND_FAILURE_ANALYSIS.md) — latest code/results, adversarial engineering targets and research assumptions.
3. [02_FORWARD_SHADOW_FIRST.md](02_FORWARD_SHADOW_FIRST.md) — **start prospective evidence collection immediately**; immutable, versioned future records.
4. [03_DISCOVERY_ENGINE_ARCHITECTURE.md](03_DISCOVERY_ENGINE_ARCHITECTURE.md) — pluggable, typed hypothesis generator/evaluator with exposure-aware economic scoring.
5. [04_HYPOTHESIS_LAB_AND_NOVEL_RESEARCH.md](04_HYPOTHESIS_LAB_AND_NOVEL_RESEARCH.md) — broad, novel candidate families and mechanism-driven experiment blueprints.
6. [05_PORTFOLIO_FIRST_OBJECTIVES.md](05_PORTFOLIO_FIRST_OBJECTIVES.md) — efficient frontier, active economic value, market-hedged and opportunity-cost accounting.
7. [06_CAUSAL_DATA_AND_MARKET_MICROSTRUCTURE.md](06_CAUSAL_DATA_AND_MARKET_MICROSTRUCTURE.md) — PIT universe/catalysts/fill analysis, data-provider legal/free-tier limits.
8. [07_STATISTICAL_DISCOVERY_AND_ANTI_OVERFIT.md](07_STATISTICAL_DISCOVERY_AND_ANTI_OVERFIT.md) — experiment registration, sequential budget, dependent inference, true forward/untouched evidence.
9. [08_CLOUD_ORCHESTRATION_AND_AUTONOMY.md](08_CLOUD_ORCHESTRATION_AND_AUTONOMY.md) — bounded GitHub cloud jobs, Pi continuity, signed release only after gates, $0-overage protections.
10. [09_PRODUCT_RESEARCH_LAB_AND_DISCORD.md](09_PRODUCT_RESEARCH_LAB_AND_DISCORD.md) — understandable candidate funnel/uncertainty, owner review and privacy.
11. [10_EXECUTABLE_ACCEPTANCE_GATES.md](10_EXECUTABLE_ACCEPTANCE_GATES.md) — full test matrix, deliverable list, scientific release profiles.
12. [11_COPY_PASTE_CLAUDE_CODE.md](11_COPY_PASTE_CLAUDE_CODE.md) — exact first-message Claude Code assignment and definition of done.

## Non-negotiable operating principles

- **Optimize out-of-sample portfolio-level net utility**, not backtest win rate, gross returns, trained-model AUC, Brier alone, number of indicators, or one selected winning strategy. The definition of “best” includes uncertainty, actual slippage, downside/tail risk, max capital usage, liquidity, turnover, missed alternatives and implementation cost. Return a Pareto frontier and honest owner trade-offs; do not claim a single universal mathematical optimum.
- **Novel means falsifiable**, not arbitrary complexity: hypotheses have mechanism, causal observables, feasible execution, risk and distinct baselines. Evidence decides; do not mandate adoption of existing S/H methods or an AI model.
- **Search is a multiple-testing problem.** Track all failures, trials, data peeks, model families and hyperparameters. The now-inspected 2017–2019 and 2019–2026 history is discovery/development material for new hypotheses, not fresh confirmatory evidence. Never rebrand symbol holdouts within the same market dates as independent future time.
- **Prospective evidence starts now.** Create immutable daily decision-time records before outcomes mature; shadow V2 frozen S1/S3 alongside research-only challengers in SEPARATE namespaces. No reconstructed past rows masquerading as prospective.
- **Abstention is a success case.** If nothing robustly beats appropriate cash/ETF/hedged alternatives, APEX outputs NO TRADE and no model promotion.
- **No live orders, Robinhood automation, user-account scraping, unauthorized group recommendations, paid services, proprietary market-data redistribution or routine owner laptop execution.** Signatures, auth, Alpaca entitlements, Vercel terms and member privacy remain explicit independent gates.

## Owner-facing outcome expected

Claude Code should produce functioning, reviewable incremental code PRs (not merely another 150-page plan), an operational decision-recording pipeline and a demonstrably reproducible mechanism-led hypothesis search workflow. Report actual tests, source SHA, cloud run IDs, scientific limitations, budget, strategy dispositions and one next measured bottleneck. A completed V3 implementation does NOT imply a validated profitable strategy.