# Evidence engine battery: results

| | |
|---|---|
| Script | `apps/pi-worker/bench/epa_battery.py`. Synthetic and seeded; no market data is read |
| Outputs | `docs/evidence/battery/{size,velocity,multiverse}.json`, byte-for-byte the script's stdout |
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

**Setup.**
- 1,000 universes per scenario, in two worlds of 10 candidates each.
- Each universe has a 7-year backtest window followed by a 10-year deployment window.
- Candidate 0's alpha is given as (backtest / deployment years 0–3 / deployment years 3–10). Every other candidate carries the world's null alpha (its costs).
- A **mirage** is an edge that exists only in the backtest: survivorship, forking paths or overfitting. That is the program's situation on DEV.
- Tournament policies (P2–P2d) admit all 10 candidates at the start of deployment with null mass 0.5. Their claims need evidence of 1/(0.05 γ_k), which is 200 under a uniform prior.
  - Each policy claims on one test per candidate, so that threshold controls its family.
  - The prospective lane claims on up to four tests per candidate, against SPY and against the twin, each assumption-free and bounded. It splits γ_k equally across them, so each test needs m_k/(0.05 γ_k) ([TOURNAMENT_LANE.md](TOURNAMENT_LANE.md) §1).
  - Its false-claim rate can therefore only be lower than the rates below, and its claims come later by at most log 4 ≈ 1.4 nats of evidence.

**Policies.**
- P1 holds the candidate with the best backtest log growth.
- P1h holds the best candidate only if its one-sided p passes Holm's first step over the 10 candidates, otherwise SPY. This is the program's current rule.
- P2/P2b use the mixture book (the literal identity).
- P2c/P2d use the evidence-weighted book, with bounded-loss evidence while the bound holds.
- A "backtest prior" sets γ ∝ exp(backtest t-statistic): valid, because it is fixed before deployment.
- P3, the oracle, knows the true alpha path and holds the best constant bet up to 1.

### Concentrated long-only books (tracking error 10%–25%, null alpha -1.0%, loss bound 0.2)

Mean relative log growth against SPY, % a year over the 10 deployment years; in brackets, the share of universes ending 20% or more behind SPY.

| Policy | null (-1.0% / -1.0% / -1.0%) | mirage (+10.0% / -1.0% / -1.0%) | weak-edge (+4.0% / +4.0% / +4.0%) | strong-edge (+12.0% / +12.0% / +12.0%) | decay (+12.0% / +12.0% / -1.0%) |
|---|---|---|---|---|---|
| P0 SPY only | +0.00 (0%) | +0.00 (0%) | +0.00 (0%) | +0.00 (0%) | +0.00 (0%) |
| P1 deploy the best backtest | -3.13 (55%) | -3.07 (55%) | -0.94 (40%) | +7.51 (13%) | +0.03 (31%) |
| P1h deploy if Holm passes (current rule) | -0.05 (1%) | -0.45 (10%) | +0.06 (2%) | +2.86 (1%) | +0.38 (6%) |
| P2 tournament, mixture book, uniform prior | -0.30 (0%) | -0.32 (0%) | -0.13 (0%) | +0.20 (0%) | -0.16 (0%) |
| P2b mixture book, backtest prior | -0.30 (0%) | -0.32 (0%) | -0.01 (0%) | +1.45 (0%) | +0.10 (0%) |
| P2c evidence-weighted book, uniform prior | -0.72 (14%) | -0.77 (16%) | -0.34 (12%) | +1.02 (4%) | -0.41 (10%) |
| P2d evidence-weighted book, backtest prior | -0.74 (17%) | -0.80 (19%) | -0.09 (12%) | +3.73 (2%) | +0.15 (9%) |
| P3 oracle (knows the truth) | +0.00 (0%) | +0.00 (0%) | +2.31 (19%) | +10.39 (2%) | +2.93 (6%) |

Claims per universe, false / true (a false claim names a candidate whose null held through the claim):

| Policy | null | mirage | weak-edge | strong-edge | decay |
|---|---|---|---|---|---|
| P1h deploy if Holm passes (current rule) | 0.027 / 0.000 | 0.189 / 0.000 | 0.028 / 0.031 | 0.012 / 0.275 | 0.008 / 0.291 |
| P2 tournament, mixture book, uniform prior | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 |
| P2b mixture book, backtest prior | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 |
| P2c evidence-weighted book, uniform prior | 0.002 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.030 | 0.000 / 0.000 |
| P2d evidence-weighted book, backtest prior | 0.001 / 0.000 | 0.001 / 0.000 | 0.000 / 0.002 | 0.000 / 0.110 (void 0.001) | 0.000 / 0.004 |

