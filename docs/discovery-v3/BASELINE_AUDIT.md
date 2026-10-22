# Discovery V3 baseline audit (M0)

| | |
|---|---|
| Audited code | `main` at `cd135bd` (PR #16 merge); protocol-2 A1 sha256 `33bd4e99…` |
| Audited evidence | `research-results/real-equities/qv2-20261022-s1-s5-u1-presample/` (run 39849016753) |
| Method | Read the sources named in `01_BASELINE_AND_FAILURE_ANALYSIS.md`. Rebuilt the frozen decision logic for the forward shadow, which forced a line-by-line parity proof against `study_v2` (`tests/test_forward_shadow_parity.py`). Extracted the book-level numbers below from the committed `strategy_metrics.json` |
| Rule | **No published metric is edited.** Every number below is quoted from the committed artifact or recomputed from it |

## 1. Correctness: defects versus limitations

**No new correctness defect was found in the protocol-2 study code.** The forward-shadow parity tests reconstruct `study_v2.run_arm` on a fixture exactly:
- decisions from data truncated at *t* equal full-history decisions;
- future shocks do not move a decision;
- kept signals, quarantines, de-duplication, per-signal values on every leg and cost, touch-fill, and the K matched-control symbols and values all match;
- the S5 book series matches.

**Findings:**

| Id | Kind | Severity | Finding | Effect on published results | Status |
|---|---|---|---|---|---|
| B-1 | latent reproducibility hazard | medium | S2's every-5th-session rebalance and S5's every-10th are computed as `np.arange(len(frame)) % R`, i.e. relative to the **first row of the dataset**. The matched-control seeds use the integer position *t* too. Re-running the study from a different first session would shift rebalance days and control draws | none: protocol-2 fixes the first session at 2016-01-04 | Handled in PR-A: the forward shadow anchors its window at axis position 2070 (a multiple of 10) and uses the protocol-axis index for seeds. Any future study must keep the axis or register the change |
| B-2 | reuse blocker | low | `data_v2.fetch_v2` / `quality_inventory_v2` hard-coded the 2016-01-04 window | none | Fixed in PR-A with optional parameters; the study's defaults are unchanged |
| B-3 | limitation (design) | high for economics | The book is **price-only with a 0% cash yield**. SPY and the strategies are both price-only, so the comparison is fair in kind; absolute returns are understated by dividends (~1.5%/yr for SPY) | absolute book and SPY returns understated | Documented; V3 reports cash-yield-0 and price-only explicitly (`PORTFOLIO_OBJECTIVE.md`) |
| B-4 | limitation (data) | high | U1 is today's S&P 500 survivors, with membership by `date_added`. Index leavers are absent | flatters absolute returns; paired controls cancel part of it | `DATA_LIMITED_SURVIVORS` on every V3 result; U2 (point-in-time) stays SOURCE_BLOCKED |
| B-5 | limitation (data) | high | Daily bars cannot order intraday events. Stops and targets inside one bar are bounded (pessimistic and optimistic legs). There is no queue, spread or partial-fill model; 25 bps per side is an **assumption** | 78 of the 114 S3 level rejections would pass at 10 bps, so S3's sample depends on the cost assumption | V3 reports zero-cost books (the friction bound) next to 25 bps |
| B-6 | limitation (evidence) | critical | PRESAMPLE 2017–2019 was exposed by the cancelled A0 run and is backward in time. DEV 2019–2026 shaped the designs. Neither is untouched for V3 | — | `contracts.EvaluationWindow` refuses any non-`DEV_CONTAMINATED` label before 2026-10-23, and `tier_for` caps every such result at `DEV_DIAGNOSTIC` |

## 2. The economic case study (quoted from the committed artifact)

| Book (25 bps/side, 10 slots, sector cap 30%) | Segment | Total return | Avg exposure | Turnover | Exposure-matched SPY/cash | Matched-control books (mean) | SPY buy-and-hold |
|---|---|---|---|---|---|---|---|
| S1 E0X1 | PRESAMPLE | +6.2% | 47% | 49× | +12.7% | −1.0% | +32.4% |
| S1 E0X1 | DEV | −8.5% | 46% | 137× | +46.2% | −18.2% | +154.0% |
| S2 E0X1 | PRESAMPLE | −9.7% | 72% | 67× | +12.3% | −11.4% | +32.4% |
| S2 E0X1 | DEV | −41.6% | 66% | 163× | +89.1% | +13.1% | +154.0% |
| S3 C_X0 | PRESAMPLE | +1.8% | 6% | 7× | +0.8% | −1.7% | +32.4% |
| S3 C_X0 | DEV | −5.6% | 5% | 22× | +4.4% | −6.3% | +154.0% |

**Per-trade economics:**
- **S1:** gross +0.81% per fill becomes +0.31% net at 25 bps per side. At 50 bps the net per signal is −0.18%.
- **S3:** +1.04% gross and +0.53% net per fill.

**Reading:**
1. **S1 beats the matched-control books** in both segments: by 7.2 pp on PRESAMPLE and 9.6 pp on DEV. There is book-level selection value, consistent with the per-signal paired result.
2. **Every swing book loses heavily to holding SPY at the same exposure**, and so does every control book. The common drag is the trade wrapper itself: 20-session exits, stops harvesting losses, and 25 bps per side on high turnover.
3. **Idle capital is the second drag.** At about 47% exposure, the S1 PRESAMPLE book forgoes about 20 pp versus full SPY on idle capital alone (+12.7% vs +32.4%).
4. **The root cause is portfolio-level, not "no signal".** The V3 pilot (PR-C) should therefore test:
   - the wrapper (exit and horizon policy);
   - idle-capital policy (cash versus SPY);
   - cost sensitivity;
   - regime abstention (S1's risk-off signals were negative: −0.86%, n = 28).

   These must be preregistered, run on DEV only, and compared against SPY / exposure-matched SPY/cash. Further indicator search is not the priority.

## 3. The ten adversarial questions (status at this SHA)

| # | Question | Answer from current evidence | What V3 adds |
|---|---|---|---|
| 1 | Can a positive matched differential still destroy capital? | **Yes.** S1 is +0.33 pp per signal versus controls, yet its DEV book returns −8.5% while exposure-matched SPY/cash returns +46.2% | Portfolio-first estimand (`PORTFOLIO_OBJECTIVE.md`) |
| 2 | Is the advantage just beta, size, sector or a bull market? | Unknown at book level. Most S1 signals (90%) came in `trending_up`; risk-off was negative | `decompose()` reports beta, the market term and selection versus controls separately |
| 3 | How much is lost by holding 50–94% cash? | About 20 pp over PRESAMPLE and about 108 pp over DEV for S1 (exposure-matched versus full SPY) | `idle_capital = SPY` is a registered policy axis |
| 4 | Do other horizons help? | Not tested (20 sessions fixed by the protocol) | PR-C registers horizon/exit variants on DEV only |
| 5 | Can an allocator choose cash/SPY better without hindsight? | Not tested | Weights agents and the idle-SPY book; any optimizer must beat the greedy book (ECO13) |
| 6 | Are overlapping signals counted as independent? | Inference already uses session-block bootstraps (protocol-2). V3 tests **daily active returns** with the same block rule | `active_test()` |
| 7 | Costs, gaps, missed fills? | 25 bps per side halves S1's gross. Gaps beyond stops are simulated (pessimistic open); missed fills count 0 per signal | Zero-cost and 25 bps books side by side; cost grid in PR-C |
| 8 | Does strategy rank predict portfolio utility? | **No evidence.** Paired per-signal advantage did not translate into book utility | The principal estimand is book log growth against the investable alternative |
| 9 | Is every failure logged? | Protocol-2 logged every configuration. V3 now requires it mechanically: the trial registry refuses a batch with unclosed trials | `registry.validate_complete()` (SCI01) |
| 10 | Does a novel idea beat cash, SPY or no trade? | No idea so far beats SPY at matched exposure | Every V3 result names its comparator; NO_TRADE is the default aggregate |

## 4. Reproduction

**Official run.** Dispatch `apex-real-equities-research` on `eda340a` (inputs in `REVIEW.md` §6). Alpaca vendor revisions can change the dataset hash.

**Parity and engine tests (offline, synthetic):**

```bash
cd apps/pi-worker
uv run pytest tests/test_forward_shadow_parity.py tests/test_discovery_v3.py -q
uv run apex research discovery run --plan engine-smoke --source fixture --experiment-id demo-v3-engine-smoke \
  --out /tmp/v3-smoke                       # DEMO only: fixture-only plan, never market data
```

**Book numbers above.** `python3 -c` over `strategy_metrics.json` (`strategies.<S>.books.<segment>`, `baselines.spy_buy_and_hold`). They are aggregate fields only.
