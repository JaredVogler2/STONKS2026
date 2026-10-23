# Discovery V3 pilot 1 (`v3-pilot-1-2026-10-23`): reviewer notes

| | |
|---|---|
| Run | [39884381394](https://github.com/JaredVogler2/STONKS2026/actions/runs/39884381394), `apex-discovery-v3`, manual dispatch on `main`, 2026-10-23 01:30 UTC (research step 3 min 50 s) |
| Code | `dddf352` (PR #22 merge). Plan `v3-pilot-1`, sha256 `11d0af37…ae2c7`, equals the [preregistration](../../../docs/discovery-v3/PILOT_PREREGISTRATION.md). Objective `obj-v3-1`, sha256 `8911f89e…34bc` |
| Data | Alpaca historical SIP daily bars, split-adjusted, U1 (S&P 500 survivors, `DATA_LIMITED_SURVIVORS`), 2016-01-04 → 2026-10-20 after the 2-session finality lag. 507 of 512 symbols usable (495 stocks, 11 sector ETFs). 143 provider requests. Dataset `0339c815…651f` |
| Window | DEV decisions 2019-10-23 → 2026-09-18 (`DEV_CONTAMINATED`), with books run 1,757 sessions to cover every exit. PRESAMPLE was not touched |
| Artifact | `apex-discovery-39884381394-1`, digest `sha256:9e9367b1…7f5255`. The six files here are byte-for-byte copies |
| Evidence tier | `DEV_DIAGNOSTIC` at most. Aggregate **NO_TRADE** |

## 1. Result

**All 10 registered trials are `REJECTED_DEV`.**
- No policy's net annualised log growth exceeds its investable alternative.
- Holm over the 10 trials gives p = 1.0 for each.
- No trial is `PROMISING_EXPLORATORY`, so **nothing is nominated** for the prospective exploratory lane.

The full table is [TRIAL_INDEX.md](TRIAL_INDEX.md), and the economic reading is [DECISION_REPORT.md](DECISION_REPORT.md).

## 2. Integrity checks done for this review

| Check | Result |
|---|---|
| Engine self-checks | Look-ahead trap **detected** (289 signals, p 0.0002). Placebo **quiet** (290 signals, p 0.58). `engine_valid: true` |
| Registry | `TrialRegistry.load(...).verify()` finds no problems and `validate_complete()` passes. 34 events: 4 hypotheses, 10 trials registered before the manifest froze, exactly 1 counted LOOK each, and 10 RESULTs. Head `4af10ea0…`; the head at freeze, `c7483653…`, is recorded in `manifest.json` |
| Reproduction of protocol-2 | **Exact.** The cash-idle S1 book (`f1-s1-idle-cash`) reproduces the committed protocol-2 S1 DEV book in `qv2-20261022-s1-s5-u1-presample/strategy_metrics.json`: total −8.5192%, exposure-matched SPY/cash +46.2093%, max drawdown −30.1463%, 693 trades, and the same 66 sector-cap and 85 full-slot skips. The dataset hash is the official run's |
| Publication guard | `research.forward.records.publication_problems` over all six files reports nothing: no raw bar fields and no credential-shaped strings. The files hold aggregates only |
| Plan and code | `manifest.json` records the plan sha, the objective sha and `code_sha = dddf352…`, all matching `main` |

## 3. Findings disclosed after the run

None of these changes a disposition: every trial has active growth ≤ 0, which rejects it under the registered rule with or without them. They are recorded so that no later plan inherits them silently. **No trial is re-run or relabelled under this plan id.**

| Id | Kind | Finding | Effect on this batch |
|---|---|---|---|
| A-1 | Agent definition (undisclosed in the preregistration text) | The selection agents (`momentum_weights`, `random_weights`, `momentum_top`) draw from protocol-2's swing **eligibility mask** (`research.dataset.gate_mask`). That mask includes (i) a market-wide **risk-off breaker**, under which no stock is eligible while p(risk_off) + p(volatility_shock) ≥ 0.6, and (ii) an **extension cap** that excludes stocks more than 3.5 ATR above their 20-session mean. A F5 rebalance that falls on a breaker session therefore holds **100% cash for 20 sessions**. The preregistration's "top 10 by 60-session return, equal weight" did not say this | F5's books average **84% exposure**: the primary and the random ablation run on the same schedule, so they have the same cash months. The F5 primary is therefore **not a fully invested momentum book**, and its `abstain` ablation compares two abstaining variants. The registered F5 falsification (momentum versus random under the same schedule) is unaffected, because both share the gate. F2 also uses the mask, but its idle capital is in SPY, so the breaker only suppresses new swing entries there |
| O-1 | Objective design | The `obj-v3-1` max-drawdown floor (−30%) is **tighter than the comparator's own DEV drawdown**: SPY buy-and-hold lost −34.2% (the 2020 crash). Any near-fully-invested policy violates it by construction. 8 of the 10 trials carry this violation | None: all 8 also have active growth ≤ 0. A successor objective should express the drawdown limit **relative to the comparator**, under a new objective id. `obj-v3-1` is never edited |
| A-2 | Constraint accounting | The SPY-idle F1 book's turnover (40.8 a year) counts the SPY sweep trades that fund each entry. It breaches the 40 a year cap by 0.8 | None: active growth is −6.6 pp a year either way |
| R-1 | Random-control variance | F5's random ablation is **one** seeded random path. Its result (−15.1 pp a year against SPY; +3.6% a year at zero cost) is one draw from a wide distribution: a 10-stock equal-weight book has a tracking error of the order of 15% a year. A gap against it is a single comparison, not a test | The momentum-minus-random gap (+13.1 pp a year net) is reported **descriptively only**. A later plan should test against a distribution of K random books, as the swing arms already do with their matched controls |

## 4. How to reproduce

**Same command.** Dispatch *Actions → apex-discovery-v3* on a commit containing plan `v3-pilot-1`, with any `experiment_id`.
- On the same data, the trial ids are identical (`t-…` derive from the hypothesis, the policy, the dataset hash and the objective), and the registry counts the extra LOOK.
- Alpaca vendor revisions change the dataset hash, which yields **new** trial ids that are visibly a new batch.
- Bootstrap p-values use `n_boot = 999` with fixed seeds.

**DEMO.** Without market data:

```bash
cd apps/pi-worker
uv run apex research discovery run --plan v3-pilot-1 --source fixture --experiment-id demo-x --out /tmp/x
```

This is the synthetic fixture, and it is never evidence.
