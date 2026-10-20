# Verified sources, quotas and licensing constraints — as of 2026-10-20

## Mandatory amendment — October 20, 2026: Laptop-free training

**This amendment supersedes any earlier suggestion that a personal laptop is needed for model retraining or manual Pi model deployment.** Read and implement [Autonomous cloud training amendment](AMENDMENT_2026-10-20_AUTONOMOUS_CLOUD_TRAINING.md). GitHub-hosted Actions runs recurring model training/evaluation, a private versioned model registry stores approved artifacts, and Raspberry Pi autonomously verifies, loads and rolls back models. Integrate into existing work without restarting or overwriting it. Required implementation: Phase 06 train/validation; Phase 11 deployment/refresh; Phase 12 chaos/security; Phase 13 laptop-free E2E acceptance. Offline DEMO must work without accounts. All model promotion must satisfy honest point-in-time/OOS risk and calibration gates; $0 recurring is contingent on quota.

This is a **research register**, not a permanent guarantee. Agent: on deployment, verify terms and limits directly from official providers and record date + URL; never assume a historical price is valid today.

| Service | Use | Free plan and meaningful constraint | Policy |
|---|---|---|---|
| Vercel Hobby | Private admin Next.js site and short APIs | Personal, noncommercial; function quotas; Hobby cron at most daily; new Sept 2026 Vercel Authentication can protect production free | DO NOT run always-on scanner on Vercel |
| Alpaca Basic | IEX real-time stocks, 15-min-delayed SIP historical; historical batching | Free; 30 symbols per IEX WS; historical 200 calls/min; IEX single exchange only | No claimed consolidated NBBO; inspect licensing for external redistribution |
| Supabase Free | Shared Postgres | 500 MB DB (read-only afterward), inactivity pause possible; limited backup and usage | Compact retention, independent encrypted backup, queue/spool |
| Discord | Multi-user alerts & private commands | Free bot APIs, rate limits; ephemeral privacy for interaction replies | Do not show personal fills publicly; avoid unrestricted @everyone |
| Cloudflare Workers Free | Optional heartbeat/backup scheduler | 100k requests/day, free Cron triggers ≤5/account; CPU limits | Only thin signed HTTP calls; no heavy calculations |
| SEC EDGAR | Form 4, Forms 8-K/10-Q, filings | Free, SEC fair access ≤10 requests/second total and correctly identified User-Agent | Point-in-time filed/observed dates; no "real-time politician trades" |
| Senate periodic reports | Public politician disclosure context | Reports can be filed up to 45 days after trades | Do not present as instant purchases; source terms and data quality |
| Other news / macro | Catalysts and regime | Source-dependent; many "free" APIs are trial or redistributor-limited | Verify license, timestamp, cache restrictions; disable unsupported APIs |
| Robinhood | User execution venue | Do NOT automate its credentials or scrape broker UI | Maintain eligible exchange/security list, manual tradability confirmation |

### Official documentation references

- Vercel Sep 9 2026 production protection: https://vercel.com/changelog/protect-production-deployments-for-free-on-every-plan
- Vercel Hobby policy: https://vercel.com/docs/plans/hobby
- Vercel Hobby cron restrictions: https://vercel.com/docs/cron-jobs/manage-cron-jobs
- Alpaca market data Basic specifics: https://docs.alpaca.markets/us/docs/about-market-data-api
- Supabase free database/quota: https://supabase.com/docs/guides/platform/billing-on-supabase
- Supabase inactivity pausing: https://supabase.com/docs/guides/platform/free-project-pausing
- Discord interactions: https://discord.com/developers/docs/interactions/receiving-and-responding
- Cloudflare free Workers quotas: https://developers.cloudflare.com/workers/platform/limits/
- SEC fair access: https://www.sec.gov/about/developer-resources
- Senate PTR disclosure delay: https://www.ethics.senate.gov/public/index.cfm/financialdisclosure
- NYSE redistribution documentation: https://www.nyse.com/connectivity/documents

## Genuine operational caveats

1. Historical SEC/public filings are NOT an intraday real-time edge based on when a politician actually traded; `trade_date` and `disclosure_date` differ.
2. Quoted Robinhood tradability cannot be guaranteed without a supported official verification source. Never promise every eligible stock is actually tradable in every user's account; use exchange/asset metadata plus manual confirmation.
3. Market data redistributed to other Discord users can be subject to contracts/fees even if the source's personal-use market API itself is free. A legal review is required before public or monetized distribution.
4. Personalized BUY/ADD/SELL guidance for a group may create investment-adviser obligations irrespective of whether the service is free. Build paper-only personalized functionality behind a release gate until reviewed.
5. Vercel Authentication protects users with access to that Vercel project. Discord-only users do not need dashboard access; use Discord Gateway on the Pi. Do not rely only on a web app login check to authorize APIs.
6. Zero required monthly fees is conditional, not a performance guarantee. A Pi consumes power and a free project can be throttled or paused.
7. An outage must display stale status, never substitute a generated/guessed quote.

## Free-tier preservation strategy

- Broad end-of-day/day-delayed scanning, bounded active subscriptions, capped fetches.
- Python Pi crunches features locally; GitHub-hosted Actions performs scheduled CPU-heavy retraining within shared free-minute quotas; signed approved model artifacts are automatically fetched and activated on Pi. No personal laptop required for ongoing operation.
- One cached quote per symbol feeds all members.
- Supabase retains signals, critical snapshots, position events and audit only; local Parquet compressed bars with rotate/backup.
- Dedup repeated alerts and exponentially back off APIs.
- Halt nonessential collectors at 70/85/95% configured daily quota thresholds.
- Optional Cloudflare Worker is a recovery/availability aid, not a subscription obligation.
