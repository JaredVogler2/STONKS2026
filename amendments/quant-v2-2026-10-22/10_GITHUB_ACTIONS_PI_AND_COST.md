# Phase P2 — GitHub-hosted Research, Autonomous Pi Integration, Quota and Recovery

## Nonnegotiable operating model

- **Personal laptop not required** for recurring experiments, training, validation, release or Pi updating.
- Standard GitHub Actions hosted Linux runners perform bounded heavy research and retraining. Raspberry Pi maintains continuous market features/bot. Vercel dashboard short functions and authenticated routes; Supabase small canonical state. Never use GitHub-hosted runner as all-day scanner or pi-executed brokerage order engine.
- All experiments must remain within available shared GitHub Actions minutes, Alpaca historical SIP terms and provider rate limits. No automatic unapproved paid upgrade.
- Research-only runners must never require Vercel production registry configuration. Signed champion release requires separate admin-approved deployment infrastructure and gates.

## 1. Extend independent research runner with v2 protocol mode

Existing `.github/workflows/apex-real-equities-research.yml` and `apps/pi-worker/src/apex_worker/research/realeq/runner.py` do actual real SIP history without production release API. Extend with **new** selectable `protocol_id=realeq-protocol-2`, bounded `experiment_id`/`universe_id`, and strict endpoint/feed data settings. `realeq-protocol-1` must remain reproducible and unchanged; no silent default migration. Test old v1 fixture and protocol hash before/after.

CLI should validate:
- real vs DEMO (fail closed on missing credentials, wrong feed, source not permitted; NEVER silent fixture fallback);
- frozen protocol hash and as-of data/available-at time;
- number of stocks, allowed historical PIT status, budget limit/estimated fetch count and bytes, cloud memory budget;
- all candidate arms and preregistered family of statistical tests;
- which experiment segments are permitted for selected stage, and whether fresh evaluation is truly fresh;
- output publication hygiene and GitHub artifact expiry.

Provide `--smoke` mode on synthetic demo no keys, but prevent writing results to real-equities permanent path. Provide `--validate-inputs-only` to show preregistration and predicted resource needs without touching protected data.

## 2. Research workflow deliverable and observed resource controls

Create/update workflow on trusted reviewed `main`:
- `workflow_dispatch` only for heavy v2 real-equity research until baseline is stable, standard runner and approved repository secrets (Alpaca historical keys, no brokerage execution).
- GitHub Actions `contents: read` and `id-token` OFF for pure research; no direct push to main; repo PR for aggregate reports, not vendor raw OHLCV.
- Pin GitHub actions SHA and Python/uv deps; `timeout-minutes`, disk/memory limits, parallel matrix cap, rate-limit/backoff and retry budget, `concurrency` no overlapping same experiment names, no cancel-active experiments without owner consent.
- Record expected/max monthly runner minutes, predicted CPU/time from mock benchmark, API request count and actual measured cost. Stop optional research at resource threshold, preserve useful status+partial safe diagnostics.
- No secrets available to PR code, supply-chain audit of actions. No token in logs/artifacts. Store immutable code SHA, canonical protocol hash, dataset hash, runner image and package lock digest.
- After successful run, emit **seven or expanded approved sanitized documents** via small 7-day ephemeral artifact; create reviewed results PR from trusted code (can be manual agent connector write), append immutable run to index; never write licensed raw prices or private member lots.
- Publish failure reason `BLOCKED_CREDENTIALS, PIT_NOT_AVAILABLE, DATA_RIGHTS, SOURCE_INVALID, INSUFFICIENT_INDEPENDENT_CLUSTERS, QUOTA_EXHAUSTED, RESEARCH_ENGINE_INVALID, COMPLETED_NO_EDGE`. Never report SUCCESS when only provider bars downloaded.

## 3. Separation of research vs model registry

