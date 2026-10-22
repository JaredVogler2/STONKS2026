# Real-equity execution status (Phase 14 / Issue #6)

**Status (2026-10-21 23:57Z): FIRST REAL EXPERIMENT COMPLETED. The aggregate result is NO_TRADE.**

- Results: [`2026-10-21-fixed78-sip-8y-r2/`](2026-10-21-fixed78-sip-8y-r2/), with reviewer notes in [REVIEW.md](2026-10-21-fixed78-sip-8y-r2/REVIEW.md).
- No hypothesis is validated. Live alerts, promotion and orders stay disabled.
- **Erratum (2026-10-22):** [ERRATUM.md](2026-10-21-fixed78-sip-8y-r2/ERRATUM.md), from the [Quant V2 M0 audit](../../docs/quant-v2/M0_AUDIT.md). The published files are unchanged; NO_TRADE stands.

## Protocol-2 (Quant V2, [Issue #12](https://github.com/JaredVogler2/STONKS2026/issues/12))

**Status (2026-10-22): preflight amendment A1 issued** ([PROTOCOL_V2.md §12](PROTOCOL_V2.md#12-amendment-a1-2026-10-22-pre-evaluation)).
- The official run, `qv2-20261022-s1-s5-u1-presample`, follows once A1 is merged to `main`.
- The cancelled A0 run below is sealed: complete, but neither read nor published as a result.

| Run | Commit | Experiment id | Outcome | Details |
|---|---|---|---|---|
| [39829472789](https://github.com/JaredVogler2/STONKS2026/actions/runs/39829472789), 2026-10-22 18:13:42Z start, research step 18:13:52–18:17:29Z | `cb1f494` (main, PR #13 merge; protocol A0 `a15b4e92…4c9f`) | `2026-10-22-u1-sp500-sip-p2` (alpaca, u1-sp500, `realeq-protocol-2`) | **CANCELLED BY THE AGENT; OUTPUTS SEALED.** Not a published result. | <ul><li>**Why cancelled:** the pre-run review of PR #13 found defects that were later fixed in A1.</li><li>**What it holds:** the study step finished before the cancel landed. Artifact `apex-realeq-39829472789-1` (id 12449060139, zip sha256 `380eb7e927dcb61e6e96dc832173288a8746be7bc9d7fd3b55df675eb2e9fa23`, expires 2026-10-29) contains all seven files with A0 PRESAMPLE outcomes.</li><li>**What the agent saw:** only the file names, sizes and hash. It opened no file, log or job summary.</li><li>**What happens next:** it is opened once, after the A1 results are published, only as a reproducibility check.</li></ul> |

## Protocol-1 attempted real runs (every attempt is recorded, including blocked ones)

| Run | Commit | Experiment id | Outcome | Details |
|---|---|---|---|---|
| [39757487973](https://github.com/JaredVogler2/STONKS2026/actions/runs/39757487973), 2026-10-22 09:42:25–09:43:25Z | `8146a79` (main, PR #11 merge; research code and workflow identical to `2acad29`) | protocol-1 replay of r2 (alpaca, `years=8`, `time_end=2026-10-21`, fixed78) | **DIAGNOSTIC REPLAY**, not a new experiment and not published as one | 16 SIP requests. Every metric in `strategy_metrics.json` identical to r2 and `trades_summary.csv` byte-identical, but `dataset_hash` and `history_hash` differ (vendor bar values changed after the r2 fetch). Recorded in [M0_AUDIT.md §2](../../docs/quant-v2/M0_AUDIT.md) and erratum E1. Artifact id 12415284265, zip sha256 `933c7afd…5bbe`. |
| [39696547618](https://github.com/JaredVogler2/STONKS2026/actions/runs/39696547618) (job 119555196116), 2026-10-21 23:55:54–23:57:22Z | `2acad29` (main, PR #8 merge) | `2026-10-21-fixed78-sip-8y-r2` (alpaca, 8 years, fixed78) | **COMPLETED**: aggregate NO_TRADE | 16 SIP requests, 78/78 symbols usable, 156,773 raw rows, 0 invalid. Holdout 2025-05-02 → 2026-09-21. Dispositions: H8 FAILED_REAL_OOS; H5 UNVALIDATED; H1/H2/H4/H6 INSUFFICIENT_HISTORY; H3/H7 NOT_EVALUABLE_MISSING_INPUT. Artifact id 12390019748, sha256 `270c8895…`. |
| [39589850123](https://github.com/JaredVogler2/STONKS2026/actions/runs/39589850123) (job 119187791407), 2026-10-21 11:09:25–11:09:47Z | `7ee224c` (main, PR #7 merge) | `2026-10-21-fixed78-sip-8y` (alpaca, 8 years, fixed78) | **BLOCKED_ALPACA_ACTIONS_SECRETS_MISSING**, exit 2 | Minutes checkpoint passed: 480 of 1500 minutes worst case, measured usage unknown. `ALPACA_KEY_ID` and `ALPACA_SECRET_KEY` were empty, so no provider request was made, no data was downloaded, no synthetic fallback ran and no strategy was evaluated. Artifact `apex-realeq-39589850123-1` (id 12341355551, sha256 `ac2b17f1…`, 7 days) holds the seven BLOCKED-status files only. |

## Ready, verified offline

| Item | Evidence |
|---|---|
| Frozen protocol `realeq-protocol-1` | [PROTOCOL.md](PROTOCOL.md). The sha256 is asserted against the code by a test. |
| Research runner `apex research real-equities` | 38 new tests (`tests/test_real_equities.py`, `tests/test_real_equities_workflow.py`). They cover: no-keys BLOCKED with no fallback; wrong feed; auth failure; future bar; incomplete/stale history; dataset counts; chronological split, embargo and label tail; no-look-ahead under truncation; label availability; net return and R at cost; horizon expiry; same-bar ambiguity; gap through stop; dedup; portfolio capacity; dispositions and Holm; output hygiene (no prices, no NaN); DEMO isolation. The full Python suite passes (674 tests). |
| Workflow `apex-real-equities-research.yml` | actionlint clean. Static tests check: manual dispatch on `main` only, `contents: read`, no `id-token`, only the two Alpaca secrets in two steps, no cache, pinned actions, a 7-day artifact of exactly the seven files, and a minutes checkpoint before the fetch. |
| DEMO smoke run (fixture, **not market data**) | Full pipeline with 8 years × 78 symbols through the Alpaca wire-format emulation: 16 requests, about 70 s locally, all seven files written and labelled DEMO, aggregate `NO_TRADE`. Its output is deliberately **not** committed here: synthetic results never enter `research-results/`. |

## Former blockers (resolved)

1. ~~The workflow is not on the default branch yet.~~ Resolved: PR #7 merged at `7ee224c`, and the workflow dispatches from `main`.
2. ~~The Alpaca secrets are not configured.~~ Resolved: the owner added them, and run 39696547618 completed. Run 39589850123 had stopped BLOCKED with both values empty.
   - The coding agent cannot read repository secrets and has no Alpaca credentials in its container.
   - By policy it did not use, and will not use, any connected-tool (e.g. ChatGPT Alpaca connector) credential.
   - Without the secrets the run stops with **BLOCKED — ALPACA_ACTIONS_SECRETS_MISSING**, as run 39589850123 did. It runs no synthetic fallback, and its execution log and job summary say so.
   - GitHub normally creates a referenced environment automatically on first use, with no protection rules. Check that
     `real-equities-research` is restricted to `main` before you add the secrets.
   - One-time setup: [PROTOCOL.md §8](PROTOCOL.md#8-owner-one-time-setup-github-ui-only-no-laptop-no-pi).

All protocol steps ran in run 39696547618. Data download, data-quality inventory, frozen manifest, H1–H8 and baselines, portfolio, robustness and dispositions are published in the experiment directory above.

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
