# Evidence Dossier — Findings to Confirm, Falsify or Remediate

**Evidence type keys:** VERIFIED_SOURCE = source expression inspected (not necessarily reproduced runtime); REPORTED_RESULT = first real-market experiment published and reviewer-verified; HYPOTHESIS = concern that requires a test. Severity may escalate once reproduced; no invented production incidents.

## Historical data snapshot

Experiment `2026-10-21-fixed78-sip-8y-r2` @ code SHA `2acad29f75a81dbde0a469d1634d8b273de5c9ea`:
- 78 assets; 66 current surviving large-caps; 156,773 SIP daily OHLCV bars (2018–2026).
- H1 n16 −0.144%; H2 n39 −0.037%; H4 n7 −1.095%; H5 n225 +0.034%; H6 n11 −2.092%; H8 n140 +0.129% (below matched-random +0.648%); H3/H7 no point-in-time catalysts. 25bps per side and 20 NYSE sessions.
- H5 ρ(Quality,net R)=~0.002; H5/Brier skill −0.0126; H8 Brier −0.1183. Baseline 20-session momentum n30 +1.189% with bootstrap p10 −0.549%, not validated.
- Cost-sensitivity from 0bps to 50bps per side erases marginal gross edge in H1/H2/H5/H8. 20-session headline does not justify longer hold for every hypothesis.
- Only 15 decision weeks for H8, one regime; 348 holdout sessions are mostly trending_up.
- Historic HON suspected split/distribution and EXC spin-off distort price-only outcomes.

## Findings with exact code and review rules

### QV2-001 — Unbound decimal rounding constant, likely runtime error — P0 candidate / VERIFIED_SOURCE
`apps/pi-worker/src/apex_worker/signals/levels.py` imports `ROUND_HALF_UP` and `Decimal` from `decimal`, but `tick_round(price, down=True)` explicitly calls `ROUND_FLOOR` which is not imported or defined. `compute_levels` calls `tick_round(..., down=True)` for entry_min and stop. **Reproduce on clean import and actual source at both baseline and HEAD.** Use explicit `from decimal import ROUND_FLOOR` if reproducible, but investigate how the reported real run and Python tests completed before marking history trustworthy. Potential explanations requiring evidence: runtime instrumentation, artifact/source mismatch, lazy error path, or test coverage gap; DO NOT assert a cause. If successful historical run cannot be reconciled with its code SHA, set `RESEARCH_PROVENANCE_QUARANTINED` and repeat experiment after code correction under a new protocol/run ID. A passing CI job is NOT proof the import path was executed.

### QV2-002 — Noncausal fill-day high/low path — P0 before executable claims / VERIFIED_SOURCE
`research/execution.py:simulate` fills if open > zone and low ≤ entry_max, then checks fill-day low ≤ stop as if stop occurred later. Entire day's highs/lows feed MFE/MAE including prefill periods. Without intraday sequence, a low below stop **could have preceded** a later limit fill. Stop-first on post-entry OHLC is safe as a worst-case scenario, but cannot be claimed an observed trade. Conversely ignoring fill-day target may be pessimistic. Require event-path ambiguity flag and conservative/optimistic bounded analysis, intraday replay when possible, no invalid certainty.

### QV2-003 — Bootstrap-derived sign tail is described as “p-value” — P0 inference gate / VERIFIED_SOURCE
`research/realeq/analysis.py:trade_stats` calculates `p_one_sided = mean(bootstrapped_sample_means <= 0)`; `holm` later applies multiple-test correction. Percentile bootstrap tail probability is NOT automatically an adequately null-centered, calibrated frequentist hypothesis-test p-value; clustered 20-session overlapping returns exacerbate error. Implement and unit-test a justified paired null test / block bootstrap under null (centered/studentized or date-block randomization) and report definition, Type-I calibration, effective number of independent time clusters. Until repaired, the Holm “significant” release gate must NOT claim valid inference.

### QV2-004 — One-week blocks are shorter than overlapping 20-session labels — P1 / VERIFIED_SOURCE
`analysis.py:boot_means` resamples decision weeks, while positions last up to ~4 weeks and many stocks share sector shocks. Percentile bands can be too tight. Compare moving/stationary contiguous blocks at 4, 6, 8, 12 weeks and date/sector clusters, report effective groups and small-cluster suppression. Do not prescribe any one lag as universally sufficient.

### QV2-005 — Published FAILED label for H8 rests on too few independent dates — P1 / REPORTED_RESULT
H8 140 trades but only ~15 distinct decision weeks, one holdout regime; matched-random median comparison mixes sampling and opportunity selection, though signal n≥100. Under new protocol, require adequate **independent dates, months and regimes** before strong PASS or FAIL label. Preserve original frozen label and add an independent qualification in V2; don't retroactively relabel r2 as if computed earlier.

