# Autonomous cloud training, validation and model release

Implements the 2026-10-20 amendment (laptop-free retraining). After the one-time setup below, retraining, validation,
calibration, registration, promotion (when the owner enables AUTO) and distribution to the Raspberry Pi run without
any personal computer. One-time setup needs a web browser and one terminal session on the Pi. After that, everything
runs on GitHub-hosted runners, Vercel, Supabase and the Pi.

> **Status: implemented and tested locally. The GitHub-hosted runs are UNVERIFIED (acceptance test B).** The workflows
> are written, parsed, linted with actionlint and checked by unit tests. They have never run on GitHub, because this
> environment has none of the account's secrets, environments or billing settings. The one-time actions below are
> what makes them runnable. Do not treat a run as proven until it appears in the Actions tab and in the dashboard's
> training-run list.

## 1. What runs where

| Workflow | Trigger | Job timeout | Secrets | Writes registry? |
|---|---|---|---|---|
| `.github/workflows/ci.yml` | `pull_request`, push to `main` | 5 / 20 / 20 / 5 min | **none** | no |
| `.github/workflows/apex-model-train-weekly.yml` | Monday 19:17 America/New_York + `workflow_dispatch` | 45 min (train step 30) | `model-release` env | registers candidates |
| `.github/workflows/apex-model-release.yml` | `workflow_dispatch` + `workflow_call` (from weekly train when AUTO) | 10 min | `model-release` env | promote / rollback |
| `.github/workflows/apex-model-drift.yml` | weekdays 20:23 America/New_York + `workflow_dispatch` | 10 min (drift step 7) | `model-release` env (no signing key) | no (reports a `drift` run) |

The weekly train runs: budget gate → current champions → bounded, incremental, checksummed history → train → sign → evidence → register → report.

1. `apex budget check --job train` checks the budget.
2. `apex release champions` fetches the current champions.
3. The history cache is restored, then extended with bounded, incremental, checksummed downloads.
4. `apex train --sign` runs conservative execution, purged walk-forward with an untouched holdout, isotonic
   calibration, red-team controls and the frozen promotion policy. It then writes Ed25519-signed JSON candidates.
5. Ephemeral evidence is uploaded: `report.json` and `summary.md` only, kept 7 days.
6. `apex release upload` registers the candidates.
7. `apex release report-run` reports the training run.

Train/release are separate workflows: the protected promotion step is reusable both by the owner (dispatch) and by the
weekly train (call), and drift stays read-only. The server re-applies the frozen promotion policy in every path.

Python commands (all in `apps/pi-worker`, `apex_worker/release/`):

| Command | Purpose |
|---|---|
| `apex release policy` | deployment mode, `models.policy`, `budgets`, this month's measured minutes, last run per workflow |
| `apex release champions [--verify]` | champion summaries for `apex train --champions`; `--verify` re-checks registry checksums/signatures |
| `apex release upload --dir DIR` | register signed candidates (promotable only by default; DEMO models never sent to a live registry) |
| `apex release report-run ...` | status, outcome, minutes estimate, code SHA, data hash, compact evaluation → `apex.training_runs` |
| `apex release promote\|rollback --model-ids ...` | through `POST /api/v1/release/promotions` (no `force`), then re-verify the registry |
| `apex release sla` | missed-run status of the scheduled workflows |
| `apex drift` | latest-window calibration (ECE, Brier skill) and feature PSI vs each champion's signed drift criteria |
| `apex budget check --job train\|release\|drift` | exit 0 proceed, **3 skip**, 2 configuration error |
| `apex train --source alpaca` | uses `apex_worker/market/history.py` + `research/training_data.py` |

Release API routes used by CI (all `rel_*` identity + `APEX_RELEASE_HMAC_SECRET`):

* `GET /api/v1/release/policy` — new.
* `GET /api/v1/release/champions[?include=artifacts]` — `include=artifacts` is new.
* `POST /api/v1/release/models` — existing.
* `POST /api/v1/release/training-runs` — existing.
* `POST /api/v1/release/promotions` — new.

## 2. One-time setup (browser + one Pi terminal session)

### 2.1 Generate the keys (once, on the Pi)

```bash
cd ~/STONKS2026/apps/pi-worker
uv run apex keygen signing   # APEX_MODEL_SIGNING_KEY (private seed) + APEX_MODEL_TRUSTED_KEYS (public JSON)
uv run apex keygen hmac      # use this value as APEX_RELEASE_HMAC_SECRET (NOT the Pi's own AGENT_HMAC_SECRET)
```

