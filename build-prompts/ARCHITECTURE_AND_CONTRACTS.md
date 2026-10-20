# APEX canonical architecture and contracts

## Runtime placement (cost- and reliability-aware)

`Pi Python worker`: asyncio scheduler, exchange calendar, market connector, feature calculations/inference, WebSocket IEX priority feed (at most Basic's limit), Discord Gateway/interaction bot, local durable spool for transient outage. No open inbound ports, no public Pi HTTPS endpoint.

`Next.js on Vercel`: owner-only admin dashboard behind Vercel Authentication (all deployments enabled); short REST APIs, decrypt/encrypt configuration after authorized admin request, HMAC agent endpoints, health and data views. No infinite loops, market WebSockets, live polling from user browsers or sub-minute cron assumptions.

`Supabase Free Postgres`: canonical signals, snapshots, position lots, transactions, model diagnostics, events, notification deliveries, secrets ciphertext, audit, public metadata, replay runs. Keep heavy historical OHLCV as compressed local Parquet on Pi/backup; save curated bars and feature snapshots to Postgres. Budget database under ~350 MB warning and 450 MB emergency stop (500 MB free quota currently).

`Discord`: one small private server; guild-scoped commands, buttons/modals, ephemeral confirmations and optional DM updates. Per-member subscriptions; channel map configured in dashboard. One shared quote per symbol; N independent member positions.

`Cloudflare Worker (optional)`: thin cron heartbeat and outage alert, or manually-triggered health checks, not a required dependency. When enabled, HMAC-signed calls only, free-CPU budget.

## Boundaries and ownership

- Domain modules `market`, `features`, `catalysts`, `signals`, `risk`, `portfolio`, `notifications`, `operations`, `identity`.
- Backend contracts in `packages/contracts` as JSON Schema / OpenAPI with generated Python Pydantic and TypeScript types (or a clearly tested equivalent).
- No client code may directly access privileged Supabase table writes or encryption secrets. Safe read-only RLS access only if justified.
- Discord Gateway user identity is authoritative at the event handler; cross-user mutation attempts are forbidden.
- Supabase service role stays **only Vercel server-side**. Pi sends authenticated API events to Vercel; it never stores the service role key.
- Transactional outbox: successful DB event and notification intent are committed together; retry delivery without repeating user-facing notification.

## Contract and required tables

Implement SQL migrations, indexes, foreign keys, RLS/server permission strategy, ownership tests and retention. Suggested tables:

`app_users`(id uuid PK, discord_user_id unique nullable, role enum, created_at, disabled_at, consent_version)  
`guilds`(discord_guild_id unique, config_json)  
`guild_memberships`(user_id,guild_id,verified_at,revoked_at)  
`encrypted_integrations`(provider, ciphertext, iv, auth_tag, key_version, configured_by, tested_at, status, updated_at)  
`settings_history`(key, encrypted_or_redacted_change, actor_id, version, timestamp)  
`market_symbols`(symbol, exchange, asset_class, active, tradability_status, tradability_checked_at, eligible_reason)  
`quote_events`(symbol, ts_exchange_utc, ts_received_utc, provider, feed_type, price, bid, ask, volume, quality_flags, delayed_by_seconds) — aggregated/retained sparsely  
`daily_bars`(symbol,session_date,adjustment,source,OHLCV,ingested_at,asof_at) — selective  
`feature_snapshots`(symbol,asof_at,features_json,sources_json,feature_version,integrity_hash)  
`catalysts`(id,symbol,catalyst_type,event_at,disclosed_at,first_observed_at,source_url,confidence,parsed_json,source_hash,legal_classification)  
`market_regimes`(asof_at,regime,state_probabilities,inputs_json,version)  
`signal_candidates`(id,symbol,created_at,strategy_version,explanation_json,quality_score,gate_status,reason_codes)  
`signals`(id unique,symbol,side,entry_min,entry_max,target_primary,target_secondary,stop_level,horizon_min_sessions,horizon_max_sessions,expires_at,state,created_at,asof_at,feed,model_version,confidence_probability,confidence_interval,confidence_label,thesis_json,risk_json,immutable_snapshot)  
`signal_events`(id,signal_id,event_type,event_at,payload_json,dedupe_key unique)  
`position_accounts`(id,user_id,account_label,currency,paper_or_self_reported)  
`position_lots`(id,user_id,account_id,signal_id nullable,symbol,fill_at,fill_price,quantity_initial,quantity_remaining,fees,currency,source,revision)  
`position_events`(id,user_id,lot_id,event_type,quantity,price,event_at,client_idempotency_key unique per user,payload_json)  
`position_snapshots`(id,user_id,symbol,asof_at,avg_cost,total_quantity,unrealized_pnl,source_age,assessment_json)  
`notification_subscriptions`(user_id,event_type,channel_preference,min_priority,quiet_hours,enabled)  
`notification_outbox`(id,event_key unique,user_id nullable,route,state,attempts,next_attempt_at,payload_json,created_at,sent_at,discord_message_id)  
`delivery_attempts`(id,outbox_id,status,http_status,rate_limit_until,occurred_at)  
`agent_devices`(id,device_name,public_key_or_hmac_id,secret_version,created_at,last_seen_at,revoked_at)  
`agent_nonces`(agent_id,nonce unique,created_at,expiry)  
`job_runs`(id,job_name,started_at,finished_at,success,metrics_json,last_error)  
`backtest_runs`(id,config_hash,data_hash,code_hash,started_at,completed_at,metrics_json,bias_warnings)  
`backtest_trades`(run_id,signal_id,entry,exit,return_net,mfe,mae,metadata_json)  
`model_versions`(id,features_hash,training_window,validation_window,calibration_metrics,decision_thresholds,promoted_at,sha)  
`audit_events`(id,actor_type,actor_id,entity,entity_id,action,redacted_metadata_json,at).

Add row-level permissions. Position queries must always be scoped by actor's verified identity, plus query-level tests. Never expose positions to all members or to every admin by default; administrative personal-data access should require an explicit, audited privileged action where justified.

## Authenticated Pi → Vercel internal API

Provision separate randomly generated `AGENT_HMAC_SECRET` stored only on Pi and as a Vercel runtime secret. Each request:
- `X-APEX-Agent-ID`
- `X-APEX-Timestamp` UTC epoch seconds
- `X-APEX-Nonce` random ≥128 bits
- `X-APEX-Body-SHA256` digest of raw payload bytes
- `X-APEX-Signature` hex HMAC-SHA256 over canonical METHOD + PATH + timestamp + nonce + body hash.

Require ±60 seconds clock tolerance, constant-time comparison, body cap, nonce single-use persisted with TTL and active device check. Secrets rotate and revoke; identifiers alone do not authenticate. Writes receive durable ack + idempotency key. For bulk loads chunk ≤ configured size and retry 429/503 with exponential backoff/jitter.

Do not issue service role keys to Pi. No public unauthenticated health endpoints that disclose private data. Vercel Deployment Protection may challenge automated agent API requests: support legitimate automation bypass secret **only for the exact expected internal routes**, with additional application-layer HMAC and explicit allowlists; never turn off production protection globally. Test both production and preview to ensure no secret exfiltration through preview builds.

## Key configuration policy

Initial bootstrap secrets (put into server env via Vercel dashboard and locally on Pi using mode 0600): `SUPABASE_SERVICE_ROLE_KEY` (Vercel only), `SUPABASE_URL`, `APEX_CONFIG_ENCRYPTION_KEY` (Vercel only), `AGENT_HMAC_SECRET` (Pi + Vercel), Discord token (Pi) and required platform deployment bypass secret if used.

User-configurable keys (within admin UI): Alpaca API key/secret, optional SEC contact User-Agent, FRED token, approved news sources, Discord guild/channel mapping, optional notification webhook, optional model inference provider. Server encrypts user-supplied keys with versioned AES-256-GCM per field, random nonces, authenticated metadata and key IDs; never return values after save. Market credential access by Pi: provision a one-time scoped, short-lived encrypted secret delivery with **Pi public-key encryption** and paired client authentication; Pi decrypts with locally held private key in owner-only filesystem. Simpler manual Pi-only market token setup is acceptable only as clearly marked fallback; admin UI must be able to test server-supported integrations and show Pi synchronization state. Never store plaintext in database, queue, browser storage, crash dumps or logs.

## Versioned HTTP routes

`GET /api/v1/admin/health` — admin only; source status, freshness and quota.  
`GET /api/v1/admin/signals` — paginated scoped list with filter, provenance.  
`GET /api/v1/admin/backtests` — runs, distributions and failure modes.  
`GET/PUT /api/v1/admin/settings` — read sanitized config, encrypted writes, audited.  
`POST /api/v1/admin/settings/:provider/test` — rate-limited server-side connectivity test.  
`POST /api/v1/agent/heartbeat` — HMAC, state and clock skew.  
`POST /api/v1/agent/observations` — HMAC, batched and deduped input.  
`POST /api/v1/agent/signals` — HMAC, validated state transitions and immutable original signal.  
`POST /api/v1/agent/positions/events` — HMAC, Discord ID binding, self-report fills.  
`GET /api/v1/agent/work` — HMAC, only minimal secret-free work.  
`POST /api/v1/agent/notifications/ack` — HMAC, idempotent delivered/failed.  
`GET /api/v1/agent/config` — HMAC; sanitized config; no plaintext API secrets.  
`POST /api/v1/admin/replay` — admin only, demo or isolated test mode.

Only the Pi Gateway handles Discord commands initially. Optional Vercel HTTP Discord handler must verify Ed25519 on **raw request body**, handle PING, enforce ≤3s initial response, and avoid running simultaneously with Gateway interaction delivery. Do not set up both Discord interaction endpoints without an explicit switch and tests.

## Canonical time/market semantics

- Persist all instants in UTC (timestamp with timezone); display America/New_York ET with DST.
- Use exchange session calendar; do not assume every weekday is an open market or every close at 16:00.
- Never treat IEX volume as total market volume; normalize multi-source volume features using comparable feed provenance.
- Preserve raw unadjusted entry/exit execution prices, and clearly document corporate-action-adjusted history in backtests.
- A trade horizon is **trading sessions**, not calendar days.
- Stop triggers based on actual available observation; overnight gapping requires gap-aware modeled exit.
- Calculate P&L with fees, fractional shares, partial exits and dividends only if modeled/available.
- Separate `asof_at` (available-to-decision time) from `event_at` and `ingested_at`.

## Signal status machine

`DISCOVERED → EVALUATING → QUALIFIED → PUBLISHED → ACTIVE → CLOSED`; rejection `DISCARDED` or `NO_TRADE`, time expiry `EXPIRED`, stale/invalidation `INVALIDATED`. Every transition append-only, with ownership and dedupe keys.

Personal position state follows lots, not a single mutable average-cost field: `WATCHING → OPEN → PARTIALLY_CLOSED → CLOSED`, with corrections as new audited events. Signal closure does not force closing a member's self-reported holding.

## Release modes

- `DEMO` — deterministic offline fixtures; unmistakable watermark.
- `PAPER_LIVE_DATA` — actual quotes, paper simulation, no money; may be disabled if market license excludes group redistribution.
- `PERSONAL_RESEARCH` — individual/admin advisory-like research without third-party personalized signals.
- `GROUP_POSITION_ALERTS` — **disabled by default** until applicable compliance/licensing/hosting requirements reviewed.

Paper mode must function fully with zero API credentials using an offline replay dataset. Live mode never uses synthetic generated prices as fallback.