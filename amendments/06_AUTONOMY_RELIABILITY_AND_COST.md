# Amendment 06 — Always-On Reliability, GitHub Cloud Training, Recovery and True Free Budget

## Why

The owner must NOT turn on a laptop periodically to retrain or deploy APEX. Continuous market monitoring and Discord interactions run on a low-power Pi; GitHub-hosted Actions executes bounded training. Vercel is not a persistent stream process. Free quotas and consumer-home outages must be engineered into service level behavior.

## 1. Runtime responsibility map

**Raspberry Pi 5/4 GB:** Persistent Discord Gateway, 1–30 active IEX stream subscriptions subject to actual plan, priority symbol cache, scheduled 15-minute broad discovery if data accessible, incremental features, inference, per-member assessment fan-out, disk-backed retry spool, local compressed historical cache and health/doctor commands. Restart under systemd, monitor memory/cpu/SD wear/disk/time sync. No ingress from public WAN.

**GitHub Actions:** Weekly heavyweight model fit, embargoed walk-forward test, challenger comparison, small signed model release; weekday optional drift/calibration reporting. Scheduled workflows can be delayed/skipped; they are not true 24/7 services. Use explicit missed-run detector and manual dispatch from owner UI with bounded quota.

**Supabase Free:** Durable canonical signal/lot/event/outbox/model manifest/history, periodic compact data only. Monitor database storage, inactivity pause and backup limitations; raw tick history lives in bounded Pi/local storage when permitted.

**Vercel Hobby:** Protected owner-only Next.js dashboard/API and short signed machine ingest; no long-lived WebSocket, no minute-level Vercel cron. Authenticated settings and config encryption. Verify eligibility of personal/noncommercial use before live group usage.

**Optional Cloudflare Free:** Thin external missed-Pi heartbeat/outage detection. Do not require it to run core scan.

## 2. Cloud retraining workflows

Required agent assets:
- `.github/workflows/apex-model-train-weekly.yml` standard hosted runner, America/New_York schedule on non-top-of-hour minute plus workflow_dispatch; timeout, concurrency, minimum permissions, pinned dependencies, trusted branch.
- `apex-model-drift.yml` lightweight weekdays if budget allows; cheaper Pi drift metrics acceptable with proper model version.
- `apex-model-release.yml` or separate protected release job with human-reviewed promotion first, frozen auto-promotion only after proven stability.

Data acquisition: historical source licenses, immutable source as-of metadata and hashes, reproducible dataset that GitHub runner can access without SSH to Pi or local laptop. Avoid exporting private member fills to Actions except justified aggregate anonymous labels and contractual permission.

Model release: protected environment signing key, versioned manifest, source hash, model schema, ECE/OOS metrics, expiry, ED25519 signature/digest, artifact in private registry—not GitHub artifacts as permanent store. Pi fetches over outbound HTTPS, validates, stages, shadow-scores, activates atomically and reports active version. Untrusted pickled models refused. If no new approved release, champion stays until documented expiry; no forced retrain because clock passed.

## 3. Quota planning

Don't assume GitHub Free minutes are dedicated to APEX; other repos/CI consume shared allowance. Monthly planned upper bound = sum each workflow's `timeout_minutes × maximum expected run count` + separate other project consumption + error retry budget. Compare provider's current included minutes and disable billable overage in GitHub billing settings. App-level estimates are advisory, not airtight provider-side spending control.

Illustrative (NOT measured): weekly 30min × 5 = 150m; weekday drift 5min × 23 = 115m; controlled retry/CI 100m => 365m, plus other repository usage. Benchmark real runs on standard runner and tune.

Rate budgets: Alpaca request cap and IEX symbols; SEC fair access; Discord rate limits; Supabase rows and DB MB; Pi Wi-Fi and local clock; Vercel server function duration and invocation quotas. Show current observed usage, limits, unknown status, stop thresholds at ~70/85/95% and a hard protective budget for optional workflows. No silent subscription upgrade.

## 4. Failure matrix

| Failure | Expected state | Recovery | Alert |
|---|---|---|---|
| Pi power outage | no real-time feed, Discord bot offline | systemd boot/start + resume local spool and reconcile | external heartbeat only if independently configured |
| Internet outage | stale feeds, no new qualified live signals | reconnect with exponential backoff, validate as-of and replay idempotently | system status on recovery |
| Alpaca 429/stream disconnect | quarantine stale feeds; fallback delayed appropriately labeled where permitted | obey Retry-After; bounded subscription resync | owner when breach longer than SLA |
| Supabase paused/read-only | do not claim canonical saved; local pending queue | connect/restore, replay with idempotency | admin + affected users “pending sync” |
| GitHub scheduled run missed | active model continues if valid | detect late train, attempt owner-safe retry only within quota | system status |
| Candidate fails training gate | retain champion; no false improvement | report reasons | system status |
| Corrupted/unsigned artifact | keep known-good model | reject and alert, rollback if needed | urgent owner warning |
| Model expires | “NO NEW HIGH-CONVICTION” (unless valid other strategy) | retrain/validate or manual review | urgent owner warning |
| Pi out of storage | stop nonessential raw archive before DB/outbox | prune, backup, safe service start | urgent |
| Discord DMs blocked | never public fallback | pending private notification or portal | private prompt to user on next interaction |

## 5. Reliability tests

- Cold boot systemd both bot and scanner without duplicate Discord token process, no login/laptop involved.
- Kill scanner mid-event; outbox exactly-once effects despite at-least-once transport.
- Disconnect home internet 8h; no fabricated new signals, resume safely, no repeat private adds/partial exits.
- Credential rotate and revoke; Pi keeps working with authorized fresh config, fails closed if revoked.
- GitHub Actions skipped/delayed: SLA detects absence, doesn't fake train complete.
- Model published while Pi offline: reconnect, verify SHA/signature/schema, warm and activate safely.
- Force bad manifest/feature mismatch/unknown model, stop opening high-confidence signals.
- Network delay > expected bar interval: mark bar incomplete and source age.
- SQL storage near quota: compact safe objects not immutable user events; maintain audit integrity.
- Create encrypted backup, restore to empty test instance and verify privacy policies.
- Heat/memory load on real Pi 5 if available, otherwise specify measured machine and unverified Pi projection.

## 6. Operator UI and runbooks

Dashboard models/operations must show live Pi version/last seen, current model hash/version/validity, GitHub job last expected/actual/failed, forecast calibration and source data age, IEX slot occupancy, DB usage, alerts backlog/dead letters, budget estimate and STOP state. Buttons: test connection, pause/restore modes, rollback trusted model, run budget-checked training workflow if configured. Never inert.

Update `raspberry-pi/` beginner instructions step by step with new service, starter migration, .env secure permissions, credential entry, setup wizard, hardware shopping adjustments if changed, maintenance and backup; no regular desktop usage after onboarding.

## 7. Acceptance

No-laptop fully automated demonstration: timed synthetic dataset arrival → GitHub hosted training job → signed candidate → protected release/registry → Pi fetch/verify/activate → Discord system notice and dashboard acknowledgment. Simulate failed candidate, scheduler miss, quota exhaustion, Pi offline and registry downtime. Supply actual job URL/commit/runner minutes when real CI ran; otherwise clear NOT VERIFIED and one-time owner steps.