Paste the values into the settings pages below, then clear the terminal (`clear; history -c`). Never store the
private signing seed on the Pi, in Vercel, in Supabase or in the repository: it belongs only in the GitHub environment.
Choose a release identity such as `rel_ghactions01`: `rel_` followed by 8–32 lowercase letters/digits.

### 2.2 GitHub repository settings (github.com → repository → Settings)

1. **Environments → New environment `model-release`**
   * *Deployment branches and tags*: **Selected branches and tags → `main` only**. PR code and other branches can then
     never receive these secrets (the workflows also refuse non-default branches).
   * Do **not** add required reviewers. They would stall every scheduled run waiting for a click, which breaks the
     no-recurring-involvement goal.
   * **Environment secrets**:
     * `APEX_RELEASE_URL`: the production dashboard origin, `https://…`.
     * `APEX_RELEASE_AGENT_ID`.
     * `APEX_RELEASE_HMAC_SECRET`.
     * `APEX_MODEL_SIGNING_KEY`.
     * optional `APEX_VERCEL_BYPASS_SECRET`: only if Vercel Deployment Protection challenges `/api/v1/release/*`. The web
       proxy honours it on the signed machine routes only.
     * optional `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY`: real history. Without them, training uses the synthetic
       fixture and the result is labelled DEMO.
   * **Environment variables** (not secret):
     * `APEX_MODEL_TRUSTED_KEYS`: the public JSON. CI then re-verifies signatures locally and in the registry.
     * optional `APEX_MODEL_SIGNER_VERSION` (default 1).
     * optional `APEX_ACTIONS_MONTHLY_BUDGET`: overrides the dashboard budget.
     * optional `APEX_RELEASE_OIDC_AUDIENCE`: only if you changed the audience on the Vercel side (default
       `apex-release`).
2. **Actions → General**
   * *Workflow permissions*: **Read repository contents**. The workflows declare `permissions: contents: read`, and
     add `id-token: write` only on the jobs that call the release API (see §2.3, *Short-lived release credential*).
   * *Fork pull request workflows*: require approval for outside collaborators.
   * If your plan offers it, turn on *Require actions to be pinned to a full-length commit SHA*. Every action here is
     already SHA-pinned.
3. **Code security**: enable secret scanning / push protection if your plan offers it for private repositories.
   `scripts/secret-scan.mjs` (CI job `secret-scan`) is the tripwire that is always on.

### 2.3 Vercel (Project → Settings → Environment Variables, Production)

Production only — do not give Preview deployments the release secret:

* `APEX_RELEASE_HMAC_SECRET`: the same value as GitHub.
* `APEX_RELEASE_HMAC_SECRET_PREVIOUS`: only during a rotation.
* `APEX_MODEL_TRUSTED_KEYS`: public. The server refuses candidates signed by any other key.
* `APEX_MODE`.
* `APEX_RELEASE_GITHUB_REPOSITORY`: `owner/repo` of this repository. **Required outside DEMO**: without it every
  release call is refused with `503 RELEASE_OIDC_NOT_CONFIGURED`.
* optional `APEX_RELEASE_GITHUB_REF` (default `main`) and `APEX_RELEASE_OIDC_AUDIENCE` (default `apex-release`).

**Short-lived release credential (GitHub OIDC).** The release HMAC secret is long-lived, so it is not enough on its
own. Every release request must also carry `x-apex-oidc`: a GitHub-issued OIDC token. The workflow job mints a fresh
one per request (`permissions: id-token: write`); it lives minutes, and the server accepts each token once (its `jti`
is burnt until it expires).

The server verifies the token's RS256 signature against GitHub's published keys
(`token.actions.githubusercontent.com`, cached 1 h; kept for up to 24 h if GitHub is unreachable). It then requires
all of these:

* issuer `https://token.actions.githubusercontent.com`;
* audience `apex-release`;
* `repository` = `APEX_RELEASE_GITHUB_REPOSITORY`;
* `environment` = `model-release`;
* `ref` = `refs/heads/main`;
* event `schedule` or `workflow_dispatch`;
* the job's workflow file is an `.github/workflows/apex-model-*.yml` of this repository on `main`;
* lifetime ≤ 1 h, with ±60 s skew.

A stolen HMAC secret therefore cannot call the release API from outside these workflows. A pull request cannot mint
a matching token: `ci.yml` has no `id-token` permission, and PR runs have neither the environment nor `main`.

Two exceptions to the requirement:

