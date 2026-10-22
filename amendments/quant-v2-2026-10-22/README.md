# APEX Quant V2 — Evidence-Driven Remediation, Strategy Replacement & Product Upgrade

**Created:** October 22, 2026. **Repository:** `JaredVogler2/STONKS2026`. **Source reviewed:** `main` @ `8b3e35eea74519c663e50fa77dc0a458463f0e5a` (refresh before work).  
**Status:** PROPOSED CODING-AGENT AMENDMENT. This folder contains specifications and tests, NOT implemented fixes, validated profitable strategies, or deployment evidence.  
**Change strategy:** Dedicated planning branch and reviewable PR. Preserve immutable first real-data experiment. All implementation must be subsequent focused code PRs.

## Purpose

The user wants APEX to discover statistically and economically credible **5–20-trading-session long swing opportunities** with useful Discord notifications and private per-member position tracking, operating on Raspberry Pi + private Vercel + Supabase + cloud GitHub Actions, **without recurring laptop training** or obligatory paid subscriptions. Our first genuine eight-year fixed78 SIP-equity backtest established **NO TRADE / 0 validated strategies**; that result is important evidence of rigor, not permission to fit new rules to the used holdout. We now need to audit research correctness, expand representative history, redesign entries/exits/selection, rigorously evaluate replacement ideas and make the app accurately communicate the actual evidence.

### Authoritative baseline (DO NOT ALTER)

- **Run:** [39696547618](https://github.com/JaredVogler2/STONKS2026/actions/runs/39696547618).
- **Experiment:** [2026-10-21-fixed78-sip-8y-r2](../../research-results/real-equities/2026-10-21-fixed78-sip-8y-r2/).
- **Source:** Alpaca historical SIP, split-adjusted, 156,773 price bars, 78/78 symbols usable (66 stocks; 11 sector ETFs + SPY references).
- **Windows:** 2019-10-23 to 2025-05-01 development; 2025-05-02 to 2026-09-21 holdout.
- **Configuration:** 20 NYSE sessions; 25 bps per side; six economically evaluated rule families H1,H2,H4,H5,H6,H8. H3/H7 not evaluable due to missing point-in-time catalysts.
- **Results:** H1 −0.144% (n16), H2 −0.037% (n39), H4 −1.095% (n7), H5 +0.034% (n225), H6 −2.092% (n11), H8 +0.129% (n140, underperforms matched random); simple momentum_20 +1.189% (n30; uncertainty includes losses). All means after primary costs. H5 and H8 forecasting Brier skill negative. No validated edge; group LIVE and auto promotion remain OFF.
- **Potential source issue:** `apps/pi-worker/src/apex_worker/signals/levels.py` calls `ROUND_FLOOR` but import list only includes `ROUND_HALF_UP, Decimal`; reproduce and explain historical successful run before trusting new work.
- **Sources:** `research-results/real-equities/ADVERSARIAL_REVIEW_BRIEF.md`, `research-results/real-equities/PROTOCOL.md`, `docs/TESTING_AND_METHODOLOGY_REPORT.md`.

## Read order: comprehensive implementation prompts

1. [00_MASTER_AGENT_EXECUTION.md](00_MASTER_AGENT_EXECUTION.md) — master handoff, incremental execution schedule and PR discipline.
2. [01_EVIDENCE_AND_ADVERSARIAL_FINDINGS.md](01_EVIDENCE_AND_ADVERSARIAL_FINDINGS.md) — source-anchored failures/uncertainties; correct evidence classifications.
3. [02_P0_RESEARCH_CORRECTNESS.md](02_P0_RESEARCH_CORRECTNESS.md) — failure reproduction, trade simulator and statistical defects; CI gates.
4. [03_DATA_AND_UNIVERSE_EXPANSION.md](03_DATA_AND_UNIVERSE_EXPANSION.md) — larger point-in-time-aware universe and corporate action handling.
5. [04_PREREGISTRATION_AND_STATISTICS.md](04_PREREGISTRATION_AND_STATISTICS.md) — frozen experimental design, block dependence, inference and power.
6. [05_REPLACEMENT_STRATEGIES_S1_S5.md](05_REPLACEMENT_STRATEGIES_S1_S5.md) — five independently falsifiable strategy replacements with parameters and acceptance.
7. [06_ENTRIES_EXITS_COST_AND_PORTFOLIO.md](06_ENTRIES_EXITS_COST_AND_PORTFOLIO.md) — alternative entry/stop/target mechanics, partial fills, gap costs, book-level economics.
8. [07_RANKING_MODEL_AND_DECISION_ENGINE.md](07_RANKING_MODEL_AND_DECISION_ENGINE.md) — separate ranking, calibrated competing risks and net expected utility.
9. [08_EVENT_CATALYST_ENGINE_H3_H7.md](08_EVENT_CATALYST_ENGINE_H3_H7.md) — first-publication-time event records and honest unavailable strategy states.
10. [09_APP_DISCORD_UX_INTEGRATION.md](09_APP_DISCORD_UX_INTEGRATION.md) — research version/mode, cards, audit traces, group and privacy gates.
11. [10_GITHUB_ACTIONS_PI_AND_COST.md](10_GITHUB_ACTIONS_PI_AND_COST.md) — cloud-only unattended experiment runner, model release and quotas.
12. [11_TEST_MATRIX_AND_RELEASE_GATES.md](11_TEST_MATRIX_AND_RELEASE_GATES.md) — source-level tests, performance, negative controls and release gate.
13. [12_EXPERIMENT_TEMPLATE_AND_CHANGELOG.md](12_EXPERIMENT_TEMPLATE_AND_CHANGELOG.md) — experiment schema, falsification log, statuses and publication.
14. [13_COPY_READY_AGENT_HANDOFF.md](13_COPY_READY_AGENT_HANDOFF.md) — concise paste-ready assignment including sequential milestones.

## Governance and priorities

- **P0 integrity:** Inability to reproduce published experiment, train/live mismatch, broken math/runtime, leakage, invalid fills, invalid p-value claims, fake confidence, broken authentication or market-data rights. Stop high-confidence release until evidence-based fix; do not overwrite historical report.
- **P1 quant:** New point-in-time data, causal execution, fair controls, defensible inference, genuine economic benefit; first focus after P0.
- **P2 product:** quality/calibration UX and reporting, secure Discord member experience, robust operator controls.
- **IDEA:** novel alpha without OOS evidence. Never equate ranking intuition with a validated strategy.

**Frozen holdout warning:** The first holdout is now contaminated for *new* strategy choice because its outcomes informed S1–S5 and mechanics recommendations. The same dates may be used for openly labeled development/diagnostic sensitivity only. Use a distinct new final evaluation period, source or independently held-out names with clear dependence caveat; favor prospective paper/live shadow data for final confidence. A sector/name split alone is NOT an untouched time holdout because the same market shocks are shared.

**No backtest victory by brute force:** A result generated by searching hundreds of variants then cherry-picking is not a discovery; show all experiments and adjustments.

## Expected agent deliverables

- Multiple small implementation PRs after this **documentation-only PR**, each with exact issue/finding IDs, before/after tests, reproducible GitHub-run URL, code + protocol SHA, research-result path and rollback.
- Immutable original report; next manifest named `realeq-protocol-2` with fresh hash, false-positive/power assessment and all version changes.
- No compulsory laptop training; no raw vendor bars, API keys or member fills in repo/artifacts; no order placement.
- If results remain negative or underpowered: explicitly report NO TRADE and do not enable HIGH CONVICTION, live-group personalized trade guidance or model promotion.
