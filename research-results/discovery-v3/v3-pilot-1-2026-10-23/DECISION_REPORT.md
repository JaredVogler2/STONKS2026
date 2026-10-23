# Discovery V3 pilot 1: decision report

**Decision: NO_TRADE.** No live, paper-actionable or group-actionable output follows from this batch. Nothing is nominated to the prospective exploratory lane, and the frozen forward shadow (`qv2a1-frozen-v1`) continues unchanged.

| | |
|---|---|
| Question | On protocol-2's DEV history, does any of four portfolio-level mechanisms beat its **investable alternative**, net of costs? |
| Answer | **No: 0 of 10 registered trials.** Every point estimate of active log growth is negative |
| Evidence tier | `DEV_DIAGNOSTIC` (contaminated history, U1 survivors). These are diagnostics, never validation |
| Inputs | [REVIEW.md](REVIEW.md) (provenance, integrity checks, disclosed findings A-1, O-1, A-2, R-1); [results.json](results.json); [preregistration](../../../docs/discovery-v3/PILOT_PREREGISTRATION.md); [size and power](../../../docs/discovery-v3/STATISTICAL_SIZE_POWER.md) |

## 1. Registered result

SPY buy-and-hold over the same 1,757 sessions returned **+13.5% a year** (log growth), with a max drawdown of −34.2%. All figures are net of 25 bps per side (5 bps for SPY-only agents).

| Family | Policy | Log growth / yr | Comparator | Active / yr | One-sided p | Disposition |
|---|---|---|---|---|---|---|
| F1 leaders | S1, idle in SPY (primary) | +6.9% | SPY | **−6.6 pp** | 0.99 | REJECTED_DEV |
| | S1, idle in cash | −1.3% | exposure-matched SPY/cash (+5.4%) | −6.7 pp | 0.99 | REJECTED_DEV |
| | S1, idle in SPY, 40-session horizon | +5.5% | SPY | −8.0 pp | 0.98 | REJECTED_DEV |
| F5 momentum | top 10 by 60-session return, equal weight, monthly (primary) | +11.6% | SPY | **−1.9 pp** | 0.50 | REJECTED_DEV |
| | random 10, same schedule | −1.5% | SPY | −15.1 pp | 1.00 | REJECTED_DEV |
| | momentum, cash in risk-off / shock | +10.2% | SPY | −3.3 pp | 0.57 | REJECTED_DEV |
| F2 opportunity state | momentum swing trades only at high dispersion, idle in SPY (primary) | +12.2% | SPY | **−1.4 pp** | 0.61 | REJECTED_DEV |
| | same, ungated | +6.0% | SPY | −7.6 pp | 0.96 | REJECTED_DEV |
| F9 regime | SPY, cash in risk-off / shock (primary) | +11.0% | SPY | **−2.5 pp** | 0.95 | REJECTED_DEV |
| | SPY, cash in shocks only | +12.5% | SPY | −1.0 pp | 0.97 | REJECTED_DEV |

**Holm** over all 10 trials gives p = 1.0 for each.

**How to read the p-values.** Measured power on DEV is very low (+1 pp a year: about 6% at the 5% level). Three primaries (F5, F2, F9) are therefore **statistically indistinguishable from SPY** (p 0.50–0.95): they are not shown to be worse, but they are not better. The rejection follows the registered rule: active ≤ 0 is `REJECTED_DEV`.

## 2. What the batch teaches (descriptive, not tested; contaminated DEV)

### 2.1 Friction, not signal, is the binding constraint

| Book | Zero cost | 10 bps/side | 25 bps/side (registered) | 50 bps/side | Turnover / yr | SPY |
|---|---|---|---|---|---|---|
| F2 dispersion-gated | **+14.5%** | +13.1% | +12.2% | +9.8% | 18 | +13.5% |
| F5 momentum | **+15.2%** | — | +11.6% | — | 14 | +13.5% |
| F1 S1, SPY-idle | +12.7% | +9.8% | +6.9% | +1.7% | 41 | +13.5% |
| F2 ungated | +10.7% | +8.3% | +6.0% | +2.1% | 31 | +13.5% |

**Reading the table:**
- Before costs, two books edge past SPY: F5 momentum by about +1.7 pp and F2 gated by about +1.0 pp a year.
- After 25 bps per side, both trail it.
- Interpolating linearly, the break-even one-way cost is **about 7 bps for F2 gated** and **about 12 bps for F5**.
- These are post-hoc numbers on contaminated DEV history, and survivorship flatters both. They are not a reason to assume lower costs.
- **Implication.** Any future swing design has to start from turnover. Realistic costs are an empirical question that the forward shadow cannot answer, because it records decisions and never fills.

### 2.2 S1 has a little selection value, and the wrapper eats it

