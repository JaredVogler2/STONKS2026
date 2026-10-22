# Real-equity experiment protocol `realeq-protocol-2` (frozen)

- **Status:** registered 2026-10-22, before any protocol-2 outcome was computed on market data. **Amended (A1) on 2026-10-22, before any protocol-2 outcome informed the protocol** (§12).
- **Machine copy:** `PROTOCOL_V2` in [`apps/pi-worker/src/apex_worker/research/realeq/protocol_v2.py`](../../apps/pi-worker/src/apex_worker/research/realeq/protocol_v2.py). Where the two copies differ, the machine copy governs.
- **Hash (A1, binding):** sha256 `33bd4e99ecaf06e8fbe0312e2892a0e382d39a632c28280bb66dbd4f5d05475d` of its canonical JSON, which includes the universe snapshot's sha256. A test fails if the code and this document disagree.
- **Hash as first frozen (A0, superseded, kept on record):** `a15b4e92013657ab2fdef79cf860a3b257fcf4517649f035113dadb914444c9f`.
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
- **What protocol-1 never touched:** no protocol-1 outcome exists for decisions, or label windows, before 2019-10-23.
- **What A0 exposed (2026-10-22):** the cancelled A0 run 39829472789 computed PRESAMPLE outcomes and published them in its job log, its job summary and its artifact. The analyst never read them, and no design choice responded to them. But the window is **not untouched**, and this document no longer calls it that (§12).

Protocol-2 therefore separates three windows:

| Window | Decisions | Role |
|---|---|---|
| **DEV** (`DEV_CONTAMINATED`) | 2019-10-23 → data end − 22 sessions | Arm selection, variance and MDE estimates, diagnostics. Never a verdict. |
| **PRESAMPLE** (`EVALUATION_PRESAMPLE_EXPOSED_A0_UNREAD_BY_ANALYST`; evidence type `PRESAMPLE_BACKWARD_TIME_HISTORICAL_ROBUSTNESS`) | first session with 252 prior sessions (2017-01-03) → the last whose label (decision + 22 sessions) ends before 2019-10-23 (2019-09-20) | The **one** confirmatory look, for the DEV-selected arm of each strategy. A trade or control whose exit (either leg) falls on or after 2019-10-23 is excluded (A1). |
| **FORWARD shadow** | from 2026-10-23 | Registered, not run. Earliest evaluation 2027-04-30, under this same protocol. |

**PRESAMPLE is earlier in time, not later.** It is historical data, not future evidence: every report labels it **backward-time historical robustness, not forward out-of-sample**, and states its A0 exposure. A validated result here is still research-only until the forward shadow agrees. (The disposition name `RULE_VALIDATED_RESEARCH_OOS` is kept for continuity; "OOS" there means outside the design data, not later in time.)

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
  - **A1:**
    - An unavailable corporate-actions endpoint stops the run (`SOURCE_INVALID`). A0 recorded `UNAVAILABLE` and continued with only the two registered quarantines.
    - Per-year coverage, spin-off parents not in U1, and the current-ticker limitation are reported.
    - A split ex-date whose split-adjusted close still jumps by more than 25% is **adjustment-suspect**, and its trade windows are quarantined.
  - The quarantine window is the **fixed** label window [decision, decision + 22], extended to a later delayed exit of either leg. It never depends on how the trade turned out (A1).
- **Integrity checks (A1):**
  - impossible bars (open or close outside [low, high]) are removed and become missing sessions;
  - SPY must have every session (its dates are the session axis) and a median volume ≥ 20M shares (consolidated tape, not one venue);
  - the 300-request budget is enforced on every provider call, bars and corporate actions together;
  - a repeated page token stops the run.

## 3. Execution and costs (`exec-v2.1` under A1, [M1_ENGINE.md](../../docs/quant-v2/M1_ENGINE.md) §1)

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
  - **A1:** a missing session while a trade is held is ambiguity `MISSING_BAR`. Its legs are the worst and best of: the stop that day at min(stop, next valid open); the target that day (X0); or continue.
  - An entry window with no bar at all is an incomplete label (`NO_DATA_IN_ENTRY_WINDOW`), not a no-fill.
  - Derived prices (limit − trade-through, target + trade-through, stop + one tick) are compared on the cent grid.
- **Sensitivities:**
  - the optimistic leg;
  - touch-fill with `trade_through_ticks = 1`, for resting buy limits **and** target sell limits (A1), with the re-simulated windows quarantined like the primary ones;
  - costs of 0, 10 and 50 bps per side.

  These are descriptive, never a winner election.
