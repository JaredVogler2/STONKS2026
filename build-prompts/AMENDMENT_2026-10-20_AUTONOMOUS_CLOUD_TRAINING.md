# MANDATORY AMENDMENT — 2026-10-20 — Laptop-free, autonomous cloud retraining & model distribution

**Status:** REQUIRED. Applies to all current and future APEX implementation phases; takes precedence over older references to training on a personal laptop.  
**Repository:** https://github.com/JaredVogler2/STONKS2026  
**Owner requirement:** **No personal laptop/computer is required for recurring model retraining, validation, tuning, promotion or distribution to the Raspberry Pi.** One-time account setup, secrets and permissions may require browser interaction; all normal operation thereafter is automatic.

## Instructions to a coding agent ALREADY in progress (read this first)

1. **Integrate without restarting or discarding existing work.** Read current repository commits, working tree, implementation log and open PRs; do not force-reset, force-push or overwrite pending work. If working on another branch, **fetch this amendment and merge/rebase/cherry-pick as appropriate** before completing the currently active phase. For uncommitted work, preserve it before integrating.
2. This is a **scope change**, not a request to stop current coding. Preserve all previously required trading, research, security, Vercel, Discord, and Raspberry Pi features. Add the cloud ML delivery pipeline into the existing work breakdown, especially phases 06, 11, 12 and 13.
3. Replace any requirement resembling “train heavier models on a personal laptop and manually copy them to the Pi” with **scheduled GitHub-hosted training runners + automated, validated model release + Raspberry Pi autonomous refresh**.
4. Update §docs/IMPLEMENTATION_LOG.md§ and architecture decision record with the amendment, migration impact, test evidence, GitHub Actions minutes estimate, accepted limitations and exact deployed state. Do not falsely claim Actions jobs ran if credentials or workflows are not yet set up.
5. The Pi remains a reliable low-power **inference and market-monitoring** appliance; it should not need a local desktop or manual model copying to keep intelligence current.

## Target pipeline (fully unattended after initial setup)

~~~text
[Pi continuous scanner] ─────► [Current price/events/features]
          │
          └───────────────► [Compact feature/label observations]
                                        │
                 [Official historical data API + curated labels]
                                        │
                                        ▼
             [GitHub Actions: WEEKLY scheduled train job]
                         (standard hosted Linux runner)
                                        │
                                        ▼
             [Purged chronological walk-forward validation]
               [Cost/slippage, OOS, baseline, calibration]
                                        │
                      ┌─────────────────┴────────────────┐
                      │ FAIL/NO IMPROVEMENT              │ PASS
                      ▼                                  ▼
         [Retain incumbent champion]         [Versioned release candidate]
         [Notify admin: reasons]             [Promotion policy checks]
                                                          │
                                             [Private signed model registry]
                                                          │
                                                          ▼
                                           [Pi outbound periodic fetch]
                                                          │
                                             [Verify and warm model]
                                                          │
                                            ┌─────────────┴────────────┐
                                            │ FAIL                    │ PASS
                                            ▼                         ▼
                                   [Keep former version]      [Atomic activation]
                                   [Log & notify admin]       [Ack version/status]
                                                                   │
                                                                   ▼
                                                    [APEX uses updated model]
~~~

A second lightweight DAILY or WEEKDAY GitHub Actions workflow updates calibration/drift reports only **if quota budget supports it**. When capacity is scarce, evaluate drift on the Pi with small incremental calculations and reserve hosted minutes for weekly heavyweight work.

## Concrete required GitHub Actions workflows

Create, test, and document:

- §.github/workflows/apex-model-train-weekly.yml§ — §schedule§ e.g. Monday 19:17 §America/New_York§, plus §workflow_dispatch§; choose a non-top-of-hour minute; skip market holidays as appropriate; enforce §concurrency§ with cancellation controls, maximum job duration, least privilege, verified branch and non-overlapping production promotions. Use standard hosted §ubuntu§ runner, not costly large/macOS runners.
- §.github/workflows/apex-model-drift.yml§ — weekday 20:23 §America/New_York§, configurable lightweight monitoring; timeout and fail-safe quota logic; §workflow_dispatch§.
- §.github/workflows/apex-model-release.yml§ — validated promote or rollback (manual, or automatically via strong preapproved policy). Production secrets only on protected trusted branch/environments, NEVER in workflows that execute untrusted PR code.
- If separate workflow is not justified, train/release can be one staged pipeline with a protected promotion job; document the chosen arrangement.
- Make all workflows observable from owner Vercel dashboard: last scheduled/actual run, status, model version, validation score, failure, next expected execution, minutes consumed (if GitHub API reports them) and quota estimate. Mark unobservable fields explicitly UNKNOWN.
- GitHub schedules execute from the **default branch** and may be delayed or skipped. Add a missed-run detector on the Pi or independent service that alerts after a missed SLA; GitHub cron alone does NOT guarantee reliable scheduling. GitHub supports §schedule.timezone§ with IANA name; cite official docs.

