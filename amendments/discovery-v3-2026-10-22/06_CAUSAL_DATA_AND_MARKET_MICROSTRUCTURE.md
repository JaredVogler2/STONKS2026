# Historical Data, Point-in-Time Causality, Tradability, and Microstructure

## A. Account for the evidence we already have

APEX v2's U1 uses 2026-10-22 S&P 500 surviving members, historical date-added filter, and current sector classifications. This is NOT a full PIT universe with delistings and old share classes; the current gap should not be papered over with yet more surviving tickers. Alpaca SIP split-adjusted daily bars were retrieved for 1.334M observations, but price-only treatment omits dividends, queue position, spread, order path and several corporate-action intricacies.

Candidate discovery must not assume all historical names, historical earnings or original industries are known based on 2026 present-day profiles.

## B. Prioritized data improvements (do not require paid subscriptions without owner)

1. **Canonical instrument identity:** issuer CIK and securities/CUSIP/FIGI when legally accessible, valid ticker/date mapping, corporate action parent-child lineage, active status/delisted name. ETF type separate from operating company; avoid duplicate share classes. Historic S&P index members who later left must be represented or U2 remains BLOCKED. Characterize attrition instead of fabricating data.
2. **Total-return consistency:** adjustment semantics (split-only/all) verified with source; explicit dividends/cash distributions and spin-offs/merger events at ex-date, correct share adjustment for positions and levels; benchmark total-return comparable to strategy. If only split-only, report price return and material dividend bias.
3. **Point-in-time corporate/sector data:** effective and publicly known timestamps, industry reclassification revisions; median dollar volume and free float as-of to avoid future winner screening.
4. **As-of tradability:** minimum price and dollar liquidity, whole/fractional shares, opening auction availability, halt and limit mechanics, SEC restrictions if event-specific, borrow if short considered, historical spreads where actually licensed.
5. **Earnings/SEC event provenance:** `occurred_at, first_public_at, observed_at, available_at, issuer/security_id, source_version, correction_of` and license; no current earnings calendar applied retrospectively. Unexpected filing time can itself be signal; delayed reported insider/congress transaction is not known at transaction time.
6. **Intraday execution sample:** get provider-authorized 1/5/15-minute bars for stratified 100–200 historical fills to estimate within-daily-bar chronology; intrabar OHLC still not tick sequence. Group by liquidity, spread, gap, year, stock-vol, strategy; compare daily conservative/optimistic bounds. If historical entitlement missing, report infeasibility and carry uncertainty into EV.
7. **Cross-asset public state:** sector ETFs, rates/Treasury yields, volatility, economic releases and credit proxies with true as-of vintage. FRED current revised series can leak; choose archived vintage where possible or delay/mark unreconstructable. No scraping paywalled data against terms.
8. **Graph edges for novel F3:** filings or company disclosures that existed as-of event plus confidence and availability, no inference from future stock winners or labels.

## C. Data contract and source-rights ledger

For every source log:
`provider, instrument universe, endpoint/feed, source plan, account entitlements, publication and provider first-observed timestamps, revision mode, adjustedness, dividend/source corporate action, retention/cache allowed, non-display vs group redistribution rights, quote age, request cap, cost today and cost in proposed production, reviewer/checked_at`.

Alpaca Basic historical SIP entitlement depends on account, endpoint/time delay and user rights. The previously successful historical fetch proves that *that account* had access to those historical calls, not that group distribution of derived quotes is licensed. Keep provider-only raw bars and any copyrighted text out of PR/Discord unless permission explicitly verified.

## D. Universe methodology

- U0 fixed66 and U1 current-survivor remain baselines for reproducibility.
- U2 PIT historical index with delisted and removed constituents is scientific target; sourcing must be public/licensed; free source may be unavailable.
- U3 broader liquid listings with captured historical issuance/delistings if viable, including midcap/smallcap where costs and broker access manageable. Broadness alone does not cure survival selection.
- Hold out markets from different years and sectors where feasible, but same-date held-out symbols share market shocks. Distinguish names-only stress from true unseen prospective time.
- Include survivorship-only sensitivity and bias bounds; explicit `DATA_LIMITED_SURVIVORS` status. Never claim all-US-equities alpha.
- Record rejected symbols/missing sessions and quality source timeline, not just 495/500 present symbols.

## E. Execution economics

- Separate purchase at opening print, market order, stop order, limit order with queue, after-close signal and order valid interval. RTH vs premarket/extended sessions explicit.
- If only daily OHLCV, possible stop-first/target-first and entry-before-low chronology unresolved. Use pessimistic/optimistic bounds, model uncertain fill and possible better fill only as sensitivity. Exact limit at daily low should not be counted as guaranteed.
- Bid-ask spread and impact modeled as cost separate from broker commission (often zero retail commissions); assumed 25bps/side must be stressed, not presented as measured. Price-gap through stop can exceed theoretical loss.
- Trading-calendar metadata (DST, holiday, half day), corporate action price level resets and data finality lag as already implemented.
- Paper hedge/margin financing & short borrow are separate from unlevered executable scenario. If not reliably costed, mark exploratory comparator not an actionable implementation.
- No model may use future whole-series standardized volume/returns or realized postannouncement earnings vintages. Write point-in-time feature parity assertions.

## F. Research artifact constraints

All experiments register source as-of, data hash, membership bias, missing assets, corporate actions and revision exposure. GitHub commits store aggregate metrics and hashes only where licensed. Large provider raw inputs in temporary runner or authorized private storage; no public raw vendor table, secret, or member positions. Retain exact reproduce instructions without exposing keys.

## G. Acceptance

Source-health dashboard and PIT test fixtures including mergers/delists/splits/cash distributions, sector reclassification, pre/post-publication and late corrections, missing SIP/entitlement, intraday order ambiguity, trading halt/gap and stale data. If essential data unavailable, disable that hypothesis; never use made-up “reasonable” values to produce confident profitable returns.