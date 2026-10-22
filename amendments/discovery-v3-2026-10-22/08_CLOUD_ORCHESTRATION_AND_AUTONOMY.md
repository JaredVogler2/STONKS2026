# Autonomous Operation — Bounded GitHub Cloud Research, Raspberry Pi and Model Lifecycle

## Architecture / actual scope

User desires unattended learning/monitoring without a laptop and no obligatory new paid services. Pi 5 runs long-lived scan and Discord Gateway; GitHub Actions hosted standard Linux performs bounded historical training/research tasks; private Supabase (if configured) stores canonical records; Vercel is short-lived authenticated dashboard/API, NOT continuous WebSocket/cron worker. Market data via Alpaca with actual Basic plan entitlement, no promise of free consolidated live broadcast. Preserve separate production model release gates; no new automated brokerage routing.

## 1. Three independently scoped action lanes

### A. Forward capture (daily, minimum viable critical path)
Prefer Pi if already connected and reliable; GitHub Actions schedule only where it can capture adequate as-of truth. NYSE/DST calendar aware. Limit ~2–5m per trading day when measured. Keys scoped read-only market data, no signed production model promotion, no Discord group alert token for research job. Separate append-only store/digest anchor and outcome maturity process, retries and missing-day alarm. See 02.

### B. Discovery DEV batches (manual or low-frequency)
`.github/workflows/apex-discovery-v3.yml`: workflow_dispatch with `batch_id, source=alpaca, universe, experiment_manifest_sha, max_provider_requests, trial_budget, mode=RESEARCH_ONLY`; run only reviewed trusted branch after manifest exists, standard ubuntu, timeout <= explicit budget, concurrency=1. Never requires production Vercel/Supabase registry or model signing key just to produce findings. Fails if secrets unavailable or history unreliable; no silent synthetic fallback. Bounded CPU and use lightweight models first. Aggregate output in short-retention artifact and reviewed PR, no direct pushes to main from untrusted research payload.

### C. Production model train/release (existing, separate)
Existing weekly model workflow can fit synthetic DEMO when real data missing, but DO NOT promote/demo masquerade as live high-confidence alpha. Model release via protected environment, feature schema + version + digest + trusted signature, Pi outbound fetch/atomic activation/rollback; no agent editing environment secrets from PRs. Financial/market-model release requires independent forward and legal/data approvals beyond positive Brier skill. A compliant research scoreboard does not grant broker group advice rights.

## 2. Cost / compute/ quotas

Measure and display monthly cost estimate:
`monthly Actions minutes ≈ daily_capture_runtime×NYSE_sessions + scheduled_train_timeout×weeks + discovery_run_budget + retries + CI minutes + other repository usage`. Github account allowance is SHARED across repositories; the agent cannot assume free minutes are unconsumed. GitHub billing UI or trusted API/provider limits, no positive-cost overage enabled. No expensive runners, always-on remote server, or large hyperparameter sweep by default.

Provider market data: API requests, SIP historical entitlement/finality, IEX live simultaneous subscriptions <= authorized limit, SEC published data rate and relevant hosting storage inactivity. Resource budget is preregistered; optional research stops at quota but capture prioritized. One-off owner approval required for paid data or infrastructure. Caching vendor bars only where license permits; otherwise recompute only bounded samples and record source hash/version.

## 3. Safety gates and cloud security

- Only trusted main for actions accessing secrets; pin action SHA, least token permissions, environment branch protections and read-only Alpaca credentials. No secrets in PR/fork workflows, workflow artifacts, job summaries or application bundles.
- Workflow inputs validated/sanitized; never let agent-supplied shell, arbitrary git ref or LLM-composed Python run with tokens.
- Research artifact export scanner denies raw licensed OHLCV tables, protected derived quote exports, private member positions and secrets. Include aggregate metrics and protocol/source hashes only.
- Cloud model registry uses signed, bounded, non-executable JSON/approved ONNX; no remote pickle. If model invalid/revoked/expired, no new high-confidence output. Live user fills remain per-member RLS/Discord access controls.
- Discord private member note delivered ephemeral/DM only, with blocked-DM safe queue. No generic channel fallback; synthetic never displayed as live quote.
- Vercel Hobby personal/noncommercial eligibility and Alpaca downstream market-data redistribution / personalized investment-adviser obligations require separate human/legal determination. Default GROUP_ACTIONABLE OFF.

## 4. Failure/recovery checklist

| Failure | Required handling |
|---|---|
| GitHub schedule skipped, job queued for hours | Mark expected session MISSED if not observed in time; no retrospective as-of forgery |
| Pi shutdown or home network outage | Local disk-backed outbox; external heartbeat if configured; replay permitted future outcomes without fabricating decisions |
| Alpaca SIP not entitled / quota exhausted | Source invalid; stop research and notify owner; do not fall back to random demo for real study |
| Corporate-action endpoint unavailable | SOURCE_INVALID or explicit exclusion pre-rule; no unrecorded economic event |
| Supabase unavailable | Persistent pending queue with integrity, never say durable write succeeded; after recovery idempotent |
| Discord DM blocked or removed member | Retain private status / revoke membership, never public fallback |
| Signed model incompatible | Reject and retain valid prior; report mismatch, disable expired predictor |
| Full disk on Pi | Stop nonessential archive, preserve canonical event ledger and health notice |
| Actions shared-minute cap near zero | Suspend discovery before forward capture if possible and safe; no paid run triggered |
| No valid strategy | Display NO_TRADE with explanation, maintain app runtime and safe group educational alerts if permitted |

## 5. Operator UI and acceptance

Show actual last capture session, missing count, chain integrity and anchoring, cloud job/run conclusion, market source/finality, candidate run budget, model and protocol active hashes, amount of matured evidence, next allowed confirmatory look, and why group trading disabled.

Tests:
- end-to-end from verified Alpaca sample → GitHub cloud bounded discovery → signed research summary artifact/review PR without production sign key;
- schedule DST and holiday logic with frozen clock; test missing/late run; exact append idempotency;
- run 5-minute synthetic fixture smoke and true real-data research with actual source reported; never claim a fixture validation is real-alpha;
- no-laptop Pi autonomous reconnect and outbound registry query;
- no paid provider token or cloud compute enabled automatically;
- old experimental strategy disabled while stock scanner and private positions stay intact;
- model promotion conditional on scientific, model, operations, compliance gates, none bypassable by UI toggle.

**Deliverables:** cloud workflows, launcher/runbook for one-time owner setup, measured minute/request budget, failure tests, dashboard health panel, actual executed job URL and OWNER_SETUP_REQUIRED where service/permission remains unconfigured.