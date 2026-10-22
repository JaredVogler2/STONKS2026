# Erratum — `2026-10-21-fixed78-sip-8y-r2`

- **Issued:** 2026-10-22 by the Quant V2 M0 audit ([`docs/quant-v2/M0_AUDIT.md`](../../../docs/quant-v2/M0_AUDIT.md)).
- **Status:** the seven published files are **unchanged and remain the historical record**. This note qualifies how some fields may be read.
- **Verdict:** the aggregate verdict **NO_TRADE stands**.

| # | Field or claim | Correction |
|---|---|---|
| E1 | `manifest.source.dataset_hash`, `history_hash` | **Not bitwise reproducible.** An independent replay on identical code over the same window ([run 39757487973](https://github.com/JaredVogler2/STONKS2026/actions/runs/39757487973), 2026-10-22) reproduced **every metric exactly** (`strategy_metrics.json` 0 differences; `trades_summary.csv` byte-identical), but the dataset and history hashes differ, so some vendor bar values changed after the original fetch. The cause is not determined; no raw bars are retained by design. |
| E2 | `holdout.p_one_sided` and `holm_adjusted_p`, for every hypothesis | **Not calibrated p-values.** They are tail shares of a one-week-block bootstrap of the mean. With overlapping 20-session holds, a nominal 5% test rejects a true null 10.9–17.1% of the time in simulation (M0 §4). Read them as descriptive. Protocol-1's VALIDATED gate depended on them, so it was not a valid inference gate. No hypothesis came near it: `p_one_sided` ≥ 0.42 and Holm = 1.0 for every evaluable hypothesis. |
| E3 | H8 `FAILED_REAL_OOS` | The label is retained as computed under the frozen protocol-1 rules. It rests on 140 trades in only **15 decision weeks and one regime**. Under the cluster-minimum rule adopted for protocol-2, the qualified reading is **INCONCLUSIVE (insufficient independent clusters)**, not evidence of failure with power. |
| E4 | Fill-session handling; `mean_mfe` / `mean_mae` | On the fill session only the stop could trigger. When the fill was at the open and the target was touched later that day, the simulation held instead of selling. Depending on the next open, that is optimistic or pessimistic, so it is **not a uniform worst case** (M0 §3). MFE and MAE use whole-day extremes and can include price action before the fill or after the exit; read them as bounds. The size on these real bars is not yet measured. |
| E5 | Signal screen cost | `compute_levels` screened net reward/risk ≥ 1.5 at **10 bps per side**, while results are evaluated at 25 bps. Some screened signals are uneconomic at the primary cost. |
| E6 | Matched-random and baselines | Controls used the generic stop hint close − 1.5 ATR and were not de-duplicated. That matches H5 and H8, but not H1, H2, H4 or H6, which use their own hints. |

**Why NO_TRADE is unaffected:**
- E2, E3, E5 and E6 can only weaken the evidence, or they are about comparability, not sign.
- E4 is mixed in direction. Turning the closest candidate, H5 (+0.034% per trade, bootstrap p10 −0.555%), into a validated result would need about +0.6 percentage points per trade from E4. On synthetic paths the mechanism averages under 0.01 pp.
- Protocol-2 measures E4 and E5 directly on the real bars.
