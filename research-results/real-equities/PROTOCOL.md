# Real-equity experiment protocol `realeq-protocol-1` (frozen)

- **Status:** frozen 2026-10-21. It was fixed before any real market data was downloaded or inspected for this project.
- **Machine copy:** `PROTOCOL` in [`apps/pi-worker/src/apex_worker/research/realeq/protocol.py`](../../apps/pi-worker/src/apex_worker/research/realeq/protocol.py).
- **Hash:** sha256 of its canonical JSON: `721d3e3e3b82a602e36429ac7e40a0c8d01240d81e5340cb25bdd700dc95d636`. A test fails if the code and this document disagree.
- **Changing the protocol:** any change creates a new version and needs a new experiment. Results from different versions are never pooled.
- **Mandate:** [Phase 14](../../build-prompts/14_REAL_EQUITIES_VALIDATION_EXECUTION.md).
- **Runner:** `apex research real-equities` (research only: it never signs, registers, promotes, messages or trades).
- **Workflow:** [`apex-real-equities-research.yml`](../../.github/workflows/apex-real-equities-research.yml).

## 1. Engineering plan (what was built and why)

| Need (Phase 14) | Implementation |
|---|---|
| Isolated, research-only path with no Vercel, Supabase, registry, Pi or Discord | New package `apex_worker.research.realeq` and the `apex research` CLI group. It never imports the release client, upload, model signing, Discord or order code, and a test enforces that. `apex train` is unchanged: research evaluation and production promotion stay separate. |
| Real Alpaca historical SIP only, with no synthetic fallback | Credentials are read only from `ALPACA_KEY_ID` and `ALPACA_SECRET_KEY`, which the workflow maps from the environment secrets `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY`. Missing credentials stop the run as `BLOCKED_ALPACA_ACTIONS_SECRETS_MISSING`. A 401/403, a persistent 429, a non-SIP feed or a future-dated bar stops it as `SOURCE_INVALID`. The DEMO fixture is a separate `--source fixture`: it is labelled everywhere and refused under `research-results/`. |
| Reuse the existing H1–H8 code | The runner calls the same `build_events`, which provides point-in-time features, `signals.levels`, `research.execution.simulate`, `walk_forward` and `evaluate_strategy` (promotion-policy-2). Only small additive hooks were added: a stage funnel, raw fill and exit prices, and an optional fixed holdout date. Defaults are unchanged. |
| No raw vendor data in Git or artifacts | Bars live in a temporary directory that is deleted when the step ends, and no Actions cache is used. The outputs contain only counts, dates, hashes and group-level aggregates. A test rejects price fields in the outputs. |
| Frozen design before results | `manifest.json` records boundaries, hashes, versions and this protocol. It is written before any metric is computed, and `strategy_metrics.json` quotes its sha256. |
| Production drift and weekly training failing on an unprovisioned registry | `apex release preflight` (no network) reports `PRODUCTION_REGISTRY_UNCONFIGURED`. Scheduled runs skip the registry steps with a job-summary notice; manual runs fail. HMAC and OIDC authentication are unchanged. |

## 2. Data

- **Source:** Alpaca market data, `feed=sip`, `adjustment=split`, `1Day` bars. The default history is 8 years, bounded to 3–10 years, ending at the latest completed NYSE session or at `time_end`.
- **Coverage:** the actual coverage comes from the retrieved bars, not from the request.
- **Fresh download:** each run downloads everything again into an ephemeral cache, so every symbol's raw provider response is inspected. Inspection counts rows, duplicates, invalid OHLC rows, zero-volume sessions and dates after the latest session.
- **Universe `fixed78`:**
  - SPY: benchmark and reference only.
  - The 11 sector SPDRs (XLK, XLF, XLE, XLV, XLI, XLY, XLP, XLU, XLB, XLRE, XLC): sector-relative features only, never traded.
  - The 66 member stocks, six per sector (`research/training_data.py`): the only traded candidates.
- **Run aborts:**
  - **BLOCKED:** credentials missing.
  - **SOURCE_INVALID:** authentication or entitlement failure, rate limit after retries, a feed other than SIP, any bar dated after the latest completed session, or more than 0.1% invalid raw rows.
  - **INSUFFICIENT_HISTORY:** the request budget ran out, SPY has fewer than 504 sessions, or fewer than 60 of the 66 stocks are usable.
- **Symbol exclusions:**
  - fewer than 260 sessions;
  - more than 3 sessions stale;
  - more than 2% of sessions missing within its own span;
  - rows dated off the NYSE calendar.
