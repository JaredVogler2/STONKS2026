# Real-equity execution status (Phase 14 / Issue #6)

**Status (2026-10-21 11:09Z): BLOCKED — ALPACA_ACTIONS_SECRETS_MISSING.** The first real run was dispatched on `main` and
stopped before any download, because the Alpaca secrets are not configured. No real-equity backtest has been completed, and
none is claimed.

## Attempted real runs (every attempt is recorded, including blocked ones)

| Run | Commit | Experiment id | Outcome | Details |
|---|---|---|---|---|
| [39589850123](https://github.com/JaredVogler2/STONKS2026/actions/runs/39589850123) (job 119187791407), 2026-10-21 11:09:25–11:09:47Z | `7ee224c` (main, PR #7 merge) | `2026-10-21-fixed78-sip-8y` (alpaca, 8 years, fixed78) | **BLOCKED_ALPACA_ACTIONS_SECRETS_MISSING**, exit 2 | Minutes checkpoint passed: 480 of 1500 minutes worst case, measured usage unknown. `ALPACA_KEY_ID` and `ALPACA_SECRET_KEY` were empty, so no provider request was made, no data was downloaded, no synthetic fallback ran and no strategy was evaluated. Artifact `apex-realeq-39589850123-1` (id 12341355551, sha256 `ac2b17f1…`, 7 days) holds the seven BLOCKED-status files only. |

## Ready, verified offline

| Item | Evidence |
|---|---|
| Frozen protocol `realeq-protocol-1` | [PROTOCOL.md](PROTOCOL.md). The sha256 is asserted against the code by a test. |
| Research runner `apex research real-equities` | 38 new tests (`tests/test_real_equities.py`, `tests/test_real_equities_workflow.py`). They cover: no-keys BLOCKED with no fallback; wrong feed; auth failure; future bar; incomplete/stale history; dataset counts; chronological split, embargo and label tail; no-look-ahead under truncation; label availability; net return and R at cost; horizon expiry; same-bar ambiguity; gap through stop; dedup; portfolio capacity; dispositions and Holm; output hygiene (no prices, no NaN); DEMO isolation. The full Python suite passes (674 tests). |
| Workflow `apex-real-equities-research.yml` | actionlint clean. Static tests check: manual dispatch on `main` only, `contents: read`, no `id-token`, only the two Alpaca secrets in two steps, no cache, pinned actions, a 7-day artifact of exactly the seven files, and a minutes checkpoint before the fetch. |
| DEMO smoke run (fixture, **not market data**) | Full pipeline with 8 years × 78 symbols through the Alpaca wire-format emulation: 16 requests, about 70 s locally, all seven files written and labelled DEMO, aggregate `NO_TRADE`. Its output is deliberately **not** committed here: synthetic results never enter `research-results/`. |

## Not yet run (blockers)

1. ~~The workflow is not on the default branch yet.~~ Resolved: PR #7 merged at `7ee224c`, and the workflow dispatches from `main`.
2. **The Alpaca secrets are not configured.** Run 39589850123 confirmed this: both values were empty in the `real-equities-research` job.
   - The coding agent cannot read repository secrets and has no Alpaca credentials in its container.
   - By policy it did not use, and will not use, any connected-tool (e.g. ChatGPT Alpaca connector) credential.
   - Without the secrets the run stops with **BLOCKED — ALPACA_ACTIONS_SECRETS_MISSING**, as run 39589850123 did. It runs no synthetic fallback, and its execution log and job summary say so.
   - GitHub normally creates a referenced environment automatically on first use, with no protection rules. Check that
     `real-equities-research` is restricted to `main` before you add the secrets.
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
