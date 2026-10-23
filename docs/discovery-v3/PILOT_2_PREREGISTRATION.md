# Discovery V3 pilot 2: preregistration (plan `v3-pilot-2`)

| | |
|---|---|
| Machine copy | `apex_worker/discovery/v3/plans.py::PILOT_2`. `apex research discovery plans` prints its sha256, and the run's `manifest.json` must show the same value |
| Plan sha256 | `5a2cc40c5ba414791eb2ddaf73ac83a29f5a7aaee20eb2ccdeb642c98fdd8c0e` |
| Registered | 2026-10-23, after [pilot 1](../../research-results/discovery-v3/v3-pilot-1-2026-10-23/DECISION_REPORT.md) and [cost-study-1](../../research-results/discovery-v3/cost-study-1-2026-10-23/REVIEW.md), before any pilot-2 outcome. The engine ran on the synthetic fixture only |
| Data | Protocol-2's Alpaca SIP daily dataset (U1, survivorship-limited) |
| Window | Protocol-2 DEV decisions, 2019-10-23 → data end − 22 sessions. **DEV is contaminated twice**: protocol-2 shaped it, and pilot 1 and the cost study were read on it. PRESAMPLE is never touched |
| Objective | `obj-v3-2` (§3) |
| Cost model | `cost-v3-2` (§2), **measured** |
| Multiplicity | Holm over **the 7 trials of this batch**. The registry's cumulative V3 count rises from 10 to 17, recorded in `TRIAL_INDEX.md` |
| Best possible result | `PROMISING_EXPLORATORY`: a nomination for a separately registered prospective exploratory shadow, never validation |
| Aggregate | NO_TRADE under every outcome |

## 1. Why these three families (each from a finding, not from a search)

| Family | Finding it answers | Primary | Ablations (one axis each) |
|---|---|---|---|
| G1 S1 execution timing | **Pilot 1:** S1 beats its matched controls by about +1.3 pp a year, but friction erased it. **Cost study:** buying at the open costs about 20 bps per side, an order at the close about 2 | S1 E0X1 decisions bought with a **limit-on-close** order (new arm `EC`) at the next close inside the same entry band; X1 exits; SPY-idle | (entry) the same decisions bought at the next **open** (E0) under the same cost model |
| G2 low-turnover momentum | **Pilot 1:** F5 beat SPY by +1.7 pp a year **before** costs, but it was in cash about 16% of the time because of the inherited risk-off breaker (A-1), and it turned over fully every month | Top 10 by 60-session return, equal weight, under an **explicit eligibility mask without the breaker**: fully invested. Rebalanced every 60 sessions with a **rank buffer** (holdings stay while ranked in the top 20). Traded at the close | (entry) the same book traded at the open; (exit) monthly with no buffer, at the close |
| G3 opportunity state at the close | **Pilot 1:** the dispersion gate helped mostly by trading less | Pilot 1's F2 gated momentum swing (top 5, every 5th session, dispersion ≥ its 60th trailing-year percentile), entries at the close (`EC`), SPY-idle | (market filter) the same without the gate |

Each family's mechanism, competing explanation, null, falsification test and negative controls are in `plans.py`; this table summarises them.

**Selection test for G2** (registered as its falsification check): the zero-cost momentum book against the mean of **20 random buffered books**. These use the same mask, schedule, buffer and close execution, and are ranked by a seeded random draw. The test is run **at zero cost** because random books turn over more, so costs would confound selection (finding R-1).

## 2. Cost model `cost-v3-2` (measured; `costs.py::COST_MODELS`)

The figures are expected cost, not a tail (finding C-1). They come from cost-study-1, which read no strategy return.

| Execution | Per side | Source |
|---|---|---|
| Marketable entry or rebalance at the **open** | **21 bps** | ceil(mean buy at open + 1 min against the daily open, 20.4, + 0.15 fees) |
| Entry or rebalance at the **close**, and time exits at the close | **2 bps** | ceil(mean half-spread at close − 1 min, 1.4, + 0.15) |
| Stop or target exits inside a session | **25 bps** | **not measured**: the conservative pilot-1 assumption is kept |
| SPY sweeps | 5 bps | unchanged |

