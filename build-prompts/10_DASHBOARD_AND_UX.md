# PHASE 10 — World-class private Next.js trading intelligence dashboard

## Mission

Build an elegant, polished, accessible, high-information-density web application worthy of daily financial decision support. It must be REAL and fully connected; no unusable buttons, lorem ipsum, arbitrary dummy probabilities or fake live prices. Mobile/iPhone experience important, desktop dashboard powerful.

## Design direction

- Name: **APEX** — Adaptive Predictive Equity eXchange (or simply APEX Trading Intelligence).
- Sophisticated dark visual theme; charcoal/ink surfaces, very restrained emerald for favorable and muted coral for risk, cool white text, subtle grid lines and proportional typography. Offer light theme optional. Avoid gaudy casino/gamification framing.
- Consistent top masthead with market phase (pre/open/after/closed), ET time, Pi/feeds/DB/Discord health, data as-of and DEMO/LIVE mode.
- Sidebar desktop; bottom/mobile accessible navigation. Fast, low motion by default; reduced-motion support.
- Use shadcn/ui + Tailwind and stable chart libraries; colorblind accessible and textual status labels. Skeleton loaders, visual empty states, help tooltips.
- Every panel states its calculation definition and interval. No inferred profitability from a last price stale by an hour.

## Screens and exact interactions

### 1. Command Center (landing)
Top-row real backend KPIs:
- scanned eligible symbols, % coverage, live IEX subscriptions used / cap, candidate count, published high-quality signals today, open monitored self-reported positions, feed freshness, last successful scan and calibration age.
- "Best Live Candidates" table with symbol, setup, quality score, probability status, R/R, horizon, sector, signal decision, quote source and age, drilldown.
- Market regime ribbon with evidence, sector strength heatmap, sector crowding.
- recent event timeline: published, invalidated, source outage, earnings risk.
- dashboard alert banner showing when credentials unavailable, demo mode or group alerts disabled.

### 2. Signals Explorer
- Filters: LIVE vs DEMO, quality, validated P, market session, status, strategy, time horizon, sector, news catalysts and Robinhood-eligibility verification.
- Full sortable table plus charts with candlesticks, overlays for entry band, stop, targets, projection horizon and event timestamps. Charts use licensed own API historical bars and reference provider; not fake finance embeds.
- Signal details drawer: why selected, counterevidence, ranked factors, correlated evidence warning, raw feature values, freshness and confidence sample/bins, original prediction vs updates, result once closed.
- Compare two signals side-by-side using expected utility and regime context; do not present unvalidated ones as winner.
- Manual admin action: mark watch/hold, suppress/force review (cannot force high confidence without flag as admin override); audit trail.

### 3. Catalyst Intelligence
SEC timeline, filings, open-market insider purchase tags, politician delayed reports, topic clusters and market reaction. Add inline links to original filings. Clear timestamp separation; report no live feed if unsupported.

### 4. Strategy & Model Lab
- Strategy toggles and hypothesis cards. Configuration per strategy with tracked version and backtest results.
- Histories: out-of-sample performance, baseline comparisons, rolling drawdown, parameter robustness, ablations, calibration reliability diagram, performance by market regime, time-to-hit histogram, effect of spread costs.
- Explain Quality vs calibrated probability with an interactive glossary.
- New model cannot go live until owner promotion/review and gates pass. Show all models in experimental mode when weak sample count.

### 5. Personal Position Monitoring
- Owner account view restricted; do not expose another user's private fills by default. Admin can see redacted aggregate user/position counts and notification health unless specifically authorized and audited.
- Open self-reported positions with lots and average cost, realized/unrealized P&L, exposure, distance to stop/target, elapsed trading sessions, remaining upside and independent strategy state.
- Position page: add, partial sell, close, corrections with immutable event history; display user inputs as **self-reported** not broker proof.
- Cross-position risk and correlation charts if owner opted into a hypothetical risk budget.
- Admin support workflow should not allow arbitrary impersonation of a Discord member.

### 6. Settings & Secrets
- Secure integration onboarding wizard with provider info, requirements, links, key entry, Test, Save, Rotate, Disable.
- Field descriptions, save state, tested_at, source and quota; never retrieve/show stored plaintext secret.
- Market rules: universe filters, slot allocation, scan cadence, risk/alerts; validation and recalibration prompts; model release switch.
- Discord channel map, role ping opt-in, per-event routing, no accidental public personal notifications.
- Pi pairing status, clock skew, agent key rotation, host online and version.

### 7. Operations / Audit / Costs
- Pi process heartbeat and version, runtime errors, API quota consumed/remaining, database MB, Discord delivery backlog, dead-letter queue, RLS violations and suspicious HMAC rejects.
- Log table with redacted messages and correlation ID; replayable failed jobs; explicit fail-closed STOP alerts switch.
- Free-tier budget gauge with 70/85/95% warnings; nothing triggers a paid upgrade automatically.
- Search audit changes, configuration history, model promotions, emitted trade alerts and superseded forecasts.

### 8. Guided onboarding
- Friendly eight-step interactive setup: Pi assembled, Discord bot invited, Vercel protected, Supabase connected, Pi paired, Alpaca connected, data health pass, private demo replay pass.
- Each step: exact link to existing `raspberry-pi` documentation and a working automated connection test where possible.
- Warn that market signals cannot be live until empirical and compliance gates cleared.

## Interaction design and functional expectations

- Only owner sees authenticated dashboard; Discord members don't require Vercel project accounts.
- All meaningful buttons invoke working APIs and respond with success/failure; no inert dummy interactivity.
- Server components or SWR/TanStack Query cache with data-age badges and polling tuned for free-tier limits, not uncontrolled 1-sec refresh.
- Filtering/pagination server-backed; large tables virtualized if necessary.
- Charts never use future bars, and absolute/user-entered financial data is restricted.
- Every major error state includes "what to do next": connect Alpaca, wait for ingest, inspect model, Pi offline etc.
- A responsive print/export trade report in CSV + privacy-safe screenshot mode with sensitive values hidden.

## Demo/testing

- Screenshot tests of iPhone 390px and desktop 1440px; accessible keyboard traversal; semantic labels/screen reader.
- Features with user-set slider, toggle or table filter must persist and affect actual displayed data if advertised.
- Simulate a new user entering demo; UI updates to exact persisted position, not duplicate dummy chart.
- Verify no secrets in Next.js frontend output or network waterfall.

## Acceptance

App looks and functions like a finished product in DEMO after fresh install. Real mode shows true quotes and honest source ages when credentials available; missing integrations yield graceful states. Complete mobile walkthrough recorded in docs.