# Quant V2 M2–M4: universe, features, strategies and the frozen `realeq-protocol-2`

| | |
|---|---|
| Milestones | M2 (data and universe), M3 (preregistration), M4 (S1–S5) of [Issue #12](https://github.com/JaredVogler2/STONKS2026/issues/12) |
| Builds on | [M0_AUDIT.md](M0_AUDIT.md), [M1_ENGINE.md](M1_ENGINE.md) |
| Frozen protocol | [`PROTOCOL_V2.md`](../../research-results/real-equities/PROTOCOL_V2.md), sha256 `a15b4e92…4c9f` |
| Market data used | none: every number below comes from seeded synthetic fixtures |
| Trading | none; research-only plugins, disabled by default; no promotion, alerts or orders |

## 1. Universe (M2)

**U1 source:**
- The current S&P 500 constituents table from Wikipedia (CC BY-SA 4.0), fetched 2026-10-22 (raw wikitext sha256 `66ff63a5…dfac`).
- Parsed into [`universe_sp500_2026_10_22.json`](../../apps/pi-worker/src/apex_worker/research/realeq/universe_sp500_2026_10_22.json). Each row keeps symbol, mapped sector, date added, CIK and name, with provenance and limitations in the file.
- Licensing: facts only; the derived table is shared under the same licence.

**Counts:**
- 503 rows, reduced to **500 stocks** with one listing per CIK. GOOG, FOX and NWS are dropped, so share-class twins never act as each other's controls.
- By sector: industrials 83, financials 76, technology 75, health care 60, consumer discretionary 47, consumer staples 34, utilities 31, real estate 30, materials 24, energy 21, communication services 19.
- All 66 U0 stocks are in U1.

**Membership by date added:**

| Date | Current members already in the index |
|---|---|
| 2017-01-03 (PRESAMPLE start) | 347 |
| 2018-01-02 | 369 |
| 2019-10-23 (DEV start) | 395 |
| 2025-05-02 (old holdout start) | 469 |

**Eligibility rule:**
- A stock is a member at t only if `date_added ≤ t`.
- `build_panel(..., membership=...)` is a new optional argument; the live scanner passes None and is unchanged. With it, cross-sectional ranks and breadth use members only (test `test_panel_ranks_and_breadth_use_members_only`).
- This removes the strongest look-ahead of a current list: names added because they later rose.
- It does **not** remove survivorship. Members that left the index are absent. The bias is reported and partly cancels in same-session paired controls.

**U2 is `BLOCKED_PIT_UNIVERSE`:** there is no free, licensed, verified point-in-time membership with delisted price history.

**Corporate actions and the HON/EXC items (QV2-009):**
- `AlpacaAdapter.get_corporate_actions_paged` covers every symbol batch and page. Protocol-1's call covered only the first 100 symbols, which was enough for fixed78. If a long range is rejected, it falls back to calendar-year windows.
- Spin-off ex-dates quarantine the parent's trade windows, as do the registered known events HON 2026-06-29 and EXC 2022-02-02. This is a quarantine, not a reconstruction of distribution values, which the free data cannot verify.
- An unavailable endpoint is reported as `UNAVAILABLE`.

**Finality lag:** data ends 2 sessions before the latest completed session (M0 found vendor revisions within a day).

## 2. Features (`features/quant_v2.py`, `qv2-features-1`)

**What is computed:**
- Sector percentiles of ret_20 and ret_60 among eligible members.
- Leader and peak-age geometry for S1.
- Sector breadth.
- The S2 residual-momentum score.

**The S2 score:**
- Betas against SPY and (sector ETF − SPY) are fitted on [t−145, t−20] by windowed OLS with a tiny ridge, then applied to the scoring window [t−19, t].
- The vectorised version matches brute-force `lstsq` to about 0.3% (the ridge).
- It is causal: a later jump never changes earlier scores.
- It falls back to one factor when the sector ETF has no history (XLC before mid-2018).

**Tests:**
- `test_v24_v2_features_are_causal_under_truncation`: features computed on bars cut at T equal the full-history features for every t ≤ T.
- `test_v33_residual_betas_never_use_the_scoring_window`.

## 3. Strategies (M4; `signals/strategies/quant_v2.py`)

**The spec card:** each plugin has a typed `StrategySpecV2` with the fields amendment 05 requires:
- mechanism and null;
- universe at t;
- feature dependencies and trigger;
- order, stop hypothesis and exit candidates;
- maximum hold;
- disconfirming evidence and event blackouts;
- provenance and release profile, `RESEARCH_ONLY`, disabled by default.

The exact rules are in [PROTOCOL_V2.md §4](../../research-results/real-equities/PROTOCOL_V2.md).

**Synthetic scenario tests** (`tests/test_qv2_protocol2.py`):

| Strategy | Scenarios |
|---|---|
| S1 | 10 parametrised cases: 1 positive, plus too-recent peak, stale peak, too shallow, too deep, not a leader, below SMA50, falling SMA50, no confirmation, missing input. Plus the mechanism pool. |
| S2 | 6 cases on the rebalance cadence and each filter. Plus the raw-momentum pool using the raw percentile. |
| S3 | Shared opportunity between arms; first retest only; no signal after 3 sessions or on a close back below the pivot (V35, V36). |
| S4 | Never triggers without point-in-time events (V39). Availability mapping: after the close → next session, pre-open → same session, unknown → dropped (V37, V38). Planted-event mechanics. |
| S5 | Top-n cap, SMA50 filter, risk-off abstention, rebalance costs once, cash periods flat (V40). |

## 4. Study pipeline (`research/realeq/study_v2.py`) and runner

**Flow:**
- bars → membership-masked panel → context → live gates ∧ membership → v2 features → `Market`;
- per strategy and arm: signals → `compute_levels` at 25 bps → `simulate_v2` → quarantine → de-dup → K = 10 same-session matched controls (generic, plus the mechanism pool for S1/S2);
- DEV-only arm selection; PRESAMPLE rows of unselected arms are discarded unseen;
- the PRESAMPLE decision test, Holm, dispositions;
- books, sensitivities, legacy H rules, baselines, and the look-ahead-trap and placebo self-checks.

**Runner:**
- `--protocol realeq-protocol-2 --universe u1-sp500`. The default remains protocol-1.
- The same seven output files are written, so the workflow artifact list is unchanged.
- The workflow gains a `protocol` choice input (default `realeq-protocol-1`) and a 45-minute job ceiling. The minutes checkpoint uses that ceiling. actionlint is clean.

## 5. Design changes made before the freeze (all on synthetic data, before any protocol-2 market outcome existed)

DEMO runs exposed four problems in the first draft. Each was fixed before the hash was taken:

| Found | Change | Why it is not result-shaping |
|---|---|---|
| X1/X2 arms were screened by a target they never use. Pullback leaders sit just under their 120-session high, so the resistance cap rejected most S1 signals (106 of 116 in a fixture). | X1/X2 skip the target-based screens (resistance cap, minimum R/R) for signals **and** controls; the stop must still be valid. | A rule about which screen applies to which exit, fixed before any market outcome. |
| The cluster rule plus L = 60 was infeasible. A 684-session PRESAMPLE has at most 12 sixty-session blocks, and any market-wide risk-off breaker closure (every stock gated out) empties some. | The time axis is the gate-open sessions. The decision uses L ∈ {20, 40}; L = 60 is a reported sensitivity. The size of the new statistic was measured (below) before freezing. | Thresholds unchanged (12 blocks, α = 0.05). Size remains at or below nominal. |
| Arm selection by the raw DEV mean picked tiny noisy arms (54 signals), which then had too few PRESAMPLE clusters. | Select by the conservative DEV t-statistic. | Selection uses DEV only. |
| The `UNCERTAINTY_TOO_HIGH` rule fired whenever the optimistic leg's raw p passed and Holm failed, even with identical legs. | It now requires differing legs that pass at the same Holm multiplier. | A logic fix, with a regression test. |

**Decision-statistic battery** (`uv run python bench/qv2_p2_inference_battery.py`; 1,000 replicates per design, 999 draws, seeded; the M1_ENGINE.md designs on shorter axes):

| Design | Axis sessions / dates × trades | Insufficient clusters | Reject @5% decision (95% MC) | @10% decision | @5% single L = 20 / 40 |
|---|---|---|---|---|---|
| `presample_684s_120d_x3` | 684 / 120 × 3 | 0.0% | **4.4%** (3.1%–5.7%) | 9.7% | 5.6% / 5.8% |
| `gate_compressed_541s_100d_x3` | 541 / 100 × 3 | 0.0% | **4.6%** (3.3%–5.9%) | 9.6% | 5.8% / 5.6% |
| `gate_compressed_480s_60d_x2` | 480 / 60 × 2 | 3.7% | **3.6%** (2.5%–4.8%) | 9.2% | 4.5% / 5.2% |
| `fat_tails_vol_regimes_541s` | 541 / 100 × 3 | 0.0% | **3.9%** (2.7%–5.1%) | 10.2% | 5.7% / 5.2% |
| `dense_541s_400d_x4` | 541 / 400 × 4 | 0.0% | **3.8%** (2.6%–5.0%) | 8.4% | 4.8% / 4.7% |
| `dev_like_1700s_300d_x3` | 1700 / 300 × 3 | 0.0% | **5.0%** (3.6%–6.3%) | 8.8% | 5.6% / 5.5% |

**Power** (500 replicates, planted mean per trade; one paired trade has an SD of about 8.5 pp in these panels):

| Design | +0.8 pp | +1.5 pp | +2.5 pp |
|---|---|---|---|
| `presample_684s_120d_x3` | 48% | 89% | 100% |
| `gate_compressed_541s_100d_x3` | 41% | 86% | 100% |
| `gate_compressed_480s_60d_x2` | 25% | 52% | 85% |
| `fat_tails_vol_regimes_541s` | 23% | 44% | 72% |

**Reading:** the PRESAMPLE window can detect large paired edges only, roughly +1 to +1.5 pp per 20-session signal. Smaller true edges are expected to read `INCONCLUSIVE`, not `FAILED`. Each run reports the real MDE from the DEV variance.

## 6. DEMO measurements (synthetic, not evidence)

**Command:**

```bash
cd apps/pi-worker
APEX_DEMO_PER_SECTOR=46 uv run apex research real-equities --protocol realeq-protocol-2 --universe u1-sp500 \
  --source fixture --experiment-id demo-p2-smoke --out /tmp/demo-p2
```

**Run:**
- 389 synthetic stocks + 12 references through the Alpaca wire-format emulation.
- 113 bar requests; the real U1 needs about 165 bar requests plus about 6–70 corporate-action requests, under 300.
- **319 s** locally, with peak RSS of about **2.5 GB**. That gives an estimate for real U1 of about 7–12 minutes on a standard runner, within the 40-minute step limit.

**Engine checks:**
- The look-ahead trap was detected (mean +14.4%, p = 0.0002).
- The placebo was null.

**Results on the no-edge fixture:**
- S1, S2 and S5 came out `FAILED_WITH_POWER`, and S3 `INCONCLUSIVE`. That is the expected outcome for a market with no planted edge.
- It also shows the design can reach powered negative verdicts when signals are plentiful.

## 7. Tests

`tests/test_qv2_protocol2.py`, 48 tests, about 100 s:
- the protocol hash is pinned in PROTOCOL_V2.md;
- the protocol bounds research and never enables trading;
- protocol-1 stays the default;
- boundaries (V45);
- universe and membership;
- member-masked ranks;
- causality (V24, V33);
- S1–S5 scenarios (V31, V32, V35–V40);
- dispositions, all seven, including the UNCERTAINTY and identical-leg cases (V26, V41, V42);
- finality lag and quarantine;
- corporate-action paging and the yearly fallback;
- quarantine exclusion;
- the look-ahead trap detected and the placebo quiet (V25);
- runner input validation;
- a full DEMO pipeline (seven files, withheld arms, no prices, manifest hash).

## 8. Still blocked or open

- **S4, H3, H7:** no verified point-in-time event source.
- **U2:** no licensed point-in-time membership.
- **Intraday replay of ambiguous trades:** not needed for the decision (pessimistic leg); a possible later refinement.
- **Forward shadow:** registered from 2026-10-23, earliest evaluation 2027-04-30.
- **M7** (UI/Discord) is out of scope for this request.
