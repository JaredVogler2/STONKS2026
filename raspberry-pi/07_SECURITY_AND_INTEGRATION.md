# 07 — Security + architecture for future APEX integration

## What exists TODAY

- Private repository with a build-and-install manual.
- Raspberry Pi Discord Gateway **starter** bot.
- Guild-scoped Discord slash commands, ephemeral responses.
- Local SQLite demo entries keyed by **Discord user ID**, with ownership checks on reads and deletes.
- systemd restart-on-failure service, auto-start on boot.
- Secrets kept in untracked local `.env` and not printed to logs.
- NO live stock monitoring, API trading, Vercel integration, or calibrated investment suggestions yet.

## Recommended future production architecture

~~~text
                  ┌──────────────────────────────┐
                  │ Private APEX Vercel dashboard │
                  │ admin auth + server endpoints │
                  └───────────────┬──────────────┘
                                  │ authenticated
                           ┌──────▼───────┐
                           │ Database     │
                           │ RLS/ownership│
                           │ signals      │
                           │ positions    │
                           └──────▲───────┘
                                  │ restricted API
   Market feeds ──► Signal engine ─┤
     (cloud jobs)     + evaluations│
                                  │
                     ┌────────────▼─────────────┐
                     │ Raspberry Pi 5 at home   │
                     │ always-on Discord client │
                     └────────────┬─────────────┘
                                  │ outbound TLS only
                                  ▼
                        Private Discord server
                              │         │
                        shared alerts  private interactions
                                        │
                              member-specific position state
~~~

The Pi is for persistent Discord connectivity and lightweight user interactions. A market-data provider should be polled **once per symbol** in the appropriate backend; each member's position is then evaluated independently. Do not open inbound ports to the Pi or let it serve the public web dashboard.

## Never confuse shared and personal content

**Shared alerts:** signal ID, symbol, setup, thesis, target, invalidation, bounded horizon, provenance, age of data, and separate calibrated probability vs signal-quality score.

**Private records:** member's actual cost basis, lots, quantity, current hypothetical/unrealized return, stops, add/trim guidance and private alerts.

Prefer Discord interaction **buttons/modals + ephemeral acknowledgments**. Persistent position updates should arrive by private DM if allowed, or appear in an authenticated member portal. Users may block DMs, in which case store a pending notification and show it privately the next time they interact. NEVER assume a plain public channel reply is private.

## Requirements for the coding-agent APEX integration phase

1. Implement a **versioned server API** with OpenAPI/JSON Schema for `signals`, `positions`, `fills`, `users`, `alerts`, `bot heartbeats` and `health`.
2. Link Discord user IDs to authorized member records. Validate Discord signatures for HTTP interactions if using webhooks; the Gateway bot must validate the origin of event metadata.
3. On submit: require a unique immutable `signal_id`, valid ticker, positive finite price/quantity, optional fill time, and client-generated idempotency key. Accept partial fills, add lots, reduce lots, close positions and corrections with an audit trail.
4. Persist positions to canonical backend storage. Use row-level authorization and deny cross-user access. Do not trust a member-supplied Discord user ID.
5. Track source timestamp and market session for each quote. Guard against stale data and duplicate signals. Never calculate alert profitability from a quote that is no longer fresh enough for its use.
6. Maintain separate states: NEW → WATCHING → OPEN → TRIMMED → CLOSED, with EXPIRED and INVALIDATED as distinct terminal or review states.
7. Monitor each open position on a bounded cadence, with **independent decision thresholds**. A target or stop breach triggers a risk event, but quotes alone do not prove a limit order would have filled.
8. For ADD recommendations, require incremental expected value, remaining risk budget, liquidity capacity, and improved thesis; prohibit averaging down purely on price decline.
9. For SELL/HOLD/REDUCE recommendations, consider thesis invalidation, remaining expectancy, stop proximity, regime shift, correlation, tax/friction assumptions and maximum holding deadline.
10. Make all recommendations **research alerts, not automated orders**. Test through paper trading and require confidence calibration with out-of-sample observations.
11. Configure alert routing, per-user opt-in, timezones, minimum notification importance, throttling, and duplicate suppression.
12. Build a store-and-forward queue. The Pi may reboot, internet may fail, or Discord may be rate-limited; do not drop critical events. Deduplicate by event ID and preserve exactly-once EFFECTS where possible.
13. Track data-provider quotas separately from Discord rate limits; exponential backoff on 429/5xx; cache symbol quotes once for all member positions.
14. Provide a security posture appropriate for private financial information: server-side encryption at rest for secrets, HTTPS in transit, token rotation, minimal Discord permissions, bounded retention, audit logs, and clear user deletion/export paths.
15. Health reporting must distinguish **Pi online**, **Discord connected**, **market feed fresh**, **model calibrated**, and **alerts delivering**. One green light is insufficient.
16. Do not auto-install additional hardware, AI HAT, broker plugins or third-party paid tools.
17. Verify provider redistribution rights and hosting terms before multicasting market quotation data or monetizing shared securities recommendations.

## Security checklist before production release

- [ ] Never expose sensitive secrets through `NEXT_PUBLIC_*`, HTML, browser requests or screenshots
- [ ] No secrets committed to GitHub; rotate any accidentally exposed keys immediately
- [ ] Private Vercel access configured for production AND previews; authenticated server routes
- [ ] Strong Discord user authorization and per-user access controls (not just hiding frontend fields)
- [ ] Pi has outbound-only internet access; no public forwarded router ports
- [ ] Pi SSH keys, Discord tokens, and bot runtime user follow least privilege
- [ ] Pi release updates have a defined review/test/rollback procedure
- [ ] Database encrypted/controlled access and documented backup recovery
- [ ] Auditable, deduplicated model signals and position transitions
- [ ] Explicit stale-quote and missing-provider FAIL-CLOSED logic
- [ ] Member consent and signal performance disclosures
- [ ] Financial/regulatory and market-data redistribution requirements assessed for group usage

## Why not put the real-time scanner entirely on Vercel?

Vercel functions are not a replacement for a process that must hold an uninterrupted WebSocket connection. Run a persistent Gateway connection on the Pi, and use appropriately scheduled cloud tasks or a separate scanner process for market data. The web dashboard can stay serverless.

**Zero subscription does not guarantee zero costs:** equipment, power, internet, hosting quotas, database retention, data licensing, regulatory requirements, and free-tier conditions may vary. Vercel Hobby use restrictions must be checked if operating an advice service or expanding beyond a strictly personal noncommercial project.

## Longer-term alternative

If you already own an x86 mini-PC that can remain on, it can also run this Python Discord Gateway service. A Raspberry Pi is not intrinsically required. A Pi 5 4 GB is a straightforward dedicated appliance, but if current 2026 Pi pricing is high, compare the cost of a mini-PC you already have before ordering.

**Do not rely on this starter for trading decisions.**