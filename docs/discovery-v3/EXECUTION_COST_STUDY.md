# Discovery V3 execution-cost study: preregistration (plan `cost-study-1`)

| | |
|---|---|
| Machine copy | `apex_worker/discovery/v3/costs.py::COST_STUDY_1`. `apex research discovery plans` prints its sha256, and the run's `cost_study.json` must show the same value |
| Plan sha256 | `94477312f4dc1b86689b4869a1d659c1ecb88dfa818795e2c0e6dbf81b2ca576` |
| Registered | 2026-10-23, before any quote is read |
| Why | [Pilot 1](../../research-results/discovery-v3/v3-pilot-1-2026-10-23/DECISION_REPORT.md) found friction is the binding constraint. At zero cost, F5 momentum and F2 gated beat SPY; at the **assumed** 25 bps per side they trail it. Amendment 06 §E: "assumed 25bps/side must be stressed, not presented as measured" |
| Kind | A **measurement**, not a strategy test. No strategy return is read, nothing is selected on outcomes, and no published result changes |

## 1. Question

The daily-bar simulator fills entries at the session's **open** and time exits at its **close**.

**What would a retail-sized marketable order have paid, per side, relative to those two prices?**

## 2. Sample (seeded, stratified, frozen before measurement)

**Source of events.** Events are drawn from protocol-2's own dataset (U1, Alpaca SIP daily bars) on DEV dates:

| Stratum | Events | Rule |
|---|---|---|
| Random eligible entries | 7 years (2020–2026) × 3 liquidity terciles × 8 = **168** | An entry session *e* whose previous close was a valid decision close for a name in the cell. The tercile is that name's cross-sectional rank of 20-session median dollar volume among names eligible at *e* − 1 (1 = least liquid third) |
| Strategy fills | **42** | A seeded random draw of protocol-2 S1 E0X1 DEV fills (entry sessions) |

**Draws.** Seeds are `[20261023, 0]` (random stratum) and `[20261023, 1]` (S1), without replacement inside each cell. The sample's sha256 (sorted symbol, date, stratum) is recorded in `cost_study.json`.

**Not a full population.** Eligibility follows protocol-2's mask, including its risk-off breaker (pilot 1 finding A-1). Breaker sessions are therefore under-represented, and those are exactly the stressed sessions with the widest spreads. **This bias makes the measured costs optimistic for stress days.** The gap buckets (§4) partly expose it.

## 3. Measurements per event (SIP, historical; the newest valid NBBO at or before each instant)

**Instants:** open + 1 min, open + 5 min, 12:00 ET, and close − 1 min. Half days use their own close.

**Quote selection.** For each instant the study reads the latest **valid** NBBO in the 5 seconds up to that instant, widening once to 60 seconds. A valid quote has positive sizes, a positive spread, and is not crossed or locked.

| Metric (bps) | Definition | Sign |
|---|---|---|
| `half_spread_<instant>` | (ask − bid) / (ask + bid) | ≥ 0 |
| `buy_cost_open+60`, `buy_cost_open+300` | ask at the instant ÷ the daily bar's open − 1 | positive = worse than the simulator |
| `sell_cost_close-60` | 1 − bid at close − 1 min ÷ the daily close | positive = worse than the simulator |
| `per_side_marketable` | mean of `buy_cost_open+60` and `sell_cost_close-60` | the principal number |
| `per_side_half_spread` | mean of the half-spreads at open + 1 min and close − 1 min | spread only |
| `mid_drift_open+60` | midquote at open + 1 min ÷ daily open − 1 | descriptive (path, not cost) |
| `first_minute_vwap_vs_open`, `last_minute_vwap_vs_close` | the one-minute VWAP against the daily open and close | what a patient order near the auction gets |
| `minute_open_vs_daily_open` | the first one-minute bar's open against the daily open | checks the open's definition |

**Price basis.** Daily prices for this comparison are **raw** (unadjusted), so they are on the same scale as quotes.

**Fees.** Commissions are $0. Regulatory fees on sales (SEC §31, FINRA TAF) are at most 0.3 bps, counted as 0.15 bps per side.

**Budget.** About 6 provider requests per event (about 1,300 in total), capped at 2,400. Requests run 4 at a time under the adapter's 190-per-minute bucket.