- **De-duplication:** one pending or open position per (strategy, arm, symbol) until the pessimistic exit.

## 4. Strategies (falsification cards)

Each strategy is selected **on DEV only** among the arms below. Its selected arm is evaluated **once** on PRESAMPLE. Plugins: `signals/strategies/quant_v2.py` (version 0.1.1 under A1; 0.1.0 under A0). Features: `features/quant_v2.py` (`qv2-features-1`). Everything is causal, on the benchmark session index.

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
  - Each breakout is judged on its own window, so a newer breakout never hides an older one's retest. If two opportunities retest on the same session, the signal carries the more recent breakout (A1; strategies 0.1.1).
- **Mechanism contrast:** per opportunity, C minus A with the same exit. A missed retest counts 0, so missed runaway winners are charged to C.
  - **DEV only (A1 isolation).** The contrast needs both entry arms, and one of them is not the locked arm, so its PRESAMPLE half is withheld, not computed. It is descriptive and never a gate.
  - An opportunity is **excluded**, not counted as a miss, when its retest session falls outside the breakout's segment, or when either trade is quarantined or label-incomplete (A1). There is no de-duplication: every qualified breakout is its own opportunity.

### S4: first-publication earnings/guidance continuation — **NOT_EVALUABLE_MISSING_INPUT**

- **Implemented:** the plugin and the availability mapping. `available_at` before the close maps to that session; at or after the close, to the next session; a missing time drops the event. Both are tested on synthetic events.
- **Not evaluated:** no verified point-in-time first-publication source exists, so nothing is backdated and no result is reported.

### S5: sector-ETF trend allocation with cash abstention (arms R10, R5; default R10)

- **Rule:**
  - every R sessions, hold up to 3 sector ETFs above their SMA50, ranked by ret_60 / (rv_20 · √(60/252)), equal weight;
  - fewer qualifiers leave the rest in cash;
  - risk-off or volatility-shock regimes mean all cash.
- **Execution:** at the next open, with the cost on traded weight. Regime abstention is checked at rebalance sessions only.
- **Null:** mean daily active return vs **exposure-matched SPY/cash** ≤ 0.
  - **Benchmark (A1):** per session, (1 + prior-close exposure × SPY overnight return) × (1 + post-open exposure × SPY open-to-close return) − 1. Cash yields 0 and the benchmark pays no cost.
  - A0 applied the prior close's exposure to SPY's whole close-to-close return, which mis-times every rebalance day.
- **PRESAMPLE:** only the DEV-selected arm is computed (A1). A0 computed both arms and published the unselected one.
- **Mechanism contrast:** equal-weight sector ETFs.
- **Price-only bias:** dividends are not modelled. A yield gap of about 1%/yr between the held ETFs and SPY moves the daily active return by about 0.4 bp × exposure, against an economic minimum of +1 bp/day.

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
- **S5** has no matched control. Its arms are ranked by the smaller DEV t (L = 20 and 40) of the daily active return vs exposure-matched SPY/cash. A1 registers this; it is what the code always did.
- **Isolation (A1).** The study runs in three phases:
  1. Signals are generated from causal features only.
  2. **Every registered arm is evaluated on DEV only.** The rule picks one arm per strategy, and the choice is hashed (`selection_lock`). The lock is written to the execution log before any PRESAMPLE outcome exists.
  3. **PRESAMPLE is simulated for the locked arms only.**
- **Unselected arms** are never simulated on PRESAMPLE, so no PRESAMPLE outcome of theirs exists, in memory or in any output. They are reported as `withheld`.
  - Under A0 they were simulated and then discarded (7 of 7 unselected S1–S3 arms), and both S5 arms were published.
  - A regression test makes any PRESAMPLE simulation raise an error until the lock exists.

## 7. Inference and multiplicity (`inference-v2.0`; small-sample floor A1)

- **Unit:** the per-signal paired difference, placed on the decision session.
- **Time axis:** the segment's **gate-open sessions**, where at least one stock passes the live gates. S5 uses every session.
- **Test:**
  - a studentized, null-centred circular block bootstrap;
  - the block-cluster SE includes a Bartlett lag-1 term;
  - 4,999 draws, seed 11.
