# Amendment 09 — Coding Agent Handoff and Execution Prompts

Use these prompts at the appropriate point in development. Do not feed the entire folder into an agent as “replace existing code now” while PR #1 is actively under construction. Work incrementally, preserve existing code and attach evidence.

## Prompt A — NOW, while APEX PR #1 is still active

> You are continuing APEX development in `JaredVogler2/STONKS2026`. I created a separate planning branch named `planning/apex-v2-audit-amendments` containing the `amendments/` directory. It is an **independent future audit and improvement backlog**. Do not restart current implementation, overwrite uncommitted work, copy fixes blindly from the planning branch, or merge PR #1 prematurely.
>
> Read `amendments/README.md` and `amendments/00_EVIDENCE_REGISTER.md` after safely fetching the branch or reviewing it on GitHub. Treat all source observations as **potential issues until reproduced**. When current PR work intersects a suspected P0 release blocker, reproduce it in an isolated test, fix with minimal code if confirmed, and report the change. Otherwise continue phases 09–13 as already scheduled.
>
> Of particular concern: `research/dataset.py` includes `squeeze_prev` but `signals/policy.py` live `model_row` currently populates it as `None`; inspect training/inference feature parity. `confidence_block` uses `sqrt(p(1-p)/n)` as an uncertainty band, which may not represent fitted-model uncertainty. H7 is **already implemented**; diagnose missing research report counts, DO NOT implement a duplicate.
>
> Preserve the stricter research results: no synthetic model may be sold as actual stock alpha. Preserve default-disabled group actionable advice and all Vercel/Discord/Pi privacy/authorization gates. Continue GitHub Actions laptop-free retraining/release implementation and finish the complete v1 build.
>
> End progress messages with explicit implementation phase, test command/status, current commit, newly confirmed P0 issues, and release blockers. Do not claim production-ready until deployment, security, real data and end-to-end acceptance evidence exists.

## Prompt B — AFTER PR #1 feature completeness and stabilization

> Perform the full independent APEX architecture, quantitative, security, usability and operational audit documented in `amendments/02_POST_BUILD_AUDIT.md`, using the CURRENT merged/checked-out code and real CI evidence. First capture environment, commit, deployed status and accepted scope. Read `amendments/00_EVIDENCE_REGISTER.md` and all V2 prompts. Confirm or close every finding via reproducible tests. Produce `docs/V2_INDEPENDENT_AUDIT.md` and prioritize P0/P1/P2 separately. Do not prematurely change model thresholds to generate trade alerts.
>
> Inspect every cross-system journey: scanner→candidate→signal→Discord broadcast→member fill/position→private update→close; GitHub-hosted model training→holdout gates→signed private registry→Pi autonomous activation/rollback; protected Vercel Settings/API→Supabase. Verify no user data leaks and model confidence reflects real OOS evidence.
>
> Implement only critical P0 fixes first in a separate, minimal V2 implementation PR with regression tests, reversibility and measured resource effect. Obtain approval or clearly document blocked release if missing data licenses, group personalized investment-advice review, hosting eligibility or runtime credentials.

## Prompt C — QUANTITATIVE RESEARCH V2, only after source/holdout protocol locked

> Execute `amendments/03_QUANT_RESEARCH_V2.md` as a reproducible research program. Do not infer strategy validity from deterministic synthetic tests or optimize parameter search on the known H1 synthetic holdout. Pre-register real historical universe/source/time windows, costs, baseline strategies and number of experiments. Verify point-in-time SEC/news releases, calendar purging, share/split handling and feed provenance. Provide H1–H8 per-stage counts including H7 NO_EVENTS if appropriate; conduct genuine H7 ablation and train/live feature vector parity. Report economic expectancy and calibration uncertainty on unseen samples, including cases with NO TRADE. Place new candidate models behind validation/production gates. Document every failed strategy rather than cherry-picking winners.

## Prompt D — OPERATIONS, DISCORD AND PRODUCT V2

> After safe v1 merge and independent audit, implement approved P1 items from `04_ARCHITECTURE_AND_UX_V2.md`, `05_SECURITY_DATA_RIGHTS_COMPLIANCE.md`, `06_AUTONOMY_RELIABILITY_AND_COST.md` and `07_DISCORD_MEMBER_POSITION_V2.md`. Require actual functional responsive UI, permissioned and idempotent multi-user lots, no public P&L leakage, verified model manifests and autonomous Pi upgrades without laptop, free-tier budgets and honest market-feed labels. Demonstrate adversarial/outage paths using `08_TEST_PLAN_ACCEPTANCE_MATRIX.md`. Keep all release switches gated until actual review.

## Implementation discipline for every V2 patch

1. Confirm base branch and no active uncommitted work before editing.
2. Open issue/finding ID and reproduce on current SHA.
3. Write failing test capturing expected behavior; link it to finding.
4. Make smallest accurate patch; avoid replacing working architecture for style.
5. Run relevant unit/contract/integration/E2E and quant replay tests; show real results.
6. Ensure schema and model version migration/rollback; sign if artifact logic changes.
7. Run security review: no unintended group advice, public fills, leaked keys, unlicensed data or paid dependencies.
8. Update documentation, changelog and `docs/IMPLEMENTATION_LOG.md` with measured impact, minutes/DB footprint and remaining limitations.
9. Create reviewable PR titled `APEX V2: [issue IDs and concise objective]`; don't squash away required audit trace without retaining link.
10. Report what remains BLOCKED by access, external rights or owner approval. Never invent success or pretend the app is live.

## Agent output format

- **Scope:** finding IDs and modules.
- **Before:** exact reproduction and evidence.
- **After:** behavioral change and code diff/commit.
- **Validation:** commands, PASS/FAIL counts, environment, CI links.
- **Security/quant:** confidence and redistribution implications.
- **Operations:** model/version compatibility, rollback, resource delta.
- **Disposition:** closed, blocked, deferred with explicit reason.
- **Next step:** single focused work item.

## Absolute non-goals

No automated Robinhood orders, credentials scraping, paid data forced, bypassing provider access rights, loosening security/holdout criteria to make alerts, publishing private user positions in group channels, or claiming 95% confidence from a 95/100 Quality score.