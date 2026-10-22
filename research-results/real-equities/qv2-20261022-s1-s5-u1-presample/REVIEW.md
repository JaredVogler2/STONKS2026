# Reviewer notes — `qv2-20261022-s1-s5-u1-presample`

These are the reviewer's QA notes and adversarial interpretation, written by hand. The other seven files are the unmodified artifact of the run. The decision report is [QUANT_V2_DECISION_REPORT.md](QUANT_V2_DECISION_REPORT.md).

## 1. Provenance (verified)

| Item | Value |
|---|---|
| Run | [GitHub Actions run 39849016753](https://github.com/JaredVogler2/STONKS2026/actions/runs/39849016753), job 120069895003, `workflow_dispatch` on `main` at `eda340a` (PR #15 merge). Conclusion **success**. |
| Timing | Queued 20:27:31Z. Research step 20:27:47–20:33:47Z (**6.0 min** of the 40-minute step limit). Job 6 min 17 s. |
| Artifact | `apex-realeq-39849016753-1` (id 12456158162). Zip sha256 `ffeff19fd1d00dce5ed171eb8e6202b97673021e0d37cfd6b6ea0ff9e0c4222c`, identical to GitHub's digest. Exactly these seven files. |
| Protocol | `realeq-protocol-2` amendment A1, sha256 **`33bd4e99ecaf06e8fbe0312e2892a0e382d39a632c28280bb66dbd4f5d05475d`**: in the manifest, in `strategy_metrics.json`, and computed from `main`. A0 `a15b4e92…4c9f` is superseded (PROTOCOL_V2.md §12). |
| Manifest | sha256 `be792db3eca3ce7999d45644ff46e83cc671f636d675f602eeb07fc210b91997`, equal to `strategy_metrics.json.manifest_sha256`. Status `FROZEN_BEFORE_METRICS`, frozen at 20:30:45Z. |
| Selection lock | `017070e1654680bb32f7570e9b869d9e35007c562a920d0d9236bd453a878833`, locked at 20:32:20Z. It is logged in `execution_log.md` **before** the evaluation step. Chosen on DEV: S1 E0X1, S2 E0X1, S3 C_X0, S5 R10. |
| Source | Alpaca historical **SIP** daily bars, split-adjusted. Fetched 2026-10-22T20:27:49Z. Data end 2026-10-20 (2-session finality lag). |
| Requests | **143 of 300**: 137 for bars, 6 for corporate actions. |
| Raw rows | 1,334,342, 0 invalid. |
| Hashes | dataset `0339c815532901fc976327cf6ef268b0561c5a60df419f81217afcd978ae651f`; history `ebd26bdee6f735737390a8b23b236471a61a7f1ffbf83d529850d04bd9f854bf` |
| Windows | PRESAMPLE decisions 2017-01-03 → 2019-09-20 (sessions 252–935). DEV 2019-10-23 → 2026-09-18 (958–2692). 2,715 sessions. |
| Hygiene | No price, quote or volume fields per trade (the `high`/`low` keys are bounds of a return bracket). No NaN. No credentials: the execution log records only that the two keys were present. |
| Cost | About 6 runner minutes; no paid data. |

## 2. Data checks

- **Symbols:**
  - 511 of 512 returned data. **DOW** (Dow Inc., an S&P 500 member since 2019-04-01) came back empty and is `MISSING`.
  - Four recent listings were excluded for incomplete history: FDXF, HONA, Q, VYLR.
  - 507 usable symbols: 495 stocks, 11 sector ETFs and SPY.
- **Corporate actions:**
  - Available: 145 records, with records in every year from 2016 to 2026.
  - 27 symbols carry quarantined windows.
  - **Split-adjustment suspects:** EQT 2018-11-13 (the date of the Equitrans Midstream separation) and HON 2026-06-29 (the registered known event). Trade windows across both are quarantined.
  - The one spin-off record whose parent is not in U1 is CTVA; it quarantines nothing.
- **Membership and coverage:**
  - At the PRESAMPLE start, 347 members are usable (the stocks already in the index by 2017-01-03).
  - Late starts: APTV, HWM, LIN, SKYD, VTRS.
- **Benchmark:** SPY is complete, with median daily volume 73.0M shares. That is consolidated-tape size, so the feed is SIP.
- **Data flags:** missing-bar flags were zero for every primary arm, so no `MISSING_BAR` ambiguity occurred.

## 3. Engine checks

- **Look-ahead trap:** mean +14.87% per signal, conservative p 0.0002. **Detected**, so the engine can see a known edge.
- **Placebo** (one random eligible member per session): paired +0.025%, p 0.454. Quiet.
- **Daily-bar ambiguity:** zero ambiguous fills on every primary arm and on the controls. The pessimistic and optimistic legs coincide, and the daily-bar bracket is a single point. X0's target and stop never fell on the same bar for S3's 34 fills.

## 4. Adversarial interpretation — read this before the numbers

1. **PRESAMPLE is exposed, not untouched.** The cancelled A0 run 39829472789 published PRESAMPLE outcomes from slightly different code. The analyst never read them, and A1 was fixed before this run, so nothing here was tuned to them. Anyone who saw them could still have formed expectations. Treat this as a single, honestly executed look at a window that was not perfectly sealed.
2. **It is backward in time.** 2017–2019 precedes the design data (2019–2026). It tests whether rules shaped on later data held earlier. It is not evidence about the future.
3. **The universe is survivorship-limited.** The 2017–2019 cross-section is today's S&P 500 members that were already in the index. Losers that later left are absent. Paired same-session controls drawn from the same set cancel part of this bias, but absolute returns and the books are flattered.
4. **The market was in one regime.** 2017–2019 was mostly `trending_up`: 90% of S1 signals and 95% of S3's. Both promising strategies failed the "≥ 2 regimes with ≥ 15% each" gate. In the few risk-off signals, S1's paired mean was negative (−0.86%, n = 28).
5. **The p-values are marginal and slightly anti-conservative.**
   - S1's conservative raw p is **0.058** and S3's is **0.089**. Neither clears 0.05 before Holm; after Holm they are 0.23 and 0.27.
   - On realistic synthetic nulls the decision rule rejects 1.2–1.5× nominal. So the true evidence is weaker than these numbers suggest, not stronger.
6. **Power is low.**
   - S1's realised MDE is +0.61 pp per signal and S3's is +1.69 pp, against a +0.30 pp economic minimum.
   - S2's MDE of +0.26 pp makes its failure informative.
   - S3 rests on 36–37 trades on 32 distinct sessions.
7. **Descriptive numbers are not confirmatory.**
   - S1's mechanism contrast (vs leaders in any state, p 0.040).
   - h6's raw p of 0.030 (legacy family, Holm 0.18).
   - The books' "p vs controls".

   None of these is a registered test of the primary family.
8. **Paired success does not mean absolute success.** The matched controls are random same-sector S&P names traded with the same stop, horizon and costs. They lose money on average: −0.04%, −0.13% and −0.42% per signal for S1, S2 and S3. Every swing book also trails exposure-matched SPY/cash in both segments, S3's PRESAMPLE book aside, so beating the controls mostly means losing less than the trade wrapper's drag.

## 5. Reproducibility check against the A0 artifact

This is pending. It will be done once, after this results PR is opened, and recorded here. Code differences between A0 and A1 (PROTOCOL_V2.md §12.2) and vendor data revisions are expected to change some numbers. It is a consistency check, never a second evaluation.

## 6. Reproduce

The run is reproduced by dispatching the same workflow on `eda340a`. Alpaca's vendor revisions can change the dataset hash, as the r2 replay showed.

**Inputs:**
- source `alpaca`
- protocol `realeq-protocol-2`
- universe `u1-sp500`
- years `8` (ignored by protocol-2)
- empty time overrides

**Requirements:** the `real-equities-research` environment with the two read-only Alpaca secrets.

**Locally:**

```bash
cd apps/pi-worker
ALPACA_KEY_ID=... ALPACA_SECRET_KEY=... uv run apex research real-equities --protocol realeq-protocol-2 \
  --universe u1-sp500 --source alpaca --experiment-id <new-id> --out <dir>
```

**Offline engine smoke test** (synthetic data, no market data):

```bash
APEX_DEMO_PER_SECTOR=46 uv run apex research real-equities --protocol realeq-protocol-2 --universe u1-sp500 \
  --source fixture --experiment-id demo-p2-smoke --out /tmp/demo-p2   # 46 per sector clears the 350-stock minimum
```
