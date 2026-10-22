# Comparisons — `qv2-20261022-s1-s5-u1-presample` (`realeq-protocol-2`)

**Aggregate: NO_TRADE.** Live alerts, promotion and orders stay disabled.

* PRESAMPLE (earlier in time than the design data): decisions 2017-01-03 → 2019-09-20. Evidence type: **backward-time historical robustness, not forward out-of-sample**. Integrity: **exposed** — the cancelled A0 run 39829472789 computed and published PRESAMPLE outcomes (log, job summary, artifact); the analyst never read them and the design was not changed in response (PROTOCOL_V2.md §12)
* DEV (contaminated: design and diagnostics only): decisions 2019-10-23 → 2026-09-18
* Universe: U1 SURVIVORSHIP_LIMITED_CURRENT_CONSTITUENTS (snapshot sp500-current-2026-10-22); costs 25 bps per side; pessimistic leg; protocol sha256 `33bd4e99ecaf06e8…`
* Limitations (full text at the end): SURVIVORSHIP; SECTOR LABELS; EARNINGS UNKNOWN; PRICE-ONLY RETURNS; DAILY BARS; PRESAMPLE IS EARLIER, NOT LATER; PRESAMPLE EXPOSURE; SURVIVORSHIP-LIMITED UNIVERSE; INFERENCE SIZE; LARGE MINIMUM DETECTABLE EFFECT; SPLIT ADJUSTMENT; CORPORATE ACTIONS BY CURRENT TICKER

## Primary family (one PRESAMPLE look per strategy)

|  | Strategy | Arm (DEV) | Signals | Per-signal net | Control | Paired mean | p (cons.) | Holm p | Disposition |
|---|---|---|---|---|---|---|---|---|---|
| S1 | Leadership continuation after a controlled pullback | E0X1 | 331 | 0.289% | -0.036% | 0.325% | 0.0576 | 0.2304 | **RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD** |
| S2 | Market/sector residual momentum | E0X1 | 2951 | -0.184% | -0.130% | -0.055% | 0.7674 | 1.0000 | **FAILED_WITH_POWER** |
| S3 | Volatility-contraction breakout: immediate vs retest | C_X0 | 37 | 0.491% | -0.421% | 0.767% | 0.0888 | 0.2663 | **RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD** |
| S4 | First-publication earnings/guidance continuation | — | — | — | — | — | — | — | **NOT_EVALUABLE_MISSING_INPUT** |
| S5 | Sector-ETF trend allocation with cash abstention | R10 | 683 | -0.0654 | 0.2480 | -0.000422 /day | 1.0000 | 1.0000 | **FAILED_WITH_POWER** |

**S5 row:** the columns read sessions | S5 total return | exposure-matched SPY/cash total return | mean daily active return (S5 is an allocation, not a per-signal rule; its benchmark is SPY/cash, not a matched control).

## Reasons

* **S1** RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD: failed gates: holm_p_below_alpha, two_regimes_15pct
* **S2** FAILED_WITH_POWER: mean -0.00055 <= 0 and every one-sided 95% upper bound < 0.003
* **S3** RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD: failed gates: holm_p_below_alpha, two_regimes_15pct
* **S4** NOT_EVALUABLE_MISSING_INPUT: no verified point-in-time first-publication earnings source; nothing is backdated
* **S5** FAILED_WITH_POWER: mean -0.00042 <= 0 and every one-sided 95% upper bound < 0.0001

## Decision statistics (PRESAMPLE, locked arm; conservative over L = 20 and 40)

|  | n | Clusters | Mean (paired; S5 daily active) | 90% CI low | 90% CI high | p (cons.) | Holm p (family of 4) | p optimistic leg |
|---|---|---|---|---|---|---|---|---|
| S1 | 331 | OK | 0.325% | -0.016% | 0.717% | 0.0576 | 0.2304 | 0.0576 |
| S2 | 2951 | OK | -0.055% | -0.206% | 0.097% | 0.7674 | 1.0000 | 0.7674 |
| S3 | 36 | OK | 0.767% | -0.180% | 1.800% | 0.0888 | 0.2663 | 0.0888 |
| S5 | 683 | OK | -0.000422 | -0.000590 | -0.000255 | 1.0000 | 1.0000 | — |

