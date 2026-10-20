# Amendment 08 — Executable Regression Test Plan and Acceptance Matrix

## Goal

Prove fixes, detect inadvertent degradation of current PR #1, and make v2 release evaluation empirical. This is an execution contract, not a generic checklist. For every case, agent must record: test ID, source commit, fixture hash, command, expected behavior, actual output, status, links to logs and owner-facing action.

## Test environment requirements

Use isolated seeded local Postgres with real migrations and realistic row-level permissions; clean Next.js build with strict types and pinned lockfile; Python unit/property tests and actual arm64 Pi where hardware is available; simulated and real Discord sandbox guild (no customer messages); deterministic signed test artifacts; frozen synthetic time replay; optionally read-only live Alpaca/test provider for source semantics. All fixture prices must be explicitly DEMO. Never run destructive tests on members' production positions.

### Matrix

| ID | Severity if fails | Purpose | Exact minimal scenario | Pass condition |
|---|---|---|---|---|
| Q01 | P0 | train/serve feature parity | same H1 squeeze high/low from identical t snapshot through `_row_features` & `model_row` | identical ordered features/source/as-of; equivalent model prediction; no silently missing `squeeze_prev` |
| Q02 | P0 | model schema | signed champion expects old feature layout, Pi features change | block activation, preserve champion or NO TRADE, report mismatch |
| Q03 | P0 | calibrated uncertainty honesty | 100 independent vs clustered identical forecasts, same p and count | interval/coverage accounts for model and temporal dependence or unavailable label, not naive fixed binomial CI |
| Q04 | P0 | no look-ahead | news trade Aug 1 filing Sep 10 + next-close gap | signal at Aug 1 cannot use Sep 10 publication |
| Q05 | P0 | calendar purge | overlapping 20-session labels across Good Friday/Thanksgiving and half days | no train outcome window leaks into held-out fold |
| Q06 | P1 | H7 coverage | known positive catalyst `available_at` then tight base | H7 registered, counts/reason codes, correct candidate once; missing catalyst → NO_EVENTS |
| Q07 | P0 | no false real alpha | all synthetic strategies unvalidated | no real high-confidence Discord; NO TRADE where gate fails |
| Q08 | P0 | shuffled-label control | retain features, randomly permute future outcomes | out-of-sample predictive gain collapses; no validated release |
| Q09 | P1 | repeated tuning | try many parameter variants on dev only, last holdout immutable | no test set hyperparameter exposure, complete attempt log |
| Q10 | P1 | costs/fragility | 0/10/25/50 bps each side and target-stop both same bar | realistic fills and conservative same-bar; flagged strategy if edge vanishes |
| Q11 | P0 | venue/source | IEX quote stale or bid crossed while delayed SIP healthy | data source distinguished; no invented consolidated NBBO |
| Q12 | P0 | account eligibility | common NYSE stock unknown Robinhood support | “likely eligible,” not proven tradable; optional owner verification |
| Q13 | P0 | member isolation | A and B same NVDA, different fills; B queries/updates A's lot | access denied at API + SQL + bot, no private data leaked |
| Q14 | P0 | fill ledger | A 20@180 add 5@186 sell 12@195 fees/fractions | independently verified FIFO cost, realized/unrealized and remaining lots |
| Q15 | P0 | idempotency | duplicate Discord interaction + HTTP retry + restart | precisely one persisted buy/exit and at-most-one user-facing effect |
| Q16 | P0 | blocked DM | member blocks APEX and receives risk update | pending private event only, no channel leak |
| Q17 | P0 | forged component | stale/fabricated signal custom ID, revoked member | unauthorized mutation denied |
| Q18 | P0 | gateway/restart | Pi reboot while saving entry then 8h outage | queue durability, explicit pending status, no repeated fill after retry |
| Q19 | P0 | endpoint auth | unauthenticated request to every admin endpoint and production alias | deny with no sensitive response or timing leak |
| Q20 | P0 | HMAC | reused nonce, stale timestamp, modified body, revoked agent | reject; no side effect |
| Q21 | P0 | settings crypto | wrong master key, corrupted ciphertext, frontend fetch, log inspection | graceful error, no plaintext leak, no `NEXT_PUBLIC_*` secret |
| Q22 | P0 | model release | forged signer, hash mismatch, 10MB oversized file, JSON schema mismatch | Pi refuses; old version intact; no pickle execution |
| Q23 | P1 | cloud autotrain | scheduled or workflow_dispatch trainer → tested champion manifest | real GitHub Actions job on trusted branch, reproducible SHA/data hash, cost recorded |
| Q24 | P0 | auto Pi distribution | Pi offline during promotion then reconnect | safe outbound verified model update/ack without laptop, version matches registry |
| Q25 | P1 | missed GitHub job | artificially suppress schedule event | late run detected; status alert; no imaginary success |
| Q26 | P1 | free quotas | near cap Github minutes, Alpaca, Supabase MB, Discord 429 | throttle/stop optional work, preserve critical ledger, no paid upgrade |
| Q27 | P0 | stale quote | halted symbol, disconnected IEX, outlier print | suppress precise trade guidance, mark “data uncertain” |
| Q28 | P1 | UI functional | desktop 1440, iPhone 390, every setting/button | actual persisted actions or disabled reasons, keyboard + no overflow |
| Q29 | P1 | snapshots | public Discord shared signal and personal follow-up | price + time + model source exactly auditable, no synthetic masquerade |
| Q30 | P0 | group research legal gate | intentionally unapproved `GROUP_ACTIONABLE` enable attempt | blocked irrespective of UI Boolean alone; external review requirement documented |
| Q31 | P1 | backup/restore | empty DB restoration from encrypted backup | correct migrations, RLS, user ownership, event history; no secret disclosure |
| Q32 | P1 | performance/cost | 500 symbols/25 live IEX slots/50 members/100 positions | quote fetched once per symbol, bounded Pi memory/API rate/DB growth |
| Q33 | P0 | live calibration expiry | model expired after 21 days without successful retrain | no HIGH_CONVICTION until validated new model, health warning |
| Q34 | P1 | current work migration | upgrade old Pi `apex-pi-bot` service to new worker | only one Gateway bot running, old demo entries backed up/migrated |
| Q35 | P1 | news & insider precision | Form4 award code A vs purchase P, 10b5-1, transaction time vs filing | no blanket positive classification or pre-publication event |
| Q36 | P1 | market stress | sector-wide correlated gap loss and high spreads | lower-bound economic risk and correlation appropriately reflected |

