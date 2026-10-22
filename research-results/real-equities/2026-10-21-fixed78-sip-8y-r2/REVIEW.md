# Reviewer notes — `2026-10-21-fixed78-sip-8y-r2`

This file is a reviewer's QA note and is **not generated**. The other seven files are the unmodified artifact of the run.

## Provenance (verified)

- **Run:** [GitHub Actions run 39696547618](https://github.com/JaredVogler2/STONKS2026/actions/runs/39696547618)
  - job 119555196116
  - `workflow_dispatch` on `main` at `2acad29`
  - 2026-10-21 23:55:54–23:57:22Z
  - conclusion **success**
- **Artifact:** `apex-realeq-39696547618-1` (id 12390019748).
  - The zip's sha256 `270c88959b8984d276da3f5573b1b7f9da1cf76366c868112ce6894a5099edab` matches the digest GitHub reports.
  - The archive held exactly these seven files.
- **Source:** Alpaca historical **SIP** daily bars, split-adjusted.
  - 16 provider requests
  - 156,773 raw rows
  - 78 of 78 symbols usable, including all 66 traded stocks
  - window 2018-10-22 → 2026-10-21
- **Hashes:**
  - dataset `f428e39e…`
  - history `18d9678f…`
  - `manifest.json` sha256 `ac30ce8b338da3d13e620d6e6f7eab3cb4b898ef8242e555ff1d9e12f14beb08`. It equals `strategy_metrics.json.manifest_sha256`, and the manifest was frozen at 23:56:23Z, before any metric was computed.
- **Protocol:** `realeq-protocol-1` (sha256 `721d3e3e…`), unchanged.
- **Hygiene:**
  - no price, quote or volume fields;
  - no per-trade rows;
  - no NaN;
  - no credentials. The execution log records only that the two keys were present.

## Data quality

- **Raw provider data:** 0 invalid, duplicate or future-dated rows. There are no missing or stale sessions and no zero-volume days.
- **Corporate actions:** available. Split continuity is **ok** for every split checked (AAPL, NVDA ×2, AVGO, AMZN, GOOGL, WMT, NEE, SHW, GE, NFLX, and the five sector ETFs on 2025-12-05), with one exception, HON.
- **HON 2026-06-29:**
  - The provider lists a split on that date, but the split-adjusted close moves more than 25%. Its continuity is `suspect`.
  - This is most likely a separation or distribution recorded as a split.
  - Any trade holding HON across 2026-06-29, which falls inside the holdout, may carry a false return.
  - It is one symbol on one date. It cannot change the aggregate conclusion, but it is the first data item to fix.
- **EXC 2022-02-02:** the large move matches the Constellation spin-off. This is the documented *spin-off* bias of split-only adjustment.
- **Other flagged moves are real market events:** COVID March 2020 (COP, SLB, EOG, FCX, SPG), the 2020-11-09 vaccine rally (SPG), NFLX 2022-04-20 and ORCL 2025-09-10.

## Results at the primary configuration (H = 20 sessions, 25 bps per side, untouched holdout 2025-05-02 → 2026-09-21)

**Aggregate: NO_TRADE.** No hypothesis is close to `VALIDATED_REAL_OOS`.

| Hypothesis | Disposition | What drives it |
|---|---|---|
| H8 turn-of-month | **FAILED_REAL_OOS** | n=140 and mean +0.13%, which is below the matched-random median of +0.65% (16th percentile vs random). Only 15 decision weeks, all in one regime. Holm p = 1.0. |
| H5 sector rotation | UNVALIDATED (inconclusive) | n=225 with mean +0.03% (p10 −0.55%), the 52nd percentile vs random. Positive at 0 bps (+0.54%) but negative at 50 bps. Sector groups disagree (A +0.82%, B −1.23%). Development mean was −0.12%. |
| H2 RS pullback | INSUFFICIENT_HISTORY | 98 development trades, just under the frozen 100. Holdout n=39, mean −0.04%. |
| H1, H4, H6 | INSUFFICIENT_HISTORY | 16 / 7 / 11 holdout trades. Every point estimate is negative. |
| H3, H7 | NOT_EVALUABLE_MISSING_INPUT | No point-in-time catalyst history, so no events. This is not a failure. |

**Cross-cutting observations**
- **The SPY comparison dominates.** SPY rose +36.4% over the holdout (CAGR 23.6%, max drawdown −9.1%). Every rule's trades lagged SPY over their *own* holding windows: the mean net excess was −0.70% to −3.43% per trade, and only 27–44% of trades beat SPY. The 10-slot books made between −2.3% and +3.6% at 3–71% exposure. These comparisons do not mean any rule beat SPY on risk.
- **Costs decide the sign.** Gross (0 bps) means are positive for H1, H2, H5 and H8, at +0.36% to +0.63% per trade. At 25 bps they shrink to between −0.14% and +0.13%, and at 50 bps all are negative. With a 2R target, the target-before-stop rate is about 34% for H5 and H8, which is close to gross breakeven.
- **Execution mechanics.**
  - Same-bar target+stop ambiguity is 0% across all hypotheses, so stop-first resolution does not drive any result.
  - Fill rates are 90–100%.
  - Gaps through the stop occur in 0–17 trades per hypothesis.
- **Quality ranking.**
  - No signal for H5 (ρ 0.00).
  - A positive holdout correlation for H8 (ρ 0.25, 90% CI [0.06, 0.39]) and H6 (n=11), but not in development.
  - With five tests, treat the H8 result as a hypothesis for the next protocol version, not as a finding.
- **Forecast model.**
  - Only H5 and H8 had enough development events to fit a model, and neither shows Brier skill on the holdout (−0.013 and −0.118; permutation p 0.52 and 0.95).
  - Probability forecasts stay hidden.
- **Baselines.**
  - `momentum_20` had the best per-trade mean (+1.19%, n=30, p10 −0.55%), which is not significant.
  - `breakout_basic` was flat at −0.02% (n=478).

## Reviewer recommendation (ranked by evidence; every change needs a new pre-registered protocol version)

1. **Data integrity.**
   - Resolve the HON 2026-06-29 adjustment.
   - Pre-register an `adjustment=all` variant (dividends, spin-offs) so price-only and distribution effects are separated.
   - Add a point-in-time earnings calendar.
2. **Sample size before tuning.** H1, H2, H4 and H6 cannot be judged with 66 survivor large caps.
   - The highest-value next experiment is a larger universe with **point-in-time constituents**. It addresses power and survivorship together.
   - Do not tune entries, stops or weights on these small samples.
3. **Mechanics and costs.** Every positive gross edge is consumed by about 50 bps round-trip costs. A pre-registered test of cost-aware entries could address this, for example skipping signals whose expected gross R does not cover costs. Wider stops alone are not supported by this run.
4. **Ranking.** Do not present Quality as a confidence proxy (H5 ρ ≈ 0). The H8 correlation is a candidate hypothesis only.
5. **Keep everything disabled.** No promotion, live or group alerts, or champion change. Retire or redesign H8 before any re-test. Do not lower any gate.
