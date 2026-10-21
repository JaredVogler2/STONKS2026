# Amendment 02 — Independent Full Application Audit After PR #1 Completion

**Purpose:** Once v1 implementation is feature-complete, evaluate what truly works, what is simulated, and what is merely described. Do not start redesign before this evidence sweep.

## Assignment

Act as an independent, skeptical software architect, quantitative analyst, security engineer, performance engineer and new user. Audit CURRENT source/commits/PR/CI/production rather than trusting self-assessments. Produce `docs/V2_INDEPENDENT_AUDIT.md` with a traceable matrix and prioritized improvements. Do not deploy fixes during initial inspection unless critical vulnerabilities require coordinated action.

## Audit workflow

### A. Build and reconcile scope

1. Fetch PR #1 latest status, commit and changed files; read `docs/IMPLEMENTATION_LOG.md`, original `build-prompts/` and all amendments.
2. Build an inventory for each promise: signal discovery, Alpaca feed, SEC/news, H1–H8, regime, probabilities, backtesting, channel publishing, member entry/add/trim/close, Supabase SQL, encrypted Settings, Pi startup, signed models, GitHub Actions, dashboard and health.
3. For each, classify `IMPLEMENTED_AND_TESTED`, `IMPLEMENTED_UNTESTED`, `PARTIAL`, `DEMO_ONLY`, `STUB`, `MISSING`, `BLOCKED_BY_CREDENTIALS`, or `INCOMPATIBLE_WITH_FREE/Licensing`. Cite actual source paths and executable test.
4. Independently run lint, typecheck, migrations, contract drift, unit, integration, security tests, build and Playwright; distinguish reported vs executed results.
5. Check codegen and schema consistency across TypeScript, Python, SQL and Discord payloads. Find duplicated parsing or business rules; inspect backward compatibility with starter Pi bot.

### B. Full product journey

From a fresh clone in zero-secret DEMO:
- Open the web app on a desktop and iPhone-sized viewport and verify all menus and controls.
- Load fixture scanner; inspect candidate/score/negative evidence.
- Click one signal, view chart source timestamps/entry stop target and original forecast.
- Create shared DEMO Discord payload, test button/modal and per-member identity.
- Member A enters 10 @180, B enters 10 @190, C watches without buying. Confirm ownership is enforced by server/database, not CSS.
- Advance price + time; A adds 5, sells 8; B exits; C remains watcher. Verify FIFO, realized and unrealized, account cost basis, model success denominator, notification dedupe.
- Force model failure, quote stale, internet outage, Discord DM blocked, repo quota pressure. Verify safe degrade and recovery.
- Export position history, privacy-safe report and delete member data; check data retention constraints.

Document each step as screenshot/video, persisted record IDs (non-sensitive), endpoint response tests and actual as-of labels.

### C. Independent engineering audit

Review modularity: bounded contexts, dependency graph/cycles, typed contracts, configuration schemas, atomic outbox and idempotency. Identify invisible failure paths, synchronous blocking in Pi 4 GB asyncio loop, poor memory/CPU, unbounded market subscriptions, N+1 user-per-symbol quote fetches, stale cache and ambiguous retries. Prefer minimal targeted replacements over rewrite.

Compute performance budgets under 100, 500, 1500 simulated symbols and 5/20/50 members with 10/100 lots. Measure p50/p95 scan-to-alert latency, API rate, Supabase storage/month, Discord sends/day, Pi RAM and CPU. All measurements specify machine hardware and test methodology, no invented throughput.

Inspect admin screen operations: Settings test/save/rotate/delete changes actual server state; training dispatch enforces provider-side spend policy; rollback real signed manifest; import/export actual records; every button has a working callback or is disabled with explanation. Review mobile accessibility, keyboard navigation, load/error/empty states and dynamic charts.

### D. Quant research audit

For each hypothesis H1–H8:
- Verify defined strategy + enabled flag + candidate counts + reported status.
- Verify feature/data as-of, nonfuture adjustments, earnings/news publication timing, calendar/label horizon.
- Verify train/serve parity, calibrated probability, source-quality limits.
- Verify real historical experiments, not only synthetic red-team, and a frozen untouched holdout.
- Compare to SPY/QQQ/sector and matched universe controls with plausible execution costs, multiple-testing correction and trade overlap.
- Review sample size and statistical power of target-before-stop at horizon.
- Review proper “no edge” state; beware leaderboard sorting on favorable backtests.

### E. Security and permission audit

Probe anonymous site and APIs, forged admin request, Discord guild outsider, member A reading B's fill by guessing UUID, replayed Pi HMAC, signed artifact tampering, preview Vercel leaked deployment URL, CSP/XSS with malicious headline, agent SSRF through source URL, encrypted integration key exposures, CI environment misconfiguration, GitHub token exfil and logged secrets.

Validate Vercel Hobby eligibility and Alpaca/Discord licensing for group dissemination. A security checkbox or compliance toggle is not substitute for actual authorization or legal advice.

### F. Continuity and operations audit

Power cycle Pi; disconnect internet; simulate stale feed, Supabase free pause, GitHub Actions scheduler miss, registry unavailable, signing key rotate, version mismatch, DB nearing quota; measure alert response and status labels. Confirm no periodic manual laptop or SSH install step.

## Deliverable format

Executive summary first: working user journeys, blocked releases, quantified unresolved risk, resource cost, critical defects with links and reproducibility, V2 proposals ranked by expected impact/effort.

A comprehensive matrix must have columns: `Feature | code paths | source version | test command/result | screenshot/evidence | correctness | security/privacy | cost/legal | severity | fix recommendation | risk if omitted | PR owner`.

Every proposed patch must include (1) why, (2) what changes, (3) exact modules/contracts, (4) realistic fixture, (5) acceptance gate, (6) rollback path, (7) alternative considered.

## Definition of completion

Audit complete only when an independent reviewer can reproduce the major user flows and can clearly distinguish DEMO, paper-live and compliant production possibilities. No unexplained claim “fully deployed” based on a PR description.