The 90% interval is the two-sided studentized block interval (one-sided 95% bounds), widest over the decision block lengths, with the A1 Student-t floor. A p-value exists only with >= 12 non-empty blocks at every decision length. On production-shaped synthetic nulls this rule still rejects about 1.2-1.5x nominal, so a Holm p between 0.03 and 0.05 is borderline evidence, not a clean pass.

## DEV diagnostics per arm (CONTAMINATED; selection only)

|  | Arm | Signals | Per-signal net | Paired mean | Test status | Chosen |
|---|---|---|---|---|---|---|
| S1 | E0X0 | 83 | 0.076% | -0.053% | OK |  |
| S1 | E0X1 | 903 | -0.139% | 0.102% | OK | ✔ |
| S1 | E1X0 | 84 | -0.597% | -0.586% | OK |  |
| S1 | E1X1 | 931 | -0.251% | -0.111% | OK |  |
| S2 | E0X1 | 7269 | -0.067% | -0.113% | OK | ✔ |
| S2 | E0X0 | 5051 | -0.158% | -0.240% | OK |  |
| S3 | A_X0 | 226 | -1.265% | -0.894% | OK |  |
| S3 | A_X2 | 414 | -0.963% | -0.770% | OK |  |
| S3 | C_X0 | 122 | -0.526% | 0.103% | OK | ✔ |
| S3 | C_X2 | 256 | -0.689% | -0.212% | OK |  |
| S5 | R10 | 1734 | 0.3915 | -0.000104 | OK | ✔ |
| S5 | R5 | 1734 | 0.2444 | -0.000217 | OK |  |

## Mechanism contrasts (each strategy's own null; descriptive)

* S1: PRESAMPLE mean 0.430%, p 0.0402 (OK); DEV mean 0.034% (DEV is contaminated)
* S2: PRESAMPLE mean 0.337%, p 0.1184 (OK); DEV mean -0.129% (DEV is contaminated)
* S3: PRESAMPLE needs the unselected entry arm on PRESAMPLE; not computed (A1 isolation); DEV mean 0.904% (DEV is contaminated)
* S5 vs equal-weight sectors: mean daily -0.000438

## Sensitivities of the primary arm (PRESAMPLE)

|  | Paired 0 bps | Paired 10 bps | Paired 25 bps | Paired 50 bps | Net/signal 0 bps | Net/signal 50 bps | Optimistic leg (paired) | Touch-fill (paired) | Ambiguous fills | p (L=60) |
|---|---|---|---|---|---|---|---|---|---|---|
| S1 | 0.322% | 0.323% | 0.325% | 0.328% | 0.763% | -0.183% | 0.325% | 0.325% | 0.0% | — |
| S2 | -0.052% | -0.053% | -0.054% | -0.057% | 0.302% | -0.668% | -0.054% | -0.052% | 0.0% | — |
| S3 | 0.771% | 0.769% | 0.767% | 0.763% | 0.954% | 0.030% | 0.767% | 0.780% | 0.0% | — |

Paired differences barely move with cost because the strategy and its controls pay the same cost per fill; the absolute per-signal net shows the cost drag.

## Trade economics of the primary arm (PRESAMPLE, pessimistic leg, 25 bps)

|  | Initial signals | Kept | Fill rate | Net / initial signal | Gross / fill | Net / fill | Median net / fill | Win rate | Avg win | Avg loss | Payoff | Stop | Target | Time | No fill | Small sample |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 | 374 | 331 | 94.3% | 0.256% | 0.809% | 0.306% | -1.713% | 44.9% | 4.57% | -3.17% | 1.44 | 52.2% | — | 47.8% | 5.7% |  |
| S2 | 5822 | 2951 | 97.2% | -0.093% | 0.311% | -0.190% | -1.442% | 44.0% | 4.55% | -3.91% | 1.16 | 46.6% | — | 53.4% | 2.8% |  |
| S3 | 152 | 37 | 91.9% | 0.120% | 1.038% | 0.535% | 1.010% | 52.9% | 4.17% | -3.56% | 1.17 | 41.2% | 20.6% | 38.2% | 8.1% |  |

