# Quant V2 decision report — `qv2-20261022-s1-s5-u1-presample`

| | |
|---|---|
| Evidence | **one** confirmatory look under `realeq-protocol-2` A1 (sha256 `33bd4e99…475d`), [Actions run 39849016753](https://github.com/JaredVogler2/STONKS2026/actions/runs/39849016753), code `eda340a`, dataset `0339c815…651f` |
| Window | PRESAMPLE decisions 2017-01-03 → 2019-09-20: **backward-time historical robustness, not forward out-of-sample**, and **exposed** by the cancelled A0 run (unread by the analyst; [REVIEW.md](REVIEW.md) §4) |
| Universe | U1, the current S&P 500 (500 stocks; 495 usable; 347 members at the PRESAMPLE start), **survivorship-limited** |
| Data | Alpaca historical SIP daily bars, split-adjusted; 143 requests; costs 25 bps per side; pessimistic execution leg |
| Mode | research only; nothing is promoted, alerted or traded |

## A. Executive outcome

**No strategy demonstrated validated incremental economic value. The aggregate is `NO_TRADE`.**

**S1 and S3: positive but not significant.**
- S1 (leader pullback) beat same-session matched controls by **+0.33 pp per signal**:
  - conservative p 0.058, Holm 0.23;
  - consistent across years and both halves;
  - concentrated in one market regime;
  - underpowered.
- S3 (breakout retest) did the same, by **+0.77 pp**, on only 36 trades (p 0.089, Holm 0.27).
- Neither survives the multiple-testing correction. Neither beat exposure-matched SPY/cash in absolute terms, S3's tiny PRESAMPLE book aside.

**S2 and S5: powered failures.**
- **S2** (residual momentum) failed with power: −0.05 pp, with the upper bound below the economic minimum.
- **S5** (sector-ETF trend) failed with power: −4.2 bp/day vs exposure-matched SPY/cash, negative even at zero cost.

**S4, H3 and H7** remain not evaluable.

The engine detected its look-ahead trap, and the placebo was quiet. The evidence is honest, but small, backward-looking and from a survivorship-limited universe.

## B. Full performance table (PRESAMPLE, 25 bps per side, pessimistic leg)

Per-signal values are 20-session trade returns; a no-fill counts as 0. "Paired" is the strategy minus the mean of its K = 10 same-session, same-sector-and-volatility matched controls, which are themselves a matched-random selection under the same stop, horizon and costs.

| Rule (arm) | Signals kept / initial | Net per signal | Net per fill (gross) | Matched control net | **Paired mean** [90% CI] | p cons. → Holm | Win rate · payoff | Disposition |
|---|---|---|---|---|---|---|---|---|
| **S1** leader pullback (E0X1) | 331 / 374 | +0.289% | +0.306% (+0.809%) | −0.036% | **+0.325%** [−0.016, +0.717] | 0.058 → 0.230 | 44.9% · 1.44 | PROMISING, requires forward |
| **S2** residual momentum (E0X1) | 2,951 / 5,822 | −0.184% | −0.190% (+0.311%) | −0.130% | **−0.055%** [−0.206, +0.097] | 0.767 → 1.000 | 44.0% · 1.16 | **FAILED_WITH_POWER** |
| **S3** breakout retest (C_X0) | 37 / 152 | +0.491% | +0.535% (+1.038%) | −0.421% | **+0.767%** [−0.180, +1.800] | 0.089 → 0.266 | 52.9% · 1.17 | PROMISING, requires forward |
| **S4** earnings continuation | — | — | — | — | — | — | — | NOT_EVALUABLE (no point-in-time source) |
| **S5** sector-ETF trend (R10) | 683 sessions | −0.79 bp/day | — | exp.-matched SPY/cash | **−4.22 bp/day** [−5.90, −2.55] | 1.000 → 1.000 | — | **FAILED_WITH_POWER** |
| H1 VCP breakout | 150 | −0.177% | | −0.093% | −0.089% | 0.592 → 1.000 (legacy family) | | INCONCLUSIVE |
| H2 RS pullback | 115 | −0.052% | | −0.333% | +0.256% | 0.214 → 0.854 | | INCONCLUSIVE |
| H3 post-event drift | — | | | | | | | NOT_EVALUABLE |
| H4 oversold recovery | 58 | −1.672% | | −0.395% | −1.243% | 0.977 → 1.000 | | FAILED_WITH_POWER |
| H5 sector rotation | 1,146 | −0.262% | | −0.525% | +0.249% | 0.070 → 0.351 | | INCONCLUSIVE |
| H6 gap and hold | 62 | +0.817% | | −0.376% | +1.109% | 0.030 → 0.178 | | PROMISING (legacy only; DEV paired −0.84%) |
| H7 catalyst compression | — | | | | | | | NOT_EVALUABLE |
| H8 turn of month | 653 | −0.650% | | −0.116% | −0.553% | 0.944 → 1.000 | | FAILED_WITH_POWER |
| Momentum (top-1 ret_20 every 5 sessions) | 63 / 122 | −1.853% | | +0.232% | −2.062% | descriptive | 30.0% · 0.99 | baseline |
| Basic breakout | 2,842 / 14,926 | −0.227% | | −0.172% | −0.051% | descriptive | 40.9% · 1.29 | baseline |
| Matched-random placebo (1 random member per session) | 579 / 608 | +0.048% | | +0.023% | +0.025% | 0.454 (engine check) | 44.9% | baseline |

**Index and S5 references:**
- SPY, 100% invested over PRESAMPLE: **+32.4%**, max drawdown −20.2%.
- S5: **−6.5%** total, against **+24.8%** for exposure-matched SPY/cash and +25.5% for equal-weight sectors.

**Capital-constrained books** (10 slots, 30% sector cap, 25 bps, PRESAMPLE; descriptive):

| Book | Trades taken | Total return | Max drawdown | Average exposure | Turnover | Sharpe | Exposure-matched SPY/cash | IR vs it | Mean of 10 control books |
|---|---|---|---|---|---|---|---|---|---|
| S1 | 247 | +6.2% (2.2%/yr) | −7.8% | 47% | 49× | 0.34 | +12.7% | −0.41 | −1.0% |
| S2 | 337 | −9.7% | −19.1% | 72% | 67× | −0.36 | +12.3% | −1.19 | −11.4% |
| S3 | 34 | +1.8% | −2.5% | **6%** | 7× | 0.34 | +0.8% | +0.22 | −1.7% |
| Basic breakout | 566 | −12.0% | −22.0% | 77% | — | −0.42 | +16.0% | — | −13.2% |
| Momentum top-1 | 60 | −11.1% | −12.1% | 8% | — | −1.67 | +2.1% | — | −0.0% |

The DEV books (2019–2026, contaminated diagnostics) show the same pattern:
- S1: −8.5%, against +46.2% for SPY/cash; its control books −18.2%.
- S2: −41.6%, against +89.1%.

## C. Root-cause analysis

| Factor | S1 | S2 | S3 | S5 |
|---|---|---|---|---|
| **Stock selection** (paired, per-fill term) | **+0.32 pp**: all of the paired mean | −0.05 pp: none | **+0.77 pp**: all of it | Negative: −4.4 bp/day vs equal-weight sectors |
| **Entry timing and fills** (fill-rate term) | 0.00 pp (fill rates 94% vs 95% for controls) | −0.00 pp | −0.00 pp | n/a (market orders) |
| **Stop / target construction** | X1 (stop + time): 52% stops, 48% time exits; MFE ≈ +4.5%, MAE ≈ −2.2% | 47% stops | X0: 41% stops, 21% targets. **75% of signals were rejected by the target screen** (114 of 152; 78 would pass at 10 bps) | n/a |
| **Transaction costs** | Gross +0.81% per fill → net +0.31%. **Net per signal is negative at 50 bps (−0.18%)** | Gross +0.31% per fill turns into −0.19% net | Net per signal still +0.03% at 50 bps | 1.9 bp/day of the −4.2 (−2.3 bp/day even at 0 bps) |
| **Portfolio crowding** | 65 of 312 fills skipped (35 for slots, 30 for the sector cap) | 2,437 slot skips: far more signals than capacity | none (6% capital use) | n/a |
| **Sample size** | MDE +0.61 pp (> economic minimum): underpowered | MDE +0.26 pp: **adequately powered** | MDE +1.69 pp: badly underpowered (32 distinct sessions) | MDE 2.8 bp/day, but the whole CI is below 0 |
| **Market exposure** | Beta 0.85; alpha −0.21% per trade vs SPY over the window | Beta 0.89; alpha −0.57% per trade | Beta 0.95; alpha +0.50% per trade | Average exposure 80%; lost to SPY at the same exposure |
| **Regime** | 90% trending_up; paired −0.86% in risk-off (n = 28) | Mixed | 95% trending_up | Abstention rarely triggered (17 invested risk-off sessions) |

**Reading.**

1. **The swing-trade wrapper is the dominant drag.** Under the same stop and 20-session horizon at 25 bps, random matched S&P names lose money: −0.04% to −0.42% per signal. Every swing book trails exposure-matched SPY/cash, S3's tiny PRESAMPLE book aside. Selection skill has to overcome this first.
2. **S1 and S3 show selection value, and nothing else.** Their paired edge comes entirely from what is bought, not from fills or timing. S1's mechanism contrast agrees: leaders bought on a controlled pullback beat leaders in any state by +0.43 pp (descriptive, p 0.040). But the edge is smaller than the gap to the index, and it is regime-dependent.
3. **S2 and S5 have no selection value** in this window, and costs make them worse. The momentum baseline was the worst rule tested (−2.06 pp paired).
4. **The test cannot see the economic minimum.** Realistic power means a +0.30 pp edge would read INCONCLUSIVE most of the time. S1 is exactly that case.

## D. Strategy disposition

| | Disposition | Why |
|---|---|---|
| **S1** leader pullback (E0X1) | **Retain for prospective validation.** Promising but statistically inconclusive. | Positive selection edge in every year and both halves, but p 0.058 (Holm 0.23), underpowered, 90% one regime, negative absolute net at 50 bps, behind SPY/cash. Frozen as-is for the forward shadow; no re-tuning on this window. |
| **S3** breakout retest (C_X0) | **Retain for prospective validation.** Promising but statistically inconclusive. | Largest paired edge, but on 36 trades; MDE 1.7 pp. The cost-dependent target screen removes most signals. The DEV diagnostic (contaminated) also favours retest over immediate entry (+0.90 pp per opportunity, p 0.086). |
| **S2** residual momentum | **Retire with adequate evidence.** | FAILED_WITH_POWER (MDE 0.26 pp < 0.30), negative in DEV too, worst book. |
| **S5** sector-ETF trend | **Retire with adequate evidence.** | FAILED_WITH_POWER, with the whole CI below zero; negative at 0 bps; behind SPY/cash and equal-weight sectors. |
| **S4** earnings continuation | **Not evaluable due to missing data.** | No verified point-in-time first-publication earnings source. |
| H4, H8 | **Retire.** | FAILED_WITH_POWER. |
| H6 | **Revise a specific mechanism**, or leave as a legacy comparator only. | +1.1 pp paired (n = 62, raw p 0.03), but its DEV paired mean is −0.84% and it sits in the legacy Holm family. Not a candidate without its own registration. |
| H1, H2, H5 | **Deprioritize.** | Inconclusive; H2 and H5 are positive vs controls but negative in absolute terms. |
| H3, H7 | **Not evaluable due to missing data.** | No point-in-time catalysts. |
| Momentum and basic breakout baselines | Not strategies | Strongly and mildly negative: they show what the wrapper does to naive selection. |

## E. Recommended next milestone (one)

**Build the forward-shadow recorder for the two retained arms, S1 E0X1 and S3 C_X0. Record a market-relative (SPY-hedged) leg next to the registered leg.**

**Why this, and not a new model or strategy:**
- The historical evidence is spent. PRESAMPLE has had its one look and is exposed. DEV is contaminated. The 2017–2019 window cannot be re-used.
- The only uncontaminated evidence left is prospective, and it accrues one session at a time. The frozen cluster rule needs about 480 forward sessions for a p-value, roughly October 2028 (docs/quant-v2/FORWARD_SHADOW_PLAN.md). Every day without the recorder is evidence lost.
- The results point at one question: does S1's and S3's selection value survive outside a trending market and net of market exposure? A hedged leg answers that by separating selection from beta, without changing the frozen rules.
- Fills are not the problem (fill-rate term ≈ 0). An execution-mechanics study would be the second milestone, registered separately on DEV-only diagnostics before any forward evaluation. Stops and horizon are the wrapper that loses to SPY.

**What it involves:**
- A scheduled, research-only GitHub Actions job, on the free tier, with no paid data and no laptop.
- It appends hash-chained decision records after each close, posts the chain head to the tracking issue, and records outcomes separately once labels mature.
- It records every eligible candidate, both locked arms, the matched controls drawn at decision time, and model and protocol versions.
- No Discord signal, alert or order.

## F. Forward validation requirements (Phase 6)

The full plan is [docs/quant-v2/FORWARD_SHADOW_PLAN.md](../../../docs/quant-v2/FORWARD_SHADOW_PLAN.md).

**Rules:**
- Decisions from 2026-10-23 only.
- Decision records are written before outcomes exist, append-only and hash-chained, with prices observable at decision time and planned execution assumptions.
- No retrospective editing.

**Evaluation dates:**
- The registered first evaluation date, 2027-04-30, is a status check only: 130 sessions cannot meet the 12-block rule.
- The first powered look is about late October 2028.

**Before any use, a positive forward result would still need:**
- an independent replication (a point-in-time universe once licensed, or a later window);
- intraday validation of execution;
- the release, legal and data-rights gates.

Live, group-actionable and high-conviction outputs stay disabled. NO_TRADE stands.

## G. Limitations that could change these conclusions

- **Survivorship:** current constituents only. It inflates absolute returns; paired controls cancel part of it.
- **Exposed PRESAMPLE:** see REVIEW.md §4.
- **Backward in time:** PRESAMPLE precedes the design data.
- **Regime:** 2017–2019 was mostly an up-trending market.
- **Price-only returns:** dividends are not modelled. Absolute returns and the books are understated by about 1.5–2% a year for SPY, and S5's active return by about 0.4 bp/day × exposure, against an observed −4.2 bp/day.
- **Daily bars:** no ambiguity occurred here. Fills at the limit, with no queue or spread modelling, are an assumption, and 25 bps is a modelled cost.
- **Inference size:** about 1.2–1.5× nominal on realistic nulls, which strengthens the "not significant" reading.
- **Power:** low.
- **Ticker changes and missing data:** a spin-off by a parent under a former ticker may be missing from the quarantine; DOW is missing.
- **One window:** single universe, single window. No conclusion generalises beyond large-cap US equities in 2017–2019.
