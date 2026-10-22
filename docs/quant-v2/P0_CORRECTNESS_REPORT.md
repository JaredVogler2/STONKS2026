# P0 correctness report: sign-off before any `realeq-protocol-2` run

This is the global gate of [amendment 02 §E](../../amendments/quant-v2-2026-10-22/02_P0_RESEARCH_CORRECTNESS.md). It collects the M0 audit and the M1 engine:
- what was reproduced, and the source SHAs;
- the tests and their results;
- the provenance verdict on the first experiment;
- the execution, cost and statistical policies protocol-2 must use;
- which experiments are allowed and which are blocked.

## 1. Source and provenance

| Item | Value |
|---|---|
| Original experiment | `2026-10-21-fixed78-sip-8y-r2`, code `2acad29`, run [39696547618](https://github.com/JaredVogler2/STONKS2026/actions/runs/39696547618) |
| Replay | run [39757487973](https://github.com/JaredVogler2/STONKS2026/actions/runs/39757487973) on `8146a79`. Research code and workflow are identical to `2acad29` (`git diff 2acad29 8146a79 -- apps/pi-worker/src .github/workflows` is empty). |
| Result | Every metric is reproduced exactly and `trades_summary.csv` is byte-identical. The dataset hash drifted: some vendor bar values changed after the r2 fetch. |
| Disposition | **`VALID_AS_PUBLISHED_WITH_ERRATUM`**, errata E1–E6 in [ERRATUM.md](../../research-results/real-equities/2026-10-21-fixed78-sip-8y-r2/ERRATUM.md). NO_TRADE stands; the seven published files are unchanged. |
| Audit and engine records | [M0_AUDIT.md](M0_AUDIT.md) (PR #13), [M1_ENGINE.md](M1_ENGINE.md) (this PR) |

## 2. P0 findings and their state

| ID | Finding | Reproduced? | State after M1 | Regression evidence |
|---|---|---|---|---|
| QV2-001 | `rounding="ROUND_FLOOR"` without an import | **No.** `decimal.ROUND_FLOOR` is that string. | Closed. The constant is imported; behaviour is identical. | `test_v01_*` (3) |
| QV2-002 | Fill-day ordering; MFE/MAE from whole-day extremes | **Yes**, mixed in direction | **Fixed for protocol-2:** `simulate_v2` keeps pessimistic and optimistic legs and MFE/MAE bounds. Protocol-1 `simulate` is unchanged. | `test_v07_v11_*` (6 arms × 2,000 paths), §B fixtures 1–8, V09–V11, V14, V18 |
| QV2-003/004 | `mean(boot ≤ 0)` treated as a p-value; Holm on it | **Yes**: 10.9–17.1% at nominal 5% | **Fixed for protocol-2:** a studentized, null-centred block test with a conservative L ∈ {20, 40, 60} summary measures 3.1–4.6% at nominal 5%. A cluster minimum gates both FAILED and VALIDATED. | `test_v19_v27_*`, `test_v20_v26_*`, `test_v21_*`, battery in M1_ENGINE §2 |
| QV2-005 | H8 FAILED on 15 weeks | Yes | Erratum E3. Protocol-2 cluster minimum: 12 non-empty blocks at L = 60. | `test_v20_v26_few_decision_clusters_give_no_p_value` |
| QV2-006 | 10 bps screen vs 25 bps evaluation | Yes | The engine takes the cost from the policy. Protocol-2 must screen at its primary 25 bps (M3) and log 10 vs 25 bps rejections. | `test_v12_*` (cost applied once; formulas equal protocol-1's) |
| QV2-007 | Controls with a different geometry, not de-duplicated | Yes | **Fixed for protocol-2:** controls are same-session and stratified, with the signal's stop distance in ATR, the same policy and per-signal values. | `test_controls_*`, `test_per_signal_*` |
| V16 | Portfolio over-allocation / same-day cash | Not observed in protocol-1 (entries ran first) | New `book` with start-of-session funding and slot, sector and duplicate caps. Negative cash raises. | `test_v16_*`, `test_book_same_session_*` |

## 3. Policies protocol-2 must adopt

These are engine facts and constraints. The numeric study design is frozen in M3 and its hash recorded here before the run.

**Execution:**
- `simulate_v2` only, under a versioned `ExecutionPolicy` (`exec-v2.0`).
- The **pessimistic leg** is primary. The optimistic leg and the `trade_through_ticks = 1` touch-fill sensitivity are reported.
- A verdict that holds only on the optimistic leg is `UNCERTAINTY_TOO_HIGH`.

**Costs:**
- The primary cost is 25 bps per side, with 0, 10 and 50 bps as stress levels. Stress levels are descriptive and never a separate winner election.
- The same 25 bps is passed to the `compute_levels` screen.
- 25 bps is a modelled total cost, not a measured spread; it is reasonably conservative for liquid large caps (Frazzini, Israel & Moskowitz, cited in amendment 04 §7).

**Statistics:**
- **Primary estimand:** the per-signal paired difference in net return against a same-session matched control. A no-fill counts as 0 for both legs.
- **Test:** the conservative block test (largest p over L = 20/40/60).
- **Multiplicity:** Holm over every preregistered primary hypothesis.
- **No p-value without clusters:** fewer than 12 non-empty blocks at any length means no p-value.
- **Descriptive only:** percentile bootstrap tails and Quality ranks are never confidence measures.

**Minimum detectable effect:**
- **From real data:** it must be derived in M3 from the development variance of the real paired differences.
- **Synthetic guidance:** on synthetic panels with an 8.5 pp paired SD, about 2.7 years of evaluation detect only about +1.2 to +1.5 pp per 20-session trade at 80% power.
- **Smaller true edges:** expected to come out INCONCLUSIVE, not FAILED.

**Chronology:**
- **Contaminated window:** the r2 window (2018-10-22 → 2026-10-21) and in particular its 2025-05-02 → 2026-09-21 holdout. Any result there is a development diagnostic only.
- **Fresh evaluation:** must use data no protocol-1 outcome touched, and must be labelled for what it is: a future window or forward shadow, an untouched pre-sample window, or held-out entities.

## 4. Allowed and blocked

| Item | Status |
|---|---|
| Engineering on the engine, S1–S5 plugins behind research-only flags, protocol-2 drafting (M2–M4) | **Allowed** |
| A `realeq-protocol-2` evaluation run | **Blocked** until M1 (this PR) and the frozen protocol-2 (M3) are merged to `main` and the protocol hash is recorded here |
| Re-using the 2025–26 holdout as untouched OOS | **Blocked** permanently: DEV diagnostic only |
| S4 earnings continuation evaluation | **Blocked** (`NOT_EVALUABLE_MISSING_INPUT`) until a verified point-in-time first-publication source exists |
| Any live, group or high-conviction release; any order placement | **Blocked** (unchanged; independent of research results) |

## 5. Fail-closed conditions

If any of the following holds when protocol-2 runs, the run reports `RESEARCH_NOT_RELIABLE` or `UNCERTAINTY_TOO_HIGH` rather than a disposition:
- a provenance mismatch the data cannot explain;
- an ambiguity band that changes the sign of a primary effect;
- a failed engine self-check.
