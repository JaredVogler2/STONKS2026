# APEX PHASE 14 — MANDATORY REAL-EQUITY BACKTEST, RESEARCH REPRODUCIBILITY AND STRATEGY DISPOSITION

**Priority:** P0 RESEARCH — next highest-value task; does not authorize live/group signals.  
**Prepared:** 2026-10-21. **Source of truth:** current default `main` branch of `JaredVogler2/STONKS2026`, not an older PR snapshot.  
**Owner's explicit instruction:** Stop relying solely on synthetic stock-market simulations to assess predictive or economic strategy quality. **Run APEX's actual existing H1–H8 research code against real historical equity market data** and publish honest, independently inspectable results **inside this repository's single permanent research record**. This task must be performed in GitHub's hosted compute or an otherwise credentialed repo-owned environment, not require the owner's laptop. Do not promote any model merely because it trains successfully.

## Launch instructions for coding agent

You are the APEX quantitative research and integration owner. Read this file, `docs/TESTING_AND_METHODOLOGY_REPORT.md`, `docs/BACKTEST_REPORT.md`, `docs/AUTONOMOUS_TRAINING.md`, `docs/RELEASE_AUDIT.md`, the latest main branch and current CI status. Then ACTUALLY IMPLEMENT code, tests, research workflow and reports; do not stop after writing an analytical plan. Respect ongoing branches; use a separate reviewable PR for changes; preserve current functional code and security. This is an additive research execution task, not a rewrite.

**Critical finding verified by repository inspection:** The application already has most of the necessary real-data pipeline:
- `apps/pi-worker/src/apex_worker/research/training_data.py` defines an 78-symbol fixed universe: SPY benchmark, 11 sector ETFs, 66 currently listed liquid names (six per sector).
- `apps/pi-worker/src/apex_worker/market/history.py` defines bounded/checksummed daily-bar download (default `feed="sip"`, `adjustment="split"`, eight years, budgeted requests).
- `apps/pi-worker/src/apex_worker/cli.py` supports `apex train --source alpaca`; `apps/pi-worker/src/apex_worker/release/cli.py` reads `ALPACA_KEY_ID` and `ALPACA_SECRET_KEY` for training.
- `.github/workflows/apex-model-train-weekly.yml` already exposes manual `workflow_dispatch source=alpaca` but assumes an authenticated production release-policy API. The scheduled `apex-model-drift` run at 2026-10-21 05:01Z failed on **Release policy** before drift actually executed. Identify and fix that prerequisite independently without weakening authorization; do not make real-equities research depend unnecessarily on a deployed Vercel/Supabase registry.
- The current `docs/TESTING_AND_METHODOLOGY_REPORT.md` explains synthetic experiments, including economic-rejection and flawed Quality-vs-return correlation. Reported 636 Python / 101 web / 17 Playwright tests are engineering tests; none establish financial-market alpha.
- An authorized Alpaca connection has successfully retrieved **actual IEX AND historical SIP daily bars** for SPY, NVDA and AAPL in October 2025, and historical SIP bars for SPY/NVDA in January 2019. This establishes provider capability for those calls, not a successful 78-stock strategy backtest or entitlement for public redistribution. The owner will need to configure appropriate credentials as documented; never copy connected-tool credentials to GitHub/CI/chat.

## A. First deliverable: isolated real-equity research runner, no production dependencies

Implement a **manual-dispatch, bounded, CI-safe** workflow named `.github/workflows/apex-real-equities-research.yml`. Its sole purpose is reproducible, real-equity historical research. Do NOT require a working Vercel deployment, Supabase production DB, model-registry release policy, Pi machine pairing or Discord bot. No promotion or registration side effects.

