# Real-equity execution status (Phase 14 / Issue #6)

**Status (2026-10-21): PENDING FIRST REAL RUN. No real-equity backtest has been completed, and none is claimed.**

## Ready, verified offline

| Item | Evidence |
|---|---|
| Frozen protocol `realeq-protocol-1` | [PROTOCOL.md](PROTOCOL.md). The sha256 is asserted against the code by a test. |
| Research runner `apex research real-equities` | 38 new tests (`tests/test_real_equities.py`, `tests/test_real_equities_workflow.py`). They cover: no-keys BLOCKED with no fallback; wrong feed; auth failure; future bar; incomplete/stale history; dataset counts; chronological split, embargo and label tail; no-look-ahead under truncation; label availability; net return and R at cost; horizon expiry; same-bar ambiguity; gap through stop; dedup; portfolio capacity; dispositions and Holm; output hygiene (no prices, no NaN); DEMO isolation. The full Python suite passes (674 tests). |
| Workflow `apex-real-equities-research.yml` | actionlint clean. Static tests check: manual dispatch on `main` only, `contents: read`, no `id-token`, only the two Alpaca secrets in two steps, no cache, pinned actions, a 7-day artifact of exactly the seven files, and a minutes checkpoint before the fetch. |
| DEMO smoke run (fixture, **not market data**) | Full pipeline with 8 years × 78 symbols through the Alpaca wire-format emulation: 16 requests, about 70 s locally, all seven files written and labelled DEMO, aggregate `NO_TRADE`. Its output is deliberately **not** committed here: synthetic results never enter `research-results/`. |

## Not yet run (blockers)

1. **The workflow is not on the default branch yet.** GitHub can only dispatch a `workflow_dispatch` workflow that exists on the default branch. The first real run is possible only after the implementation PR merges.
2. **The Alpaca secrets in the `real-equities-research` environment are unverified.**
   - The coding agent cannot read repository secrets and has no Alpaca credentials in its container.
   - By policy it did not use, and will not use, any connected-tool (e.g. ChatGPT Alpaca connector) credential.
   - If the secrets are absent, the run stops with **BLOCKED — ALPACA_ACTIONS_SECRETS_MISSING**. It runs no synthetic fallback, and its execution log and job summary say so.
   - One-time setup: [PROTOCOL.md §8](PROTOCOL.md#8-owner-one-time-setup-github-ui-only-no-laptop-no-pi).

Steps NOT run: the real SIP download, the real data-quality inventory, the real H1–H8 and baseline evaluation, the portfolio, the dispositions, and publication of a real experiment directory.

## Production drift failure (separate from research)

- **Diagnosis:** the scheduled `apex-model-drift` run 39547226607 (2026-10-21) stopped at *Release policy* with `release API not configured: missing APEX_RELEASE_URL, APEX_RELEASE_AGENT_ID, APEX_RELEASE_HMAC_SECRET`. That is **PRODUCTION_REGISTRY_UNCONFIGURED**: the `model-release` environment has no registry secrets.
- **Remediation in this change:** `apex release preflight` reports that status first, with no network call.
  - Scheduled drift and weekly training then skip the registry steps, with a job-summary notice.
  - Manual runs fail.
  - A partial or malformed configuration fails.
  - HMAC and OIDC authentication are unchanged.
- **Owner fix:** to run production drift and retraining, provision the registry per `docs/AUTONOMOUS_TRAINING.md` §2. Research does not need it.

## When the first real run finishes

The run is triggered from Actions → `apex-real-equities-research` → Run workflow on `main`.

1. Check the job summary.
   - `COMPLETED`: the data-quality and dispositions tables appear.
   - Otherwise it shows the exact blocker (`BLOCKED_…`, `SOURCE_INVALID`, `INSUFFICIENT_HISTORY`).
2. Open a reviewed PR that adds the seven artifact files under `research-results/real-equities/<experiment_id>/`.
3. Link the experiment from [../README.md](../README.md), with the run URL, commit SHA and dataset hash.
4. Replace this status with the outcome. A failed or blocked run is recorded as such and is never re-labelled.
