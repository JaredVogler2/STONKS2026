# Design study `design-study-1` (design-study-1-2026-10-23)

Status: **COMPLETED**. Sessions 2019-10-24 → 2026-09-18 (1734; DEV_CONTAMINATED, DEV_DIAGNOSTIC). Research only: paper books, no orders.

Tracking error and worst session are risk measurements; the means are DEV_CONTAMINATED diagnostics and select nothing.

| Design | every | TE vs twin (per unit dispersion) | worst vs twin | TE vs SPY | worst vs SPY | turnover/yr | extra cost/yr | gross alpha for 800 in 10 y: limit · bounded | DEV IR vs twin |
|---|---|---|---|---|---|---|---|---|---|
| `mom-raw` | 20 | 2.27% (2.26%) | -1.27% (2020-03-19) | 7.11% | -2.87% | 1.46 | 0.024% | 2.65% · 3.02% | -0.36 |
| `mom-sector` | 20 | 1.75% (1.70%) | -1.04% (2020-03-19) | 7.10% | -2.88% | 1.55 | 0.027% | 2.05% · 2.72% | -0.31 |
| `mom-full` | 20 | 1.24% (1.26%) | -0.88% (2020-03-19) | 7.14% | -2.90% | 1.53 | 0.026% | 1.46% · 1.86% | -0.19 |
| `rev-raw` | 5 | 2.46% (2.45%) | -1.21% (2020-03-18) | 7.89% | -3.71% | 9.41 | 0.342% | 3.18% · 3.48% | +0.26 |
| `rev-sector` | 5 | 1.89% (1.83%) | -0.92% (2025-04-03) | 7.72% | -3.61% | 9.61 | 0.349% | 2.54% · 2.64% | +0.20 |
| `rev-full` | 5 | 1.42% (1.45%) | -0.67% (2020-03-18) | 7.66% | -3.47% | 9.19 | 0.333% | 1.98% · 2.05% | +0.08 |
| `hi52-raw` | 20 | 2.46% (2.45%) | -1.47% (2020-11-09) | 6.93% | -2.98% | 1.13 | 0.010% | 2.86% · 3.15% | -0.40 |
| `hi52-sector` | 20 | 1.92% (1.86%) | -1.04% (2020-11-09) | 6.90% | -2.86% | 1.25 | 0.015% | 2.23% · 2.79% | -0.37 |
| `hi52-full` | 20 | 1.38% (1.40%) | -0.84% (2020-03-19) | 6.96% | -2.91% | 1.27 | 0.016% | 1.61% · 1.93% | -0.11 |
| `composite-full` | 5 | 1.32% (1.40%) | -0.53% (2020-03-19) | 7.04% | -3.06% | 4.57 | 0.148% | 1.68% · 1.79% | -0.13 |
| `lowvol-full` | 20 | 0.76% (0.80%) | -0.27% (2026-08-19) | 7.31% | -2.85% | 1.30 | 0.017% | 0.90% · 0.92% | -0.54 |

Twin `ew` vs SPY: TE 7.49%, worst session -3.00% (2020-03-12).

Cohort 0's declared loss bounds on DEV:
- `mom-raw`: vs twin worst -1.27% against L 0.03 (held); vs SPY worst -2.87% against L 0.05 (held)
- `rev-raw`: vs twin worst -1.21% against L 0.03 (held); vs SPY worst -3.71% against L 0.05 (held)
- `hi52-raw`: vs twin worst -1.47% against L 0.03 (held); vs SPY worst -2.98% against L 0.05 (held)

**Proposal** (PROPOSAL for a later, separately registered cohort; changes no recorded series): per signal, the lowest tracking error against the twin at the reference dispersion (ties: the more neutral), plus the extras; loss bound = smallest grid value >= 2.0 x the worst DEV session.

| Candidate | every | twin loss bound | loss bound vs SPY | prior mass |
|---|---|---|---|---|
| `mom-full` | 20 | 0.02 | 0.075 | 0.04 |
| `rev-full` | 5 | 0.015 | 0.075 | 0.04 |
| `hi52-full` | 20 | 0.02 | 0.075 | 0.04 |
| `composite-full` | 5 | 0.015 | 0.075 | 0.04 |
| `lowvol-full` | 20 | 0.0075 | 0.075 | 0.04 |
