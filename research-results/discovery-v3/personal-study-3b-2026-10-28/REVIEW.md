# Personal method study `personal-study-3b` (`personal-study-3b-2026-10-28`): reviewer notes

| | |
|---|---|
| Run | [40621112174](https://github.com/JaredVogler2/STONKS2026/actions/runs/40621112174), `apex-discovery-v3` with `plan = personal-study-3b`, manual dispatch on `main`, 2026-10-28 02:28 UTC |
| Code | `8265b55` (the [JaredVogler2/STONKS2026#51](https://github.com/JaredVogler2/STONKS2026/pull/51) merge). Plan sha256 `7c94e4c7…cf86` equals the [registration](../../../docs/discovery-v3/PERSONAL_STUDY_3.md). The registration was amended before any evaluation (§11 there): SPLG became SPYM, and the twin check was corrected |
| Data | Alpaca historical **SIP** daily bars for 17 ETFs, adjusted for **splits and dividends** (total returns, SPY's too). 2016-01-04 → 2026-10-23 after the finality lag: 2,718 sessions each, none missing or invalid, 5 provider requests. Dataset `50e0381b…5fb3` |
| Twin check | SPYM, mapped through its rename, against SPY: **consistent**. Daily correlation 0.987; largest 21-session drift 0.76 points (limit 1.5). The largest daily gaps came in March 2020 and reversed within a session or two (03-12 −3.0, 03-13 +3.2, 03-18 +2.5, 03-19 −1.9 points). None was near the 2025-10-31 rename |
| Windows | FULL: first decision 2017-01-31, last session 2026-09-23, in halves split at 2021-11-22. DEV: from 2019-10-22. `DEV_CONTAMINATED`, tier `DEV_DIAGNOSTIC` |
| Artifact | `apex-discovery-40621112174-1`, digest `sha256:111634b0…e55c`. The three files here are byte-for-byte copies |
| Kind | A **method comparison** for the owner's alerts. Ten trials, added to the [trial index](../TRIAL_INDEX.md) (33 cumulative); `sso-trend200` is a reference |

## 1. Result: no method beat SPY repeatedly; leverage won the decade, and only three methods beat SPY at all

SPY turned the same $1000 into **$3,899** (+15.2% a year) over the FULL window.

| Method | $1000 → | Growth / yr | Excess / yr FULL | 1st half | 2nd half | DEV | Max drawdown (SPY −34%) | Ahead in 36-month spans | 90% bootstrap, excess / yr | At 20 bps | Trades / yr | Holm p | Disposition |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `sso-hold` | **$7,463** | +23.2% | **+6.8%** | +10.3% | +3.1% | +6.7% | **−59%** | 94% | +4.4% to +16.8% | +6.7% | 0.1 | 0.057 | BEATS_SPY_PARTLY (drawdown) |
| `mtum-hold` | $4,452 | +16.8% | +1.4% | +2.5% | −0.7% | +0.2% | −34% | 38% | −3.1% to +6.8% | +1.4% | 0.1 | 1.0 | BEATS_SPY_PARTLY |
| `sso-trend200` (reference) | $4,448 | +16.8% | +1.4% | +2.6% | −0.2% | +2.9% | −36% | 60% | −4.7% to +10.3% | −0.2% | 10.6 | — | BEATS_SPY_PARTLY (2nd half, 20 bps) |
| `hfea` | $3,371 | +13.5% | −1.5% | +15.9% | −15.5% | −5.3% | −69% | 38% | −9.0% to +14.4% | −1.5% | 6.4 | 1.0 | DOES_NOT_BEAT_SPY |
| `voltarget-sso` | $3,368 | +13.5% | −1.5% | −2.9% | −0.5% | −2.7% | −26% | 25% | −5.8% to +2.7% | −2.1% | 20.2 | 1.0 | DOES_NOT_BEAT_SPY |
| `adm` | $2,668 | +10.7% | −3.9% | −1.3% | −6.7% | −5.7% | −35% | 1% | −8.3% to +0.2% | −5.1% | 7.6 | 1.0 | DOES_NOT_BEAT_SPY |
| `halloween-sso` | $2,240 | +8.7% | −5.8% | −4.7% | −7.1% | −7.8% | −59% | 16% | −11.2% to +3.3% | −6.3% | 4.5 | 1.0 | DOES_NOT_BEAT_SPY |
| `tom-sso` | $1,831 | +6.5% | −7.9% | −8.1% | −7.7% | −4.9% | −21% | 25% | −15.2% to −0.7% | −14.9% | 50.4 | 1.0 | DOES_NOT_BEAT_SPY |
| `gtaa5` | $1,747 | +6.0% | −8.3% | −9.9% | −7.2% | −8.3% | −7% | 0% | −14.1% to −4.4% | −8.9% | 21.4 | 1.0 | DOES_NOT_BEAT_SPY |
| `vaa-g4` | $1,744 | +6.0% | −8.4% | −7.5% | −9.4% | −10.2% | −17% | 1% | −14.3% to −3.8% | −10.6% | 16.0 | 1.0 | DOES_NOT_BEAT_SPY |
| `rsi2-sso` | $1,727 | +5.8% | −8.5% | −12.6% | −4.4% | −5.4% | −27% | 5% | −14.8% to −3.3% | −13.6% | 35.8 | 1.0 | DOES_NOT_BEAT_SPY |

**Decision under the registered rule:** no method passed all six tests, so there is **no recommended switch**. The alerts keep `sso-trend200`, and the choice of direction stays with the owner.

**What it shows:**
- **Leverage won this decade.** The 2× fund held throughout beat SPY by 6.8% a year. It was ahead in both halves and in 94% of 36-month spans. Its bootstrap interval excludes zero, and its Holm p of 0.057 was the closest to significance. It failed one test, the drawdown: −59% in the 2020 crash, against SPY's −34%. Over the century (`personal-study-4`) the same holding lost 98% in 1929–32.
- **The trend filter's edge was small this decade.** `sso-trend200` beat SPY by 1.4% a year over FULL and by 2.9% over DEV, matching study 2.
  - **What it failed:** the second half (−0.2%) and the 20 bps stress (−0.2%).
  - **Why:** the filter whipsawed in late 2018, 2020 and 2022–23 and gave back part of leverage's gain.
  - **What it bought:** a drawdown about equal to SPY's (−36%), against −59% held unfiltered.
- **Momentum (MTUM) was a coin flip.** It was ahead overall (+1.4%), behind in the second half and ahead in only 38% of 36-month spans.
- **The defensive rules did their job and paid for it.** GTAA (−7%), VAA (−17%) and volatility targeting (−26%) cut drawdowns sharply, but trailed SPY by 1.5–8.4% a year in a decade led by US stocks.
- **HFEA swung hardest.** It was +15.9% a year ahead in the first half, then −15.5% a year in the second, after its 2022 loss of 63% when stocks and long bonds fell together.
- **Calendar and mean-reversion rules lost.** Halloween, the turn of the month and RSI(2) trailed by 6–9% a year. The last two trade 36–50 times a year: at 20 bps the turn of the month loses 14.9% a year.

**Calendar years** (`sso-trend200` | `sso-hold` | SPY):

| | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 (to 09-23) |
|---|---|---|---|---|---|---|---|---|---|---|
| `sso-trend200` | +39% | −14% | +26% | +9% | +60% | −25% | +18% | +43% | +16% | +17% |
| `sso-hold` | +39% | −15% | +63% | +22% | +60% | −39% | +47% | +43% | +26% | +22% |
| SPY | +19% | −5% | +31% | +18% | +29% | −18% | +26% | +25% | +18% | +13% |

## 2. Read with `personal-study-4` (the century)

| | `sso-trend200` | `sso-hold` |
|---|---|---|
| 2017–2026, real funds, whole shares | +1.4% a year over SPY, drawdown −36% (SPY −34%) | +6.8% a year, drawdown −59% |
| 1927–2026, rebuilt funds | **+4.4% a year, ahead in 8 of 9 decades, drawdown −68% (market −84%): HOLDS_UP** | +2.1% a year, drawdown −98%: fails the drawdown test |

Across both studies, only one method beat the index over each whole window with a drawdown near or better than the index's: the adopted 2× trend rule. Its edge was smaller in the recent decade than in the century. Five of the ten new methods could be rebuilt over the century, and none of them held up (`personal-study-4`).

## 3. Disclosed with the result

- **P3-1 (DEV contamination).** The author knew this decade favored leveraged US stocks and had seen study 2's result. The rules and parameters are the publications', and every one is published here, losers included. The forward record of the alerts is the test.
- **P3-2 (stops and a correction before evaluation).** `personal-study-3` stopped at the data check: SPLG became SPYM. The first 3b run stopped at the first version of the twin check. The correction was made before anything was simulated (doc §11). This run's evidence shows the original stop came from the March 2020 dislocations, not from a splice.
- **P3-3 (multiple testing).** Holm runs over the ten trials, and the index now holds 33 V3 trials. No method is significant after Holm. `sso-hold` comes closest (0.057), and it fails the drawdown test.
- **P3-4 (overlap).** The two halves share no day. The DEV window and the rolling spans overlap the FULL window.
- **P3-5 (whole shares).** $1000 in whole shares left little idle cash: at most 10% on average (`gtaa5`), under 5% for the rest. Sizing uses adjusted prices, so leftover cash is approximate for early years.
- **P3-6 (what changes).** Nothing changes automatically: the registered rule recommends no switch. The alerts keep `sso-trend200`, and the owner chooses the direction.