Kept = after the levels screen, quarantine, the DEV-crossing exclusion and de-duplication. Exit shares are of fills on the pessimistic leg (an ambiguous bar counts as a stop there); the next table separates the exits daily bars resolve from the ones they cannot.

Exits that daily bars resolve, the unresolvable ones, excursion bounds and market exposure:

|  | Stop (resolved) | Target (resolved) | Time | Unresolved (daily-bar ambiguity) | MFE bounds (mean) | MAE bounds (mean) | Sessions held | Beta to SPY (trade window) | Alpha / trade | Signals on top 5% sessions |
|---|---|---|---|---|---|---|---|---|---|---|
| S1 | 163 | 0 | 149 | 0 | 4.54% … 4.55% | -2.21% … -2.21% | 13.3 | 0.85 | -0.214% | 21.4% |
| S2 | 1335 | 0 | 1533 | 0 | 4.20% … 4.22% | -2.76% … -2.76% | 14.3 | 0.89 | -0.568% | 11.5% |
| S3 | 14 | 7 | 13 | 0 | 4.06% … 4.07% | -2.51% … -2.51% | 12.8 | 0.95 | 0.503% | 13.5% |

MFE/MAE are bounds, not observations: daily bars cannot order the high and low around the fill. Beta and alpha regress each trade's net return on SPY's return from the decision close to the exit close (approximate, descriptive).

Where the paired mean comes from: paired = p_s (r_s − r_c) + (p_s − p_c) r_c, with p the fill rate and r the net per fill (no-fill = 0 for both legs):

|  | Strategy fill rate | Control fill rate | Net / fill (strategy) | Net / fill (controls) | Per-fill term (selection + exits) | Fill-rate term | Data flags (missing bars etc.) |
|---|---|---|---|---|---|---|---|
| S1 | 94.3% | 95.2% | 0.306% | -0.038% | 0.324% | 0.000% | none |
| S2 | 97.2% | 96.6% | -0.190% | -0.134% | -0.054% | -0.001% | none |
| S3 | 91.7% | 91.6% | 0.378% | -0.459% | 0.767% | -0.000% | none |

## Stability by calendar year (paired mean, locked arm)

|  | Segment | Per year (n < 30 marked *) |
|---|---|---|
| S1 | PRESAMPLE | 2017: 0.26% (n=120), 2018: 0.65% (n=93), 2019: 0.13% (n=118) |
| S1 | DEV | 2019: 1.31% (n=44), 2020: 0.08% (n=108), 2021: -0.76% (n=146), 2022: 0.21% (n=50), 2023: 0.12% (n=126), 2024: 1.01% (n=156), 2025: 0.19% (n=151), 2026: -0.62% (n=122) |
| S2 | PRESAMPLE | 2017: -0.08% (n=1155), 2018: 0.10% (n=905), 2019: -0.18% (n=891) |
| S2 | DEV | 2019: -0.38% (n=285), 2020: -0.20% (n=1117), 2021: -0.13% (n=1186), 2022: 0.15% (n=638), 2023: -0.54% (n=925), 2024: -0.03% (n=1194), 2025: 0.31% (n=1071), 2026: -0.27% (n=853) |
| S3 | PRESAMPLE | 2017: 0.22% (n=14*), 2018: 2.13% (n=7*), 2019: 0.65% (n=15*) |
| S3 | DEV | 2019: -1.97% (n=3*), 2020: -1.24% (n=30), 2021: -0.02% (n=20*), 2022: 1.07% (n=9*), 2023: 0.94% (n=11*), 2024: 0.23% (n=16*), 2025: 1.23% (n=18*), 2026: 0.78% (n=13*) |