- Stage A `RESEARCH_ONLY`: strategy prototypes, signatures optional, no risk of promotion or Discord trade alert. An approved research run can exist independently of production.
- Stage B `SHADOW_CHALLENGER`: validated research policy plus signed model artifact released only after frozen gates, stored in private model registry, Pi fetch/verify, paper-inference logging, no group advice.
- Stage C `OWNER_PERSONAL_RESEARCH`: only after provider license/security checks, independent forward outcome monitoring; no action execution.
- Stage D `GROUP_ACTIONABLE`: default disabled until independent legal/data/hosting authorization and proper personalized-suitability policy. Backtest profitability never alone enables it.

Do not conflate “GitHub workflow succeeded” with “model release promoted” or “predictive edge validated.”

## 4. Signed artifact and Pi activation safety

- Publish small versioned **non-executable** model payload; never deserialize untrusted pickle/joblib, never execute remote training code on Pi.
- Private registry manifest: signer/version, Ed25519 signed metadata, checksum, feature schema SHA, model version, strategy protocol SHA, as-of training data, approved mode, valid-from/through, champion parent, evaluation digest, signer version.
- Pi HTTPS outbound fetch every 30–60min (tunable), bounds file size, verifies signature SHA, compatibility, version anti-rollback, stages on disk, warms shadow inference, atomic switch; logs version acknowledgment in owner dashboard.
- If artifact malformed, unknown or bad feature version, retain champion if fresh and approved. If no valid champion, NO NEW HIGH-CONVICTION SIGNALS. Don’t promote based on higher synthetic profits.
- GitHub scheduled retraining can be skipped/delayed; independent Pi missed-job detector and optional external heartbeat with honest status. Preserve $0 subscription target if consumer-home power/data unavailable; report degraded.

## 5. Quota plan and billing controls

Variables and hard provider controls:
`MAX_MONTHLY_ACTIONS_MINUTES`, `MONTHLY_RESEARCH_BUDGET_MINUTES`, `MAX_API_REQUESTS_PER_RUN`, `MAX_CANDIDATE_STOCKS`, `MAX_YEARS`, `MAX_GRID_ARMS`, `MAX_HISTORY_STORAGE_MB`, `MAX_ARTIFACT_MB`, `MAX_PARALLEL_RUNS`.

Use real account-wide GitHub minutes (shared with unrelated repos) if authorized; app-estimated remaining quota is advisory. Owner sets provider-side spending limit of $0 billable usage if supported. With no account billing visibility, protect with conservative configured cap and label uncertainty. Matrix workflows can multiply minutes unpredictably; calculate worst case `runner timeout × jobs × frequency`.

Batched 500–1000 stock fetch may increase time/entitlement and memory; stage 66→200→500→1000 with measured worst-case thresholds, not promise a fixed count at no cost. Use allowed private cache if vendor terms permit; provenance and checksum; stale cache invalidation.

## 6. Robustness failure matrix

| Failure | Safe behavior | Test |
|---|---|---|
| Alpaca SIP unavailable/401/403 | source invalid; no IEX/synthetic fallback labelled SIP | Integration stub and real allowed endpoint |
| Action minutes near cap | skip optional experiment with signed/typed status; Pi keeps prior model | mock account budget |
| New strategy fails OOS | keep champion, no Discord high-conviction | seeded bad candidate |
| Untrusted branch requests signer | deny secrets/release | PR/fork security test |
| Release model signed but outdated features | reject Pi install, report incompatible | arm64 or mock fixture |
| Scheduled retraining missed | status alert after tolerance, incumbent valid only until expiry | scheduler outage |
| Supabase paused | local durable spool, no false write acknowledgement | outage/recovery |
| Pi offline while model published | catchup verified version after reconnect, no double send | controlled reboot |
| Bot DM blocked | private pending notification; no public fallback | Discord sandbox |
| License/host no group permission | disable group market display and actionable features | release policy |
| Training runner fails halfway | no partial “validated” published; inspect safe log | injected process kill |

## 7. Deliverables

`docs/quant-v2/OPERATIONS_PROFILE.md`: actual job URLs, CLI commands, CI/env safe names (never values), measured run/API quota, device storage/RAM, cloud scheduler/policy, model registry status, incomplete owner setup and credential-less DEMO behavior. Update the Raspberry Pi installation instructions **only when the working service changes**, preserve operator-friendly phone setup and no recurring laptop involvement.