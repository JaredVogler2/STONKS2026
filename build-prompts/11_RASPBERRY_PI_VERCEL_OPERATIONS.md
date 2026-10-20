# PHASE 11 — Raspberry Pi 24/7 worker, private Vercel deployment and resilient zero-subscription operations

## Mandatory amendment — October 20, 2026: Laptop-free training

**This amendment supersedes any earlier suggestion that a personal laptop is needed for model retraining or manual Pi model deployment.** Read and implement [Autonomous cloud training amendment](AMENDMENT_2026-10-20_AUTONOMOUS_CLOUD_TRAINING.md). GitHub-hosted Actions runs recurring model training/evaluation, a private versioned model registry stores approved artifacts, and Raspberry Pi autonomously verifies, loads and rolls back models. Integrate into existing work without restarting or overwriting it. Required implementation: Phase 06 train/validation; Phase 11 deployment/refresh; Phase 12 chaos/security; Phase 13 laptop-free E2E acceptance. Offline DEMO must work without accounts. All model promotion must satisfy honest point-in-time/OOS risk and calibration gates; $0 recurring is contingent on quota.

**Specific Phase 11 additional deliverables:** private signed model registry + release API; Pi outbound manifest polling and signature/SHA256/feature-compatibility verification; safe non-executable model format; atomic swap, prior-version rollback, version acknowledgment and private system health. Implement GitHub Actions model release workflow, dashboard model version controls, secure short-lived release credentials and automatic catchup after outage. Absolutely no recurring laptop, SSH copying or manual deployment. Retain running validated model if candidate fails or budget exhausted (subject to expiry).

## Mission

Make the application boot reliably and safely after power or network outage, stay within free plans and be maintainable by a complete Raspberry Pi beginner. All install instructions must use the existing `raspberry-pi` docs as starting point, updating them where needed.

## Deployment components

### Raspberry Pi 5 (4 GB)
- Raspberry Pi OS Lite 64-bit, Python virtualenv, systemd services: `apex-market-scanner.service` and `apex-discord-bot.service` (or one supervised process with independently health-checked components). Existing `apex-pi-bot` service must be migrated, disabled or replaced; NEVER run two competing bots with same token.
- Separately health-check market ingestion, signals, Discord Gateway, signed backend API, local spool, memory/disk, NTP clock. Structured logs with masked credentials; log rotation.
- No Docker necessary on small Pi, but can be optional; keep beginner install simple.
- Local disk-backed work spool using SQLite WAL and idempotent event records. Offline for 6 hours → never hallucinate live quote updates; replay queued writes on reconnect.
- Watchdog restart-on-failure with bounded backoff and uptime logging; no endless token-error reboot loop. Lower-memory processing, disk retention and swap monitoring.
- One-command `./scripts/apex-install.sh` or `make pi-install` with safety validation, token preflight, version compatibility, dependencies and systemd registration. Never rewrite .env without consent.
- One-command `./scripts/apex-doctor.sh` prints safe provider health and specific fixes, never secrets.
- Manual rollback via git commit selection and safe schema compatibility check.
- Scheduled base workloads: premarket data refresh if source supports, every 15min discovery when market open, priority symbol IEX stream, nightly backtest diagnostics at low load, after-close replay/summary. Use ET exchange calendar; no weekends except appropriate delayed news/health.

### Vercel frontend/back-end
- Next.js app deploy from GitHub `main` via Git integration. Project MUST be configured for **Vercel Authentication → All Deployments** including production, confirmed in test (as supported by Sep 2026 change).
- Set server env and separate prod/preview config; previews do not connect to actual production Discord channel or emit signals by default.
- Node function execution bounded; no long-running scanner in serverless API handlers. API routes for short signed ingestion and admin requests only.
- No required paid plan or Vercel cron more frequent than daily. For scheduled sub-hour work rely on Pi, optional Cloudflare thin trigger.
- Vercel Hobby noncommercial/personal eligibility notice on admin setup. Multi-user commercial service demands hosting/legal re-evaluation.
- Verify production alias is actually protected, including `/api/v1/*` and previews. If protection automation bypass used, also require signed agent HMAC on internal endpoints. No "bypass secret" as sole trust boundary.

