# Evidence engine battery: results

| | |
|---|---|
| Script | `apps/pi-worker/bench/epa_battery.py`. Synthetic and seeded; no market data is read |
| Outputs | `docs/evidence/battery/{size,velocity}.json` (and `multiverse.json` once its run completes), byte-for-byte the script's stdout |
| Run | 2026-10-23, numpy 2.5.4, Python 3.13 |
| Commands | `uv run python bench/epa_battery.py size 4000`<br>`... velocity 400`<br>`... multiverse 1000` |
| Method | [EVIDENCE_TOURNAMENT.md](EVIDENCE_TOURNAMENT.md) |

**The simulated market** is fat-tailed, with clustered volatility:
- **SPY:** GARCH(1,1) with t(5) shocks, 8% a year drift and 18% volatility.
- **A candidate:** SPY plus a drift α(t), plus tracking noise. The noise shares SPY's volatility state and a common factor across candidates (correlation 0.3), with t(4) shocks.
- **Costs:** sub-account rebalancing is charged 2 bps per side, the measured close cost (`cost-v3-2`). The bounded-loss evidence is a statistic, not a portfolio, and carries no charge.
- **Evidence level:** 20 (α = 0.05).

## A. Size under continuous monitoring

Setup: 4,000 null paths of 10 years. Tracking error is 15%. The exact null has α = 0, and the cost null has α = −1% a year.

**False-claim rate:** the share of null paths on which the rule *ever* claims an edge within 10 years.

| Rule | Exact null | Cost null |
|---|---|---|
| e-process, assumption-free (λ ≤ 1) | **0.000** | **0.000** |
| e-process, bounded loss L = 0.2 (valid paths) | **0.0018** | **0.0015** |
| t-test (one-sided 5%), read **every session** from session 63 | **0.338** | 0.294 |
| t-test read every 21 sessions | 0.288 | 0.249 |
| t-test read every 252 sessions | 0.169 | 0.130 |
| t-test read **once**, at 10 years | 0.050 | 0.032 |

The bound L = 0.2 broke on 1.3% of paths, because t(4) tails are heavier than a diversified book's. Those paths are void, not counted as claims.

**Reading.**
- A fixed-horizon test is valid only if it is read once.
- The forward shadow publishes every session. Reading its interim results with the registered fixed-horizon test inflates false claims about **sevenfold** (5% → 34%).
- The e-process stays below its nominal 5% under any reading.

## B. Evidence velocity

Setup: 400 paths of 30 years per case. The bet is unknown (λ-grid mixture), compared with analytic values that assume the best constant bet is known.

| Case | IR | L | Assumption-free: P(e ≥ 20 within 10 y) | Assumption-free: within 20 y | Assumption-free: within 30 y | Bounded: within 10 y | Bounded: within 20 y | Bounded: simulated median (years) | Bounded: analytic, known bet (years) | Information limit (years) | Bound broken |
|---|---|---|---|---|---|---|---|---|---|---|---|
| G2 at its DEV estimate (log growth +8.6%, TE 26.8%) | 0.46 | 0.2 | 0.000 | 0.023 | 0.155 | 0.095 | 0.225 | > 30 | 28.9 | 28.9 | **26% of paths** |
| Concentrated book, +12% at TE 15% | 0.80 | 0.2 | 0.000 | 0.000 | 0.105 | 0.248 | 0.773 | 14.4 | 9.6 | 9.4 | 2.5% |
| Diversified tilt, 3% at TE 3% | 1.00 | 0.05 | 0.000 | 0.000 | 0.000 | 0.300 | 0.903 | 12.4 | 7.6 | 6.0 | 1.5% |
| Diversified tilt, 4.5% at TE 3% | 1.50 | 0.05 | 0.000 | 0.000 | 0.000 | **0.858** | 0.988 | **7.3** | 4.5 | 2.7 | 1.3% |

**Reading.**
1. **Evidence = money, simulated.** Without an assumption, no realistic candidate reaches evidence 20 within 20 years, except G2 on 2% of paths. The unlevered paper book must first multiply its wealth relative to SPY about 20-fold, and more after the grid's prior.
2. **A declared loss bound is where speed comes from**, and it must suit the design:
   - on a 26.8%-TE concentrated book, L = 0.2 broke on a quarter of the paths, voiding them;
   - on diversified tilts, L = 0.05 held on about 99%.
3. **Only high-information designs are provable within a decade:** IR 1.5 had an 86% chance within 10 years. For IR ≤ 1 the median is above 12 years.
4. The simulated mixture is 3–5 years slower than the known-bet analytic time. That is the price of not knowing the bet (the grid's prior), plus the grid's top bet (0.9/L) sitting below the optimum for very low-TE tilts.

## C. Multiverse of allocation policies

**Pending.** The 1,000-universe run (two worlds × five scenarios) is in progress. Its results will be added here, unedited, together with `battery/multiverse.json`.