## Data and scientific integrity

1. Use only **publicly available at-the-time** price, fundamentals, filings, and labels; never construct training labels with unseen future events.
2. Training runners download permitted historical bars from a configured no-cost API, plus immutable, sanitized/correctly permissioned feature/label snapshots. Never assume GitHub has direct access to Pi local Parquet; choose a reproducible fetch plan with API constraints and checksums.
3. Avoid copying a huge raw dataset into GitHub or Supabase. Implement bounded, incremental data pulls and labeled/historical feature archives in an approved private object store if needed; preserve data license and redistribution rules.
4. Use fixed/random seeds, pinned Python versions/dependency hashes, recorded source/label snapshot hash, model feature schema version and code commit SHA.
5. Run genuine purged chronological walk-forward validation with untouched holdout, profit net of friction, calibration/Brier/log loss, regime stability, benchmark comparisons, minimum sample sizes and concentration penalties. Include expected utility and prediction uncertainty, not arbitrary “confidence.”
6. Candidate promotion **must NOT depend solely on training accuracy or a single backtest profit figure**. Define a frozen promotion policy before comparing candidate vs champion and ensure out-of-sample improvement is economically meaningful. Preserve failure reports.
7. Model freshness is not the same as predictiveness: even if training successfully completes weekly, do not pretend predictions are reliable where training data are insufficient or drift is material.
8. Simple incremental updates on Pi may be useful but must not silently mutate the audited production model or break calibration; log and version adaptive state separately.

## Portable private model registry (NO manual file copying)

Implement a persistent **model registry interface**, e.g. a private Supabase Storage bucket + Postgres model metadata, with strict service-side access and free-tier retention budget. Store only compact current champion + 1–2 rollback versions plus metadata; GitHub Actions artifacts are **ephemeral CI evidence**, not the sole production model store.

Model release record:
- model_id, semantic/version UUID, created_at, training window, training data hash, label definition, feature_schema_version, code SHA, evaluation results, calibration parameters, expiry/drift criteria, artifact_size_bytes, artifact_sha256, signature, signer_key_version, storage_uri (private), promotion status, rollback_parent.
- Include signed versioned manifest. A Pi-held trusted public key verifies Ed25519 signature (or equivalent audited signature). Verify checksum, architecture/feature compatibility, size limits, validation metadata, and rollback safety **before activation**.
- **Never load arbitrary Python pickle/joblib model files from remote storage**; they can run arbitrary code. Prefer signed schema-validated JSON coefficients/calibrators for simpler models; ONNX only after verifying Pi ARM64 compatible runtime and safe operator constraints. No remote code download/execution.
- GitHub-hosted runner can upload a validated model through a narrowly scoped owner-controlled API endpoint or restricted release-only credentials stored as **GitHub Actions encrypted Environment secrets**. Prefer short-lived OIDC exchange where a supported integration genuinely exists; do not invent it. Avoid raw Supabase service-role exposure to CI whenever possible.
- Restrict release to reviewed code on protected default branch, pin third-party GitHub Actions to reviewed SHAs, §permissions: contents: read§ by default. Promote only after acceptance tests and source verifications. Never grant arbitrary PR code release credentials.
- Pi checks active model manifest via outbound HTTPS every 30–60 minutes (tunable), fetches privately using its authenticated agent identity, stages on disk, validates and swaps model **atomically** without stopping Discord or scanning; report active vs requested version and source as-of. Keep previous working model for rollback; fail closed if neither approved nor valid.
- If Supabase Free project pauses or registry unavailable, run current approved model only if still fresh/valid under policy; otherwise emit **NO NEW HIGH-CONVICTION SIGNALS**, system warning and recover automatically. Never fall back to fake predicted probabilities.
- Versioned registry enables administrative rollback from Vercel Settings, with an immutable audit entry and Pi acknowledgement. Automatic rollback if post-promotion health check fails.

## Resource governance: $0 recurring subscription goal