* **DEMO.** The token is optional, so local replays and the DEMO seed work without GitHub. A token that *is* sent is
  still verified.
* **`APEX_RELEASE_OIDC=off`.** This is an explicit escape hatch for a host that cannot issue OIDC tokens. It falls back
  to HMAC only and logs a warning. Do not set it on a GitHub-hosted setup.

Each verified write is audited (`release.oidc_verified`, with run id, commit and workflow). Training-run rows take
their run id, attempt and commit from the signed claims, not from the request body.

The registry lives in Supabase Postgres:

* `apex.model_versions` holds metadata and the signed manifest.
* `apex.model_artifacts` holds the compact JSON artifact (≤ 2 MB, typically a few KB).
* Both have RLS enabled. They are reachable only through the server: the database URL (`DATABASE_URL`) exists only in Vercel, and no Supabase API key (anon or service-role) is used at all.
* Retention is champion + 2 other versions per strategy, pruned on promotion. That fits Supabase Free.
* A Supabase Storage bucket is **not** used: the artifacts are small enough for Postgres. This is a deliberate
  simplification.

### 2.4 The Pi

Put the **public** `APEX_MODEL_TRUSTED_KEYS` in the Pi's `.env` (chmod 600). The Pi then refuses unsigned, tampered,
incompatible or expired models and keeps the previous one (`apex model verify|install|rollback`). Pairing and the
periodic champion fetch (`models.policy.poll_minutes`, default 45) are part of the Pi worker.

### 2.5 GitHub spending limit: the only real billing safeguard

`apex budget check` is an **application-level forecast, not a billing safeguard**. It cannot see:

* minutes used by `ci.yml`;
* minutes used by other repositories on the account, or by coding-agent CI;
* GitHub's actual billing cycle.

Enforce the no-spend goal at the provider:

1. Open https://github.com/settings/billing → **Budgets and alerts** → **New budget**.
2. Choose *Product-level budget* → **Actions**, and set the amount to **$0**.
3. Select **Stop usage when budget limit is reached**, if available. Without it GitHub only emails you and usage is
   **not** stopped.
4. Enable the threshold alerts (75/90/100 %).

GitHub's billing docs (checked 2026-10-20, https://docs.github.com/en/billing/concepts/product-billing/github-actions)
add two points:

* "If your account does not have a valid payment method on file, usage is blocked once you use up your quota."
* GitHub sends email notifications at 90 % and 100 % of included Actions usage.

How-to: https://docs.github.com/en/billing/how-tos/set-up-budgets. Included allowances (2,000 min/month on Free,
3,000 on Pro, private repositories, standard runners) are listed at
https://docs.github.com/en/billing/reference/product-usage-included. Verify them at deployment; they are plan-wide and
shared.

## 3. Promotion policy

* **Default: MANUAL** (`models.policy.promotion_mode = "manual"`). The weekly train only *registers* candidates that
  passed the frozen local policy **and** beat the champion. The owner then promotes from the dashboard, or with
  *Actions → apex-model-release → Run workflow* (`promote`, model id, reason).
* **AUTO** (owner opts in on the dashboard, after independent backtests, risk review and stable shadow evaluation). The
  server promotes at registration when the candidate is `CALIBRATED`, and the weekly train also calls
  `apex-model-release.yml`. That call is idempotent ("already champion" is success) and re-verifies the registry.
* Frozen server policy, in every path:
  * `UNVALIDATED` is never promoted.
  * Holdout Brier skill must improve on the champion by ≥ `MIN_SKILL_GAIN` (0.005).
  * The economic p10 lower bound must not be worse than the champion's.
  * Expired validation is refused.
  * Synthetic/DEMO models are refused by a live registry, and the Pi refuses them outside DEMO mode.
  * CI has no `force` path.
* **Failed candidate (acceptance D)**: it is not uploaded, the incumbent is untouched, and the run reports
  `champion_retained` with the reasons in `report_json` and the job summary.
* **Rollback**: dashboard, or `apex-model-release` with `rollback` and the *current champion's* id. Rollback restores
  its recorded parent and writes an audit entry. The Pi re-verifies whatever it fetches.

## 4. Schedules, reliability and the missed-run SLA

GitHub documents an IANA `timezone` for schedule entries (checked 2026-10-20 on
https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows and
https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax):

* "By default, scheduled workflows run in UTC. You can optionally specify a timezone using an IANA timezone string for
  timezone-aware scheduling."