- **Decision statistic:** the **conservative** result over L = 20 and 40: the largest p and the smallest one-sided 95% lower bound. L = 60 is reported as a sensitivity, because a 684-session window has at most 12 such blocks.
- **Small-sample floor (A1):** at each length, p is the **larger** of the bootstrap-t p and the Student-t p with df = non-empty blocks − 1. The bounds use the wider of the two quantiles. It can only make the test more conservative (§12).
- **Clusters:** fewer than **12** non-empty blocks at any decision length gives `INSUFFICIENT_CLUSTERS`: no p-value, and neither FAILED nor VALIDATED.
- **Multiplicity:** Holm over the primary family {S1, S2, S3, S5} at one-sided α = 0.05. A member without a p-value enters with **p = 1**, so the family size stays 4 (A1; A0 dropped it, which shrank the multiplier). The legacy family uses the same rule.
- **Calibration.**
  - **A0 battery:** `bench/qv2_p2_inference_battery.py` measured 3.6–5.0% at nominal 5%. Its designs pair each trade with an independent control, so trades carry almost **no cross-trade dependence** (design effect about 1). It does **not** validate the production rule.
  - **A1 battery:** `bench/qv2_a1_production_null_battery.py` simulates production-shaped nulls: K = 10 same-session, same-sector controls; persistent stock identities; residual beta and style exposure; clustered signals; fat tails. Its table is in §12. The A0 rule over-rejected there. With the floor, size is close to nominal at the Holm first-step level. It stays above nominal under a slowly varying factor premium; that is disclosed, not fixed.
