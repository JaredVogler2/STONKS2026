# Design study `design-study-2` (design-study-2-2026-10-25)

Status: **COMPLETED**. Sessions 2019-10-24 → 2026-09-21 (1735; DEV_CONTAMINATED, DEV_DIAGNOSTIC). Research only: paper books, no orders.

SEC events 2018-01-01 → 2026-09-21: 17494 earnings releases, 5926 insider purchases; 495 of 495 names mapped to a CIK. Flagged share of the masked names: insider 13.7%, expected release 13.0%; announcement return available 93.6%.

Tracking error and worst session are risk measurements; the means are DEV_CONTAMINATED diagnostics and select nothing.

| Design | every | TE vs twin (per unit dispersion) | worst vs twin | TE vs SPY | worst vs SPY | turnover/yr | extra cost/yr | gross alpha for 1000 in 10 y: limit · bounded | DEV IR vs twin |
|---|---|---|---|---|---|---|---|---|---|
| `insider-full` | 5 | 0.69% (1.14%) | -0.53% (2020-03-18) | 7.52% | -3.20% | 1.84 | 0.038% | 0.85% · 1.33% | -0.11 |
| `eap-full` | 5 | 0.69% (1.42%) | -0.50% (2020-03-18) | 7.45% | -3.22% | 3.68 | 0.112% | 0.92% · 1.09% | -0.04 |
| `ear-full` | 5 | 0.82% (0.83%) | -0.51% (2020-03-18) | 7.33% | -3.22% | 2.52 | 0.066% | 1.04% · 1.42% | -0.07 |

Twin `ew` vs SPY: TE 7.50%, worst session -3.00% (2020-03-12).

**Proposal** (PROPOSAL for a later, separately registered cohort; changes no recorded series): every design; loss bound = smallest grid value >= 2.0 x the worst DEV session.

| Candidate | every | twin loss bound | loss bound vs SPY | prior mass |
|---|---|---|---|---|
| `insider-full` | 5 | 0.015 | 0.075 | 0.04 |
| `eap-full` | 5 | 0.01 | 0.075 | 0.04 |
| `ear-full` | 5 | 0.015 | 0.075 | 0.04 |
