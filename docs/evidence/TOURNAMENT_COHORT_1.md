# Evidence tournament, cohort 1 (`v3x-epa-tournament-c1-v1`)

| | |
|---|---|
| Status | **Implemented.** It records together with cohort 0. The same repository variable, `APEX_TOURNAMENT_LANE` = `on`, activates both; until it is set, every run is a dry rehearsal that posts nothing |
| Evidence tier | `PROSPECTIVE_EXPLORATORY`, never confirmatory. Interim reading is valid by design (anytime-valid evidence) |
| Mode | **Research only:**<br>- paper books;<br>- no orders, alerts, Discord signals or promotion;<br>- `HIGH_CONVICTION` and `GROUP_ACTIONABLE` stay disabled;<br>- cohort 0 (`v3x-epa-tournament-v1`) and the frozen lane are untouched |
| Designs | The proposal of the registered design study [`design-study-1`](DESIGN_STUDY_1.md), adopted as registered ([results](../../research-results/discovery-v3/design-study-1-2026-10-23/REVIEW.md)) |
| Code | - Decision logic: `apex_worker/discovery/v3/tournament_books_c1.py`. It computes the study's own decisions (`discovery.v3.design.decide`, the study's plan restricted to its `full` variants).<br>- Lane: `apex_worker/research/forward/tournament_lane.py::COHORT_1`.<br>- Registry entry: `research/forward/exploratory.py::EPA_TOURNAMENT_C1` |
| Workflow | `.github/workflows/apex-evidence-tournament.yml`, the same two weekday attempts as cohort 0. One pass records both cohorts from one market-data fetch (`apex research forward-shadow run-tournament`) |
| Ledger | Its own GitHub issue, "APEX forward-shadow ledger · v3x-epa-tournament-c1-v1 · vol 1", created by its first real run. It has its own sha256 chain, and GitHub's comment timestamps are the independent anchor |
| Approved | By the owner, 2026-10-23 |

## 1. Registered slate

Each candidate is a tilt of the equal-weight mask with sector-, beta- and size-neutral active weights, and each is claimed against its equal-weight twin `ew`.

