# Discovery V3 pilot 2 (`v3-pilot-2-2026-10-23`): reviewer notes

| | |
|---|---|
| Run | [39921190552](https://github.com/JaredVogler2/STONKS2026/actions/runs/39921190552), `apex-discovery-v3`, manual dispatch on `main` `ed8c8e5` (the PR #27 merge), 2026-10-23 07:56 UTC, about 4 minutes |
| Plan / objective | `v3-pilot-2`, sha256 `5a2cc40c…8c0e`, equals the [preregistration](../../../docs/discovery-v3/PILOT_2_PREREGISTRATION.md). Objective `obj-v3-2` `2637726e…`. Cost model `cost-v3-2` |
| Data | Protocol-2's U1 SIP dataset `0339c815…651f`, identical to pilot 1 and the cost study (`data_quality_summary.json` is byte-identical). DEV 2019-10-23 → 2026-09-18, **contaminated twice** |
| Artifact | `apex-discovery-39921190552-1`, digest `sha256:6fc58c03…25e8`. The six files here are byte-for-byte copies |
| Evidence tier | `DEV_DIAGNOSTIC` at most. Aggregate **NO_TRADE** |

## 1. Result (net of `cost-v3-2`; SPY buy-and-hold +13.5% a year, max drawdown −34.2%, volatility 19.5%)

| Family | Policy | Log growth / yr | Active vs SPY | One-sided p | Holm p (7) | Max DD | Disposition |
|---|---|---|---|---|---|---|---|
| G1 | S1 at the **close** (primary) | +10.0% | −3.5 pp | 0.93 | 1.0 | −33.8% | REJECTED_DEV |
| G1 | S1 at the **open** | +8.2% | −5.3 pp | 0.97 | 1.0 | −34.0% | REJECTED_DEV |
| G2 | **Momentum, quarterly, buffered, close** (primary) | **+22.2%** | **+8.6 pp** | 0.092 | 0.623 | −34.9% | **INCONCLUSIVE_DEV** |
| G2 | the same at the **open** | +22.2% | +8.7 pp | 0.089 | 0.623 | −35.1% | INCONCLUSIVE_DEV |
| G2 | monthly, **unbuffered**, close | +16.9% | +3.4 pp | 0.29 | 1.0 | **−48.2%** | REJECTED_DEV (drawdown more than 10 pp worse than SPY's) |
| G3 | Dispersion-gated swing at the close (primary) | +10.6% | −2.9 pp | 0.82 | 1.0 | −35.2% | REJECTED_DEV |
| G3 | Ungated, at the close | +9.7% | −3.9 pp | 0.83 | 1.0 | −35.2% | REJECTED_DEV |

**No trial is `PROMISING_EXPLORATORY`, so nothing is nominated** under the registered rule.

**G2's registered selection test** (zero cost, against 20 random buffered books with the same mask, schedule, buffer and timing):
- momentum beats **all 20** random books (random mean +10.4% a year, p10 +7.0%, p90 +15.4%);
- active **+11.4 pp a year, one-sided p 0.074**: not significant at 5%.

## 2. Integrity checks

| Check | Result |
|---|---|
| Engine self-checks | Look-ahead trap detected (p 0.0002); placebo quiet (p 0.58). `engine_valid: true` |
| Registry | `verify()` finds no problems and `validate_complete()` passes. 24 events: 3 hypotheses, 7 trials registered before the manifest froze, 1 counted look each, and 7 results |
| Independent book engines agree | G2 at the **open** runs on protocol-2's validated `quant_v2.allocation_returns`; at the **close** it runs on the new `close_rebalance_returns`. They give +8.66 and +8.62 pp: a book defect cannot explain the effect |
| Publication guard | Clean on all six files (aggregates only, no prices) |
| Plan, objective and code | `manifest.json` records plan sha `5a2cc40c…`, objective `obj-v3-2` and code `ed8c8e5` |

## 3. Findings that bound the G2 result (disclosed with it)

| Id | Finding |
|---|---|
| P2-1 | **Survivorship × momentum, not removed by the control.** U1 is today's S&P 500, with membership by `date_added`. Momentum selects recent winners, and winners that later collapsed and left the index (failed banks in 2023, for example) are absent from the universe. The random-books control removes the *general* survivor and equal-weight effect: random survivors earned +10.4%, below SPY. It cannot remove the **interaction** between selecting on past returns and conditioning on future survival. Its size is unknown; only a point-in-time universe (U2, `SOURCE_BLOCKED`) can measure it |
| P2-2 | **The growth is bought with risk.** Beta 1.20 and volatility 35% against SPY's 19.5%. Daily expected shortfall is −5.4% against −2.9%. Growth per unit of volatility is 0.63 against SPY's 0.70, so **risk-adjusted, G2 is not better than SPY**. On the registered utilities: conservative −0.325 against SPY −0.246 (worse); balanced −0.052 against −0.055 (equal); growth +0.129 against +0.072 (better) |
| P2-3 | **Execution timing is irrelevant at low turnover.** Open and close give the same result (+8.66 against +8.62 pp): stock turnover is 6.9 a year, and friction costs at most about 1.4 pp |
| P2-4 | **What moved momentum from pilot 1's −1.9 pp** (descriptive decomposition, not tested): the monthly unbuffered close book (no breaker, no extension cap, about 2 bps) gives +3.4 pp, and the quarterly buffer adds about +5.3 pp more. The pilot-1 mask's **extension cap** excluded the most extended momentum names. Removing it is part of the "no breaker" change and was **not** separated |
| P2-5 | **G1 confirms its competing explanation.** Close entries cut friction (−4.5 to −2.3 pp) and raise the S1 book by +1.8 pp. But S1's advantage over its matched controls falls from +1.3 pp (open) to +0.2 pp (close): **part of S1's selection value is earned on the entry day**, and buying at the close forfeits it |
| P2-6 | **Forking paths.** G2 was designed after pilot 1 had shown momentum beating SPY before costs, on the same history. The winner's curse applies in full |

## 4. How to reproduce

**Same command.** Dispatch *Actions → apex-discovery-v3* on a commit containing plan `v3-pilot-2`, with any `experiment_id`.
- On the same data, the trial ids repeat and the registry counts the extra look.
- A vendor revision of the data gives new trial ids.

**DEMO.**

```bash
apex research discovery run --plan v3-pilot-2 --source fixture --experiment-id demo-x --out /tmp/x
```
