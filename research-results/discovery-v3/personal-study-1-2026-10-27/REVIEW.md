# Personal method study `personal-study-1` (`personal-study-1-2026-10-27`): reviewer notes

| | |
|---|---|
| Run | [40604191780](https://github.com/JaredVogler2/STONKS2026/actions/runs/40604191780), `apex-discovery-v3` with `plan = personal-study-1`, manual dispatch on `main`, 2026-10-27 23:44 UTC. The research step took 3 min 33 s |
| Code | `e81e561` (the PR #46 merge). Plan sha256 `89bd5a40…6aba` equals the [registration](../../../docs/discovery-v3/PERSONAL_STUDY_1.md), which was committed (30b9d14) before any real price was read for this study |
| Data | Alpaca historical **SIP** daily bars, split-adjusted (price returns), protocol-2's path. Dataset `602147ab…a6`, data end 2026-10-23 after the finality lag, 143 provider requests. All 11 sector ETFs and SPY usable |
| Window | First decision 2019-10-22, last session 2026-09-23: 6.9 years, 84 month-end decisions, `DEV_CONTAMINATED`, tier `DEV_DIAGNOSTIC` |
| Artifact | `apex-discovery-40604191780-1`, digest `sha256:ec01edff…ff74`. The three files here are byte-for-byte copies |
| Kind | A **method choice** for the owner's personal alerts. Two trials (below), added to the [trial index](../TRIAL_INDEX.md) |

## 1. Result: neither method beat SPY

| | `sector-mom-3` | `sector-mom-3-abs` | SPY (same $1000, fractional, frictionless) |
|---|---|---|---|
| $1000 became | $1,946 | $1,419 | **$2,570** |
| Growth a year (CAGR) | +10.1% | +5.2% | **+14.7%** |
| Excess log growth a year vs SPY | **−4.0%** | **−8.6%** | — |
| Maximum drawdown | −35.4% | −35.4% | −34.2% |
| Volatility | 20.0% | 18.8% | — |
| Rolling 12-month windows ahead of SPY | 40% | 33% | — |
| Monthly active return: mean, HAC t, one-sided p, Holm p | −0.34%, −1.16, 0.88, 1.0 | −0.74%, −2.12, 0.98, 1.0 | — |
| Trades a year · cost paid | 12.6 · $2.52 | 14.4 · $2.27 | — |
| Mean cash (whole-share leftovers; `abs` also empty slots) | 3.5% | 10.5% | — |

**Disposition under the registered rule:** both `DOES_NOT_BEAT_SPY_ON_DEV`. The rule makes the better candidate, `sector-mom-3`, the personal method as an **EXPERIMENT**, and the report says plainly that no method beat the index. Cost sensitivity changes nothing: at 0 bps the excess is −3.9% and −8.4% a year.

**Calendar years** (`sector-mom-3` vs SPY):

| Year | 2019 (from Oct 22) | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 (to Sep 23) |
|---|---|---|---|---|---|---|---|---|
| Method | +0.6% | +13.4% | +15.3% | **−2.7%** | +17.0% | +23.2% | +6.9% | −1.4% |
| SPY | +7.7% | +16.3% | +26.9% | −19.5% | +24.3% | +23.3% | +16.4% | +12.6% |

The rotation earned its keep in exactly one year, 2022, when it held energy while the index fell. In every other year it trailed or tied. Holding three equal-sized sectors was no match for a cap-weighted index led by its largest companies: the method bought XLK only 6 times in 48 buys.

## 2. Disclosed with the result

- **P1-1 (DEV contamination).** These dates had been studied before, and sector momentum is a textbook rule whose history the author knew. A failure here is informative; a pass would not have been.
- **P1-2 (price returns).** Dividends are excluded for both sides (registered). Sector ETFs yield about as much as SPY on average, so this is unlikely to reverse a 4-point gap.
- **P1-3 (whole shares).** Sizing used split-adjusted prices, so whole-share leftovers are approximate where an ETF split during the window. Mean cash was 3.5%, too small to explain the gap.
- **P1-4 (what changes).** The alerts keep `sector-mom-3` only as a labelled EXPERIMENT until a registered method beats SPY. No parameter of either method is changed after this run. Any new candidate needs a new plan (`personal-study-2`), with its own registration and trial-index entries.