## 4. Aggregation and the registered rule

**Groups:**
- all events;
- by stratum (random, S1);
- by tercile (random stratum);
- by year (random stratum);
- by overnight-gap bucket (< 1%, 1–3%, ≥ 3%).

**Statistics per group and metric:** n, mean, p50, p75 and p90. Any cell with **fewer than 5 events is suppressed**. The mean per-side cost also gets a 95% iid bootstrap interval: events are independent draws across dates.

**Proposed cost model `cost-v3-1`.** For each liquidity tercile and overall:

**ceil(p90 of the per-side marketable cost + 0.15 bps)**

- This is a **proposal for later registered plans only**. It is applied to nothing retroactively. Pilot 1's dispositions at 25 bps stand.
- Any plan that uses it must keep 25 bps as a registered sensitivity.

**Assumption check.** The study reports the share of events whose per-side marketable cost exceeds 25 bps.

## 5. What this does not measure (reported as limitations)

- **Fills.** Opening- or closing-auction fills (MOO/MOC orders), which would avoid the spread entirely. The study reports the one-minute VWAP as a proxy.
- **Order size.** Market impact for orders larger than the displayed size.
- **Stop exits.** Slippage of intraday stop exits, and gaps through stops (the simulator already models gaps at the open).
- **Broker behaviour.** Payment-for-order-flow price improvement, or the latency of a person placing an order by hand.
- **Population.** The S&P 500 survivors (U1) only. Smaller names cost more.

## 6. Data rights and publication

**Source.** Alpaca historical SIP quotes and one-minute bars, read with the `real-equities-research` environment's read-only keys.

**What leaves the runner.** Nothing raw: no quote, price or per-event row. Only aggregates over at least 5 events.

**Blocked endpoint.** If the account is not entitled to an endpoint (401 or 403), the study ends **`SOURCE_BLOCKED`**, without estimates. The workflow warns and fails visibly.

## 7. Execution

**Dispatch.** *Actions → apex-discovery-v3* on `main`, with `plan = cost-study-1` and `experiment_id = cost-study-1-<date>`.

**DEMO.** Synthetic quotes:

```bash
uv run apex research discovery costs --plan cost-study-1 --source fixture --experiment-id demo-costs --out /tmp/c
```

**Publication.** A reviewed PR copies `cost_study.json` and `COST_STUDY.md` into `research-results/discovery-v3/<experiment_id>/`.

## 8. Source-rights ledger (06 §C)

| Source | Endpoint and feed | Entitlement observed | Revision mode | Adjustment | Stored or published | Cost |
|---|---|---|---|---|---|---|
| Alpaca Market Data, historical bars | `/v2/stocks/bars`, `feed=sip`, 1Day | Yes: protocol-2 run 39849016753, pilot 1 run 39884381394 | Vendor revisions possible; the dataset hash records each fetch | split (studies); raw (this study) | Runner temp only; aggregates and hashes published | $0 (Basic) |
| Alpaca Market Data, corporate actions | `/v1beta1/corporate-actions` | Yes: protocol-2 quarantine | Revisions possible | — | Runner temp only; counts published | $0 |
| Alpaca Market Data, one-minute bars | `/v2/stocks/bars`, `feed=sip`, 1Min | Yes: cost-study-1 run 39908161943 (210/210 events) | Revisions possible | raw | Runner temp only; aggregates published | $0 |
| Alpaca Market Data, historical quotes | `/v2/stocks/quotes`, `feed=sip` | Yes: cost-study-1 run 39908161943 (210/210 events, 4 instants each) | — | — | Runner temp only; aggregates published | $0 |
| Point-in-time index membership (U2) | none identified | — | — | — | — | **SOURCE_BLOCKED** |
| Point-in-time earnings and event timestamps | none identified | — | — | — | — | **SOURCE_BLOCKED** |

**Results.** [`cost-study-1-2026-10-23`](../../research-results/discovery-v3/cost-study-1-2026-10-23/REVIEW.md): about 10.5 bps per side on average for open-entry and close-exit marketable orders. The registered p90 rule gives 36 bps but is not adopted (finding C-1).

**Redistribution.** Group display or redistribution of any market data is **not** licensed. The `ALLOW_EXTERNAL_MARKET_DATA_REDISPLAY` switch stays off.
