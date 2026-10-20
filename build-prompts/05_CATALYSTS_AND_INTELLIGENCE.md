# PHASE 05 — News, SEC filings, politician disclosures, macro and event-driven signals

## Mission

Build the second major evidence pipeline: *timely public catalysts* that may alter a stock's 5–20-session forward risk/reward. Each piece of intelligence MUST have distinct `event_time`, `publication_time`, `first_seen_time` and `available_to_algorithm_at`. Do not treat a month-old politician transaction as a new buy at its transaction price.

## Source modules (capability gated)

1. **SEC EDGAR submissions**: official company submissions and filings metadata; forms 4/8-K/10-Q/10-K, S-3/424B for dilution, 13D/13G holdings. Extract metadata and source link; only download documents needed for validated watchlist. Use descriptive `User-Agent` with contact email according to SEC guidance; cap global requests at <=10/sec (recommend substantially fewer, e.g. 2/sec), cache and honor 403/429.
2. **Form 4 insider transactions**: parse the official filing's reporting owner, issuer, transaction codes (P/S vs compensation/award, option exercise, planned trades), direct/indirect ownership, shares, price, amount, 10b5-1 annotations where available, filing and transaction dates. Distinguish open-market insider purchases from non-discretionary awards and vesting; never label all ownership changes bullish.
3. **Congress/politicians**: use official House/Senate financial disclosure sources if legally accessible with automated means or verified provider/public datasets with explicit provenance. Preserve `transaction_date` and `reported_date`; Senate reporting may be up to **45 days** late. Disclosures should rarely independently initiate short-window swing trades; treat as lagged thematic context. If official feeds require manual steps or prohibit scraping, disable automatic collection and show a manual-import interface instead.
4. **News**: source-allowlisted RSS/APIs with license and usage review. Store headline, publisher, canonical URL, published UTC, first seen UTC, original source and entity-resolution confidence. Respect robots, API quotas and copyrights; no full text republishing. Don't scrape paid sites.
5. **Economic/market news**: optional public FRED economic series, government releases (BLS/BEA/Fed calendars where public) with release times, data revision identification and observed availability. Historical revised macro data must not be treated as information available pre-revision.
6. **Earnings and corporate calendars**: provider permitting free reliable calendar; otherwise manually curated official company investor relations links with uncertainty and expected/confirmed markers. Unexpected earnings within trade horizon may veto strategy.

## Relevance and novelty pipeline

- Normalize company ticker ↔ entity mapping, SEC CIK, aliases, historical ticker changes; disambiguate brands with repeated terms.
- Deduplicate reprints/near-identical wire articles using source URL, normalized headline, time window and text hash; no double count for widely syndicated report.
- Categorize catalyst type: earnings surprise, guidance raise/cut, contract award, FDA/regulatory decision, merger, buyback, material insider open-market purchase, dilution, share offering, regulatory action, government procurement, macro/sector shock, management departure.
- Tag direction as `potentially_positive`/`potentially_negative`/`mixed`/`unknown`. Avoid unconditional sentiment optimism; qualify context: valuation, surprise relative to expectations (if expectations properly sourced), magnitude, company size and price reaction already realized.
- Relevance: direct issuer vs supplier/peer spillover; evidence weight decays with age and price adjustment. Pre-event returns matter; a catalyst causing a +15% gap may no longer offer attractive upside.
- Time-causal features: age of disclosed event, unusual novelty, source trust, expected value of development, controversy/disagreement; do not use full article published later than the signal.
- Multiple independent source confirmations increase *source reliability*, not mechanically imply higher expected stock returns.

## Deterministic reasoning first

No paid LLM requirement. Create local rules + dictionaries + parsers + regular expressions for SEC codes, keywords and event taxonomy. Permit optional user-configured free-compatible LLM (local model on capable non-Pi workstation, or separately provided API) ONLY for summarization; supply citations, confidence intervals and source excerpt caps. LLM outputs are `unverified summary` until deterministic corroboration. Never let LLM hallucinate earnings dates or financial data.

## Output schema

For each `Catalyst`: immutable ID; issuer symbol; event type; actual event time; reported time; first_seen; canonical URL; headline <=200 chars; source grade; direction uncertain; initial vs revised report; raw source hash; regulatory flags; actionable-age category; cross-sector links; impact score (NOT estimated win probability); reason codes; confidence in ENTITY MATCH and EXTRACTION separated from confidence in future stock return.

## Dashboard

- News/activity timeline with event time, published time, detection lag and currently available data.
- Raw-source links (SEC accession or publisher).
- Configurable 8-K, insider, politicians, macro and earnings filters, plus *disabled* integration state explanation.
- Explain whether information was public at the exact signal timestamp.
- Show both bullish and bearish events, and whether price already reacted.

## Adversarial / accuracy tests

- A politician buys on Aug 1, discloses Sep 10: backtest may NOT use it Aug 1.
- Insider share award with code A should not be treated as spontaneous open-market buying.
- Major headline republished across 5 outlets => 1 catalyst evidence cluster.
- After-hours earnings release vs prior close: point-in-time correct.
- Missing earnings schedule must be unknown, not “no earnings”.
- A ticker reused for a new company or changed symbol must not mix entities.
- SEC 403, parsing errors, weird HTML and stale filings: robust backoff + quarantine.
- Revised macro series: sample matches original released vintage or feature marked unavailable.

## Acceptance

At least ten real-format but copyright-compliant fixtures, versioned parsers with measured precision on labeled samples, verified source URLs, and clear "unavailable/no legal free integration" states. No promises of rapid political transaction intelligence.