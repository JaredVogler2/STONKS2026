# Real-equity experiment protocol `realeq-protocol-2` (frozen)

- **Status:** registered 2026-10-22, before any protocol-2 outcome was computed on market data. Only synthetic fixtures have been run.
- **Machine copy:** `PROTOCOL_V2` in [`apps/pi-worker/src/apex_worker/research/realeq/protocol_v2.py`](../../apps/pi-worker/src/apex_worker/research/realeq/protocol_v2.py).
- **Hash:** sha256 `a15b4e92013657ab2fdef79cf860a3b257fcf4517649f035113dadb914444c9f` of its canonical JSON, which includes the universe snapshot's sha256. A test fails if the code and this document disagree.
- **Changing it:** any change is a new protocol version and a new experiment. `realeq-protocol-1` ([PROTOCOL.md](PROTOCOL.md)) is unchanged and stays the runner's default.
- **Mandate:** [Issue #12](https://github.com/JaredVogler2/STONKS2026/issues/12) and [`amendments/quant-v2-2026-10-22/`](../../amendments/quant-v2-2026-10-22/) (milestone M3: preregistration before execution).
- **Engineering record:**
  - [M0_AUDIT.md](../../docs/quant-v2/M0_AUDIT.md) (P0 audit);
  - [M1_ENGINE.md](../../docs/quant-v2/M1_ENGINE.md) (execution, inference, controls);
  - [PROTOCOL_V2_DESIGN.md](../../docs/quant-v2/PROTOCOL_V2_DESIGN.md) (universe, features, strategies, calibration of this design);
  - [P0_CORRECTNESS_REPORT.md](../../docs/quant-v2/P0_CORRECTNESS_REPORT.md) (sign-off).
- **Run:**
  - Command: `apex research real-equities --protocol realeq-protocol-2 --universe u1-sp500 --source alpaca`.
  - Workflow: the [`apex-real-equities-research`](../../.github/workflows/apex-real-equities-research.yml) dispatch, with input `protocol: realeq-protocol-2`.
  - Research only: it never signs, registers, promotes, messages or trades.

## 1. Why this design: what is contaminated and what is not

- **What r2 touched:** the first real experiment, r2, downloaded data from 2018-10-22 and evaluated outcomes for decisions from 2019-10-23.
- **What shaped S1–S5:** its 2025-05-02 → 2026-09-21 holdout and its 229 reported configurations shaped the S1–S5 ideas. That window, and all of r2's development window, are **contaminated** for these designs.
- **What is untouched:** no protocol-1 outcome exists for decisions, or label windows, before 2019-10-23.

Protocol-2 therefore separates three windows:

| Window | Decisions | Role |
|---|---|---|
| **DEV** (`DEV_CONTAMINATED`) | 2019-10-23 → data end − 22 sessions | Arm selection, variance and MDE estimates, diagnostics. Never a verdict. |
| **PRESAMPLE** (`FRESH_EVALUATION_PRESAMPLE_UNTOUCHED`) | first session with 252 prior sessions (2017-01-03) → the last whose label (decision + 22 sessions) ends before 2019-10-23 (2019-09-20) | The **one** confirmatory look, for the DEV-selected arm of each strategy. |
| **FORWARD shadow** | from 2026-10-23 | Registered, not run. Earliest evaluation 2027-04-30, under this same protocol. |

**PRESAMPLE is earlier in time, not later.** It is untouched historical data, not future evidence. A validated result here is still research-only until the forward shadow agrees.

## 2. Data and universe

- **Data:**
  - Source: Alpaca historical SIP, `adjustment=split`, `1Day` bars, from 2016-01-04.
  - Data end: 2 NYSE sessions before the latest completed session (finality lag; M0 found vendor revisions within a day of the close).
  - Download: fresh and ephemeral, with no Actions cache. The same abort rules as protocol-1 apply, plus: the benchmark starts by 2016-01-08, and at least 350 usable stocks.
  - Budget: at most 300 provider requests.
- **U1 universe** (`SURVIVORSHIP_LIMITED_CURRENT_CONSTITUENTS`):
  - Source: the pinned S&P 500 snapshot [`universe_sp500_2026_10_22.json`](../../apps/pi-worker/src/apex_worker/research/realeq/universe_sp500_2026_10_22.json) — Wikipedia, CC BY-SA 4.0, raw sha256 recorded.
  - 503 rows, reduced to 500 stocks with one listing per SEC CIK.
  - Membership: a stock is a **member at t only if its `date_added` ≤ t**. Cross-sectional ranks and breadth use members only.
- **References:** SPY and the 11 SPDR sector ETFs. S1–S3 and the legacy rules never trade them; S5 trades the ETFs.
- **U0** (the fixed66 stocks) is reported as a descriptive subset.
- **U2** (verified point-in-time membership with delisted history): `BLOCKED_PIT_UNIVERSE`. No free, licensed, verified source exists.
- **Gates:** the live `GateConfig` defaults:
  - price ≥ $10;
  - median dollar volume ≥ $30M;
  - average volume ≥ 1M shares;
  - ≥ 180 bars;
  - extension ≤ 3.5 ATR;
  - risk-off breaker 0.6.

  Earnings are unknown point-in-time, so every signal is flagged `EARNINGS_UNKNOWN`.
- **Corporate actions:**
  - Splits and spin-offs are fetched for every symbol and page (with yearly windows if a long range is rejected).
  - A trade, strategy or control alike, whose window [decision, exit] contains a **spin-off ex-date** of its symbol is excluded. The registered known events are also excluded: HON 2026-06-29 and EXC 2022-02-02.
  - An unavailable endpoint is recorded as `UNAVAILABLE`, never guessed.

## 3. Execution and costs (`exec-v2.0`, [M1_ENGINE.md](../../docs/quant-v2/M1_ENGINE.md) §1)

- **Decision and timing:** the decision is at the close of t. Orders are valid t+1..t+2. The horizon is 20 sessions.
- **Entry arms:**
  - **E0:** a limit at `entry_max` with the `entry_min` open guard.
  - **E1:** a limit 0.5 ATR below the close, with an open guard one tick above the stop.
- **Exit arms:**
  - **X0:** stop + target (2R, capped below the prior 120-session high) + time exit.
  - **X1:** stop + time exit.
  - **X2:** a trailing stop at the highest close through d−1 minus 3 ATR(d−1).
- **Levels:**
  - `compute_levels` at **25 bps**, so the screen and the evaluation use the same cost.
  - X0 arms keep the resistance cap and the net reward/risk ≥ 1.5 screen.
  - X1 and X2 have no target, so only stop validity applies. That rule is the same for signals and controls.
- **Ambiguity:** where daily bars cannot order events, a trade has pessimistic and optimistic legs. **Verdicts use the pessimistic leg.**
- **Sensitivities:**
  - the optimistic leg;
  - touch-fill with `trade_through_ticks = 1`;
  - costs of 0, 10 and 50 bps per side.

  These are descriptive, never a winner election.
- **De-duplication:** one pending or open position per (strategy, arm, symbol) until the pessimistic exit.

## 4. Strategies (falsification cards)

Each strategy is selected **on DEV only** among the arms below. Its selected arm is evaluated **once** on PRESAMPLE. Plugins: `signals/strategies/quant_v2.py` (version 0.1.0). Features: `features/quant_v2.py` (`qv2-features-1`). Everything is causal, on the benchmark session index.

### S1: leadership continuation after a controlled pullback (arms E0X0, E0X1, E1X0, E1X1; default E0X0)

- **Rationale:** buy sector-relative leaders after a shallow, confirmed pullback rather than at a chase entry.
- **Rule at t:**
  - On the most recent 10-session peak, which is 2–5 sessions old, the stock was a **leader**:
    - its sector percentile of ret_20 and of ret_60 is ≥ 0.75 among eligible members (all eligible if the sector has fewer than 8);
    - rs_bench_60 > 0;
    - ret_20 ≤ ln 1.25.
  - The pullback from that peak's close is 0.5–1.5 ATR.
  - Close > SMA50, and SMA50 is rising.
  - Close > the prior session's high (confirmation).
- **Stop:** the pullback trough − 0.25 ATR, clamped to 1–3 ATR.
- **Null:** per-signal paired net vs the same-session matched control ≤ 0.
- **Mechanism control:** a matched pool of **leaders in any pullback state**.
- **Main confounders:** factor momentum, the 2017–19 bull regime, survivorship.

### S2: market/sector residual momentum (arms E0X1, E0X0; default E0X1)

- **Rationale:** stock-level strength after removing SPY and sector exposure persists over 10–20 sessions.
- **Feature:**
  - The 20-session residual of daily log returns against SPY and (sector ETF − SPY).
  - Betas are fitted by OLS with a tiny ridge on the 126 sessions ending at **t−20**, never the scoring window.
  - The score is the residual divided by the fit's residual SD × √20.
  - A one-factor fit is used when the sector ETF lacks history (XLC before mid-2018).
- **Rule:**
  - only on every 5th session of the index;
  - residual percentile ≥ 0.8 among eligible members;
  - ret_20 > 0;
  - sector breadth (share of eligible sector members above SMA50) ≥ 0.5;
  - |gap_1| ≤ 6%.
- **Stop:** close − 2 ATR.
- **Mechanism control:** a matched pool of **raw-momentum** names chosen by the same filters.

### S3: volatility-contraction breakout, immediate vs retest (arms A_X0, A_X2, C_X0, C_X2; default A_X0)

- **Qualified breakout at b:**
  - close > the 20-session pivot high;
  - prior squeeze percentile ≤ 0.35, base ≥ 10 sessions, depth ≤ 15%;
  - relative volume ≥ 1.3;
  - rs_bench_20 > 0;
  - close > SMA50, and SMA50 is rising;
  - sector RS > 0 when the sector ETF exists.
- **Arm A** buys at b, with the stop 0.5 ATR under the pivot.
- **Arm C** buys the **first** session in b+1..b+3 whose low revisits pivot + 0.5 ATR_b and whose close is back above the pivot. Its stop is min(low, pivot) − 0.5 ATR.
- **Mechanism contrast:** per opportunity, C minus A with the same exit. A missed retest counts 0, so missed runaway winners are charged to C.

### S4: first-publication earnings/guidance continuation — **NOT_EVALUABLE_MISSING_INPUT**

- **Implemented:** the plugin and the availability mapping. `available_at` before the close maps to that session; at or after the close, to the next session; a missing time drops the event. Both are tested on synthetic events.
- **Not evaluated:** no verified point-in-time first-publication source exists, so nothing is backdated and no result is reported.

### S5: sector-ETF trend allocation with cash abstention (arms R10, R5; default R10)

- **Rule:**
  - every R sessions, hold up to 3 sector ETFs above their SMA50, ranked by ret_60 / (rv_20 · √(60/252)), equal weight;
  - fewer qualifiers leave the rest in cash;
  - risk-off or volatility-shock regimes mean all cash.
- **Execution:** at the next open, with the cost on traded weight.
- **Null:** mean daily active return vs **exposure-matched SPY/cash** (yesterday's exposure × SPY return; cash yield 0) ≤ 0.
- **Mechanism contrast:** equal-weight sector ETFs.

**Legacy comparators** (H1, H2, H4, H5, H6, H8):
- run on the same engine (E0X0) with generic matched controls;
- form their own Holm family and never affect S statuses;
- H3 and H7 are `NOT_EVALUABLE_MISSING_INPUT`.

**Descriptive baselines:** top-1 ret_20 every 5 sessions; basic breakout; SPY; flat.

## 5. Matched controls

Per signal, K = 10 controls are drawn without replacement:
- on the **same session**;
- from gate-eligible members, or the strategy's mechanism pool;
- excluding the signal's symbol and that day's other signals;
- from the first stratum with ≥ 5 candidates, in this order: sector × volatility tercile, then sector, then volatility tercile, then pool.

Each control gets:
- the signal's stop distance in ATR;
- the same target rule and policy;
- the same quarantine.

**Values:** per-signal net at 25 bps. **A no-fill counts as 0 for strategy and control.** The draw seed derives from (strategy, arm, pool, symbol, session).

## 6. Arm selection (DEV only)

- **Rule:** the largest DEV studentized paired difference: pessimistic, 25 bps, vs the generic control, taking the smaller t of L = 20 and 40.
- **Eligibility:** arms whose DEV test is not OK are not eligible. If none is eligible, the default arm is used; ties go to the earlier arm.
- **Unselected arms:** their PRESAMPLE rows are discarded unseen and reported as `withheld`.

## 7. Inference and multiplicity (`inference-v2.0`)

- **Unit:** the per-signal paired difference, placed on the decision session.
- **Time axis:** the segment's **gate-open sessions**, where at least one stock passes the live gates. S5 uses every session.
- **Test:**
  - a studentized, null-centred circular block bootstrap;
  - the block-cluster SE includes a Bartlett lag-1 term;
  - 4,999 draws, seed 11.
- **Decision statistic:** the **conservative** result over L = 20 and 40: the largest p and the smallest one-sided 95% lower bound. L = 60 is reported as a sensitivity, because a 684-session window has at most 12 such blocks.
- **Clusters:** fewer than **12** non-empty blocks at any decision length gives `INSUFFICIENT_CLUSTERS`: no p-value, and neither FAILED nor VALIDATED.
- **Multiplicity:** Holm over the primary family {S1, S2, S3, S5} at one-sided α = 0.05.
- **Calibration** (`bench/qv2_p2_inference_battery.py`, 1,000 replicates per design, synthetic): the decision statistic rejects true nulls 3.6–5.0% of the time at nominal 5%, on axes of 480–1,700 sessions, including fat tails with volatility regimes. Tables are in [PROTOCOL_V2_DESIGN.md](../../docs/quant-v2/PROTOCOL_V2_DESIGN.md) §5.
- **Minimum detectable effect:** reported per strategy as (z(1 − 0.05/4) + z(0.8)) × SE, with the SE from DEV projected to the PRESAMPLE n and the realised PRESAMPLE SE.
- **Economic minimum:** +0.30 pp per 20-session signal (paired); +1 bp per day for S5.

## 8. Dispositions (PRESAMPLE, selected arm)

| Disposition | Rule |
|---|---|
| `RULE_VALIDATED_RESEARCH_OOS` | **All** of the following. It is still research-only; the forward shadow is required. <ul><li>clusters OK;</li><li>Holm p < 0.05;</li><li>mean ≥ the economic minimum;</li><li>absolute per-signal mean > 0 at 25 bps;</li><li>paired mean > 0 at 50 bps and under touch-fill;</li><li>positive in both PRESAMPLE halves;</li><li>≥ 2 regime labels with ≥ 15% of signals each;</li><li>DEV paired mean > 0;</li><li>ambiguous trades ≤ 25% of fills.</li></ul> |
| `UNCERTAINTY_TOO_HIGH` | Ambiguous trades > 25% with every other gate passing, **or** the optimistic leg passes the p and mean gates the pessimistic leg fails, at the same Holm multiplier, when the legs differ. |
| `FAILED_WITH_POWER` | Clusters OK, mean ≤ 0, and the largest one-sided 95% upper bound < the economic minimum. |
| `RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD` | Clusters OK, paired mean > 0 and absolute mean > 0, not validated. |
| `INCONCLUSIVE` | Everything else, including `INSUFFICIENT_CLUSTERS`. |
| `NOT_EVALUABLE_MISSING_INPUT` | S4, H3, H7. |
| `RESEARCH_ENGINE_INVALID` | The look-ahead trap was not detected (see §9). No strategy status is reported. |

**Aggregate:** `NO_TRADE` unless at least one strategy is `RULE_VALIDATED_RESEARCH_OOS`. Even then the outcome is research-only, with live alerts disabled, until the forward shadow and the release gates pass.

## 9. Books, sensitivities and engine self-checks

- **Books:** per selected arm and segment, a 10-slot, capital-constrained book with:
  - a 30% sector cap;
  - start-of-session funding;
  - 25 bps costs.

  It is compared with the K matched-control books and exposure-matched SPY/cash. Descriptive.
- **Sensitivities:** cost grid, optimistic leg, touch-fill, L = 60, U0 subset, year / sector / regime breakdowns.
- **Look-ahead trap:**
  - Each PRESAMPLE session, it buys the eligible member with the top *realized forward* 20-session return (E0X1, 2 ATR stop).
  - It must reach p < 0.001, or the run is `RESEARCH_ENGINE_INVALID`.
  - It cheats by construction and is never a strategy.
- **Placebo:** one random member per session. p < 0.01 raises a warning.

## 10. Registration fields (amendment 12 §1)

```yaml
id: realeq-v2-20261022-s1-s5-presample
experiment_status: FROZEN
registered_at: 2026-10-22
registered_by: coding_agent_with_owner_review
hypothesis_family: [S1, S2, S3, S5]   # S4 NOT_EVALUABLE_MISSING_INPUT
primary_test_question: "Does the DEV-selected arm beat a same-session matched executable control, per signal, net of 25 bps?"
null_hypothesis: "Incremental paired mean net per signal <= 0"
universe: {id: U1, point_in_time_status: SURVIVORSHIP_LIMITED (date_added membership), min_liquidity: live GateConfig}
data: {provider: Alpaca, feed: sip, adjusted: split, dataset_hash: "recorded in manifest.json at retrieval"}
chronology:
  known_old_holdout: "2025-05-02..2026-09-21 (CONTAMINATED_FOR_NEW_DESIGN)"
  development_dates: "2019-10-23 .. data end - 22 sessions (DEV_CONTAMINATED)"
  fresh_evaluation_dates: "2017-01-03 .. 2019-09-20 (PRESAMPLE_UNTOUCHED, earlier in time); forward shadow from 2026-10-23"
  purge_sessions: "22 (label) between PRESAMPLE labels and DEV"
strategy_version: "0.1.0 (qv2-features-1)"
entry_policy: "E0 | E1 per arm"
exit_policy: "X0 | X1 | X2 per arm"
horizon_sessions: 20
costs_bps_each_side_primary: 25
cost_stress_each_side: [0, 10, 50]
benchmark: {primary: "same-session sector/volatility matched executable control", secondary: "mechanism pool, raw momentum, breakout, exposure-matched SPY/cash, flat"}
positions: {max_slots: 10, leverage: false, concentration_limits: "one per symbol, sector <= 30% of equity"}
primary_metric: "per-signal paired net return (no-fill = 0) at 25 bps, pessimistic leg"
economic_min_effect: "+0.30 pp per signal (S5: +1 bp/day)"
uncertainty: {resampling: "null-centred studentized circular blocks on gate-open sessions", block_sensitivity_sessions: [20, 40, 60],
              independent_regime_coverage: ">= 12 non-empty blocks; >= 2 regimes with >= 15% of signals",
              type_i_validation: "bench/qv2_p2_inference_battery.py: 3.6-5.0% at nominal 5%"}
multiplicity: {family: [S1, S2, S3, S5], correction: Holm}
research_budget: {github_runner_minutes_max: 45, alpaca_requests_max: 300, grid_arms_max: 12}
protocol_sha256: a15b4e92013657ab2fdef79cf860a3b257fcf4517649f035113dadb914444c9f
mode: RESEARCH_ONLY
production_promotion_allowed: false
group_actionable_allowed: false
```

## 11. Outputs and limitations

**Outputs:** the same seven files as protocol-1: `manifest.json`, `data_quality.json`, `strategy_metrics.json`, `trades_summary.csv`, `comparisons.md`, `recommendations.md`, `execution_log.md`.
- `strategy_metrics.json` consolidates the template's metrics, portfolio, ablation and error-sensitivity sections, with an `index`.
- Everything is aggregates only: no vendor OHLCV, per-trade prices, keys or member positions.
- Publishing is a reviewed PR that adds `review.md`.

**Limitations, written into every report:**
- survivorship (current constituents);
- current sector labels;
- unknown earnings;
- price-only returns;
- daily-bar ambiguity;
- PRESAMPLE precedes the design data;
- for the window's length, the MDE is large (about +1 pp per signal on synthetic panels), so smaller true edges will most likely read `INCONCLUSIVE`.