- `workflow_dispatch` inputs: `source` = `alpaca` only for the real run; `years` (default 8, bounded), `experiment_id` (human label, sanitized), `universe` = `fixed78` initially; optional `time_start` and `time_end` if existing pipeline permits; `research_only=true` hardcoded.
- Use standard Linux hosted runner, pinned setup actions and pinned uv/python dependencies, least permissions `contents: read`, no long-lived deploy permission, no id-token needed for offline artifact-only experiment, concurrency bound, `timeout-minutes` and checkpoint budget before fetch; skip and report if account's reserved minutes unavailable. Do not use expensive larger runners.
- Access only **read-only Alpaca market-data credentials**, configured as GitHub Actions environment/repository secrets `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, never echoed or copied. It is acceptable to use a read-only/paper-data credential set. NO brokerage orders, broker-secret logging or trading.
- No synthetic fallback: if Alpaca keys absent/invalid, historical SIP inaccessible, history insufficient or provider rate-limited, job should **FAIL/SKIP with clear reason**, never quietly run `synthetic` and call it real.
- Fetch batched eligible historical **SIP daily bars older than current real-time delay** from Alpaca Basic where entitled; verify response, feed, split-adjustment, provenance, age, symbol/date coverage, trading calendar and value integrity. Do not silently switch to IEX-only for consolidated volume-based features.
- Cache historical bars only inside a private, license-compliant internal cache if allowed; do not check 8 years of raw vendor OHLCV into Git or public GitHub artifacts. Publish aggregated research metrics, hashes and compact row counts, not redistributed price bars.
- Add a CLI subcommand or simple script (prefer extending existing `apex train`) that **can produce an unsigned, unpromoted report** without `--sign`, release-policy calls, or backend API connectivity. Split research evaluation and production model promotion cleanly.
- Test it on a small mock adapter with **REAL-source emulation fixtures** first, then on a real sample where credentials are available; do not label fixture-run as live research.

## B. Dataset and bias inventory (REQUIRED BEFORE STRATEGY RESULTS)

Fix the experiment design BEFORE inspecting the final test block:
1. Universe initially = existing `fixed78`, document exact 78 tickers and sectors and separate benchmarks/ETF reference assets from **66 stock investment candidates**. ETFs should not be double counted as equity signal trades unless explicitly intended.
2. Actual sample window (requested 8 years, e.g. late 2018–2026 depending actual data and ETF inception) determined by retrieved history, not guessed. Report each asset's first/last date, number of sessions, missing data, duplicate/corrupt bars, corporate actions, adjustment method, abnormal zero-volume days and source fetch failures.
3. **Explicit biases**: survivorship from current constituents; present-day sector classification applied retrospectively; partial histories/inception (especially XLC/XLRE or symbols with corporate restructurings); unknown point-in-time earnings; no catalyst observations for H7/H3 if appropriate; only split-adjusted not dividend total return. Report any trade-generating strategy materially impaired by these missing inputs.
4. Use a chronological training/validation/calibration/untouched-final-holdout protocol with embargo by **NYSE sessions and overlapping label windows**; never tune on final holdout. Save boundaries in a versioned `experiment_manifest.json` before computing metrics.
5. Determine execution fill rules and costs a priori: next possible execution, conservative unfilled limit order behavior, slippage 0/10/25/50 bps EACH SIDE, target/stop same OHLC bar worst-first or UNRESOLVED, gap through stop, session-expiry for H=5/10/15/20, capped realistic risk. Does not claim to know real order fill sequence from daily OHLCV.
6. Test two robustness splits: (a) chronological unseen years, (b) sector/date clustering or entirely held-out sectors where feasible. Explicitly distinguish the universe sample's limits from statistically generalizable claims.
7. Compute exact number of QUALIFIED signals, completed trades, no-fills, stop-first/target-first/time-outs, per strategy and regime. Report null vs zero: H7 missing due to no catalysts is `NOT_EVALUABLE_MISSING_INPUT`, NOT failed alpha.

## C. Run the existing strategies and comparator baselines

For H1–H8 (including the already implemented H7), and at least SPY buy-and-hold, simple 20-day momentum, simple breakout and matched-random qualified entries:
- Record discovery count, eligibility count, triggered, quality gated, filled, complete-horizon outcome, training/calibration/test sample. Every registered strategy is shown even if zero trades.
- Compute mean/median NET return (% and R-multiple), positive-trade fraction, 5th/10th percentile **bootstrapped mean** net return where appropriately defined, lower-tail realized R, max drawdown of a comparable exposure portfolio, profit factor with adequate N, average hold, MFE/MAE, target-before-stop probability, precision/recall only where label is sensible, Brier skill and ECE where calibrated, and sensitivity to trading friction.
- Test simple benchmarks under comparable trading exposure and investable constraints; SPY buy-and-hold alone has different exposure and shouldn't be declared fair comparator to a low-frequency swing system.
- For model ranking evaluate Spearman Quality vs realized net R with sample size, dependence adjustment, confidence uncertainty and out-of-sample breakdown. A useful quality rank should not be assumed from its numerical label.
- Report conditional performance by bullish/choppy/bearish/vol shock regime, sector concentration, liquidity quartiles, earnings UNKNOWN, holding horizon, exit type and market-year.
- Report overlapping exposure and concurrent signal capital allocation: the same $1 cannot fund unlimited simultaneous trades.
- Present candidate/feature ablations for H1/H2/H4 where possible. For H7, report NOT_EVALUABLE until causal catalyst data present; **do not manufacture** historical catalysts or add delayed politician transaction as if known at execution.
- Track all attempted configurations including failures; avoid survivorship over research experiments and p-hacking.
- Compare net expectancy and practical trading frequency. Reject "80% winner" when average losses swamp small gains.

## D. Primary acceptance criterion: diagnostic report, not guaranteed alpha

Do NOT lower gates merely to graduate a model. Classify every strategy:
- `VALIDATED_REAL_OOS`: point-in-time real-data, sufficient independent sample, conservative executable positive expectancy, stable calibration and frozen gates. Even this remains research, not proof future profitability.
- `PROMISING_NEEDS_FORWARD_TEST`: economic/forecast results encouraging but sample/regime/data limitations preclude a live high-confidence claim.
- `FAILED_REAL_OOS`: robust negative expectancy or baseline inferior with adequate power.
- `NOT_EVALUABLE_MISSING_INPUT`: e.g. H7 without historical catalysts or H3 without event timing.
- `INSUFFICIENT_HISTORY`, `SOURCE_INVALID`, `UNVALIDATED` with explicit counts and reasons.

A `NO_TRADE` aggregate conclusion is valid. This task aims to learn which factors/entry/exit mechanics work on real prices and whether the current ranking predicts net performance. DO NOT auto-publish buy alerts, change the active champion, or use LIVE_GROUP/ADD/SELL permissions.

## E. Persistent results live in ONE central repository location

Create permanent structured experiment reports under `research-results/real-equities/<experiment_id>/`. GitHub PR must commit the compact, *nonlicensed* research summaries as source-controlled immutable records, never raw price history or credentials:

- `manifest.json`: current code commit SHA, runner details, source=alpaca, feed=sip, provider entitlement/time, adjustment, universe, actual date coverage, train/calibration/validation/test boundaries, label method, transaction costs, seeds, feature/model/strategy versions, dataset hash and warnings.
- `data_quality.json`: symbol coverage, status, missingness, split notes, stale date checks and survivorship warnings (no raw OHLCV).
- `strategy_metrics.json`: full H1–H8 + baselines with sample counts, OOS net expectancy, P10 confidence/uncertainty, economic pass/fail and reasons; use JSON null for missing values, no fictitious 0s.
- `trades_summary.csv`: counts and group-level summary only, **not vendor raw prices** or user positions. If individual replay trades are permitted contractually, restricted private artifact; otherwise omit trade-by-trade vendor data.
- `comparisons.md`: rich human report with a single summary table, distributional validation, charts as derived aggregate statistics where allowed, failed variants and honest recommendation.
- `recommendations.md`: keep / modify / retire / not-evaluable per hypothesis plus evidence; prioritized experiments/fixes and expected risk/benefit.
- `execution_log.md`: GitHub Actions run URL, job conclusions, start/end, data/API errors, steps actually executed, explicit NOT RUN tests and account secrets blockers (never secret values).
- Include `research-results/README.md` index linking every real-equity experiment, with clear separation from synthetic `docs/TESTING_AND_METHODOLOGY_REPORT.md`.

Output artifacts from Actions can be ephemeral (small, 7 days), but **the permanent non-sensitive report belongs under the versioned repository directory**. Use a reviewed PR to ingest/review summaries; do not give workflow a powerful auto-commit token to untrusted code or auto-push directly to main. If the agent has GitHub write permission, commit the compact report via a reviewable PR after actual execution. Otherwise provide generated artifact with exact SHA and instructions, and mark permanent publish PENDING.

## F. GitHub workflow and current operational blocker

The latest scheduled drift workflow run 39547226607 on 2026-10-21 **failed** at `Release policy` before the actual drift diagnostic. Latest normal CI run 39560906621 **passed**. Research must not be blocked by not-yet-provisioned production API secrets. Diagnose drift separately, avoid bypassing OIDC/HMAC, and report remediation; an independent research-only GitHub workflow is the correct first step.

If the GitHub release infrastructure lacks configured keys, clearly report `PRODUCTION_REGISTRY_UNCONFIGURED` and keep the research-only path functioning with just Alpaca read credentials. If the owner has not supplied Alpaca Actions secrets, provide exact secure GitHub UI instructions and a blocked status. **Never substitute connected ChatGPT Alpaca connector credentials for the repo's CI secrets.**

## G. Explicit staged execution sequence (DO NOT SKIP)

1. Read current HEAD and research pipeline, inspect risk methodology, relevant tests, scheduled workflow and prior research report. Write a short engineering implementation plan and frozen experiment protocol.
2. Implement standalone CLI/script + Actions dispatch workflow; no release dependencies. Add tests for no-keys failure, wrong feed, no future bars, dataset count, historical split, returns, horizon expiry, same-bar ambiguity, incomplete history, and label availability.
3. Run unit/integration/actionlint and a synthetic *smoke test* for the standalone workflow, LABEL DEMO.
4. If Alpaca credentials available in trusted GitHub Secrets, launch **real historical SIP** 78-symbol data download (or staged 10-symbol subset first) using `workflow_dispatch`; report exact symbol coverage, load status and provider response, no false success.
5. Execute H1–H8/backtesting + baselines on authentic source, with frozen chronological split and conservative costs. Never misuse synthetic fixtures in this run.
6. Produce the structured experiment reports, QA assertions, aggregate derived graphs and human summary. Disclose data/research limitations.
7. Make a separate PR with implementation and permanent compact results, keeping owner secrets/data confidential.
8. Post link to new PR and readout in the central GitHub tracking issue. Include pass/fail tests and genuine OOS results, even if all strategies fail.
9. Recommend next changes ranked by empirical evidence: data integrity first, then entry/stop/target mechanics and ranking calibration, then advanced catalyst and regime enhancements. NO untested H1/H2/H3 weighting changes yet.
10. Do not enable live high-confidence group signals or automatic model promotion without separately completing operational/compliance gates.

## H. Owner actions (ONE TIME ONLY; no recurring laptop)

To run under the account's GitHub Actions permissions:
- In repository Settings → Environments, configure a restricted `real-equities-research` environment (or a vetted secrets scope) restricted to trusted `main` dispatch for read-only data. Add `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY` *as secrets*, not repo files or chat messages.
- GitHub Actions budget guard: use free included runner and provider-side $0 billable spending controls where supported. Check budget shared across other repos. Never incur billable overage automatically.
- In Actions tab run `apex-real-equities-research` manually from trusted branch. This is an owner approval for resource use only, not permission to trade or broadcast derived prices.
- For full future unattended weekly retraining, separately configure production release environment, secure Vercel/Supabase model registry and owner-approved promotion policy. Research-only evaluation must function without this.
- No Raspberry Pi purchase or laptop needed for the initial GitHub cloud research run.

## Definition of DONE

A real GitHub-hosted workflow run from trusted source has downloaded genuine historical SIP bars for named liquid U.S. equities, documented coverage/provenance and all H1–H8 disposition states, executed genuine historical out-of-sample evaluations plus baselines with conservative costs, and produced persistent versioned research reports linked in `research-results/README.md`. A reviewer can check the run/commit/data-hash/manifest. If credentials or entitlement are missing, report exact blocker and do NOT claim a completed real-equities backtest. Synthetic results must remain distinctly labeled and not replace the real run.

## Guardrails

- Never expose private quote data, pricing-feed raw historical tables, keys or private member fill information in a GitHub issue or report.
- No automated trading orders, external broker credentials, group personalized guidance, auto model promotion or provider terms evasion.
- If model calibration/strategy fit still fails economic release policy, KEEP all high-conviction alerts disabled and present *why* with statistics, not marketing.
- Use only legal/licensed historical inputs, respecting Alpaca API market-data terms and free-tier quota.
