# Discovery V3 trial index

This index lists every V3 trial on real data, meaning each registered hypothesis × policy variant. **Losers, ablations, abandoned and SOURCE_BLOCKED trials are included** (`07_STATISTICAL_DISCOVERY_AND_ANTI_OVERFIT.md` section 1).
- A batch is added only from its committed `registry.jsonl` and `TRIAL_INDEX.md`, after a real `apex-discovery-v3` run and a reviewed PR.
- Fixture (DEMO) runs are never listed here.

Protocol: [docs/discovery-v3/RESEARCH_PROTOCOL.md](../../docs/discovery-v3/RESEARCH_PROTOCOL.md). Objective: [docs/discovery-v3/PORTFOLIO_OBJECTIVE.md](../../docs/discovery-v3/PORTFOLIO_OBJECTIVE.md) (`obj-v3-1`).

| Batch | Run | Code | Plan (sha256) | Trials | Dispositions |
|---|---|---|---|---|---|
| [`v3-pilot-1-2026-10-23`](v3-pilot-1-2026-10-23/) ([review](v3-pilot-1-2026-10-23/REVIEW.md), [decision report](v3-pilot-1-2026-10-23/DECISION_REPORT.md)) | [39884381394](https://github.com/JaredVogler2/STONKS2026/actions/runs/39884381394), 2026-10-23 | `dddf352` | `v3-pilot-1` (`11d0af37…ae2c7`) | 10 | **10 REJECTED_DEV.** Aggregate NO_TRADE; nothing nominated |
| [`v3-pilot-2-2026-10-23`](v3-pilot-2-2026-10-23/) ([review](v3-pilot-2-2026-10-23/REVIEW.md), [decision report](v3-pilot-2-2026-10-23/DECISION_REPORT.md)) | [39921190552](https://github.com/JaredVogler2/STONKS2026/actions/runs/39921190552), 2026-10-23 | `ed8c8e5` | `v3-pilot-2` (`5a2cc40c…8c0e`), `obj-v3-2`, `cost-v3-2` | 7 | **5 REJECTED_DEV, 2 INCONCLUSIVE_DEV** (G2 quarterly buffered momentum, +8.6 pp/yr vs SPY, Holm 0.62; risk-loaded, survivorship-exposed). Aggregate NO_TRADE; nothing nominated |
| [`personal-study-1-2026-10-27`](personal-study-1-2026-10-27/) ([review](personal-study-1-2026-10-27/REVIEW.md)) | [40604191780](https://github.com/JaredVogler2/STONKS2026/actions/runs/40604191780), 2026-10-27 | `e81e561` | `personal-study-1` (`89bd5a40…6aba`) | 2 | **2 DOES_NOT_BEAT_SPY_ON_DEV** (best: sector-mom-3, −4.0 pp/yr vs SPY, Holm 1.0). Runs only as a labelled EXPERIMENT in the owner's personal alerts |
| [`personal-study-2-2026-10-28`](personal-study-2-2026-10-28/) ([review](personal-study-2-2026-10-28/REVIEW.md)) | [40607489452](https://github.com/JaredVogler2/STONKS2026/actions/runs/40607489452), 2026-10-28 | `94b7097` | `personal-study-2` (`1bbef24b…d6f3`) | 4 | **1 BEATS_SPY_ON_DEV** (sso-trend200, +2.9 pp/yr vs SPY on total returns, drawdown −35.8% vs −33.8%, Holm 0.65), **3 DOES_NOT_BEAT_SPY_ON_DEV** (upro-trend200 on drawdown). Adopted for the owner's personal alerts; weak evidence (DEV, chosen after study 1) |
| `personal-study-3` (stopped, [doc §11](../../docs/discovery-v3/PERSONAL_STUDY_3.md)) | [40616902467](https://github.com/JaredVogler2/STONKS2026/actions/runs/40616902467), 2026-10-28 | `dd343be` | `personal-study-3` (`4b67f1db…cac0`) | 0 | **SOURCE_INVALID before any evaluation**: SPLG's bars fell short of the 99% coverage gate (its ticker became SPYM on 2025-10-31). Nothing simulated; superseded by `personal-study-3b` |
| [`personal-study-4-2026-10-28`](personal-study-4-2026-10-28/) ([review](personal-study-4-2026-10-28/REVIEW.md)) | [40619097607](https://github.com/JaredVogler2/STONKS2026/actions/runs/40619097607), 2026-10-28 | `4700f1b` | `personal-study-4` (`ea525e61…d333`) | 0 (replication) | A century replication (French daily market data, 1927–2026) of the S&P 500 timing methods: **sso-trend200 HOLDS_UP** (+4.4 pp/yr over the market, 8 of 9 decades, drawdown −68% vs −84%, +2.8 pp/yr after publication); sso-hold, voltarget-sso, halloween-sso, tom-sso HOLDS_UP_PARTLY; spy-trend200, rsi2-sso DOES_NOT_HOLD_UP. Not new trials |

**Cumulative real-data V3 trials:** 23. By family: f1-leader-continuation 3, f5-cross-sectional-momentum 3, f2-opportunity-state 2, f9-regime-abstention 2, g1-leader-execution-timing 2, g2-cross-sectional-momentum-low-turnover 3, g3-opportunity-state-close 2, p1-sector-etf-momentum-whole-shares 2, p2-published-trend-and-dual-momentum 4. Measurements (not trials): `cost-study-1-2026-10-23`, `design-study-1-2026-10-23`, `design-study-2-2026-10-25`. Replications (not trials): `personal-study-4-2026-10-28`.

## `v3-pilot-1-2026-10-23`

- **Data:** DEV 2019-10-23 → 2026-09-18 (`DEV_CONTAMINATED`), U1 survivors, dataset `0339c815…651f`.
- **Costs:** 25 bps per side (5 bps for SPY-only agents).
- **Holm:** over all 10 trials.
- **Benchmark:** SPY buy-and-hold over the same sessions earned +13.5% a year (log growth).

| Trial | Hypothesis | Policy | Axis | Disposition | Active log growth / yr vs comparator | One-sided p | Holm p | Looks |
|---|---|---|---|---|---|---|---|---|
| `t-83b3d60e088e7993` | f1-leader-idle-capital | f1-s1-idle-spy | primary | REJECTED_DEV | −0.0664 vs spy_hold | 0.988 | 1.0 | 1 |
| `t-2bbcf8f194806c29` | f1-leader-idle-capital | f1-s1-idle-cash | idle_capital | REJECTED_DEV | −0.0673 vs exposure_matched | 0.988 | 1.0 | 1 |
| `t-83418bcfda96a241` | f1-leader-idle-capital | f1-s1-idle-spy-h40 | exit | REJECTED_DEV | −0.0801 vs spy_hold | 0.979 | 1.0 | 1 |
| `t-7220dd98b21957a9` | f5-xs-momentum-monthly | f5-momentum-ew-monthly | primary | REJECTED_DEV | −0.0194 vs spy_hold | 0.504 | 1.0 | 1 |
| `t-5f309a6243743f91` | f5-xs-momentum-monthly | f5-random-ew-monthly | selection | REJECTED_DEV | −0.1505 vs spy_hold | 0.997 | 1.0 | 1 |
| `t-854c27e4761dbc3e` | f5-xs-momentum-monthly | f5-momentum-ew-monthly-abstain | market_filter | REJECTED_DEV | −0.0330 vs spy_hold | 0.574 | 1.0 | 1 |
| `t-d09c60af8417670e` | f2-dispersion-gated-swing | f2-momentum-swing-dispersion-gate | primary | REJECTED_DEV | −0.0136 vs spy_hold | 0.614 | 1.0 | 1 |
| `t-bc6830608ddd6d9d` | f2-dispersion-gated-swing | f2-momentum-swing-ungated | market_filter | REJECTED_DEV | −0.0757 vs spy_hold | 0.960 | 1.0 | 1 |
| `t-568acbd05a5373ae` | f9-regime-abstention | f9-spy-regime-abstain | primary | REJECTED_DEV | −0.0250 vs spy_hold | 0.949 | 1.0 | 1 |
| `t-700823083c6db9d0` | f9-regime-abstention | f9-spy-shock-only | market_filter | REJECTED_DEV | −0.0104 vs spy_hold | 0.965 | 1.0 | 1 |

**Disclosed after the run** (no disposition changes; [REVIEW.md §3](v3-pilot-1-2026-10-23/REVIEW.md)):
- **A-1:** the selection agents inherit protocol-2's risk-off breaker and extension cap.
- **O-1:** the −30% drawdown floor is tighter than SPY's own −34.2%.
- **A-2:** SPY sweep trades count toward turnover.
- **R-1:** the random ablation is a single path.

## `v3-pilot-2-2026-10-23`

- **Data:** the same DEV window and dataset as pilot 1, **contaminated twice**.
- **Costs:** the measured cost model `cost-v3-2` (open 21 bps, close 2, stop exits 25 assumed).
- **Objective:** `obj-v3-2`.
- **Holm:** over the 7 trials.
- **Benchmark:** SPY +13.5% a year.

| Trial | Hypothesis | Policy | Axis | Disposition | Active log growth / yr vs SPY | One-sided p | Holm p | Looks |
|---|---|---|---|---|---|---|---|---|
| `t-8a482bf362c5f2e0` | g1-s1-close-execution | g1-s1-close-spy-idle | primary | REJECTED_DEV | −0.0352 | 0.927 | 1.0 | 1 |
| `t-e094bc6c63e4a766` | g1-s1-close-execution | g1-s1-open-spy-idle | entry | REJECTED_DEV | −0.0534 | 0.966 | 1.0 | 1 |
| `t-cf2725f130cf882a` | g2-low-turnover-momentum | g2-momentum-quarterly-buffered-close | primary | INCONCLUSIVE_DEV | +0.0862 | 0.092 | 0.623 | 1 |
| `t-14060656d1f3c401` | g2-low-turnover-momentum | g2-momentum-quarterly-buffered-open | entry | INCONCLUSIVE_DEV | +0.0866 | 0.089 | 0.623 | 1 |
| `t-d7e00ad1ae7c87c0` | g2-low-turnover-momentum | g2-momentum-monthly-unbuffered-close | exit | REJECTED_DEV (drawdown) | +0.0335 | 0.289 | 1.0 | 1 |
| `t-87814b11e0e9d9fd` | g3-dispersion-gated-close | g3-momentum-swing-dispersion-gate-close | primary | REJECTED_DEV | −0.0292 | 0.822 | 1.0 | 1 |
| `t-7825e3a20df7b343` | g3-dispersion-gated-close | g3-momentum-swing-ungated-close | market_filter | REJECTED_DEV | −0.0388 | 0.831 | 1.0 | 1 |

**G2's selection test** (zero cost, against 20 random buffered books): +11.4 pp a year, p 0.074. It beat all 20 books.

**Disclosed with the result** ([REVIEW.md §3](v3-pilot-2-2026-10-23/REVIEW.md)):
- **P2-1:** survivorship × momentum is not removed by the control.
- **P2-2:** beta 1.2 and volatility 35%; risk-adjusted below SPY.
- **P2-3:** timing is irrelevant at low turnover.
- **P2-4:** the gain decomposes into the mask and costs plus the buffer.
- **P2-5:** S1's selection value is partly earned on the entry day.
- **P2-6:** forking paths.

## `personal-study-1-2026-10-27`

- **Data:** the same DEV window as the pilots (first decision 2019-10-22, last session 2026-09-23), Alpaca SIP daily bars, split-adjusted, dataset `602147ab…a6`; only SPY and the 11 sector ETFs are read.
- **Simulation:** $1000, whole shares, decisions at month-end closes, fills at the next open, 5 bps a side.
- **Holm:** over the 2 trials.
- **Benchmark:** SPY bought with the same $1000 at the first fill open, fractional, frictionless: +14.7% a year (CAGR), price returns.

| Trial | Method | Disposition | Excess log growth / yr vs SPY | Max drawdown (SPY −34.2%) | One-sided p | Holm p |
|---|---|---|---|---|---|---|
| `personal-study-1/sector-mom-3` | top 3 sector ETFs by 126-session return, monthly, kept while in the top 5 | DOES_NOT_BEAT_SPY_ON_DEV | −0.0403 | −35.4% | 0.877 | 1.0 |
| `personal-study-1/sector-mom-3-abs` | the same, positive 126-session return required, else cash | DOES_NOT_BEAT_SPY_ON_DEV | −0.0862 | −35.4% | 0.983 | 1.0 |

**Disclosed with the result** ([REVIEW.md §2](personal-study-1-2026-10-27/REVIEW.md)): P1-1 DEV contamination; P1-2 price returns; P1-3 whole-share sizing on split-adjusted prices; P1-4 nothing tuned after the run.

## `personal-study-2-2026-10-28`

- **Data:** personal-study-1's DEV window (first decision 2019-10-22, last session 2026-09-23); Alpaca SIP daily bars for SPY, SSO, UPRO, EFA, AGG and BIL, adjusted for splits **and dividends** (total returns, SPY's too); dataset `21971aa5…ecc`.
- **Simulation:** $1000, whole shares, one asset at a time. The trend rules are checked at every close; switches fill at the next open, 5 bps a side.
- **Holm:** over the 4 trials.
- **Benchmark:** SPY bought with the same $1000 at the first fill open, fractional, frictionless: +16.3% a year (CAGR), total return.

| Trial | Rule (published) | Disposition | Excess log growth / yr vs SPY | Max drawdown (SPY −33.8%) | One-sided p | Holm p |
|---|---|---|---|---|---|---|
| `personal-study-2/spy-trend200` | SPY above its 200-day average, else BIL (Gayed & Bilello 2016) | DOES_NOT_BEAT_SPY_ON_DEV | −0.0487 | −17.7% | 0.972 | 1.0 |
| `personal-study-2/sso-trend200` | SSO (2×) above the average, else BIL (Gayed & Bilello 2016) | **BEATS_SPY_ON_DEV** | **+0.0286** | −35.8% | 0.216 | 0.647 |
| `personal-study-2/upro-trend200` | UPRO (3×) above the average, else BIL (Gayed & Bilello 2016) | DOES_NOT_BEAT_SPY_ON_DEV (drawdown) | +0.0843 | −49.4% | 0.080 | 0.321 |
| `personal-study-2/gem` | Global Equities Momentum: SPY/EFA vs BIL, else AGG (Antonacci 2014) | DOES_NOT_BEAT_SPY_ON_DEV | −0.0665 | −28.1% | 0.986 | 1.0 |

**Disclosed with the result** ([REVIEW.md §2](personal-study-2-2026-10-28/REVIEW.md)): P2-1 DEV contamination and a family chosen after study 1; P2-2 no statistical significance; P2-3 risk (25% volatility, 2× daily-reset fund); P2-4 switching costs and taxes; P2-5 adjusted-price sizing; P2-6 nothing tuned after the run.

## `personal-study-4-2026-10-28` (replication, not trials)

- **Data:** the Kenneth R. French Data Library's daily factors file (created from the 202609 CRSP database, sha256 `96c23b88…9c89`): the US market's daily total return and the T-bill return, 1926-07-01 → 2026-09-30. Downloaded once; not stored or published.
- **Funds rebuilt:** 1× (market less 0.09%), daily-reset 2× (2 × market − T-bill − 0.50% − 0.90% a year), T-bills (less 0.14%).
- **Simulation:** decided at a close, traded at the next close, 5 bps a side; continuous weights; first decision 1927-05-05.
- **Holm:** over the 5 replications (references excluded).
- **Benchmark:** the 1× fund from the first fill: +10.1% a year (CAGR) over 99.4 years.

| Method | Role | Disposition | Excess log growth / yr | Decades ahead (of 9) | Max drawdown (market −84%) | After publication | One-sided p | Holm p |
|---|---|---|---|---|---|---|---|---|
| `sso-trend200` | reference | **HOLDS_UP** | **+0.0441** | 8 | −68% | +0.0283 since 2017 | 0.0005 | — |
| `spy-trend200` | reference | DOES_NOT_HOLD_UP | −0.0017 | 5 | −41% | −0.0330 since 2017 | 0.803 | — |
| `sso-hold` | replication | HOLDS_UP_PARTLY (drawdown) | +0.0208 | 6 | −98% | +0.0667 since 2017 | 0.0001 | 0.0006 |
| `voltarget-sso` | replication | HOLDS_UP_PARTLY (post-publication) | +0.0107 | 6 | −68% | −0.0324 since 2018 | 0.103 | 0.402 |
| `halloween-sso` | replication | HOLDS_UP_PARTLY (post-publication) | +0.0099 | 7 | −88% | −0.0092 since 2003 | 0.101 | 0.402 |
| `tom-sso` | replication | HOLDS_UP_PARTLY (four tests) | +0.0009 | 4 | −54% | −0.0503 since 1989 | 0.587 | 1.0 |
| `rsi2-sso` | replication | DOES_NOT_HOLD_UP | −0.0514 | 3 | −57% | −0.0738 since 2009 | 1.000 | 1.0 |

**Disclosed with the result** ([REVIEW.md §2](personal-study-4-2026-10-28/REVIEW.md)): P4-1 known history (the 200-day rule's paper tested 1928–2015); P4-2 single-day crashes (−35% on the 2× fund in 1987); P4-3 the rebuilt 2× fund's financing assumption; P4-4 timing (+6.0% at the signal's own close) and costs (+2.7% at 20 bps); P4-5 the CRSP index is not the S&P 500; P4-6 nothing changes automatically.
