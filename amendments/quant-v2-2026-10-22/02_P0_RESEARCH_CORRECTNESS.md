# Phase P0 — Correctness Audit: Prove or Reject the Research Machinery

**Priority:** FIRST. Do not redesign alpha until the historical engine is trustworthy. If the original experiment is not computationally reproducible, the first responsibility is to report that fact and repair it honestly.

## A. Exact baseline/replay investigation (QV2-001)

Source in `signals/levels.py` uses `rounding=ROUND_FLOOR` and imports `Decimal, ROUND_HALF_UP` but not `ROUND_FLOOR`. Run:
1. Inspect Git blob at `2acad29f75a81dbde0a469d1634d8b273de5c9ea` and at current HEAD. Record file blob SHA and exact import and function.
2. In clean Python 3.11 locked env, directly call `tick_round(11.235, down=True)` and `compute_levels(close=100, atr=2, invalidation_hint=97, resistance=None)`. Assert expected cent rounding.
3. Check `uv run pytest` and coverage over this path, import patching/global vars and any exceptions swallowed in research runner; inspect published real run logs and artifact/manifests.
4. If `NameError` occurs, write minimal failing test, fix import and exercise true complete `build_events` path. Investigate whether reported trades came from another module/function, stale checkout, monkeypatch, dynamic redefinition or unavailable logs. Do not speculate in conclusion without evidence.
5. Replay the **frozen protocol-1** after only a bugfix under a **new corrected experiment identifier** with same source/data window/settings if lawful and available. Report baseline drift in sample count, each mean, disposition; use separate `ERRATUM.md` referencing original, never overwrite historical JSON. If no bitwise data reproducibility due to vendor revisions, record reconciled hash changes.
6. If issue NOT reproducible (e.g. changed source), close with transcript and exact import semantics.

**Acceptance:** a clean independent reproducibility test with command and stdout; source SHA and fixture case in new regression suite; explicit verdict on trustworthiness of original result.

## B. Chronological execution state machine (QV2-002, 010)

Implement a deterministic event-time `ExecutionState` with states `SIGNAL→PENDING_LIMIT→FILLED→STOP/TARGET/TIME/CANCELLED`, and explicit as-of, quote source/venue and order assumption. No real order submission.

- On signal day after close, orders can start next market session only.
- A daily high/low cannot establish the order in which entry limit, stop, and target were touched. On entry-day orders that fill intraday, all later barrier ordering is UNKNOWN unless intraday bars prove order or prices on the opening print suffice.
- Implement conservative **pessimistic** bounds, optimistic bound, and ambiguous/drop-from-executable metrics; do not fill with “perfect low” price and then assume exact stop-after-entry from the same day's low.
- Intraday verification under a small predetermined sample subset: 1/5/15m SIP or allowed IEX bars with feed fidelity and API quota. Distinguish consolidated daily close vs intraday venue data and unavailable historical intraday entitlement. Do not falsely pretend 15m bar high/low solves within-15m ordering. Indicate residual ambiguity.
- Opening-price fills inside limit zone are determinable under assumed auction access but still must respect spread/gap/market quality and whether order actually existed before open.
- Ensure stop on entry day after intra-day entry is bracket-eligible only if stop order assumed active. If price was already below stop before entry, impose explicit cancellation/missing-fill logic.
- For profitable target opening gaps after fill, model gap to open; for stops open below stop, model worse open. For market holidays, halts, limit-up/down and splits, omit impossible fills.
- Maintain correct `MFE` and `MAE` only from available after-entry path; for unobservable same-day excursion store `observed_intraday_path=false` and bounded estimates rather than fabricated precise numbers.
- Distinguish *stock touched target* from actually selling a target-limit order, and stop price crossed from broker execution price.
- Build two parallel economic modes: fixed deterministic old-style simulated assumptions and ambiguous/bounded new-style; don't compare return improvements across both while calling selection-only alpha.

