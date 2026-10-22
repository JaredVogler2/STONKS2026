# Phase P1 — Point-in-Time Data, Universe Expansion and Corporate-Action Integrity

**Objective:** Increase sample size and representativeness without creating survivor-biased “alpha,” funding a paid vendor by accident, or manufacturing historical events. The 66 stocks in first run are intentionally a small proof of execution, not a discovery universe.

## 1. Data entitlement and source manifest first

In `apps/pi-worker/src/apex_worker/market/history.py`, `research/training_data.py`, `features/panel.py` and `research/realeq/data.py`:
- Preserve true consolidated SIP daily bars for market-volume-dependent features when entitled (rather than silently substituting IEX).
- For EACH source, record provider, feed, endpoint, instrument type, adjusted vs unadjusted, corporate actions included, trading-session timestamps, first known_at and last fetched_at, revision/version, quote/trade coverage, entitlements, storage and reuse rights, API quota, contract date.
- Data license explicitly constrains exporting daily OHLCV or per-trade prices to GitHub artifacts and Discord. Keep raw inputs in permitted private ephemeral fetch/object storage, publish aggregate sanitized reports only.
- Repo runner MUST hard fail if missing SIP entitlement; no synthetic or IEX fallback can be called real SIP.
- Add source-time cutoff, excludes pre/postmarket where relevant; do not assume provider daily bar represents a regular-session auction until checked.

## 2. Expand historical universe in controlled stages

### U0 reference
Exactly 66 fixed existing surviving large-caps for reproducibility. Always report alongside new universe.

### U1 broad CURRENT-LIQUID cohort (intermediate ONLY)
Add ~300–1000 current liquid U.S. stocks across sectors with min median daily dollar volume, price and data length controls, benchmark and sector references, but label `SURVIVORSHIP_LIMITED_CURRENT_CONSTITUENTS`. This increases events/power but is NOT point-in-time membership.

### U2 causal point-in-time (preferred final scientific population)
Reconstruct membership at each historical decision date from licensed/public date-stamped membership changes if possible. Include newly listed, delisted, merger and ticker change mapping using persistent security IDs; do not use an ex-post “today's top 1000” membership. Check survivorship, inactive exchanges, sector classification evolving through GICS changes, dual-class shares, ADR/OTC rules, splits, spin-offs, buyouts and bankruptcy exits. Design `universe_at(asof_ts)` that returns a typed allowed set and provenance. If credible free PIT historical membership is unavailable, return `BLOCKED_PIT_UNIVERSE` or use U1 explicitly caveated; do not claim unbiased OOS.

### U3 market-cap and liquidity layers
Independent large-cap vs mid-cap vs small-cap performance (point-in-time tradable only); test 20–60-day momentum breadth regimes. Avoid selecting only symbols that happen to be winners today; include hard-to-trade and losers to measure fragility. Exclude OTC/penny/microcaps if original user intends mainstream brokerage liquidity, but report limits.

**Measured resource budget:** Design paginated batching within Alpaca request/min limits, max history years, bounds by GitHub runner memory and job minutes. Fetch only necessary columns/hashes, incrementally cache if terms permit, never repeat 1000×10yr raw downloads needlessly; do not use personal laptop. If free-source cap prevents U2, stop with explicit blocked state and request owner decision on licensed source vs conservative U1 analysis.

## 3. Corporate action reconciliation

- HON June 29, 2026 suspect >25% split-adjusted discontinuity; inspect provider corporate actions, issuer IR filing, spin/distribution economic terms and ex-date. Compare independently verifiable external source or quarantine trade holding windows crossing event. Do NOT assume “most likely spin-off” is confirmed factual mechanism.
- EXC 2022-02-02 Constellation spin-off: ensure adjustment captures the economic received asset/cash value or exclude cross-event trades with transparent sensitivity; price-only charts not total return.
- Validate each >25% move with exchange and issuer news, allow real event if corroborated; do not auto delete actual returns (e.g. COVID 2020, NFLX earnings).
- Track stock splits, reverse splits, symbol/name changes, mergers, cash acquisition, dividend and noncash distribution as a dated ledger. For cross-corporate-action trade simulation, apply economically correct entitlement and stop/target scaling on relevant ex-date; preserve original event.
- Run paired ``split` vs `all` adjustment (only if provider `all` semantics proven) and zero-DCA/no-event-quarantine sensitivity. A “fully adjusted” price series can itself introduce as-of future-action price rescaling into features if mishandled; price relative features generally invariant under consistent constant scaling over a pre-event window, but always test.
- Dividends must enter portfolio total-return calculation when held through ex-date, but avoid double counting if prices are total-return adjusted.

## 4. Earnings/catalyst data availability policy

Event fields for every historical record:
`issuer_id; event_type; occurred_at; first_public_at; provider_observed_at; available_at=max(first_public_at,provider_observed_at); effective_at; revision_time; source_ref; payload_hash; classification_confidence; correction_of`.

Earnings calendar: historically KNOWN scheduled earnings times (not today's retrospectively corrected calendar) plus actual release; half of estimates unknown -> `EARNINGS_UNKNOWN`, not `NONE_NEAR`. If the free API gives only release day with ambiguous intraday time, shift to next eligible trade or mark timing uncertainty. For H3/H7 use only archived records with defensible original published-time availability; SEC filing timestamps are not transaction timestamps and insider/politician reports are often delayed.

Test "time travel": a filing recorded today announcing transaction 45 days earlier cannot affect a signal before first public disclosure; amended filing cannot rewrite earlier signal.

## 5. Feature and benchmark cleanliness

- Compute cross-sectional percentile ranks on only historically eligible stocks available on decision date; no ranks from future survivors or end-of-period membership.
- Sector/market beta and residual returns fitted on trailing bars only, with as-of sector memberships; no future scaling, revised sector rank or all-history volatility estimator.
- Benchmark price-only vs total-return comparisons must be like-for-like. Exposure matched SPY/cash benchmark for idle strategy capital; cash proxy yield where permitted, otherwise explicitly assume 0.
- Historical market regimes from causal rolling breadth; if available cohort small or unstable, mark regime coverage/denominator and suppress unsupported breadth state.
- Sample opportunity counts independent of many duplicate time-correlated stocks; report exposures and sector risk.

## 6. Acceptance

Commit only safe summary files in `research-results/real-equities/<new-v2-experiment>/` and an immutable manifest including U0 vs U1 vs U2 classification. Test duplicate shares, stale names, delist, spin-off, per-sector membership shifts, real vs missing earnings, as-of price feed and 10-year data. Include an explicit **limitations section** that explains what remains biased.

A stronger result on a larger **survivor-only** sample is exploratory, never automatically sufficient for LIVE, no matter how many trades accumulated.