## End-to-end scenario suites

### E2E-A — First-time owner
Clone, install in DEMO without keys, authenticate admin, view startup checklist, simulate 1,240 symbol universe, discover candidate, see reject/no-trade reasons, open chart, test Discord in sandbox and per-user position flow. No true live alerts or real-looking synthetic current tickers.

### E2E-B — Distinct user positions
Same published signal, A fills 20@180, B 10@193 (outside entry zone), C watches; each gets different appropriate personalized risk state. A adds/partially sells and B closes; group signal has unchanged performance record. D blocked DM doesn't expose fills. Verify after Pi restart and Vercel refresh.

### E2E-C — Trading outcomes
Replay rally, breakout failure, sideways expiry, same bar touching stop and target and overnight gap. Assert target touched ≠ proven fill. Clock counted by NYSE trading sessions, source timestamps and data maturity correct.

### E2E-D — Cloud model release
Train with deterministic offline dataset on GitHub-hosted runner within quota; inspect validation and signed non-executable model; Vercel registry private; Pi offline catchup and feature parity checks; active version in admin health; failed champion challenger retains current; old model validity expiration suppresses signals.

### E2E-E — Disaster recovery
Pi internet disconnect, Supabase read-only/pause, Vercel 503, Discord 429 + DMs disabled, clock skew and power outage. All transitions are explicit (pending/unknown/degraded), no quietly accepted writes or fake market status. Recover and replay without duplicated state.

### E2E-F — Live feed if licensed/configured
Use real exchange timestamps/source labels from Alpaca Basic; enforce IEX limit and role; compare bar calculations at end-of-day and after hours; validate provider timeout and late corrections; mark group redistribution eligibility separately. Real historical alpha remains UNVALIDATED until genuine OOS and prospective performance meets frozen gates.

## Quant methodology checks

Compute exact holdout date, training/calibration separation, effective sample/cluster, baseline performance, net R/return, MFE/MAE, expected shortfall, max loss, transaction-cost sensitivity and calibration metrics. Test no illicit look-ahead with shuffled-label and intentionally future-leaking trap. Verify model promotion thresholds and release signer, not just a JSON status field.

## Automation and evidence

CI must separate lightweight PR checks (lint/typecheck/contracts/unit) from heavier scheduled quantitative tests. Pin actions to reviewed SHA, use read-only default permission, no production secrets for untrusted PRs. Store bounded artifacts with hashed fixture metadata. Real services need owner secrets; if not available, state `UNVERIFIED LIVE` and include secure one-time setup.

Generate:
- `docs/V2_TEST_REPORT.md` with matrix actual statuses and evidence.
- `docs/V2_RESOURCE_PROFILE.md` with measured hardware/runner.
- `docs/V2_RELEASE_DECISION.md` with open P0 blockers and owner signoff needs.
- A short Discord/admin dashboard summary of true test status, not marketing.

**Definition of done:** every P0 pass recorded or release blocked. No claims of passing tests without actual executed command and environment.