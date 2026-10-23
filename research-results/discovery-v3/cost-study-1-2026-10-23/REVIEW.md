# Execution cost study `cost-study-1` (`cost-study-1-2026-10-23`): reviewer notes

| | |
|---|---|
| Run | [39908161943](https://github.com/JaredVogler2/STONKS2026/actions/runs/39908161943), `apex-discovery-v3` with `plan = cost-study-1`, manual dispatch on `main`, 2026-10-23 05:52 UTC. The research step took 9 min 31 s |
| Code | `43cffcb` (the PR #25 merge). Plan sha256 `94477312…ca576` equals the [preregistration](../../../docs/discovery-v3/EXECUTION_COST_STUDY.md) |
| Data | Alpaca historical **SIP** quotes and one-minute bars, plus raw and split-adjusted daily bars. The sample was drawn from protocol-2's U1 dataset `0339c815…`; `data_quality_summary.json` is byte-identical to pilot 1's |
| Sample | 210 events (168 random eligible entries plus 42 S1 E0X1 fills), sha256 `8fba2379…`. **210 of 210 measured**, none missing, 1,332 study requests |
| Artifact | `apex-discovery-39908161943-1`, digest `sha256:a2d21eb4…cb5e3`. The three files here are byte-for-byte copies |
| Kind | A **measurement**. No strategy return was read, and no published result changes |

## 1. Entitlement (06 §C ledger)

**The account is entitled** to historical SIP NBBO quotes and one-minute bars: every request succeeded. Two consequences:
- The ledger rows marked "unverified until this study runs" are now verified.
- Pilot 1's decision-report step 3 ("measured cost model … SOURCE_BLOCKED until a quote source is found") is **superseded**: a free source exists and has been used. Pilot 1's files are not edited.

## 2. Results (bps; positive = worse than the simulator's price; all 210 events)

| What a retail-sized order would have paid | Mean | p50 | p90 |
|---|---|---|---|
| NBBO half-spread, open + 1 min | 18.8 | 13.7 | 41.1 |
| NBBO half-spread, open + 5 min | 11.2 | 8.4 | 25.5 |
| NBBO half-spread, 12:00 ET | 2.9 | 2.3 | 5.5 |
| NBBO half-spread, close − 1 min | 1.4 | 1.0 | 2.7 |
| **Buy** at the ask, open + 1 min, against the daily open | **20.4** | 21.4 | 70.6 |
| **Sell** at the bid, close − 1 min, against the daily close | **0.4** | 1.1 | 11.2 |
| **Per side** (mean of those two legs) + fees | **10.5** (95% CI 7.3–13.4) | 10.7 | 35.0 |
| First-minute VWAP against the open (patient order near the open) | −0.2 | +0.1 | +10.2 |
| Last-minute VWAP against the close | −0.5 | −0.3 | +6.1 |
| First one-minute bar's open against the daily open (open definition check) | +0.3 | 0.0 | 0.0 |

**Registered outputs:**
- **Proposed `cost-v3-1`** (registered rule: ceil(p90 of per-side marketable cost + fees)): **36 bps** overall. By tercile, 38 / 31 / 38 bps, from least to most liquid.
- **Assumption check:** 22.4% of events cost more than 25 bps per side.

## 3. Reading

1. **On average, the 25 bps per side assumption is about 2.4 times too pessimistic** for the simulator's execution style, which buys at the open and time-exits at the close. A marketable order one minute after the open, closed one minute before the close, costs **about 10.5 bps per side** (95% CI 7.3–13.4).

2. **Almost all of that cost is the entry.**
   - At open + 1 min, a buy pays about 20 bps against the open: the first-minute half-spread (median 13.7) plus noise.
   - A sell before the close pays about 0.4 bps.
   - Waiting 5 minutes roughly halves the spread, and mid-day spreads are about 3 bps.
   - A patient order near the open (first-minute VWAP) averages the open price itself (−0.2 bps).
   - **Execution timing is therefore a first-order design axis.** Close-to-close execution (decide at the close of *t*, trade near the close of *t* + 1) or near-auction execution would cut friction by about 5–10 times compared with chasing the open.

3. **Why the registered rule's 36 bps should not be adopted as is (a post-run design finding, C-1).**
   - The rule takes the **p90** of a quantity that includes the **random price move in the first minute**: the midquote drift at open + 1 min has p90 +49 bps and mean +1.6.
   - That tail is price risk, not friction, and averages out in a strategy's expected return.
   - `cost-v3-1` is recorded as the registered output and **is not used**.
   - A successor `cost-v3-2` should be registered before any plan uses it:
     - the expected cost: mean half-spread at the registered execution instant, plus mean signed drift, plus fees;
     - 25 bps kept as a stress sensitivity;
     - tails reported as execution risk.

4. **Implications for pilot 1** (descriptive only, post hoc, on contaminated DEV; **no disposition changes**):
   - Interpolating its cost-sensitivity books to about 10.5 bps per side, F5 momentum lands at roughly SPY (about +0.2 pp a year) and F2 gated at about −0.5 pp a year.
   - **The gap to SPY closes, but nothing beats it.** Testing at a measured cost needs a newly registered plan, and its best possible outcome is again a nomination for a prospective test.

## 4. Findings and limitations

| Id | Finding |
|---|---|
| C-1 | **Registered rule:** p90 mixes friction with first-minute price noise (§3.3) |
| C-2 | **Unexplained year pattern:** opening half-spreads are wider in recent years (open + 1 min median 6.9 bps in 2023, then 18.5, 27.9 and 34.5 in 2024, 2025 and 2026), and mid-day medians move from 1.8 to 4.0. With 24 events per year this can be sample noise, a real change, or a feed or definition change. It is **not explained** by these aggregates, and no conclusion rests on it |
| C-3 | **Liquidity terciles are noisy:** the most liquid tercile has the tightest opening spread (median 9.7 against 21.0 for the least liquid), but its buy cost is not lower, because first-minute drift dominates. 56 events per tercile do not resolve the difference |
| C-4 | **Sampling bias:** the sample inherits protocol-2's eligibility mask, which includes the risk-off breaker, so stress days are under-sampled and costs are optimistic for them. Overnight gaps of 3% or more: only 5 events |
| C-5 | **Not measured:** MOO and MOC auction fills, impact beyond the displayed size, stop-exit slippage, PFOF price improvement, names outside U1 (the S&P 500 survivors) |

## 5. Integrity

- **Plan and sample:** the plan sha equals the registered one, and the sample sha256 is recorded.
- **Coverage:** 210 of 210 events measured.
- **Publication guard:** clean on all three files.
- **No quote-derived per-symbol data:** `cost_study.json` has no per-event rows and no symbols. The only tickers in this folder are `data_quality_summary.json`'s list of five late-start stocks, which is identical to pilot 1's published file.
- **Raw data:** no quote or price is stored, only aggregates over at least 5 events.
- **Reproduction:** dispatching the same plan on the same dataset reproduces the sample exactly (seeded). Quotes are historical, so the measurements should reproduce unless the vendor revises them.