* Example: `cron: '30 5 * * 1-5'` with `timezone: "America/New_York"`.
* During DST spring-forward, "scheduled workflows in skipped hours advance to the next valid time". 19:17 and 20:23
  are never in a skipped hour.

So the wall-clock times are fixed in New York:

* train: 23:17 UTC in EDT, 00:17 UTC on Tuesday in EST;
* drift: 00:23 / 01:23 UTC on the following day.

The same pages warn that:

* schedules run only on the latest commit of the **default branch**;
* the shortest interval is 5 minutes;
* runs "can be delayed during periods of high loads", and "if the load is sufficiently high enough, some queued jobs
  may be dropped" — hence the off-the-hour minutes;
* in a public repository, schedules are disabled after 60 days without activity.

GitHub cron is therefore **not** a reliable clock. Missed-run SLA (`apex_worker.release.schedule.sla_status`, the
single source of the expected times):

| Workflow | Expected | Missed when no run is *reported* by |
|---|---|---|
| `train_weekly` | Monday 19:17 ET | +24 h (Tuesday 19:17 ET) |
| `drift` | weekdays 20:23 ET | +6 h |

`skipped_budget` / `skipped_holiday` reports count as reported: they are deliberate and visible. There are two detectors:

* `apex release sla`, run by the drift workflow. This is a secondary detector and only works while Actions runs.
* The Pi detector (primary, independent of Actions). This is concurrent Pi-worker work in `runtime/notices.py`, outside
  this change: it raises a `MISSED_TRAINING` system notice when the last *successful* training is older than 8 days.
  That matches the Monday slot + 24 h grace above.
* The dashboard. It should show the same status from `apex.training_runs`, or from `GET /api/v1/release/policy`
  `last_runs` with `sla_status`. That wiring belongs to the web app and is not part of this change.

When a run is missed:

* the Pi keeps the last validated model until its `validation_expires_at`;
* after expiry it emits no new high-conviction signals;
* nothing is promoted blindly.

Market holidays:

* The weekly train still runs on holiday Mondays. It uses completed sessions through the prior session, and skipping
  would only make the model a week staler.
* The scheduled drift run reports `skipped_holiday` on non-sessions.

## 5. Minutes budget

The forecast always uses the **maximum possible runtime = job `timeout-minutes` × scheduled runs**, never the average.
Runs are counted per UTC month: GitHub resets included minutes per billing cycle, and the calendar month is the
approximation. GitHub has historically billed each job rounded up to the whole minute, and the estimate assumes this
to stay conservative.

| Source | Timeout | Max runs / month | Max minutes / month |
|---|---|---|---|
| weekly train (`train`) | 45 | 5 | 225 |
| auto-release (`release`, AUTO only) | 10 | 5 | 50 |
| drift (`drift`) | 10 | 23 | 230 |
| **APEX model workflows, worst case** | | | **505** |
| `ci.yml` per PR update / push (4 parallel jobs) | 5 + 20 + 20 + 5 | event-driven | 50 per run worst case |

Default budget `budgets.actions_monthly_minutes` = **1500** of the 2,000 free. The 500 left over is headroom for
`ci.yml`, coding-agent CI and other repositories. Gate logic:

* **Required** (train, release): proceed while `measured + own timeout ≤ budget`. If measured usage is unknown, the run
  proceeds, bounded by its own timeout.
* **Optional** (drift): proceed only if `measured + own timeout + every remaining required run at its timeout ≤ budget`,
  `drift_enabled` is on, and measured usage is known. Otherwise exit 3, reported as `skipped_budget`.
* `measured` = the sum of `minutes_used` reported by APEX runs this month (`apex.training_runs`). It **excludes** ci.yml
  and other repositories, which is why the provider-side $0 budget is mandatory.

Measured so far (local sandbox, not a GitHub runner):

* a synthetic-fixture training run takes about 46 s of compute;
* the red-team training run takes a few seconds.

Runner setup (checkout, uv, locked install) is not measured. Real Alpaca runs are UNVERIFIED. Revise the timeouts once
real runs report `minutes_used`. Storage:

* evidence artifacts are a few tens of KB, kept 7 days;
* the history cache is a few MB per entry in the Actions cache (GitHub evicts entries not accessed for 7 days);
* the permanent metadata lives in Supabase.

## 6. Data and reproducibility

* **Provider**: Alpaca Basic daily bars, `feed=sip` by default. On Basic, SIP history older than 15 minutes is free, and
  the adapter clamps `end`. This matches the Pi's own daily refresh, so training and live features share one volume
  definition. `--history-feed iex` exists, but IEX is a single venue whose volume would skew the liquidity/volume
  features against the live scanner.
