# Personal method study `personal-study-2` (`personal-study-2-2026-10-28`): reviewer notes

| | |
|---|---|
| Run | [40607489452](https://github.com/JaredVogler2/STONKS2026/actions/runs/40607489452), `apex-discovery-v3` with `plan = personal-study-2`, manual dispatch on `main`, 2026-10-28 00:14 UTC. The research step took 23 s |
| Code | `94b7097` (the PR #47 merge). Plan sha256 `1bbef24b…d6f3` equals the [registration](../../../docs/discovery-v3/PERSONAL_STUDY_2.md), which was merged before any real price of these ETFs was read |
| Data | Alpaca historical **SIP** daily bars for SPY, SSO, UPRO, EFA, AGG and BIL, adjusted for **splits and dividends**, so every return here (SPY's too) is a total return. 2016-01-04 → 2026-10-23 after the finality lag: 2,718 sessions each, none missing or invalid, 2 provider requests. Dataset `21971aa5…ecc` |
| Window | First decision 2019-10-22, last session 2026-09-23: personal-study-1's window. `DEV_CONTAMINATED`, tier `DEV_DIAGNOSTIC` |
| Artifact | `apex-discovery-40607489452-1`, digest `sha256:74ced076…b88a`. The three files here are byte-for-byte copies |
| Kind | A **method choice** for the owner's personal alerts. Four trials, added to the [trial index](../TRIAL_INDEX.md) (23 cumulative) |

## 1. Result: the 2× trend rule beat SPY under the registered rule

| | `spy-trend200` | **`sso-trend200`** | `upro-trend200` | `gem` | SPY (same $1000) |
|---|---|---|---|---|---|
| $1000 became | $2,030 | **$3,461** | $5,079 | $1,796 | $2,841 |
| Growth a year (CAGR) | +10.8% | **+19.7%** | +26.6% | +8.9% | +16.3% |
| Excess log growth a year vs SPY | −4.9% | **+2.9%** | +8.4% | −6.7% | — |
| Maximum drawdown | −17.7% | **−35.8%** | −49.4% | −28.1% | −33.8% |
| Volatility | 11.2% | 25.1% | 37.4% | 15.4% | — |
| Rolling 12-month windows ahead of SPY | 16% | **58%** | 60% | 14% | — |
| Monthly active: HAC t · one-sided p · **Holm p** | −1.92 · 0.97 · 1.0 | 0.79 · 0.22 · **0.65** | 1.40 · 0.08 · 0.32 | −2.21 · 0.99 · 1.0 | — |
| Trades a year · cost paid a year | 10.7 · $6.35 | 10.9 · $8.22 | 10.7 · $9.62 | 3.3 · $1.96 | — |
| Excess at 0 / 20 bps a side | −4.6% / −7.1% | +3.3% / +1.3% | +9.0% / +6.8% | −6.5% / −6.9% | — |
| Disposition | DOES_NOT_BEAT | **BEATS_SPY_ON_DEV** | DOES_NOT_BEAT (drawdown) | DOES_NOT_BEAT | — |

**Decision under the registered rule:** `sso-trend200` is the only candidate that beat SPY's growth with a maximum drawdown no more than 10 points worse (−35.8% against −33.8%). It is **ADOPTED** for the owner's alerts.

`upro-trend200` grew faster, but its −49.4% drawdown is more than 10 points worse than SPY's, so the registered rule excludes it. The rule is not changed after the run.

**Calendar years** (`sso-trend200` vs SPY):

| Year | 2019 (from Oct 22) | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 (to Sep 23) |
|---|---|---|---|---|---|---|---|---|
| Method | +16% | +9% | **+60%** | −25% | +17% | **+43%** | +16% | +17% |
| SPY | +8% | +18% | +29% | −18% | +26% | +25% | +18% | +13% |

The rule earned its edge in steady uptrends (2021, 2024), where it held 2× exposure. It gave some back in fast reversals: in 2020 and 2023 it re-entered late after the switch to T-bills, and in 2022 it whipsawed in and out of a falling market.

## 2. Disclosed with the result

- **P2-1 (DEV contamination and the family choice).** The rules and parameters were published in 2014 and 2016, before the window, and nothing was fitted. But choosing to test leveraged trend rules was made after personal-study-1 failed, by an author who knew how 2019–2026 unfolded. That is a researcher's degree of freedom, and it favors leverage in a window this bullish. **The pass is weak evidence; the forward record is the test.**
- **P2-2 (no statistical significance).** One-sided p 0.22, Holm p 0.65 over the four candidates (and the trial index now holds 23 V3 trials). An excess of +2.9% a year over 6.9 years is well inside what chance produces at 25% volatility.
- **P2-3 (risk).**
  - **Volatility** is 25% a year, against roughly 17–20% for the index.
  - **Worst years:** the worst calendar year was 2022, at −25% against −18%.
  - **Drawdown:** it fell as far as −35.8% from a peak.
  - **Leverage:** SSO resets daily, so in choppy markets it decays relative to 2× the index.
- **P2-4 (switching).** About 5½ round trips a year.
  - **Costs:** about $8 a year at 5 bps. The excess stays positive at 20 bps a side (+1.3%).
  - **Taxes:** every exit realizes a gain or a loss, short-term in a taxable account.
- **P2-5 (adjusted prices).** Whole shares were sized on split- and dividend-adjusted prices, so leftover cash is approximate for early dates. Mean cash was 1.2%.
- **P2-6 (what changes).** The alerts switch to `sso-trend200`, labelled with this result. `sector-mom-3` stays recorded as an EXPERIMENT that did not beat SPY. No parameter of any rule is changed, and a new candidate needs a new plan id.
