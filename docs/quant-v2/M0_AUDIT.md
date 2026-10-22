# Quant V2 — M0 research-correctness audit

Covers QV2-001 through QV2-014 and acceptance cases V01–V03, V07–V12 and V19–V20 ([02](../../amendments/quant-v2-2026-10-22/02_P0_RESEARCH_CORRECTNESS.md), [11](../../amendments/quant-v2-2026-10-22/11_TEST_MATRIX_AND_RELEASE_GATES.md)).

| Field | Value |
|---|---|
| Milestone | **M0** |
| Audited `main` | `8146a7913b2a69f5a12e6c777b42c8ceef57f928` (merge of PR #11). CI [run 39756525626](https://github.com/JaredVogler2/STONKS2026/actions/runs/39756525626) is green. No open PRs. |
| First real experiment source | `2acad29f75a81dbde0a469d1634d8b273de5c9ea`. `git diff 2acad29..8146a79 -- apps/pi-worker/` is **0 lines**: the audited code equals the experiment code. |
| Changed paths (this PR) | `signals/levels.py` (explicit `ROUND_FLOOR` import, no behaviour change); `research/realeq/data.py` (per-symbol content hashes in `data_quality.json`); `tests/test_qv2_m0_audit.py`; `bench/qv2_m0_measurements.py`; this file; the r2 `ERRATUM.md` and its index links |
| Actions used | 1 protocol-1 replay ([run 39757487973](https://github.com/JaredVogler2/STONKS2026/actions/runs/39757487973), about 1 runner-minute, 16 Alpaca requests) |
| Release mode | Unchanged: **NO_TRADE**; live, group and high-conviction alerts disabled; no promotion; no orders |
| Next PR | **M1**: execution policy v2, null-calibrated dependent-sample inference, identical-geometry matched controls (protocol-1 left intact) |

## 1. QV2-001: `ROUND_FLOOR` in `signals/levels.py` — **NOT REPRODUCIBLE (closed)**

**Evidence:**

- **Same file at both commits.** The blob is `c6d77f5e6c71f452a189b4f255e7b9bf092d1c4f` at both `2acad29` and `8146a79`. Line 16 is `from decimal import ROUND_HALF_UP, Decimal`, and line 62 is `return float(d.quantize(q, rounding="ROUND_FLOOR"))`.
- **The argument is a quoted string literal, not a bare name.** `decimal`'s rounding constants are themselves strings (`decimal.ROUND_FLOOR == 'ROUND_FLOOR'`). The name `ROUND_FLOOR` is not bound in the module's globals, and no code references it as a name.
- **Clean-runtime reproduction.** I ran a fresh worktree at `2acad29` with `uv sync --locked --python 3.11` (CPython 3.11.17, C `_decimal`):

  ```
  decimal.ROUND_FLOOR = 'ROUND_FLOOR' | == 'ROUND_FLOOR': True
  'ROUND_FLOOR' bound in levels globals: False
  tick_round(11.235, down=True) = 11.23
  tick_round(11.235) = 11.24
  tick_round(97.999, down=True) = 97.99
  compute_levels(100, 2, 97, None) = Levels(entry_min=99.0, entry_max=101.0, stop=97.0, target=106.0, ..., reward_to_risk_net=1.81, ...)
  misspelled rounding string -> TypeError valid values for rounding are: [ROUND_CEILING, ROUND_FLOOR, ...
  pure-python _pydecimal accepts the string: 11.23
  ```

  The rounding mode is validated, not ignored: a misspelling raises `TypeError`, and both decimal implementations floor-round correctly.
- **The path was exercised.**
  - At `2acad29`, the levels, execution, feature-parity and level-study suites call `tick_round(down=True)` **19,769** times, and pass.
  - The published r2 funnel shows **3,472** successful `compute_levels` results at H = 20 alone. Each needs two floor roundings, for `entry_min` and `stop`.
  - The only broad `except` on the research path is the runner's top-level handler (`research/realeq/runner.py:273`). It would have set `run_status=FAILED`, but r2 is `COMPLETED`.

**Root cause of the report:** the planning review read the quoted literal as an unbound name. Nothing runs differently, so no erratum is needed for QV2-001.

**Hygiene change:** `levels.py` now imports `ROUND_FLOOR` and passes the constant. The object is identical (`'ROUND_FLOOR'`) and the behaviour is unchanged. Regression tests V01:
- `test_v01_tick_round_floor_and_half_up_exact_cents`: 2,000 random prices;
- `test_v01_compute_levels_reference_case`;
- `test_v01_rounding_mode_is_the_validated_decimal_constant`.

## 2. Provenance: protocol-1 replay (V03, E2E1) — **metrics reproduced exactly; dataset hash drifted**

I dispatched protocol-1 on `main` with the identical code, `years=8` and `time_end=2026-10-21`, which gives the same window as r2: [run 39757487973](https://github.com/JaredVogler2/STONKS2026/actions/runs/39757487973). Artifact 12415284265 has zip sha256 `933c7afd…5bbe` and was verified on download.

| Compared item | r2 (run 39696547618) | Replay (run 39757487973) | Result |
|---|---|---|---|
| `strategy_metrics.json` (every field except `experiment_id` and `manifest_sha256`) | — | — | **0 differences** |
| `trades_summary.csv` | `d3216db6…` | `d3216db6…` | **byte-identical** |
| Boundaries, coverage, protocol sha256 | — | — | identical |
| Provider requests / corporate actions | 16 / available | 16 / available | identical |
| `dataset_hash` | `f428e39e…d419` | `f0e9812e…6dfa` | **differs** |
| `history_hash` | `18d9678f…fecf` | `ab37f63e…c1f2` | **differs** |
| `data_quality.json` | — | — | only `fetched_at` and `history_hash` differ; row counts and every flag are the same |

**Interpretation:**
- **Some bar values differ.** The hashes cover the encoded OHLCV values, and the row counts match, so some values changed between the fetch at 2026-10-21 23:56Z and the one at 2026-10-22 09:42Z.
- **No published number moved.** None of the changes altered any signal, fill, exit or metric.
- **The cause is not determined.** The licence design keeps no raw bars, and r2 has no per-symbol hashes. A plausible hypothesis is that r2 fetched the 2026-10-21 bar about 4 hours after the close, before late prints or corrections were final. That is unverified.

**Mitigations:**
- Starting with this PR, `data_quality.json` carries a `content_hash` for every symbol: the whole series, one digest per year, and the last session. Hashes reveal no prices. A future drift can therefore be pinned to a symbol and year (test `test_v03_content_hashes_localise_a_single_changed_bar`).
- Protocol-2 will end its data window at least 2 sessions before the fetch (finality lag).

**Disposition of r2: `VALID_AS_PUBLISHED_WITH_ERRATUM`.** The results reproduce exactly on identical code. The dataset hash is not bitwise reproducible. The inference caveats in §4 apply. See [`ERRATUM.md`](../../research-results/real-equities/2026-10-21-fixed78-sip-8y-r2/ERRATUM.md).

## 3. QV2-002: fill-day path ordering (V07–V11) — **CONFIRMED, refined**

**Method:** a reference "truth" model of protocol-1's order semantics on continuous intraday paths. A limit order rests at `entry_max` from the open, and nothing fills on a session that opens below `entry_min`. Stop and target orders rest from the moment of the fill. The daily simulator is then run on the same paths' OHLC. Tests are in `tests/test_qv2_m0_audit.py` (3,000 paths); measurements are in `bench/qv2_m0_measurements.py` (20,000 paths, seeded).

| Claim from the planning review | Finding |
|---|---|
| "A low below the stop could precede a later limit fill" (open above the zone) | **Impossible on a continuous path.** From an open above `entry_max`, price must cross `entry_max`, which fills the limit, before it can reach a low below the stop. Fill decisions and fill prices matched the path in **every** scenario. Stop-after-fill on a limit fill is causally valid. Spec fixture 1 cannot occur under these semantics. |
| Daily rules are a worst case | **False.** "Only the stop can trigger on the fill session" ignores a target that a resting order **would have sold** that day. This is certain when the fill was at the open, because the day's high comes after the open. The daily model then holds the position, and an overnight gap above the target books **more** than was achievable. Of 17,743 filled synthetic trades: 17,679 equal, **61 optimistic** (mean excess +1.88%, every case a skipped fill-day target, as the test asserts), 3 pessimistic. The direction depends on the path, so this is **not a bound**. |
| Pre-fill highs in MFE/MAE | **Confirmed.** MFE and MAE use whole-day extremes on the fill and exit days, so they can include price action before the fill or after the exit. They are bounds, not observed post-fill excursions. They are diagnostics only and enter no P&L, feature or disposition. |
| Limit fill on a touch | **Open question.** A fill is assumed when the low touches `entry_max`, which is optimistic about queue position. Protocol-2 will add a trade-through sensitivity. |

**Effect on r2 is unknown in size.** Measuring it needs the real bars, and protocol-2 will run both execution versions on the same signals. For scale: lifting H5 from its +0.03% mean (bootstrap p10 −0.55%) to a validated result would need roughly **+0.6 percentage points per trade**. On the synthetic paths the mechanism averages **under 0.01 pp per filled trade**.

**Fix (M1):** a versioned execution policy (`simulate_v2`), used by protocol-2 only:
- on open fills, the same-bar rules apply on the fill session;
- on limit fills, the target is left unresolved, and pessimistic and optimistic bounds are reported;
- MFE and MAE become bounds, flagged as such;
- a trade-through fill sensitivity is added.

Protocol-1's `simulate` stays byte-for-byte unchanged, so r2 remains reproducible.

## 4. QV2-003 / QV2-004: the protocol-1 `p_one_sided` is not a calibrated p-value — **CONFIRMED (P0 for any VALIDATED claim)**

Protocol-1 computes `p_one_sided = mean(weekly-block bootstrap means ≤ 0)`, and Holm is applied to it. Its Type-I error was measured with the true mean trade return exactly zero, using `bench/qv2_m0_measurements.py`: 1,000 replicates, 500 bootstrap draws, a shared market factor plus sector factors plus idiosyncratic noise, and 20-session holds.

| Design | Rejects at nominal 5% (95% CI) | Rejects at nominal 10% |
|---|---|---|
| H8-like: 15 decision weeks × 9 trades | **10.9%** (9.0–12.8%) | 15.5% |
| H5-like: 60 decision weeks × 4 trades | **17.1%** (14.8–19.4%) | 23.1% |
| IID control: 140 independent trades | 3.9% (2.7–5.1%) | 9.1% |

The statistic is close to nominal for independent trades but **2.2–3.4× anticonservative** when 20-session holds overlap across one-week blocks. A CI regression demonstrates this: `test_qv2_003_old_bootstrap_tail_rejects_true_nulls_too_often_under_clustering`.

**Effect on r2: none on any verdict.** No evaluable hypothesis had `p_one_sided` below 0.42, every Holm value was 1.0, and nothing was VALIDATED. However, protocol-1's VALIDATED gate rested on this statistic, so it was **not** a valid inference gate. That is recorded in the erratum.

**Fix (M1):**
- paired strategy-minus-control differences on decision dates;
- a null-centred, studentised moving-block or stationary bootstrap with blocks of 20, 40 and 60 sessions;
- an effective-cluster count;
- a Type-I and power battery that must pass before any p-value is reported.

## 5. Other findings

| ID | Status | Evidence | Effect on r2 | Remedy |
|---|---|---|---|---|
| QV2-005: H8 FAILED rests on 15 weeks | **CONFIRMED** | `strategy_metrics.json`: H8 has `distinct_weeks=15` and `regimes=1` | The original label stands as computed under protocol-1. Under V2 cluster minimums it would be `INCONCLUSIVE` (qualification in the erratum) | Protocol-2 requires minimum independent weeks, months and regimes for **both** FAILED and VALIDATED |
| QV2-006: 10 bps screen vs 25 bps evaluation | **CONFIRMED** | `LevelConfig.cost_bps_per_side=10` and `min_net_rr=1.5`; the runner never passes a cost to `compute_levels` | Some screened signals are uneconomic at 25 bps; the count needs the real bars | Cost becomes part of the versioned execution policy and is passed to the screen; rejection counts logged at 10 and 25 bps. Costs are applied exactly once (test `test_v12_cost_applied_once_simulate_vs_post_applied`, 3 cost levels × 500 paths) |
| QV2-007: baseline geometry differs | **CONFIRMED** | `BaselineFactory` uses the hint close − 1.5 ATR; H1/H2/H4/H6 use 1.6/1.4/1.2 ATR or the pre-gap close; random draws are not de-duplicated | Like-for-like only for H5 and H8 | Matched controls with the strategy's own geometry, liquidity and sector matching, and the same de-dup and slots |
| QV2-008: survivorship and sector look-ahead | **CONFIRMED (limitation)** | `research/training_data.py` uses current members and current sectors | Biases absolute returns upward; partly cancels in paired tests | Larger universe labelled `SURVIVORSHIP_LIMITED`; no point-in-time membership source is freely licensed and verified yet |
| QV2-009: HON / EXC corporate actions | **OPEN** | `data_quality.json`: HON 2026-06-29 continuity `suspect`; EXC 2022-02-02 spin-off | Unquantified | Protocol-2 quarantines HON trade windows across 2026-06-29 and EXC across 2022-02-02, with a sensitivity check |
| QV2-010: 2R target, midpoint geometry | **CONFIRMED (design)** | `compute_levels` uses the midpoint plus 2R and a resistance cap; screen R/R uses the midpoint. The simulator's R uses the actual fill | No defect in the reported R | Exit-policy contrasts preregistered in protocol-2 |
| QV2-011: Quality is not a probability | **CONFIRMED** | H5 holdout ρ = 0.002; the H2 Quality-top-half result is holdout-contaminated | — | Quality stays descriptive; it is never used as a confidence |
| QV2-012: multiplicity (229 configurations) | **CONFIRMED** | `configurations_attempted` has 229 rows | Protocol-1's primary configuration was frozen | Protocol-2 multiple-testing family includes S1–S5 and every preregistered arm; the 2025–26 window is labelled contaminated |
| QV2-013: model vs rule populations | **CONFIRMED** | `model_block` trains on all filled events in all regimes; rule economics use regime-allowed, de-duplicated trades | The forecast was `UNVALIDATED` everywhere | Protocol-2 reports rule economics separately and does not use the forecast for dispositions |
| QV2-014: research vs production | **CONFIRMED (architecture)** | The research workflow is research-only. The scheduled drift run 39725868779 now passes via the `PRODUCTION_REGISTRY_UNCONFIGURED` preflight. The registry is still not provisioned | — | Unchanged; out of scope for M0 |

## 6. P0 sign-off for the next stage

- **Confirmed P0s:**
  - QV2-003/004 inference: blocks any VALIDATED claim until M1 lands calibrated inference.
  - QV2-002 fill-day rule: mixed bias; blocks executable claims until `simulate_v2` lands.
- **Falsified:** QV2-001.
- **Provenance:** r2 is reproduced at the metric level; the dataset-hash drift is recorded.
- **Allowed now:** engineering work (M1), preregistration drafting (M3), and implementing S1–S5 behind research-only flags (M4).
- **Blocked:** any `realeq-protocol-2` evaluation run until M1 is merged, and every live or group release (unchanged).

**Commands:**

```bash
cd apps/pi-worker
uv run pytest tests/test_qv2_m0_audit.py -q        # 9 tests: V01, V02, V03 support, QV2-002 (x2), V12, QV2-003
uv run python bench/qv2_m0_measurements.py         # path study (20,000 paths) and Type-I table (about 1 min)
```
