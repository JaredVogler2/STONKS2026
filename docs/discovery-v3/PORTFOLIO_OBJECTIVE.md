# Discovery V3 portfolio objective (pre-registered before any V3 outcome)

**Code:** `apex_worker.discovery.v3.objective.PRINCIPAL_OBJECTIVE`, id `obj-v3-1`. Every trial records the objective sha256. Changing any value below is a new objective, and trials registered under the old one stay attached to it (test ECO12).

## 1. Unit of evaluation

The unit is a whole account:
- frozen starting equity;
- cash;
- open positions;
- competing signals;
- costs;
- what idle capital holds.

A signal's hit rate, paired edge or "quality" score is secondary. The baseline audit shows why: S1's +0.33 pp paired edge came with a book that lost to SPY at the same exposure.

## 2. Principal estimand

**Annualised log growth of the policy's account minus that of the investable alternative it competes with.** Returns are net of 25 bps per side, on pessimistic fills, over the DEV window.

| Policy's idle capital | Comparator | Why |
|---|---|---|
| CASH (the protocol-2 book) | exposure-matched SPY/cash: the previous close's equity exposure in SPY, the rest in cash | the same market exposure, timed without hindsight |
| SPY (the idle sleeve holds SPY) | SPY buy-and-hold | the account is fully invested, so full SPY is the fair alternative |
| WEIGHTS agents (rebalanced allocations) | SPY buy-and-hold | — |

**Uncertainty.** A dependent block bootstrap of the **daily active return**, using protocol-2's conservative rule (largest p and smallest lower bound over L = 20 and 40, with the Student-t floor). Holm is applied over **every registered trial of the batch** (m counts ablations).

**Minimum interesting effect.** +1.0 pp a year of log growth. α = 0.05, one-sided, after Holm.

## 3. Hard constraints (a violation means REJECTED_DEV)

- **Leverage and caps.** No leverage. At most 10 positions. Sector cap 30% of previous-close equity.
- **Maximum drawdown.** Not below −30%.
- **Turnover.** At most 40× equity a year.
- **Activity.** Average exposure at least 5%, so "always cash" is never mistaken for skill.

## 4. Owner scenarios (illustrations, fixed here)

U = log growth − λ_es · 10 · |ES5 daily| − λ_dd · |MaxDD| − λ_to · turnover per year

| Scenario | λ_es | λ_dd | λ_to |
|---|---|---|---|
| conservative | 0.6 | 0.6 | 0.002 |
| balanced | 0.3 | 0.3 | 0.001 |
| growth | 0.1 | 0.1 | 0.0005 |

These are illustrations of trade-offs, not a personalised financial plan. Raw metrics are always shown next to U, and there is no 0–100 score.

## 5. What every result reports

**Account metrics:**
- total return; CAGR (one year or more only); log growth;
- volatility, downside volatility, maximum drawdown, ES5, worst day;
- average stock exposure and idle-SPY exposure; cash-day share;
- trades taken and skipped (by reason); maximum positions;
- turnover, costs paid;
- net return per capital-day deployed.

**Benchmarks (the same sessions and clock):** cash, SPY buy-and-hold, and exposure-matched SPY/cash.

**Active tests:**
- versus the comparator;
- versus SPY;
- versus the **matched-control books** (the same execution, allocation and dates, other stocks).

**Attribution (`portfolio.decompose`):**

| Term | Definition |
|---|---|
| market | exposure-matched SPY/cash |
| selection | policy − mean matched-control book |
| friction | policy − the same policy at zero cost |
| cash drag | exposure-matched − full SPY |
| beta | the account's beta to SPY |
| residual | the explicit remainder: timing, allocation and interaction, never hidden |

**Pareto frontier** over log growth, |MaxDD|, |ES5| and turnover. It is descriptive and DEV-only, and never a selection rule.

## 6. Benchmark fairness and accounting rules (tested)

**Accounting (tested):**
- **Funding.** Entries are funded from the session's opening cash; exit proceeds arrive after that session's entries (ECO09).
- **Hard limits.** Never negative cash, never leverage, never more than the slot count (ECO01). Same-day candidates are taken in priority order.
- **Gaps.** A gap through a stop loses more than 1R; there is no fantasy exit at the stop (ECO05). A no-fill counts as zero for the opportunity (ECO07).
- **Equivalence.** The cash-idle book is **identical** to `research.book.run_book`, including its exposure-matched comparator (asserted in a test).

**Idle SPY (tested):**
- An entry first spends cash, then sells SPY at that session's open.
- At the close, all cash is swept into SPY at 5 bps per side (an assumption, stated).

**Limitations, stated with every result:**
- price-only returns (no dividends for stocks or SPY);
- cash yield 0;
- survivorship-limited U1;
- daily bars.

## 7. Dispositions (pre-registered)

| Disposition | When |
|---|---|
| `REJECTED_DEV` | a constraint is violated, or active log growth versus the comparator is ≤ 0 |
| `PROMISING_EXPLORATORY` | active ≥ +1 pp a year **and** the Holm p (all trials) < 0.05 on DEV. This is only a nomination for a separately registered prospective shadow, never evidence of a future edge |
| `INCONCLUSIVE_DEV` | otherwise |
| `SOURCE_BLOCKED` | a required input is unavailable or not point-in-time |
| `SIMULATOR_INVALID` | the engine self-checks failed (the look-ahead trap was not detected, or the placebo fired) |

**Aggregate.** It stays **NO_TRADE** under every outcome.
