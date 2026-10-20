# PHASE 03 — Market universe, Alpaca adapters, two-speed scanner and strict data provenance

## Product objective

A one-to-four-week swing trading bot needs broad liquid stock discovery and actionable intraday monitoring without paid consolidated data. Implement **market breadth scanning + ≤30 active IEX symbols** with queue and feed provenance. Do NOT imply free IEX is the complete stock market.

## Source requirements

Primary: Alpaca Basic historical stocks/ETFs, daily bars, latest IEX trades/quotes and IEX WebSocket, with 15-minute restriction for consolidated recent SIP. Read current official API docs and quotas before coding; write provider capability tests.

Implement `MarketDataAdapter` interface:
- `get_assets/universe`: US exchange-listed common shares/ETFs metadata where accessible.
- `get_daily_bars(symbols, start, end, adjustment)` batched, paginated, anchored to venue dates.
- `get_intraday_bars(symbols, start, end, feed)` with interval/source/delay.
- `subscribe_priority_symbols(symbols)` bounded real-time streaming.
- `get_market_clock` using exchange calendars and source schedule.
- `get_corporate_actions` if supported; if unavailable, flag data-quality limitations, don't fabricate.
- `health` with provider name, quota, freshness, last reconnect, last error.

Fallback: offline replay provider implementing the exact same interface. Optional second provider only if legally free, no scraping or unlicensed redistribution. Never mix IEX and SIP volume as if interchangeable.

## Universe generation

- US exchange common shares, selected liquid ETFs; exclude OTC, penny stocks, closed-end funds where incompatible, warrants, units, leveraged/inverse ETFs by default, preferreds, very thin ADRs, halted/suspended symbols, severe data gaps.
- Robinhood availability must be treated as `likely_eligible` based on supported exchange metadata plus `user_verified` or `unknown`; there is no guarantee broker supports every listed symbol for every account. Exclude `unknown` from auto-published trades by default unless user manually confirms eligibility.
- Suggested configurable filters: price ≥$10, median 20-session dollar turnover ≥$30M, 20-day average volume ≥1M, adequate historical coverage ≥180 sessions, normal spreads <0.5% when consolidated executable spread evidence exists; exclude repeated halts/unreliable liquidity. Defaults are starting hypotheses subject to testing, not discovered optimal values.
- Allow admin include/exclude lists, filter presets, flag ETFs and exceptions.
- Survivorship warning: current universe backtests historically without delisted symbols are biased; procure honest historical universe or report limitation.

## Dual-speed scan cadence (not a promise of data latency)

1. Once per weekday night, fetch batched adjusted daily bars and refresh universe features.
2. Every 15–30 minutes during market session, refresh candidates via permitted delayed/bar data and reevaluate risk; adapt if providers throttle.
3. Prioritize ≤25 IEX WebSocket subscriptions, reserving slots for user-held positions; do not exceed provider's documented limit (30 Basic at research time). Use stream subscription manager with watchlist scoring, heartbeat, incremental replacement, exponential reconnect and backfill after disconnect.
4. Detect halts, empty books, zero volumes, out-of-order timestamps, unrealistic gap prints, crosses, invalid negative bid/ask, late ticks. Reject or quarantine.
5. Combine source-specific bar aggregation for IEX only, never label as consolidated, and never trigger a risk action from an isolated bad print.
6. Shared quote cache: one observation per symbol per interval for all users. Updates must carry `provider`, `feed_type`, `exchange_ts`, `ingest_ts`, `is_delayed`, `staleness_sec`.
7. Market closed: do not assume price is current. Session-aware status and next open. Support US market holidays/early closes with pinned `exchange_calendars` or equivalent tested library.

## Candidate priority / quotas

Implement weighted priority for: open personal positions > published active signals > high-quality candidate > general watchlist > broad universe. Weighted fairness prevents one sector monopolizing slots. Honor hard API request caps (200/min Basic), 429 Retry-After, 503, jitter, budget warning at 70/85/95%. Persist last-good snapshot but MARK stale. Do not use unverified Robinhood scraping.

Build price-validation logic:
- Level 1: input verified syntactically and timestamp valid.
- Level 2: quality of venue print/quote, price in plausible band, no duplicate/outlier.
- Level 3: cross-check against appropriately delayed consolidated feed when available, or declare unverified.
- Decision class: candidate discovery may use delayed; hard exit alert requires fresh, verified observations or escalates “PRICE UNCERTAIN—CHECK BROKER.”

## Files and tests

- Tests using recorded, permissioned/fixture JSON: splits 2:1, reverse splits, DST, holiday, half-day, no volume, IEX quote outside consolidated spread, network partition, 429 backoff, tick replay after reconnect.
- CLI `apex ingest --mode fixture` and `apex ingest --mode alpaca --dry-run`.
- Dashboard status for each feed, % universe checked, 30-slot occupancy, subscriptions and symbol data freshness.
- Persist only downsampled curated bars to Supabase; raw tick archive (if permitted) local rolling store with configurable retention.

## Acceptance

In DEMO, run a repeatable 100-symbol scan with sample fixtures and show reproducible ranked candidates, provider-age badges and rotation. In live-data mode with valid credentials, independently verify quotes, time labels and rate limits. Never issue a LIVE signal with demo data.