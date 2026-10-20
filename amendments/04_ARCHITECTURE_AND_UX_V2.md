# Amendment 04 — Architecture and Interactive Product UX V2

## Core goal

Make APEX a coherent, reliable, usable production-quality system. Distinguish beautiful interaction from real operating correctness, while retaining Raspberry Pi scanner + Discord Gateway + protected Vercel UI + Supabase canonical persistence + GitHub-hosted model training.

## 1. Existing structure to inspect, not replace blindly

`apps/pi-worker`: data source adapters, features, strategies, model registry, portfolio assessments, Discord bot and Pi operational services.  
`apps/web`: Next.js admin, settings, signed agent endpoints, Supabase access, charts and training dispatch.  
`packages/contracts`: generated Python/TypeScript schemas.  
`supabase/migrations`: domain tables, RLS, outbox.  
`raspberry-pi`: beginner physical/setup guide and original starter bot migration.  
`build-prompts`: existing charter and 2026 autonomous training amendment.

Use current module names after inspecting latest PR; descriptions here are architectural intentions, not guarantees.

## 2. Principle: one canonical domain model, many user experiences

Architecture decision requirements:
- Discord is an interaction client, not owner of position truth. The Pi can spool temporarily but Supabase transactional ledger is canonical.
- Vercel APIs are bounded stateless transactions; not WebSocket workers. Persistent Pi processes handle streaming and rate budgets.
- Quote cache is per symbol/feed/time and shared across members; one quote source update fans out into N independent assessments.
- Inference and backtest use exactly compatible normalized features and model semantics, with source and timestamp flags.
- Signal forecasts are immutable publications with subsequent revision events; do not quietly rewrite previous predictions.
- All lifecycle/outbox writes idempotent, actor authorized, traceable and auditable.
- The browser never receives secrets/private cross-user fills. Owner aggregate telemetry may be visible but personally sensitive content requires explicit audit scope.

## 3. Next.js command center information architecture

### Homepage — Market and agent status
Above-the-fold market phase (ET calendar), current as-of feed/source, scanner last-good, API remaining, priority IEX slots used, eligible universe scanned, model champion + calibration age, NO TRADE/alerts and Discord delivery health. Distinguish `APP ONLINE` from `MARKET DATA FRESH` and `MODEL VALIDATED`. Never one misleading green status.

### Signal board
Sorted candidates and published signal history, with quality rank, prediction status, source, entry zone, stop, target, max trading-session hold, uncertainty and negative evidence. Filtering by strategy, regime, sector, time and validity. A signal card expands into candlesticks plus bands and explainability (feature numeric values, source evidence, disconfirming evidence). DEMO badges impossible to hide.

### Quant Research Lab
Each H1–H8 card shows: enabled status, generated → eligible → filled → OOS counts, actual model status, holdout period, net expectancy after friction, baselines, leakage/survivorship warnings, calibration plot, uncertainty, model version. H7 zero events gets explicit NO_EVENTS, not invisible disappearance. Training-control panel describes GitHub Actions last successful run, failed/skipped run, quota, champion/challenger, active Pi model, rollback.

### Catalyst Intelligence
SEC/insider/congress/news timeline distinguishes occurred_at / publicly_disclosed_at / observed_at. Direct source links; clearly label stale politician disclosures. Drill down into materiality, issuer disambiguation and price already reacted.

### Member Positions
Admin can review only authorized/personal data; other users' fills not visible by default. Position view presents user's lots, weighted cost, realized/unrealized (estimated price source and age), distance to stop/target, expiry and materially changed thesis. Manual entry/update/partial sell/close and audit history all persisted.

### Settings and System
Encrypted API keys with Test/Save/Rotate/Disable, no plaintext re-display; Pi pairing/revocation, Discord channel routing, member notification preferences, compliance release gates, resource budgets, backups and restore steps. Dangerous settings require explanation and confirmation. All actions must have working callbacks, errors and durable state.

## 4. Full product walkthrough as E2E contract

A. Initial visit with no keys → sign in to protected UI and see honest DEMO onboarding with eight steps.  
B. Preview a fixture scan and candidate; see feature score and reject/qualify reason.  
C. Examine signal card and navigate published Discord example.  
D. User A and B each record their *own* fill on mobile using button/modal; private confirmation; state persists after reload and Pi restart.  
E. Three price paths: rally target, fake breakout through stop, sideways max-hold expiry. Independently update each user (different fills).  
F. User adds/partially exits; correct cost and P&L, target touched ≠ guaranteed order fill.  
G. News/market regime changes the thesis; original publication retained with revision reason and timestamp.  
H. Train model in GitHub Actions, sign release, Pi auto-switches or rejects, dashboard shows version and redacted logs.

## 5. UX interaction quality

- Responsive 390/430px phone and desktop; focus/keyboard/screenreader support, 44px taps, no overflow from dense data, charts zoomable with accessible tabular alternatives.
- No inert buttons. Show precise disabled reason if lacking API, permission or history.
- Real charts and event prices only from durable entities; simulated fixture explicitly marked. Avoid mixing chart source feeds without label.
- Signal cards communicate action in English while not implying certainty: why now, expected horizon, what invalidates thesis, contrary evidence, what changed.
- Portfolio member privacy controls and Discord DM preference onboarding.
- “Confidence” explanation tooltip distinguishes quality rank, empirical calibration, source trust, prediction uncertainty and actual performance.
- Badges only for material status: UNVALIDATED, NO TRADE, FEED STALE, HIGH CONVICTION (when actually qualified), DEMO; avoid casino-style gamification.
- Quick feedback button for false/duplicate notifications and broken source links; structured issue rather than freeform notes leaking private fills.
- Track page load, screenshot-based visual regressions and interaction error rates, no invasive third-party analytics by default.

## 6. Data and service contracts to validate

- `MarketObservation` has provider, IEX/SIP/fixture, quote/exchange ts, received ts, bar-final vs provisional, quality flags and staleness.
- `TradeSignal` includes ID, immutable forecast version, explicit target-before-stop-within-H event, probability status, sources, costs, levels, as-of, expiry.
- `PositionLot` + `PositionEvent` immutable FIFO events; member identity from trusted Discord actor and app auth.
- `NotificationIntent` unique idempotency key, recipient route, delivery status, backoff and privacy.
- `ModelManifest` signed digest, exact feature schema hash, allowed source/mode, calibration expiry, rollback parent; no pickle.
- Cross-service contract drift tests in TypeScript/Python plus Postgres migrations.

## 7. Modernization sequence

First audit. Address P0 risks; implement shared data contracts; build complete vertical flows; only then improve animation/layout/performance. Use feature flags + reversible migrations for changes to notifications and models. Always measure resource cost vs free plan.

## Definition of done

A brand-new owner can configure from browser/phone, run DEMO without keys, eventually pair Pi with one-time secure setup, and let users self-report private positions and receive appropriate alerts without a laptop. A real UI and a real data path; no fabricated signal and no cross-user leak.