**Stress case:** a uniform 25 bps per side is reported for every trial (`cost_sensitivity`), along with the zero-cost book. Neither changes a disposition.

## 3. Objective `obj-v3-2` (`objective.py::OBJ_V3_2`, sha256 `2637726e…`)

**Principal:** the same as `obj-v3-1` (annualised log growth minus the investable alternative), but net of `cost-v3-2`.

**Constraints:**
- **Drawdown:** max drawdown **no more than 10 pp worse than the comparator's own** (fixes O-1: SPY's −34% broke the absolute −30% floor);
- **Turnover:** at most 40 a year, counted on **stock trades only** (fixes A-2: SPY sweeps are not strategy turnover);
- no leverage, at most 10 positions, a 30% sector cap, and minimum exposure 5%.

## 4. Engine changes (additive; pilot 1 and protocol 2 are reproducible)

- **`execution_v2`: entry arm `EC`.** A limit-on-close order, filled at a later close inside the E0 band, within the entry window.
  - The fill session carries **no** intraday exposure; stops apply from the next session.
  - E0 and E1 are untouched, and `EXECUTION_VERSION` is unchanged.
  - The forward shadow's pinned behaviour fingerprint test passes.
- **`portfolio.run_portfolio`:** optional `entry_at="close"`:
  - SPY is sold at the entry session's close;
  - that session's exits are credited first and never fund the same close's entries (conservative);
  - entry, time-exit and stop-exit costs can be set separately;
  - stock-only turnover is reported.
  - **The defaults reproduce pilot 1 exactly:** a fixture rerun of `v3-pilot-1` gives byte-identical values to `main`.
- **`portfolio.close_rebalance_returns`:** target weights decided at close *t* are traded at close *t* + 1.
- **`agents`:** `explicit_mask`, `momentum_buffered` and `random_buffered`.
- **Matched controls:** an EC trial reuses E0's control draws (the same stocks per signal), so the G1 timing ablation isolates timing.

## 5. Registered rules (no change after the run)

| Rule | Detail |
|---|---|
| Engine self-checks | As pilot 1: the look-ahead trap must be detected and the placebo must stay quiet, otherwise every trial is `SIMULATOR_INVALID` |
| `REJECTED_DEV` | A constraint is violated, or active ≤ 0 against the comparator at `cost-v3-2` |
| `PROMISING_EXPLORATORY` | Active ≥ +1 pp a year **and** Holm p (7 trials) < 0.05 |
| `INCONCLUSIVE_DEV` | Otherwise |
| Falsification checks | Each family's checks (its ablation; for G2 the random-books test) are reported in `results.json` beside the disposition, and **the review applies this rule**: a `PROMISING_EXPLORATORY` primary whose falsification check fails is reported as such, and **cannot** be nominated |

**Power.** As measured before pilot 1 ([STATISTICAL_SIZE_POWER.md](STATISTICAL_SIZE_POWER.md)), DEV cannot detect a +1 pp a year edge. Pilot 2 can only reject large claims or flag large effects for prospective testing.

**Forking paths.** This batch was chosen after reading pilot 1 and the cost study on the same history. Any apparent winner is more likely to overstate its effect than pilot 1's would have been. Only the prospective lane can say otherwise.

## 6. Execution and publication

- **Run:** *Actions → apex-discovery-v3* on `main`, with `plan = v3-pilot-2` and `experiment_id = v3-pilot-2-<date>`. It uses the read-only Alpaca secrets of `real-equities-research`.
- **Publication:** a reviewed PR copies the aggregate files into `research-results/discovery-v3/<experiment_id>/` and lists every trial in `TRIAL_INDEX.md`, losers included.