### QV2-006 — Backtest screen cost vs evaluation cost mismatch — P1 / VERIFIED_SOURCE
`signals/levels.py:LevelConfig.cost_bps_per_side` defaults 10, gate `min_net_rr=1.5`, while real-equities primary test uses 25 bps per side. A signal may pass 10bps economics and fail 25bps. Align versioned settings in research and live where appropriate; run controlled same-signal sensitivity and log rejection counts. Do NOT silently change the original baseline.

### QV2-007 — Baseline and strategy level geometry differ — P1 / VERIFIED_SOURCE
`research/realeq/analysis.py:BaselineFactory` uses generic stop hint close −1.5 ATR while strategy-specific hints vary; matched random is not universally exposure/sector/fill/cost matched. Build matched comparator using exact strategy entry/stop geometry on same decision dates, matched symbol liquidity/volatility/sector; also retain identical-geometry simple momentum/breakout comparisons. Separate stock-selection alpha from timing/exit alpha. Random reference must observe same capital constraints/dedup.

### QV2-008 — Current-universe survivorship and sector look-ahead — P1 / REPORTED_RESULT
`research/training_data.py` selects today’s survivors and historical sector classification; biased research cannot make broad claims about U.S. equities. A 300–1000-stock universe can improve power but worsens survivorship if constructed exclusively from today's names; membership must be PIT or explicit LIMITED label. Historic ticker delisting and mergers must be captured if licensed, otherwise quantify sensitivity.

### QV2-009 — Corporate actions and missing event history — P1 / REPORTED_RESULT
HON 2026-06-29 suspect discontinuity, EXC 2022-02-02 spin-off. Split-only price series omits cash/noncash distributions and dividend economics; universe spot checks do not prove every return valid. H3/H7 cannot be evaluated without first-publication-time catalyst snapshots and earnings schedules. Do not backfill with future-known revisions/analyst hindsight.

### QV2-010 — 2R fixed target mixes distinct trade archetypes — P1 / VERIFIED_SOURCE
`signals/levels.py` constructs midpoint + 2R with resistance cap for all hypotheses, stop 1–3 ATR; risk from zone midpoint rather than actual fill. Fixed target can truncate trend continuation and miss short bounce economics. Pre-register matched-policy exit comparisons, avoid fitting individual outcomes. Use actual fill-based marginal economics, label ambiguity and net risk at worse fill.

### QV2-011 — Quality 0–100 is not observed alpha or calibrated probability — P1 / REPORTED_RESULT
H5 holdout Quality–R Spearman ~0; Quality top-half H2 variant +1.778% is **holdout-contaminated hypothesis**, not discovery. Retain UI as descriptive evidence coverage if necessary; do not sort “high-confidence” buys by it. Train ranker on development causally, validate return distribution and calibration separately.

### QV2-012 — Multiplicity from 229 diagnostic configurations — P1 / REPORTED_RESULT
229 variants appear in `strategy_metrics.json`; primary config frozen, which is good. But selection of future S1–S5 weights/exits based on best observed 2025–26 variant contaminates future use of that window as untouched evidence. Audit trials across branches and research cycles; version registry, false-discovery simulations and adequate prospective holdout.

### QV2-013 — Common-snapshot leakage from model vs rule populations — P1 / VERIFIED_SOURCE
The competing-risk model trains on all filled events (possibly repeated/overlapping) while the primary rule economics use regime-allowed, deduplicated positions. The fitted probability refers to a different selection population. Align primary estimate to planned execution or explicitly target preselection likelihood; inspect calibration and training sample dependence. This does not automatically make the model wrong, but could explain unhelpful downstream probabilities.

### QV2-014 — Research scope vs production architecture — P1 / VERIFIED_ARCHITECTURE
`.github/workflows/apex-real-equities-research.yml` is research-only. Production cloud model release still depends on separately provisioned registry; prior drift job failed preflight for missing release env. No credible claim “live system learns automatically” until protected registry + Pi version ack + failover tested. Preserve $0-goal and no laptop.

## Prioritization based on expected information value

**First:** QV2-001 reproducibility, QV2-002 fill sequencing, QV2-003/004 p-values/bootstrap and QV2-006 cost parity. These could alter the interpretation of existing empirical findings and release gates.  
**Second:** QV2-008/009 universe and event provenance, QV2-007 causal controls.  
**Third:** QV2-010/011 exit policy and ranking, then S1–S5.  
**Always:** enforce QV2-012 multiplicity and QV2-013 population alignment.

For EACH issue: attach exact latest SHA, test repro, before/after, expected shift, severity, reviewer, PR and run evidence. Withdraw any stale claim, never hide it.