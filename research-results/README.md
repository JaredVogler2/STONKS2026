# APEX Empirical Research Results — Central Register

**Current status (2026-10-22):** two real-equity experiments are completed, and both are **NO_TRADE**:
- protocol-1 r2;
- the Quant V2 protocol-2 study `qv2-20261022-s1-s5-u1-presample`. In it, S1 and S3 are promising but not significant, and S2 and S5 failed with power.

No hypothesis is validated. Do not treat synthetic smoke tests or market-data retrieval as validated backtest performance.

The coding agent's execution contract is [Phase 14 — Real Equity Validation](../build-prompts/14_REAL_EQUITIES_VALIDATION_EXECUTION.md).

- Frozen experiment protocol: [real-equities/PROTOCOL.md](real-equities/PROTOCOL.md) (`realeq-protocol-1`).
- Execution status and blockers: [real-equities/EXECUTION_STATUS.md](real-equities/EXECUTION_STATUS.md).
- Runner: `apex research real-equities`. Workflow: `.github/workflows/apex-real-equities-research.yml` (manual, `main` only, research only).

## Publication rules

- Each genuine experiment belongs in `real-equities/<experiment_id>/`, with `manifest.json`, `data_quality.json`, `strategy_metrics.json`, `comparisons.md`, `recommendations.md` and `execution_log.md`.
- The manifest must identify the source, SIP/IEX feed, adjusted-price policy, universe, sample/holdout dates, exact strategy versions, code SHA and dataset hash.
- Backtests must use true historical market data with immutable chronology, explicit limitations, conservative execution and benchmark comparison.
- Every H1–H8 hypothesis has a recorded result, including NOT_EVALUABLE / NO_EVENTS when required inputs are unavailable.
- Synthetic research remains documented separately in [Testing and Methodology](../docs/TESTING_AND_METHODOLOGY_REPORT.md). Never blend it with real results.
- Do not commit market-data vendor raw OHLCV, secrets, source-restricted redistribution, or user positions.
- Add the completed experiment link to this index **only after a real GitHub Actions run and reviewer-checked summary**.

## Real experiments

| Experiment | Run | Code | Data | Result |
|---|---|---|---|---|
| [`2026-10-21-fixed78-sip-8y-r2`](real-equities/2026-10-21-fixed78-sip-8y-r2/) ([reviewer notes](real-equities/2026-10-21-fixed78-sip-8y-r2/REVIEW.md), [erratum](real-equities/2026-10-21-fixed78-sip-8y-r2/ERRATUM.md)) | [39696547618](https://github.com/JaredVogler2/STONKS2026/actions/runs/39696547618), 2026-10-21 | `2acad29`, `realeq-protocol-1` | Alpaca SIP daily, split-adjusted, fixed78, 2018-10-22 → 2026-10-21; dataset `f428e39e…` | **NO_TRADE**. H8 FAILED_REAL_OOS; H5 UNVALIDATED; H1/H2/H4/H6 INSUFFICIENT_HISTORY; H3/H7 NOT_EVALUABLE_MISSING_INPUT. **Erratum 2026-10-22:** p-values are descriptive, not calibrated; dataset hash not bitwise reproducible (metrics are); NO_TRADE stands |

| [`qv2-20261022-s1-s5-u1-presample`](real-equities/qv2-20261022-s1-s5-u1-presample/) ([reviewer notes](real-equities/qv2-20261022-s1-s5-u1-presample/REVIEW.md), [decision report](real-equities/qv2-20261022-s1-s5-u1-presample/QUANT_V2_DECISION_REPORT.md)) | [39849016753](https://github.com/JaredVogler2/STONKS2026/actions/runs/39849016753), 2026-10-22 | `eda340a`, `realeq-protocol-2` A1 (sha256 `33bd4e99…475d`) | Alpaca SIP daily, split-adjusted, U1 S&P 500 (survivorship-limited), PRESAMPLE 2017-01-03 → 2019-09-20 (backward-time; exposed by the cancelled A0 run, unread); dataset `0339c815…` | **NO_TRADE.** S1 and S3 RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD (paired +0.33 / +0.77 pp per signal; Holm 0.23 / 0.27). S2 and S5 FAILED_WITH_POWER. S4, H3 and H7 NOT_EVALUABLE. Forward shadow recommended |

Attempted runs, including blocked ones, are listed in [real-equities/EXECUTION_STATUS.md](real-equities/EXECUTION_STATUS.md).

For independent scrutiny, [real-equities/ADVERSARIAL_REVIEW_BRIEF.md](real-equities/ADVERSARIAL_REVIEW_BRIEF.md) gathers every strategy result, test and known weakness in one place, for an adversarial reviewer. Its tables are generated from the committed files.

## Pending run

- **Historical:** none. The protocol-2 PRESAMPLE look is used, and the window is exposed, so it is never re-evaluated.
- **Next:** the registered forward shadow (decisions from 2026-10-23; [plan](../docs/quant-v2/FORWARD_SHADOW_PLAN.md)). Any new historical experiment needs a new pre-registered protocol version.
