# STONKS2026 — APEX Swing-Trading Intelligence

Private research and alert platform: liquid U.S. equities, 5–20 trading-session opportunity windows, empirical probability calibration, zero-required-subscription architecture, protected Vercel management dashboard, Raspberry Pi 5 continuous scanner, private Discord group alerts and independently monitored user-reported entry positions.

## Run it in five minutes (no accounts, no keys)

```bash
corepack enable && pnpm install          # Node 22, pnpm 10
pnpm demo                                # dashboard at http://localhost:3000 -> "Enter local DEMO"
cd apps/pi-worker && uv sync && uv run apex demo      # the Pi side of the same synthetic story
uv run apex replay --all                 # 12 deterministic failure/recovery scenarios
```

Everything in DEMO is synthetic (fictional `DM*` tickers) and labelled as such.

## What is in this repository

| Path | What |
|---|---|
| `apps/web` | Next.js owner dashboard + signed agent/release API (Vercel) |
| `apps/pi-worker` | Python worker: market data, features, strategies, research, signal policy, Discord bot, model sync (Raspberry Pi) |
| `packages/contracts` | JSON Schema contracts → generated TypeScript and Pydantic types |
| `packages/quant` | cross-language test vectors (ledger, HMAC, envelopes, pairing) and the XNYS calendar |
| `supabase/migrations` | the `apex` schema (RLS on every table) |
| `scripts/` | Pi install/doctor/update/rollback/backup, deployment smoke test, cost report, secret scan |
| `.github/workflows` | CI, weekly cloud training, drift checks, model release |
| `docs/` | user, operator, admin, risk, backtest, security, cost, release audit, implementation log, ADRs |
| `raspberry-pi/` | beginner hardware and Discord guides; the original starter bot (kept; see guide 08 to upgrade) |
| `build-prompts/` | the specification this implementation follows |

## Documentation

- Members: [docs/README_USER.md](docs/README_USER.md)
- Owner/operator: [docs/README_OPERATOR.md](docs/README_OPERATOR.md), [raspberry-pi/08_APEX_UPGRADE.md](raspberry-pi/08_APEX_UPGRADE.md), [docs/ADMIN_SETTINGS.md](docs/ADMIN_SETTINGS.md), [docs/PI_WORKER.md](docs/PI_WORKER.md), [docs/AUTONOMOUS_TRAINING.md](docs/AUTONOMOUS_TRAINING.md) (cloud retraining), [docs/DISCORD_BOT.md](docs/DISCORD_BOT.md)
- Method and evidence: [docs/RISK_MODEL.md](docs/RISK_MODEL.md), [docs/BACKTEST_REPORT.md](docs/BACKTEST_REPORT.md), [docs/METHODOLOGY_REFERENCE.md](docs/METHODOLOGY_REFERENCE.md) (every formula and constant)
- For research and refinement agents: [docs/TESTING_AND_METHODOLOGY_REPORT.md](docs/TESTING_AND_METHODOLOGY_REPORT.md), [docs/TEST_EVIDENCE_MATRIX.md](docs/TEST_EVIDENCE_MATRIX.md)
- Safety: [docs/SECURITY.md](docs/SECURITY.md), [docs/COST_AND_QUOTAS.md](docs/COST_AND_QUOTAS.md)
- Status: [docs/RELEASE_AUDIT.md](docs/RELEASE_AUDIT.md), [docs/IMPLEMENTATION_LOG.md](docs/IMPLEMENTATION_LOG.md), [docs/adr/](docs/adr/README.md)

## For the coding agent

1. [Copy-ready coding agent launch prompt](build-prompts/START_HERE_COPY_TO_AGENT.md)
2. [Complete ordered phase specification](build-prompts/README.md)
3. [Master permanent agent directive](build-prompts/00_MASTER_AGENT_DIRECTIVE.md)
4. [Architecture, schema and API contracts](build-prompts/ARCHITECTURE_AND_CONTRACTS.md)
5. [Source licensing, verified free limits and cost register](build-prompts/SOURCE_AND_COST_REGISTER.md)

## For the person building the hardware

[Beginner Raspberry Pi hardware, Amazon shopping and Discord starter guide](raspberry-pi/README.md), then [08 — Upgrade to full APEX](raspberry-pi/08_APEX_UPGRADE.md).

## Important safety and operating constraints

- No automated brokerage execution, no Robinhood credential scraping.
- Market-data licensing and group/personalized securities-advice requirements must be reviewed prior to sending actionable personalized recommendations to a group.
- The free Alpaca feed is live *IEX-only* for priority symbols; consolidated data is not fully real time on the Basic tier.
- Vercel Hobby eligibility applies to personal noncommercial usage, and Hobby cron is not a continuous scanner.
- Investment probability estimates require actual point-in-time backtesting and out-of-sample calibration; do not label ranking scores as historical confidence.
- Secret keys must never be placed in GitHub, frontend bundles or public Discord messages.

**Status:** implemented and tested locally (see [docs/RELEASE_AUDIT.md](docs/RELEASE_AUDIT.md) for the exact commands and results). It has **not** been deployed to Vercel/Supabase, connected to a live Discord server or to Alpaca, or run on a physical Raspberry Pi from this environment; those steps need the owner's accounts and are listed in the operator guide. On the research evidence available (synthetic data only), every real strategy is **UNVALIDATED**, so APEX publishes labelled research setups or NO TRADE, never probabilities.
