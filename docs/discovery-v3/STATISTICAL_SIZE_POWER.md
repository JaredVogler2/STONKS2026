# Discovery V3: size and power of the principal test (measured before the pilot's results)

**The test.** The daily active return of a book against its investable alternative. It uses protocol-2's dependent block bootstrap (conservative over L = 20 and 40, Student-t floor), via `portfolio.active_test`.

**Data.** Synthetic and seeded only: no market data. Script: `apps/pi-worker/bench/v3_size_power_battery.py`.

## 1. Production-shaped daily active series (2,000 replicates per row)

**Command:**

```bash
cd apps/pi-worker && uv run python bench/v3_size_power_battery.py series 2000
```

**Model.** Each replicate is 1,720 sessions (the DEV decision window) with:
- daily active sd 0.4% (assumed; a half-invested swing book against its exposure-matched alternative);
- AR(1) of 0.15;
- t(4) shocks;
- a 3× volatility regime for a third of the window;
- 10% beta leakage on a fat-tailed market.

**Results:**

| True active edge | Reject at 5% | Reject at 1.25% | Reject at 0.5% (Holm first step, m = 10) |
|---|---|---|---|
| none (size) | **4.1%** (MCSE 0.44%) | 0.85% | 0.15% |
| +1 pp/yr | 5.8% | 1.25% | 0.4% |
| +3 pp/yr | 10.7% | 3.15% | 1.2% |
| +5 pp/yr | 20.9% | 7.85% | 2.95% |
| +10 pp/yr | 49.7% | 27.1% | 15.1% |

## 2. Real book construction, true null (40 replicates)

**Command:**

```bash
cd apps/pi-worker && uv run python bench/v3_size_power_battery.py books 40
```

**Setup.** On the synthetic factor fixture (4 stocks per sector):
- a **random** eligible stock every 5th DEV session, traded with protocol-2's E0X1 execution in the cash-idle book;
- tested against the mean of its 10 matched-control books (identical execution, allocation and dates).

Both sides are random, so the null is exact.

**Results:**

| Reject at 5% | Reject at 1.25% | Mean p |
|---|---|---|
| 7.5% (3/40, MCSE about 4.2 pp) | 2.5% | 0.43 |

This is consistent with nominal size, but only 40 replicates are affordable here (each one runs the full arm with controls). Read it as "no gross over-rejection", not as a precise size.

## 3. Consequences, registered before the pilot's results

1. **Size.** The rule is close to nominal or slightly conservative on daily active book returns. The protocol-2 caveat still applies to per-signal tests: 5.8–6.6% at nominal 5%.
2. **Power is very low on DEV.** An edge of +1 pp a year (the economic minimum in `obj-v3-1`) is essentially undetectable: about 6% power at 5%, and about 0.4% at the Holm first step for 10 trials. Even +10 pp a year is found only about half the time at 5%.
3. **Interpretation.** Pilot 1 can **reject large claims**, show when a policy loses to its alternative, and nominate very large effects for a prospective test. An `INCONCLUSIVE_DEV` result says nothing about small edges. A `PROMISING_EXPLORATORY` result on contaminated DEV history, after a search, is still likely to overstate the effect (winner's curse).
4. **What could change this.** Only longer **untouched** data, i.e. the forward shadow, or a legitimately independent point-in-time universe. Pooling families does not help, and neither does relabelling history.
