# APEX implementation log

Branch `claude/bold-ptolemy-4iwfsv`. Every entry is dated 2026-10-20 (one build session). Test numbers are from commands
actually run in the build container, not estimates. Release-level evidence is in [RELEASE_AUDIT.md](RELEASE_AUDIT.md).

## Deployed state (read first)

| Thing | State |
|---|---|
| Vercel project | **not deployed** by this work. No Vercel deployment was created or tested. |
| Supabase project | **not created.** Migrations were applied to a local Postgres 16 (non-superuser role) and to embedded PGlite. |
| Raspberry Pi | **not touched.** Install, doctor and update scripts were tested with `--dry-run` and shellcheck, not on a Pi. |
| Discord | **no real bot token used.** Bot logic is tested in-process and in dry-run preview. Nothing was posted to Discord. |
| GitHub Actions | workflows are committed but **have never run on GitHub.** The `model-release` environment and its secrets do not exist yet. |
| Alpaca | **no keys.** The real-data paths are tested against mocked HTTP transports only. |
| Live champion model | **none.** All research is synthetic and UNVALIDATED (see [BACKTEST_REPORT.md](BACKTEST_REPORT.md)). |

## Phases

| Phase | Commit(s) | What landed | Tested with |
|---|---|---|---|
| 01–04 foundations | `46f9df0` | contracts (JSON Schema → TS + Pydantic), Postgres schema with RLS, ledgers with shared vectors, HMAC / AES-GCM / X25519 / Ed25519 with cross-language vectors, synthetic market, features, H1–H8 strategies, candidate engine | pytest, vitest, contracts drift check |
| 03, 06 market data + research | `ef57918` | Alpaca adapter, replay adapter, quota governance, IEX slot planner; execution model, walk-forward + holdout, isotonic calibration, frozen promotion policy, red-team controls; signed model store with atomic swap and rollback | pytest |
| 05, 07, 08 + web server | `75679cb` | signal policy and lifecycle, member assessments, catalysts (SEC, Form 4, congress, macro, earnings, news), owner auth, HMAC agent API, pairing, encrypted settings, RLS positions, model registry, DEMO seed | pytest, vitest integration on PGlite |
| 09 Discord bot | `1d9d1d4` | production bot over a plain-Python service, spool when the server is down, delivery loop, one-bot-per-token guards, starter migration | pytest |
| 10 dashboard | `219855d` | all owner pages, Playwright journeys at 1440 and 390 px | Playwright, vitest |
| 11 operations | `930ca07` | systemd units, install/doctor/update/rollback, encrypted backups, `db:migrate`, deployment smoke | shellcheck, `db:migrate --check` on local Postgres |
| 12 fixes + adversarial | `3931e12` | **JSON double-encoding bug on real Postgres fixed** (below); agent API additions; adversarial suite against the real route handlers | vitest on PGlite **and** local Postgres |
| 12 tests + docs | `75e46b1` | XNYS calendar edge cases, Flow D multi-member ledger, workload benchmark, member/admin/risk docs | vitest, pytest, bench |
| 06 research revision, 10 a11y | `32cb60f` | promotion-policy-2 (permutation gate), WCAG AA tokens and axe suite, ADRs 0001–0009, README | pytest, Playwright + axe |
| Amendment + Pi connected mode | `2bf2819` | GitHub training/drift/release workflows, release CLI, history cache, CI; Pi agent client, pairing, worker runtime, model sync, connected bot, replay scenarios | pytest (3.11 and 3.13), vitest, actionlint, secret scan |
| 13 research docs | `efbb6cf` | BACKTEST_REPORT, DEMO seed under policy-2, worst-case Actions forecast, Python release CLI round trip against the real routes | vitest (opt-in `APEX_PY_E2E=1`), Playwright |
| 12 CI fix | `2890d4e` | web CI job installs uv for the contracts drift check (first GitHub CI run had failed) | GitHub CI green: 4/4 jobs |
| 11 amendment: short-lived credentials | `c044a0f` | GitHub OIDC as the second factor on the release identity | 20 unit + route tests on PGlite and Postgres (local RSA key only) |
| 12 cross-language | `88910a1` | real Pi worker against the real agent routes (`pi-agent.test.ts`, opt-in) | 9/9 on PGlite and Postgres |
| 12 dependencies, flake | `fcc0b54` | vitest 4.1.11 / ajv 8.18.0 (audit: 3 critical → 0); e2e screenshot no longer races hydration | Playwright 17/17 twice |
| 12 characterization | `84aa547` | levels, Quality and assessment rules pinned (48 tests); `assessment.py` coverage 25 % → 97 % | pytest |
| amendments/ P0 | `bd3dbdf` | **APEX-001** train/live feature parity (shared assembler, fail-closed), **APEX-002** unsupported interval removed | parity test over 228 pairs; retrain byte-identical |
| amendments/ P1 | `fc5b8c9` | **APEX-004** embargo counted in NYSE sessions | 8 holiday-boundary tests; ≤ 2 extra purged rows, no gate change |
| policy bug | `18224ee` | open-per-sector cap counted sectorless names under a different key | reproduced (fails on the old line), fixed |
| 13 report | (this commit) | TESTING_AND_METHODOLOGY_REPORT, METHODOLOGY_REFERENCE, TEST_EVIDENCE_MATRIX, RELEASE_AUDIT, `bench/level_study.py` | see RELEASE_AUDIT |

