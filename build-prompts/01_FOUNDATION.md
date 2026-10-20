# PHASE 01 — Repository foundations, developer experience and vertical demo slice

## Agent assignment

Read `00_MASTER_AGENT_DIRECTIVE.md`, `ARCHITECTURE_AND_CONTRACTS.md`, all existing files in `raspberry-pi/`, and repository history. Implement a maintainable monorepo WITHOUT deleting the beginner Pi documentation or breaking its installed service. Create a genuinely runnable vertical demo before adding external integrations.

## Proposed repo layout

`apps/web` Next.js App Router + TypeScript + Tailwind + shadcn/ui, Recharts + a chart provider whose license is compatible with personal use.  
`apps/pi-worker` Python 3.11+ asyncio scanners, data adapters, Discord Gateway integration and lightweight inference. Prefer replacing old `raspberry-pi/bot` using compatibility shim and migration, OR keep implementation under `raspberry-pi/bot` if lower risk—document chosen canonical location.  
`packages/contracts` JSON Schema/OpenAPI request/response/event definitions and TypeScript/Pydantic code generation.  
`packages/quant` pure calculation spec fixtures (Python owns numerical analysis); browser displays outputs only.  
`supabase/migrations` ordered SQL migrations, seed schema and RLS tests.  
`fixtures` offline market scenarios, normal + failure traces; DEMO label mandatory.  
`tests` unit/integration/e2e with Playwright and Python pytest.  
`docs` architecture decisions, install guide, env matrix, user manual, implementation log, cost/quotas and threat model.  
`.github/workflows` lean lint/test/build pipeline respecting limited GitHub Actions budget.

## Technology decisions

- Next.js stable version pinned in lockfile; TypeScript strict; React; Tailwind; shadcn/ui; Zod for runtime boundary validation.
- Python standard async stack, httpx, Pydantic, Pandas/NumPy where necessary; avoid libraries requiring elaborate compilation on ARM64; scikit-learn for OFFLINE training if Pi performance permits or train elsewhere.
- pnpm workspace or npm workspaces: choose once and document Node version; no floating dependency ranges in final lockfile.
- SQLite local write-ahead spool, Supabase Postgres canonical DB. Use UTC.
- Easy dev command: `pnpm dev` for web; `make pi-dev` or `python -m apex_worker` for Python; `make test` for full tests.
- Server requests must be typed; use OpenAPI-generated or contract-test approach. Avoid writing independently drifting Python/TypeScript DTOs.

## Deliverables

1. Working Next.js shell with protected page routing and responsive nav for Overview, Signals, Intelligence, Positions (future restricted), Backtests, Alerts, Settings and System Health.
2. Deterministic `DEMO` dashboard populated by fixture data through the same service layer that production will use. Large visible DEMO / NOT LIVE banner and source-age badge, never ambiguous demo prices.
3. Python CLI `apex doctor` checking OS, Python, local SQLite, mock-feed, optional credentials and a dry-run discord adapter; clear remedial messages.
4. End-to-end simulated candidate → signal → outbound notification intent (not actual Discord unless credential configured) → member paper entry → risk event sequence, persisted locally. This is the skeleton reused by later phases.
5. Typed JSON Schema definitions for `MarketObservation`, `MarketBar`, `FeatureSnapshot`, `SignalCandidate`, `TradeSignal`, `PositionFill`, `PositionEvent`, `NotificationIntent`, `WorkerHeartbeat`, `ServiceHealth` and `AuditEvent`.
6. Repo hygiene: root README, .gitignore, .env.example files with fake placeholders only, precommit secret scan, ESLint/TypeScript, Ruff/pytest and Playwright smoke.
7. GitHub workflow triggers with concurrency cancellation, minimal permissions, no secrets on PR from forks, and optional manual heavy backtests rather than burning free minutes.
8. Development docs with specific commands for Mac/Windows/Linux and Pi ARM64.
9. Coding agent must identify and reconcile existing `raspberry-pi/bot` commands; do NOT leave duplicate online Discord bots consuming the same token.

## User experience standards

- Financial-terminal sophistication: information dense without clutter, elegant dark base + accessible contrast; color is not sole meaning.
- iPhone-first responsive layouts; touch areas ≥44px, keyboard access, meaningful tooltips.
- Status shows Data Feed: MOCK/DELAYED IEX/SIP and last updated time; risk banners above charts.
- Charts annotated signal events, target/stop bands, horizon date/session, holding-period uncertainty and actual as-of bar.
- Empty loading/error/unconfigured states must be beautiful, honest and actionable; no random glowing dummy metrics.

## Test scenarios

- Completely fresh clone + no secrets: dependency install, migrations on local mock store, `pnpm build`, unit tests, screenshot E2E and DEMO journey succeed.
- Misconfigured Supabase/Discord API keys: graceful provider status, no build-time crashing and no exposed exception/token.
- Simultaneous users cannot see each other's demo position.
- Market closed, DST, half-day and delayed quote visibly correct.
- Data source disconnected never silently shows a new trade as LIVE.

## Acceptance gate

Provide a public-facing local visual walkthrough or saved screenshots (repo screenshots may be committed if no secrets), all run commands and exact test results; commit working code. Do not proceed to Phase 02 until Phase 01 passes.