DEV years are contaminated diagnostics (the design data), shown only for stability context.

## Sample size, power and concentration (PRESAMPLE)

|  | Distinct sessions | Blocks L20 / L40 / L60 | Sectors | Sector HHI | Top sector | MDE | Economic min. | Underpowered |
|---|---|---|---|---|---|---|---|---|
| S1 | 185 | 31 / 16 / 11 | 11 | 0.119 | 19.0% | 0.00613 | 0.00300 | yes |
| S2 | 121 | 31 / 16 / 11 | 11 | 0.110 | 16.3% | 0.00259 | 0.00300 | no |
| S3 | 32 | 20 / 13 / 11 | 10 | 0.125 | 18.9% | 0.01688 | 0.00300 | yes |
| S5 | 683 | 35 / 18 | — | — | — | 0.00028 | 0.00010 | yes |

MDE = (z(1 − 0.05/4) + z(0.8)) × the largest realised PRESAMPLE SE (per signal; S5 per day). Underpowered means a true edge at the economic minimum would most likely read INCONCLUSIVE.

## Capital-constrained books (10 slots, 30% sector cap, 25 bps; descriptive)

|  | Segment | Trades | Total return | Max DD | Avg exposure | Turnover | Sharpe | Exp.-matched SPY* | IR vs SPY/cash* | Control books | p vs controls (descriptive) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 | PRESAMPLE | 247 | 6.17% | -7.78% | 46.6% | 49.3 | 0.34 | 12.74% | -0.41 | -1.00% | 0.0790 |
| S1 | DEV | 693 | -8.52% | -30.15% | 45.8% | 137.4 | -0.07 | 46.21% | -0.80 | -18.15% | 0.2590 |
| S2 | PRESAMPLE | 337 | -9.66% | -19.12% | 71.5% | 67.0 | -0.36 | 12.28% | -1.19 | -11.37% | 0.4360 |
| S2 | DEV | 832 | -41.56% | -47.57% | 66.2% | 162.8 | -0.57 | 89.09% | -1.79 | 13.06% | 1.0000 |
| S3 | PRESAMPLE | 34 | 1.80% | -2.49% | 6.2% | 6.8 | 0.34 | 0.80% | 0.22 | -1.68% | 0.0330 |
| S3 | DEV | 110 | -5.60% | -9.39% | 5.1% | 22.1 | -0.33 | 4.39% | -0.63 | -6.33% | 0.4447 |

*The books' SPY/cash curve holds SPY close-to-close at the previous close's exposure; intraday entries and exits are not timed. 'p vs controls' is an unregistered, unadjusted descriptive test of the daily book difference; it never enters a disposition.

## S5 sector-ETF allocation (PRESAMPLE, selected arm)

* Mean daily net -0.000079; active vs exposure-matched SPY/cash -0.000422; total -6.54% vs 24.80% (SPY 100%: 32.40%)
* Max drawdown -18.99% (SPY -20.15%); average exposure 80.5%; Sharpe -0.20; IR -1.92
* Mean daily active by cost: 0bps -0.000233, 10bps -0.000309, 25bps -0.000422, 50bps -0.000611
* Invested sessions by regime: {'trending_up': 527, 'risk_off': 17, 'rebound': 8, 'volatility_shock': 1}

## Descriptive baselines (same engine; not registered hypotheses)

| Baseline | Segment | Signals | Per-signal net | Control | Paired mean |
|---|---|---|---|---|---|
| momentum_20 | PRESAMPLE | 63 | -1.853% | 0.232% | -2.062% |
| momentum_20 | DEV | 157 | -0.900% | -0.654% | -0.279% |
| breakout_basic | PRESAMPLE | 2842 | -0.227% | -0.172% | -0.051% |
| breakout_basic | DEV | 11304 | -0.306% | -0.162% | -0.147% |
| matched-random (placebo: one random eligible member per session) | PRESAMPLE | 579 | 0.048% | 0.023% | 0.025% |