Merged to `main`:
- through `3931e12` via [JaredVogler2/STONKS2026#1](https://github.com/JaredVogler2/STONKS2026/pull/1) (merge `de754cd`);
- through `2890d4e` via [JaredVogler2/STONKS2026#3](https://github.com/JaredVogler2/STONKS2026/pull/3);
- through `18224ee` via [JaredVogler2/STONKS2026#4](https://github.com/JaredVogler2/STONKS2026/pull/4) (merge `4823a8a`).

The owner's `amendments/` audit program (merged as PR #2) was read from `main`. Its Prompt A asks the in-flight agent to reproduce
suspected P0 findings and fix confirmed ones minimally. APEX-001 and APEX-002 were confirmed and fixed; APEX-004 was fixed;
the rest are tracked in [TESTING_AND_METHODOLOGY_REPORT.md](TESTING_AND_METHODOLOGY_REPORT.md) §7, together with new
findings APEX-011 to APEX-026. This session's permission guard refused `git merge` and `git checkout -B` onto `main`, so
the working branch keeps its already-merged history and adds new commits on top; each follow-up PR merges cleanly.

## Amendment 2026-10-20: laptop-free cloud training

**Integration.** The amendment arrived on `main` while phases 09–11 were in progress. Its content was read from
`origin/main` and implemented without restarting work. A `git merge --ff-only origin/main` into the working branch was
refused by this session's permission guard. The amendment's own files therefore reach this branch's history only
through `main` (PR #1's merge). The follow-up PR merges with them without conflicts. No work was discarded.

**Migration impact.** None of these needs a manual data migration:
- The registry tables (`model_versions`, `model_activations`, `training_runs`) were part of the first schema (`46f9df0`).
- The new release routes are additive.
- The Pi's model store layout moved to one directory per strategy. Pis paired before this change have no models installed yet.

**What exists:**
- **Training workflows.**
  - `apex-model-train-weekly.yml`: Mondays 19:17 America/New_York, ≤ 45 min.
  - `apex-model-drift.yml`: weekdays 20:23 ET, ≤ 10 min, optional.
  - `apex-model-release.yml`: validated promote and rollback; `force` cannot be reached from CI.
- **Release API.**
  - Registers candidates, verifying signature and checksum.
  - Re-applies the frozen policy, so DEMO or synthetic models can never become live champions.
  - Records training runs.
- **Pi model sync.**
  - Verifies the signature against keys held on the Pi before downloading.
  - Installs staged, runs a probe health check, swaps atomically, rolls back automatically and acks the activation.
- **Missed-run detection** in two independent places: the dashboard (8 days without a successful training run) and the Pi notice router.
- **Models & Automation panel** and **Run Training Now**: server-side dispatch, rate-limited, budget-checked, disabled with instructions when not configured.

**GitHub Actions minutes estimate.** These are maximums from `timeout-minutes` × frequency, not measurements:

| Workflow | Max per run | Runs per month | Max per month |
|---|---|---|---|
| Weekly training | 45 | ≤ 5 | 225 |
| Auto-release (only when `promotion_mode=auto`) | 10 | ≤ 5 | 50 |
| Drift | 10 | ≤ 23 | 230 |
| **Model workflows total** | | | **505** |
| CI (per push to the PR, about 4 jobs) | ≈ 12 | depends on pushes | — |

The default app budget is 1,500 of the 2,000 free minutes, which leaves room for CI. Measured so far, locally only:
- a synthetic training run takes 70 s of compute;
- a red-team run takes 7 s.

No real Alpaca download time has been measured. [AUTONOMOUS_TRAINING.md](AUTONOMOUS_TRAINING.md) explains how to set
the GitHub budget to $0 so a quota overrun stops jobs instead of billing.

**Accepted limitations:**
- None of the workflows has run on GitHub.
- Real-data training has never run.
- The full cloud-runner-to-Pi round trip is demonstrated only in pieces:
  - the Python release CLI against the real release routes (`tests/integration/python-cli.test.ts`);
  - the Pi model sync against the Python fake API and the replay scenarios;
  - the real Pi worker against the real agent routes (`tests/integration/pi-agent.test.ts`).

  It has never been demonstrated end to end on live infrastructure.

## Phase 14 (2026-10-21): real-equity research pipeline

- `apex research real-equities` (package `apex_worker.research.realeq`) runs H1–H8 plus baselines on real Alpaca
  historical SIP bars under the frozen [`realeq-protocol-1`](../research-results/real-equities/PROTOCOL.md).
  It is research only: no signing, registry, promotion, Discord or orders.
- Manual workflow `apex-real-equities-research.yml` (`main` only, `contents: read`, Alpaca market-data secrets only).
- `apex release preflight` reports `PRODUCTION_REGISTRY_UNCONFIGURED` for drift and weekly training.
- First real experiment (run 39696547618, 2026-10-21): aggregate **NO_TRADE**. See
  [`research-results/real-equities/2026-10-21-fixed78-sip-8y-r2/`](../research-results/real-equities/2026-10-21-fixed78-sip-8y-r2/).

## Deficiencies found and fixed

| Problem | How it was found | Fix |
|---|---|---|
| postgres.js double-encoded JSON text into `jsonb`, so on Supabase every JSON column would have stored a string | running the integration suites against a real Postgres (PGlite hid it) | custom json/jsonb serializer plus a regression test on stored JSON types (`3931e12`) |
| Account upsert needed an UPDATE grant the member role does not have, and DEMO seeding silently swallowed the error | DEMO seed left positions missing | `INSERT … ON CONFLICT DO NOTHING` + SELECT; the seed now rethrows unexpected errors |
| DEMO champion never promoted: expiry was compared against the wall clock, not the replay clock | dashboard showed no champion | `now` threaded through promote / rollback / heartbeat |
| A single shuffled-label control flagged H2 as "leakage" | 30-seed permutation study | promotion-policy-2 (20 permutations, real skill must beat the 95th percentile); see BACKTEST_REPORT §3 |
| Secret rotation reached the Pi only at the next config refresh (up to 30 min) | connected-mode tests | heartbeat-announced versions are compared directly |
| Dashboard colours failed WCAG AA contrast; the loading skeleton used a prohibited ARIA attribute | axe-core in Playwright | token changes, underlined inline links, `role="status"` |
| `node --test tests/` failed on Node 22 for the contracts package | CI dry run | glob pattern |
| "Run Training Now" sent only `reason`, so its runs were recorded as owner GitHub-UI runs | workflow input review | sends `source` and `requested_by=admin` |
| Live model inputs differed from training: `squeeze_prev` always None, rounded regime probabilities (APEX-001) | amendment register; parity test over 228 pairs | one shared assembler, fail-closed (`bd3dbdf`) |
| Per-alert "intervals" were binomial SEs of the holdout count (APEX-002) | amendment register; code review | removed; "interval not established" (`bd3dbdf`) |
| Embargo counted weekdays, so holidays shortened it (APEX-004) | amendment register | NYSE sessions (`fc5b8c9`) |
| Open-per-sector cap ignored sectorless names (`None` vs `"unknown"` key) | methodology audit | same key (`18224ee`) |
| Web CI job had no `uv` for the contracts drift check | first GitHub CI run | setup-uv + locked sync (`2890d4e`) |
| Intermittent e2e hydration mismatch | Playwright 2/2 reproduction | screenshot `caret: "initial"` (`fcc0b54`) |
| Scheduled drift failed at *Release policy* because the registry was never provisioned | run 39547226607 log | `apex release preflight`: `PRODUCTION_REGISTRY_UNCONFIGURED` skips registry steps on schedules, fails manual runs; auth unchanged |

## Next tasks (owner)

These steps need accounts and cannot be done from the build container. Each is in
[README_OPERATOR.md](README_OPERATOR.md) and [AUTONOMOUS_TRAINING.md](AUTONOMOUS_TRAINING.md).

1. Create the Supabase project, then run `pnpm --filter @apex/web db:migrate`.
2. Create the Vercel project with Vercel Authentication on all deployments, then run `scripts/smoke-deployment.mjs`.
3. Create the GitHub `model-release` environment (main only), add its secrets and set the $0 Actions budget.
4. Dispatch `apex-model-train-weekly` once with `source=synthetic` and confirm the run report appears on the Models page.
5. Install the Pi with `scripts/apex-install.sh`, pair it, and confirm the model activation on the Models page.
6. Add the Discord bot token on the Pi and run `/apex_health`.
