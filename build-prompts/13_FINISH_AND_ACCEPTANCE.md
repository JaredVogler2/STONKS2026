# PHASE 13 — Assemble, validate, ship and hand over APEX

## Objective

Integrate all preceding phases into a full, deployable, reliable, secure, aesthetically strong application. A coding agent must DO the work, not summarize what a human could code. Use the versioned contracts, runnable tests and documentation as source of truth.

## Required end-to-end product flows (verify individually)

### Flow A — No-cost first-run
New clone → install dependencies → run local demo with zero provider secrets → admin sees DEMO, all sections render → deterministic scanner publishes a fictional signal flagged DEMO → Discord adapter test shows payload → user creates a demo lot → replay advances time → scenario-specific private update is generated → close lot → model metrics and user metrics are distinct. No calls to paid APIs.

### Flow B — Admin onboarding
Owner deploys Next.js to Vercel linked to private GitHub, enables Vercel Authentication for **All Deployments**, configures Supabase environment securely, initializes migrations. Owner creates Discord app in Developer Portal, invites bot privately, configures Raspberry Pi. Pairing wizard validates device and encrypted transfer. Connected provider statuses accurately show last test/age/permissions. Production access denial verified in browser with logged-out non-owner session.

### Flow C — True market observation
Alpaca Basic credentials set in Settings and safely provisioned to Pi; asset universe harvested as available → daily bars fetched → liquid universe screening → priority symbols rotated within live-IEX 30 symbol Basic cap → technical features computed → published ONLY IF genuinely validated model/policy gate and compliant delivery mode. Otherwise show analysis only, with truthful NO TRADE. No real symbol priced with mock data in live mode.

### Flow D — Real multi-user Discord monitoring
One shared signal (can be DEMO) appears in high-conviction channel with evidence. Three authorized guild users record different entries and sizes, add partial fills and reductions. Private portfolio data stays private; position recommendations/assessments differ by user and react to horizon, target/stop/regime. DM blocked → pending private alert. Published model outcomes measured without cherry-picking only users who traded.

### Flow E — Degradation, backup, recovery
Pi internet unplugged and restored, Alpaca 429, Supabase temporarily read-only, Discord reconnect, Vercel temporary failure: source status degrades, no invented or repeated alerts, position events spool/retry, system health shows real fault, recovery revalidates data, incident logged. Owner restores an encrypted test backup and tests authentication boundaries.

### Flow F — Compliance release switching
Default mode DEMO/PAPER. Explicit control panel flags `ALLOW_EXTERNAL_MARKET_DATA_REDISPLAY`, `ALLOW_PERSONALIZED_GROUP_GUIDANCE` and `ALLOW_COMMERCIAL_HOSTING` are **OFF** and cannot be enabled by changing a local boolean alone without required administrative acknowledgement and externally verified permissions. Do not imply checkboxes alone ensure legal compliance. The system supports safe group research notices and direct member-entered watch/position tracking while personalized order-style recommendations stay unavailable pending review.

## Acceptance matrix (all true or status reported)

| Domain | Mandatory acceptance |
|---|---|
| Full-stack | Clean install, build, signed APIs, schema migrations, offline fixtures and working admin UI |
| Quant | Real computations, benchmark comparison, OOS calibration or appropriately labelled unvalidated status, no leakage |
| Market | 15-min SIP and live IEX distinction, quota respect, tradability uncertainty, staleness circuit breaker |
| Research | SEC/news source time-aware, returns not backfilled with information published later |
| Risk | Entry zone, stop, target, time horizon, reason codes and immutable versioned publication |
| Members | Separate authenticated privacy and accurate lots, cost basis, realized/unrealized P&L |
| Discord | Actual bot code, buttons/modals/private replies, dedupe, retries, blocked DM fallback, rate-limited |
| Vercel | Private production and previews, no serverless persistent worker, secure bootstrap env |
| Raspberry Pi | Simple install, automatic reboot survival, backup guide, no port forwarding, sparse resource use |
| Zero cost | No required paid API, free quota forecast, graceful throttling/pause, current provider documentation links |
| Security | No leaked secrets, RLS/API actor ownership, HMAC/nonce, audit and retention |
| QA | Unit, integration, replay, adversarial and E2E evidence; failed cases documented and fixed |
| Delivery | Current repo committed with all docs; live deployment status accurately stated |

## Exact user documentation to hand over

Generate:
- `docs/README_USER.md` — beginner overview: what APEX does/doesn't do, how to add own trade, how private notifications work, what confidence means.
- `docs/README_OPERATOR.md` — dashboard/Discord bot setup, Pi install on new device vs migration from starter, troubleshooting.
- `docs/ADMIN_SETTINGS.md` — all configuration fields, optional vs required API keys, how to test/rotate/revoke, what stays on Pi vs Vercel.
- `docs/RISK_MODEL.md` — calculations, target/stop methodology, signal labels and uncertainty.
- `docs/BACKTEST_REPORT.md` — research periods, source bias, benchmark, methodology, performance and limitations (NEVER make up results).
- `docs/SECURITY.md` — threat model, backup encryption, scope, keys, permissions, retention and data deletion.
- `docs/COST_AND_QUOTAS.md` — current official free plan links and computed capacity forecasts.
- `docs/RELEASE_AUDIT.md` — exact test commands, artifacts, passing/failing tests and outstanding risks.
- `docs/IMPLEMENTATION_LOG.md` — phase-by-phase changes, commit SHA, deficiencies and remediation.

## Final agent response MUST contain

1. Actual repository location and commit SHAs, branch/deployment target and code layout.
2. Completed vs partial vs not implemented in a compact, truthful matrix.
3. Precise local commands to run Next.js and Pi worker, with no secrets in printed text.
4. Actual typecheck/lint/pytest/unit/integration/Playwright/build/replay results, not estimated passes.
5. Dashboard URL, Vercel deployment status and authentication test **only if deployed and verified**; otherwise clear deploy steps.
6. Discord bot real live test results **only if connected and tested**, otherwise owner portal steps.
7. Quota/operating-cost assessment and legal/redistribution/commercial-use blockages.
8. Current model validation statistics only when computed from a documented historical source; no invented 80%/90% claims.
9. Known limitations, how to reproduce errors and recommended next iterations.
10. Final beginner checklist: "from hardware powered off to first DEMO Discord fill" with exact commands and which secrets stay in provider UIs.

## Quality bar

What must NOT be considered done:
- gorgeous page with no backend
- fictional ticker recommendations masquerading as current
- LLM guessing trade probabilities
- regular chat reply publicly exposing user entry
- Pi that only runs while SSH session remains open
- Vercel Hobby cron set to every minute
- key pasted into `NEXT_PUBLIC_*`, GitHub or Discord
- using raw 2026 current tickers to backtest decades of delisted companies without bias warning
- claims that delayed politician filing occurred when trade was placed
- paper trades presented as Robinhood broker orders
- an advisory/personal trade service enabled to unreviewed group
- “tests passed” without executing them.

**Final instruction:** Ship reliable, privacy-safe, empirically honest system using no mandatory paid service. Prefer a less flashy working feature over a spectacular untested one. Do not call the project production-complete until the complete acceptance matrix is supported by test and deployment evidence.