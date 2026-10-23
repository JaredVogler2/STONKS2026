# Discovery V3 pilot 2: decision report

**Decision: NO_TRADE.**
- No trial reached `PROMISING_EXPLORATORY`, so **nothing is nominated** under the registered rule.
- The frozen forward shadow continues unchanged.
- No live, paper-actionable or group-actionable output follows.

| | |
|---|---|
| Question | With the **measured** cost model and execution timing as a design axis, does any of three post-hoc mechanisms beat its investable alternative on DEV? |
| Answer | **Not demonstrably.** Five of 7 trials lose to SPY or break a constraint. The two G2 momentum books show **+8.6 pp a year** but are `INCONCLUSIVE_DEV` (Holm p 0.62), with known biases that inflate them (P2-1, P2-6) |
| Evidence tier | `DEV_DIAGNOSTIC`, on history contaminated twice |
| Inputs | [REVIEW.md](REVIEW.md); [results.json](results.json); [preregistration](../../../docs/discovery-v3/PILOT_2_PREREGISTRATION.md) |

## 1. What pilot 2 adds (descriptive; DEV; not confirmatory)

1. **Low-turnover, fully invested momentum is the first V3 policy with a positive point estimate after realistic costs:** +22.2% a year against SPY's +13.5%.
   - It beat all 20 random survivor books built the same way (+11.4 pp a year at zero cost, p 0.074).
   - **But** it carries beta 1.2 and **35% volatility**, so risk-adjusted it trails SPY (0.63 against 0.70 growth per unit of volatility).
   - Its maximum drawdown (−35%) is similar to SPY's (−34%), but its daily tail loss is almost twice SPY's.
   - Survivorship interacts with momentum in a way the random control cannot remove.
   - **Reading:** a strategy that leans into a known risk premium (momentum plus higher beta) on a survivor universe, not a demonstrated edge.

2. **Turnover, not timing, made the difference for momentum.**
   - Quarterly buffering added about +5 pp a year over monthly rebalancing.
   - The monthly book also broke the relative drawdown limit (−48%).
   - Executing at the open or the close no longer matters at 7 trades a year.

3. **For S1 swing trades, close execution helps the book (+1.8 pp a year) but takes away most of S1's selection value** (+1.3 to +0.2 pp against controls). S1 still trails SPY by 3.5 pp a year. **The S1 swing family stays rejected** on DEV under every execution tested.

4. **Dispersion gating:** +1 pp a year over ungated, both losing to SPY, and no selection value against controls. **Rejected.**

## 2. Status of the program's objective

**Across protocol-2's preregistered study, V3 pilot 1 (10 trials), pilot 2 (7 trials) and one measurement:**
- **No swing-trading method** in S&P 500 names beats holding SPY after realistic costs on DEV history.
- The only positive point estimate is a **quarterly, low-turnover momentum allocation**, which is not a swing method. Its edge over SPY is not statistically distinguished, is risk-loaded, and is plausibly inflated by survivorship and forking paths.

The most defensible conclusions remain:
1. Small selection value exists in some swing selections (S1, S3), but it does not survive the swing wrapper.
2. Low turnover and full investment matter more than signals.
3. Only untouched prospective data, or a point-in-time universe, can separate a real momentum premium from survivorship.

**No hypothesis is validated.**

## 3. Next steps

| # | Step | Status |
|---|---|---|
| 1 | Keep the frozen forward shadow (S1, S2, S3, S5) running | Live; its first decision follows the 2026-10-23 close |
| 2 | **Owner decision, not automatic:** register G2 (quarterly buffered momentum, close execution, `cost-v3-2`) as a **prospective exploratory** forward series (`v3x-g2-momentum-v1`) | **Not nominated** under the registered rule (it is INCONCLUSIVE, not PROMISING), so this would be an explicit owner choice to collect clean prospective evidence on the strongest DEV candidate. The cost is small: one decision every 60 sessions, recorded through the existing recorder's exploratory lane (schema present, no policy yet). It stays labelled `PROSPECTIVE_EXPLORATORY` and NO_TRADE |
| 3 | A point-in-time universe (U2) to measure the survivorship × momentum interaction (P2-1) | `SOURCE_BLOCKED`: no free source with delisted constituents has been identified |
| 4 | Further DEV searches | **Not recommended.** DEV is contaminated twice and power is low (a +1 pp a year edge is undetectable). More searching on the same history mostly manufactures winners' curse |

**Standing constraints.** Research only:
- no orders, alerts, Discord signals or promotion;
- `HIGH_CONVICTION` and `GROUP_ACTIONABLE` stay disabled.
