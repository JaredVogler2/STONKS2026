# Personal method study `personal-study-4` (`personal-study-4-2026-10-28`): reviewer notes

| | |
|---|---|
| Run | [40619097607](https://github.com/JaredVogler2/STONKS2026/actions/runs/40619097607), `apex-discovery-v3` with `plan = personal-study-4`, manual dispatch on `main`, 2026-10-28 02:07 UTC |
| Code | `4700f1b` (the [JaredVogler2/STONKS2026#50](https://github.com/JaredVogler2/STONKS2026/pull/50) merge). Plan sha256 `ea525e61…d333` equals the [registration](../../../docs/discovery-v3/PERSONAL_STUDY_4.md), merged before any method was computed on this data |
| Data | The Kenneth R. French Data Library's daily factors file, one download. Created from the 202609 CRSP database; 178,326 bytes, sha256 `96c23b88…9c89`. It holds 26,338 daily rows, 1926-07-01 → 2026-09-30. The worst market day is −17.4% and the best +15.7%. The file is neither stored nor published |
| Window | First decision 1927-05-05 (after 252 sessions of history), last session 2026-09-30: 99.4 years. `KNOWN_HISTORY`, tier `ROBUSTNESS_REPLICATION` |
| Artifact | `apex-discovery-40619097607-1`, digest `sha256:65899c15…ad24`. The three files here are byte-for-byte copies |
| Kind | A **robustness replication**: the S&P 500 timing methods of studies 2 and 3, unchanged, on a century of history. No new trial is counted |

## 1. Result: only the adopted 2× trend rule holds up across the century

| Method | $1000 became | Market → | Growth / yr | Excess / yr | Decades ahead | Max drawdown (market) | Worst day | Ahead in 10-year windows | Excess after publication | Disposition |
|---|---|---|---|---|---|---|---|---|---|---|
| **`sso-trend200`** (adopted) | **$1.11 billion** | $13.8 million | **+15.0%** | **+4.4%** | **8 of 9** | **−68%** (−84%) | −35% | **84%** | **+2.8% since 2017** | **HOLDS_UP** |
| `sso-hold` | $109 million | $13.8 million | +12.4% | +2.1% | 6 of 9 | −98% (−84%) | −35% | 74% | +6.7% since 2017 | HOLDS_UP_PARTLY (drawdown) |
| `voltarget-sso` | $40.1 million | $13.8 million | +11.3% | +1.1% | 6 of 9 | −68% (−84%) | −15% | 64% | −3.2% since 2018 | HOLDS_UP_PARTLY (post-publication) |
| `halloween-sso` | $37.0 million | $13.8 million | +11.2% | +1.0% | 7 of 9 | −88% (−84%) | −24% | 76% | −0.9% since 2003 | HOLDS_UP_PARTLY (post-publication) |
| `tom-sso` | $15.0 million | $13.8 million | +10.2% | +0.1% | 4 of 9 | −54% (−84%) | −19% | 47% | −5.0% since 1989 | HOLDS_UP_PARTLY (four tests) |
| `spy-trend200` (1× twin) | $11.6 million | $13.8 million | +9.9% | −0.2% | 5 of 9 | −41% (−84%) | −17% | 32% | −3.3% since 2017 | DOES_NOT_HOLD_UP |
| `rsi2-sso` | $83,362 | $13.8 million | +4.6% | −5.1% | 3 of 9 | −57% (−84%) | −35% | 21% | −7.4% since 2009 | DOES_NOT_HOLD_UP |

**What it shows:**
- **`sso-trend200` passes all six registered tests.** It was ahead of the market in every complete decade but the 2010s, where it trailed by 0.9% a year. It was furthest ahead in the bad decades:
  - **1930s:** +63% while the market lost 5%;
  - **2000s:** +20% while the market lost 5%.
  
  Over 99 years its monthly excess has a Newey–West one-sided p of 0.0005. It is a reference here, so no Holm adjustment applies.
- **Leverage alone earns more than the market but can wipe an account out.** Held throughout, the 2× fund lost 98% from its 1929 peak to its 1932 low. The trend filter cut the worst drawdown to −68%, still severe.
- **The filter's own effect is protection, not return.** Unlevered, it trailed the market by 0.2% a year but halved the worst drawdown (−41%). Leverage makes the excess; the filter makes leverage survivable.
- **The timing rules faded after publication.** Volatility targeting, Halloween and the turn of the month all beat the market before their papers and trailed it after. This matches the post-publication decay of published anomalies (McLean & Pontiff 2016).
- **RSI(2) with leverage lost.** It trailed by 5.1% a year and turned over 33 times its account a year.

**Excess log growth per year over the market, by decade** (the full table is in `PERSONAL_STUDY.md`):

| | 1930s | 1940s | 1950s | 1960s | 1970s | 1980s | 1990s | 2000s | 2010s |
|---|---|---|---|---|---|---|---|---|---|
| `sso-trend200` | +5.4% | +7.9% | +8.2% | +5.2% | +2.4% | +2.1% | +2.4% | +2.3% | −0.9% |
| `sso-hold` | −11.4% | +5.3% | +12.6% | +1.9% | −3.3% | +3.3% | +8.6% | −9.4% | +8.6% |

The rule's edge has shrunk decade by decade, from +5% to +8% a year before 1970 to about +2% since, and −0.9% in the 2010s.

## 2. Disclosed with the result

- **P4-1 (known history).** The 200-day rule's own paper tested 1928–2015, so most of this century is in-sample for its authors. The out-of-sample part is 2017 on, about ten years. There it beat the market by 2.8% a year, in line with study 2's +2.9% on real fund data, but ten years is a short record.
- **P4-2 (single-day crashes).** The rule's worst day was −35% (the 2× fund on the 1987 crash, a −17% market day). The filter acts only at closes, so it cannot protect against a crash that comes from above the average.
  - **The worst calendar year** was 47 points behind the market.
  - **In its worst 10-year window** it grew 171 percentage points less than the market.
- **P4-3 (the rebuilt fund).** Before 2006 the 2× fund is a reconstruction: twice the daily market return, less the T-bill rate plus 0.50% on the borrowed half, less a 0.90% fee. Higher financing costs would lower the excess roughly point for point with the time spent in the fund.
- **P4-4 (timing).**
  - **Same close:** trading at the signal's own close gives +6.0% a year. A one-session delay therefore costs about 1.6 points a year.
  - **Next open (the alerts):** this falls between the two.
  - **Costs:** at 20 bps a side the excess is +2.7% a year. The rule switches about 5.7 times a year, so about three round trips.
- **P4-5 (index).** The CRSP index holds every US-listed stock. It moves very closely with the S&P 500 but is not the same index.
- **P4-6 (what changes).** Nothing changes automatically. With studies 2 and 3b, this is evidence for the owner's choice of direction. The alerts keep `sso-trend200`, which this replication supports, until the owner decides.
