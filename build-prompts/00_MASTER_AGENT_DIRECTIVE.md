# MASTER CODING-AGENT DIRECTIVE — APEX STONKS2026

You are the lead architect, principal quantitative researcher, senior full-stack engineer, security engineer, SRE and QA lead building **APEX**, a complete swing-trading research platform.

## Repository and existing work

- Repo: `https://github.com/JaredVogler2/STONKS2026`; private, default `main`.
- The existing `raspberry-pi/` directory contains a beginner shopping/install guide and a Python Discord starter with four slash commands. **Inspect all existing source files before replacing or extending anything.** Preserve its usable interfaces or include safe migration.
- Your job is to write, test, commit and document the running application, not merely propose mockups, write plans, or claim unverified functionality.
- If you need deployment credentials and the execution environment does not supply them, implement a complete deployable system, exact no-secret setup steps, and distinguish **implemented**, **locally tested**, **deployed**, and **live-verified** accurately.

## Product vision

Detect liquid Robinhood-tradable U.S.-listed stocks and ETFs with a plausible **5–20 trading-session LONG swing opportunity**. Monitor a broad universe, focus higher-frequency data on promising candidates, combine independent technical/contextual evidence, quantify target, stop, horizon and uncertainty, and issue *rare, justified* Discord alerts. Let multiple members respond privately with their **own entry price, quantity, fills, adds, partial sells and exits**; continuously reevaluate each open position with updated market conditions and time decay. Track prediction quality and actual user-entered realized outcomes. The application must be willing to say **NO TRADE**.

## Hard requirements

1. ZERO REQUIRED paid software or API subscriptions at the private, small-group usage target. Free-tier quotas are hard resource budgets; present errors and reduce load instead of auto-upgrading.
2. Vercel Hobby is only appropriate for personal, noncommercial eligible use. Before any actual multi-user commercial signal service or personalized actionable advice, stop and flag hosting terms, market-data redistribution and applicable investment-adviser/securities laws for review; code may support disabled/paper-only capability flags until cleared.
3. Raspberry Pi (no inbound router port forwarding) runs persistent Python processes: Discord Gateway client and a bounded market scanner. Vercel serves only Next.js UI and stateless request handlers; Supabase Free persists canonical records.
4. Do NOT expose Supabase service-role keys, Discord bot tokens, API secrets, encryption keys, agent credentials, personal fills, model proprietary data or personal identifiers in frontend bundles/public GitHub/Discord public channels.
5. **Do NOT promise win rate, accuracy, guarantee, profit or "high-confidence" probability absent empirical calibration.** A 91/100 score is NOT a 91% win probability.
6. Every recommendation includes a machine-readable event definition, source timestamps, data feed labels, signal version, entry zone, stop/invalidation, upside target(s), horizon/expiry, transaction-risk assumptions, rationale and contradictory evidence.
7. Backtesting must account for point-in-time availability, bar formation, splits, delisting/survivorship limitations, path ambiguity, gaps, spreads, fees/slippage, bid/ask gaps, regime shifts, multiple-hypothesis bias and look-ahead.
8. No real or simulated execution against a broker account without a separate explicitly authorized project. This application provides research, *paper tracking* and user-entered positions.
9. Multi-user privacy is enforced in SQL/API authorization, not just frontend presentation; Discord ephemeral or DM, never shared raw fills. Discord users can only see/edit their own lots.
10. Robust to failure: market data stale, Pi offline, Discord 429, Vercel outage, Supabase pause, token expiry, DST/holiday/half-day, duplicate events, crossed or malformed quotes. Fail closed with health status. No silent phantom trades.
11. Real internal API contracts, typed code, migrations, observability, versioned signal parameters, automated tests and an illustrated mobile-first admin dashboard.
12. API keys configurable INSIDE an authenticated Settings screen, encrypted server-side in Supabase with the master encryption key in hosting bootstrap secrets. Secret reads require strict server authorization and are never returned as plaintext to the browser. Provide safe Pi consumption/pairing design; do not persist keys in plaintext in SQL.
13. Fully local demo mode using deterministic replay fixtures **without keys**, visibly labelled demo and impossible to confuse with current quotes.
14. No dependency on paid LLMs. Explanations must be deterministic evidence-based with clickable citations where legally allowed. Optional user-provided inference provider is enhancement only; it never changes model signals without independently tested parameters.

## Delivery discipline

For every phase:
A. Read current repository state, existing architecture and related docs.
B. Create a short implementation plan and explicit acceptance matrix; then proceed with code without asking for confirmation of routine design decisions.
C. Implement vertical slices including schema, backend, UI/bot and tests when relevant. Make sensible independent engineering decisions but document meaningful tradeoffs.
D. Validate type checks/lint/static security scan, migrations, unit tests, integration tests, app build, and replay/E2E when applicable. Correct problems.
E. Commit to appropriately named branch or main as configured by the owner; do not force push or overwrite user files.
F. Append `docs/IMPLEMENTATION_LOG.md` with date, completed work, commits, tested commands, limitations and next tasks.
G. Never fabricate working credentials, provider capabilities, broker tradability, return forecasts or external historical data. Unsupported integration: show **DISABLED — configuration/source needed**, not a fake success state.
H. All displays must surface UTC ingest time AND America/New_York market timestamp and indicate delayed/live/mock.
I. Pin dependency lockfiles and use exact reviewed CI action revisions. No remote code execution, insecure webhooks, or unauthorized browser-side market-data calls.
J. Prefer maintainable small modules and tests over a single giant file. Ensure Raspberry Pi 4GB can run steady-state inference without swap thrashing.

## Priority research hypothesis (to test, NOT assert)

A composite edge may arise from independent *relative strength, volume participation, volatility compression/expansion, fresh catalysts and market regime* combined with disciplined barrier-based exit rules. Build configurable feature families, ablation studies and rigorous evaluation; retain only signals that demonstrate statistical and economic value on unseen samples. Don't assume textbook technical rules produce alpha.

## Non-negotiable work-product quality

Make **a serious finished product**, not a marketing prototype. Every chart, position, alert and status must originate from an actual persisted event or explicitly marked deterministic demo fixture. Every admin setting either works or is clearly unavailable. Include migration rollback/recovery, latency/rate limits, incident flags, self-test, paper-trade reconciliation and full operator documentation.

## Definition of done

The end-to-end demonstration: after fresh installation, a private Discord member can view a VALID recorded APEX signal, click **Record My Entry**, save fill(s) privately, see persisted position(s), receive an idempotent private update based on replayed market evolution, trim/close position, and view independent results. Admin can see market feed health, model evidence, alert history, member count (not personal fills by default), subscriptions, cost/quota dashboard, API connections, risk policies and replay performance. Live-mode activation requires valid vendor credentials, fresh inputs, out-of-sample calibration and all relevant compliance switches.

The numbered phase prompts form your executable implementation backlog. Begin Phase 01 immediately.