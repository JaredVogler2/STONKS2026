# APEX V2 — Evidence Register and Reproduction Queue

**Snapshot:** STONKS2026 PR #1 source, SHA `75679cbc5dfbf51bddc72487648a22e3bcbcd092`. This review inspected selected source files and the PR description. It did NOT run real-market pipelines or independent integration tests. Recheck latest SHA after agent updates.

Finding statuses: **Observed in source** = expression exists, downstream issue not yet reproduced. **Reported** = agent says it; unverified by reviewer. **Hypothesis** = test proposal. **Confirmed defect** requires a failing test/log. Every finding must retain source SHA, exact reproduction and closure evidence.

## APEX-001 — Live vs trained feature mismatch
**Priority:** P0 if used for published calibrated probability. **Observed in source.**

`apps/pi-worker/src/apex_worker/research/dataset.py`: `MODEL_FEATURES` contains `squeeze_prev`, while `_row_features` uses `row.get("squeeze_prev")`. In `apps/pi-worker/src/apex_worker/signals/policy.py`, `model_row` returns `"squeeze_prev": None` unconditionally. Model code imputes missing predictors to training mean.

**Consequence:** a live candidate may be evaluated on an imputed predictor while research used actual prior compression, changing its forecast and calibration. Do not infer magnitude without reproducing with a trained artifact.

**Reproduction:** build identical candidate/time in backtest and inference; compare full ordered feature vector, raw and calibrated prediction, with a strong non-average prior squeeze. Add train/live vector parity test for all columns, source and as-of timestamps, not just this one. **Fix direction:** canonical versioned feature-vector assembler shared by both paths, explicit critical missingness veto and compatible manifest schema. **Gate:** no live confidence if feature parity is unproven.

## APEX-002 — Per-alert probability interval may be statistically unsupported
**Priority:** P1 / P0 if advertised as accurate interval. **Observed in source.**

`signals/policy.py` `confidence_block` sets `se = sqrt(p*(1-p)/max(n,1))` and creates z-based 80/95% bands based on total holdout count. This treats a fitted per-stock prediction like an iid Bernoulli sample proportion and does not represent parameter, data, calibration and regime uncertainty.

**Reproduction:** use equal p and n across two sets of varying within-week/sector correlations, bootstrap temporal blocks and compare intervals. **Fix:** rigorously label estimate or replace with empirically tested block-bootstrap/held-out calibration interval with coverage metrics and effective samples. Until then display point estimate and “uncertainty band unavailable.” No false precision.

## APEX-003 — H7 implemented, missing from synthetic outcome report
**Priority:** P1 diagnostic. **Observed code + PR report.**

`signals/strategies/library.py` contains `CatalystCompression` with `h7_catalyst_compression`, `requires_catalysts=True`. `research/dataset.py` skips event-required strategies when a symbol has no matching catalyst. PR summary reports H1–H6 and H8 but no H7.

**Interpretation:** H7 is NOT missing from implementation. It may have zero applicable catalyst events in synthetic fixtures or insufficient qualified rows. **Verify counts:** enabled, had catalyst, had valid as-of event, triggered, passed universe, filled, train/holdout sufficient, report state. Add a synthetic event time-available positive catalyst test. H7 vs H1-only and catalyst-only ablation on real source later. Every registered strategy reports a row, even NO_EVENTS.

## APEX-004 — Walk-forward purge uses business weekdays instead of NYSE sessions
**Priority:** P1. **Observed source.**

`research/validation.py` uses `pd.tseries.offsets.BDay(embargo)` in `_purge`. The label horizon is explicitly trading sessions, and US holidays differ from weekdays. Test Thanksgiving, Good Friday, Juneteenth, market closures and 20-session overlapping outcomes. Use exchange-calendar session ordinal and exact event exit times to ensure no label overlap. Avoid adjusting embargo merely to pass tests.

## APEX-005 — Historical universe survivorship limitations
**Priority:** P1 evidence caveat. **Acknowledged in code.**

`market/universe.py` explicitly warns the current eligible symbols list misses historically delisted names. This can bias results. Require point-in-time historical membership or scoped caveated research and a quantitative sensitivity bound. Do not fabricate historical membership.

## APEX-006 — Research does not yet demonstrate edge on actual equities
**Priority:** P0 for high-confidence real alerts, not a coding defect. **PR-reported, unverified.**

PR #1 describes H1–H6, H8 as UNVALIDATED on deterministic synthetic data. H1 holdout Brier skill 0.032, ECE 0.016 but p10 bootstrapped **mean** net return <=0 at 25 bps per side. 361 Python tests + 12 web integration tests reported. Planted-alpha redteam model is a DEMO champion, not evidence of real trading profitability.

**Gate:** genuine point-in-time historical data, untouched chronological holdout, sensible baselines, multiple-testing control, conservative fill model and forward paper validation. Do not tune the synthetic dataset or thresholds to fabricate success.

## APEX-007 — Cross-service operations incomplete at review time
**Priority:** P1 with P0 security checks. **PR-reported.**

PR says Discord bot and dashboard in progress, Pi/Vercel operations, final QA pending; GitHub training workflows and Pi autonomous sync pending. An open PR, 290 changed files and reported tests do not mean deployed, connected or operational.

**Gate:** actual Discord/private modal, migrated Pi service and reboot, protected Vercel production and preview, cloud training, signed registry fetch, observed failure recovery.

## APEX-008 — Correlation and uncertainty dependence
**Priority:** P1 research. **Observed method / hypothesis.**

`research/metrics.py` bootstraps by decision week; `effective_sample_size` is a rough span/window estimate. Tests should compare tightly correlated sector/date samples to independent stock samples and assess interval robustness. Include correlation clustering and embargo sensitivity, no made-up precise ESS.

## APEX-009 — Free feeds, broker eligibility and prices are not guaranteed
**Priority:** P0 before public display, known constraint. **Observed in source.**

`market/universe.py` uses likely eligible exchange metadata; that is not definitive Robinhood tradability. IEX live quotations are not consolidated NBBO or total market volume. Display feed name, quote timestamp, age, venue/source and eligibility confidence. Quote retransmission to a private group needs provider-rights review.

## APEX-010 — Discord multiple-user privacy and state invariants
**Priority:** P0 if failure reproduced. **Design review hypothesis.**

Ephemeral command replies are private; ordinary Discord channel replies are public, DMs can be disabled. Audit cross-user membership, lot ownership, signed component identity, blocked DM, offline queue and dedup. Exercise A/B same-symbol different entry/quantity positions; never emit someone else's P&L to shared channel.

## Review discipline

- For each finding, append **commit SHA, exact line, test case, expected/actual, severity decision, implementation PR, observed CI result, closed date**.
- Do NOT report H7 absent, model profitable, GitHub tests independently passed, or production currently live without evidence.
- Distinguish source-code concerns from confirmed real incidents. Withdraw finding if already fixed during PR #1.
