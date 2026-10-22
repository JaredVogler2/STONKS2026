# Adversarial review brief: APEX real-equity strategy evaluation

**Audience:** an independent coding/quant agent asked to attack these results and recommend improvements.
**Status of the evidence:** real market data, research only. The aggregate verdict is **NO_TRADE**: no hypothesis is validated.
**Prepared:** 2026-10-22, from `main` after PR #9.

Every table below is generated from the committed experiment files in [`2026-10-21-fixed78-sip-8y-r2/`](2026-10-21-fixed78-sip-8y-r2/): `strategy_metrics.json`, `trades_summary.csv`, `data_quality.json` and `manifest.json`. When this brief and those files disagree, **the files win**. Synthetic-fixture research is documented separately in [`docs/TESTING_AND_METHODOLOGY_REPORT.md`](../../docs/TESTING_AND_METHODOLOGY_REPORT.md) and must not be mixed with these results.

## 0. Your mandate as reviewer

1. **Try to break the conclusions.** Look for leakage, look-ahead, survivorship, data errors, mis-specified costs or fills, statistical errors (dependence, multiple testing, power), and code bugs. Look in both directions: a result that is wrongly negative matters as much as one that is wrongly positive.
2. **Check every claim against the code and the files.** File and line references are in §10. The pipeline reproduces offline with the DEMO fixture, and online by re-running the workflow (§11).
3. **Recommend improvements** to stock selection (universe, entry triggers, ranking) and to price levels (entry zone, stop, target, horizon, costs). Rank them by expected value of information.
4. **Respect the guardrails:**
   - Do not tune anything on the holdout window 2025-05-02 → 2026-09-21.
   - Every proposed change is a new pre-registered protocol version (`realeq-protocol-2`, …), with its frozen rules written before the run.
   - Do not lower any gate to graduate a model.
   - Never enable live, group or high-confidence alerts, model promotion or orders.
   - Never request or commit API keys or raw vendor OHLCV.

**Requested output format.** Give one entry per finding, with these fields:
- `ID`
- `Severity`: blocker, major, minor or question
- `Claim attacked`
- `Evidence`: a file:line reference, a JSON path, or a reproduction
- `Impact on the verdict`
- `Proposed fix or pre-registered experiment`, including success and failure criteria fixed in advance
- `Expected cost`: compute minutes and API requests

End with a ranked list of the next experiments.

## 1. Executive summary

**Primary configuration:** 20-session horizon, 25 bps per side, rule trades (qualified, regime-allowed and de-duplicated signals), untouched holdout **2025-05-02 → 2026-09-21** (348 sessions).

| Hypothesis | Disposition | Holdout n | Mean net | Bootstrap p10 | Win rate | Mean R | Random median | Pctl vs random | Holm p | Forecast |
|---|---|---|---|---|---|---|---|---|---|---|
| h1_vcp_breakout | **INSUFFICIENT_HISTORY** | 16 | -0.144% | -2.103% | 43.8% | 0.018 | 0.406% | 0.34 | — | UNVALIDATED |
| h2_rs_pullback | **INSUFFICIENT_HISTORY** | 39 | -0.037% | -1.491% | 38.5% | 0.022 | -0.258% | 0.62 | 1.0 | UNVALIDATED |
| h3_post_event_drift | **NOT_EVALUABLE_MISSING_INPUT** | 0 | — | — | — | — | — | — | — | — |
| h4_oversold_recovery | **INSUFFICIENT_HISTORY** | 7 | -1.095% | -3.590% | 42.9% | -0.172 | -0.603% | 0.40 | — | UNVALIDATED |
| h5_sector_rotation | **UNVALIDATED** | 225 | 0.034% | -0.555% | 39.6% | -0.078 | 0.020% | 0.52 | 1.0 | UNVALIDATED |
| h6_gap_and_hold | **INSUFFICIENT_HISTORY** | 11 | -2.092% | -4.139% | 27.3% | -0.407 | -0.166% | 0.14 | — | UNVALIDATED |
| h7_catalyst_compression | **NOT_EVALUABLE_MISSING_INPUT** | 0 | — | — | — | — | — | — | — | — |
| h8_turn_of_month | **FAILED_REAL_OOS** | 140 | 0.129% | -0.650% | 37.1% | -0.036 | 0.648% | 0.16 | 1.0 | UNVALIDATED |

- **Aggregate:** `NO_TRADE`. Holm adjustment over 3 evaluable hypotheses at the primary configuration. Validated: none.
- **SPY over the holdout:** total 36.41%, CAGR 23.55%, max drawdown -9.13%. Every rule's trades lagged SPY over their own holding windows (§7).
- **Costs set the sign:** gross (0 bps) means of +0.36% to +0.63% per trade (H1, H2, H5, H8) shrink to between −0.14% and +0.13% at 25 bps. At 50 bps every hypothesis is negative.
- **Forecast model:** no hypothesis with a fitted model shows holdout Brier skill, and every forecast is `UNVALIDATED`.
- **Quality score:** it does not rank realized net R for H5 (ρ ≈ 0.00). H8 holdout ρ = 0.25 with 90% CI [0.06, 0.39], but its development ρ is −0.08, so treat this as a candidate hypothesis only.
- **Sample size is the binding constraint.** Four of the six evaluable hypotheses have fewer than 30 completed holdout trades, or fewer than 100 completed development trades.

## 2. Provenance and reproducibility

