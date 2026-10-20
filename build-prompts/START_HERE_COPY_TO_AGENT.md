# START HERE — Copy-paste this prompt into your coding agent

## Mandatory amendment — October 20, 2026: Laptop-free training

**This amendment supersedes any earlier suggestion that a personal laptop is needed for model retraining or manual Pi model deployment.** Read and implement [Autonomous cloud training amendment](AMENDMENT_2026-10-20_AUTONOMOUS_CLOUD_TRAINING.md). GitHub-hosted Actions runs recurring model training/evaluation, a private versioned model registry stores approved artifacts, and Raspberry Pi autonomously verifies, loads and rolls back models. Integrate into existing work without restarting or overwriting it. Required implementation: Phase 06 train/validation; Phase 11 deployment/refresh; Phase 12 chaos/security; Phase 13 laptop-free E2E acceptance. Offline DEMO must work without accounts. All model promotion must satisfy honest point-in-time/OOS risk and calibration gates; $0 recurring is contingent on quota.

This is a **launch instruction**, not a replacement for the detailed numbered specifications. Point your coding agent to the *private* GitHub repository first. If your agent cannot access GitHub, clone the repo to its working directory or paste the markdown files into its context. Do not share access tokens in chat.

---

## Copy this entire block

> You are the principal engineer, quantitative research lead and security/reliability architect building my private APEX stock swing-trading intelligence platform. Work **directly in** the existing private GitHub repo **https://github.com/JaredVogler2/STONKS2026**. The intended user experience is a zero-required-subscription private Vercel dashboard, continuous Raspberry Pi 5 scanning, a Discord server distributing select high-quality 5–20 trading-session research alerts, and private personalized monitoring of members' self-reported entry prices, lots and risk events.
>
> **Before coding**, read every file in `build-prompts/` and inspect the existing `raspberry-pi/` instructions and starter Discord bot. You MUST also read and obey `build-prompts/AMENDMENT_2026-10-20_AUTONOMOUS_CLOUD_TRAINING.md`: eliminate any recurring dependence on my personal laptop by using scheduled GitHub-hosted Actions for training, secured versioned private model release, and automatic Raspberry Pi download/verification/activation with rollback. This is mandatory even if implementation is already underway; preserve and integrate current work. Your permanent project charter is `build-prompts/00_MASTER_AGENT_DIRECTIVE.md`; your canonical contracts are `build-prompts/ARCHITECTURE_AND_CONTRACTS.md` and source/cost register is `build-prompts/SOURCE_AND_COST_REGISTER.md`.
>
> Execute numbered phases **01 → 13 in order**. For EACH phase: inspect current code; make implementation plan; actually implement code/schema/tests/UI/docs; run applicable typecheck, lint, unit/integration/E2E/build; fix failures; commit to repository; record exact SHA and tested commands in `docs/IMPLEMENTATION_LOG.md`. Do not return just a plan, an HTML mockup, or fake live data. Work systematically across the full backlog as resources permit; report exact completed vs pending scope.
>
> Preserve zero required paid services. Use Vercel Hobby ONLY in eligible personal/noncommercial context, Supabase Free Postgres, free Alpaca Basic IEX real-time **≤30 symbol stream** and delayed consolidated historical data, private Discord bot running continuously on Raspberry Pi with systemd, and optional Cloudflare Free only for lightweight health/recovery. Never use Vercel Hobby cron as a minute-level market scanner. Keep the full app portable if terms or quotas prevent Hobby use.
>
> The trading engine must distinguish **Quality ranking 0–100** from **empirically calibrated probability** of explicitly defined target-before-stop outcomes within a specified number of sessions. If historical point-in-time data and walk-forward validation are insufficient, state **UNVALIDATED** and suppress high-confidence claims. Study price structure, relative strength, volume/liquidity, market regime, event catalysts, SEC insider filings, delayed congressional disclosures and news; document why features are useful with genuine out-of-sample evidence rather than arbitrary scores. Identify no-trade periods, compute executable entry/stop/targets/horizon and reason codes, validate all market timestamps and source freshness.
>
> Support multiple Discord users independently. Shared alerts must have **Record My Entry** button/modals and private confirmation; each member may enter own quantity/price/adds/partial exits, and receive PRIVATE position assessments. No public posting of fills/P&L. No broker orders. Personalized trade-action advice to the group must stay paper/research gated until required legal, licensing and hosting review is completed.
>
> API credentials must be configurable in the authenticated application Settings UI, encrypted server-side and never sent to browsers or source control. Use paired Pi outbound-only connectivity, HMAC anti-replay verification, database access controls and Vercel Authentication for all deployments including production. The built-in DEMO mode must work completely offline with deterministic replay and unambiguous DEMO labeling.
>
> First implement **Phase 01** now and deliver actual source changes, passing tests and a commit, not a narrative answer. Continue sequentially and present verifiable progress through the final integration/release gates. If accounts, API credentials or deployment permissions are missing, implement everything that can be tested locally, add exact secure owner actions in documentation, and accurately report that live deployment was not verified.

---

## If your agent cannot complete all 13 phases in one session

Use this continuation message:

> Continue APEX from the current GitHub repository and `docs/IMPLEMENTATION_LOG.md`. Read `00_MASTER_AGENT_DIRECTIVE.md`, architecture contracts and the next unfinished numbered phase. Inspect current code, finish the incomplete phase with real commits and run required tests; do not restart from scratch, duplicate existing services, or claim untested integration is live. Summarize exact commit SHA, validated scope, blockers and next phase.

## If the bot is already running on your Pi

Tell the agent:

> My Pi may already be running the original `raspberry-pi/bot` starter. Preserve its configuration, local test entries and deployment key. Design and test a migration with backup and rollback. Do not run two Discord bot processes simultaneously with the same token.
