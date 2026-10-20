# PHASE 09 — Rich Discord bot, group alerts, private modals and reliable delivery

## Mission

Upgrade the already committed `raspberry-pi/bot` proof of concept into a reliable production-grade **private Discord group** experience without requiring paid push-notification services. Preserve beginner setup and ensure members can manage their own positions by slash commands, buttons and modals. Discord is an interaction/notification front end, not the canonical financial database.

## Runtime and Discord rules

- Use persistent **Discord Gateway bot on Pi** for commands and interaction events. Do not require Vercel incoming Discord callback on the same app simultaneously (Discord interaction delivery can use Gateway OR HTTP).
- Use `discord.py` or another stable supported library pinned to reviewed version; slash commands, select menus, buttons, modals; guild-scoped commands with least permission.
- No privileged Message Content intent required for buttons/slash commands. Optional parsing of direct text replies `Bought 20 @ 181.25` can be developed only behind explicit consent and privacy warning, since public replies can expose fills; prefer button + modal for safe user experience.
- Discord requires initial interaction acknowledgement in <=3 seconds; defer and process async for slow backend calls; interaction tokens expire (document current official 15-minute behavior).
- Ephemeral replies for immediate private fill confirmations; DMs (only after opt-in) for private updates. No fallback to public channel containing personal data.
- Each alert embed has stable `signal_id` signed in custom component IDs or resolved server-side lookup; reject tampered/stale component IDs.
- Bot joins one or more allowlisted guilds ONLY, default one private server. Auto-sync commands with explicit guild ID. Revoked membership loses privileges.
- Notification sender obeys rate limits (429 Retry-After), uses durable outbox, jittered retries, dead-letter/error state. No re-sending same alert after restarting Pi or timeout.
- Implement Discord role ping only for users explicitly opted into high-priority mentions; no indiscriminate @everyone.

## Server/channel design

User-configured Discord server + channels:
`#high-conviction-alerts` — statistically validated published setups ONLY.  
`#active-trades` — shared changes to original thesis, published target/invalidation.  
`#exit-alerts` — group signal closure/invalidation, NOT individual personal sell orders.  
`#market-intelligence` — catalysts, warnings, watchlist, differentiated from trade alert.  
`#performance-reports` — timestamped calibrated aggregate scorecards.  
`#system-status` — owner/admin health, stale/failed feed incidents.  

Channel creation can be documented as manual Discord permissions to avoid overbroad bot privileges.

## Alert schema and formatting

A shared `NEW_SIGNAL` embed MUST include:
- symbol/company name, **LONG** / venue + account-eligibility caution, machine source event ID
- quality rank /100, separately probability `P(target before stop before deadline)` or **unvalidated**
- empirical sample count + calibration version, 80/95% interval if meaningful
- entry range, target(s), stop/thesis invalidation, net R/R estimate, price precision
- recommended horizon min/max trading sessions and expiry for unfilled alerts
- original decision time, quote timestamp and **IEX LIVE / SIP DELAYED / DEMO**; explicit data-age.
- why this signal was selected: 2–5 cited, measurable feature reasons
- counterevidence: at least one opposing factor or risk
- buttons: `Record My Entry`, `Watch`, `View Evidence`, `Mute Symbol`
- no fictional data; no fake backtest score; link to authenticated Vercel dashboard may only work for admin (don't promise members they can open it).

Personal `POSITION_UPDATE` DM payload:
- privately held symbol, self-reported cost basis, quantity, risk vs stop, current price provenance, P&L estimated, revised scenario and risk status
- differentiate `Hold thesis intact`, `Review invalidation`, `Consider trim`, `Target touched but fill unknown`, `Time exit review`, `Add criteria met` from unconditional broker-order instructions
- source event IDs and direct button `Update My Position`. If group personalized recommendations enabled, require compliance check and user explicit opt-in.
- throttle to material changes, urgency, and difference from last sent assessment; not spam each 1% movement.

Admin `SYSTEM_ALERT`:
- Pi offline beyond SLA, Discord disconnect, Alpaca/SEC API outage, stale quote cohort, database read-only near quota, failed key test, job crash, calibration expired; include what to do and last-good update.

## Commands

`/apex_help`, `/apex_health`, `/apex_signals`, `/apex_watch`, `/apex_unwatch`, `/apex_entry`, `/apex_positions`, `/apex_add`, `/apex_reduce`, `/apex_close`, `/apex_correct`, `/apex_notifications`, `/apex_export`, `/apex_delete_my_data`, and owner-only `/apex_admin_status`.

For all position-affecting commands:
- Require actor to be the interaction's actual user (Discord library event context).
- Validate exchange symbol length, positive finite Decimal price and quantity, bounded notes, fill datetime and position ownership.
- Ephemeral confirmation before destructive correction; explicit confirmation step for remove-all.
- Deduplicate interaction ID + client idempotency key, tolerate resends.
- Provide clear messages when market feeds offline while still permitting position metadata updates.

## Bot architecture upgrade

Separate layers `DiscordCommands -> ApplicationService -> SignedAgentAPI -> CanonicalDB`; don't mix SQL and slash callbacks. Repository starter SQLite becomes local offline spool/demo fallback, not primary live canonical DB. If Vercel unreachable, queue encrypted or minimally sensitive events locally and tell user “pending sync” without falsely claiming server saved the position.

Ship:
- bot command registration + permissions tests
- embed payload JSON snapshots; Discord field length and 2000-char bounds
- retry/dedupe/delivery receipt integration tests with simulated 429/5xx
- unit tests for ephemeral privacy, DM opt-out, cross-guild rejection, malicious component ID, deleted signal, revoked user
- no need to grant Discord Administrator permission
- rollback to starter with clear migration path and schema version check.

## Interaction demos

Show three users: A opens + adds NVDA, B watches then buys later, C only watches. Send one shared alert, three different private outcomes, then block one member's DMs and prove pending notification never leaks. Verify after Pi restart, no duplicate signal or position fill.

## Acceptance

User can install and connect bot using `raspberry-pi/` beginner guide, and from iPhone input entry by buttons/modals without typing SSH commands. Guild members see only their private data, Discord messages match audited backend records and the bot resumes after reboot.