- **Minimum detectable effect:** reported per strategy (S5 per day) as (z(1 − 0.05/4) + z(0.8)) × SE, with the SE from DEV projected to the PRESAMPLE n and the realised PRESAMPLE SE.
  - On production-shaped panels it is about **+1.5–2 pp per signal**, 5–7 times the economic minimum, not the +1 pp A0 quoted from the independent-control battery.
  - Expect `INCONCLUSIVE` or `PROMISING` for realistic edges, and read any `VALIDATED` effect size as inflated (winner's curse).
- **Economic minimum:** +0.30 pp per 20-session signal (paired); +1 bp per day for S5.

## 8. Dispositions (PRESAMPLE, selected arm)

| Disposition | Rule |
|---|---|
| `RULE_VALIDATED_RESEARCH_OOS` | **All** of the following. It is still research-only; the forward shadow is required. <ul><li>clusters OK;</li><li>Holm p < 0.05;</li><li>mean ≥ the economic minimum;</li><li>absolute per-signal mean > 0 at 25 bps;</li><li>paired mean > 0 at 50 bps and under touch-fill;</li><li>positive in both PRESAMPLE halves;</li><li>≥ 2 regime labels with ≥ 15% of signals each;</li><li>DEV paired mean > 0;</li><li>ambiguous trades ≤ 25% of fills.</li></ul> **S5** (A1), all of: clusters OK; Holm p < 0.05; mean daily active ≥ +1 bp; mean daily net > 0 at 25 bps; mean daily active > 0 at 50 bps; positive in both halves; ≥ 2 regime labels with ≥ 15% of invested sessions each; DEV mean > 0. Touch-fill and daily-bar ambiguity do not apply to market orders at the open. |
| `UNCERTAINTY_TOO_HIGH` | Clusters OK and not validated, and either: <ul><li>ambiguous trades > 25% of fills, **whatever the other gates say**; or</li><li>the legs differ and **every** validation gate passes on the optimistic leg, with its Holm p recomputed by entering the optimistic p in the family.</li></ul> (A1: this follows the hashed machine copy. A0's text here required every other gate to pass, and its code checked only the p and mean gates on the optimistic leg.) |
| `FAILED_WITH_POWER` | Clusters OK, mean ≤ 0, and the largest one-sided 95% upper bound < the economic minimum. When the legs differ, the optimistic leg must meet the same two conditions (A1); otherwise `INCONCLUSIVE`. |
| `RULE_ECONOMICALLY_PROMISING_REQUIRES_FORWARD` | Clusters OK, paired mean > 0 and absolute mean > 0, not validated. |
| `INCONCLUSIVE` | Everything else, including `INSUFFICIENT_CLUSTERS`. |
| *Order (A1)* | The first rule that holds, in this order: clusters not OK → `INCONCLUSIVE`; every gate → `VALIDATED`; ambiguous > 25% → `UNCERTAINTY`; optimistic leg passes every gate → `UNCERTAINTY`; `FAILED_WITH_POWER`; `PROMISING`; `INCONCLUSIVE`. |
| `NOT_EVALUABLE_MISSING_INPUT` | S4, H3, H7. |
| `RESEARCH_ENGINE_INVALID` | The look-ahead trap was not detected (see §9). No strategy status is reported, primary or legacy (A1). |

**Decision legs:**
- The decision value is the strategy's pessimistic leg minus the controls' pessimistic leg (**both legs pessimistic**).
- Reported descriptively (A1): what daily bars can actually bound, [strategy pessimistic − controls optimistic, strategy optimistic − controls pessimistic], and the controls' ambiguous share.

**Aggregate:** `NO_TRADE` unless at least one strategy is `RULE_VALIDATED_RESEARCH_OOS`. Even then the outcome is research-only, with live alerts disabled, until the forward shadow and the release gates pass.

## 9. Books, sensitivities and engine self-checks

- **Books:** per selected arm and segment, a 10-slot, capital-constrained book with:
  - a 30% sector cap;
  - start-of-session funding;
  - 25 bps costs.

  It is compared with the K matched-control books and exposure-matched SPY/cash. Descriptive.
  - Every book of a segment runs to the latest exit among the strategy and control trades (A1).
  - The books' SPY/cash curve holds SPY close-to-close with the previous close's exposure; intraday entries and exits are not timed.
- **Sensitivities:** cost grid, optimistic leg, touch-fill, L = 60, U0 subset, year / sector / regime breakdowns.
- **Look-ahead trap:**
  - Each PRESAMPLE session, it buys the eligible member with the top *realized forward* 20-session return (E0X1, 2 ATR stop).
  - It must reach p < 0.001, or the run is `RESEARCH_ENGINE_INVALID`.
  - It cheats by construction and is never a strategy.
- **Placebo:** one random member per session. p < 0.01 raises a warning.

## 10. Registration fields (amendment 12 §1)

```yaml
id: realeq-v2-20261022-s1-s5-presample
experiment_status: FROZEN (amendment A1, pre-evaluation)
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
strategy_version: "0.1.1 (qv2-features-1)"   # A1
entry_policy: "E0 | E1 per arm"
exit_policy: "X0 | X1 | X2 per arm"
horizon_sessions: 20
costs_bps_each_side_primary: 25
cost_stress_each_side: [0, 10, 50]
benchmark: {primary: "same-session sector/volatility matched executable control", secondary: "mechanism pool, raw momentum, breakout, exposure-matched SPY/cash, flat"}
positions: {max_slots: 10, leverage: false, concentration_limits: "one per symbol, sector <= 30% of equity"}
primary_metric: "per-signal paired net return (no-fill = 0) at 25 bps, pessimistic leg"
economic_min_effect: "+0.30 pp per signal (S5: +1 bp/day)"
uncertainty: {resampling: "null-centred studentized circular blocks on gate-open sessions", decision_block_sessions: [20, 40], block_sensitivity_sessions: [60],
              independent_regime_coverage: ">= 12 non-empty blocks; >= 2 regimes with >= 15% of signals",
              type_i_validation: "bench/qv2_p2_inference_battery.py: 3.6-5.0% at nominal 5%"}
multiplicity: {family: [S1, S2, S3, S5], correction: Holm}
research_budget: {github_runner_minutes_max: 45, alpaca_requests_max: 300, grid_arms_max: 12}
protocol_sha256: 33bd4e99ecaf06e8fbe0312e2892a0e382d39a632c28280bb66dbd4f5d05475d   # A1; A0 was a15b4e92013657ab2fdef79cf860a3b257fcf4517649f035113dadb914444c9f
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

## 12. Amendment A1 (2026-10-22, pre-evaluation)

| | |
|---|---|
| Supersedes | A0, sha256 `a15b4e92013657ab2fdef79cf860a3b257fcf4517649f035113dadb914444c9f` |
| Binding hash | `33bd4e99ecaf06e8fbe0312e2892a0e382d39a632c28280bb66dbd4f5d05475d` (machine copy field `amendment`) |
| Reviews behind it | the analyst's own pre-run review, then three independent adversarial reviews (execution, controls and book; statistics and protocol; data and universe), all on synthetic data |
| Thresholds changed | **None.** Unchanged: α, the block lengths (20/40 decision, 60 sensitivity), the 12-block minimum, both economic minimums, the costs, the arms, the arm-selection rule, K = 10, the stratum minimum, every date, the universe and every strategy definition (S3C's rule is now implemented as written). |
| Versions | `exec-v2.1`, `inference-v2.1`, strategies `0.1.1`; features and levels unchanged |

### 12.1 What A0 exposed

A0 run [39829472789](https://github.com/JaredVogler2/STONKS2026/actions/runs/39829472789) (`cb1f494`) was cancelled after its study step had finished. The A1 audit checked the job's step list, ran a redacted scan of its log (line kinds and counts only) and a presence-only scan of its artifact (keys and booleans only). No value was displayed.

| Question | Answer |
|---|---|
| Computed? | **Yes.** PRESAMPLE tests for the DEV-selected arms of S1, S2 and S3, both S5 arms, the legacy rules and the self-checks. The unselected S1–S3 arms were simulated on PRESAMPLE in memory, then discarded. |
| Printed? | **Yes.** The job log has one PRESAMPLE summary line (mean, p) each for S1, S2 and S3. |
| Saved? | **Yes.** The artifact holds all seven files (zip sha256 `380eb7e927dcb61e6e96dc832173288a8746be7bc9d7fd3b55df675eb2e9fa23`, expires 2026-10-29), including both S5 arms' PRESAMPLE numbers. |
| Displayed? | **Yes.** The job summary on the run page embeds the first 40 lines of `comparisons.md`, which include the primary PRESAMPLE table. |
| Inspected by the analyst? | **No.** No value was ever displayed to the analyst. |
| Inspected by anyone else? | **Unknown.** The GitHub API does not reveal page views. |

**Consequence:**
- The window is recorded as **EXPOSED**: role `EVALUATION_PRESAMPLE_EXPOSED_A0_UNREAD_BY_ANALYST`. It is not called untouched anywhere.
- No A1 change responded to an outcome. Every change rests on a defect shown on synthetic data and has a regression test.
- The first A1 run (`qv2-20261022-s1-s5-u1-presample`) is the confirmatory look.
- After the A1 results are published, the A0 artifact is opened once as a reproducibility check, never as a second evaluation.

### 12.2 Changes

Each change has a regression test that fails on A0 behaviour.

| Area | A0 defect | A1 rule | Test |
|---|---|---|---|
| **Isolation** | Unselected S1–S3 arms simulated on PRESAMPLE (7 of 7) and discarded; both S5 arms computed and published; the S3 contrast valued the unselected entry arm on PRESAMPLE. | DEV for every arm, then a hashed `selection_lock` (logged first), then PRESAMPLE for the locked arms only. S5 is simulated segment-locally. The S3 contrast is DEV only. | `test_a1_selection_is_locked_before_any_presample_outcome…` (a PRESAMPLE simulation raises before the lock); `test_a1_split_dev_then_presample_runs_equal_one_run_over_both`; `test_a1_s5_segment_simulation_matches_full_history…` |
| **Inference** | The cited battery had no cross-trade dependence. On production-shaped nulls the rule rejected 5.9–7.9% at nominal 5%. | Student-t floor (df = non-empty blocks − 1) at each length. The residual over-size is disclosed (§12.3). | `test_a1_t_floor_never_lowers_p…`, `test_a1_student_t_tail_matches_reference` |
| **Holm** | Members without a p were dropped, shrinking the multiplier below 4. | A missing p enters as 1 (both families). | `test_a1_holm_counts_members_without_a_p_value_as_one` |
| **Dispositions** | The UNCERTAINTY rule differed between the copies; the optimistic leg was checked on p and mean only; FAILED could rest on the pessimistic leg alone; the order was unstated; RESEARCH_ENGINE_INVALID left legacy statuses. | The machine copy governs; every gate on the optimistic leg with its own Holm p; FAILED needs both legs; order frozen (§8); no status at all when the engine is invalid. | `test_v26_v41_v42_dispositions` (new cases) |
| **S5** | Benchmark mis-timed on rebalance days; no cost, regime or absolute gates; no MDE; selection criterion unregistered. | Overnight move at the prior-close exposure, intraday at the post-open exposure. S5 gates (§8), MDE per day, criterion registered (§6). | `test_a1_s5_exposure_matched_benchmark…`, `test_a1_s5_gates…` |
| **Execution** | A missing held bar could hide a stop (pessimistic leg too high); an entry window without bars was a no-fill (0); float tick arithmetic missed exact one-tick trade-throughs (about 6%); target sells needed no trade-through. | `MISSING_BAR` ambiguity; `NO_DATA_IN_ENTRY_WINDOW` is incomplete; cent-grid comparisons; trade-through on target sells. | `test_a1_missing_bar_while_held…`, `test_a1_entry_window_without_any_bar…`, `test_a1_exact_one_tick…`, `test_a1_touch_fill_sensitivity…` |
| **Labels and quarantine** | A delayed PRESAMPLE exit could reach DEV; a filled trade cut short by the data end was valued; the quarantine window followed the realised (pessimistic) exit, so it selected on the outcome. | DEV-crossing exclusion; incomplete label; fixed label window over both legs. | `test_a1_presample_trade_reaching_dev…`, `test_a1_quarantine_uses_the_fixed_label_window…`, `test_a1_quarantine_window_covers_both_legs` |
| **Data** | Unavailable corporate actions went unnoticed (2 quarantines); mis-adjusted splits undetected; impossible bars counted but kept; budget not enforced (a looping page token spins until timeout); SPY gaps removed sessions for all symbols; SIP checked only on the request. | Corporate actions required, with coverage reported; split-jump quarantine; impossible bars removed; per-call budget and a repeated-token stop; complete SPY; SPY consolidated-volume check. | `test_a1_corporate_actions_unavailable…`, `test_a1_mis_adjusted_split…`, `test_a1_impossible_bars…`, `test_a1_request_budget…`, `test_a1_a_repeated_page_token…`, `test_a1_benchmark_must_have_every_session…` |
| **S3C** | A newer breakout could hide an older one's retest. | Each breakout gets its own window (strategies 0.1.1). | `test_a1_s3_retest_of_an_older_breakout…` |
| **Book** | The sector cap counted entry cost; control books stopped at the strategy's last exit. | Previous-close marks; books run to the latest exit. | `test_a1_book_sector_cap_counts_open_positions_at_their_mark` |
| **Reporting** (descriptive) | Several required economics were missing; limitations appeared only at the end of one file; unregistered numbers were unlabelled. | See the list below. | `test_v42_protocol2_demo_end_to_end` |

**Reporting additions:**
- win/loss distribution, payoff, gross and net per fill, and net per initial signal;
- exits that daily bars resolve vs those they cannot; excursion bounds over resolved trades;
- the fill-rate decomposition of the paired mean;
- the control ambiguity share and the daily-bar bracket;
- market beta and time concentration;
- per-year stability;
- the matched-random placebo;
- book Sharpe and IR;
- the S5 cost grid;
- confidence intervals, MDE and underpowered flags;
- limitations in every output and in the first 40 lines of `comparisons.md`;
- labels on everything descriptive.

**Engine robustness (no change to registered semantics):** a bootstrap degeneracy guard, and a deterministic fixture seed.

**Log wording.** A0 printed PRESAMPLE values in progress lines; A1's progress lines print none. The runner still prints the aggregate conclusion in its "evaluate" step, and the job summary embeds the official results table. Both come after the lock, from the one confirmatory look.

### 12.3 Measured size of the A1 decision rule (synthetic nulls)

Source: `bench/qv2_a1_production_null_battery.py`, 3,000 replicates per design, Monte Carlo SE about 0.4 pp at 5% and 0.25 pp at 1.25%.

| Null design | A0 rule at 5% | **A1 at 5%** | A0 rule at 1.25% (Holm first step) | **A1 at 1.25%** |
|---|---|---|---|---|
| A0 battery design (independent controls, 684 sessions) | 3.6% | **3.6%** | 1.0% | **1.0%** |
| Production-shaped, Gaussian | 5.9% | **5.8%** | 1.8% | **1.7%** |
| Production-shaped, t3 tails and a volatility regime | 7.9% | **6.6%** | 2.4% | **1.9%** |
| Production-shaped, slowly varying factor premium (5%/yr) | 6.5% | **6.4%** | 1.7% | **1.6%** |

An independent re-run with other seeds gave the same picture: 7.3% → 6.1% and 1.6% → 1.0% for the t3 design.

**Reading:**
- **Over-size:** on realistic dependence, the rule is still about **1.2–1.5× nominal**. Lowering α would be a threshold change and is not made. The over-size is disclosed in every report: a Holm p between 0.03 and 0.05 is borderline evidence.
- **Power:** the realistic MDE is about +1.5–2 pp per signal (§7).
