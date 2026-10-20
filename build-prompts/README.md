# APEX / STONKS2026 — Complete coding-agent build program

**Project:** https://github.com/JaredVogler2/STONKS2026  
**Updated:** October 20, 2026  
**Goal:** Build a production-quality, privately hosted, zero-recurring-subscription (within provider quotas) U.S. stock swing-trading **research and alerting** platform with 1–4 week horizons, Discord multi-user interaction, personal position tracking and risk alerts.

## How to use this with a coding agent

1. Give your agent [00_MASTER_AGENT_DIRECTIVE.md](00_MASTER_AGENT_DIRECTIVE.md) as the **permanent instruction**. Ask it to read [ARCHITECTURE_AND_CONTRACTS.md](ARCHITECTURE_AND_CONTRACTS.md) and the existing `raspberry-pi/` folder first.
2. Run **each numbered prompt below in order**, in the SAME repository; they are implementation assignments, not optional essays.
3. Require actual committed code, migrations, tests, screenshots, and a concise validation report at each phase. Never accept a mock frontend as completion.
4. Do not provide real keys to the coding agent's chat or a Git commit. Enter them directly into secure provider configuration/the admin application.
5. Do not enable live personalized financial recommendations to a group until data redistribution and advisory/legal requirements have been reviewed. Paper mode and individual research are available beforehand.

## Implementation sequence

| Phase | Prompt | Deliverable / gate |
|---|---|---|
| 00 | [Master directive](00_MASTER_AGENT_DIRECTIVE.md) | Global non-negotiables and workflow |
| 01 | [Repository & foundations](01_FOUNDATION.md) | Full-stack scaffold, CI, local dev, docs |
| 02 | [Data, identity & secrets](02_DATABASE_IDENTITY_SECURITY.md) | SQL migrations, encrypted config, auth and audit |
| 03 | [Market-data adapters](03_MARKET_DATA_AND_UNIVERSE.md) | Live IEX + delayed SIP, quality, eligible symbols |
| 04 | [Signal discovery](04_TECHNICAL_SIGNAL_ENGINE.md) | Real technical strategies and candidate ranking |
| 05 | [Catalysts & filings](05_CATALYSTS_AND_INTELLIGENCE.md) | Time-aware SEC, news, government/politician context |
| 06 | [Historical research](06_BACKTEST_AND_CALIBRATION.md) | Walk-forward, leakage-safe evidence, replay |
| 07 | [Signal decisions](07_SIGNAL_POLICY_RISK_TARGETS.md) | Gated signals, targets, invalidations, horizons |
| 08 | [Members & positions](08_PERSONAL_POSITIONS_AND_MONITORING.md) | Fill lots, each-user risk analysis, idempotent events |
| 09 | [Discord interaction](09_DISCORD_BOT_AND_ALERTS.md) | Shared alerts, private actions, outbound delivery |
| 10 | [Vercel dashboard](10_DASHBOARD_AND_UX.md) | Premium working Next.js app, real backend data |
| 11 | [Pi + deployments](11_RASPBERRY_PI_VERCEL_OPERATIONS.md) | 24/7 services, secure pairing, backup/recovery |
| 12 | [QA and adversarial audit](12_TESTING_AND_RELEASE_GATES.md) | Unit/integration/E2E/security/replay/reliability |
| 13 | [Final integration](13_FINISH_AND_ACCEPTANCE.md) | End-to-end demo, runbooks, actual deployment checklist |

Read [ARCHITECTURE_AND_CONTRACTS.md](ARCHITECTURE_AND_CONTRACTS.md) for canonical table names, integration protocols, states and recommended defaults. Read [SOURCE_AND_COST_REGISTER.md](SOURCE_AND_COST_REGISTER.md) for provider quotas and honest limitations.

## Suggested operating model

- **Raspberry Pi 5 4GB**: continuous Python scanner, signal evaluations and Discord Gateway worker (existing starter in `raspberry-pi/bot`, upgrade in place).
- **Next.js on Vercel Hobby**: PRIVATE owner/admin dashboard, read/write APIs, encryption service, reconciliation. No long-running WebSockets on Vercel.
- **Supabase Free PostgreSQL**: canonical append-only events and derived entities; per-user isolation; small database footprint.
- **Alpaca Basic**: free IEX live data for a small priority universe, delayed SIP for broader validation. DO NOT present IEX as full-market NBBO or SIP.
- **Discord private server**: shared setups and per-member ephemeral/DM position tools. No member requires a Vercel login for Discord use.
- **Cloudflare Free**: optional small heartbeat/recovery scheduler only, not a required part of the market engine.
- **No brokerage order placement**. Robinhood availability is eligibility checked from supported metadata plus a user-correctable list, not scraped credentials.

## Critical working definitions

**Quality score** (0–100) = internal ranking value.  
**Calibrated confidence** = measured probability of an explicitly stated event (e.g., target hit before stop within 15 sessions), backed by a held-out outcome set; otherwise display **Insufficient history**.
**Horizon** = estimated hold in trading sessions, with hard expiry (default maximum 20 trading sessions).
**Near real time** = live *IEX-only* observation for subscribed priority symbols; broad data may be 15 minutes delayed; outages/staleness must be visible.
**Zero subscription** = no compulsory recurring software/API expense under current quotas; excludes Raspberry Pi hardware, power, internet and any license/regulatory requirements.

## Agent checklist on every phase

- [ ] Implement actual functionality; avoid hardcoded fake signals in live views
- [ ] Update schemas, contracts, migrations, tests and docs together
- [ ] Add realistic fixtures with unmissable DEMO labels
- [ ] Use proper market calendars, ET exchange-local times and UTC timestamps
- [ ] Show provenance and freshness; fail closed on stale/ambiguous data
- [ ] No secrets in Git, browser JS, logs, build artifacts, test snapshots or Discord
- [ ] Run typecheck, lint, tests, build and applicable E2E; report what was NOT run
- [ ] Commit and summarize the resulting SHA, paths, verified steps and remaining limitations

**Start by sending the coding agent the entire contents of 00, then the Phase 01 prompt.**