### Diversified index tilts (tracking error 2%–4%, null alpha -0.5%, loss bound 0.05)

Mean relative log growth against SPY, % a year over the 10 deployment years; in brackets, the share of universes ending 20% or more behind SPY.

| Policy | null (-0.5% / -0.5% / -0.5%) | mirage (+5.0% / -0.5% / -0.5%) | weak-edge (+2.0% / +2.0% / +2.0%) | strong-edge (+4.5% / +4.5% / +4.5%) | decay (+4.5% / +4.5% / -0.5%) |
|---|---|---|---|---|---|
| P0 SPY only | +0.00 (0%) | +0.00 (0%) | +0.00 (0%) | +0.00 (0%) | +0.00 (0%) |
| P1 deploy the best backtest | -0.55 (6%) | -0.53 (4%) | +1.50 (1%) | +4.43 (0%) | +0.94 (0%) |
| P1h deploy if Holm passes (current rule) | -0.00 (0%) | -0.51 (4%) | +0.52 (0%) | +3.94 (0%) | +0.83 (0%) |
| P2 tournament, mixture book, uniform prior | -0.11 (0%) | -0.11 (0%) | -0.05 (0%) | +0.02 (0%) | -0.07 (0%) |
| P2b mixture book, backtest prior | -0.11 (0%) | -0.11 (0%) | +0.16 (0%) | +0.94 (0%) | +0.17 (0%) |
| P2c evidence-weighted book, uniform prior | -0.26 (0%) | -0.26 (0%) | -0.04 (0%) | +1.18 (0%) | -0.15 (0%) |
| P2d evidence-weighted book, backtest prior | -0.26 (0%) | -0.25 (0%) | +0.52 (0%) | +3.19 (0%) | +0.41 (0%) |
| P3 oracle (knows the truth) | +0.00 (0%) | +0.00 (0%) | +1.93 (0%) | +4.46 (0%) | +1.31 (0%) |

Claims per universe, false / true (a false claim names a candidate whose null held through the claim):

| Policy | null | mirage | weak-edge | strong-edge | decay |
|---|---|---|---|---|---|
| P1h deploy if Holm passes (current rule) | 0.006 / 0.000 | 0.937 / 0.000 | 0.007 / 0.271 | 0.001 / 0.881 | 0.002 / 0.884 |
| P2 tournament, mixture book, uniform prior | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 |
| P2b mixture book, backtest prior | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 |
| P2c evidence-weighted book, uniform prior | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.001 | 0.000 / 0.345 | 0.000 / 0.000 |
| P2d evidence-weighted book, backtest prior | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.015 | 0.000 / 0.804 (void 0.003) | 0.000 / 0.006 |

**Reading.**
1. **Validity.** The current rule validates mirages: in **19%** of concentrated-book mirage universes and **94%** of tilt mirage universes. Every tournament variant stays at or below 0.002 false claims per universe in every scenario.
2. **No edge anywhere.**
   - Naive backtest selection loses 3.1% a year against SPY on concentrated books, and 55% of universes end 20% or more behind.
   - The current rule stays close to SPY.
   - The tournament costs 0.1–0.3% a year with the mixture book, and 0.3–0.8% with the evidence-weighted book.
3. **An honest strong edge.**
   - Naive selection gains most on average (+7.5% / +4.4% a year), with heavy tails.
   - The prior-informed evidence-weighted book (P2d) earns **+3.7%** on concentrated books, above the current rule's +2.9%, and **+3.2%** on tilts (81% of the current rule's +3.9%).
   - P2d makes valid claims in 11% (books) and 80% (tilts) of universes within the 10 years.
4. **The trade-off.** On concentrated books the evidence-weighted book holds candidates at full weight. It ends 20% or more behind SPY in 14–19% of no-edge universes; the mixture book never does. On diversified tilts, no tournament variant ever ends 20% behind. The design rule again: diversified designs make the tournament both faster and safer.
5. **Where a backtest is honest, it is informative.** In the strong-edge and decay scenarios, the backtest window carries the true edge, so backtest rules capture more in the early years. In decay, the tournament's slower start costs more than its quicker exit saves (+0.15% / +0.41% against the current rule's +0.38% / +0.83%). The tournament's advantage is that it never needs the backtest to be honest, and the program's DEV history is not.
6. **No method approaches the oracle on concentrated books** (+10.4% with a strong edge). The binding constraint is evidence, not allocation.
