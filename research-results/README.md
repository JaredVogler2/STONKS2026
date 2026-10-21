# APEX Empirical Research Results — Central Register

**Current status (2026-10-21):** Awaiting first complete REAL-equity strategy experiment. Do not treat synthetic smoke tests or market-data retrieval as validated backtest performance.

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

_None completed yet._

## Pending run

- [Issue #6 — execute the real-equities research](https://github.com/JaredVogler2/STONKS2026/issues/6). The pipeline, workflow and frozen protocol are implemented and tested offline. The first real run waits for (1) the workflow to reach `main` and (2) the owner's `real-equities-research` Alpaca secrets. Without those secrets it reports BLOCKED — ALPACA_ACTIONS_SECRETS_MISSING. Real-data backtest results are **not yet produced**.
