# APEX — Versioned Amendment and Independent Audit Program

**Prepared:** 2026-10-20. **Target:** STONKS2026 PR #1, snapshot head `75679cbc5dfbf51bddc72487648a22e3bcbcd092` (active agent branch at review time).  
**Status:** PROPOSED and CODE-REVIEW DRIVEN; not deployed, merged, or represented as a verified production incident.

## Mission and operating principles

Capture high-value research improvements, real implementation risks, reproducible tests, fixes, acceptance criteria, and roadmap changes without disrupting the coding agent building APEX v1. The end goal remains a genuine, private, selectively alerting 5–20-trading-session swing research product with Discord multi-user entries, secure Vercel management, Supabase persistence, Raspberry Pi continuous monitoring, GitHub Actions unattended retraining, and no compulsory paid software subscriptions at small scale.

**Do not restart PR #1, overwrite uncommitted work, force-push, change release flags, or attempt to make synthetic results profitable through trial-and-error.** The work in this folder is a post-build audit and V2 proposal; immediately escalate only reproducible release blockers.

## Prioritization

| Class | Definition | Action |
|---|---|---|
| P0 release blocker | Leaked fills/secrets, unvalidated score presented as reliable probability, noncausal backtest, training-serving mismatch in published model, stale/fake real-time quotes, unreviewed group actionable advice, untrusted model code, unprotected production routes | Repair before live release; coordinate without destroying in-flight work |
| P1 first V2 iteration | Quant methodological uncertainties, incomplete H7 diagnostics, unreliable retraining, weak replay/CI coverage, incorrect fill-state transitions | Write regression test and implement just after v1 is functionally complete unless it becomes P0 |
| P2 improvement | UX, visual polish, extra feature research, operational optimizations | Post-launch paper-mode refinement |
| IDEA | Unproven predictive hypothesis or extra feature | Research register, hypothesis and falsification plan before development |

## Reading and implementation order

1. [00_EVIDENCE_REGISTER.md](00_EVIDENCE_REGISTER.md) — exact observed source issues and verification.
2. [01_CRITICAL_PRE_RELEASE.md](01_CRITICAL_PRE_RELEASE.md) — release-stopping conditions and narrowly scoped fixes.
3. [02_POST_BUILD_AUDIT.md](02_POST_BUILD_AUDIT.md) — systematic architecture, security, quantitative and product audit.
4. [03_QUANT_RESEARCH_V2.md](03_QUANT_RESEARCH_V2.md) — robust point-in-time research methods and experiment program.
5. [04_ARCHITECTURE_AND_UX_V2.md](04_ARCHITECTURE_AND_UX_V2.md) — dashboard interactions, typed contracts, source-of-truth designs.
6. [05_SECURITY_DATA_RIGHTS_COMPLIANCE.md](05_SECURITY_DATA_RIGHTS_COMPLIANCE.md) — private, multi-user data and legal/redistribution controls.
7. [06_AUTONOMY_RELIABILITY_AND_COST.md](06_AUTONOMY_RELIABILITY_AND_COST.md) — GitHub cloud training, Pi sync, Vercel, quotas, backups.
8. [07_DISCORD_MEMBER_POSITION_V2.md](07_DISCORD_MEMBER_POSITION_V2.md) — position-specific Discord UX, ledger and exit/add rules.
9. [08_TEST_PLAN_ACCEPTANCE_MATRIX.md](08_TEST_PLAN_ACCEPTANCE_MATRIX.md) — exact validation suite, success criteria and artifacts.
10. [09_CODING_AGENT_HANDOFF.md](09_CODING_AGENT_HANDOFF.md) — copy-ready agent instructions respecting ongoing development.
11. [CHANGELOG.md](CHANGELOG.md) — amendment decisions and status.

## Mandatory evidence and patch workflow

- FIRST check latest PR, source and actual CI. This snapshot is not immutable truth.
- Record `Finding-ID / class / source path and SHA / reproduction / expected vs actual / financial or security impact / severity / fix / regression test / status`.
- Confirm a failure before treating it as a bug. Preserve good implementations; close items with proof rather than rebuilding duplicate components.
- For patches: root cause, minimal code change, executable regression, code review, actual test logs, metrics, rollback and post-release follow-up.
- New quantitative research must use genuinely unseen future data. Retuning against the inspected holdout invalidates it as a pristine test set.
- Observed “NO TRADE” on synthetic market is a valid conservative outcome, NOT a reason to weaken acceptance gates.
- Vercel Hobby, Alpaca Basic IEX and group market-data dissemination remain subject to provider limitations, licensing, and applicable financial-adviser rules. Document unresolved blockers, don't bypass with settings toggles.

## Preserve existing sources

`build-prompts/00_MASTER_AGENT_DIRECTIVE.md`, `build-prompts/ARCHITECTURE_AND_CONTRACTS.md`, `build-prompts/AMENDMENT_2026-10-20_AUTONOMOUS_CLOUD_TRAINING.md` and `raspberry-pi/README.md` remain foundational. APEX PR #1 continues separately.

**Nothing here authorizes automatic broker execution or publishing personally tailored buy/sell signals to external users without required permissions.**