| Item | Value |
|---|---|
| Run | [GitHub Actions 39696547618](https://github.com/JaredVogler2/STONKS2026/actions/runs/39696547618) (job 119555196116), `workflow_dispatch` on `main` |
| Code commit | `2acad29f75a81dbde0a469d1634d8b273de5c9ea` (dirty worktree: False) |
| Runner | Linux X64, image ubuntu24 20261012.334.1, Python 3.11.15 |
| Protocol | `realeq-protocol-1`, sha256 `721d3e3e3b82a602e36429ac7e40a0c8d01240d81e5340cb25bdd700dc95d636` ([PROTOCOL.md](PROTOCOL.md)) |
| Promotion policy / strategy versions | `promotion-policy-2`; all strategies `1.0.0` |
| Source | alpaca `sip`, adjustment `split`, `1Day`, fetched 2026-10-21T23:56:11Z, 16 requests; corporate actions available |
| Hashes | dataset `f428e39edbc92f565693853895ad062afb2639809cb3bd6ad1f460851c94d419`; history `18d9678faf9373fe6dd3526e05374e25a0b2ad2445d9a90ec783160bc328fecf` |
| Manifest | frozen 2026-10-21T23:56:23Z (FROZEN_BEFORE_METRICS); sha256 = `strategy_metrics.json.manifest_sha256` = `ac30ce8b338da3d13e620d6e6f7eab3cb4b898ef8242e555ff1d9e12f14beb08` |
| Artifact | `apex-realeq-39696547618-1` (id 12390019748), zip sha256 `270c88959b8984d276da3f5573b1b7f9da1cf76366c868112ce6894a5099edab` (verified) |
| Seeds | PYTHONHASHSEED=0, bootstrap=11, matched_random=20261021, validation=20261020 |
| Configurations evaluated | 229 (listed in `strategy_metrics.json.configurations_attempted`) |

A previous attempt, run 39589850123, stopped as `BLOCKED_ALPACA_ACTIONS_SECRETS_MISSING` before any request was made. It is recorded in [EXECUTION_STATUS.md](EXECUTION_STATUS.md).

## 3. Universe, data and chronology

**Universe `fixed78`** (`apps/pi-worker/src/apex_worker/research/training_data.py`):
- SPY is the benchmark only.
- The 11 sector SPDRs feed sector-relative features only and are never traded.
- The **66 member stocks**, six per sector, are the only traded candidates:

| Sector | ETF | Stocks |
|---|---|---|
| communication_services | XLC | GOOGL, VZ, T, DIS, CMCSA, NFLX |
| consumer_discretionary | XLY | AMZN, HD, MCD, NKE, SBUX, LOW |
| consumer_staples | XLP | PG, KO, PEP, WMT, CL, MO |
| energy | XLE | XOM, CVX, COP, SLB, EOG, PSX |
| financials | XLF | JPM, BAC, WFC, GS, MS, BLK |
| health_care | XLV | JNJ, UNH, PFE, MRK, ABT, TMO |
| industrials | XLI | CAT, HON, UNP, GE, LMT, DE |
| materials | XLB | LIN, APD, SHW, ECL, NEM, FCX |
| real_estate | XLRE | PLD, AMT, CCI, SPG, EQIX, PSA |
| technology | XLK | AAPL, MSFT, NVDA, AVGO, ORCL, CSCO |
| utilities | XLU | NEE, DUK, SO, D, AEP, EXC |

**Chronology** (computed from the actual SPY sessions, not guessed):

| Segment | Dates | Sessions | Use |
|---|---|---|---|
| Data | 2018-10-22 → 2026-10-21 | ≈2,010 per symbol | fetched 8 years back from the latest completed session |
| Warm-up | 2018-10-22 → day before 2019-10-23 | 252 | features only |
| Development | 2019-10-23 → 2025-05-01 | 1388 | 4 expanding purged walk-forward folds; last 25% of each training window for isotonic calibration; threshold chosen here |
| Embargo / purge | training label windows must end before 2025-04-25 | 5 NYSE sessions | plus purging of overlapping 20-session labels |
| **Holdout** | **2025-05-02 → 2026-09-21** | 348 | evaluated once per pre-registered configuration |
| Label tail | after 2026-09-21 | 22 | outcomes of the last decisions only; no new decisions |

**Data quality** (`data_quality.json`):
- 156,773 raw provider rows, with 0 invalid (0.0).
- 0 symbols with future-dated bars; request budget exhausted: False.
- Usable symbols: 78/78, candidate stocks 66/66, sector ETFs 11/11.
- No symbol has missing, stale, duplicate or zero-volume sessions.

These symbols carry flags; every other symbol is clean:

| Symbol | Role | Sessions | First | Last | Flags |
|---|---|---|---|---|---|
| XLK | sector_etf | 2010 | 2018-10-22 | 2026-10-21 | splits 2025-12-05 (continuity ok) |
| XLE | sector_etf | 2010 | 2018-10-22 | 2026-10-21 | splits 2025-12-05 (continuity ok) |
| XLY | sector_etf | 2010 | 2018-10-22 | 2026-10-21 | splits 2025-12-05 (continuity ok) |
| XLU | sector_etf | 2010 | 2018-10-22 | 2026-10-21 | splits 2025-12-05 (continuity ok) |
| XLB | sector_etf | 2010 | 2018-10-22 | 2026-10-21 | splits 2025-12-05 (continuity ok) |
| XLRE | sector_etf | 2010 | 2018-10-22 | 2026-10-21 | Real Estate SPDR launched 2015-10-08 |
| XLC | sector_etf | 2010 | 2018-10-22 | 2026-10-21 | Communication Services SPDR launched 2018-06-19; the GICS sector itself was created 2018-09-24 |
| AAPL | candidate_stock | 2010 | 2018-10-22 | 2026-10-21 | splits 2020-08-31 (continuity ok) |
| NVDA | candidate_stock | 2010 | 2018-10-22 | 2026-10-21 | splits 2021-07-20, 2024-06-10 (continuity ok) |
| AVGO | candidate_stock | 2010 | 2018-10-22 | 2026-10-21 | splits 2024-07-15 (continuity ok) |
| ORCL | candidate_stock | 2010 | 2018-10-22 | 2026-10-21 | >25% daily move on 2025-09-10 |
| COP | candidate_stock | 2010 | 2018-10-22 | 2026-10-21 | >25% daily move on 2020-03-24 |
| SLB | candidate_stock | 2010 | 2018-10-22 | 2026-10-21 | >25% daily move on 2020-03-09 |
| EOG | candidate_stock | 2010 | 2018-10-22 | 2026-10-21 | >25% daily move on 2020-03-09 |
| HON | candidate_stock | 2010 | 2018-10-22 | 2026-10-21 | >25% daily move on 2026-06-29; splits 2018-10-29, 2026-06-29 (continuity suspect) |
| GE | candidate_stock | 2010 | 2018-10-22 | 2026-10-21 | splits 2021-08-02 (continuity ok) |
| AMZN | candidate_stock | 2010 | 2018-10-22 | 2026-10-21 | splits 2022-06-06 (continuity ok) |
| WMT | candidate_stock | 2010 | 2018-10-22 | 2026-10-21 | splits 2024-02-26 (continuity ok) |
| NEE | candidate_stock | 2010 | 2018-10-22 | 2026-10-21 | splits 2020-10-27 (continuity ok) |
| EXC | candidate_stock | 2010 | 2018-10-22 | 2026-10-21 | >25% daily move on 2022-02-02 |
| SHW | candidate_stock | 2010 | 2018-10-22 | 2026-10-21 | splits 2021-04-01 (continuity ok) |
| FCX | candidate_stock | 2010 | 2018-10-22 | 2026-10-21 | >25% daily move on 2020-03-24 |
| SPG | candidate_stock | 2010 | 2018-10-22 | 2026-10-21 | >25% daily move on 2020-03-16, 2020-11-09 |
| GOOGL | candidate_stock | 2010 | 2018-10-22 | 2026-10-21 | splits 2022-07-18 (continuity ok) |
| NFLX | candidate_stock | 2010 | 2018-10-22 | 2026-10-21 | >25% daily move on 2022-04-20; splits 2025-11-17 (continuity ok) |

**Data issues the reviewer should weigh:**
- **HON 2026-06-29:** the split continuity check is `suspect`. The split-adjusted close jumps more than 25% on a provider-listed split date, inside the holdout.
- **EXC 2022-02-02:** the drop matches the Constellation spin-off. Split-only adjustment leaves distributions as price drops.
- **Expected real events:**
  - ORCL 2025-09-10, NFLX 2022-04-20;
  - the March 2020 moves in energy and REITs;
  - SPG 2020-11-09.

**Bias inventory** (from `data_quality.json.bias_warnings`):

- SURVIVORSHIP: fixed universe of currently listed large caps; historical results are optimistic
- SECTOR LABELS: current classification applied to all history (mild look-ahead in sector-relative features)
- EARNINGS UNKNOWN: no point-in-time earnings calendar in CI; earnings vetoes not modelled
- NO CATALYSTS: catalyst-dependent strategies produce no events in CI training
- PRICE-ONLY RETURNS: split-adjusted bars, dividends not modelled
- REFERENCE ASSETS: SPY and the 11 sector SPDRs feed relative-strength/sector features and are never traded
- UNIVERSE SIZE: 66 large caps in 11 sectors; results describe this sample, not U.S. equities in general
- DAILY BARS: intraday order of high/low is unknown; same-bar target+stop is resolved stop-first
- SPIN-OFFS: split-only adjustment leaves spin-off / special distributions as price drops (e.g. T 2022, GE 2023-2024); a trade held across such an ex-date can book a false loss (large moves are flagged)

## 4. Frozen method (what produced every number)

The full text is in [PROTOCOL.md](PROTOCOL.md) and the machine copy is `research/realeq/protocol.py`. The essentials:

- **Signals.** Hypothesis plugins are in `signals/strategies/library.py` and use causal features at the session-t close. Each must pass the gates in `research/dataset.py:gate_mask`:
  - price ≥ $10;
  - median 20-day dollar volume ≥ $30M;
  - average volume ≥ 1M;
  - at least 180 bars;
  - not more than 3.5 ATR above SMA20;
  - P(risk_off) + P(vol_shock) < 0.6.
- **Quality ranking (0–100).** It is not a probability. Weighted evidence groups: price 1.0, sector 0.8, volume 0.8, volatility 0.6, regime 1.0, catalyst 0.6; setup weight 0.4; at least 60% coverage required (`signals/quality.py`).
- **Price levels** (`signals/levels.py:compute_levels`):
  - Entry zone: [close − 0.5 ATR, close + min(0.5 ATR, 2%)].
  - Stop: the plugin's invalidation hint, clamped to [1, 3] ATR below the zone midpoint.
  - Target: midpoint + 2R, capped 0.25 ATR below the prior 120-close high when that sits between the zone and the target.
  - Rejection: a signal is rejected if net R/R < 1.5. **That screen assumes 10 bps per side, while evaluation uses 25 bps** (§9).
- **Execution** (`research/execution.py:simulate`):
  - The limit order is valid for sessions t+1 and t+2.
    - An open inside the zone fills at the open.
    - An open above the zone with a low ≤ entry_max fills at entry_max.
    - An open below the zone means no fill that session.
  - On the fill session only the stop can trigger.
  - A gap through the stop exits at the open; a gap above the target exits at the open.
  - A bar touching both target and stop is booked as a stop.
  - Time exit is at the close of fill + H sessions.
  - Simulated at 0 bps; costs are applied afterwards: net = (1+g)(1−c)/(1+c) − 1, and R = (exit(1−c) − entry(1+c)) / (entry(1+c) − stop).
- **Rule trades:**
  - only regime-allowed signals are kept (live logic marks the rest WATCH);
  - de-duplicated to one pending or open position per (strategy, symbol);
  - no earnings veto, because there is no point-in-time earnings calendar, so `earnings_unknown = 1` for every trade.
- **Uncertainty.** A weekly-block bootstrap of the mean (2000 draws, seed 11); the one-sided p is the share of bootstrap means ≤ 0. Holm adjustment is applied across evaluable hypotheses.
- **Baselines.** All use the same levels-from-ATR, execution and costs, with the generic hint close − 1.5 ATR:
  - matched random: 50 seeded draws, same decision dates and count, random gate-passing stocks;
  - `momentum_20`: every 5th session, the top eligible stock by 20-session return;
  - `breakout_basic`: a close above the prior 20-session high.
- **Dispositions** (the thresholds are in `protocol.py`):
  - **VALIDATED** requires n ≥ 100, at least 26 weeks, at least 2 regimes, mean > 0 with p10 > 0 at 25 bps, mean > 0 at 50 bps, Holm p < 0.10, mean above the random 95th percentile, both sector groups > 0, and a forecast that is not UNVALIDATED.
  - **FAILED** requires n ≥ 100 and either bootstrap p90 < 0 or a mean at or below the random median.
  - **INSUFFICIENT** means fewer than 30 holdout or fewer than 100 development completed trades.

## 5. Results by hypothesis

### h1_vcp_breakout: Volatility contraction breakout

- **Hypothesis:** After a tight, shallow base with compressed volatility, an expansion through the pivot on above-normal participation and positive relative strength continues over 5-20 sessions more often than unconditional entries.
- **Trigger:** `breakout_today == 1` AND prior-day `squeeze_pct_120 <= 0.35` AND prior-day `base_length >= 10` AND prior-day `base_depth_pct <= 0.15` AND `rel_volume_1 >= 1.3` AND `rs_bench_20 > 0` AND `dist_sma50_atr > 0`. Invalidation hint: close − 1.6 ATR. Allowed regimes: trending_up, choppy_neutral, rebound. Default enabled: True; weak prior: False.
- **Disposition: `INSUFFICIENT_HISTORY`.** completed rule trades: holdout 16 (minimum 30), development 54 (minimum 100). Holm p = —. Generated recommendation: NOT EVALUABLE YET — too few trades in this universe/window.

| Triggered | Gate eligible | Quality scored | Levels valid | Regime allowed | After de-dup | Filled | Completed |
|---|---|---|---|---|---|---|---|
| 126 | 107 | 107 | 78 | 77 | 76 | 70 | 70 |

*The funnel covers the whole decision window (development and holdout), at H = 20.*

**Primary configuration, development vs holdout** (rule trades, H = 20, 25 bps):

| Metric | Development | Holdout |
|---|---|---|
| Signals (after de-dup) | 60 | 16 |
| Filled | 54 | 16 |
| No fill | 6 | 0 |
| Completed (TARGET/STOP/TIME) | 54 | 16 |
| Fill rate | 90.0% | 100.0% |
| Mean net / trade | 0.099% | -0.144% |
| Median net / trade | -3.069% | -3.210% |
| Win rate (net > 0) | 40.7% | 43.8% |
| Mean R | 0.005 | 0.018 |
| Median R | -1.047 | -0.938 |
| R p10 (lower tail) | -1.102 | -1.070 |
| Expected shortfall 5% (net) | -6.29% | -6.62% |
| Profit factor (n ≥ 30) | 1.041 | — |
| P(target before stop) | 36.0% | 42.9% |
| Target / stop / time exits | 18 / 32 / 4 | 6 / 8 / 2 |
| Same-bar ambiguous (booked stop) | 0 | 0 |
| Gap through stop | 6 | 0 |
| Mean net excl. ambiguous (UNRESOLVED) | 0.099% | -0.144% |
| Mean sessions held | 9.2 | 8.8 |
| Mean MFE / MAE | 4.43% / -3.31% | 4.38% / -3.27% |
| Bootstrap mean p5 / p10 / p50 / p90 / p95 | -1.30% / -0.96% / 0.06% / 1.18% / 1.52% | -2.60% / -2.10% / -0.18% / 1.62% / 2.07% |
| One-sided bootstrap p (mean ≤ 0) | 0.4715 | 0.5495 |
| Distinct decision weeks / dates | 43 / 48 | 13 / 15 |
| Regimes present | 2 | 1 |

**Holdout mean net by horizon × cost** (n = completed trades):

| Horizon | 0 bps | 10 bps | 25 bps | 50 bps |
|---|---|---|---|---|
| H=5 | 0.527% (n=16) | 0.326% (n=16) | 0.026% (n=16) | -0.473% (n=16) |
| H=10 | -0.188% (n=16) | -0.387% (n=16) | -0.685% (n=16) | -1.181% (n=16) |
| H=15 | 0.428% (n=16) | 0.227% (n=16) | -0.073% (n=16) | -0.572% (n=16) |
| H=20 | 0.356% (n=16) | 0.156% (n=16) | -0.144% (n=16) | -0.642% (n=16) |

**Development mean net by horizon × cost:**

| Horizon | 0 bps | 10 bps | 25 bps | 50 bps |
|---|---|---|---|---|
| H=5 | 0.535% (n=54) | 0.334% (n=54) | 0.034% (n=54) | -0.465% (n=54) |
| H=10 | 0.208% (n=54) | 0.008% (n=54) | -0.292% (n=54) | -0.789% (n=54) |
| H=15 | 0.661% (n=54) | 0.460% (n=54) | 0.159% (n=54) | -0.341% (n=54) |
| H=20 | 0.601% (n=54) | 0.400% (n=54) | 0.099% (n=54) | -0.400% (n=54) |

**Holdout by regime**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| trending_up | 16 | -0.144% | 43.8% | 0.018 |  |

**Holdout by sector**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| consumer_discretionary | 1 | -4.408% | 0.0% | -1.058 | yes |
| consumer_staples | 2 | 1.029% | 50.0% | 0.339 | yes |
| energy | 4 | -0.615% | 50.0% | -0.086 | yes |
| financials | 2 | 5.708% | 100.0% | 1.650 | yes |
| health_care | 1 | -3.770% | 0.0% | -1.068 | yes |
| industrials | 2 | 0.691% | 50.0% | 0.315 | yes |
| technology | 2 | 0.112% | 50.0% | 0.029 | yes |
| utilities | 2 | -3.373% | 0.0% | -0.954 | yes |

**Holdout by liquidity quartile (development-set edges)**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| Q1 | 3 | 0.068% | 33.3% | -0.243 | yes |
| Q2 | 4 | -2.710% | 25.0% | -0.446 | yes |
| Q3 | 2 | 0.540% | 50.0% | 0.317 | yes |
| Q4 | 7 | 1.036% | 57.1% | 0.309 | yes |

**Holdout by year**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| 2025 | 10 | -0.172% | 40.0% | -0.005 |  |
| 2026 | 6 | -0.097% | 50.0% | 0.056 | yes |

**Holdout by exit type**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| STOP | 8 | -4.362% | 0.0% | -1.061 | yes |
| TARGET | 6 | 5.533% | 100.0% | 1.509 | yes |
| TIME | 2 | -0.304% | 50.0% | -0.140 | yes |

**Development by year**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| 2019 | 3 | -0.459% | 33.3% | -0.108 | yes |
| 2020 | 10 | 3.287% | 60.0% | 0.579 |  |
| 2021 | 10 | -1.775% | 30.0% | -0.511 |  |
| 2022 | 2 | -5.834% | 0.0% | -1.042 | yes |
| 2023 | 10 | -1.463% | 30.0% | -0.325 |  |
| 2024 | 17 | 0.865% | 47.1% | 0.284 |  |
| 2025 | 2 | 1.600% | 50.0% | 0.201 | yes |

**Comparisons and robustness** (holdout, 25 bps):

| Check | Value |
|---|---|
| Matched random (50 draws): median / p05 / p95 | 0.406% / -1.032% / 3.006% |
| Strategy percentile vs random | 0.34 |
| SPY over the same windows: mean SPY / mean excess / share beating SPY | 1.029% / -1.173% / 43.8% |
| Sector group A mean net (n) | 0.120% (10) |
| Sector group B mean net (n) | -0.585% (6) |
| Quality vs net R, holdout: Spearman (n, weeks) [90% CI] | -0.341 (16, 13) [-0.7835, 0.3037] |
| Quality vs net R, development: Spearman (n) [90% CI] | 0.052 (54) [-0.1874, 0.3158] |
| Cross-sector model Brier skill A→B / B→A | — / — |

**Forecast model** (existing competing-risk model with promotion-policy-2 gates, trained on all filled events in all regimes):

| Item | Value |
|---|---|
| Status / reasons | `UNVALIDATED`: holdout entries 0 < 100; distinct dates 15 < 50; holdout covers fewer than 2 regimes; no Brier skill over the base rate on holdout; calibration error None > 0.06; too few selected holdout trades |
| Samples | {"development_filled": 55, "final_calibration": 14, "final_fit": 41, "final_training_after_purge": 55, "holdout_filled": 16} |
| Holdout n / base rate (train) / observed TARGET rate | — / — / — |
| Brier / Brier skill / log loss / ECE | — / — / — / — |
| Shuffled-label skill mean / q95 / permutation p | — / — / — |
| Threshold (chosen on development OOS) / selected n / selected mean net | — / 0 / — |
| Precision / recall of TARGET at the threshold | — / — |

**Pre-registered ablations** (holdout, 25 bps):

| Variant | n | Mean net | Win rate | Note |
|---|---|---|---|---|
| Rule as registered | 16 | -0.144% | 43.8% |  |
| All regimes (no regime filter) | 16 | -0.144% | 43.8% |  |
| Quality top half (development-median cut) | 12 | -0.513% | 41.7% | cut = 70.795 |
| Model without Quality features | None | — | — | Brier skill — vs full — |

**Capital-constrained book** (10 equal slots, holdout, 25 bps):
- 16 trades taken and 0 skipped for capacity.
- Total -0.25%, CAGR -0.21%, max drawdown -1.85%.
- Average exposure 4.6%, maximum 2 positions.
- SPY over the same period 26.78%; SPY × exposure 1.22%.

### h2_rs_pullback: Relative-strength pullback

- **Hypothesis:** Cross-sectional 60-session leaders that pull back 3-11% toward a rising SMA50 with a bullish reversal bar resume their trend.
- **Trigger:** `xs_rank_ret_60 >= 0.75` AND `above_sma200 == 1` AND `-0.11 <= pct_from_high_60 <= -0.03` AND `-1.2 <= dist_sma20_atr <= 0.3` AND `sma50_slope_20 > 0` AND `clv_1 > 0.25` AND `ret_5 < 0`. Invalidation hint: close − 1.4 ATR. Allowed regimes: trending_up, choppy_neutral, rebound. Default enabled: True; weak prior: False.
- **Disposition: `INSUFFICIENT_HISTORY`.** completed rule trades: holdout 39 (minimum 30), development 98 (minimum 100). Holm p = 1.0. Generated recommendation: NOT EVALUABLE YET — too few trades in this universe/window.

| Triggered | Gate eligible | Quality scored | Levels valid | Regime allowed | After de-dup | Filled | Completed |
|---|---|---|---|---|---|---|---|
| 944 | 739 | 739 | 218 | 205 | 148 | 137 | 137 |

*The funnel covers the whole decision window (development and holdout), at H = 20.*

**Primary configuration, development vs holdout** (rule trades, H = 20, 25 bps):

| Metric | Development | Holdout |
|---|---|---|
| Signals (after de-dup) | 107 | 41 |
| Filled | 98 | 39 |
| No fill | 9 | 2 |
| Completed (TARGET/STOP/TIME) | 98 | 39 |
| Fill rate | 91.6% | 95.1% |
| Mean net / trade | 0.095% | -0.037% |
| Median net / trade | -2.438% | -2.417% |
| Win rate (net > 0) | 43.9% | 38.5% |
| Mean R | 0.017 | 0.022 |
| Median R | -1.038 | -0.809 |
| R p10 (lower tail) | -1.115 | -1.107 |
| Expected shortfall 5% (net) | -6.46% | -8.91% |
| Profit factor (n ≥ 30) | 1.042 | 0.986 |
| P(target before stop) | 43.2% | 36.7% |
| Target / stop / time exits | 38 / 50 / 10 | 11 / 19 / 9 |
| Same-bar ambiguous (booked stop) | 0 | 0 |
| Gap through stop | 11 | 2 |
| Mean net excl. ambiguous (UNRESOLVED) | 0.095% | -0.037% |
| Mean sessions held | 8.2 | 8.6 |
| Mean MFE / MAE | 4.29% / -3.30% | 5.03% / -3.57% |
| Bootstrap mean p5 / p10 / p50 / p90 / p95 | -0.81% / -0.61% / 0.12% / 0.78% / 1.01% | -1.84% / -1.49% / -0.10% / 1.54% / 2.05% |
| One-sided bootstrap p (mean ≤ 0) | 0.4170 | 0.5335 |
| Distinct decision weeks / dates | 73 / 90 | 29 / 38 |
| Regimes present | 2 | 1 |

**Holdout mean net by horizon × cost** (n = completed trades):

| Horizon | 0 bps | 10 bps | 25 bps | 50 bps |
|---|---|---|---|---|
| H=5 | 0.817% (n=40) | 0.615% (n=40) | 0.314% (n=40) | -0.186% (n=40) |
| H=10 | 0.644% (n=39) | 0.443% (n=39) | 0.142% (n=39) | -0.358% (n=39) |
| H=15 | 0.791% (n=39) | 0.590% (n=39) | 0.289% (n=39) | -0.212% (n=39) |
| H=20 | 0.464% (n=39) | 0.263% (n=39) | -0.037% (n=39) | -0.536% (n=39) |

**Development mean net by horizon × cost:**

| Horizon | 0 bps | 10 bps | 25 bps | 50 bps |
|---|---|---|---|---|
| H=5 | 0.142% (n=106) | -0.058% (n=106) | -0.358% (n=106) | -0.855% (n=106) |
| H=10 | 0.213% (n=102) | 0.012% (n=102) | -0.287% (n=102) | -0.785% (n=102) |
| H=15 | 0.670% (n=100) | 0.469% (n=100) | 0.168% (n=100) | -0.332% (n=100) |
| H=20 | 0.597% (n=98) | 0.396% (n=98) | 0.095% (n=98) | -0.404% (n=98) |

**Holdout by regime**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| trending_up | 39 | -0.037% | 38.5% | 0.022 |  |

**Holdout by sector**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| communication_services | 3 | -3.626% | 0.0% | -0.979 | yes |
| consumer_discretionary | 2 | -1.949% | 0.0% | -0.713 | yes |
| consumer_staples | 5 | -1.650% | 0.0% | -0.672 | yes |
| energy | 4 | -3.092% | 25.0% | -0.440 | yes |
| financials | 3 | 0.447% | 66.7% | 0.151 | yes |
| health_care | 5 | 2.852% | 80.0% | 0.823 | yes |
| industrials | 4 | -0.302% | 50.0% | 0.501 | yes |
| materials | 3 | 1.253% | 66.7% | 0.892 | yes |
| real_estate | 1 | 0.445% | 100.0% | 0.091 | yes |
| technology | 9 | 1.706% | 33.3% | 0.113 | yes |

**Holdout by liquidity quartile (development-set edges)**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| Q1 | 5 | 1.082% | 40.0% | 0.408 | yes |
| Q2 | 7 | -3.783% | 14.3% | -0.922 | yes |
| Q3 | 13 | 1.616% | 61.5% | 0.543 |  |
| Q4 | 14 | -0.098% | 28.6% | -0.126 |  |

**Holdout by year**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| 2025 | 17 | 1.238% | 35.3% | 0.118 |  |
| 2026 | 22 | -1.022% | 40.9% | -0.051 |  |

**Holdout by exit type**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| STOP | 19 | -4.845% | 0.0% | -1.094 |  |
| TARGET | 11 | 8.103% | 100.0% | 1.897 |  |
| TIME | 9 | 0.164% | 44.4% | 0.088 | yes |

**Development by year**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| 2019 | 1 | 2.525% | 100.0% | 0.916 | yes |
| 2020 | 16 | 2.049% | 62.5% | 0.359 |  |
| 2021 | 29 | -2.128% | 24.1% | -0.510 |  |
| 2022 | 4 | -1.532% | 25.0% | -0.628 | yes |
| 2023 | 21 | 0.215% | 42.9% | 0.171 |  |
| 2024 | 18 | 2.254% | 61.1% | 0.539 |  |
| 2025 | 9 | -0.362% | 44.4% | -0.103 | yes |

**Comparisons and robustness** (holdout, 25 bps):

| Check | Value |
|---|---|
| Matched random (50 draws): median / p05 / p95 | -0.258% / -1.925% / 1.294% |
| Strategy percentile vs random | 0.62 |
| SPY over the same windows: mean SPY / mean excess / share beating SPY | 0.819% / -0.856% / 33.3% |
| Sector group A mean net (n) | -0.485% (28) |
| Sector group B mean net (n) | 1.104% (11) |
| Quality vs net R, holdout: Spearman (n, weeks) [90% CI] | 0.009 (39, 29) [-0.2297, 0.253] |
| Quality vs net R, development: Spearman (n) [90% CI] | 0.186 (98) [0.0181, 0.3647] |
| Cross-sector model Brier skill A→B / B→A | — / — |

**Forecast model** (existing competing-risk model with promotion-policy-2 gates, trained on all filled events in all regimes):

| Item | Value |
|---|---|
| Status / reasons | `UNVALIDATED`: holdout entries 0 < 100; no Brier skill over the base rate on holdout; calibration error None > 0.06; too few selected holdout trades |
| Samples | {"development_filled": 148, "final_calibration": 37, "final_fit": 111, "final_training_after_purge": 148, "holdout_filled": 55} |
| Holdout n / base rate (train) / observed TARGET rate | — / — / — |
| Brier / Brier skill / log loss / ECE | — / — / — / — |
| Shuffled-label skill mean / q95 / permutation p | — / — / — |
| Threshold (chosen on development OOS) / selected n / selected mean net | — / 0 / — |
| Precision / recall of TARGET at the threshold | — / — |

**Pre-registered ablations** (holdout, 25 bps):

| Variant | n | Mean net | Win rate | Note |
|---|---|---|---|---|
| Rule as registered | 39 | -0.037% | 38.5% |  |
| All regimes (no regime filter) | 40 | -0.096% | 40.0% |  |
| Quality top half (development-median cut) | 19 | 1.778% | 47.4% | cut = 62.29 |
| Model without Quality features | None | — | — | Brier skill — vs full — |

**Capital-constrained book** (10 equal slots, holdout, 25 bps):
- 39 trades taken and 0 skipped for capacity.
- Total -0.24%, CAGR -0.17%, max drawdown -3.50%.
- Average exposure 9.8%, maximum 4 positions.
- SPY over the same period 29.16%; SPY × exposure 2.86%.

### h3_post_event_drift: Post-event drift

- **Hypothesis:** A positive, point-in-time-available catalyst followed by a held gap continues to drift; entry only on the next session within a zone (no fill at the release print); compared against a placebo event set.
- **Trigger:** `cat_positive_recent_2d >= 1` AND prior-day gap in [3%, 12%] AND close held >= 99% of prior close AND prior-day `rel_volume_1 >= 2.0` (requires point-in-time catalysts). Invalidation hint: pre-gap close × 0.995. Allowed regimes: trending_up, choppy_neutral, rebound. Default enabled: True; weak prior: False.
- **Disposition: `NOT_EVALUABLE_MISSING_INPUT`.** not failed alpha: no event can exist without that input. Holm p = —. Generated recommendation: NOT EVALUABLE — needs point-in-time catalyst history.

| Triggered | Gate eligible | Quality scored | Levels valid | Regime allowed | After de-dup | Filled | Completed |
|---|---|---|---|---|---|---|---|
| 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

*The funnel covers the whole decision window (development and holdout), at H = 20.*

> requires catalysts available_at <= decision time; CI has no point-in-time catalyst history. No events were manufactured. This is **not** evidence against the hypothesis.

### h4_oversold_recovery: Oversold recovery in uptrend

- **Hypothesis:** Short-term oversold names inside a long-term uptrend with a reversal bar mean-revert toward SMA20.
- **Trigger:** `above_sma200 == 1` AND `ret_5 <= -0.06` AND `dist_sma20_atr <= -1.5` AND `clv_1 >= 0.3` AND `ret_60 > -0.05` AND `failed_breakouts_60 <= 2`; allowed regimes trending_up / choppy_neutral only. Invalidation hint: close − 1.2 ATR. Allowed regimes: trending_up, choppy_neutral. Default enabled: True; weak prior: False.
- **Disposition: `INSUFFICIENT_HISTORY`.** completed rule trades: holdout 7 (minimum 30), development 34 (minimum 100). Holm p = —. Generated recommendation: NOT EVALUABLE YET — too few trades in this universe/window.

| Triggered | Gate eligible | Quality scored | Levels valid | Regime allowed | After de-dup | Filled | Completed |
|---|---|---|---|---|---|---|---|
| 69 | 55 | 55 | 55 | 50 | 43 | 41 | 41 |

*The funnel covers the whole decision window (development and holdout), at H = 20.*

**Primary configuration, development vs holdout** (rule trades, H = 20, 25 bps):

| Metric | Development | Holdout |
|---|---|---|
| Signals (after de-dup) | 36 | 7 |
| Filled | 34 | 7 |
| No fill | 2 | 0 |
| Completed (TARGET/STOP/TIME) | 34 | 7 |
| Fill rate | 94.4% | 100.0% |
| Mean net / trade | 0.681% | -1.095% |
| Median net / trade | -2.719% | -2.949% |
| Win rate (net > 0) | 41.2% | 42.9% |
| Mean R | -0.019 | -0.172 |
| Median R | -1.039 | -1.032 |
| R p10 (lower tail) | -1.100 | -1.142 |
| Expected shortfall 5% (net) | -8.44% | -8.27% |
| Profit factor (n ≥ 30) | 1.268 | — |
| P(target before stop) | 40.6% | 20.0% |
| Target / stop / time exits | 13 / 19 / 2 | 1 / 4 / 2 |
| Same-bar ambiguous (booked stop) | 0 | 0 |
| Gap through stop | 2 | 3 |
| Mean net excl. ambiguous (UNRESOLVED) | 0.681% | -1.095% |
| Mean sessions held | 6.9 | 10.1 |
| Mean MFE / MAE | 5.25% / -3.89% | 4.40% / -5.23% |
| Bootstrap mean p5 / p10 / p50 / p90 / p95 | -1.33% / -0.88% / 0.72% / 2.27% / 2.78% | -4.49% / -3.59% / -1.09% / 1.39% / 2.56% |
| One-sided bootstrap p (mean ≤ 0) | 0.2910 | 0.7230 |
| Distinct decision weeks / dates | 29 / 32 | 5 / 6 |
| Regimes present | 2 | 1 |

**Holdout mean net by horizon × cost** (n = completed trades):

| Horizon | 0 bps | 10 bps | 25 bps | 50 bps |
|---|---|---|---|---|
| H=5 | -1.517% (n=7) | -1.714% (n=7) | -2.008% (n=7) | -2.497% (n=7) |
| H=10 | -2.488% (n=7) | -2.683% (n=7) | -2.974% (n=7) | -3.458% (n=7) |
| H=15 | -0.732% (n=7) | -0.931% (n=7) | -1.227% (n=7) | -1.720% (n=7) |
| H=20 | -0.599% (n=7) | -0.798% (n=7) | -1.095% (n=7) | -1.588% (n=7) |

**Development mean net by horizon × cost:**

| Horizon | 0 bps | 10 bps | 25 bps | 50 bps |
|---|---|---|---|---|
| H=5 | 1.428% (n=34) | 1.225% (n=34) | 0.922% (n=34) | 0.418% (n=34) |
| H=10 | 1.247% (n=34) | 1.044% (n=34) | 0.742% (n=34) | 0.239% (n=34) |
| H=15 | 1.261% (n=34) | 1.059% (n=34) | 0.756% (n=34) | 0.254% (n=34) |
| H=20 | 1.186% (n=34) | 0.984% (n=34) | 0.681% (n=34) | 0.179% (n=34) |

**Holdout by regime**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| trending_up | 7 | -1.095% | 42.9% | -0.172 | yes |

**Holdout by sector**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| consumer_discretionary | 1 | -4.024% | 0.0% | -1.064 | yes |
| consumer_staples | 1 | -2.949% | 0.0% | -1.113 | yes |
| health_care | 1 | 3.222% | 100.0% | 1.144 | yes |
| materials | 2 | 4.965% | 100.0% | 1.024 | yes |
| technology | 2 | -6.922% | 0.0% | -1.108 | yes |

**Holdout by liquidity quartile (development-set edges)**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| Q2 | 2 | 2.533% | 50.0% | 0.397 | yes |
| Q3 | 3 | 0.372% | 66.7% | 0.074 | yes |
| Q4 | 2 | -6.922% | 0.0% | -1.108 | yes |

**Holdout by year**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| 2025 | 3 | -1.585% | 33.3% | -0.120 | yes |
| 2026 | 4 | -0.727% | 50.0% | -0.210 | yes |

**Holdout by exit type**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| STOP | 4 | -5.204% | 0.0% | -1.098 | yes |
| TARGET | 1 | 9.089% | 100.0% | 1.857 | yes |
| TIME | 2 | 2.032% | 100.0% | 0.668 | yes |

**Development by year**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| 2019 | 1 | -2.623% | 0.0% | -1.103 | yes |
| 2020 | 7 | 1.358% | 42.9% | -0.033 | yes |
| 2021 | 10 | 0.796% | 50.0% | 0.168 |  |
| 2022 | 1 | -3.459% | 0.0% | -1.075 | yes |
| 2023 | 2 | -4.038% | 0.0% | -1.069 | yes |
| 2024 | 8 | 2.277% | 50.0% | 0.306 | yes |
| 2025 | 5 | 0.329% | 40.0% | -0.048 | yes |

**Comparisons and robustness** (holdout, 25 bps):

| Check | Value |
|---|---|
| Matched random (50 draws): median / p05 / p95 | -0.603% / -4.345% / 4.053% |
| Strategy percentile vs random | 0.40 |
| SPY over the same windows: mean SPY / mean excess / share beating SPY | -0.231% / -0.864% / 28.6% |
| Sector group A mean net (n) | -1.372% (5) |
| Sector group B mean net (n) | -0.401% (2) |
| Quality vs net R, holdout: Spearman (n, weeks) [90% CI] | — (7, —) None |
| Quality vs net R, development: Spearman (n) [90% CI] | -0.136 (34) [-0.4403, 0.1659] |
| Cross-sector model Brier skill A→B / B→A | — / — |

**Forecast model** (existing competing-risk model with promotion-policy-2 gates, trained on all filled events in all regimes):

| Item | Value |
|---|---|
| Status / reasons | `UNVALIDATED`: holdout entries 0 < 100; distinct dates 7 < 50; no Brier skill over the base rate on holdout; calibration error None > 0.06; too few selected holdout trades |
| Samples | {"development_filled": 44, "final_calibration": 11, "final_fit": 33, "final_training_after_purge": 44, "holdout_filled": 8} |
| Holdout n / base rate (train) / observed TARGET rate | — / — / — |
| Brier / Brier skill / log loss / ECE | — / — / — / — |
| Shuffled-label skill mean / q95 / permutation p | — / — / — |
| Threshold (chosen on development OOS) / selected n / selected mean net | — / 0 / — |
| Precision / recall of TARGET at the threshold | — / — |

**Pre-registered ablations** (holdout, 25 bps):

| Variant | n | Mean net | Win rate | Note |
|---|---|---|---|---|
| Rule as registered | 7 | -1.095% | 42.9% |  |
| All regimes (no regime filter) | 8 | 0.589% | 50.0% |  |
| Quality top half (development-median cut) | 3 | -5.956% | 0.0% | cut = 62.035 |
| Model without Quality features | None | — | — | Brier skill — vs full — |

**Capital-constrained book** (10 equal slots, holdout, 25 bps):
- 7 trades taken and 0 skipped for capacity.
- Total -0.77%, CAGR -0.72%, max drawdown -2.16%.
- Average exposure 2.7%, maximum 2 positions.
- SPY over the same period 21.53%; SPY × exposure 0.57%.

### h5_sector_rotation: Sector rotation leadership

- **Hypothesis:** Stocks ranking highly within a sector whose cross-sector rank has improved sharply outperform while breadth is supportive.
- **Trigger:** `sector_rank_change_20 >= 0.3` AND `sector_rank_20 >= 0.7` AND `xs_rank_ret_20 >= 0.65` AND `regime_breadth >= 0.5` AND `-0.5 <= dist_sma20_atr <= 2.0`. Invalidation hint: close − 1.5 ATR. Allowed regimes: trending_up, choppy_neutral, rebound. Default enabled: True; weak prior: False.
- **Disposition: `UNVALIDATED`.** inconclusive; failed checks: p10_pos_25, mean_pos_50, holm, beats_random_p95, both_sectors_pos, forecast_ok. Holm p = 1.0. Generated recommendation: MODIFY / RE-TEST — inconclusive; do not use.

| Triggered | Gate eligible | Quality scored | Levels valid | Regime allowed | After de-dup | Filled | Completed |
|---|---|---|---|---|---|---|---|
| 4704 | 4334 | 4334 | 2342 | 2265 | 958 | 908 | 908 |

*The funnel covers the whole decision window (development and holdout), at H = 20.*

**Primary configuration, development vs holdout** (rule trades, H = 20, 25 bps):

| Metric | Development | Holdout |
|---|---|---|
| Signals (after de-dup) | 720 | 238 |
| Filled | 683 | 225 |
| No fill | 37 | 13 |
| Completed (TARGET/STOP/TIME) | 683 | 225 |
| Fill rate | 94.9% | 94.5% |
| Mean net / trade | -0.123% | 0.034% |
| Median net / trade | -2.589% | -3.027% |
| Win rate (net > 0) | 41.3% | 39.6% |
| Mean R | -0.059 | -0.078 |
| Median R | -1.041 | -1.050 |
| R p10 (lower tail) | -1.107 | -1.095 |
| Expected shortfall 5% (net) | -8.81% | -7.24% |
| Profit factor (n ≥ 30) | 0.949 | 1.014 |
| P(target before stop) | 35.6% | 34.0% |
| Target / stop / time exits | 206 / 372 / 105 | 67 / 130 / 28 |
| Same-bar ambiguous (booked stop) | 0 | 0 |
| Gap through stop | 52 | 17 |
| Mean net excl. ambiguous (UNRESOLVED) | -0.123% | 0.034% |
| Mean sessions held | 9.1 | 8.6 |
| Mean MFE / MAE | 4.40% / -3.45% | 4.53% / -3.43% |
| Bootstrap mean p5 / p10 / p50 / p90 / p95 | -0.59% / -0.49% / -0.12% / 0.25% / 0.35% | -0.71% / -0.55% / 0.04% / 0.62% / 0.79% |
| One-sided bootstrap p (mean ≤ 0) | 0.6700 | 0.4575 |
| Distinct decision weeks / dates | 185 / 400 | 60 / 139 |
| Regimes present | 3 | 2 |

**Holdout mean net by horizon × cost** (n = completed trades):

| Horizon | 0 bps | 10 bps | 25 bps | 50 bps |
|---|---|---|---|---|
| H=5 | 0.273% (n=251) | 0.073% (n=251) | -0.227% (n=251) | -0.724% (n=251) |
| H=10 | 0.365% (n=231) | 0.165% (n=231) | -0.135% (n=231) | -0.633% (n=231) |
| H=15 | 0.336% (n=226) | 0.136% (n=226) | -0.164% (n=226) | -0.662% (n=226) |
| H=20 | 0.536% (n=225) | 0.335% (n=225) | 0.034% (n=225) | -0.465% (n=225) |

**Development mean net by horizon × cost:**

| Horizon | 0 bps | 10 bps | 25 bps | 50 bps |
|---|---|---|---|---|
| H=5 | 0.270% (n=771) | 0.070% (n=771) | -0.230% (n=771) | -0.727% (n=771) |
| H=10 | 0.208% (n=699) | 0.008% (n=699) | -0.292% (n=699) | -0.789% (n=699) |
| H=15 | 0.372% (n=686) | 0.172% (n=686) | -0.128% (n=686) | -0.626% (n=686) |
| H=20 | 0.378% (n=683) | 0.177% (n=683) | -0.123% (n=683) | -0.621% (n=683) |

**Holdout by regime**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| rebound | 7 | 4.051% | 57.1% | 0.717 | yes |
| trending_up | 218 | -0.095% | 39.0% | -0.103 |  |

**Holdout by sector**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| communication_services | 25 | 0.113% | 40.0% | -0.017 |  |
| consumer_discretionary | 19 | -0.287% | 36.8% | -0.144 |  |
| consumer_staples | 11 | -0.716% | 36.4% | -0.396 |  |
| energy | 44 | 0.174% | 40.9% | -0.047 |  |
| financials | 14 | 0.502% | 50.0% | 0.153 |  |
| health_care | 24 | -1.717% | 25.0% | -0.499 |  |
| industrials | 17 | 1.199% | 58.8% | 0.292 |  |
| materials | 19 | 1.618% | 52.6% | 0.312 |  |
| real_estate | 13 | -2.237% | 15.4% | -0.652 |  |
| technology | 23 | 2.612% | 56.5% | 0.502 |  |
| utilities | 16 | -2.335% | 12.5% | -0.753 |  |

**Holdout by liquidity quartile (development-set edges)**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| Q1 | 29 | -1.402% | 20.7% | -0.436 |  |
| Q2 | 52 | -0.130% | 38.5% | -0.145 |  |
| Q3 | 60 | 0.611% | 43.3% | 0.093 |  |
| Q4 | 84 | 0.220% | 44.0% | -0.035 |  |

**Holdout by year**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| 2025 | 119 | 0.045% | 37.8% | -0.114 |  |
| 2026 | 106 | 0.022% | 41.5% | -0.037 |  |

**Holdout by exit type**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| STOP | 130 | -4.166% | 0.0% | -1.119 |  |
| TARGET | 67 | 7.308% | 100.0% | 1.710 |  |
| TIME | 28 | 2.134% | 78.6% | 0.482 |  |

**Development by year**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| 2019 | 33 | 0.477% | 57.6% | 0.141 |  |
| 2020 | 149 | 1.486% | 57.7% | 0.291 |  |
| 2021 | 137 | -0.056% | 42.3% | 0.008 |  |
| 2022 | 61 | -1.380% | 26.2% | -0.344 |  |
| 2023 | 113 | -1.581% | 27.4% | -0.447 |  |
| 2024 | 165 | -0.229% | 37.6% | -0.098 |  |
| 2025 | 25 | -0.525% | 40.0% | -0.077 |  |

**Comparisons and robustness** (holdout, 25 bps):

| Check | Value |
|---|---|
| Matched random (50 draws): median / p05 / p95 | 0.020% / -0.668% / 0.498% |
| Strategy percentile vs random | 0.52 |
| SPY over the same windows: mean SPY / mean excess / share beating SPY | 0.811% / -0.776% / 35.1% |
| Sector group A mean net (n) | 0.819% (139) |
| Sector group B mean net (n) | -1.234% (86) |
| Quality vs net R, holdout: Spearman (n, weeks) [90% CI] | 0.002 (225, 60) [-0.1528, 0.1544] |
| Quality vs net R, development: Spearman (n) [90% CI] | 0.021 (683) [-0.0509, 0.0871] |
| Cross-sector model Brier skill A→B / B→A | -0.0369 / -0.0505 |

**Forecast model** (existing competing-risk model with promotion-policy-2 gates, trained on all filled events in all regimes):

| Item | Value |
|---|---|
| Status / reasons | `UNVALIDATED`: no Brier skill over the base rate on holdout; selected trades' p10 mean net return <= 0 at 25 bps/side; holdout skill -0.01265 not above the shuffled-label 95% quantile 0.00236 (permutation p=0.5238) |
| Samples | {"development_filled": 1654, "final_calibration": 414, "final_fit": 1240, "final_training_after_purge": 1654, "holdout_filled": 566} |
| Holdout n / base rate (train) / observed TARGET rate | 566 / 0.2823 / 0.2898 |
| Brier / Brier skill / log loss / ECE | 0.20846 / -0.01265 / 0.61013 / 0.04797 |
| Shuffled-label skill mean / q95 / permutation p | -0.02327 / 0.00236 / 0.5238 |
| Threshold (chosen on development OOS) / selected n / selected mean net | 0.28 / 423 / -0.416% |
| Precision / recall of TARGET at the threshold | 0.2837 / 0.7317 |

| Fold | Train n | Test n | Test window | Brier skill | ECE | Base rate |
|---|---|---|---|---|---|---|
| 1 | 343 | 296 | 2020-10-19 → 2021-08-13 | 0.00902 | 0.0978 | 0.3032 |
| 2 | 628 | 348 | 2021-08-17 → 2023-02-10 | -0.10656 | 0.21679 | 0.3312 |
| 3 | 984 | 306 | 2023-02-13 → 2024-05-10 | 0.0278 | 0.0046 | 0.2927 |
| 4 | 1305 | 338 | 2024-05-13 → 2025-03-07 | -0.05385 | 0.11581 | 0.272 |

*Holdout reliability:* [0.1,0.2) n=33 pred 0.1124 obs 0.2727; [0.2,0.3) n=110 pred 0.2602 obs 0.3182; [0.3,0.4) n=423 pred 0.3203 obs 0.2837

**Capital-constrained book** (10 equal slots, holdout, 25 bps):
- 213 trades taken and 12 skipped for capacity.
- Total 1.94%, CAGR 1.38%, max drawdown -12.29%.
- Average exposure 52.5%, maximum 10 positions.
- SPY over the same period 35.34%; SPY × exposure 18.56%.

### h6_gap_and_hold: Gap and hold

- **Hypothesis:** Gap-ups of 2.5-10% that close in the top half of the range on heavy volume continue; executable entry is the next session's zone, never the official open print.
- **Trigger:** `0.025 <= gap_1 <= 0.10` AND `clv_1 >= 0.5` AND `rel_volume_1 >= 1.8` AND `dist_sma50_atr > 0`. Invalidation hint: pre-gap close × 0.995. Allowed regimes: trending_up, choppy_neutral, rebound. Default enabled: True; weak prior: False.
- **Disposition: `INSUFFICIENT_HISTORY`.** completed rule trades: holdout 11 (minimum 30), development 37 (minimum 100). Holm p = —. Generated recommendation: NOT EVALUABLE YET — too few trades in this universe/window.

| Triggered | Gate eligible | Quality scored | Levels valid | Regime allowed | After de-dup | Filled | Completed |
|---|---|---|---|---|---|---|---|
| 129 | 68 | 68 | 54 | 53 | 52 | 48 | 48 |

*The funnel covers the whole decision window (development and holdout), at H = 20.*

**Primary configuration, development vs holdout** (rule trades, H = 20, 25 bps):

| Metric | Development | Holdout |
|---|---|---|
| Signals (after de-dup) | 41 | 11 |
| Filled | 37 | 11 |
| No fill | 4 | 0 |
| Completed (TARGET/STOP/TIME) | 37 | 11 |
| Fill rate | 90.2% | 100.0% |
| Mean net / trade | 0.786% | -2.092% |
| Median net / trade | -1.722% | -4.766% |
| Win rate (net > 0) | 46.0% | 27.3% |
| Mean R | 0.115 | -0.407 |
| Median R | -0.256 | -1.038 |
| R p10 (lower tail) | -1.156 | -1.053 |
| Expected shortfall 5% (net) | -7.82% | -7.13% |
| Profit factor (n ≥ 30) | 1.340 | — |
| P(target before stop) | 34.6% | 22.2% |
| Target / stop / time exits | 9 / 17 / 11 | 2 / 7 / 2 |
| Same-bar ambiguous (booked stop) | 0 | 0 |
| Gap through stop | 5 | 0 |
| Mean net excl. ambiguous (UNRESOLVED) | 0.786% | -2.092% |
| Mean sessions held | 10.5 | 9.4 |
| Mean MFE / MAE | 5.81% / -3.59% | 3.68% / -4.44% |
| Bootstrap mean p5 / p10 / p50 / p90 / p95 | -0.85% / -0.43% / 0.78% / 2.21% / 2.61% | -4.54% / -4.14% / -2.24% / 0.04% / 0.73% |
| One-sided bootstrap p (mean ≤ 0) | 0.2145 | 0.8935 |
| Distinct decision weeks / dates | 32 / 34 | 10 / 11 |
| Regimes present | 2 | 1 |

**Holdout mean net by horizon × cost** (n = completed trades):

| Horizon | 0 bps | 10 bps | 25 bps | 50 bps |
|---|---|---|---|---|
| H=5 | -0.691% (n=11) | -0.890% (n=11) | -1.187% (n=11) | -1.679% (n=11) |
| H=10 | -0.423% (n=11) | -0.622% (n=11) | -0.919% (n=11) | -1.413% (n=11) |
| H=15 | -0.748% (n=11) | -0.947% (n=11) | -1.243% (n=11) | -1.736% (n=11) |
| H=20 | -1.601% (n=11) | -1.798% (n=11) | -2.092% (n=11) | -2.580% (n=11) |

**Development mean net by horizon × cost:**

| Horizon | 0 bps | 10 bps | 25 bps | 50 bps |
|---|---|---|---|---|
| H=5 | 1.188% (n=37) | 0.986% (n=37) | 0.684% (n=37) | 0.181% (n=37) |
| H=10 | 1.665% (n=37) | 1.462% (n=37) | 1.158% (n=37) | 0.654% (n=37) |
| H=15 | 1.630% (n=37) | 1.427% (n=37) | 1.123% (n=37) | 0.619% (n=37) |
| H=20 | 1.291% (n=37) | 1.089% (n=37) | 0.786% (n=37) | 0.283% (n=37) |

**Holdout by regime**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| trending_up | 11 | -2.092% | 27.3% | -0.407 |  |

**Holdout by sector**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| communication_services | 2 | 2.225% | 50.0% | 0.365 | yes |
| consumer_discretionary | 1 | -5.625% | 0.0% | -1.044 | yes |
| energy | 1 | -6.435% | 0.0% | -1.038 | yes |
| health_care | 1 | -3.764% | 0.0% | -1.069 | yes |
| industrials | 2 | -3.175% | 0.0% | -0.634 | yes |
| materials | 2 | -0.920% | 50.0% | -0.010 | yes |
| utilities | 2 | -1.724% | 50.0% | -0.383 | yes |

**Holdout by liquidity quartile (development-set edges)**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| Q1 | 2 | -0.029% | 50.0% | 0.035 | yes |
| Q2 | 4 | -2.884% | 25.0% | -0.530 | yes |
| Q3 | 4 | -1.288% | 25.0% | -0.346 | yes |
| Q4 | 1 | -6.268% | 0.0% | -1.039 | yes |

**Holdout by year**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| 2025 | 4 | -3.422% | 25.0% | -0.718 | yes |
| 2026 | 7 | -1.332% | 28.6% | -0.229 | yes |

**Holdout by exit type**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| STOP | 7 | -5.566% | 0.0% | -1.047 | yes |
| TARGET | 2 | 8.004% | 100.0% | 1.391 | yes |
| TIME | 2 | -0.029% | 50.0% | 0.035 | yes |

**Development by year**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| 2020 | 7 | 2.543% | 57.1% | 0.259 | yes |
| 2021 | 3 | -0.710% | 33.3% | -0.168 | yes |
| 2022 | 2 | -4.163% | 0.0% | -1.162 | yes |
| 2023 | 8 | -0.405% | 37.5% | -0.043 | yes |
| 2024 | 14 | 1.171% | 50.0% | 0.278 |  |
| 2025 | 3 | 2.857% | 66.7% | 0.572 | yes |

**Comparisons and robustness** (holdout, 25 bps):

| Check | Value |
|---|---|
| Matched random (50 draws): median / p05 / p95 | -0.166% / -3.332% / 3.292% |
| Strategy percentile vs random | 0.14 |
| SPY over the same windows: mean SPY / mean excess / share beating SPY | 1.341% / -3.433% / 27.3% |
| Sector group A mean net (n) | -1.454% (7) |
| Sector group B mean net (n) | -3.209% (4) |
| Quality vs net R, holdout: Spearman (n, weeks) [90% CI] | 0.718 (11, 10) [0.2711, 0.9634] |
| Quality vs net R, development: Spearman (n) [90% CI] | 0.085 (37) [-0.2183, 0.3634] |
| Cross-sector model Brier skill A→B / B→A | — / — |

**Forecast model** (existing competing-risk model with promotion-policy-2 gates, trained on all filled events in all regimes):

| Item | Value |
|---|---|
| Status / reasons | `UNVALIDATED`: holdout entries 0 < 100; distinct dates 11 < 50; holdout covers fewer than 2 regimes; no Brier skill over the base rate on holdout; calibration error None > 0.06; too few selected holdout trades |
| Samples | {"development_filled": 37, "final_calibration": 10, "final_fit": 27, "final_training_after_purge": 37, "holdout_filled": 11} |
| Holdout n / base rate (train) / observed TARGET rate | — / — / — |
| Brier / Brier skill / log loss / ECE | — / — / — / — |
| Shuffled-label skill mean / q95 / permutation p | — / — / — |
| Threshold (chosen on development OOS) / selected n / selected mean net | — / 0 / — |
| Precision / recall of TARGET at the threshold | — / — |

**Capital-constrained book** (10 equal slots, holdout, 25 bps):
- 11 trades taken and 0 skipped for capacity.
- Total -2.30%, CAGR -2.14%, max drawdown -2.51%.
- Average exposure 3.9%, maximum 2 positions.
- SPY over the same period 21.31%; SPY × exposure 0.82%.

### h7_catalyst_compression: Catalyst + compression concurrence

- **Hypothesis:** The intersection of a compression base and a fresh positive catalyst beats either component alone.
- **Trigger:** prior-day `squeeze_pct_120 <= 0.4` AND prior-day `base_length >= 8` AND `cat_positive_recent_5d >= 1` AND `dist_pivot_atr >= -0.5` AND `rs_bench_20 > 0` (requires point-in-time catalysts). Invalidation hint: close − 1.5 ATR. Allowed regimes: trending_up, choppy_neutral, rebound. Default enabled: True; weak prior: False.
- **Disposition: `NOT_EVALUABLE_MISSING_INPUT`.** not failed alpha: no event can exist without that input. Holm p = —. Generated recommendation: NOT EVALUABLE — needs point-in-time catalyst history.

| Triggered | Gate eligible | Quality scored | Levels valid | Regime allowed | After de-dup | Filled | Completed |
|---|---|---|---|---|---|---|---|
| 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

*The funnel covers the whole decision window (development and holdout), at H = 20.*

> requires catalysts available_at <= decision time; CI has no point-in-time catalyst history. No events were manufactured. This is **not** evidence against the hypothesis.

### h8_turn_of_month: Turn-of-month seasonality (weak prior)

- **Hypothesis:** Entries on the last session of a month in uptrending names capture a turn-of-month effect; treated as a weak prior subject to multiple-testing penalty and strict holdout.
- **Trigger:** last NYSE session of the month (from the published calendar) AND `above_sma200 == 1` AND `-1 <= dist_sma20_atr <= 1.5`; constant setup score 0.1. Invalidation hint: close − 1.5 ATR. Allowed regimes: trending_up, choppy_neutral, rebound. Default enabled: False; weak prior: True.
- **Disposition: `FAILED_REAL_OOS`.** mean net @25bps not above the matched-random median; failed checks: weeks, regimes, p10_pos_25, mean_pos_50, holm, beats_random_p95, forecast_ok. Holm p = 1.0. Generated recommendation: MODIFY OR RETIRE — redesign entry/exit before re-testing under a new protocol version.

| Triggered | Gate eligible | Quality scored | Levels valid | Regime allowed | After de-dup | Filled | Completed |
|---|---|---|---|---|---|---|---|
| 1904 | 1639 | 1639 | 725 | 678 | 660 | 621 | 621 |

*The funnel covers the whole decision window (development and holdout), at H = 20.*

**Primary configuration, development vs holdout** (rule trades, H = 20, 25 bps):

| Metric | Development | Holdout |
|---|---|---|
| Signals (after de-dup) | 505 | 155 |
| Filled | 481 | 140 |
| No fill | 24 | 15 |
| Completed (TARGET/STOP/TIME) | 481 | 140 |
| Fill rate | 95.2% | 90.3% |
| Mean net / trade | -0.111% | 0.129% |
| Median net / trade | -2.652% | -2.502% |
| Win rate (net > 0) | 41.6% | 37.1% |
| Mean R | -0.067 | -0.036 |
| Median R | -1.046 | -1.047 |
| R p10 (lower tail) | -1.099 | -1.108 |
| Expected shortfall 5% (net) | -6.98% | -6.96% |
| Profit factor (n ≥ 30) | 0.950 | 1.060 |
| P(target before stop) | 38.0% | 34.4% |
| Target / stop / time exits | 163 / 266 / 52 | 41 / 78 / 21 |
| Same-bar ambiguous (booked stop) | 0 | 0 |
| Gap through stop | 24 | 5 |
| Mean net excl. ambiguous (UNRESOLVED) | -0.111% | 0.129% |
| Mean sessions held | 8.1 | 8.9 |
| Mean MFE / MAE | 3.90% / -3.08% | 4.40% / -3.06% |
| Bootstrap mean p5 / p10 / p50 / p90 / p95 | -0.63% / -0.53% / -0.12% / 0.31% / 0.45% | -0.87% / -0.65% / 0.10% / 0.83% / 1.05% |
| One-sided bootstrap p (mean ≤ 0) | 0.6475 | 0.4225 |
| Distinct decision weeks / dates | 49 / 49 | 15 / 15 |
| Regimes present | 2 | 1 |

**Holdout mean net by horizon × cost** (n = completed trades):

| Horizon | 0 bps | 10 bps | 25 bps | 50 bps |
|---|---|---|---|---|
| H=5 | 0.596% (n=142) | 0.395% (n=142) | 0.095% (n=142) | -0.405% (n=142) |
| H=10 | 0.411% (n=142) | 0.210% (n=142) | -0.090% (n=142) | -0.588% (n=142) |
| H=15 | 0.785% (n=142) | 0.584% (n=142) | 0.282% (n=142) | -0.218% (n=142) |
| H=20 | 0.631% (n=140) | 0.429% (n=140) | 0.129% (n=140) | -0.371% (n=140) |

**Development mean net by horizon × cost:**

| Horizon | 0 bps | 10 bps | 25 bps | 50 bps |
|---|---|---|---|---|
| H=5 | 0.177% (n=496) | -0.023% (n=496) | -0.323% (n=496) | -0.820% (n=496) |
| H=10 | 0.643% (n=496) | 0.441% (n=496) | 0.141% (n=496) | -0.359% (n=496) |
| H=15 | 0.462% (n=496) | 0.262% (n=496) | -0.039% (n=496) | -0.537% (n=496) |
| H=20 | 0.390% (n=481) | 0.190% (n=481) | -0.111% (n=481) | -0.609% (n=481) |

**Holdout by regime**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| trending_up | 140 | 0.129% | 37.1% | -0.036 |  |

**Holdout by sector**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| communication_services | 16 | -1.508% | 25.0% | -0.526 |  |
| consumer_discretionary | 8 | 1.636% | 50.0% | 0.453 | yes |
| consumer_staples | 15 | -1.950% | 13.3% | -0.638 |  |
| energy | 12 | 1.643% | 50.0% | 0.593 |  |
| financials | 15 | 1.853% | 53.3% | 0.512 |  |
| health_care | 9 | 1.032% | 44.4% | 0.236 | yes |
| industrials | 6 | 1.062% | 50.0% | 0.203 | yes |
| materials | 12 | 0.281% | 33.3% | -0.134 |  |
| real_estate | 6 | -0.508% | 33.3% | -0.033 | yes |
| technology | 23 | 1.100% | 47.8% | 0.185 |  |
| utilities | 18 | -1.694% | 22.2% | -0.625 |  |

**Holdout by liquidity quartile (development-set edges)**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| Q1 | 22 | -0.932% | 31.8% | -0.367 |  |
| Q2 | 19 | -0.182% | 31.6% | -0.131 |  |
| Q3 | 36 | -0.039% | 30.6% | -0.181 |  |
| Q4 | 63 | 0.689% | 44.4% | 0.191 |  |

**Holdout by year**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| 2025 | 75 | 0.661% | 42.7% | 0.107 |  |
| 2026 | 65 | -0.486% | 30.8% | -0.201 |  |

**Holdout by exit type**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| STOP | 78 | -3.681% | 0.0% | -1.113 |  |
| TARGET | 41 | 7.101% | 100.0% | 1.870 |  |
| TIME | 21 | 0.665% | 52.4% | 0.245 |  |

**Development by year**

| Group | n | Mean net | Win rate | Mean R | n < 10 |
|---|---|---|---|---|---|
| 2019 | 34 | -0.543% | 38.2% | -0.114 |  |
| 2020 | 92 | 1.728% | 58.7% | 0.333 |  |
| 2021 | 138 | 0.114% | 45.6% | 0.018 |  |
| 2022 | 11 | -3.050% | 9.1% | -0.822 |  |
| 2023 | 78 | -1.037% | 32.0% | -0.292 |  |
| 2024 | 109 | -0.499% | 36.7% | -0.199 |  |
| 2025 | 19 | -2.140% | 21.1% | -0.420 |  |

**Comparisons and robustness** (holdout, 25 bps):

| Check | Value |
|---|---|
| Matched random (50 draws): median / p05 / p95 | 0.648% / -0.159% / 1.818% |
| Strategy percentile vs random | 0.16 |
| SPY over the same windows: mean SPY / mean excess / share beating SPY | 0.833% / -0.704% / 36.4% |
| Sector group A mean net (n) | 0.016% (84) |
| Sector group B mean net (n) | 0.297% (56) |
| Quality vs net R, holdout: Spearman (n, weeks) [90% CI] | 0.246 (140, 15) [0.0645, 0.3882] |
| Quality vs net R, development: Spearman (n) [90% CI] | -0.075 (481) [-0.168, 0.022] |
| Cross-sector model Brier skill A→B / B→A | -0.1209 / -0.1914 |

**Forecast model** (existing competing-risk model with promotion-policy-2 gates, trained on all filled events in all regimes):

| Item | Value |
|---|---|
| Status / reasons | `UNVALIDATED`: distinct dates 15 < 50; holdout covers fewer than 2 regimes; no Brier skill over the base rate on holdout; calibration error 0.1362 > 0.06; selected trades' p10 mean net return <= 0 at 25 bps/side; holdout skill -0.1183 not above the shuffled-label 95% quantile 0.01718 (permutation p=0.9524) |
| Samples | {"development_filled": 542, "final_calibration": 136, "final_fit": 406, "final_training_after_purge": 542, "holdout_filled": 142} |
| Holdout n / base rate (train) / observed TARGET rate | 142 / 0.321 / 0.3028 |
| Brier / Brier skill / log loss / ECE | 0.23646 / -0.1183 / 0.6736 / 0.1362 |
| Shuffled-label skill mean / q95 / permutation p | -0.05069 / 0.01718 / 0.9524 |
| Threshold (chosen on development OOS) / selected n / selected mean net | 0.26 / 55 / 0.121% |
| Precision / recall of TARGET at the threshold | 0.3273 / 0.4186 |

| Fold | Train n | Test n | Test window | Brier skill | ECE | Base rate |
|---|---|---|---|---|---|---|
| 2 | 252 | 99 | 2021-10-29 → 2023-03-31 | -0.469 | 0.36042 | 0.4325 |
| 3 | 349 | 90 | 2023-04-28 → 2024-04-30 | -0.05072 | 0.16842 | 0.3639 |
| 4 | 437 | 99 | 2024-05-31 → 2025-02-28 | -0.00649 | 0.09465 | 0.3387 |

*Holdout reliability:* [0.1,0.2) n=65 pred 0.1602 obs 0.2769; [0.2,0.3) n=22 pred 0.2 obs 0.3182; [0.3,0.4) n=23 pred 0.3382 obs 0.3478; [0.4,0.5) n=4 pred 0.4255 obs 0.25; [0.6,0.7) n=28 pred 0.6154 obs 0.3214

**Capital-constrained book** (10 equal slots, holdout, 25 bps):
- 124 trades taken and 16 skipped for capacity.
- Total 1.07%, CAGR 0.80%, max drawdown -6.95%.
- Average exposure 32.2%, maximum 10 positions.
- SPY over the same period 29.39%; SPY × exposure 9.47%.

## 6. Baselines

### momentum_20

| Metric | Development | Holdout |
|---|---|---|
| Signals (after de-dup) | 126 | 30 |
| Filled | 117 | 30 |
| No fill | 9 | 0 |
| Completed (TARGET/STOP/TIME) | 117 | 30 |
| Fill rate | 92.9% | 100.0% |
| Mean net / trade | 0.266% | 1.189% |
| Median net / trade | -3.018% | -2.478% |
| Win rate (net > 0) | 41.0% | 43.3% |
| Mean R | 0.046 | 0.209 |
| Median R | -1.029 | -0.739 |
| R p10 (lower tail) | -1.116 | -1.250 |
| Expected shortfall 5% (net) | -11.64% | -9.09% |
| Profit factor (n ≥ 30) | 1.088 | 1.432 |
| P(target before stop) | 38.6% | 46.4% |
| Target / stop / time exits | 39 / 62 / 16 | 13 / 15 / 2 |
| Same-bar ambiguous (booked stop) | 0 | 0 |
| Gap through stop | 15 | 5 |
| Mean net excl. ambiguous (UNRESOLVED) | 0.266% | 1.189% |
| Mean sessions held | 8.8 | 7.7 |
| Mean MFE / MAE | 5.94% / -4.25% | 6.72% / -4.29% |
| Bootstrap mean p5 / p10 / p50 / p90 / p95 | -0.80% / -0.58% / 0.28% / 1.17% / 1.41% | -0.99% / -0.55% / 1.20% / 2.93% / 3.43% |
| One-sided bootstrap p (mean ≤ 0) | 0.3535 | 0.1955 |
| Distinct decision weeks / dates | 117 / 117 | 30 / 30 |
| Regimes present | 4 | 1 |

| Horizon | 0 bps | 10 bps | 25 bps | 50 bps |
|---|---|---|---|---|
| H=5 | — | — | — | — |
| H=10 | — | — | — | — |
| H=15 | — | — | — | — |
| H=20 | 1.696% (n=30) | 1.493% (n=30) | 1.189% (n=30) | 0.684% (n=30) |

Capital-constrained book:
- 30 trades taken and 0 skipped.
- Total 3.56%, CAGR 2.72%, max drawdown -2.90%.
- Average exposure 7.1%; SPY over the same period 27.04%.

### breakout_basic

| Metric | Development | Holdout |
|---|---|---|
| Signals (after de-dup) | 1815 | 510 |
| Filled | 1718 | 478 |
| No fill | 97 | 32 |
| Completed (TARGET/STOP/TIME) | 1718 | 478 |
| Fill rate | 94.7% | 93.7% |
| Mean net / trade | -0.006% | -0.021% |
| Median net / trade | -2.508% | -2.955% |
| Win rate (net > 0) | 41.6% | 40.2% |
| Mean R | -0.029 | -0.051 |
| Median R | -1.047 | -1.052 |
| R p10 (lower tail) | -1.118 | -1.094 |
| Expected shortfall 5% (net) | -7.64% | -7.40% |
| Profit factor (n ≥ 30) | 0.997 | 0.991 |
| P(target before stop) | 37.4% | 36.5% |
| Target / stop / time exits | 556 / 931 / 231 | 156 / 271 / 51 |
| Same-bar ambiguous (booked stop) | 0 | 0 |
| Gap through stop | 155 | 35 |
| Mean net excl. ambiguous (UNRESOLVED) | -0.006% | -0.021% |
| Mean sessions held | 9.1 | 8.3 |
| Mean MFE / MAE | 4.10% / -3.19% | 4.26% / -3.32% |
| Bootstrap mean p5 / p10 / p50 / p90 / p95 | -0.28% / -0.22% / -0.00% / 0.21% / 0.27% | -0.47% / -0.38% / -0.03% / 0.30% / 0.39% |
| One-sided bootstrap p (mean ≤ 0) | 0.5035 | 0.5385 |
| Distinct decision weeks / dates | 237 / 700 | 68 / 220 |
| Regimes present | 4 | 4 |

| Horizon | 0 bps | 10 bps | 25 bps | 50 bps |
|---|---|---|---|---|
| H=5 | — | — | — | — |
| H=10 | — | — | — | — |
| H=15 | — | — | — | — |
| H=20 | 0.480% (n=478) | 0.279% (n=478) | -0.021% (n=478) | -0.520% (n=478) |

Capital-constrained book:
- 305 trades taken and 173 skipped.
- Total 0.72%, CAGR 0.50%, max drawdown -11.66%.
- Average exposure 70.8%; SPY over the same period 36.96%.

| SPY buy-and-hold (price only) | First | Last | Total | CAGR | Max drawdown |
|---|---|---|---|---|---|
| Development | 2019-10-23 | 2025-05-01 | 86.26% | 11.95% | -34.18% |
| Holdout | 2025-05-02 | 2026-10-21 | 36.41% | 23.55% | -9.13% |

> 100% exposure, price-only; NOT a fair comparator for a low-frequency swing system. The *always flat* baseline is 0 by definition.

## 7. Cross-hypothesis views (holdout, 25 bps unless noted)

| Hypothesis | 0 bps | 10 bps | 25 bps | 50 bps | Excess vs SPY (same windows) | Share beating SPY |
|---|---|---|---|---|---|---|
| h1_vcp_breakout | 0.356% | 0.156% | -0.144% | -0.642% | -1.173% | 43.8% |
| h2_rs_pullback | 0.464% | 0.263% | -0.037% | -0.536% | -0.856% | 33.3% |
| h4_oversold_recovery | -0.599% | -0.798% | -1.095% | -1.588% | -0.864% | 28.6% |
| h5_sector_rotation | 0.536% | 0.335% | 0.034% | -0.465% | -0.776% | 35.1% |
| h6_gap_and_hold | -1.601% | -1.798% | -2.092% | -2.580% | -3.433% | 27.3% |
| h8_turn_of_month | 0.631% | 0.429% | 0.129% | -0.371% | -0.704% | 36.4% |

**What the matched-random comparison says about H8:** random stocks on the same 15 month-end dates earned a median of 0.648% net. Random stocks on H5's dates earned 0.020%. So in this sample the *date* appears to carry the turn-of-month return, while H8's stock filters (above SMA200, near SMA20) selected worse-than-random names. With 15 clusters this is a hypothesis, not a finding.

## 8. Engineering tests (what is and is not verified)

**Suite status at merge:**
- Python: 674 tests passing in CI on Python 3.11, from `apps/pi-worker`, `uv run pytest`.
- Lint and workflow checks: `ruff check src tests` and `actionlint` are clean.
- Secret scan: `scripts/secret-scan.mjs` is clean.
- Web suites: unchanged by Phase 14 and not re-counted here; see `docs/TESTING_AND_METHODOLOGY_REPORT.md`.
- **None of the engineering tests establish market alpha.** They verify that the pipeline computes what it claims.

**Phase 14 tests**

`apps/pi-worker/tests/test_real_equities.py` (28 tests):

| Test | Asserts |
|---|---|
| experiment_id_is_a_safe_slug | Path, shell and unicode tricks are rejected; years are bounded to 3–10. |
| protocol_document_matches_the_frozen_code | PROTOCOL.md quotes the code's protocol version and sha256; the hash is stable. |
| historical_split_is_chronological_embargoed_and_label_complete | 252-session warm-up; holdout ≈ 20%; embargo is exactly 5 NYSE sessions; 22-session label tail; time_start honoured. |
| missing_keys_block_with_no_synthetic_fallback | No download is attempted; BLOCKED exit 2; all seven files say NOT RUN; key values never appear. |
| wrong_feed_is_source_invalid | A non-SIP feed is rejected before any provider request. |
| auth_failure_is_source_invalid_and_never_echoes_keys | HTTP 403 → SOURCE_INVALID exit 3; no key material in any output. |
| provider_bar_after_latest_session_is_source_invalid | A future-dated provider bar aborts the run; the inventory is still written. |
| incomplete_history_excludes_symbols_and_short_benchmark_aborts | Short, stale and duplicate histories are classified; a short SPY → INSUFFICIENT_HISTORY. |
| dataset_counts_and_only_stocks_are_traded | 78 = 1 + 11 + 66; ETFs are never candidates. |
| no_future_bars_change_any_past_decision | Truncating future bars leaves every earlier signal, feature, level and outcome identical (causality). |
| label_availability_no_decision_without_a_complete_label | No DATA_END outcomes inside the decision window for full-history symbols. |
| horizon_expiry_exits_at_the_close_h_sessions_after_fill | TIME exit at fill + H for H = 5/10/15/20. |
| same_bar_ambiguity_is_stop_first_and_unresolved_sensitivity_excludes_it | A bar touching both → STOP and counted; the UNRESOLVED mean excludes it. |
| gap_through_stop_exits_at_the_worse_open | Gap below the stop exits at the open, not at the stop. |
| net_return_and_r_multiple_at_cost | Net and R formulas at 0/10/25/50 bps; agree with `research.evaluate.net_at_cost`. |
| dedup_skips_signals_while_a_position_is_pending_or_open | One position per (strategy, symbol). |
| portfolio_respects_slots_and_marks_to_market | 10 slots: 2 of 12 simultaneous trades skipped; return arithmetic exact. |
| frozen_disposition_rules | Every disposition branch, including the DEMO cap. |
| holm_adjustment_is_monotone | Holm step-down values. |
| demo_run_writes_the_seven_files_labelled_demo | End-to-end fixture run; manifest hash quoted by the metrics; DEMO banners. |
| demo_run_records_every_hypothesis_and_never_validates | All 8 hypotheses present; funnel monotone; baselines present. |
| outputs_contain_no_vendor_prices_or_trade_rows | No price or volume keys and no NaN in the JSON; the CSV has a fixed aggregate schema. |
| fixture_runs_can_never_be_filed_as_research_results | DEMO output under research-results/ is refused and nothing is written. |
| research_package_never_touches_release_signing_discord_or_orders | Banned imports and strings in the research package. |
| budget_checkpoint | Static Actions-minutes checkpoint proceeds, or skips with exit 3. |
| cli_rejects_bad_inputs_without_writing | Bad id or years → exit 5, no files. |
| inspector_counts_raw_rows_before_cleaning | Duplicate, invalid and zero-volume rows are counted before normalisation. |
| loaded_type_is_constructible_for_offline_review | Typed container smoke test. |

`apps/pi-worker/tests/test_real_equities_workflow.py` (10 tests) covers:
- `workflow_dispatch` only; bounded inputs; no `research_only` input;
- `contents: read`, no `id-token`, no writes or pushes;
- `main` only; environment `real-equities-research`; ubuntu runner; timeouts; non-cancelling concurrency;
- only the two Alpaca secrets, and only in the preflight and research steps;
- step order (preflight → budget → research); pinned actions; no Actions cache;
- the artifact is exactly the seven files, kept 7 days;
- `PRODUCTION_REGISTRY_UNCONFIGURED` preflight gating in drift and weekly training;
- the preflight CLI behaviour.

**Older suites that matter here:**
- `test_validation_embargo.py` (8): NYSE-session embargo.
- `test_feature_parity.py` (21): live and training feature vectors are identical.
- `test_execution.py` (9): fill and exit rules.
- `test_levels_quality.py` (29): levels and Quality.
- `test_research_redteam.py` (7): planted-alpha and shuffled-label controls.
- `test_history_cache.py` (8): bounded, checksummed history.

**Not verified by any test (reviewer attention):**
1. Correctness of Alpaca's own split adjustments. HON is flagged, but there is no independent price source to cross-check against.
2. Whether daily bars reflect the regular session only. This matters for gap and open fills.
3. Fill realism versus intraday reality: queue position, partial fills, opening-auction prices.
4. Statistical calibration of the weekly-block bootstrap for 20-session overlapping trades (§9).
5. The power of each disposition rule. No simulation of false-positive or false-negative rates under the frozen thresholds exists.

## 9. Known weaknesses and threats to validity (self-identified)

These are seeds for your review, not conclusions. Each is either verified in code or flagged as unverified.

| # | Area | Issue | Direction of bias / impact |
|---|---|---|---|
| 1 | Statistics | The bootstrap resamples **decision weeks**, but holding periods run up to 20 sessions (about 4 weeks), so trades in adjacent weeks overlap. Blocks shorter than the dependence length make the CIs **too narrow**. | Overstates significance in either direction. Neither the NO_TRADE nor the FAILED verdict depends on it, but any future VALIDATED claim would. Suggest blocks of at least 4 weeks or a stationary bootstrap. |
| 2 | Statistics | The FAILED rule counts **trades** (n ≥ 100), not independent clusters. H8 has 140 trades in only **15 weeks** and 1 regime. | H8's FAILED label rests on 15 clusters. It is weak evidence either way. Consider a minimum cluster count for FAILED as well. |
| 3 | Levels vs costs | `compute_levels` screens for net R/R ≥ 1.5 assuming **10 bps per side**, but evaluation uses 25 bps. Signals close to the R/R floor are uneconomic at the primary cost. | Slightly pessimistic for the rules as live-configured; optimistic about the screen's protection. A cost-consistent screen is a cheap pre-registered variant. |
| 4 | Universe | 66 surviving large caps chosen today, with current sector labels. | Survivorship biases results upward, yet they are still NO_TRADE. Small n is why 4 of 6 hypotheses are INSUFFICIENT. |
| 5 | Data | Split-only, price-only returns: dividends and spin-offs are not adjusted (EXC 2022, HON 2026 suspect). SPY's return also excludes dividends. | Mixed: small per trade, but an event inside a trade window can flip that trade. |
| 6 | Holdout regime | The holdout is mostly `trending_up`, with SPY +36% and max drawdown −9%. Every holdout trade of H1, H2, H4, H6 and H8 is in `trending_up`; H5 has 7 of 225 in `rebound`. | The holdout can't show regime robustness. Long-only swing rules are judged against a strong trend year. |
| 7 | Baseline design | Matched-random and the baselines use the generic hint close − 1.5 ATR, while H1/H2/H4/H6 use their own hints (1.6 / 1.4 / 1.2 ATR or the pre-gap close). Random draws are not de-duplicated. | The comparison mixes the selection effect with the level effect for those strategies. H5 and H8 use 1.5 ATR, so for them it is like-for-like. |
| 8 | Model training | The forecast model trains on all filled events in all regimes, without de-dup (as in production), while rule economics use regime-allowed, de-duplicated trades. | Heavily overlapping training rows reduce the effective sample. Neither choice yields skill here. |
| 9 | Same-bar rule | 0 ambiguous bars across all hypotheses. This is plausible, because the stop-to-target span is at least about 2.5 ATR, but it is not independently verified with intraday bars. | If wrong, stop-first would understate results. Verify with 15-minute bars. |
| 10 | Earnings | No point-in-time earnings calendar: no veto, and Quality uses earnings=UNKNOWN for every trade. | Trades through earnings add variance. The live system would veto some of them. |
| 11 | Disposition thresholds | INSUFFICIENT requires at least 100 *development* trades (H2 has 98). Thresholds are frozen but their power is uncharacterised. | Borderline cases are arbitrary. Pre-register a power analysis instead. |
| 12 | Multiple testing | 229 configurations were computed. Dispositions use only the primary configuration (Holm over 3). The secondary grids (cost, horizon, ablations, breakdowns) are descriptive only. | Do not mine the grids for winners. For example, H2's 'Quality top half' +1.78% is a candidate, not a finding. |

## 10. Code map (verify claims here)

| Concern | Location (`apps/pi-worker/src/apex_worker/`) |
|---|---|
| Frozen protocol, boundaries, id sanitising | `research/realeq/protocol.py` (`PROTOCOL`, `compute_boundaries` ~L170) |
| Credentials, raw-response inspection, data quality and abort rules | `research/realeq/data.py` (`InspectingAdapter` ~L83, `fetch` ~L150, `quality_inventory` ~L199) |
| Orchestration and freeze-before-metrics | `research/realeq/runner.py` (`run` ~L117) |
| Trade statistics, bootstrap, dedup, baselines, portfolio, model, dispositions | `research/realeq/analysis.py` (`trade_stats` ~L95, `boot_means` ~L63, `dedup` ~L77, `BaselineFactory` ~L190, `portfolio` ~L286, `model_block` ~L353, `classify` ~L419, `run_analysis` ~L479) |
| Report writers | `research/realeq/report.py` |
| Signal plugins (triggers, invalidation hints) | `signals/strategies/library.py` (~L63–L280) |
| Price levels | `signals/levels.py` (`compute_levels`) |
| Quality ranking | `signals/quality.py` |
| Gates and point-in-time event builder | `research/dataset.py` (`gate_mask`, `build_events`) |
| Fill and exit simulator | `research/execution.py` (`simulate`) |
| Walk-forward, purge, embargo | `research/validation.py` (`walk_forward`, `_purge`, `embargo_limit`) |
| Promotion-policy-2 gates | `research/evaluate.py` (`evaluate_strategy`, `PromotionPolicy`) |
| Bounded SIP history | `market/history.py` (`sync_history`); adapter `market/adapters/alpaca.py` |
| Workflow | `.github/workflows/apex-real-equities-research.yml` |

## 11. Reproduce

```bash
# offline, no keys: the full pipeline on the DEMO fixture (Alpaca wire-format emulation over synthetic data; NOT market data)
cd apps/pi-worker && uv sync --locked
uv run apex research real-equities --source fixture --experiment-id demo-review-check --out /tmp/demo-review --years 8
uv run pytest tests/test_real_equities.py tests/test_real_equities_workflow.py -q

# real data (owner, GitHub UI): Actions -> apex-real-equities-research -> Run workflow on main
#   source=alpaca, years=8, universe=fixed78, experiment_id=<new id>
# needs ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY secrets (real-equities-research environment or repository)
```

The code's protocol hash must equal the hash in PROTOCOL.md; a test enforces this. Any methodological change must bump `PROTOCOL_VERSION` and be run as a new experiment id. Never edit the published files of an existing experiment.

## 12. Questions we want answered

- Is any part of the pipeline leaking future information? Consider features, regime labels, sector ranks, the liquidity-quartile edges, threshold choice and calibration.
- Are the fill and exit rules conservative enough, or too conservative, for liquid large caps? Is the 2-session limit-zone entry the right price-point design? Should the zone or the target scale with volatility regime?
- Would different stop and target geometry turn H5's positive gross edge (+0.54% at 0 bps) into a net edge? Examples: 1R/3R, ATR trailing stops, time stops at H = 10. Propose the test with frozen success criteria.
- Is 25 bps per side a realistic cost for these names? Should costs depend on spread and liquidity, instead of being a flat 25 bps?
- What universe expansion (point-in-time S&P 500 or Russell 1000 constituents) and data source would give each hypothesis enough independent trades? Estimate the power at plausible effect sizes.
- Should H8 be retired, or re-specified as a date effect (turn-of-month on the index or on all eligible names) given the matched-random observation in §7?
- Is the Quality score worth keeping as a ranking? If so, how should it be re-fit without touching the holdout?
- Are the frozen disposition thresholds sensible? Propose a power-based replacement for `realeq-protocol-2`.