| Candidate | Rebalance | Twin loss bound | Loss bound vs SPY | Prior mass | Tracking error vs twin on DEV | Why it is in the slate |
|---|---|---|---|---|---|---|
| `mom-full` | every 20 sessions | 0.02 | 0.075 | 0.04 | 1.24% (cohort 0's `mom-tilt`: 2.27%) | Momentum without its sector, beta and size bets (residual momentum) |
| `rev-full` | every 5 | 0.015 | 0.075 | 0.04 | 1.42% (`rev-tilt`: 2.46%) | Within-sector short-term reversal: liquidity provision, weekly at the close |
| `hi52-full` | every 20 | 0.02 | 0.075 | 0.04 | 1.38% (`hi52-tilt`: 2.46%) | 52-week-high proximity (anchoring; George and Hwang 2004) |
| `composite-full` | every 5 | 0.015 | 0.075 | 0.04 | 1.32% | The three signals combined into one neutral score |
| `lowvol-full` | every 20 | 0.0075 | 0.075 | 0.04 | 0.76% | Low idiosyncratic volatility, beta-neutral: the most testable design measured |

**Where the numbers come from.**
- The loss bounds and prior masses come from the study's registered rule: twice the worst DEV session, rounded up on a fixed grid. They were not tuned afterwards.
- The tracking errors are the study's measurements on 1,734 DEV sessions (`DEV_CONTAMINATED`). They describe risk, not edge.

**Claims: against the twin only.**
- Each candidate claims on two tests: `SELECTION_VS_TWIN` (assumption-free) and `SELECTION_VS_TWIN_BOUNDED_LOSS` (under its twin loss bound).
- Its prior mass is split across those two, so a claim needs evidence of 2 / (0.05 × 0.04) = **1,000** on one of them.
- The tests against SPY still run and drive the paper allocation (the evidence-weighted book, as in cohort 0), but they make no claim.

**Why the twin.**
- Against SPY, a tilt of equal weight mostly measures equal weight against cap weight. Cohort 0's `ew` candidate already tests that.
- A tilt that beats its twin, while equal weight beats SPY, beats SPY.
- Claiming only on the mechanism test halves the evidence a claim needs, from 2,000 to 1,000.

**Tournament settings.** Cohort 0's settings:
- the evidence-weighted book, with null mass 0.5;
- α = 0.05;
- bet grid {1/16, …, 1};
- rebalancing charge 2 bps per side;
- `claim_rule` = `prior-mass-split-equally-across-claim-tests`.

## 2. One error budget across every cohort

| Cohort | Series | Prior mass |
|---|---|---|
| 0 | `v3x-epa-tournament-v1` | 0.45 |
| 1 | `v3x-epa-tournament-c1-v1` | 0.20 |
| Reserve | later cohorts (SEC-event designs, S1/S3 books, cap-weighted tilts) | 0.35 |

**Why claims stay family-wise valid across series.** The union bound behind every claim needs only two things:
- every mass is fixed before its candidate's first period: it is in that cohort's genesis record;
- all masses sum to at most 1: `tournament_lane.assert_mass_budget`, checked by the tests.

So P(any false claim, in any cohort, ever) ≤ 0.05.

Cohort 1's genesis configuration names cohort 0, its mass and the total used (0.65).

## 3. Books, timing and data

**The books:**
- **`ew`:** equal weight over the explicit mask, every 20 sessions. It is the twin.
- **The five designs:**
  - Each ranks its signal within each sector, mapped to [−1, 1].
  - It takes the least-squares residual on the within-sector-demeaned 252-session market beta (daily log returns on SPY's, at least 126 observations) and log 20-session median dollar volume, so the active weights are exactly sector-, beta- and size-neutral.
  - It scales that residual to a rank's dispersion (1/√3), clips it to [−1, 1], and holds equal weight × (1 + 0.5 × score).
- **The composite:** the mean of the three signals' standardized residuals.

**Shared conventions** (cohort 0's, [TOURNAMENT_LANE.md](TOURNAMENT_LANE.md) §2–§3):
- the explicit mask;
- long-only integer micro-weight targets;
- decided at the close of t, traded at the close of t + 1;
- cost-v3-2 (2 bps per side for stocks, 5 for SPY);
- split-adjusted SIP bars, with dividends excluded for every book and for SPY;
- outcomes after the 2-session finality lag.

**Decisions from 2026-10-26**, the same day as cohort 0.
- Rebalance phases count from that day.
- Every book invests at its first recorded decision.
- Sessions before cohort 1's genesis are recorded as `MISSED_OBSERVATION (BEFORE_SERIES_GENESIS)`, never filled in.

**The golden behaviour fingerprint** (`PINNED_FINGERPRINT_C1` = `09e110d6…`) is checked on every run. A change in the decision logic, including a change to `discovery.v3.design`, stops the series. Any such change needs a new series id.

## 4. What to expect

At the measured tracking errors, a claim at 1,000 within 10 years needs this gross yearly edge against the twin:

| Design | Edge needed for a claim in 10 years | Years to a claim at a 1% a year edge | At 2% a year |
|---|---|---|---|
| `mom-full` | 1.9% | 24.5 | 9.4 |
| `rev-full` | 2.1% (it pays about 0.33% a year more in costs) | 62.9 | 10.9 |
| `hi52-full` | 2.0% | 27.7 | 9.9 |
| `composite-full` | 1.8% | 33.3 | 8.7 |
| `lowvol-full` | **0.9%** | **9.0** | **3.5** |

These figures use the bounded twin test, the known-bet rate and the study's measured extra costs. They are roughly three times faster than cohort 0's tilts at the same edge. The paper allocation follows evidence long before a claim. On DEV, no tilt beat equal weight by more than noise; those means are contaminated and select nothing.

## 5. Records, verification and activation

**Records.** Each run posts the same record types as cohort 0, to cohort 1's own ledger:
- `SERIES_GENESIS`;
- `DECISION`;
- `OUTCOME`, with the daily net returns replayed from the committed targets and the tournament state;
- `MISSED_OBSERVATION`.

**Verify:**

```bash
apex research forward-shadow verify --lane tournament-c1 --store github
apex research forward-shadow export --lane tournament-c1 --store github --out DIR
```

**Activation.** It is activated by the same switch as cohort 0, `APEX_TOURNAMENT_LANE` = `on`:
- If the switch is already on when this registration merges, the next scheduled run creates cohort 1's ledger issue and posts its genesis record.
- Deleting the variable pauses both cohorts.

**Budget.** Cohort 1 runs inside cohort 0's job:
- one market-data fetch is shared;
- the 8-minute job timeout and the minutes reservation (`apex_worker.release.budget`) are unchanged;
- its fingerprint check takes about 5 s.

## 6. Limitations

- **Cohort 0's limitations apply:**
  - price returns (dividends excluded);
  - U1 is today's S&P 500 list;
  - the tilts are tilts of equal weight.
- **The loss bounds come from DEV.** A crash worse than March 2020 relative to the twin can break one. That voids only that design's bounded twin test; its assumption-free twin test and its evidence against SPY stand.
- **The betas are estimated** from 252 sessions, so the neutrality to the true beta is approximate. The study measured mean absolute beta exposure of about 0.001 against about 0.035 for cohort 0's tilts.