**Adversarial fixtures (minimum):**
1. Open > entry_max, low reaches below stop at 10:00, limit entry not touched until 14:00: should NOT assert stop after fill based on full-day low.
2. Open > entry_max, entry at 10:00, then stop 14:00: STOP valid.
3. Entry open in zone, gap-down through stop tomorrow: fill/open correct with modeled slippage.
4. Open below entry_min, later rebounds above entry_max: no buy unless policy supports a new order path.
5. Target and stop in one 1-minute bar: remain ambiguous with worst-first only as labeled sensitivity.
6. Missing bar on execution day, halt, exchange holiday, corporate action: do not invent value.
7. MFE before fill but not after: no positive excursion credit from prefill high.
8. Repeat old simulation on hand-generated monotone intraday path and compare exact expected event log.

**Acceptance:** event-state machine never accesses future intraday OHLC or presumes intra-session order without evidence; BDD tests show exact transitions and bounds.

## C. Correct inferential statistics and multiple testing (QV2-003/004/005/012)

Define the estimand first: (a) mean net return per **filled executed trade** under fixed policy, (b) expected return per **signal including no-fills**, and (c) time-weighted constrained portfolio net active return versus fair benchmark. Bootstrap these appropriately.

Old code `p_one_sided = share(ordinary-bootstrap means <= 0)` is a descriptive resampling tail, not a null-calibrated p-value. Fix by independently testing a null-centered studentized/moving-block procedure or paired differential randomization with justified exchangeability. Use non-overlapping evaluation clusters or stationary bootstrap with mean block length >= 20 NYSE sessions as one sensitivity, and 40/60 session alternatives. Studentization/regime conditioning if adequate clusters. Bootstrap p-value calibration using synthetic known-zero, known-positive, clustered-sector shocks across 1,000+ replicates where compute permits. Log false-positive and power curves; no “statistically significant” claim otherwise.

For paired strategies, resample **matched decision dates and sector panels together**; never treat 225 symbol trades from 15 weeks as 225 independent draws. Correct multiple testing across S1–S5, all selected exit variants, scan thresholds and prior H1–H8 when interpreting new exploratory discoveries. Holm only applied to valid hypothesis-level p-values and the correct family; otherwise report effect sizes/intervals and `INCONCLUSIVE`, not artificially adjusted numbers.

**Accept:** nominal 5% null false-positive rate measured with uncertainty under a calibrated simulation battery; uncalibrated procedures disabled from promotion. Minimum independent blocks and regimes for BOTH FAILED and VALIDATED, not just passed states. Include confidence intervals for net *incremental* return vs control.

## D. Costs and signal candidate equality (QV2-006/007)

- Live `compute_levels` defaults 10bps/side; historical evaluation primary is 25bps/side. Make costs a first-class immutable `ExecutionPolicy` field passed to screen and evaluation; always display source assumptions and stress values 0/10/25/50bps. Do not encode fees twice.
- In old `simulate(0bps)` followed by `net_at`, prove with a property test equivalence to `simulate(25bps)` where only prices/cost adjustments differ; note trade classification may change when cost-aware gate changes.
- Match strategy and control on decision dates, universe, liquidity, volatility, sector, earnings-risk eligibility, entry/stop policy, fill assumptions, number of overlapping positions and capital. Controls include exposure-matched SPY/cash and ETF trend, not only SPY 100%-invested.
- Do a factorial table: **stock selection fixed** × **entry/exit changed**, then **entry fixed** × **candidate stock swapped** to separate selection, execution and market regime contributions.
- Explicitly quantify venue-specific bid/ask, expected slippage/market impact conditional on liquidity and gaps. 25bps per side is a conservative modeled total cost, not necessarily a measured fee for a given stock.

**Accept:** property tests for no double charge; fair/identical-policy controls and separately versioned cost-aware gating changes with explanations.

## E. Global sign-off

Before launch of any `realeq-protocol-2` run: `docs/quant-v2/P0_CORRECTNESS_REPORT.md` must contain identified/reproduced P0s, source SHAs, tests and result, old-experiment provenance disposition, cost/fill/statistical policy with justified references, and allowed/blocked experiments.

**Fail closed:** if serious provenance mismatch remains or unresolved causal ambiguity can change verdict, classify `RESEARCH_NOT_RELIABLE` or `UNCERTAINTY_TOO_HIGH`. Continue data and software work; do not publish confidence or actionable buys.