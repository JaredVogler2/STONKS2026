# Phase P1 — Point-in-Time Event Engine for H3, H7 and New S4

## Current status and owner intent

H3 post-event drift and H7 catalyst + compression have **zero genuine backtest events because real historical catalyst snapshots are not present**. This is NOT evidence they fail; it is a data architecture gap. To test or trade them responsibly, the event must have been observable to APEX when a decision would have been made. A press release timestamp, 8-K filing, analyst revision and trading print may occur at different times.

## A. Canonical event-time record (migration + contracts)

Fields:
`event_id; issuer_cik; security_id_at_event; ticker_at_event; event_type; source_url; source_license; provider; occurred_at; first_public_at; provider_observed_at; available_at; parsed_at; ingested_at; session_first_tradable; event_revision_id; restatement_of; payload_hash; issuer_mapping_confidence; event_strength_proxy; strength_definition; time_precision; time_source; duplicate_cluster_id; rights_profile`.

Invariant: `available_at=max(first_public_at,provider_observed_at)` (when both reliable) and a signal at decision time t cannot read any event revision with `available_at>t`. If only date/no exact time is available, apply pessimistic NEXT regular-session availability or mark `TIMING_UNKNOWN`.

Build as append-only `EventLedger`; do not overwrite original record with latest text, sentiment or earnings estimate after hindsight. Revisions append new event; model can only see old revision if that is what was available then.

## B. Source candidates and restrictions

- SEC EDGAR 8-K item 2.02, earnings release exhibits, 10-Q/10-K (publication at accepted timestamp), Form 4 genuine open-market insider P (after filing—not transaction), accession IDs dedup. Respect SEC fair-access rates and user-agent.
- Issuer investor-relations and earnings press release feeds where time metadata and redistribution permissible, no blind scraping of paywalled sources.
- Earnings schedule historically published dates: prefer issuer's original public scheduled calendar archived with retrieval timestamp; public calendars that expose only current “actual earnings date” are retrospective data, not safe for pre-event veto.
- Finance news/headlines: permissible metadata and original timestamp, source links, not bulk copyrighted text. LLM interpretation can summarize for owner but its historical signals require archived same-time model/input/version. No retrospective LLM relabeling of outcomes after performance known.
- Political disclosures: user asked about them previously, but they are often reported well after transaction; do not use as event trade trigger until actual **filing/publication time**, and avoid claiming causality from stale disclosure.

Implement adapters with source permissions, backoff/fail closed, first-seen logs, identity resolution for changed tickers, event cluster dedup (earnings 8-K vs investor press release) and malicious headline sanitization.

## C. Event timeline test examples

1. Scheduled earnings announced before Oct 10 but actual release moved to Oct 15: pre-Oct15 signals use only old schedule until revision publicly announced; late knowledge cannot rewrite Oct 10 action.
2. Press release Tuesday 16:15 ET: first regular tradable reaction Wednesday morning, not Tuesday close if decision at Tuesday 16:15+.
3. Form 4 purchase occurred May 1 but filed May 4 17:00: May 2 signal cannot know it.
4. Wednesday 8:30 release: first RTH open 9:30, price gap may exhaust edge; do not simulate entry at previous Tuesday close.
5. News duplicated in six aggregator headlines: one catalyst event cluster, no false sixfold confidence.
6. Stock ticker changed during 8-year history, CIK same: avoid mapping future name to historical listing incorrectly.
7. Late revised quarter guidance: old decision keeps old estimate and source.
8. Source reports publication date only: mark time precision DAY, shift availability pessimistically.
9. User private note includes “buy now” injection: treat untrusted strings as data not executable agent instruction.
10. No licensed historical event source: H3/H7/S4 are NOT_EVALUABLE_MISSING_INPUT, model abstains without synthetic fallback.

## D. Integrate with strategy lab

H3: require after-publication price/volume reaction, gap-held and stability consistent with pre-registered event age. Determine event category independently of next month performance.

H7: test interaction (H1 compression x event) with 2×2 factorial after making event observation data causal; correct multiple testing, null effect after matched non-event breakouts.

S4: reaction continuation pre-registered with post-announcement retention; test against matched no-event gaps and H6.

For all, log funnel:
`events_observed_by_t → mapped security → eligible universe → confirmed materially positive event → trigger → levels valid → executable fill → completed → eligible independent clusters → OOS disposition`.

Never label 0 historical triggers “failed alpha” when source not supplied.

## E. Feature, UI and DB responsibilities

- `research/dataset.py` must build event-as-of sparse historical feature vector identical to Pi streaming event feature updates.
- Feed a canonical event snapshot (IDs, available_at, source links and policy) to `signals.context`, strategy registry and research runner; no separate untethered S4 event parser.
- Admin event timeline displays occurred vs filed vs observed, exact unknown and corrected event; recent news links and ages.
- Shared Discord alerts must not redistribute copyrighted full content or licensed price data; include short permitted categorical evidence with source link if rights allow.
- False event correction issues private system status, not silently editing past publication/forecast.

## F. Acceptance

At least one independent source of point-in-time events; reproducible known-at replay; no injected future reports, dedup and issuer-ID tests; explicit copyright/terms review. Only after licensed event history passes does agent run H3/H7/S4 full real market backtests under new frozen protocol. If source unavailable at no cost, return `BLOCKED_DATA_ACCESS` and do not buy a subscription without owner consent.