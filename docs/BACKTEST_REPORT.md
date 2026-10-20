# APEX backtest and calibration report

**Status: every result below comes from SYNTHETIC data. None of it is evidence that any strategy works on real
markets.** No Alpaca keys were available in the build environment, so the real-data training path
(`apex train --source alpaca`, also what the weekly GitHub workflow runs) has been exercised only against a mocked
Alpaca transport in tests. It has never run against real market history. Nothing has been promoted to a live
champion, and no live probability is shown anywhere.

Reproduce everything here with:

```bash
cd apps/pi-worker
uv run apex train --source synthetic --out /tmp/apex-train      # 7 hypotheses on the synthetic market (~70 s)
uv run apex train --source redteam   --out /tmp/apex-redteam    # planted-edge control (~7 s)
```

Each run writes `report.json` (every number below) and `summary.md`.

## 1. Data and period

| Item | Value |
|---|---|
| Source | `synthetic_fixture`: the deterministic DEMO market generator (`demo/synthetic.py`), 24 stocks per sector, 11 sectors, plus a benchmark and sector ETFs |
| Period | 2016-01-04 to 2026-10, daily bars |
| Data hash | `ba3067eea6ef5899…` (identical in every run below, so the runs are directly comparable) |
| Untouched holdout | decision dates from 2024-11-12 (last 20 % of decision dates), set aside before any fit or threshold choice |
| Code | commit `75e46b1` plus the working tree that became `32cb60f` (policy v2) |

**Source biases you must assume:**
- **The generator has planted structure, so it is not a market.** Regimes, sector rotation and breakouts come from simple stochastic rules. A strategy that "works" here may only be matching the generator.
- **No survivorship, delisting, corporate-action or point-in-time fundamentals problems exist in synthetic data.** Real data has all of them, and Alpaca daily history carries survivorship bias (delisted symbols are hard to get). The training loader records this in `bias_warnings`.
- **No real catalysts.** H3 (post-event drift) and H7 (catalyst compression) need earnings and news events. The fixture has too few, so both produced 0 holdout entries.

## 2. Method (the same code runs live and in backtests)

- **Outcome.** Target touched before stop within the 3–20 session horizon, measured from the entry fill:
  - the stop counts first when both are touched in the same bar;
  - an entry fills only if price trades inside the zone;
  - a gap through the stop exits at the open.

  Levels come from `signals/levels.py` (ATR-based; 2R target; minimum net reward/risk 1.5).
- **Validation.** Purged, embargoed walk-forward (4 folds, 5-session embargo, 20-session label windows purged) on the development period, then one evaluation on the untouched holdout.
- **Model.** A JSON-only competing-outcome model (target / stop / time), isotonic-calibrated, with no executable model formats.
- **Costs.** 0, 10, 25 and 50 bps per side; gates use 25 bps.
- **Baselines.** These run on the same dates:
  - always flat;
  - basic breakout;
  - 20-day momentum;
  - random qualified symbol.
- **Promotion policy** (`research/evaluate.py`, frozen and versioned). All gates are on the holdout:
  - ≥ 100 entries for EXPERIMENTAL and ≥ 200 for CALIBRATED;
  - ≥ 50 distinct dates and ≥ 2 regimes;
  - Brier skill > 0 and ECE ≤ 0.06;
  - ≥ 30 selected trades above a threshold chosen on development data only;
  - the p10 of the weekly block-bootstrap mean net return at 25 bps must be > 0;
  - shuffled-label controls (below);
  - a challenger must beat the champion's skill by ≥ 0.005.
- **Synthetic data is capped at EXPERIMENTAL** and can never become a live champion (also enforced server-side).

## 3. Why the policy changed: one shuffled-label control was too noisy

`promotion-policy-1` ran one walk-forward with shuffled labels and flagged "leakage" if its holdout skill exceeded 0.01.
On the first full run that flagged **H2** ("RED TEAM FAIL", shuffled skill 0.038). I re-ran the shuffled control with 30
different shuffle seeds per strategy to see whether that was leakage or noise:

| Strategy | Holdout n | Shuffled skill: mean | sd | p90 | max | share of shuffles > 0.01 |
|---|---|---|---|---|---|---|
| H1 vcp_breakout | 117 | −0.051 | 0.060 | 0.019 | 0.051 | 13 % |
| H2 rs_pullback | 84 | −0.073 | 0.129 | 0.035 | 0.089 | 20 % |
| H5 sector_rotation | 784 | −0.012 | 0.018 | — | 0.003 | — |
| H6 gap_and_hold | 65 | −0.070 | 0.076 | — | 0.050 | 13 % |

Conclusions:
- **H2's flag was small-sample noise, not leakage.** Its mean shuffled skill is strongly negative; one shuffle in five exceeds 0.01 by chance at n = 84.
- **H1's real holdout skill (0.032) sits inside its own shuffled distribution.** Under policy v1 that would not have been noticed.

`promotion-policy-2` therefore runs **20 shuffled-label walk-forwards** and requires the real holdout skill to beat their
95th percentile (with a permutation p-value reported). The leakage alarm now uses the *mean* shuffled skill (≤ 0.01).
With 20 permutations the smallest attainable p is 1/21 ≈ 0.048, so this is a sanity gate, not a strong significance
test. It is one gate among the economic and calibration gates, and it must never be read as "p < 0.05 proves an edge".

## 4. Results under promotion-policy-2 (synthetic, 69.8 s on 4 vCPU)

Holdout (untouched) metrics and the policy decision:

