# Prospective evidence tournament lane (`v3x-epa-tournament-v1`)

| | |
|---|---|
| Status | **Recording since 2026-10-23**, when the owner set the repository variable `APEX_TOURNAMENT_LANE` to `on` (§6).<br>- The first real pass, [run 40013334668](https://github.com/JaredVogler2/STONKS2026/actions/runs/40013334668) on `a767041`, posted the genesis record at 19:41:19 UTC.<br>- Fetched back and verified: digest `b1d7b677…`, configuration sha256 `888f3ade…`, behaviour fingerprint `f6064b1f…`.<br>- The first decision is for session 2026-10-26. Deleting the variable pauses recording |
| Evidence tier | `PROSPECTIVE_EXPLORATORY`, never confirmatory. Interim reading is valid by design (anytime-valid evidence) |
| Mode | **Research only:**<br>- paper books;<br>- no orders, alerts, Discord signals or promotion;<br>- `HIGH_CONVICTION` and `GROUP_ACTIONABLE` stay disabled;<br>- the frozen lane `qv2a1-frozen-v1` is untouched |
| Method | [EVIDENCE_TOURNAMENT.md](EVIDENCE_TOURNAMENT.md) (why), [EPA_BATTERY.md](EPA_BATTERY.md) (simulated behaviour) |
| Code | - Decision logic: `apex_worker/discovery/v3/tournament_books.py`.<br>- Lane, replay and recorder: `apex_worker/research/forward/tournament_lane.py`.<br>- Registry entry: `research/forward/exploratory.py::EPA_TOURNAMENT`.<br>- Engine: `apex_worker/evidence/` |
| Workflow | `.github/workflows/apex-evidence-tournament.yml`, weekdays at 18:42 and 22:22 New York time. One pass records every cohort (cohort 1: [TOURNAMENT_COHORT_1.md](TOURNAMENT_COHORT_1.md)) from one market-data fetch |
| Ledger | [Issue #35](https://github.com/JaredVogler2/STONKS2026/issues/35), "APEX forward-shadow ledger · v3x-epa-tournament-v1 · vol 1", created by the first real run. It has its own sha256 chain, and GitHub's comment timestamps are the independent anchor |
| Approved | By the owner, 2026-10-23. Corrected before genesis, the same day: each candidate's prior mass is split across its claim tests, so every claim of every candidate shares one α (§1). The genesis record carries the corrected configuration |

## 1. Registered slate (cohort 0)

The slate is fixed in the series configuration, and its sha256 is in the genesis record. Prior mass totals **0.45**. The other **0.55** is reserved for later cohorts (§8).

**Claims.** A candidate is tested up to four ways: against SPY and against its twin, each assumption-free and under its declared loss bound. Its prior mass is split equally across those claim tests (`claim_rule` in the configuration), so each test needs evidence of tests / (0.05 × prior mass):
- 4 / (0.05 × 0.10) = **800** for `g2` and the three tilts;
- 2 / (0.05 × 0.05) = **800** for `ew` (against SPY only).

Over every test of every candidate ever admitted, the chance of any false claim is then at most α Σγ ≤ 0.05 (a union bound, valid under any dependence). Claims under a loss bound hold while the bound holds; a break voids them.

| Candidate | Book | Twin (negative control) | Prior mass | Loss bound vs SPY | Loss bound vs twin | Rebalance | Why it is in the slate |
|---|---|---|---|---|---|---|---|
| `g2` | `g2` | `g2-twin` | 0.10 | 0.20 | 0.20 | every 60 sessions | Pilot 2's strongest DEV result. It is concentrated, so evidence takes decades (method §2); it is monitored honestly |
| `mom-tilt` | `mom-tilt` | `ew` | 0.10 | 0.05 | 0.03 | every 20 | The same momentum signal as a diversified tilt. It tests the information-rate design rule directly against G2 |
| `rev-tilt` | `rev-tilt` | `ew` | 0.10 | 0.05 | 0.03 | every 5 | Short-term reversal (liquidity provision). Weekly turnover is affordable only because close execution costs about 2 bps |
| `hi52-tilt` | `hi52-tilt` | `ew` | 0.10 | 0.05 | 0.03 | every 20 | Proximity to the 52-week high (anchoring; George and Hwang 2004) |
| `ew` | `ew` | — | 0.05 | 0.05 | — | every 20 | Equal weight over the mask. It is every tilt's twin, and also its own claim against SPY |

**Tournament settings:**
- the evidence-weighted book, with null mass 0.5 (half the paper book stays in SPY until evidence moves it);
- α = 0.05;
- bet grid {1/16, 1/8, 1/4, 1/2, 3/4, 1};
- rebalancing charge 2 bps per side.

## 2. Books

All books share three rules:
- **Mask:** every book reads only the explicit eligibility mask at t:
  - U1 membership;
  - close ≥ $10;
  - 20-session median dollar volume ≥ $30M;
  - 20-session mean volume ≥ 1M shares;
  - at least 180 bars;
  - no risk-off breaker.
- **Weights:** every target is long-only and unlevered. It is stored as integer micro-weights summing to exactly 1,000,000, so it replays exactly.
- **Schedule:** rebalance sessions are counted from the first decision session (2026-10-26 is phase 0). A book also invests at its first recorded decision, so a late activation never leaves it in SPY until the next scheduled rebalance. A missed rebalance session is not made up: the book holds until the next one.

The books:
- **`g2`:** equal weight in the top 10 by 60-session log return. Held names stay while they rank below 20 (a turnover buffer). If fewer than 10 qualify, the rest stays in cash, as registered in pilot 2.
- **`g2-twin`:** the mean of 20 random buffered books with the same mask, schedule and buffer. They are ranked by draws seeded with (book seed, sub-book, protocol session index), so they are reproducible from the data alone.
- **`ew`:** equal weight over the mask.
- **Tilts:** weight ∝ 1 + 0.5 z over the mask, where z is the name's cross-sectional rank of the signal mapped to [−1, 1]. Each name therefore holds between 0.5× and 1.5× the equal weight. A name without the signal gets z = 0. The signals:
  - `mom-tilt`: the 60-session log return;
  - `rev-tilt`: minus the 5-session log return;
  - `hi52-tilt`: close / 252-session high close − 1.

The inputs come from a light panel with the same definitions as `features.compute`. On the golden fixture it reproduces the decisions of the full `study_v2.prepare` pipeline exactly: the same pinned fingerprint.

## 3. Timing, costs and data

| | |
|---|---|
| Decision | After the close of t, with data through t. The recorder must post it before the next open, or the session is recorded as missed and never filled in later |
| Execution (paper) | At the close of t + 1. Every book starts fully in SPY |
| Returns | Close to close on split-adjusted Alpaca SIP bars, the same data as every published V3 result. **Dividends are excluded for every book and for SPY** (price returns; §7) |
| Costs | Measured `cost-v3-2`: 2 bps per side for stocks at the close, 5 bps for SPY, 0 for cash |
| Outcomes | After the 2-session finality lag, on final bars. A quarantined (symbol, session) pair and a missing bar both earn nothing that day |

## 4. Records and verification

- **`DECISION`:** each book's targets for session t, or `null` for hold, plus the source metadata and the observation window.
- **`OUTCOME`:** posted for a decision once its session is final. It contains:
  - the daily net returns of every book and of SPY for the sessions since the previous outcome, computed by replaying the committed targets;
  - the tournament state after them (evidence, allocation, claims);
  - any revision of a previously posted return.

  The state is a pure function of the posted returns, so anyone can replay it (`tournament_lane.tournament_state`).
- **Verify:**

  ```bash
  apex research forward-shadow verify --lane tournament --store github
  apex research forward-shadow export --lane tournament --store github --out DIR
  ```
- **The run fails closed, and posts nothing, when any of these happens:**
  - the decision logic changes, detected by the golden behaviour fingerprint (`PINNED_FINGERPRINT`, checked every run);
  - the series configuration changes;
  - the chain does not verify;
  - a record contains a raw bar field or a secret.

  Any such change needs a new series id.

## 5. What to expect

Most of the slate will show weak evidence for years. That is the market's information limit, not a malfunction (method §2):
- **G2:** about 35 years, even if its DEV estimate were exactly real.
- **A diversified tilt with a real IR of 1.5:** a simulated median of about 7 years to evidence 20 under its loss bound; IR 1.0 takes about 12 years.
- **Allocation moves earlier than claims.** The paper allocation follows evidence continuously, while a claim needs 800× evidence on one test.
- **Reading the job summary or the ledger at any time is valid.** No result here authorizes trading.

## 6. Activation and pausing (owner)

1. Merge the PR that adds this lane. Until step 2, the workflow only runs dry rehearsals; their job summaries show what would have been recorded.
2. Go to **Settings → Secrets and variables → Actions → Variables → New repository variable**. Set the name to `APEX_TOURNAMENT_LANE` and the value to `on`.
3. The next scheduled run does the following:
   - creates the ledger issue;
   - posts the genesis record;
   - records the sessions from 2026-10-26 until then as `MISSED_OBSERVATION (BEFORE_SERIES_GENESIS)`, which raise no alarm;
   - records the current session's decision.
4. **Pause:** delete the variable. Sessions closed while paused are recorded as missed when it is set again; they are never filled in.

**Budget.** Two weekday attempts at most 8 minutes each are reserved in `apex_worker.release.budget`. With them, the worst-case month for every APEX workflow is 1,473 of the default 1,500 minutes, in December 2026. Two consequences:
- The optional drift job skips more often late in a month.
- A manual research run (`apex-discovery-v3`, 45-minute timeout, checked before it fetches any data) fits every month of the next two years except December 2026, where 1,473 + 45 > 1,500.

To run research that month, the owner can raise the repository variable `APEX_ACTIONS_MONTHLY_BUDGET`. The default leaves 500 of the 2,000 free private-repository minutes for CI.

## 7. Limitations (disclosed with the registration)

- **Price returns.** Dividends are excluded throughout. Books that yield less than SPY, such as momentum books, gain roughly the yield gap ([review](CODEBASE_REVIEW.md), C-4). Total-return data needs a new series.
- **Equal-weight base.** No free cap weights are wired yet, so the tilts are tilts of the equal-weight universe. Their evidence against SPY is dominated by equal-weight versus cap-weight performance. **Their twin (`ew`) claim is the mechanism test.** Cap-weighted tilts need shares outstanding (the SEC company-facts API, which needs the SEC contact; §8).
- **Universe.** U1 is today's S&P 500 list with `date_added` membership. Prospectively, later index additions are missing and removals stay listed until the list is refreshed, which needs a new series.
- **G2 is concentrated.** Its 0.2 loss bound can break in a crash. That voids its bounded evidence; it does not void the assumption-free evidence.

## 8. Later cohorts

The reserved prior mass (0.55) is for candidates admitted after genesis.

**Registered: cohort 1** ([TOURNAMENT_COHORT_1.md](TOURNAMENT_COHORT_1.md), series `v3x-epa-tournament-c1-v1`, prior mass 0.20; the reserve left is 0.35):
- the neutralized tilts designed for information rate, with sector-, beta- and size-neutral active weights;
- [`design-study-1`](DESIGN_STUDY_1.md) measured them on DEV ([results](../../research-results/discovery-v3/design-study-1-2026-10-23/REVIEW.md)): about 42% less tracking error against the twin, so about 3× faster evidence at the same alpha;
- they are claimed against the twin only;
- the same workflow records them, from the same data fetch.

**Measured: SEC-event designs** ([`design-study-2`](DESIGN_STUDY_2.md), [results](../../research-results/discovery-v3/design-study-2-2026-10-25/REVIEW.md)):
- Form 4 insider purchases, the earnings-announcement premium and drift after the announcement return, point-in-time from public SEC filings;
- on DEV, their tracking error against the twin is 0.69–0.82%, the lowest measured, mostly because their tilts are small. A claim at 1,000 within 10 years needs a gross alpha of 1.1–1.4% a year;
- the proposal is all three at prior mass 0.04 each, 0.12 of the reserve. A cohort from it needs the owner's decision, its own registration and live event recording.

Planned:
- the frozen S1 and S3 arms as paper books, for a valid interim reading of the frozen shadow;
- cap-weighted tilts (shares outstanding from the SEC's company facts, with the same contact).

**How a cohort is added: as its own series.** This series' slate never changes. A later cohort gets its own series id, genesis, chain and ledger. Its configuration names the earlier cohorts and their masses, and it draws its own masses from the reserve, so the total over every cohort stays at most 1.
- Claims stay family-wise valid across series. The union bound behind §1 needs only that every mass is fixed before its candidate's first period, and that the masses sum to at most 1.
- An in-ledger admission inside this series (a cohort announced in a `DECISION` record) would also be valid, but it is not implemented and not needed.
- `tournament_lane.COHORTS` lists every cohort, and `assert_mass_budget` (checked by the tests) keeps their masses within 1.
