# Quant V2 M1: execution, inference, controls and book engine

| | |
|---|---|
| Milestone | M1 of [Issue #12](https://github.com/JaredVogler2/STONKS2026/issues/12) (amendments [02](../../amendments/quant-v2-2026-10-22/02_P0_RESEARCH_CORRECTNESS.md) §B–D, [04](../../amendments/quant-v2-2026-10-22/04_PREREGISTRATION_AND_STATISTICS.md) §3 and §5, [06](../../amendments/quant-v2-2026-10-22/06_ENTRIES_EXITS_COST_AND_PORTFOLIO.md)) |
| Builds on | [M0_AUDIT.md](M0_AUDIT.md) (PR #13): QV2-002 and QV2-003 confirmed |
| New modules | `research/execution_v2.py`, `research/inference.py`, `research/controls.py`, `research/book.py` |
| Changed | `signals/levels.py`: optional `stop_distance_atr` argument (default `None`; live behaviour unchanged) |
| Unchanged | `research/execution.py` (`simulate`), `research/realeq/*` and `realeq-protocol-1`; no protocol-1 number can move |
| Market data used | none: every number here is synthetic and seeded |
| Trading | none; no promotion, no group or live alerts |

M1 builds the engine that protocol-2 will run on. Whether any strategy works is not decided here: M3 freezes the study design before M6 runs it.

## 1. `execution_v2.simulate_v2`: daily bars without invented intraday paths

**The only path assumption:** within a session, the price moves continuously from the open to the close and touches the high and the low. Gaps happen only between sessions.

**When the bars cannot decide an outcome:** the trade carries a pessimistic and an optimistic leg, plus an ambiguity label. Inference uses the pessimistic leg; the optimistic leg is reported as a sensitivity.

| Situation (daily bar) | Protocol-1 `simulate` | `simulate_v2` |
|---|---|---|
| Open inside the entry zone | fill at the open | same (`fill_kind = OPEN`) |
| Open above the zone, low ≤ limit | fill at the limit | same (`LIMIT`); `trade_through_ticks = 1` sensitivity |
| Open below the zone | no fill that session | same (the plan's open guard) |
| OPEN fill, then the target is touched the same day | ignored (held), so it can be optimistic or pessimistic (M0) | **sold at the target**: the whole bar is after the opening fill |
| LIMIT fill, low ≤ stop | stop | stop. The price came down to the limit, so the stop touch is after the fill. |
| LIMIT fill, high ≥ target, close < target | ignored | **`FILL_DAY_TARGET_ORDER`**: the high may predate the fill. Branches are "sell at the target" and "hold"; the legs take the worse and the better. |
| LIMIT fill, close ≥ target | ignored | target, certain (the touch after the fill is proven) |
| Stop and target in one bar | stop first | **`SAME_BAR_STOP_TARGET`**: pessimistic STOP, optimistic TARGET |
| Gap through the stop / above the target | exit at the open | same |
| Missing bar | skipped | skipped and flagged. A missing horizon bar exits at the next open (`TIME_EXIT_DELAYED_MISSING_BAR`). |
| MFE / MAE | whole-day extremes, reported as observations | **bounds** over the post-fill, pre-exit segment; `observed_intraday_path = false` |

**Entry arms:**
- **E0:** the protocol-1 limit at `entry_max`, with the `entry_min` open guard.
- **E1:** a limit `k` ATR below the decision close (default 0.5 ATR), with an open guard one tick above the stop. It keeps the same stop and target, so E0 and E1 differ only in the entry price.

**Exit arms:**
- **X0:** stop + target + time exit.
- **X1:** stop + time exit.
- **X2:** a trailing stop at the highest close through d−1 minus 3 ATR(d−1). It applies from the next session and never loosens.

The only two ambiguities above both involve a target. So with limit entries, X1 and X2 are always fully resolved by daily bars.

**Costs:** `Costs.net` applies the proportional per-side cost once: `exit (1 − c) / (entry (1 + c)) − 1`.

**Evidence:** `tests/test_qv2_m1_engine.py`, run on continuous piecewise-linear intraday paths with an independent "truth" order model (`tests/intraday_paths.py`):

| Test | What it proves |
|---|---|
| `test_v07_v11_truth_lies_between_pessimistic_and_optimistic_legs` (6 arms × 2,000 paths) | For every filled path, the true net return lies in [pessimistic, optimistic]. When resolved, the exit equals the truth exactly and the true MFE/MAE lie inside the bounds. X1 and X2 are never ambiguous. |
| `test_v02_e0x0_reproduces_protocol1_wherever_the_fill_day_target_is_not_touched` | v2 E0/X0 equals protocol-1 trade for trade wherever protocol-1's fill-day rule does not apply (regression link to the published engine). |
| Amendment 02 §B fixtures 1–8 and V09, V10, V11, V14, V18 | Exact transitions for hand-built bars. Includes the M0 counterexample: protocol-1 held into a gap, v2 sells the same day. |
| `test_v12_costs_are_applied_once_and_match_protocol1_formulas` | `Costs.net` and `r_multiple` equal protocol-1's `net_at` and `r_at` at 0, 10, 25 and 50 bps. |

**Not modelled, stated rather than guessed:**
- Partial fills, queue position, spread and halts within a session. The touch-fill sensitivity bounds the queue effect.
- Intraday replay. Minute bars could resolve the ambiguous subset, but the bands are conservative, and a verdict that depends on them fails closed (§5).

## 2. `inference`: a null-calibrated block test

**Unit:**
- One value per trade, normally the strategy value minus the matched-control value.
- Each value sits on the NYSE session index of its decision date, so all values of a session stay together.

**Statistic:**
- The trade-weighted mean.
- Studentized with a block-cluster standard error over non-overlapping L-session blocks.
- That standard error includes a Bartlett lag-1 term, because 20-session holds straddle block borders.

**Null distribution and p-value:**
- A circular block bootstrap of the session index with the same studentization, centred at the estimate.
- `p = P*(t* ≥ t̂)` tests H0: mean ≤ 0.
- The percentile-t bounds give the one-sided 95% lower bound and the 90% interval.

**Decision statistic:**
- Run at L = 20, 40 and 60.
- The **conservative** summary takes the largest p and the smallest lower bound.

**Minimum clusters:**
- Fewer than 12 non-empty, non-overlapping blocks at any length gives `INSUFFICIENT_CLUSTERS`, with no p-value.
- This applies to FAILED readings as well as VALIDATED ones. The H8 sample of 15 decision weeks is therefore never readable.

**Development note:** without the lag-1 term, single-length tests rejected 8–9% of true nulls on the fat-tailed design. The term was adopted before any market data was involved.

**Battery:** `uv run python bench/qv2_m1_inference_battery.py` — 1,000 replicates per design, 999 bootstrap draws, seeded, about 10 minutes on one core.

**Design:**
- Each value is a 20-session sum of daily market, sector and idiosyncratic shocks (0.9%, 0.6% and 1.2% daily).
- Paired designs subtract a same-date control exposed to the same market shock.
- So one paired trade has an SD of about 8.5 percentage points.

**Type I error (true mean 0):**

| Design | Sessions / dates × trades | Insufficient clusters | Reject @5% conservative (95% MC) | @10% conservative | @5% single L = 20 / 40 / 60 |
|---|---|---|---|---|---|
| `presample_like_680s_120d_x3` | 680 / 120 × 3 | 1.8% | **4.0%** (2.8%–5.2%) | 9.1% | 6.1% / 5.7% / 5.5% |
| `dev_like_1700s_300d_x3` | 1700 / 300 × 3 | 0.0% | **4.5%** (3.2%–5.8%) | 8.3% | 5.6% / 5.5% / 5.6% |
| `dense_680s_400d_x3` | 680 / 400 × 3 | 0.0% | **3.8%** (2.6%–5.0%) | 7.6% | 5.0% / 4.9% / 5.3% |
| `fat_tails_vol_regimes_680s` (Student-t(3), a third of the span at 3× volatility) | 680 / 120 × 3 | 2.3% | **4.6%** (3.3%–5.9%) | 9.0% | 6.8% / 7.1% / 7.4% |
| `unpaired_680s_120d_x3` | 680 / 120 × 3 | 2.1% | **3.1%** (2.0%–4.2%) | 6.7% | 4.4% / 4.5% / 4.4% |
| `h8_like_75s_15d_x9` | 75 / 15 × 9 | 100% | **0.0%** | 0.0% | — |
| `thin_340s_40d_x2` | 340 / 40 × 2 | 100% | **0.0%** | 0.0% | — |

**Reading:** the conservative test holds its nominal size on every design. A single block length can over-reject by 1–2 points under fat tails and volatility regimes, which is why the decision uses the conservative summary.

For contrast, M0 measured protocol-1's statistic at 10.9% and 17.1% on H8- and H5-shaped samples.

**Power** (conservative, α = 5%, 500 replicates, planted mean per trade):

| Design | +0.4 pp | +0.8 pp | +1.5 pp |
|---|---|---|---|
| `presample_like_680s_120d_x3` | 16% | 44% | 87% |
| `dev_like_1700s_300d_x3` | 34% | 83% | 100% |
| `dense_680s_400d_x3` | 40% | 88% | 100% |
| `fat_tails_vol_regimes_680s` | 10% | 20% | 49% |
| `unpaired_680s_120d_x3` | 7% | 17% | 46% |

**What this means for protocol-2:**
- **Detectable edges are large.** About 2.7 years of evaluation sessions with about 360 paired trades (the pre-sample-like design) detect only per-trade edges around +1.2 to +1.5 pp per 20-session hold with 80% power.
- **Unpaired tests are much weaker.** Testing raw returns, the protocol-1 style, is far weaker than paired tests, because the market component dominates the variance.
- **The real MDE is set in M3.** It comes from the development-period variance of the actual paired differences, not from these synthetic SDs.
- **INCONCLUSIVE is not failure.** A smaller true edge would most likely be reported INCONCLUSIVE. It must not be read as FAILED (amendment 04 §6).

## 3. `controls`: one simulation path and same-date matched controls

**One code path for signals and controls:**
- `Market` aligns every symbol on the benchmark's sessions.
- `simulate_signal` runs a strategy signal and every control through the same `compute_levels`, cost-aware screen and `ExecutionPolicy`.
- There is no second implementation of the levels or execution rules.

**`matched_controls` draws K same-session controls without replacement:**
- **Pool:** stocks gate-eligible that session, from the full pool or a strategy-specific mechanism pool.
- **Exclusions:** the signal's own symbol and any symbol signalling that day.
- **Stratum:** the first with at least 5 candidates in this order: sector × volatility tercile, then sector, then volatility tercile, then pool. Terciles of `atr_pct_14` are formed among eligible stocks that same session.
- **Geometry:** each control gets the signal's stop distance in ATR units (`stop_distance_atr`, with the live 1–3 ATR clamp), the same target rule and the same policy.

**Per-signal values:**
- The strategy value and the control value are both per-signal net returns.
- **A no-fill counts as 0** (no capital deployed), so no-fills are never dropped from the denominator.
- A label still incomplete when the data ends is excluded and counted.

## 4. `book`: capital-constrained accounting (V16)

**Rules:**
- **Sizing:** equal slots of equity/10.
- **Caps:** one position per symbol, and a sector cap of 30% of equity.
- **Same-day cash:** entries are funded from cash at the **start** of the session. Proceeds of that session's exits arrive afterwards, so loop order creates no same-day cash.
- **Skips:** counted by reason. Ties within a session are broken by a seeded shuffle, never alphabetically.
- **Costs:** charged once per side.
- **Invariant:** equity = cash + marked positions, enforced each session (negative cash raises).

**Benchmark and annualization:**
- The exposure-matched benchmark holds SPY at the book's previous-session exposure and cash otherwise. Cash yield is 0, stated.
- CAGR is not reported for windows shorter than one year.

## 5. Fail-closed rules carried into protocol-2 (M3)

- **Pessimistic leg decides:** verdicts use the pessimistic leg.
- **Ambiguity-dependent results fail closed:** if a result passes only on the optimistic leg, or the ambiguous trades alone decide its sign, it is `UNCERTAINTY_TOO_HIGH`, not validated.
- **No p-value without clusters:** `INSUFFICIENT_CLUSTERS` gives no p-value and no FAILED or VALIDATED label.
- **Every number is labelled:** synthetic numbers in this file describe the engine, not markets.

## Commands

```bash
cd apps/pi-worker
uv run --no-sync pytest -q -p no:cacheprovider tests/test_qv2_m1_engine.py   # 31 tests
uv run --no-sync python bench/qv2_m1_inference_battery.py > battery.json     # size and power tables above
uv run --no-sync ruff check src tests bench
```
