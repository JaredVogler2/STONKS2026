# APEX Empirical Research Results — Central Register

**Current status (2026-10-23):** four real-data research batches are completed, and all are **NO_TRADE**:
- protocol-1 r2;
- the Quant V2 protocol-2 study `qv2-20261022-s1-s5-u1-presample`. In it, S1 and S3 are promising but not significant, and S2 and S5 failed with power;
- the Discovery V3 DEV pilot `v3-pilot-1-2026-10-23`. All 10 portfolio-level trials trail their investable alternative net of costs (`REJECTED_DEV`), and nothing is nominated.
- the Discovery V3 DEV pilot `v3-pilot-2-2026-10-23`, run at the measured costs. 5 of 7 trials are `REJECTED_DEV`. Quarterly buffered momentum shows +8.6 pp a year against SPY but is `INCONCLUSIVE_DEV` (Holm 0.62), risk-loaded and survivorship-exposed. Nothing is nominated.

The frozen forward shadow (`qv2a1-frozen-v1`) records prospective decisions from 2026-10-23.

The first **measured** execution cost (`cost-study-1-2026-10-23`) is about 10.5 bps per side for open-entry and close-exit marketable orders, against the 25 bps assumed by every study above. It is a measurement and changes no published disposition.

The design study `design-study-1-2026-10-23` measured how fast the evidence tournament's tilts could be proven. Sector-, beta- and size-neutral versions have about 42% less tracking error against their equal-weight twin, so at the same alpha their evidence arrives about 3× faster. It is a measurement of risk and changes no published disposition.

No hypothesis is validated. Do not treat synthetic smoke tests or market-data retrieval as validated backtest performance.

**Method change proposed (2026-10-23):** [docs/evidence/EVIDENCE_TOURNAMENT.md](../docs/evidence/EVIDENCE_TOURNAMENT.md).
- **Why every batch ended NO_TRADE:** [codebase review](../docs/evidence/CODEBASE_REVIEW.md).
- **The finding:** the strongest assumption-free, anytime-valid evidence that a candidate beats SPY equals the relative wealth of an unlevered paper portfolio. Evidence is money.
- **Consequence:** even G2's DEV estimate, taken at face value, would need about 35 years of prospective data.
- **The proposal:** a prospective evidence tournament. Backtests only set priors, paper capital follows earned evidence, and claims stay valid under continuous monitoring. Candidates are designed for information rate.
- **Status:** an engine with a synthetic battery ([results](../docs/evidence/EPA_BATTERY.md)), and the prospective lane `v3x-epa-tournament-v1` ([registration](../docs/evidence/TOURNAMENT_LANE.md); paper, `PROSPECTIVE_EXPLORATORY`). The lane records only once the owner activates it; until then its runs are dry rehearsals.

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
- **Next:** the registered forward shadow (decisions from 2026-10-23; [plan](../docs/quant-v2/FORWARD_SHADOW_PLAN.md), [runbook](../docs/discovery-v3/FORWARD_SHADOW.md)). Any new historical experiment needs a new pre-registered protocol version.
- **Discovery V3:** DEV experiments on contaminated history (diagnostics only) go under `discovery-v3/<experiment_id>/`; every trial, losers included, is listed in [discovery-v3/TRIAL_INDEX.md](discovery-v3/TRIAL_INDEX.md).

## Discovery V3 batches (DEV diagnostics on contaminated history; never validation)

| Batch | Run | Code | Data | Result |
|---|---|---|---|---|
| [`v3-pilot-1-2026-10-23`](discovery-v3/v3-pilot-1-2026-10-23/) ([review](discovery-v3/v3-pilot-1-2026-10-23/REVIEW.md), [decision report](discovery-v3/v3-pilot-1-2026-10-23/DECISION_REPORT.md)) | [39884381394](https://github.com/JaredVogler2/STONKS2026/actions/runs/39884381394), 2026-10-23 | `dddf352`, plan `v3-pilot-1` (`11d0af37…`), `obj-v3-1` | Alpaca SIP daily, split-adjusted, U1 survivors; DEV 2019-10-23 → 2026-09-18; dataset `0339c815…` (the protocol-2 run's) | **NO_TRADE.** 10/10 REJECTED_DEV against SPY or exposure-matched SPY/cash, net 25 bps per side. Best primaries: F2 dispersion-gated −1.4 pp/yr, F5 momentum −1.9 pp/yr, F9 regime abstention −2.5 pp/yr (with a −23% drawdown against SPY's −34%). Friction is binding: F2 and F5 beat SPY only at zero cost |
| [`cost-study-1-2026-10-23`](discovery-v3/cost-study-1-2026-10-23/) ([review](discovery-v3/cost-study-1-2026-10-23/REVIEW.md); a **measurement**, not a trial) | [39908161943](https://github.com/JaredVogler2/STONKS2026/actions/runs/39908161943), 2026-10-23 | `43cffcb`, plan `cost-study-1` (`94477312…`) | Alpaca historical SIP NBBO quotes and one-minute bars; 210 seeded entry sessions from U1 on DEV dates | **Measured execution cost.** Marketable entry at open + 1 min plus exit at close − 1 min: about **10.5 bps per side** on average (95% CI 7.3–13.4) against the assumed 25. The entry leg is about 20 bps and the exit about 0.4. Near-open VWAP averages the open (−0.2 bps). The registered p90 rule (36 bps) is dominated by price noise and is not adopted |
| [`v3-pilot-2-2026-10-23`](discovery-v3/v3-pilot-2-2026-10-23/) ([review](discovery-v3/v3-pilot-2-2026-10-23/REVIEW.md), [decision report](discovery-v3/v3-pilot-2-2026-10-23/DECISION_REPORT.md)) | [39921190552](https://github.com/JaredVogler2/STONKS2026/actions/runs/39921190552), 2026-10-23 | `ed8c8e5`, plan `v3-pilot-2` (`5a2cc40c…`), `obj-v3-2`, measured `cost-v3-2` | Same dataset and DEV window, contaminated twice | **NO_TRADE.** 5 of 7 REJECTED_DEV. Quarterly buffered momentum: +8.6 pp/yr vs SPY, but INCONCLUSIVE_DEV (Holm 0.62), beta 1.2 and 35% volatility (risk-adjusted below SPY), and survivorship-exposed. Nothing nominated |
| [`design-study-1-2026-10-23`](discovery-v3/design-study-1-2026-10-23/) ([review](discovery-v3/design-study-1-2026-10-23/REVIEW.md); a **measurement**, not a trial) | [39999238748](https://github.com/JaredVogler2/STONKS2026/actions/runs/39999238748), 2026-10-23 | `7c87dc6`, plan `design-study-1` (`d5fef5eb…`) | Same dataset; 1,734 DEV sessions, 2019-10-24 → 2026-09-18 | **Information rate of tilt designs against their equal-weight twin.** Tracking error at equal dispersion: cohort 0's tilts 2.26–2.45%, sector-neutral 1.70–1.86%, sector-, beta- and size-neutral 1.26–1.45% (about −42%, so about 3× the evidence rate at equal alpha). Worst sessions shrink, for example momentum −1.27% → −0.88%. A claim at 800 within 10 years needs a gross alpha against the twin of about 1.9–2.1% a year for the neutral signal tilts (about 3.0–3.5% for cohort 0's) and 0.9% for `lowvol-full`. Cohort 0's declared loss bounds held on every DEV session. DEV means are contaminated and select nothing |
