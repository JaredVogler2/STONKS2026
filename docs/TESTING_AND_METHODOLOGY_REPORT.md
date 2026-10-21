# APEX testing and methodology report (for the next research/engineering agent)

**Purpose.** This report is the complete evidence base for refining APEX's **stock-picking** and **price-point** methods
(entry zone, stop, target, horizon). It covers:
- what is implemented, exactly as the code does it;
- every test suite and what it actually proves, and what it does not;
- every research number produced so far;
- each known weakness, and the order in which to work on them.

It is written to be executed against by a coding agent, together with `amendments/` (on `main`) and
[METHODOLOGY_REFERENCE.md](METHODOLOGY_REFERENCE.md), the code-accurate formula reference with `file:line` for every
constant.

**Snapshot.** Code at `18224ee` (all merged to `main` through
[JaredVogler2/STONKS2026#4](https://github.com/JaredVogler2/STONKS2026/pull/4)), plus the diagnostics study in
`apps/pi-worker/bench/level_study.py`. Date 2026-10-21. Every number was produced by a command that was run. Nothing here
is a projection.

> **The one-paragraph state of play.** The software is broadly complete and well tested locally and in GitHub CI:
> - 636 Python tests, 101 web tests and 17 Playwright journeys pass;
> - 12/12 deterministic replay scenarios pass.
>
> The **research has not found an edge**. Every real hypothesis is UNVALIDATED, and the only data ever evaluated is
> synthetic. The live picking path also lacks three inputs the methods assume: earnings dates, catalysts and a sector map.
> The diagnostics study (§6) adds two sharper facts, both on synthetic development data:
> - the **Quality score does not rank net outcomes**, so the Quality ≥ 70/75 publication bars filter on noise;
> - a plain **20-day momentum baseline is not worse than any strategy**.
>
> Refinement therefore has two halves:
> 1. make the live and research paths measure the same thing on real data;
> 2. only then run a pre-registered, multiple-testing-controlled search over picking and level rules.
>
> Do not tune anything against the synthetic market or against the holdouts already inspected.

---

## 0. Ground rules for whoever acts on this report

These come from `amendments/README.md`, `amendments/01_CRITICAL_PRE_RELEASE.md` and `amendments/03_QUANT_RESEARCH_V2.md`.
They are binding.

1. **Never weaken a gate to produce alerts.** NO TRADE on synthetic data is the correct output. The promotion policy (`research/evaluate.py`, `promotion-policy-2`) may only become *stricter* without a new pre-registered protocol.
2. **The holdouts already looked at are burned.** Every synthetic holdout result in §5 has been inspected, so it cannot be used to judge a refinement. Use a new embargoed out-of-sample block, or a forward paper period, and freeze decisions before looking.
3. **Pre-register every experiment** in `research/experiments/<id>.md`, before running it, with:
   - the hypothesis and the causal mechanism;
   - the null hypothesis and the falsification rule;
   - the parameter grid and the **search budget**, i.e. the number of configurations;
   - the universe, period, costs and baselines;
   - the promotion gate.
4. **Count your tests.** Report how many configurations you tried. Correct for them: deflate the best result against the maximum over configurations under a null, or confirm it on a fresh holdout. `bench/level_study.py` (§6) shows one way to do this.
5. **Version every methodology change** and update the characterization tests in the same commit (`tests/test_levels_quality.py`, `tests/test_assessment.py`, `tests/test_feature_parity.py`). The versions:
   - levels: `LEVELS_VERSION`;
   - Quality: `QUALITY_VERSION`;
   - assessments: `ASSESSMENT_VERSION`;
   - signal policy: `POLICY_VERSION` in `signals/policy.py`;
   - promotion policy: `POLICY_VERSION` in `research/evaluate.py`;
   - features: `FEATURE_VERSION`.

   A model trained before a feature change must be refused by the Pi (feature-schema hash), not silently re-used.
6. **Train/serve parity is a release gate.** Any new model input goes through `signals/model_features.py`, and `tests/test_feature_parity.py` must stay green (APEX-001).
7. **Quality is a rank, never a probability.** Publish probabilities only from a CALIBRATED champion. Publish no uncertainty interval until a block-bootstrap interval with measured coverage exists (APEX-002).
8. **Economic expectancy beats accuracy.** A refinement that improves Brier skill but not the after-cost p10 of mean net return per trade, at 25 bps/side and 50 bps stress, is not an improvement.

---

## 1. How a stock becomes an alert today (one page)

```
daily split-adjusted bars (Alpaca SIP live; synthetic in research)
  └─ features/compute.py      45+ causal features (ATR, RS, squeeze percentile, base length, volume ratios …)
  └─ features/regime.py       rule-based 5-state regime probabilities (hand-set logits, not fitted)
  └─ signals/context.py       + cross-sectional ranks, sector ranks, squeeze_prev, catalyst counts
  └─ signals/candidates.py    universe gates ($10, $30M median $vol, 1M shares, 180 bars, ≤3.5 ATR extension,
                              risk-off breaker, earnings veto) → 8 trigger plugins (H1–H8, signals/strategies/library.py)
                              → Quality 0–100 (signals/quality.py: 6 evidence groups, coverage, penalties)
                              → one candidate per symbol (best Quality) → ≤3 PASS per sector
  └─ signals/levels.py        entry zone close −0.5 ATR … +min(0.5 ATR, 2 %), stop = strategy hint clamped to [1,3] ATR
                              below mid, target 2R capped 0.25 ATR under the prior-120-close high, net R/R ≥ 1.5
  └─ signals/policy.py        Quality ≥ 70 → caps (3/session, 1 new + 2 open per sector) → model forecast
                              (HIGH_CONVICTION only with a CALIBRATED, unexpired champion, p ≥ threshold, EV > 0)
                              else ≤1 RESEARCH_SETUP at Quality ≥ 75, else NO TRADE
  └─ signals/monitor.py       lifecycle replayed with research/execution.py (same simulator as backtests)
  └─ portfolio/assessment.py  private per-member status (thesis intact / trim / review / exit / time / add …)
```

**Research** (`research/pipeline.py`):
1. Label every gated trigger with the same simulator: fill only inside the zone on t+1..t+2; stop first on a same-bar touch; a gap through the stop exits at the open; time exit after 20 sessions.
2. Fit an L2 multinomial logit (target / stop / time) with isotonic calibration.
3. Validate with a purged, session-embargoed walk-forward and an untouched holdout (the last 20 % of decision dates).
4. Promote only through the frozen gates. 20 shuffled-label runs act as a permutation control.

The full formulas, every constant and the `file:line` of each are in [METHODOLOGY_REFERENCE.md](METHODOLOGY_REFERENCE.md).
Read §5 (strategies), §7 (Quality), §8 (levels), §10 (execution) and §17 (risks) there before changing anything.

---

## 2. Test inventory: what exists, what it proves, how to run it

**Python** (`apps/pi-worker`, `uv run pytest`): 636 tests, all passing. Statement coverage is 84 %, with
`apex_worker.contracts` (generated code) at 8 %.

| Area | Files (tests) | What it pins | Gaps |
|---|---|---|---|
| **Price levels and Quality** | `test_levels_quality.py` (29) | exact entry zone, stop clamp [1,3] ATR, 1.5 ATR fallback, 2R target, overhead-supply cap and secondary target, net R/R with costs, every rejection code, tick rounding; Quality transforms at neutral, coverage rule, each penalty, monotonicity, ceiling | characterization only: pins *current* behaviour, not correctness against markets |
| **Diagnostics study** | `test_level_study.py` (3) | the study runs, never reads holdout rows, and its production configuration matches the pipeline | smoke test on a small universe only |
| **Train/serve parity** | `test_feature_parity.py` (21) | all 17 model inputs equal between training rows and live candidates over 228 matched (symbol, strategy, session) pairs; fail-closed when the vector is missing; no unsupported interval | synthetic panel only; earnings/catalyst inputs are absent in both paths (see APEX-011/012) |
| **Execution / outcome model** | `test_execution.py` (9) | open-in-zone fill, limit touch, no fill below the zone, same-bar STOP first, gap-through-stop at the open, costs, data end | no partial fills or spread model |
| **Validation** | `test_validation_embargo.py` (8), `test_research_redteam.py` (7) | session-counted embargo across six NYSE holidays; planted alpha recovered OOS; shuffled labels collapse; holdout never used for fitting; permutation gate separates edge from noise; JSON-only model | no leakage-trap fixture with a deliberately future-leaking feature; no test of threshold-selection optimism |
| **Policy** | `test_signal_policy_caps.py` (2), replay scenarios | sector cap counts sectorless names; NO TRADE through replay | most `SignalPolicy.decide` branches are untested directly (scenario gap 8) |
| **Member assessments** | `test_assessment.py` (19) | every `assess-1` status, trim hysteresis 0.75/1.0 R, add only with a calibrated on-plan thesis, stale and unverified prices, notification policy | `GAP_BELOW_STOP` is never triggered live (see APEX-024) |
| **Features** | `test_features.py` (10) | causality (recomputing on truncated history gives identical values), selected formulas | exact ATR/RS/autocorr formulas are not asserted |
| **Market data** | `test_market_data.py` (28), `test_history_cache.py` (8) | Alpaca pagination, Retry-After, SIP 15-min clamp, IEX labelling, quote validation levels, quota governance, slot planner; incremental checksummed history | real Alpaca behaviour unverified |
| **Catalysts** | 12 files (216) | SEC, Form 4 (P vs A/S codes), congress delay labels, earnings, news dedupe, precision, SSRF-safe HTTP, scoring | **nothing feeds catalysts into picking** (APEX-012) |
| **Ledger** | `test_ledger.py` (18) | FIFO lots, partial exits, corrections, fees, decimal edge cases, shared TS/Python vectors | — |
| **Discord bot** | 5 files (89) | commands, signed components, idempotency, DM-blocked privacy, 429 handling, migration | no real gateway |
| **Pi connected mode** | `test_agent_client.py`, `test_pairing_and_secrets.py`, `test_model_sync.py`, `test_worker.py`, `test_connected_bot.py`, `test_remote_sink_backend.py`, `test_outbox_bridge_notices.py`, `test_agent_security.py` (96) | HMAC signing, spool and replay, pairing proof, secret envelopes, model sync verify/stage/rollback, notices | tested against the Python fake API; the real routes are covered by `pi-agent.test.ts` |
| **Cloud training / release** | `test_release_*.py` (60) | workflow invariants (permissions, pins, no PR secrets, OIDC only where needed), budget gate, upload, drift, OIDC token fetch | no workflow has run on GitHub |
| **Replay** | `apex replay --all` (12 scenarios) | publish → fill → target; same-bar stop; gap through stop; never filled; earnings veto; stale feed; DM blocked; duplicate click; oversell; corrupt model; expired model; Pi offline | — |

**Web** (`apps/web`, `pnpm test`): 101 tests (90 by default, plus 11 opt-in with `APEX_PY_E2E=1`). All pass on PGlite and
on a real Postgres under a non-superuser role (`APEX_TEST_DATABASE_URL`).

| File | Tests | What it pins |
|---|---|---|
| `tests/integration/server.test.ts` | 16 | RLS, append-only tables, member privacy, HMAC failure modes, encrypted settings, pairing, registry tampering, Flow D ledger, JSON storage |
| `tests/integration/adversarial.test.ts` | 14 | anonymous and spoofed identity, forged sessions, CSRF, oversize and nested bodies, revoked devices, cross-member access, SSRF, no secret echo |
| `tests/integration/release.test.ts` / `release-oidc.test.ts` | 4 / 6 | release routes; GitHub OIDC second factor (locally signed tokens only) |
| `tests/integration/pi-agent.test.ts` (opt-in) | 9 | the **real Pi worker against the real agent routes**: pairing, config, secrets, model sync and tamper rejection, outbox acks, three engine sessions, bot ledger |
| `tests/integration/python-cli.test.ts` (opt-in) | 2 | the Python release CLI (train → sign → upload → promote → rollback → report) against the real routes |
| `tests/unit/{calendar,ledger,oidc}.test.ts` | 14 / 16 / 20 | XNYS edge cases, FIFO vectors, OIDC verifier |

**Playwright** (`pnpm e2e`, local only, not in CI): 17 passed, 3 skipped by design. It covers every page at 1440 px and
390 px, axe WCAG 2.1 AA in dark and light, filters, position persistence, server-side validation, keyboard navigation,
and no secrets in bundles.

**CI on GitHub** (`.github/workflows/ci.yml`, green): secret scan, contracts/codegen drift, lint, typecheck, unit +
integration tests, DEMO build, ruff, pytest on Python 3.11, actionlint. Playwright and the opt-in cross-language suites
are **not** in CI.

How to run everything:
```bash
pnpm install --frozen-lockfile && (cd apps/pi-worker && uv sync --all-extras)
pnpm contracts:check && pnpm --filter @apex/web gen:check && pnpm lint && pnpm typecheck && pnpm test
APEX_PY_E2E=1 pnpm --filter @apex/web exec vitest run                  # + cross-language suites
APEX_TEST_DATABASE_URL=$(bash scripts/local-postgres.sh url) APEX_PY_E2E=1 pnpm --filter @apex/web exec vitest run
pnpm --filter @apex/web exec playwright test                            # e2e + axe
cd apps/pi-worker && uv run pytest -q && uv run ruff check src tests && uv run apex replay --all
uv run apex train --source synthetic --out /tmp/t && uv run apex train --source redteam --out /tmp/r
uv run python bench/level_study.py --out /tmp/level-study               # §6
```

---

## 3. Scenario evidence: what the required acceptance cases look like today

Every scenario in `build-prompts/12_TESTING_AND_RELEASE_GATES.md`, the cloud-training amendment A–L and
`amendments/08_TEST_PLAN_ACCEPTANCE_MATRIX.md` (Q01–Q36, E2E-A..F) was mapped to the test that proves it, by reading test
bodies. The full matrix is in [TEST_EVIDENCE_MATRIX.md](TEST_EVIDENCE_MATRIX.md).

| Matrix | Rows | COVERED | PARTIAL | Script only | NOT COVERED | N/A |
|---|---|---|---|---|---|---|
| Phase 12 + amendment A–L | 77 | 29 | 39 | 4 | 5 | 0 |
| Amendment 08 Q01–Q36 | 36 | 15 | 17 | 2 | 2 | 0 |
| Amendment 08 E2E-A..F | 6 | 0 | 5 | 0 | 0 | 1 |

- **Of the 23 P0 cases in amendment 08, 12 are COVERED and 11 are PARTIAL:** Q01, Q02, Q04, Q07, Q11, Q12, Q18, Q19, Q20, Q21 and Q30. None is wholly uncovered. §10 lists the concrete test that would close each one.
- **"PARTIAL" is strict.** It means some of the case is asserted, not that it fails. Q01 (train/serve parity) is PARTIAL only because the test compares input vectors, not the model's output or as-of/source labels. Its inputs already match exactly.
- **Not run in CI.** Playwright and the opt-in suites (`pi-agent.test.ts`, `python-cli.test.ts`) are evidence locally but not CI-enforced.
- **Behaviour handled in code but not asserted by any test** (worth a test before refactoring):
  - Wilder ATR and RS formulas;
  - ledger quantity and fee bounds;
  - the 10-minute pairing TTL and lockout;
  - the proxy refusing the bypass header outside machine routes;
  - SSRF rejection of IP literals and IPv6;
  - health thresholds;
  - the champion unique index plus row lock;
  - refusal of edited migrations;
  - the evaluation report's cost grid and regime gate;
  - systemd restart rules.

---

## 4. Methodology summary and where each knob lives

Full detail: [METHODOLOGY_REFERENCE.md](METHODOLOGY_REFERENCE.md). These are the numbers a picking or level refinement
would touch. All defaults today are hand-set starting hypotheses; none was fitted.

| Component | Current rule | Where |
|---|---|---|
| Universe | top 400 by 20-session median $ volume; ≥ $10; ≥ $30 M median $ volume; ≥ 1 M avg shares; ≥ 180 bars; no OTC, warrants, preferreds or leveraged | `market/universe.py`, `runtime/universe_job.py`, `signals/candidates.py` (`GateConfig`) |
| Hard gates | ≤ 3.5 ATR above SMA20; risk-off + shock probability < 0.6; earnings within 20 sessions vetoed (**only if an earnings provider exists**, see APEX-011) | `signals/candidates.py` |
| H1 VCP breakout | breakout over the 20-day pivot; prior squeeze percentile ≤ 0.35; base ≥ 10 sessions; depth ≤ 15 %; rel volume ≥ 1.3; RS > 0; above SMA50. Stop hint close − 1.6 ATR | `signals/strategies/library.py` H1 |
| H2 RS pullback | 60-day rank ≥ 0.75, above SMA200, −11 %…−3 % from the 60-day high, −1.2…0.3 ATR from **SMA20**, rising SMA50, reversal bar. Stop hint − 1.4 ATR | H2 |
| H3 post-event drift / H7 catalyst + compression | require catalysts. **Never fire anywhere today** (APEX-012) | H3, H7 |
| H4 oversold in uptrend | above SMA200, 5-day return ≤ −6 %, ≤ −1.5 ATR from SMA20, reversal bar. Stop hint − 1.2 ATR | H4 |
| H5 sector rotation | sector rank ≥ 0.7 and improving ≥ 0.3; stock 20-day rank ≥ 0.65 (across **all** stocks); breadth ≥ 0.5. **Cannot fire live** without a sector map (APEX-013) | H5 |
| H6 gap and hold | gap +2.5…10 %, close in the top half, rel volume ≥ 1.8. Stop hint = pre-gap close × 0.995 | H6 |
| H8 turn of month | disabled, weak prior | H8 |
| Quality | 0.4·setup + 0.6·weighted groups (price 1.0, sector 0.8, volume 0.8, volatility 0.6, regime 1.0, catalyst 0.6) → 50 + 50·blend, × (0.85 + 0.15·coverage), − penalties (thin liquidity, extension, failed breakouts, unknown earnings −5, negative catalyst −8) | `signals/quality.py` |
| Entry zone | close − 0.5 ATR … close + min(0.5 ATR, 2 %); valid 2 sessions | `signals/levels.py` |
| Stop | strategy hint, clamped to [mid − 3 ATR, mid − 1 ATR] | `signals/levels.py` |
| Target | mid + 2 × risk; capped at (prior 120-**close** high − 0.25 ATR) when that high is in the way; min net R/R 1.5 at 10 bps/side | `signals/levels.py`; resistance in `research/dataset.py` and `signals/candidates.py` |
| Horizon | 20 sessions from the fill | `research/execution.py`, `signals/levels.py` |
| Publication | Quality ≥ 70; ≤ 3 per session; ≤ 1 new and ≤ 2 open per sector; research setups ≥ 75, at most 1 per session | `signals/policy.py` |
| Model | multinomial logit on 18 inputs, L2 = 4, isotonic calibration on the last 25 % of training rows | `research/model.py`, `research/validation.py` |
| Promotion | holdout n ≥ 100 / 200, ≥ 50 dates, ≥ 2 regimes, Brier skill > 0, ECE ≤ 0.06, ≥ 30 selected trades, bootstrap p10 of mean net return at 25 bps > 0, mean shuffled skill ≤ 0.01, real skill > 95th percentile of 20 shuffles, challenger skill gain ≥ 0.005, 21-day validity | `research/evaluate.py` |

---

## 5. Research results to date (synthetic only)

Full write-up: [BACKTEST_REPORT.md](BACKTEST_REPORT.md).
- Data: synthetic fixture, 2016-01-04 → 2026-10, data hash `ba3067eea6ef5899`.
- Holdout from 2024-11-12.
- Results below are under `promotion-policy-2`, at 25 bps/side for the gates.

| Strategy | Holdout n | Brier skill | ECE | Shuffled q95 | Perm. p | Selected p10 net | Status |
|---|---|---|---|---|---|---|---|
| H1 vcp_breakout | 117 | 0.032 | 0.016 | 0.031 | 0.095 | −0.19 % | UNVALIDATED (economics) |
| H2 rs_pullback | 84 | −0.052 | 0.152 | 0.060 | 0.571 | −1.74 % | UNVALIDATED |
| H3, H4, H7 | 0 | — | — | — | — | — | UNVALIDATED (no holdout entries) |
| H5 sector_rotation | 784 | −0.073 | 0.059 | 0.005 | 1.000 | −3.06 % | UNVALIDATED |
| H6 gap_and_hold | 65 | 0.036 | 0.079 | 0.029 | 0.095 | −0.97 % | UNVALIDATED |
| Red team (planted edge) | 591 | 0.332 | 0.055 | −0.001 | 0.048 | +4.54 % | passes every gate (EXPERIMENTAL by synthetic cap) |

How to read these:
- **H1 is the only real candidate.** But two of its three walk-forward folds had negative skill, one of 20 shuffles beat it, and a plain 20-day momentum baseline earned a similar mean per trade (+0.70 % vs +0.89 %).
- **H1's edge is cost-fragile.** Its p10 is positive only at ≤ 10 bps/side.
- **The red team proves the pipeline finds a planted edge and rejects shuffled labels.**
- **Why the synthetic results look the way they do** (METHODOLOGY_REFERENCE §1.5):
  - The generator plants only three things: (1) compression bases whose breakouts succeed more often in bull/rebound regimes and carry a 2.2–3.5× day-1 volume surge (failures carry 1.1–1.5×); (2) persistent sector drift; (3) regime-persistent market drift and volatility.
  - It plants no post-earnings drift, gap continuation, mean reversion, calendar effect or catalysts.
  - So H3/H4/H6/H7/H8 *cannot* show an edge on synthetic data, and H1's skill is partly built in. **None of this says anything about real markets.**
- **Training/serving fixes did not change training.** After the APEX-001 parity fix the synthetic retrain is identical in every report section. After the APEX-004 embargo fix only 1–2 extra rows per strategy were purged, with no gate changes.

---

## 6. Price-point and selection diagnostics (development window only)

Produced by `apps/pi-worker/bench/level_study.py`. It reuses the production simulator, levels and validation split, and
**reads no holdout rows**. Synthetic data, so the method matters here more than the numbers.

Run: `uv run python bench/level_study.py --out <dir>`.
- **Speed:** about 3.5 min; `--quick` takes about 13 s and is only a smoke test.
- **Defaults:** 24 stocks per sector from 2016-01-04, 6,024 events, 72 configurations per strategy.
- **Output:** `level_study.json` and a readable `level_study.md` with every table, not committed. Regenerate them.
- **Smoke test:** `tests/test_level_study.py`.
- **Consistency:** the production configuration in the grid reproduces the pipeline's development rows exactly (0 mismatches in levels, outcomes, returns and `stop_dist_atr`).
- **Development cutoffs** (decision dates strictly before): H1 2024-11-12, H2 2025-01-17, H4 2025-03-31, H5 2025-04-04, H6 2024-11-29. H3 and H7 produce no events; H8 is disabled.

### 6.1 Headline

**At production levels no strategy has a positive edge on the development window**, and **no grid configuration
survives the multiple-testing deflation** for any strategy or baseline. Production levels are the structural stop, a 2R
target and a 20-session horizon. The deflation uses two tests:
- a sign-flip reality check over all 72 configurations;
- a Bonferroni z threshold of 3.20 at 5 %.

The Quality score does not rank net outcomes. This is synthetic data, so it says how the *method* behaves, not how
markets behave. It still matters: with planted structure, a good ranking should have found it.

### 6.2 Excursions and price points

How to read the table:
- Returns are mean net per trade at 25 bps/side, with the weekly block-bootstrap p10 in brackets.
- **Stop too tight** is the share of stopped trades that later reached the original target within the horizon.
- **Run past target** is the median extra move after a target hit.

| Strategy | n | MFE p50 (R / ATR) | MAE p50 (R / ATR) | Stop too tight | Run past target | Production: target / stop first, return | Best configuration, return | Reality-check p (best vs 0) | Reality-check p (best vs prod.) |
|---|---|---|---|---|---|---|---|---|---|
| H1 | 460 | 0.96 / 1.47 | −1.04 / −1.64 | 7.4 % (19/257) | 1.06 R | 30.4 % / 55.9 %, −0.10 % (−0.52 %) | 3 ATR, 3R, 10 sessions: +0.13 % (−0.34 %), 80 % time exits | 0.70 | 0.70 |
| H2 | 335 | 0.89 / 1.30 | −1.03 / −1.44 | 5.0 % | 0.70 R | 23.6 % / 59.4 %, −0.97 % (−1.48 %) | 1 ATR, 1.5R, 20: −0.45 % | 0.99 | 0.11 |
| H4 | 119 | 1.11 / 1.17 | −1.10 / −1.29 | 7.8 % | 0.60 R | 28.6 % / 64.7 %, −0.63 % (−1.41 %) | 1 ATR, 1.5R, 10: −0.29 % | 0.94 | 0.75 |
| H5 | 3,795 | 0.96 / 1.44 | −1.03 / −1.52 | 8.1 % (174/2,151) | 0.90 R (47 % ran ≥ 1R further) | 24.3 % / 56.7 %, −0.52 % (−0.79 %) | 2.5 ATR, 3R, 20: −0.27 % (−0.71 %), 58 % time exits | 0.99 | 0.24 |
| H6 | 235 | 0.68 / 1.22 | −1.02 / −1.52 | 8.4 % | 0.71 R | 19.1 % / 55.7 %, −0.83 % (−1.50 %) | 1.5 ATR, 2.5R, 20: −0.58 % | 0.99 | 0.72 |

The production configuration alone (no search) has z between −2.6 and −0.3 for every strategy.

Other measurements:
- **Excursion shape.** Winners' median adverse excursion is only −0.30R to −0.41R. Losers' median favourable excursion is +0.34R to +0.54R. Target hits take a median 8–11 sessions; stop-outs 4–5.
- **Structural stop distance (p50).** H1 1.60, H2 1.40, H4 1.20, H5 1.50, H6 1.71 ATR. These are mostly the strategies' own hints, not the clamp.
- **Entry.**
  - Fill rate is 100 %, and next-open market entry minus zone entry is about 0.000 % per trade, so the zone almost never binds. Gaps are tiny in this generator, so this says little about real markets.
  - Fills in the **upper third** of the zone do markedly worse: H5 −1.36 % vs +0.08 % in the lower third; H1 −1.91 % vs +1.32 % (only 40 trades each).
- **Regime.** Setups in regimes a strategy does not allow are worse for H2 (−1.90 % vs −0.75 %) and H6 (−2.22 % vs −0.56 %). That supports aligning research with the live regime WATCH (APEX-014).
- **Baselines** (production levels, before 2024-11-12):

  | Baseline | Mean net per trade | n | Best configuration | Reality-check p |
  |---|---|---|---|---|
  | random_qualified | −0.26 % | 249 | +0.38 % | 0.50 |
  | momentum_20 | −0.03 % | 220 | +0.64 % | 0.42 |
  | breakout_basic | −0.39 % | 4,854 | −0.36 % | 0.999 |

  H1 on the same window: −0.10 %. **The simple momentum baseline is not worse than any strategy.**

**What this means for the price-point program (§8 step 4):**
- **Stops are not obviously too tight.** Only 5–8 % of stopped trades would have reached the target. Widening stops trades that small gain for larger losses; the best configurations with wide stops are mostly time exits. L1 has a low prior.
- **Targets may be too close for trend strategies.** H1 runs a median 1.06R past target, and 47 % of H5 target hits ran ≥ 1R further. L2 (scaled exit or a trailing remainder) has the strongest prior.
- **Entry location matters more than zone width.** Upper-third fills are much worse. Test an L5 variant with a tighter top of the zone, i.e. no chasing above the close.
- **The 1.5R configurations are all rejected by the production minimum net R/R filter.** The study disables that filter so every configuration scores the same setups. Up to 39 % of 3R targets are capped by the overhead-supply rule (APEX-015).
- **Whatever is tested must beat momentum_20 on the same dates.**

### 6.3 Does Quality, or the model, rank outcomes?

Spearman correlation of Quality with net R, with the weekly-bootstrap 90 % interval in brackets. "Steps up" counts how
often mean net R rises from one decile to the next (quintiles when n < 200).

| Strategy | Spearman vs net R | Steps up | Notes |
|---|---|---|---|
| H1 | 0.035 [−0.040, 0.116] | 5/9 | top minus bottom decile +0.52R, but the 80+ bucket is worse than 70–80 |
| H2 | −0.035 [−0.153, 0.069] | 4/9 | the Quality ≥ 70 slice (−1.30 %, n 58) is **worse** than all entries; `g_regime` 0.13 [0.04, 0.22] |
| H4 | −0.113 [−0.254, 0.026] | 3/4 | only 6 entries ≥ 70 |
| H5 | −0.011 [−0.057, 0.036] | 5/9 | ranks **target-first rate**: decile bin Spearman 0.76, 32.1 % target-first at 80+ vs 22.0 % at 60–70. But not net return: the ≥ 70 slice moves only from −0.52 % to −0.43 %. `setup_score` 0.095 [0.04, 0.15] and `g_volume` 0.053 are positive; `g_volatility` −0.042 |
| H6 | 0.042 [−0.067, 0.155] | 5/9 | top minus bottom −0.23R |

**Out-of-fold model** (development folds only): miscalibrated, with negative skill everywhere.

| Strategy | ECE | Brier skill | Spearman of p-target | Other |
|---|---|---|---|---|
| H1 | 0.083 | −0.044 | −0.10 | |
| H5 | 0.118 | −0.046 | 0.005 | predicted 0.219 vs observed 0.263 |
| H2 | 0.092 | | | |
| H6 | 0.099 | | | |
| H4 | | | | no out-of-fold predictions (no fold reached the minimum training size) |

**What this means for the stock-picking program (§8 step 5):**
- **The Quality ≥ 70 / 75 publication bars select on a score that does not predict net outcome.** That is the most important finding for picking. Quality can stay as an *explanation* of a setup, but as a *filter* it needs re-derivation on real data:
  - fit group weights as a regularized model on development data only;
  - or keep only the components that show a real out-of-sample association (here `setup_score`, `g_volume`, `g_regime`);
  - then confirm on a sealed holdout (S1).
- **Ranking hit rate is not ranking payoff.** H5 shows a score that orders target-first rates without improving return. Evaluate any picking score on economic outcome, not hit rate (amendment 03 §5).
- **Test 6–7 scores per strategy.** Bonferroni needs p < 0.0071 per score at 5 %.

### 6.4 Caveats specific to the study

- **Overlapping trades.** These are per-trade means with no portfolio constraint.
- **Daily-bar approximations.** The simulator is stop-first on a same-bar touch, and MFE includes the fill bar's high.
- **Outcome windows crossing the cutoff.** The last development setups use bars after the cutoff (H5: 77). Re-scoring without them barely changes anything.
- **The quick run is not evidence.** On a 4-per-sector universe from 2022, `--quick` showed large apparent edges (H5 best +2.1 %, reality-check p 0.002) that vanish on the full data. This is a live example of why small samples and searches need the deflation.

---

## 7. Findings register: confirmed, fixed and open

The first block continues `amendments/00_EVIDENCE_REGISTER.md`. APEX-011 and later are **new findings from this audit**,
proposed for the register. Each was traced in source at `18224ee`.
- **Severity:** P0 blocks real high-confidence alerts; P1 is the first V2 iteration; P2 is an improvement.
- **Status:** FIXED (commit), OPEN, or DOC (documentation mismatch only).

### 7.1 From the amendment register

| ID | Finding | Severity | Status |
|---|---|---|---|
| APEX-001 | Live `squeeze_prev` was `None` (imputed to the training mean); regime probabilities and 5 inputs were rounded live | P0 | **FIXED `bd3dbdf`**: one shared assembler (`signals/model_features.py`), fail-closed, parity test over 228 pairs |
| APEX-002 | Per-alert 80/95 % "intervals" were binomial SEs of the holdout count; `effective_sample_size` = raw n | P0 while advertised | **FIXED `bd3dbdf`**: point estimate labelled "interval not established", no interval published |
| APEX-003 | H7 absent from the report | P1 | OPEN. H7 is implemented and reports a row with 0 entries, because no catalysts are passed (APEX-012). A per-stage funnel is not reported. |
| APEX-004 | Embargo in weekdays (`BDay`) | P1 | **FIXED `fc5b8c9`**: exchange sessions; tested across 6 holidays |
| APEX-005 | Survivorship in the historical universe | P1 | OPEN. The CI universe is 66 current large caps. |
| APEX-006 | No demonstrated edge on real equities | P0 for real alerts | OPEN. No real-data run has ever happened. |
| APEX-007 | Cross-service operations not verified live | P1 | OPEN. Nothing is deployed; no workflow has run. |
| APEX-008 | Dependence and effective sample size | P1 | OPEN. Weekly-cluster bootstrap only; `effective_sample_size` is unused. |
| APEX-009 | Feed and eligibility labelling | P0 before public display | PARTIAL. Labels exist; group redistribution is gated OFF. |
| APEX-010 | Multi-member privacy invariants | P0 if reproduced | Not reproduced. Adversarial and RLS tests pass on real Postgres. |

### 7.2 New: live and research measure different things

| ID | Finding | Evidence | Severity | Suggested fix |
|---|---|---|---|---|
| APEX-011 | **No live earnings provider.** `build_worker` passes no `earnings_status`, so every live candidate is `UNKNOWN`. Effects: Quality −5 on every live candidate, `earnings_unknown = 1`, **the earnings veto never fires live**, and `ADD_CRITERIA_MET` is unreachable. Synthetic training uses known earnings dates, so for synthetic-trained models `earnings_unknown` differs between train and serve. | `runtime/worker.py` (Worker built without `earnings_status`); `signals/candidates.py` | P0 for HIGH_CONVICTION (RISK_MODEL promises the veto); P1 otherwise | wire `catalysts/sources/earnings` into `earnings_status(symbol, day, horizon)`. Until then, document that live alerts carry "earnings unknown" and no veto. Train real models only with UNKNOWN-consistent inputs. |
| APEX-012 | **No catalysts reach picking anywhere.** Live: no `catalysts_provider`. Research: `cats=[]`. Also no caller sets `score_at`, so `impact_score` would be NaN even if wired. H3 and H7 are dead paths; the Quality catalyst group is always absent. | `runtime/engine.py`, `runtime/worker.py`, `research/pipeline.py`, `catalysts/pipeline.py` | P1 | wire the catalyst pipeline with point-in-time `available_at` and per-decision scoring. Then add the H7 per-stage funnel and the H1 / catalyst-only / H1×catalyst ablation (amendment 03 §4). |
| APEX-013 | **No live sector map.** Nothing writes `sectors.json`, and Alpaca assets carry `sector=None`. Effects live: no `rs_sector_20` or sector ranks; H5 cannot fire; the sector Quality group shrinks to RS vs the benchmark; all names share one sector bucket, so at most 1 new signal per session and crowding WATCH from the 4th PASS. | `runtime/worker.py` (reads, never writes); `market/adapters/alpaca.py` | P1 | ship a versioned symbol→sector map (SPDR holdings or SIC→GICS-like mapping), refreshed nightly, with an unknown-sector fallback |
| APEX-014 | **The research population is not the published population.** Research labels every gated trigger: no regime filter, no Quality ≥ 70/75, no one-per-symbol dedup, no sector or session caps, no open-signal exclusion. Live publishes a filtered subset. Holdout calibration therefore describes a different population from the alerts. | `research/dataset.py` vs `signals/policy.py` | P1 | add `apply_live_policy=True` to `build_events` (reuse `SignalPolicy` caps by date) and report both populations |
| APEX-015 | **The overhead-supply cap rejects most pullbacks.** The target is capped under the prior **120-close** high, and the setup is rejected unless net R/R ≥ 1.5. A pullback 3–11 % below its 60-day high (H2) or an oversold dip (H4) almost always has that high inside 1.5R, so it is rejected. This is structural, not evidence-based. | `signals/levels.py`; arithmetic in METHODOLOGY_REFERENCE §8.4; H2/H4 holdout counts | P1 | pre-registered experiment: highs vs closes; 60/120/250 lookback; "cap and accept" vs "reject"; scaled-out targets |
| APEX-016 | **Baselines reuse the first strategy's decision dates for every strategy**, skip the earnings veto, include ETFs, and the buy-and-hold benchmark is never computed. | `research/pipeline.py` (`base` built once); `research/dataset.py baseline_events`; `bench_excess` never passed | P1 | build baselines per strategy on that strategy's dates with the same gates; wire `benchmark_window_returns` |
| APEX-017 | **Split adjustment applied "as of now"** lets future splits leak into the $10 / $30 M / 1 M-share gates and tick rounding for older history | `market/adapters/alpaca.py` (`adjustment=split`); `demo/synthetic.py adjusted()` | P0C (causality) | gate on raw (unadjusted) price and volume as of t; keep adjusted prices for returns only |
| APEX-018 | **Live entry window configurable 1–5 sessions, but labels assume 2** | `runtime/worker.py`; `research/execution.py` | P1 | stamp `entry_valid_sessions` in the manifest and refuse forecasts when live ≠ trained |
| APEX-019 | **The live decision bar may be incomplete.** The scan runs at close + 15 min, but SIP requests are clamped to now − 16 min, possibly before the closing auction | `market/adapters/alpaca.py`; `runtime/worker.py` | P1 (verify) | run the end-of-day scan after the 15-minute SIP delay has covered the close (≥ 16:16 ET), or use the daily bar's own end time; add a test |
| APEX-020 | **Champion comparison uses stored skills from different windows and data** | `research/evaluate.py challenger_beats_champion`; `apps/web/src/server/services/models.ts` | P1 | re-score the champion on the challenger's holdout inside the training job |

### 7.3 New: statistical method

| ID | Finding | Severity | Suggested fix |
|---|---|---|---|
| APEX-021 | **No family-wise control across 7 strategies.** Any one passing is promotable; 25 thresholds are picked on dev OOS; the permutation gate has 20 draws (min p ≈ 0.048); weekly retraining reuses a rolling 20 % holdout | P1 | Holm or Bonferroni across strategies; ≥ 200 permutations for the promotion run; a fixed forward holdout that weekly retraining may not touch |
| APEX-022 | **Calibration fitted on an unpurged split.** The calibrator is fitted on the last 25 % of training rows by row order, without a purge | P1 | purge and embargo between the fit and calibration blocks; compare isotonic with Platt (amendment 03 §3) |
| APEX-023 | **Right-censoring treated as TIME.** DATA_END is labelled TIME, biasing the last ~20 holdout sessions | P1 | drop censored rows from the label set (or model them), and report the count |
| APEX-024 | **Gap-exit assessment branch is dead live.** `session_open_price` is never set, so `GAP_BELOW_STOP` and the gap-driven CONSIDER_EXIT cannot happen | P1 | pass the session open from the bar cache into `AssessContext` |
| APEX-025 | **After a >12 % gap a symbol stays stale.** The outlier quarantine compares against the last *accepted* price, so after a large real gap every print is rejected and the symbol stays STALE_DATA | P1 | re-anchor on the daily bar or a second confirming print; add a test |
| APEX-026 | **Drift criteria in the manifest are not enforced on the Pi** (`max_ece`, `min_brier_skill`, `max_feature_psi`) | P2 | enforce them in `models/sync.py` health checks, or document them as dashboard-only |

### 7.4 Documentation mismatches (code wins)

Each item is fixed in the doc, or the code is changed deliberately, with a version bump. Full list: METHODOLOGY_REFERENCE,
search for `DOC≠CODE`.
- **H2** measures distance to **SMA20**, not SMA50.
- **H5** ranks across all stocks, not within the sector.
- **Overhead supply** uses closes, not intraday highs.
- **Penalty caps.** `RISK_MODEL.md`'s "up to 6 / up to 10" are effectively 2.4 / 4.0 after the gates.
- **Entry fills.** An open below the zone never fills that session, whereas the docs say "fills if price trades inside the zone".
- **Expected value** is computed in price units, not R.
- **Research setups** from a validated model do carry `p_target_first`.
- **`r_multiple`** ("calibrated multiple") is never passed by any caller.
- **Gates.** `market.universe` settings change only the nightly universe, not the scan gates.
- **H3 placebo and H7 component comparisons** are described but not implemented.

---

## 8. Refinement program for stock picking and price points

The order matters: steps 1–3 make the evidence trustworthy, steps 4–6 search for improvements, step 7 validates.

### Step 1: one measurement, live and research (required before any tuning)

1. Wire **earnings** (APEX-011), **sector map** (APEX-013) and **catalysts** (APEX-012), with point-in-time `available_at`, into **both** `runtime/worker.py` and `research/pipeline.py`. Extend `tests/test_feature_parity.py` so the earnings and catalyst inputs are compared too.
2. Make the research population optionally equal to the published one (APEX-014). Report metrics for both "all triggers" and "would have been published".
3. Fix causality items APEX-017 (raw-price gates), APEX-019 (complete decision bar) and APEX-023 (censoring).
4. Per-strategy baselines on the same dates, plus a wired benchmark (APEX-016).

**Acceptance.**
- `tests/test_feature_parity.py` covers all inputs including earnings and catalysts.
- A deliberate look-ahead fixture fails CI (amendment 01 P0C).
- The baseline table differs per strategy.

### Step 2: real data, frozen protocol

1. **Data.** Use Alpaca SIP daily history (`market/history.py`, `research/training_data.py`) over ≥ 8 years and a documented universe. State survivorship limits loudly (APEX-005); add a worst-case delisting sensitivity.
2. **Pre-register** in `research/experiments/`:
   - the universe, period and chronological split, with a **new** final holdout never inspected before;
   - costs of 10 / 25 / 50 bps per side;
   - baselines: random qualified, momentum_20, breakout_basic, buy-and-hold;
   - the search budget per experiment.
3. **First run.** Every strategy at current parameters, with no tuning, reported per stage:
   - triggered;
   - gated;
   - Quality ok;
   - levels ok;
   - filled;
   - outcome.

   This is the true v1 baseline. Use `uv run apex train --source alpaca` locally, or the `apex-model-train-weekly` workflow.

### Step 3: honest uncertainty

- Implement a time + sector block bootstrap with refit for the selected-trade mean and for calibration bins (APEX-002, APEX-008).
- Measure coverage on held-out blocks, and report effective sample size from the clustering, not the raw n.
- Only then consider re-introducing intervals, with their coverage shown.

### Step 4: price-point experiments (levels-2)

Use the development window only and keep the holdout sealed. For each experiment report:
- target-first, stop-first and time rates;
- mean net R and mean net return at 10 / 25 / 50 bps;
- the weekly-block bootstrap p10;
- MAE and MFE distributions;
- the deflated significance of the best configuration vs production.

Start from the §6 diagnostics.

| # | Experiment | Hypothesis / why | Search budget |
|---|---|---|---|
| L1 | **Stop distance** {1.0, 1.5, 2.0, 2.5, 3.0} ATR and structural vs ATR-only | the §6 stop-too-tight share tells whether stops sit inside normal noise | 6 |
| L2 | **Target R** {1.5, 2, 2.5, 3} and **scaled exit** (½ at 1.5R, rest at 3R or a trailing stop) | the §6 MFE-beyond-target share tells whether winners are cut short | 6 |
| L3 | **Overhead cap** (APEX-015): highs vs closes, lookback 60/120/250, cap-and-accept vs reject | the current cap mechanically removes pullback setups | 6 |
| L4 | **Horizon** {10, 15, 20} sessions | per-strategy time-to-exit histograms | 3 |
| L5 | **Entry**: zone width {0.25, 0.5, 0.75} ATR; next-open market entry; limit at mid | fill rate vs adverse selection of fills | 4 |
| L6 | **Gap risk**: exclude entries with earnings inside the horizon once earnings exist | gap-through-stop share of losses | 2 |

Rules:
- At most about 30 configurations in total for levels-2, declared up front.
- Pick at most one configuration per strategy.
- Confirm it on the sealed holdout **once**. If it fails there, the result is a failure: document it and do not try again on that holdout.
- Ship as `LEVELS_VERSION="levels-2"`, with the characterization tests updated in the same commit.

### Step 5: stock-picking experiments (quality-2, strategy v1.1)

| # | Experiment | Why |
|---|---|---|
| S1 | **Does Quality rank outcomes?** The §6 decile and Spearman tables, on real data | if Quality does not rank, its weights are decoration; refit them as a regularized logit on dev only, or drop groups that do not contribute |
| S2 | **Regime gate** live-equivalent in research (APEX-014), then test whether `allowed_regimes` helps | live already WATCHes disallowed regimes, but research trains on them |
| S3 | **H1 ablation** (amendment 03 §4): squeeze vs no squeeze, matched on momentum, sector and volume | is compression additive, or just breakout + momentum? |
| S4 | **H2 / H5** sector-neutral relative strength vs absolute momentum; within-sector ranks (DOC≠CODE today) | avoid learning sector beta |
| S5 | **H7 factorial**: H1-only, catalyst-only, H1×catalyst, matched random (once catalysts exist) | synergy claim needs incremental OOS benefit |
| S6 | **Universe**: liquidity tiers ($30 M vs $100 M median $ volume) and price tiers, by cost sensitivity | small names may carry spreads beyond 25 bps |
| S7 | **Portfolio overlap**: one per symbol already; add a correlation cap (e.g. 60-day return correlation > 0.7 counts as same exposure) | sector caps alone miss cross-sector clusters |

**Acceptance for any new picking rule:**
- it beats momentum_20 **and** random-qualified on the same dates, after costs;
- it survives the multiple-testing correction (APEX-021);
- it survives a fresh holdout and a forward paper period of a pre-registered length (amendment 03 §7).

### Step 6: model

Only after steps 1–5. Things to try:
- compare the multinomial logit against a cause-specific hazard model (target, stop and time as competing risks);
- limit inputs to those that survived ablation;
- keep the JSON-only artifact format and the feature-schema hash.

### Step 7: forward shadow

Run PAPER_LIVE_DATA for a pre-registered number of sessions:
- every candidate is logged, including rejected and no-fill ones;
- strategy versions are locked;
- a weekly report covers fills, net after costs, calibration, Brier vs base rate, MAE and expected shortfall, and concentration.

Promotion to HIGH_CONVICTION requires the forward results to agree with the holdout.

---

## 9. Other analyses that would make the product better

- **Signal-level outcome audit.**
  - The dashboard tracks every published signal's theoretical outcome; add a monthly report of hit rate, net R and calibration by strategy and regime, **including** expired and invalidated signals.
  - The Lab page already shows per-strategy evidence; add the live-published population next to research.
- **Alert usefulness.**
  - Time from alert to fill-zone touch, and fill rate within the 2-session window.
  - How often members actually record entries (aggregate counts only, privacy-safe).
- **Risk concentration.** The daily count of open signals by sector, beta bucket and correlation cluster, and the worst historical cluster drawdown.
- **Cost realism.**
  - Collect live spreads at the open for alerted names from IEX quotes and compare them with the 10 / 25 bps assumptions.
  - Flag names whose measured spread exceeds the modelled cost.
- **Regime classifier validation.**
  - The logits are hand-set. Measure label stability, transition frequency and the forward-return separation by label on real data before using them as gates.
- **Exit-policy study** (assessments). Back-test `assess-1`'s trim and exit rules against "hold to plan" on development data. Trim at 0.75 R remaining is a hypothesis too.
- **Data-quality monitoring.** Track the share of symbols with stale or missing bars and outlier quarantines per day (APEX-025), and the coverage distribution of Quality.

---

## 10. Test backlog (highest value first)

These come from the scenario matrix. Each says where to add the test and what to assert.

**P0 acceptance cases (amendment 08) that are only partially covered:**

| Case | Add to | Assert |
|---|---|---|
| Q01 parity, model output | `tests/test_feature_parity.py` | inputs in `MODEL_FEATURES` order; at least one H1 pair with non-null `squeeze_prev`; a real `walk_forward(...).final_model` gives identical `predict()` for the training row and the live row (1e-12); same as-of instant and feed labels |
| Q02 schema drift after a Pi upgrade | `tests/test_model_sync.py` | patched `feature_schema_version` makes `load_local()` and `active_models()` empty, the policy UNVALIDATED (no probability, no HIGH_CONVICTION), the activation acked `rejected` with a notice |
| Q04 catalyst availability | new `tests/test_signal_context.py` | congress record (event Aug 1, available Sep 11) and an after-hours 8-K: `cat_count_5d == 0` up to Sep 10 and 1 from Sep 11; the Aug 1 event row has `cat_count_5d == 0` |
| Q07 no false alpha | new `tests/test_signal_policy.py` | synthetic manifest outside DEMO gives UNVALIDATED with no `p_target_first`; with research setups off and no validated model, `signals == []` and `no_trade_reason == "NO_CANDIDATE_PASSED_POLICY"` |
| Q11 venue and source labelling | `tests/test_market_data.py` | a crossed or 300-s-old IEX quote is not FRESH_VERIFIED, keeps `feed=iex`, and renders as IEX with its age, never "NBBO" or "consolidated" |
| Q12 "likely eligible" | `tests/test_market_data.py`, `tests/test_discord_service.py` | an unverified NYSE common stock gets `likely_eligible`; the alert text says to verify at the broker |
| Q18 reboot mid-save + 8 h outage | `tests/test_discord_service.py` | a queued entry survives a store reopen and an 8 h clock jump, replays once (`acked == 1`, then 0); a re-delivered interaction is DUPLICATE with exactly 1 event |
| Q19 every admin endpoint | `apps/web/tests/integration/adversarial.test.ts` | walk `src/app/api/v1/admin/**/route.ts`: every method, anonymous or with spoofed headers, returns 401/403 with only `{error}`; secret comparisons use `timingSafeEqual` |
| Q20 no side effect on rejection | `adversarial.test.ts` | reused nonce, stale timestamp, modified body or revoked agent leave the signal, event, position, outbox and heartbeat counts unchanged; only `security_events` grows |
| Q21 settings crypto | `apps/web/tests/integration/server.test.ts` | a swapped master key gives a structured "could not be decrypted", a 200 GET without plaintext, and no secret in any `console.*` output |
| Q30 group-guidance legal gate | `server.test.ts` | enabling `ALLOW_PERSONALIZED_GROUP_GUIDANCE` needs the acknowledgement, a reference and non-DEMO mode, and is audited; the Pi keeps `compliance_gated` unless the server flag is really enabled |

**Highest-value Phase 12 gaps** (full list in the matrix file):
1. **Stored XSS and Discord markdown or mention injection.**
   - Put notes or headlines with `<img onerror>`, `@everyone` and `[x](https://evil)` through the dashboard and the bot renderer.
   - Assert that the text renders literally and that `allowed_mentions` stays empty. Escaping must be implemented first.
2. **Credential routes never echo secrets** (`settings/[provider]` PUT, test and DELETE).
3. **Secret scan of git history in CI.** It was run manually for this audit and was clean; add a `--history` mode.
4. **Concurrent champion promotion race.** Two signed promotions at once must leave exactly one champion.
5. **Assertions on the evaluation report.** Selected `n` and win rate denominators, bootstrap bounds, monotone cost grid, a FRAGILE flag, and the `min_regimes` reason.
6. **NO TRADE policy decisions.** Stale quotes, an empty scan, ineligible tradability, no model with setups off, and a non-session day.
7. **The chained laptop-free round trip** in one opt-in test: observations → train → upload → promote → Pi activation → `MODEL_PROMOTED` notice. Then add the opt-in job to CI.
8. **Honest empty state outside DEMO.** Health reports UNCONFIGURED with an action, and no `DM*` symbols appear.
9. **Concurrent signed ingestion through the real routes.** Exactly one row per dedupe key.
10. **Supabase read-only or paused.** Return 503, never a fake 200; the Pi shows QUEUED.
11. **Pi heartbeat loss.** DEGRADED, then DOWN, with an operator action. The independent alert path is not built.
12. **No unverified price-target alert after a stream drop**, and **disk under 5 % free.** Implement both, then test.

---

## Appendix A: files to read first

| To change… | Read |
|---|---|
| entry, stop, target | `signals/levels.py`, `research/execution.py`, `tests/test_levels_quality.py`, `tests/test_execution.py` |
| strategy triggers | `signals/strategies/library.py`, `features/compute.py`, `signals/context.py` |
| Quality | `signals/quality.py`, `tests/test_levels_quality.py` |
| publication rules | `signals/policy.py`, `tests/test_signal_policy_caps.py`, `demo/replay.py` |
| model inputs | `signals/model_features.py`, `research/dataset.py`, `tests/test_feature_parity.py` |
| validation and gates | `research/validation.py`, `research/evaluate.py`, `research/metrics.py`, `tests/test_research_redteam.py`, `tests/test_validation_embargo.py` |
| position follow-up | `portfolio/assessment.py`, `tests/test_assessment.py` |
| cloud training | `.github/workflows/apex-model-*.yml`, `release/`, `docs/AUTONOMOUS_TRAINING.md` |

All Python paths are under `apps/pi-worker/src/apex_worker/` unless they start with `tests/` or `bench/`.