- **GitHub Actions in a private repository has plan-wide shared quotas**, including any other projects using the account. As documented in October 2026, GitHub Free standard runners include 2,000 min/month and GitHub Pro 3,000 min/month, plus storage caps. Limits/rules may change; confirm current account details at deployment: https://docs.github.com/en/billing/concepts/product-billing/github-actions and https://docs.github.com/en/billing/reference/product-usage-included
- Per-workflow §timeout-minutes§, runtime checkpoint and configurable MONTHLY TRAINING BUDGET; choose bounded CPU/research search space. Default to weekly training and weekday small diagnostics, not minute-level cloud retraining.
- Important: GitHub's minute quota is shared with coding-agent CI, other repositories and workflows; dashboard must display estimated remaining research budget. If budget low, skip optional drift/heavy experiments; **do not exceed a configured no-spend budget**.
- Document how to enable GitHub spending controls that stop metered usage if budget exhausted. Application-level budget checks are useful but are not an absolute billing safeguard: enforce provider-side spending limits or disable billable usage when unavailable.
- A monthly budget forecast should include max possible runtime (§timeout-minutes§ × expected frequency), not only average measured runtime. Measure actual runs and revise profile accordingly.
- Keep artifacts small, short retention and cleanup; important permanent metadata in database and model registry. GitHub action logs/artifact retention is bounded and is not a long-term model registry.
- If hosted minutes are exhausted, Pi continues scanning and inference with last validated version; alert admin and suppress high-confidence alerts once model validity expires.
- Avoid always-on GitHub-hosted workflows. Continuous price streaming and Discord Gateway run on Pi, not GitHub Actions.

## Live lifecycle and operational controls

- Admin Vercel **Models & Automation** panel: last successful train, daily drift status, champion/challenger comparison, tested promotion gate, remaining budget, Pi active version, reload status, distribution error and rollback control.
- Admin chooses model policy: manual promotion or AUTO with frozen thresholds and audit; **default initial rollout paper-only + manual promote**, enable unattended automatic promotion only after independent backtests, risk review and stable shadow evaluation. Goal after calibration and validation: genuinely unattended.
- Send restricted **Discord #system-status** notices on new model promoted, validation failed, model drift threshold breach, missed retraining, registry unreachable, rollback, quota budget low. Not investment alerts.
- Add a no-secrets “Run Training Now” control in private admin UI only when backend supports controlled GitHub workflow dispatch without handing GitHub tokens to browser; button must enforce admin auth, rate limits and budget policy. If secure dispatch connection isn't configured, disable button with precise instructions, not fake success.
- Document one-time setup of GitHub Actions secrets, restricted environment, Supabase private model storage, Pi pairing, Vercel admin view. Neither daily laptop usage nor SSH manual copying is necessary for ordinary operation.
- Manual override for exceptional maintenance is allowed, not a recurring dependency.

## Verification and release acceptance tests

A. **New clone, no API keys:** offline replay produces training + calibration + model JSON + signed test manifest in a sandbox and demonstrates Pi atomic swap/rollback.
B. **Real GitHub Actions workflow** created; workflow syntax validated and tested by §workflow_dispatch§ if connected, using runner minutes within budget. If no account access/credential, label unverified and list one-time actions.
C. **Full laptop-free round trip:** simulated new historical observations → scheduled GitHub-hosted train → quality gate → private registry version → Pi fetch without SSH/manual copy → new inference on Pi → version ack in dashboard → Discord system notice.
D. **Failed candidate:** incumbent remains; no false automatic promotion. Freshness and alert policy remain correct.
E. **Corrupt/unsigned/incompatible model:** Pi refuses update, previous model intact; emits private alert.
F. **GitHub Actions skipped or quota exhausted:** missed-job detector notifies admin; Pi runs until validity/expiry; no blind promotion or false status.
G. **Pi offline during release:** catches up after reconnect without duplicated event or remote code execution.
H. **Supabase unavailable:** no unrecoverable state; recovery and model checksum validation.
I. **Concurrent training jobs:** dedup/concurrency prevents conflicting champion promotion.
J. **Security:** no workflow PR secret exposure, signing key never in artifact/log, direct unsigned model download rejected, private registry not publicly readable, no unreviewed model format execution.
K. **Resources:** report measured or explicitly modeled runtime, model size, max monthly minutes, Pi CPU/RAM, storage and quota headroom.
L. **No recurring laptop involvement** appears anywhere in the deployment/training runbooks, user manual or smoke test.

## Final acceptance wording

> After one-time setup, APEX autonomously monitors, retrains, tests, calibrates, publishes approved model versions and distributes them to the Raspberry Pi, subject to quantitative gates and free-tier quota availability. If the new model fails validation or infrastructure is down, the previous validated model remains active (until its documented expiry), and the owner receives a precise status alert. **No personal laptop is required for normal ongoing operation.**

## Official reference links

- GitHub Actions schedule, timezone and reliability: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
- GitHub workflow syntax: https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax
- GitHub billing/usage: https://docs.github.com/en/billing/concepts/product-billing/github-actions
- GitHub product free allowances: https://docs.github.com/en/billing/reference/product-usage-included
- GitHub Actions artifact retention (change Oct 1, 2026): https://github.blog/changelog/2026-08-27-actions-retention-will-cover-checks-workflow-runs-and-statuses/

**Implementation priority:** Update phase checklist and architecture NOW; implement training workflow with Phase 06 and auto-distribution with Phase 11, test in Phase 12 and require acceptance in Phase 13. Do not interrupt or discard working code already in progress.
