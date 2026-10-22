# Phase P2 — Upgrade APEX Product and Discord UX to Match Scientific Evidence

## Objective

The owner wants one coherent app, not disconnected scripts: scanner → strategy evaluation → publication → shared Discord insight → personal recorded entry → private risk monitoring → honest exit outcome; all with a visually polished Vercel dashboard. New research methods change signal vocabulary and explainability, but must NOT break the already built app or leak user data.

## 1. Proposed navigation

### Dashboard / System Pulse
Three independent statuses:
- **Service health:** Pi bot connected, last market scan, database, cloud retraining, model sync and Discord delivery.
- **Market data health:** feed source and consolidated vs IEX, last completed session, quote age, entitlement, coverage, rejection/staleness.
- **Research validity:** last completed real-data experiment, current strategy version, validated research edge vs forecast calibration, NO_TRADE, prospective paper-trial counter, precise unresolved gate.

Do not render “system GREEN / confidence 96%” when model has no valid OOS. Onboarding with no credentials displays obvious DEMO dataset and secure setup guidance, never real-looking present quotes.

### Research Lab
Each H1–H8 and S1–S5 row has version, operational status, count funnel, development/holdout dates, independent weeks and regime coverage, mean/median net R, confidence *about identified metric*, gain/loss tail, SPY/exposure-matched net active, cost sensitivity, rejected reasons and actual experiment link/sha.

A strategy can be `CODE_COMPLETE` but `UNVALIDATED_REAL_ECONOMICS`. H3/H7/S4 “missing PIT catalysts” shows NOT_EVALUABLE rather than a 0% success badge. H8 disabled in primary scanner unless independent future evidence. Show how many 229 historical variants were tried and provenance of any chosen “best”.

Add “baseline vs replacement” comparison chart with shared sample period/entry/exit and selection/execute improvement decomposition; no comparison of curves with incomparable capital exposure.

### Signal Detail
Timestamped bar context, causal source and quote freshness, strategy idea and disconfirming evidence, actual entry zone, close-vs-open assumption, economic risk, expiry and policy version. Show:
- `Opportunity percentile` (rank only)
- `Modeled P(target before stop by N sessions)` only when independently calibrated, else “Probability UNVALIDATED”
- `Expected net outcome/R` and downside/reasons if economically verified, else “Exploratory model output — not validated”
- Market regime/sector breadth provenance; confidence about evidence not chance of profit
- Thesis invalidation/stop are not guaranteed execution; ticker eligibility is “likely eligible,” not proof of broker support
- Source rights and group release eligibility; no raw per-user fills.
- Explicit NO TRADE means opportunity did not pass; explain missing input rather than suggesting invest anyway.

### Historical Research and Experiment Explorer
Immutable run index under `research-results`, run URL and code SHA, protocol SHA, source data hash, real/synthetic mode, universe limitations, old protocol-1 and new protocol-2 comparison. Report changing methodology as different run, don't splice equity curves as if same.

### Admin Settings / Operations
Ability to test sources safely, validate credentials server-side, view training minute budget and Pi signed model version, choose paper/shadow/research mode and disable strategy per model status; “Run Experiment” only via authenticated backend→workflow dispatch, with quota and idempotency. No browser GitHub PAT or signing key. If credentials/infra not configured, show disabled and why; button cannot pretend success.

## 2. Discord workflows

- Shared #research-signals: permitted, evidence-based research alerts only with source time, version, time horizon, real/DEMO, no personal amounts. If unvalidated, default NOT an actionable BUY/SELL signal.
- `Why?` private/ephemeral details includes gains, negative evidence, labels, links to source/experiment. Don't treat Quality rank as confidence.
- `Record My Entry` opens modal, authorized Discord actor, private ephemeral receipt, no positions in public threads.
- Self-reported lots and independent risk from ACTUAL user's price/quantity/date, not group entry zone or model p at old entry; late entries require explicit “forecast not transferable” if it isn't recalibrated at new entry.
- Group target-touched/stop-touched event does not imply any member's broker executed; private lot continues until member reports exit.
- DM blocked => private in-app queue, never group fallback. Removed guild member => revoked service interactions and prefs.
- Suppress duplicate alerts with event id/version/idempotency; member notice cooldown and opt-in. Include source state stale/unknown override.

## 3. Concrete front-end and API updates

Source-code discovery targets:
`apps/web/src/app/api/v1/admin/backtests/route.ts`, `apps/web/src/components/client/strategy-toggles.tsx`, signal cards, member position routes, bot slash commands and `packages/contracts`.

Add one typed versioned `ResearchEvidence` JSON schema:
`data_class; source_asof; feed; entitlement; strategy_version; experiment_id; code_sha; protocol_sha; universe_bias_status; sample={trades,distinct_weeks,regimes}; estimated_net_return; marginal_net_excess; uncertainty_method; calibrated_probability_status; model_version; policy_status; unsupported_reasons`.

Backend authorization and RLS tested. Web and Python types generated from shared canonical schema. Dashboard may use stale snapshots but visibly as-of time and data rights. Feature flags allow rollout without migrating active users blindly.

## 4. UX scenarios to implement and test

1. New visitor with no API keys sees DEMO, can explore S1–S5 educational explanation but no real buy levels.
2. Real data loaded, zero validated rules => prominent NO TRADE state and no push of artificial high-conviction alerts.
3. S1 signal with no qualifying buy fill shows “missed/no fill,” not an assumed holding.
4. Model trained but rank/forecast unvalidated => show evidence score as descriptive and suppress probability.
5. Events missing => H3/H7/S4 marked unavailable; admin can inspect why.
6. Member A bought @180 and B @193, group signal original target touched; individualized P&L and stop distance differ; neither automatically “sold”.
7. Offline Pi, stale quote or missing actions release registry => run modes degraded, no fake live green.
8. Mobile 390px with chart + data table keyboard/screenreader alternative; every button genuine or disabled with explanation.
9. User confirms manual trade correction; ledger events immutable and FIFO allocations accurate; owner cannot default-view friend's private fills.
10. New protocol after old result: historic signal event tied to old model; UI doesn't retroactively change old forecast.

## 5. Release and legal controls

Group personalized “Add 20 shares” or “Sell immediately” is potentially investment advice. Free private Discord doesn't automatically remove regulatory or market-data redistribution limitations. Require human owner review of Alpaca data terms, Vercel Hobby eligibility, and applicable adviser obligations prior to `GROUP_ACTIONABLE`; default disabled regardless of strategy backtest pass. No Robinhood credential scraping or broker execution.

## 6. Acceptance

A fully real interactive web+Discord+Pi E2E journey with separately classified operational, scientific and legal gates; no manufactured probability, market data or personal position. Local tests and actual screenshot/browser checks at phone and desktop, validated persisted data, clear and actionable error reasons without leaking secrets.