| Strategy | Holdout n | Brier skill | ECE | Shuffled mean / q95 | Perm. p | Selected trades | Selected p10 net @25 bps | Status | Failing gates |
|---|---|---|---|---|---|---|---|---|---|
| H1 vcp_breakout | 117 | 0.032 | 0.016 | −0.035 / 0.031 | 0.095 | 115 | −0.0019 | UNVALIDATED | p10 ≤ 0 |
| H2 rs_pullback | 84 | −0.052 | 0.152 | −0.084 / 0.060 | 0.571 | 39 | −0.0174 | UNVALIDATED | n < 100, no skill, ECE, p10, permutation |
| H3 post_event_drift | 0 | — | — | — | — | 0 | — | UNVALIDATED | no entries (no catalysts in fixture) |
| H4 oversold_recovery | 0 | — | — | — | — | 0 | — | UNVALIDATED | no holdout entries |
| H5 sector_rotation | 784 | −0.073 | 0.059 | −0.006 / 0.005 | 1.000 | 29 | −0.0306 | UNVALIDATED | no skill, too few selected, permutation |
| H6 gap_and_hold | 65 | 0.036 | 0.079 | −0.062 / 0.029 | 0.095 | 54 | −0.0097 | UNVALIDATED | n < 100, ECE, p10 |
| H7 catalyst_compression | 0 | — | — | — | — | 0 | — | UNVALIDATED | no entries (no catalysts in fixture) |
| **Red team (planted edge)** | 591 | **0.332** | 0.055 | −0.016 / −0.001 | **0.048** | 35 | **+0.0454** | EXPERIMENTAL (synthetic cap) | none |

All-period economics (every entry, 25 bps/side, not position-sized):

| Strategy | Entries | Dates | Mean net / trade | Bootstrap p10 / p90 | Profit factor | Target first | Stop first | Gap through stop | Mean sessions held |
|---|---|---|---|---|---|---|---|---|---|
| H1 | 577 | 417 | +0.39 % | −0.03 % / +0.77 % | 1.14 | 31.5 % | 54.3 % | 0.7 % | 8.7 |
| H2 | 419 | 315 | −0.50 % | −0.95 % / −0.03 % | 0.83 | 25.8 % | 58.0 % | 0.7 % | 9.1 |
| H4 | 148 | 123 | −0.34 % | −1.02 % / +0.36 % | 0.88 | 28.4 % | 64.2 % | 0.0 % | 7.6 |
| H5 | 4579 | 874 | −0.25 % | −0.51 % / +0.01 % | 0.91 | 24.0 % | 56.6 % | 0.5 % | 9.8 |
| H6 | 300 | 258 | −0.31 % | −0.89 % / +0.31 % | 0.90 | 20.0 % | 54.0 % | 1.3 % | 10.4 |
| Red team | 3000 | 956 | +0.47 % | +0.37 % / +0.57 % | 1.33 | 27.3 % | 37.2 % | 0.0 % | 10.4 |

Benchmarks on the same holdout dates (mean net return per trade at 25 bps). H1's selected trades are shown for comparison:

| | H1 selected | Random qualified | 20-day momentum | Basic breakout | Flat |
|---|---|---|---|---|---|
| Mean net / trade | +0.89 % (n 115) | +0.09 % (n 59) | +0.70 % (n 55) | −0.49 % (n 1527) | 0 |

How to read this honestly:
- **No hypothesis passes.** H1 is the closest: positive holdout skill and good calibration. But:
  - its bootstrap p10 at 25 bps is below zero;
  - two of its three walk-forward folds had *negative* skill (+0.046, −0.169, −0.027), so the positive holdout is not stable;
  - one of its 20 shuffles beat it (p = 0.095);
  - a plain 20-day momentum rule earned a similar mean per trade.
- **The cost sensitivity is steep.** H1's selected trades go from +1.39 % per trade at 0 bps to +0.39 % at 50 bps per side, with p10 > 0 only at ≤ 10 bps. Real slippage on small caps can exceed 25 bps.
- **The red team proves the pipeline can find a real edge when one exists, and does not invent one.**
  - With a planted relationship it recovers skill 0.33 with ECE 0.055 and positive fold skill in all 4 folds.
  - It beats all 20 shuffles.
  - Shuffling its labels collapses skill to below zero (`test_shuffled_labels_collapse_to_no_skill`).
- **"Sequential" drawdowns in `report.json` are not portfolio drawdowns.** They compound every entry one after another at full size (H1: −84 %) and only show clustering of losses. APEX does not size positions or simulate a portfolio.

## 5. What this means for the live system

- **There is no champion outside DEMO.** Every live alert would be a RESEARCH_SETUP labelled UNVALIDATED, with no probability.
- **The DEMO dashboard's champion is the red-team model.** It is labelled DEMO / synthetic. The database CHECK constraint and the server's promotion code prevent it from ever being served outside DEMO.
- **What a real-data run must do.** The first `apex-model-train-weekly` run on real Alpaca history must be read against these same gates before anyone enables `promotion_mode=auto`. The amendment requires manual promotion first, then independent backtests, a risk review and a stable shadow period.

## 6. Limitations not addressed

- **No real-data result at all** (no keys in the build environment).
- **Holdouts are small** (65–117 entries for the most active single-name hypotheses). Effective independent sample size is lower still, because entries cluster on the same dates and sectors. The block bootstrap is weekly for that reason.
- **Some reported percentages are point estimates.** Bootstrap intervals are reported only for mean net return; rates such as target-first are point estimates with their denominators.
- **No transaction-cost model beyond flat bps.** No borrow, no market impact, no partial fills.
- **Regime labels come from the same rule-based classifier** the live scanner uses. The regime breakdown (H1: trending_up 83, rebound 19, risk_off 15 holdout entries) is too thin for per-regime conclusions.