### Supabase PostgreSQL
- Free project, tables + migrations, RLS/server-owned authorization, policy tests. Pooling safe for serverless.
- Cron or manual compact/retention for high-volume observations. Database warning at 350MB/450MB; stop tick persistence before exceeding 500MB.
- Backups: since free offering does not promise PITR, provide guided secure pg_dump/export of critical metadata, encryption of backup at rest and restore test. No backup upload to repo.
- Inactivity pause: dashboard explicitly reports and recovers gracefully; activity should be legitimate useful queries, not just fee-evasion pings.

### Discord and subscriptions
- Private server with least-privilege bot; member DM opt-in and channel routing. Ensure valid status after restart.
- Validate multi-member privacy and no nuisance group spam. Discord outage → backlog and later deduped notifications with original event timestamp and staleness disclosure.

### Optional Cloudflare
- Worker Free can send *lightweight* heartbeat alerts and probe cloud health; it does not replace the Pi. No public open endpoint that causes costly full scans.
- If configured, protect secrets in Worker secrets, sign requests and log no credentials; respect free cron/CPU/subrequest limits.
- If Pi + Vercel inaccessible, no false "always online" claim; daily/periodic independent alert may depend on external service availability.

## Secure pairing deployment runbook

1. On Pi, generate private/public encryption keypair + random HMAC secret using approved library/cryptographic RNG. Private key stays on Pi with owner-only permission.
2. In Vercel bootstrap, configure Supabase connection/server keys and AGENT HMAC stored securely.
3. Authenticated admin opens Pi Pairing wizard, exchanges public key and one-time pairing challenge/expiration. Ensure challenge proves possession, not just a typed public key.
4. Pi connects outbound HTTPS to admin API using signature+nonce+timestamp; no router port open.
5. Verify device identity and owner approval, register active public key, provide sanitized config and short-lived key provision envelope encrypted to Pi public key (never full secrets in browser or queue).
6. Tests: expired challenge, replay, lost Pi, rotation, revocation, malformed signature.
7. Device acknowledged as configured only after real connectivity test.

## Complete beginner operational guide update

Extend `raspberry-pi/README.md` to link APEX deployment instructions; screenshots or step-by-step text for account setup, GitHub Deploy Key, Vercel setup, Supabase setup, Discord server, API key configuration and simple sample successful message. Include distinct `If you already have the starter bot installed` migration procedure and revert path. Explain physically nothing new must be purchased if Pi 5 with fan/power/SD is already working.

## Outage / quota SLA

Define explicit SLA examples (editable): priority price observations stale after 2 minutes if subscribed and market open; broad bars stale after 30 min, Pi heartbeat late after 3 successive intervals; recovery state after revalidation. These are goals, not guaranteed free-feed behavior. On stale, suppress price-based recommendations, stamp last observation, send system warning after hysteresis.

## Final deployment artifacts

- Config samples `.env.example` for local/Vercel/Pi; secrets omitted.
- SQL migrate/rollback commands and bootstrap no-sensitive-output.
- Vercel settings checklist with screenshot-equivalent step labels.
- Automatic Pi service unit templates and update/rollback scripts.
- Deployment smoke that verifies protected Vercel prod dashboard, signed agent endpoint, database, live Discord ping and DEMO replay.
- Cost report estimating requests/day, DB storage/month, free-tier headroom and network egress for configurable number of symbols/members.
- Security report proves no Pi inbound ports and no publicly accessible positions.

## Acceptance

Pi recovers automatically from simulated crash/reboot, preserves pending event queue, reconnects to Discord, honors rate limits and resumes market scanning only when fresh feed. Vercel auth protects actual production URL (not just previews). No paid plan required for validated personal-scale operation.