The S1 books beat their matched-control books (same execution, allocation and dates, other stocks):
- **+1.3 pp a year** SPY-idle (p 0.30);
- **+1.6 pp a year** cash-idle (p 0.26).

This is consistent with protocol-2's per-signal result. But friction alone costs 5.8 pp a year, and the timing and exit structure that strategies and controls share costs about 2 pp more. A 40-session horizon made it worse (−8.0 pp against −6.6 pp). The baseline audit's hypothesis is confirmed at book level: **the swing wrapper loses more than S1's selection adds.**

### 2.3 Gating works by trading less

The dispersion gate beats the ungated book by **+6.2 pp a year** net. Most of that comes from roughly halving the swing turnover (18 against 31 a year) and leaving the capital in SPY; the zero-cost gap is only +3.8 pp. The gate is better described as a turnover filter than as evidence of a high-dispersion opportunity state, because the competing explanation in the plan (the gate times market stress) was not separated.

### 2.4 Momentum against random equal weight

Momentum beat the single random path by **+13.1 pp a year** net. This is the registered F5 falsification check, and it was **not** falsified. But it is one random draw (R-1). Both books also share the inherited risk-off cash months (A-1), and those months were costly:
- SPY held on the same exposure path would have earned only +6.0% a year, 7.6 pp below full SPY, because the cash months fall around rebounds;
- momentum earned 5.6 pp a year more than that same-exposure path.

Whether momentum would beat SPY when fully invested is **not answered by this batch**.

### 2.5 Regime abstention trades growth for drawdown

| Policy | Growth / yr | Max drawdown | Conservative utility | Balanced utility | Growth utility |
|---|---|---|---|---|---|
| SPY | +13.5% | −34.2% | −0.246 | −0.055 | +0.072 |
| F9 risk-off and shock | +11.0% | **−23.3%** | **−0.162** | **−0.026** | +0.064 |
| F9 shock only | +12.5% | −25.4% | −0.190 | −0.033 | +0.072 |

**Reading the table:**
- **Growth and drawdown.** Abstention cut the 2020 drawdown by about 11 pp, but cost 2.5 pp a year of growth. The lag behind rebounds matches the registered competing explanation.
- **Utility.** On the conservative and balanced utilities it scores better than SPY. These utilities are descriptive, not the registered principal.
- **Fragility.** The advantage rests on essentially **one episode** (March 2020). It is an exploratory observation about risk preference, not an edge.

## 3. What this means for the program's objective

**The objective.** It asks for the strongest repeatable, economically profitable swing-trading method under realistic risk, execution and capital constraints.

**The evidence so far:**
- protocol-1;
- protocol-2 (`qv2-20261022`);
- this pilot on the same data.

**What it shows.** No tested swing implementation in S&P 500 names beats holding SPY after 25 bps per side.

**The most defensible statements:**
1. Selection value exists at a small scale: S1 and S3 per signal, and S1 per book versus controls. It is smaller than realistic friction at swing-trading turnover.
2. Idle capital belongs in the index. A swing book that is half in cash loses about 8 pp a year to cash drag alone.
3. Low turnover is a precondition, not a refinement.
4. Risk-off abstention can reduce drawdowns, but it costs growth, and a single episode carries the evidence.

**No hypothesis is validated.** History before 2026-10-23 cannot validate anything. Only untouched prospective data can, and it is accruing in the frozen forward shadow, whose first registered look (2027-04-30) cannot yet be powered.

## 4. Next steps (each registered before it runs; none changes this batch)

| # | Step | Why | Status |
|---|---|---|---|
| 1 | Keep the frozen forward shadow running | It is the only clean evidence source | Live on `main` since #20; first decisions from 2026-10-23 |
| 2 | **Pilot 2** (new plan id; DEV contaminated twice, so the trial count carries over), with the cost floor moved to low turnover: (a) momentum with a rank buffer and quarterly rebalance; (b) an explicit, registered eligibility mask (A-1); (c) the K-random-book distribution as the F5 negative control (R-1); (d) `obj-v3-2` with the drawdown limit relative to the comparator (O-1) | §2.1, §2.4 and the findings | To be proposed and preregistered in a separate PR. It is bounded to 3–5 families, and its best possible outcome is again a nomination |
| 3 | A cost model from measured data (spread and slippage) instead of an assumed 25 bps | §2.1: every conclusion near break-even depends on it | Needs a free or licensed quote source. SOURCE_BLOCKED until one is identified |
| 4 | PR-D: data coverage, point-in-time universe, operator lab | U2 (point-in-time membership) remains SOURCE_BLOCKED; survivorship flatters every absolute number here | Planned |

**Standing constraints.** Research only:
- no live orders, alerts, Discord signals or model promotion;
- `HIGH_CONVICTION` and `GROUP_ACTIONABLE` stay disabled.