Every strategy's matched control is itself a matched-random selection (same session, sector and volatility stratum, same stop geometry), so the 'Control' columns are the matched-random benchmark.

## Legacy comparators (same engine; own Holm family)

| Rule | Signals | Per-signal net | Paired mean | p (cons.) | Holm p | Disposition |
|---|---|---|---|---|---|---|
| h1_vcp_breakout | 150 | -0.177% | -0.089% | 0.5922 | 1.0000 | INCONCLUSIVE |
| h2_rs_pullback | 115 | -0.052% | 0.256% | 0.2136 | 0.8544 | INCONCLUSIVE |
| h4_oversold_recovery | 58 | -1.672% | -1.243% | 0.9773 | 1.0000 | FAILED_WITH_POWER |
| h5_sector_rotation | 1146 | -0.262% | 0.249% | 0.0702 | 0.3510 | INCONCLUSIVE |
| h6_gap_and_hold | 62 | 0.817% | 1.109% | 0.0296 | 0.1776 | RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD |
| h8_turn_of_month | 653 | -0.650% | -0.553% | 0.9444 | 1.0000 | FAILED_WITH_POWER |
| h3_post_event_drift | — | — | — | — | — | NOT_EVALUABLE_MISSING_INPUT |
| h7_catalyst_compression | — | — | — | — | — | NOT_EVALUABLE_MISSING_INPUT |

## Engine self-checks

* Look-ahead trap (cheats by construction): mean 14.867%, p 0.0002 → detected: **True**
* Placebo (random member): mean 0.025%, p 0.4542; warning: False

## Limitations

* SURVIVORSHIP: U1 = current S&P 500 constituents (snapshot 2026-10-22); members that left the index are absent. Eligibility starts at each member's date_added, which removes later additions from earlier cross-sections but not the survivor effect; paired same-date controls from the same member set partly cancel it
* SECTOR LABELS: current GICS sectors applied to all history (the 2018 communication services reclassification)
* EARNINGS UNKNOWN: no point-in-time earnings calendar; S4 is NOT_EVALUABLE and earnings vetoes are not modelled
* PRICE-ONLY RETURNS: split-adjusted bars, dividends not modelled (affects absolute, much less paired, returns)
* DAILY BARS: intraday order is unknown; ambiguous outcomes carry pessimistic and optimistic legs (exec-v2.1); a missing session while held is an ambiguity too
* PRESAMPLE IS EARLIER, NOT LATER: the evaluation window precedes the design data (backward-time historical robustness, not forward out-of-sample); forward evidence is still required
* PRESAMPLE EXPOSURE: the cancelled A0 run 39829472789 computed and published PRESAMPLE outcomes (job log, job summary, artifact). The analyst never read them and no design choice responded to them, but the window is not untouched; read every PRESAMPLE result with that in mind (PROTOCOL_V2.md section 12)
* SURVIVORSHIP-LIMITED UNIVERSE: U1 is not an unbiased all-market sample; results do not generalise to delisted or smaller stocks
* INFERENCE SIZE: on production-shaped synthetic nulls the decision rule (with the A1 Student-t floor) rejects 5.8-6.6% at nominal 5% and 1.6-1.9% at the Holm first step (nominal 1.25%); dependence beyond the block length (slowly varying factor premia) is not fully covered, so a Holm p between 0.03 and 0.05 is borderline evidence
* LARGE MINIMUM DETECTABLE EFFECT: on production-shaped synthetic panels the PRESAMPLE window detects only about +1.5-2 pp per signal at 80% power (5-7x the +0.30 pp economic minimum); a true edge at the economic minimum will most likely read INCONCLUSIVE, and any VALIDATED effect size is likely inflated (winner's curse)
* SPLIT ADJUSTMENT: 2 symbol(s) show a >25% jump on a split ex-date; those trade windows are quarantined
* CORPORATE ACTIONS BY CURRENT TICKER: a spin-off by a parent under a former ticker may be missing from the quarantine; dividends and other distributions are not modelled
