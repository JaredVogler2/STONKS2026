# Discovery V3 trial index

This index lists every V3 trial on real data, meaning each registered hypothesis × policy variant. **Losers, ablations, abandoned and SOURCE_BLOCKED trials are included** (`07_STATISTICAL_DISCOVERY_AND_ANTI_OVERFIT.md` section 1).
- A batch is added only from its committed `registry.jsonl` and `TRIAL_INDEX.md`, after a real `apex-discovery-v3` run and a reviewed PR.
- Fixture (DEMO) runs are never listed here.

Protocol: [docs/discovery-v3/RESEARCH_PROTOCOL.md](../../docs/discovery-v3/RESEARCH_PROTOCOL.md). Objective: [docs/discovery-v3/PORTFOLIO_OBJECTIVE.md](../../docs/discovery-v3/PORTFOLIO_OBJECTIVE.md) (`obj-v3-1`).

| Batch | Run | Code | Plan (sha256) | Trials | Dispositions |
|---|---|---|---|---|---|
| [`v3-pilot-1-2026-10-23`](v3-pilot-1-2026-10-23/) ([review](v3-pilot-1-2026-10-23/REVIEW.md), [decision report](v3-pilot-1-2026-10-23/DECISION_REPORT.md)) | [39884381394](https://github.com/JaredVogler2/STONKS2026/actions/runs/39884381394), 2026-10-23 | `dddf352` | `v3-pilot-1` (`11d0af37…ae2c7`) | 10 | **10 REJECTED_DEV.** Aggregate NO_TRADE; nothing nominated |
| [`v3-pilot-2-2026-10-23`](v3-pilot-2-2026-10-23/) ([review](v3-pilot-2-2026-10-23/REVIEW.md), [decision report](v3-pilot-2-2026-10-23/DECISION_REPORT.md)) | [39921190552](https://github.com/JaredVogler2/STONKS2026/actions/runs/39921190552), 2026-10-23 | `ed8c8e5` | `v3-pilot-2` (`5a2cc40c…8c0e`), `obj-v3-2`, `cost-v3-2` | 7 | **5 REJECTED_DEV, 2 INCONCLUSIVE_DEV** (G2 quarterly buffered momentum, +8.6 pp/yr vs SPY, Holm 0.62; risk-loaded, survivorship-exposed). Aggregate NO_TRADE; nothing nominated |

**Cumulative real-data V3 trials:** 17. By family: f1-leader-continuation 3, f5-cross-sectional-momentum 3, f2-opportunity-state 2, f9-regime-abstention 2, g1-leader-execution-timing 2, g2-cross-sectional-momentum-low-turnover 3, g3-opportunity-state-close 2. Measurements (not trials): `cost-study-1-2026-10-23`.

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