- **Flags only:** daily moves above 25% are flagged for review, never silently excluded. Where the plan exposes corporate actions, the split dates are cross-checked against the split-adjusted closes.
- **Disclosed biases:**
  - survivorship (today's large caps);
  - current sector labels applied to all history (XLC launched 2018-06-19);
  - no point-in-time earnings calendar, so `earnings_unknown=1` and no earnings veto;
  - no catalyst history;
  - price-only returns (dividends excluded).

## 3. Chronology

- **Decision window:** starts after 252 warm-up sessions, or at `time_start` if that is later. It ends 22 sessions before the last bar (2 entry sessions plus the 20-session longest horizon), so every decision has a complete label. Those final 22 sessions are reported as label-unavailable.
- **Development:** the first 80% of decision sessions. Inside development there are 4 expanding purged walk-forward folds, and the last 25% of each training window is held out for isotonic calibration. The model threshold is chosen on development out-of-sample predictions only.
- **Untouched holdout:** the last 20% of decision sessions, evaluated once per pre-registered configuration.
- **Embargo:**
  - 5 NYSE sessions;
  - every training label window must end before the session 5 sessions ahead of a test block;
  - overlapping 20-session labels are purged.
- **Robustness splits:**
  - (a) chronological: the holdout itself;
  - (b) sectors: group A is technology, energy, industrials, consumer staples, materials and communication services; group B is the other five. The report gives rule expectancy per group, plus a model trained on one group's development events and scored on the other group's holdout events.

## 4. Execution, labels and costs (a priori)

**Entry.**
- The decision is taken at session t's close plus 15 minutes.
- A limit order over [entry_min, entry_max] is valid for sessions t+1 and t+2:
  - an open inside the zone fills at the open;
  - an open above the zone with a low at or below entry_max fills at entry_max;
  - an open below the zone does not fill that session.
- There is never a fill at the decision close, and unfilled orders are counted.

**Exits.**
- **Fill session:** only the stop can trigger.
- **Gap through the stop:** exit at the (worse) open.
- **Gap above the target:** exit at the open.
- **Same bar touches target and stop:** counted as a **stop** (worst-first). The UNRESOLVED sensitivity, which excludes those trades, is also reported.
- **Time exit:** at the close of fill + H sessions.

**Horizons and costs.**
- Horizons: H ∈ {5, 10, 15, 20}; the primary horizon is **H = 20**.
- Costs: {0, 10, 25, 50} bps per side; the primary cost is **25 bps**.
- Net return: `(1+gross)(1−c)/(1+c) − 1`.
- R multiple: `(exit(1−c) − entry(1+c)) / (entry(1+c) − stop)`.

**Risk.**
- The stop sits 1–3 ATR from entry, and the minimum net reward/risk is 1.5 (`signals.levels`).
- Only one pending or open position is allowed per (strategy, symbol).

## 5. What is evaluated

- **Rule (primary economic test):** every qualified, regime-allowed, de-duplicated signal of the registered plugin.
- **Model:** the existing competing-risk model with the promotion-policy-2 gates, evaluated on the same frozen holdout. This covers Brier skill, ECE, a 20-permutation shuffled-label control, the threshold-selected trades, and precision/recall for TARGET.
- **Hypotheses:**
  - H1, H2, H4, H5 and H6 run as registered.
  - H3 and H7 require point-in-time catalysts, so they are `NOT_EVALUABLE_MISSING_INPUT` and no events are manufactured.
  - H8 is disabled by default as a weak prior. It is evaluated for research only and flagged as such.
- **Baselines** (same levels-from-ATR, execution and costs):
  - `momentum_20`: every fifth session, the top eligible stock by 20-session return;
  - `breakout_basic`: a close above the prior 20-session high;
  - **matched random:** 50 seeded draws with the same dates and count as each strategy, using random gate-passing stocks;
  - SPY buy-and-hold: 100% exposure, so not a fair comparator;
  - SPY over each trade's own window;
  - always-flat.
- **Metrics:**
  - signal funnel and fill, target, stop, time-exit, ambiguity and gap counts;
  - mean and median net return and R;
  - win rate;
  - weekly-block bootstrap of the mean (2000 draws, seed 11): p5, p10, p50, p90 and p95, plus a one-sided p-value;
  - R p10;
  - ES5;
  - profit factor (n ≥ 30);
  - MFE and MAE;
  - holding time;
  - cost and horizon grids;
  - breakdowns by regime, sector, liquidity quartile (development-set edges), year, exit type and earnings-unknown;
  - Spearman correlation of Quality against realized net R, with a weekly-block bootstrap 90% interval.
- **Portfolio:** 10 equal slots, sized at equity / 10 at entry; a signal is skipped when the slots are full. Positions are marked at daily closes at 25 bps.
- **Ablations (H1/H2/H4):**
  - all regimes;
  - Quality top half, with the cut taken from development;
  - the model without Quality features.
- **Configuration log:** every evaluated configuration is listed in `strategy_metrics.json`.

## 6. Dispositions (primary configuration only; Holm adjustment across evaluable hypotheses)

| Disposition | Rule |
|---|---|
| `NOT_EVALUABLE_MISSING_INPUT` | The plugin needs point-in-time catalysts; none are available in CI. |
| `INSUFFICIENT_HISTORY` | Fewer than 30 completed holdout rule trades, or fewer than 100 in development. |
| `VALIDATED_REAL_OOS` | All of the following on the holdout: n ≥ 100, at least 26 decision weeks and at least 2 regimes; mean net at 25 bps > 0 with bootstrap p10 > 0; mean net at 50 bps > 0; Holm-adjusted one-sided p < 0.10; mean above the matched-random 95th percentile; mean > 0 in both sector groups; forecast status not UNVALIDATED. This is still research, not proof of future profit. |
| `FAILED_REAL_OOS` | n ≥ 100 and either bootstrap p90 of the mean at 25 bps < 0, or a mean at or below the matched-random median. |
| `PROMISING_NEEDS_FORWARD_TEST` | Otherwise: mean at 25 bps > 0 and above the matched-random median, and either p10 > 0 or n < 100. |
| `UNVALIDATED` | Everything else (inconclusive). |
| `SOURCE_INVALID` / `INSUFFICIENT_HISTORY` / `BLOCKED_ALPACA_ACTIONS_SECRETS_MISSING` (run level) | No strategy evidence is produced and none is claimed. |

- **Aggregate:** `NO_TRADE` unless at least one hypothesis is `VALIDATED_REAL_OOS`.
- **Live alerts:** live, group and high-confidence alerts, model promotion and orders stay disabled whatever the result.
- **DEMO runs:** fixture runs can never be validated or promising.

## 7. Outputs (`research-results/real-equities/<experiment_id>/`)

- `manifest.json`: frozen before metrics.
- `data_quality.json`
- `strategy_metrics.json`: `null` for missing values, never fictitious zeros.
- `trades_summary.csv`: group counts and aggregates only, no prices.
- `comparisons.md`
- `recommendations.md`
- `execution_log.md`

The workflow uploads exactly these files as a 7-day artifact. A reviewed PR copies them into the repository. The workflow's token is read-only and it never commits.

## 8. Owner one-time setup (GitHub UI only; no laptop, no Pi)

1. **Create the environment.** Go to Settings → Environments → **New environment** and name it `real-equities-research`.
   - Under **Deployment branches and tags**, choose *Selected branches* and allow only `main`.
   - Optionally, add yourself as a required reviewer so every run needs your click.
2. **Add the two secrets.** In that environment, choose **Add environment secret** and add:
   - `ALPACA_API_KEY_ID`
   - `ALPACA_API_SECRET_KEY`

   Use a read-only, market-data-capable Alpaca key pair; paper-account keys work for historical data. Never put keys in files, issues, chat or PR text. Never reuse a connected-tool credential, such as a ChatGPT Alpaca connector, for CI. Repository-level secrets with the same names also work, but the environment is narrower.
3. **Set a spending guard.** In Settings → Billing → Budgets, keep Actions on the free included minutes with *Stop usage when the budget limit is reached*. The repository variable `APEX_ACTIONS_MONTHLY_BUDGET` (default 1500) feeds the pre-fetch checkpoint.
4. **Run the experiment.** Go to Actions → **apex-real-equities-research** → *Run workflow* on `main`.
   - Keep `source=alpaca`, `years=8` and `universe=fixed78`.
   - Choose an `experiment_id` such as `2026-10-fixed78-sip-8y`.
   - This click approves resource use only. It is not permission to trade or to broadcast prices.
5. **Publish the results.** Download the `apex-realeq-<run>` artifact, and open a PR that adds the seven files under `research-results/real-equities/<experiment_id>/` and links them from [`research-results/README.md`](../README.md).
