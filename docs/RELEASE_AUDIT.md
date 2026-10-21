# APEX release audit

**Verdict: NOT releasable for real-data alerts.** The software gates pass locally and in GitHub CI. The research gate
cannot pass: no strategy has positive out-of-sample expectancy after costs, and no real market data has been evaluated
at all. APEX may run in DEMO and in PAPER_LIVE_DATA (labelled research setups, no probabilities). It must not be presented
as producing validated trade ideas.

Audited head: `18224ee`, merged to `main` through [JaredVogler2/STONKS2026#4](https://github.com/JaredVogler2/STONKS2026/pull/4)
(earlier work via [#1](https://github.com/JaredVogler2/STONKS2026/pull/1) and [#3](https://github.com/JaredVogler2/STONKS2026/pull/3)).
The owner's `amendments/` audit program was applied: APEX-001 and APEX-002 (P0) were reproduced and fixed, APEX-004 was
fixed, and new findings APEX-011 to APEX-026 are registered in the methodology report §7. Open P0s that block real
high-confidence alerts:
- **APEX-006:** no real-data edge.
- **APEX-011:** no live earnings veto.
- **APEX-017:** split-adjusted gates use future information.
- **Deployment / real-service evidence:** missing.
Date: 2026-10-21. Every number below was produced by a command actually run on that date. The full
methodology and testing reference for further research work is
[TESTING_AND_METHODOLOGY_REPORT.md](TESTING_AND_METHODOLOGY_REPORT.md).

## 1. Toolchain and dependency versions

| Area | Versions |
|---|---|
| Runtimes | Node 22.22.0 locally, 22.23.3 on GitHub; pnpm 10.28.0; Python 3.13.16 locally and 3.11.17 (CI target); uv 0.11.32 |
| Web | next 16.3.8, react 19.2.0, typescript 5.9.3, zod 4.1.12, ajv 8.18.0, postgres (postgres.js) 3.4.7, @electric-sql/pglite 0.5.8, tailwindcss 4.1.14, eslint 9.37.0 |
| Web tests | vitest 4.1.11 (+ @vitest/coverage-v8 4.1.11), @playwright/test 1.56.0, @axe-core/playwright 4.10.2 |
| Python | numpy 2.5.4, pandas 3.0.6, httpx 0.28.1, pydantic 2.14.1, cryptography 50.0.2, jsonschema 4.26.0, websockets 17.3, exchange-calendars 4.13.2, discord.py 2.7.1, scikit-learn 1.9.1, pyarrow 26.0.0 |
| Python tests | pytest 9.1.1, hypothesis 6.170.5, ruff 0.17.1, datamodel-code-generator 0.86.0 |
| GitHub Actions | checkout v7.0.1, setup-node v7.0.0, pnpm/action-setup v6.1.0, setup-uv v10.2.0, cache v6.1.0, upload-artifact v7.0.1. All are pinned to full SHAs, and each SHA was checked against its upstream tag with `git ls-remote`. |

## 2. Executed commands and results

Local runs (x86_64 container, 4 vCPU Xeon 2.1 GHz):

| Command | Result |
|---|---|
| `pnpm contracts:check` | PASS (16 schemas, TS + Pydantic in sync) |
| `pnpm --filter @apex/web gen:check` | PASS (embedded migrations in sync) |
| `pnpm lint` | PASS, 0 problems |
| `pnpm typecheck` | PASS |
| `pnpm test` (default suites) | PASS: web 90 passed, 11 skipped (opt-in); contracts 1 passed |
| `APEX_PY_E2E=1 pnpm --filter @apex/web exec vitest run` (all suites, embedded PGlite) | PASS: 101/101 across 9 files |
| the same with `APEX_TEST_DATABASE_URL` (local Postgres 16, non-superuser role) | PASS: 101/101 |
| `pnpm exec playwright test` (1440 px desktop + 390 px iPhone, axe in dark and light) | PASS: 17 passed, 3 skipped by design (desktop-only checks on the phone project); two consecutive runs |
| `APEX_MODE=DEMO pnpm build` | PASS |
| `uv run pytest` (Python 3.13) | PASS: 636 passed |
| pytest on Python 3.11 with all extras (the CI configuration) | PASS: 554 passed locally before the last 79 tests were added; GitHub CI (Python 3.11 job) green on `bd3dbdf` and `18224ee`, the PR #4 heads that include them |
| new characterization and fix tests: `test_levels_quality` 29, `test_assessment` 19, `test_feature_parity` 21, `test_validation_embargo` 8, `test_signal_policy_caps` 2 | PASS: 79 passed, plus 3 for `test_level_study` (all included in the 636) |
| `uv run ruff check src tests` | PASS |
| `uv run apex replay --all` | PASS: 12/12 scenarios |
| `uv run python bench/level_study.py` (diagnostics, development window only) | ran: 201 s; results in the methodology report §6 |
| `uv tool run --from actionlint-py==1.7.12.25 actionlint` | PASS, 0 findings |
| `node --test scripts/secret-scan.test.mjs` / `node scripts/secret-scan.mjs` | PASS: 8/8 tests, 482 files clean |
| secret scan of **every blob in git history** (all refs, 1,048 objects) | PASS: clean |
| `pnpm audit` | 1 high (braces ≤ 3.0.3, via eslint-config-next, lint-time only, **no patched release exists**). Before the upgrades in `fcc0b54` it was 3 critical, 1 high, 3 moderate. |
| `uv run --with pip-audit pip-audit` | PASS: no known vulnerabilities |
| `uv run apex train --source synthetic` / `--source redteam` | ran: 69.8 s / 7.2 s (results in [BACKTEST_REPORT.md](BACKTEST_REPORT.md)) |

GitHub CI on PR #3 (`ci.yml`, first fully green run at head `2890d4e`):

| Job | Result | Wall time |
|---|---|---|
| secret scan | success | 10 s |
| workflow lint (actionlint) | success | 9 s |
| web + contracts (lint, typecheck, test, DEMO build) | success | 2 min 03 s |
| pi-worker (ruff, pytest on 3.11) | success | 2 min 22 s |

The first CI run failed: the web job lacked `uv` for the contracts drift check. That was fixed in `2890d4e`.

**Failures found and fixed during this audit:**
1. **Intermittent Playwright failure.** A React hydration mismatch on Settings. The cause was `page.screenshot`'s default `caret: "hide"`, which writes inline styles into inputs before hydration. Reproduced in 2/2 runs, gone in 0/2 after the fix.
2. **CI missing `uv`**, as described above.
3. **Characterization-test arithmetic.** One of my own expectations was wrong (5.5 / 3.5 = 1.57, not 1.58), and the test caught it.

## 3. Coverage by domain

Web: `APEX_PY_E2E=1 vitest run --coverage`, v8 provider, line coverage of `src/server`, `src/lib` and `src/app/api`.
Pages and components are covered by Playwright, not by these numbers.

| Domain | Lines | Note |
|---|---|---|
| server/security (HMAC, envelopes, field crypto, OIDC) | 92.1 % | |
| server/http | 94.4 % | |
| server/domain (ledger, calendar) | 95.9 % | |
| server/market | 100 % | |
| server/services | 63.4 % | read models for dashboard pages are exercised by Playwright, not vitest |
| server/db | 57.8 % | the Postgres adapter is covered when `APEX_TEST_DATABASE_URL` is set |
| app/api/agent | 91.8 % | real Python Pi client in `pi-agent.test.ts` |
| app/api/release | 95.7 % | real Python release CLI in `python-cli.test.ts` |
| app/api/admin | 20.5 % | **gap**: admin routes are covered through Playwright mutations, not route-level tests |
| app/api/discord, dev-login, logout | 0 % | **gap**: Discord OAuth needs a mock IdP |
| **Total** | 66.8 % lines, 51.5 % branches | |

Python: `pytest --cov=apex_worker` at `18224ee` (633 tests; the 3 study smoke tests came later), statement coverage:

| Package | Statements covered | % | Note |
|---|---|---|---|
| (top-level: cli, config, …) | 246 / 394 | 62.4 % | `cli.py` ~48 %: several subcommands untested; **gap** |
| agent | 664 / 703 | 94.5 % |  |
| catalysts | 2561 / 2783 | 92.0 % |  |
| contracts | 40 / 482 | 8.3 % | generated Pydantic models (validated through jsonschema at runtime instead) |
| demo | 1096 / 1442 | 76.0 % | DEMO flow helpers; exercised through replay and the web seed |
| discord_bot | 2106 / 2476 | 85.1 % |  |
| features | 306 / 326 | 93.9 % |  |
| market | 1015 / 1187 | 85.5 % |  |
| models | 357 / 392 | 91.1 % |  |
| notifications | 169 / 183 | 92.3 % |  |
| operations | 148 / 266 | 55.6 % | `doctor.py` 0 %: the shell `apex-doctor.sh` is the operator path; **gap** |
| portfolio | 414 / 437 | 94.7 % |  |
| release | 895 / 1066 | 84.0 % |  |
| research | 830 / 903 | 91.9 % |  |
| runtime | 1093 / 1258 | 86.9 % |  |
| services | 144 / 147 | 98.0 % |  |
| signals | 794 / 885 | 89.7 % |  |
| storage | 231 / 251 | 92.0 % |  |
| **Total** | 13109 / 15581 | 84.1 % | |

Methodology modules: `portfolio/assessment.py` 97 %, `signals/levels.py` 99 %, `signals/quality.py` 97 %, `signals/policy.py` 90 %, `signals/model_features.py` 89 %, `research/validation.py` 96 %.

The test inventory and scenario matrix are in the methodology report (§2 and §3), including every Phase 12 and
amendment A–L scenario mapped to the test that proves it.

## 4. Replay and synthetic fixtures

- **DEMO.** `pnpm demo` serves the dashboard on embedded PGlite, seeded from `fixtures/demo/web-seed.json`. The seed was regenerated under promotion-policy-2; it replays 54 steps through the same ingestion services the Pi uses.
- **Replay scenarios** (`uv run apex replay --all`, 12/12 pass):
  1. normal publish, activation and target;
  2. same-bar stop first;
  3. gap through stop;
  4. entry never filled, expired;
  5. earnings veto;
  6. stale feed, no alerts;
  7. DM blocked, held privately;
  8. duplicate Discord interaction;
  9. oversell rejected;
  10. corrupt or unsigned model rejected;
  11. model expired, UNVALIDATED only;
  12. Pi offline, spool replay.
- **Screenshots.** Each Playwright run writes one screenshot per page into `apps/web/test-results/`; they are not committed.
- **Fixtures:**
  - `fixtures/demo/web-seed.json`;
  - `packages/quant/vectors/{hmac,envelope,pairing,ledger}.json`: cross-language test vectors;
  - `packages/quant/calendar/xnys.json`: the pinned exchange calendar;
  - the synthetic market generator `apps/pi-worker/src/apex_worker/demo/synthetic.py` (fictional `DM*` tickers);
  - the planted-alpha red-team set `research/redteam.py`.

## 5. Quantitative research results and confidence limits

The full report is [BACKTEST_REPORT.md](BACKTEST_REPORT.md). In short:
- **Synthetic data only.** No hypothesis passes promotion-policy-2.
- **H1 is the closest:** holdout Brier skill 0.032, ECE 0.016, and 115 selected trades. But it fails the after-cost economic gate (bootstrap p10 of mean net return at 25 bps per side = −0.19 %), and 2 of its 3 walk-forward folds had negative skill.
- **The planted-edge red team passes every gate,** with skill 0.332 and permutation p = 1/21. This shows the pipeline finds an edge when one exists.
- **Shuffling labels collapses skill.**
- **No real-data run exists.**

## 6. Vulnerability matrix

| Risk | Mitigation | Residual |
|---|---|---|
| Dashboard exposed publicly | Vercel Authentication on all deployments plus owner Discord OAuth allow-list and server sessions | Vercel settings cannot be verified from inside the app; `scripts/smoke-deployment.mjs` must be run after deployment (**not yet run**) |
| Forged or replayed Pi requests | HMAC-SHA256 canonical request, 60 s window, single-use nonces, device status, revocation | shared-secret theft from the Pi lets an attacker act as that Pi until it is revoked |
| Stolen CI release secret | HMAC **plus** a GitHub OIDC token bound to repository, environment `model-release`, branch, event and `apex-model-*` workflow file; single-use `jti` | OIDC verified only with a locally signed token; **never against a real GitHub token**. `APEX_RELEASE_OIDC=off` exists as an audited escape hatch. |
| Malicious or corrupt model | Ed25519 manifest checked against Pi-held keys before download, sha256 and size checks, JSON-only model format, feature-schema check, atomic swap, rollback | a compromised signing key (GitHub Environment secret) can sign a bad but well-formed model; the frozen server-side gates still apply |
| Cross-member data access | RLS on every member table under a NOLOGIN role, route-level member scoping, adversarial tests on PGlite and real Postgres | owner's database credentials bypass RLS by design |
| Secrets in browser, logs or git | no `NEXT_PUBLIC_*` secrets (lint rule), bundle scan in e2e, secret scanner in CI, history scan clean, write-only settings, encrypted at rest (AES-256-GCM), encrypted envelopes to the Pi | scanner is pattern-based; GitHub push protection is recommended |
| Personal updates leaking to channels | DM-only routing at ingestion and the database CHECK, bot refusal, held-privately path | — |
| SSRF via webhooks or sources | HTTPS allow-list (Discord webhooks only), no private address ranges | — |
| Supply chain | lockfiles frozen, actions SHA-pinned, `pnpm audit` / `pip-audit` | braces advisory has no patched version (lint-time only) |
| Over-trust in research output | Quality is never a probability; UNVALIDATED shows no probability (database CHECK); synthetic models cannot become live champions | members may still act on research setups; wording and labels are the only control |
| Denial of wallet | no Vercel cron, bounded bodies, rate-limited dispatch, Actions budget gate (forecast only) | the GitHub $0 budget must be set by the owner (**not verified**) |

## 7. Performance and cost

**Hardware.** x86_64 container, 4 vCPU Intel Xeon @ 2.1 GHz, Python 3.13.16. **Raspberry Pi 5 numbers are not measured.**

**`bench/bench_scan.py`** (532-symbol universe):

| Step | Result |
|---|---|
| feature panel | 13.3 s |
| end-of-day scan | 10.9 s |
| peak RSS | 408 MB |
| cached rescan | 0.29 s |
| 100 position replays | included in the run |

**Training (local).**
- Synthetic training takes 69.8 s with 20 shuffled-label permutations; it was 46.4 s under policy v1.
- The red-team run takes 7.2 s.
- Model artifacts are a few KB of JSON each.

**GitHub Actions.**
- CI: about 5 min of runner time per push (4 jobs; billed minutes round each job up, so about 8 billed).
- Model workflows: at most 505 min/month in a 5-Monday month (455 with manual promotion), modelled from timeouts. **No model workflow has run.**

**Free-tier headroom (modelled).** Source: `node scripts/cost-report.mjs`, written to [COST_AND_QUOTAS.md](COST_AND_QUOTAS.md):

| Resource | Modelled use |
|---|---|
| Vercel function invocations | ≈ 140 k/month (14 % of an assumed 1 M) |
| Alpaca REST | 40 requests/session; IEX stream uses 25 of 30 slots |
| Supabase database | ≈ 31 MB steady state + 9.4 MB/month |
| Pi → Vercel agent calls | ≈ 4,539/day |

## 8. Provider plan and limit verification

| Item | Status |
|---|---|
| GitHub Actions `schedule.timezone` and `concurrency.queue: max` | **verified** against docs.github.com on 2026-10-20 (both documented) |
| GitHub Actions free minutes (2,000/month Free, 3,000 Pro), shared across repositories | **taken from the amendment text (October 2026)**; not independently re-verified |
| Vercel Hobby invocation limit (1 M/month) | **assumed**; verify at https://vercel.com/docs/plans/hobby |
| Supabase Free database (500 MB) and pause after inactivity | **assumed**; verify at https://supabase.com/docs/guides/platform/billing-on-supabase |
| Alpaca Basic (IEX ≤ 30 symbols, 200 REST/min, SIP history older than 15 min) | **assumed** from the build prompts; verify at https://docs.alpaca.markets/us/docs/about-market-data-api |
| SEC EDGAR fair access (10 req/s, declared User-Agent) | **assumed**; verify at https://www.sec.gov/os/accessing-edgar-data |

## 9. Owner action checklist

None of these has been done. Tick each when you complete it.

- [ ] Create the Supabase project and run `pnpm --filter @apex/web db:migrate` (then `--check`).
- [ ] Create the Vercel project, turn on **Vercel Authentication on all deployments**, set the env vars in [ADMIN_SETTINGS.md](ADMIN_SETTINGS.md) (including `APEX_RELEASE_GITHUB_REPOSITORY`), deploy, and run `node scripts/smoke-deployment.mjs` from outside.
- [ ] GitHub: create the `model-release` environment (main only) with its secrets and variables ([AUTONOMOUS_TRAINING.md](AUTONOMOUS_TRAINING.md) §2), set the **Actions budget to $0 with "stop usage"**, and enable push protection.
- [ ] Dispatch `apex-model-train-weekly` once with `source=synthetic`. Confirm the run report and the OIDC audit row appear in the dashboard.
- [ ] Add Alpaca keys and dispatch a real-data training run. Read the result against the gates **before** changing `promotion_mode`.
- [ ] Install the Pi (`scripts/apex-install.sh`), pair it, and confirm the model activation on the Models page.
- [ ] Discord: add the bot token on the Pi, run `/apex_health`, and test a DM-blocked member.
- [ ] Decide on the compliance switches (all OFF by default). Turning one on records your decision; it does not make anything compliant.

Owner sign-off: ____________________  Date: __________

## 10. Deferred or unverified capabilities

These are **not marketed as working**:

- **Any real-market result or calibrated probability.** No real data has been evaluated.
- **GitHub workflow runs** (training, drift, release, OIDC) on GitHub's infrastructure.
- **Deployment.** Vercel and Supabase deployment, Vercel Authentication, the deployment smoke test.
- **Live services.** The Discord gateway with a real token; a physical Raspberry Pi (install, systemd, performance, SD-card durability).
- **Independent heartbeat watchdog** (for example a Cloudflare worker that alerts when the Pi stops). It was optional and is not built; the dashboard shows a late heartbeat only when someone looks.
- **Member restore after a server-side revoke** ("un-revoke"), server-side mutes and held-DM release. These are local to the Pi bot today.
- **Freshness settings on the Pi.** The Pi does not yet use `sla.freshness`; its quote SLA is fixed at 120 s.
- **Positions recorded before pairing.** They are not migrated to the server ledger.
- **CI coverage.** E2E (Playwright) is not a CI job (minutes budget); run `pnpm e2e` locally. Discord OAuth routes have no automated test.
