# Quant V2 Evidence Baseline and Adversarial Questions V3 Must Answer

Read these exact sources on CURRENT main before modifying:
- `research-results/real-equities/qv2-20261022-s1-s5-u1-presample/{QUANT_V2_DECISION_REPORT.md,REVIEW.md,comparisons.md,manifest.json,strategy_metrics.json}`;
- `research-results/real-equities/2026-10-21-fixed78-sip-8y-r2/{comparisons.md,ERRATUM.md}`;
- `docs/quant-v2/FORWARD_SHADOW_PLAN.md`;
- `apps/pi-worker/src/apex_worker/research/realeq/{runner_v2.py,study_v2.py,protocol_v2.py,universe_v2.py,data_v2.py}`;
- `apps/pi-worker/src/apex_worker/research/{book.py,execution_v2.py,inference.py,controls.py}`;
- `apps/pi-worker/src/apex_worker/signals/strategies/quant_v2.py`, `features/quant_v2.py` and `signals/levels.py`;
- `.github/workflows/{apex-real-equities-research.yml,apex-model-train-weekly.yml}`;
- `apps/web`, `apps/pi-worker` release/model/Discord services, tests and contracts.

## Empirical case study: why “best historical hit rate” is the wrong objective

First Quant V2 real-equity run under amended protocol SHA `33bd4e99ecaf06e8fbe0312e2892a0e382d39a632c28280bb66dbd4f5d05475d`:
- 1,334,342 rows (2016–2026), 507 usable instruments, 495 usable stocks; U1 survivors; 143 historical requests; 6min actual research.
- S1 +0.325% vs same-session same-sector matched controls per signal, two-sided 90% CI [-0.016,+0.717], Holm p .230. Book +6.2% (~2.2%/yr), max DD -7.8%, avg exposure 47%, versus exposure-matched SPY/cash +12.7%.
- S3 +0.767% paired per signal, CI [-0.180,+1.800], Holm p .266, only 36 filled trades (~32 sessions), 6% exposure, descriptive book +1.8%.
- S2 residual momentum: −0.055% paired, economically failed with power. S5 sector ETF trend: −4.22bp/day vs exposure-matched SPY/cash, even costless version loses; retired. H4/H8 also fail under protocol. S4/H3/H7 unavailable due PIT catalysts. No strategy currently achieves independently validated edge.
- S1 worse in risk-off: −0.86% paired across 28 qualifying signals, though many signals in rising regime. Gross return per filled S1 +0.809% reduced to net +0.306% at 25bps per side; at 50bps per side net per signal negative. S3 current fixed 2R screening rejected 114/152 qualifying signals, suggesting policy constraint—not just predictive absence.
- Independent Type-I simulations disclosed some anti-conservativeness (1.2–1.5× nominal) and limited power; no heroic claims from borderline small samples.

## Source-level architecture constraints / new questions

### Current study_v2.py (1,200 lines) and quant_v2.py strategy features
Study treats candidate selection, execution arms and matched controls but remains **registered-strategy centric**. Need modular search across strategy mechanisms, horizons, account constraints and economic objectives. Do NOT start by converting everything into an LLM scorer without data. Retain mature code and build thin common interfaces.

### Current book.py (~150 lines)
Capital book fixed ≤10 positions, sector cap 30%, same-day entry/sale order, cash yield 0 and exposure-matched SPY. Need verify daily self-financing, fill sequence, corporate actions/dividends, position weights, simultaneous signals, short/leverage policy, portfolio dead cash, opportunity-cost calculation and alternatives. Any optimizer must be forward-causal: no hindsight allocation or retroactive “best book.”

### Current features.quant_v2.py
Mostly technical momentum/residual factors computed from price panels with PIT membership gate limited to today's survivors, sector labels applied historically. No historical PIT earnings schedule or issuer/sector change mapping. Accept information-limited contexts and abstain rather than fabricating history.

### Current runner_v2.py
Research-only workflow is manually dispatched; it can evaluate historical data once but does not record tomorrow's pre-outcome decisions. Forward Shadow Plan file explicitly says “Not implemented.” Build that FIRST, so future observations compound independently of research efforts.

### Existing model workflow
Weekly train can use synthetic fixture if no Alpaca keys; this is valid for DEMO only but should never masquerade as real discovery/live alpha. Signed Pi release and Discord practical health separate from research model fitting. Model release must be gated to legitimate champion evidence.

### Existing sample and experiment limitations
- Historical 2017–2019 presample was evaluated after being exposed in cancelled A0; not an untouched test. Backward-in-time. 2019–2026 already used to design strategies and read metrics, therefore development data for new V3 ideas, NOT confirmatory.
- Surviving S&P500 selection and split-only corporate actions; dividends, spin-offs, delistings and risk-free cash rate omission alter book-relative economics.
- Historical daily OHLCV alone cannot prove limit queue, spread, stop-order execution, target-first chronology or signal arrival at market boundary.
- Stock selection vs book outcome disconnected; past best indicator may be unexploitable. Ranks not chance-of-success.
- If no affordable PIT delisted dataset, present serious limitation. Do not call V3 “proved best in equities.”

## Adversarial questions in the first independent technical review

1. Can a profitable matched differential still destroy capital after aggressive stop/target and churn?
2. Is an apparent strategy advantage just a long equity beta exposure, size factor, sector concentration or conditioned bull market?
3. How much portfolio return is lost to keeping 50–94% in cash without actively comparing alternatives?
4. Does changing horizon from 20 sessions to 3/5/10/30/40/60 produce real improvement (DEV-only; register before tests)?
5. Can the optimizer choose a better cash/SPY portfolio before observation, without hindsight or look-ahead?
6. Are signals chosen on overlapping days and names but counted as independent outcomes?
7. How do commissions/slippage, gap risk, missed fills, spread and earnings restrictions affect realistic net result?
8. Does measured strategy rank correlate with **incremental realized portfolio utility** rather than raw price changes?
9. Will every research failure be logged, including experiments rejected after inspecting outcomes?
10. Does an experimental novel idea beat simplest opportunity-aware alternatives (cash, SPY, broad ETF, no trade), or simply produce an elaborate description?

**Output:** `docs/discovery-v3/BASELINE_AUDIT.md` with current SHA, confirmed defects vs limitations, actual reproduction commands and priority severity. No edits to previously published metrics.