* **Universe**: a fixed, pre-registered research universe of 78 symbols (`research/training_data.py`):
  * SPY;
  * the 11 sector SPDR ETFs;
  * 6 long-history large caps per sector.
* **Bias warnings**: every report carries these:
  * survivorship;
  * current sector labels;
  * unknown earnings dates;
  * no catalysts in CI;
  * price-only returns.
* **Bounded**:
  * at most 8 years;
  * at most 300 requests per run (Alpaca Basic allows 200/min, and the adapter's bucket runs at 190/min);
  * at most 200 symbols.
  * Symbols beyond the request budget keep their verified cached bars and are listed as stale.
* **Incremental and checksummed**:
  * Only `overlap_sessions` before the last cached bar are re-fetched.
  * If the overlap disagrees, the symbol is re-fetched in full. This catches retroactive split re-adjustment.
  * Each file's sha256 is verified on read, and a corrupt file is re-fetched.
  * `history_hash` is recorded, and the manifest's `training_data_hash` (sha256 of the OHLCV arrays) is recorded too.
  * The cache is persisted with `actions/cache` (restore + save, keyed per run) inside the private repository. It is
    never redistributed. The checksums protect against corruption, not against someone with write access to `main`.
* **Determinism**:
  * The research code has fixed seeds (validation 20261020, bootstrap 11, baselines 7).
  * `PYTHONHASHSEED=0`.
  * Locked dependencies (`uv sync --locked`, `uv.lock`) and pinned uv 0.11.32 / Python 3.11.
  * Manifests record the code SHA and the feature schema version.

## 7. Security properties (acceptance J)

* `ci.yml` has no secrets and no environment, and the model workflows have no `pull_request` / `pull_request_target`
  trigger, so PR code never meets release credentials. The `model-release` environment is restricted to `main`, and
  the jobs also check `github.ref` against the default branch.
* Least privilege throughout:
  * `permissions: contents: read` on every workflow.
  * The only write scope is `id-token: write`. It is on the train, drift and release jobs (and on the reusable call,
    because a called workflow can only narrow its caller's token). It mints the short-lived OIDC token, not repository
    access, and `ci.yml` never has it (asserted by `tests/test_release_workflows.py`).
  * `persist-credentials: false` on checkout.
  * Third-party actions are pinned to full commit SHAs, resolved with `git ls-remote` with annotated tags dereferenced:
    * `actions/checkout` v7.0.1;
    * `actions/setup-node` v7.0.0;
    * `pnpm/action-setup` v6.1.0;
    * `astral-sh/setup-uv` v10.2.0;
    * `actions/cache` restore/save v6.1.0;
    * `actions/upload-artifact` v7.0.1.
* Workflow inputs reach scripts only through `env:`. This is a script-injection guard, enforced by
  `tests/test_release_workflows.py`.
* The signing key:
  * is exposed only to the single train step;
  * is popped from the process environment after it is read;
  * is never written to disk;
  * is never uploaded: artifacts contain only `report.json` / `summary.md` / `drift.*`, and tests assert it.
  * GitHub also masks secret values in logs.
* Requests are signed with APEX-HMAC-SHA256-V1: a ±60 s window, single-use nonces, and the body hash.
  * Every request also carries a fresh single-use GitHub OIDC token bound to this repository, `model-release`, `main`
    and an `apex-model-*` workflow (§2.3). Verified locally with a test RSA key only; see §9.
  * The client never follows redirects.
  * The base URL must be `https://`.
  * Secrets are masked in `repr`.
* Models are JSON only, never pickle.
  * The server verifies the signature, checksum, size and kind before storing anything.
  * The Pi verifies again before activation.
  * `force` is unreachable from CI.
* Registry writers serialise on the `apex-model-registry` concurrency group (acceptance I):
  * `cancel-in-progress: false`.
  * `queue: max`, a documented GitHub property that keeps every pending run instead of replacing it. actionlint 1.7.12
    does not know it yet; `.github/actionlint.yaml` ignores exactly that message.
  * The nested auto-release uses a per-caller group, because re-using the caller's group would deadlock.

## 8. Everyday operation (no laptop)

* **Run training now**: from any browser, *Actions → apex-model-train-weekly → Run workflow*, with a reason and a
  source.
  * A dashboard "Run Training Now" button needs a server-side GitHub dispatch credential, which is not configured. The
    button must stay DISABLED with these instructions until a backend dispatch exists. `requested_by=admin` is already
    accepted and reported as `admin_dispatch`.
* **Promote / roll back**: from the dashboard, or *Actions → apex-model-release*.
* **Rotate the signing key**:
  1. Add the new public key as version 2 to `APEX_MODEL_TRUSTED_KEYS` on Vercel, on the Pi and in the GitHub variable.
  2. Replace the `APEX_MODEL_SIGNING_KEY` secret and set `APEX_MODEL_SIGNER_VERSION=2`.
  3. After the next champion is signed with version 2, remove version 1.
* **Rotate the release HMAC secret**:
  1. Set `APEX_RELEASE_HMAC_SECRET_PREVIOUS` on Vercel to the old value.
  2. Update both `APEX_RELEASE_HMAC_SECRET` values.
  3. Remove the previous one later.
* **Budget low / quota exhausted**:
  * drift skips automatically;
  * the train is skipped once its worst case would exceed the budget;
  * GitHub's $0 budget stops metered usage;
  * the Pi keeps scanning with the last validated model until it expires.

## 9. Verification record

What was verified in the development sandbox on 2026-10-20 (no GitHub runner, no account secrets):

* **actionlint 1.7.12** (`uv tool run --from actionlint-py==1.7.12.25 actionlint`, with shellcheck 0.11.0): 0 errors
  on all four workflows. The only suppressed message is the documented `concurrency.queue` key.
* **Workflow YAML** (`tests/test_release_workflows.py`, PyYAML), which checks:
  * parsing;
  * read-only permissions, except `id-token: write` on the model-release jobs and never in `ci.yml`;
  * timeouts on every job;
  * SHA pins with version comments;
  * no PR triggers on the model workflows;
  * the default-branch + environment guard;
  * the signing key appearing in exactly one step;
  * artifact paths and retention ≤ 7 days;
  * registry concurrency;
  * cron + timezone matching `schedule.py`;
  * timeouts matching the budget profile;
  * no `${{ inputs.* }}` inside `run:`.
* **Python tests**: client signing/retries/config, upload/acceptance D/report-run/promote, budget/DST/SLA,
  history download (full → reuse → incremental → corrupt → split re-adjustment → request budget), the Alpaca loader
  feeding `run_training`, and drift.
* **GitHub OIDC factor** (`apps/web/tests/unit/oidc.test.ts`, `tests/integration/release-oidc.test.ts`, Python
  `tests/test_release_client.py`), with a locally generated RSA key and an injected key set:
  * a valid token is accepted;
  * these are rejected: wrong audience, issuer, repository, environment, ref or event; a non `apex-model-*`
    workflow; expired, not-yet-valid or over-long tokens; `alg: none` / HS256; forged signatures; unknown key ids
    (after one rate-limited refetch); replayed `jti`;
  * missing token outside DEMO is 401; unconfigured repository is 503; a key-set outage with nothing cached is 503;
    the `off` switch and DEMO-optional behaviour work;
  * the Python client fetches a fresh token per attempt.
* **Cross-language end to end** (`apps/web/tests/integration/python-cli.test.ts`, opt-in with `APEX_PY_E2E=1`): the
  Python `apex` CLI against the real Next.js
  release route handlers on PGlite with the real migrations. The flow was:
  1. policy;
  2. budget;
  3. champions;
  4. `train --sign`;
  5. upload (identical challenger: champion retained, incumbent untouched);
  6. forced registration (idempotent);
  7. promote ("already champion", registry re-verified; a wrong trusted key is detected);
  8. report-run (row in `apex.training_runs`);
  9. rollback;
  10. sla.

  A wrong HMAC secret got 401.

**UNVERIFIED**:

* any real GitHub Actions run;
* a real GitHub OIDC token: the claim names and formats (`job_workflow_ref`, `environment`, `ref`, `event_name`) follow
  GitHub's documentation but have only been tested with locally signed tokens. If a first real run is refused with
  `RELEASE_OIDC_INVALID (<reason>)`, `apex.security_events` names the failing check;
* the `model-release` environment and its secrets;
* GitHub's acceptance of the `timezone` and `queue` keys in a live parse (both are documented, and actionlint accepts
  `timezone`);
* real Alpaca downloads and their runtime and minutes;
* Vercel Deployment Protection behaviour on `/api/v1/release/*`;
* the GitHub budget configuration;
* dashboard rendering of these runs;
* the Pi-side missed-run notice end to end (concurrent work).
