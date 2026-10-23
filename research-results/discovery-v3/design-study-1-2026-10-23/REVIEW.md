# Design study `design-study-1` (`design-study-1-2026-10-23`): reviewer notes

| | |
|---|---|
| Run | [39999238748](https://github.com/JaredVogler2/STONKS2026/actions/runs/39999238748), `apex-discovery-v3` with `plan = design-study-1`, manual dispatch on `main`, 2026-10-23 17:56 UTC. The research step took 4 min 10 s |
| Code | `7c87dc6` (the PR #32 merge). Plan sha256 `d5fef5eb…7689` equals the [registration](../../../docs/evidence/DESIGN_STUDY_1.md) |
| Data | Alpaca historical **SIP** daily bars, split-adjusted: protocol-2's U1 dataset `0339c815…`. `data_quality_summary.json` is byte-identical to pilot 1's and the cost study's |
| Window | 1,734 DEV sessions, 2019-10-24 → 2026-09-18 (first decision 2019-10-22), `DEV_CONTAMINATED`, tier `DEV_DIAGNOSTIC`. On average 361 of 495 stocks were in the explicit mask |
| Artifact | `apex-discovery-39999238748-1`, digest `sha256:6abf1b56…02e38`. The three files here are byte-for-byte copies |
| Kind | A **measurement of risk**. The proposal rule reads only tracking error and worst sessions, and no published result changes |

## 1. The design rule holds: neutralizing removes about 40% of the noise

Tracking error against the equal-weight twin, at the reference dispersion (a rank's 1/√3). Every variant's realised score dispersion was 0.55–0.60, so the raw figures tell the same story.

| Signal | `raw` (cohort 0) | `sector` | `full` | `full` vs `raw` | Evidence rate at equal alpha, (raw/full)² |
|---|---|---|---|---|---|
| Momentum (60 sessions) | 2.26% | 1.70% | **1.26%** | −44% | **3.2×** |
| Reversal (5 sessions) | 2.45% | 1.83% | **1.45%** | −41% | **2.9×** |
| 52-week high | 2.45% | 1.86% | **1.40%** | −43% | **3.1×** |
| `composite-full` | — | — | **1.40%** | — | — |
| `lowvol-full` | — | — | **0.80%** | — | — |

**Sector neutrality removes about a quarter of the noise, and beta and size neutrality as much again.** The worst sessions shrink too.

| Design | Worst session against the twin | Proposed twin loss bound (2× rule) |
|---|---|---|
| `mom-raw` → `mom-full` | −1.27% → **−0.88%** (both 2020-03-19) | 0.03 → **0.02** |
| `rev-raw` → `rev-full` | −1.21% → **−0.67%** (both 2020-03-18) | 0.03 → **0.015** |
| `hi52-raw` → `hi52-full` | −1.47% (2020-11-09, the vaccine-day momentum crash) → **−0.84%** (2020-03-19) | 0.03 → **0.02** |
| `composite-full` | **−0.53%** (2020-03-19) | **0.015** |
| `lowvol-full` | **−0.27%** (2026-08-19) | **0.0075** |

**The construction did what it claims** (mean absolute active exposure per decision):

| | `raw` | `full` |
|---|---|---|
| Market beta | 0.033–0.036 | 0.0008–0.0023 |
| Size (log dollar volume) | 0.032–0.033 | 0.0008–0.0015 |
| Largest sector imbalance | 0.022–0.024 | 0.0004–0.0011 |

The `sector` variants are exactly sector-neutral (0.0000).

## 2. What it would take to prove a design

The gross active alpha a year against the twin, before costs, needed for a claim at evidence 800 within 10 years. It is computed with the bounded test at the proposed loss bound (bets capped at 0.9/L) and the design's measured extra cost:

| Design | Alpha needed | Years to 800 at 1% a year | Years to 800 at 2% a year | Extra cost a year (`full`) |
|---|---|---|---|---|
| `mom-raw` → `mom-full` | 3.02% → **1.86%** | 72.2 → **23.7** | 18.5 → **9.1** | 0.03% |
| `rev-raw` → `rev-full` | 3.48% → **2.06%** | 186.4 → **60.8** | 29.4 → **10.5** | 0.33%: 9.2 one-way turnovers a year |
| `hi52-raw` → `hi52-full` | 3.15% → **1.93%** | 82.7 → **26.8** | 20.6 → **9.6** | 0.02% |
| `composite-full` | **1.79%** | **32.2** | **8.4** | 0.15% |
| `lowvol-full` | **0.92%** | **8.8** | **3.4** | 0.02% |

**Reading.**
- Neutralizing cuts the time to a claim by about **3×** at the same edge.
- The bar is still high. A ±50% tilt that earns 1% a year more than equal weight needs **2–6 decades** to be proven at 800, unless it is `lowvol-full`.
- That is the market's information limit, not a weakness of the test. The paper allocation follows evidence long before a claim.

**`lowvol-full` is the most testable design.** Its active return has a tracking error of only 0.76%, so a 1% edge would be provable in about 9 years.

## 3. Cohort 0's declared loss bounds held on every DEV session

| Tilt | Worst against the twin (L = 0.03) | Margin | Worst against SPY (L = 0.05) | Margin |
|---|---|---|---|---|
| `mom-tilt` | −1.27% | 2.4× | −2.87% (2025-04-09) | 1.7× |
| `rev-tilt` | −1.21% | 2.5× | −3.71% (2020-03-12) | **1.35×** |
| `hi52-tilt` | −1.47% | 2.0× | −2.98% (2025-04-09) | 1.7× |

The twin `ew` against SPY: worst −3.00% (2020-03-12), margin 1.7×.

**Finding.** The SPY bounds of cohort 0 have **less margin than this study's 2× rule**, which would set 0.075.
- A crash day about a third worse, relative to SPY, than 2020-03-12 would void `rev-tilt`'s bounded SPY test. Its assumption-free test and its twin tests would be unaffected.
- That costs no validity. Allocation falls back to the assumption-free evidence.
- **Recommendation: leave cohort 0 as registered.** Any change after its genesis needs a new series. A wider bound would cost no speed (against SPY the bet cap does not bind at these tracking errors), but neither does the current one.

## 4. DEV diagnostics (`DEV_CONTAMINATED`; they select nothing)

| Against the twin, a year | `raw` | `full` |
|---|---|---|
| Momentum | −0.82% | −0.23% |
| Reversal | **+0.65%** | +0.11% |
| 52-week high | −0.99% | −0.15% |
| `composite-full` | — | −0.17% |
| `lowvol-full` | — | −0.41% |

The twin `ew` against SPY: −1.29% a year (the mega-cap era).

**None of these is evidence.**
- DEV is contaminated twice and U1 is survivor-biased.
- Each standard error is about 0.4 in IR, so every figure in this table is within noise.
- They agree with the program's record: nothing beat its investable alternative on DEV.
- The tournament exists because no history can settle these questions (method §2).

## 5. The registered proposal

The rule reads risk only: per signal the lowest tracking error at the reference dispersion, plus the two extras.

| Candidate | Rebalance | Twin | Twin loss bound | Loss bound against SPY | Prior mass |
|---|---|---|---|---|---|
| `mom-full` | every 20 | `ew` | 0.02 | 0.075 | 0.04 |
| `rev-full` | every 5 | `ew` | 0.015 | 0.075 | 0.04 |
| `hi52-full` | every 20 | `ew` | 0.02 | 0.075 | 0.04 |
| `composite-full` | every 5 | `ew` | 0.015 | 0.075 | 0.04 |
| `lowvol-full` | every 20 | `ew` | 0.0075 | 0.075 | 0.04 |

The prior mass totals 0.20 of the 0.55 reserve. **It is a proposal**: registering it as cohort 1, its own series, is the owner's decision. Two suggestions for that registration:
1. **Claim against the twin only.** That is the mechanism test. Cohort 0's `ew` already tests equal weight against SPY, and a tilt that beats its twin while equal weight beats SPY beats SPY. Two claim tests per candidate instead of four halve the threshold, from 2,000 to 1,000. The tests against SPY would still drive the paper allocation.
2. **Run it in the tournament's existing job.** It shares the data fetch and stays within that job's 8-minute timeout, so the minutes budget is unchanged.

## 6. Limits

- **Survivorship.** The worst-session statistics miss names removed from the S&P 500 before today. With about 361 names in the mask, one name carries at most 0.5/361 ≈ 0.14% of active weight, so a −50% day moves a tilt against its twin by about −0.07%.
- **Future crashes.** They can be worse than March 2020. The safety factor of 2 is a declared judgement.
- **Price returns.** Prices are split-adjusted only, so dividends are excluded throughout.
