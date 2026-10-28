# Personal $1000 method study 3: preregistration (plan `personal-study-3b`)

| | |
|---|---|
| Machine copy | `apex_worker/discovery/v3/personal_study.py::PERSONAL_STUDY_3B`, with the rules in `apex_worker/personal/method.py` (`RULES`). `apex research discovery plans` prints its sha256, and the run's `personal_study.json` must show the same value |
| Plan sha256 | `7c94e4c7c28bc1568fec8f0748a653e3f0a3f2e93b8ec08d013489406f48cf86` (it covers every rule's parameters, sources and the decision rule) |
| Amended | **Before any evaluation** (section 11). The first registration, `personal-study-3` (sha `4b67f1db…cac0`, commit `dd343be`), stopped `SOURCE_INVALID` at the data check: its S&P 500 fund SPLG became **SPYM** on 2025-10-31. This plan reads SPYM instead and is otherwise identical |
| Registered | 2026-10-28, before this study reads any real price. Study 2 has already read six of the ETFs below (SPY, SSO, UPRO, EFA, AGG, BIL) from 2016 on, and the author has seen its results. No earlier study in this repository read the other eleven |
| Why | [`personal-study-2`](../../research-results/discovery-v3/personal-study-2-2026-10-28/REVIEW.md) adopted `sso-trend200` (a 2× S&P 500 fund under a 200-day trend filter) on weak evidence: one window, chosen after study 1. The owner asked for more distinct methods to be tested properly before settling on a direction. This study puts **ten more published rules** through one battery, together with the adopted `sso-trend200` for comparison |
| Kind | A **method comparison on known dates** (`DEV_CONTAMINATED`). Ten new trials, added to the [trial index](../../research-results/discovery-v3/TRIAL_INDEX.md): 33 cumulative V3 trials. `sso-trend200` is a reference here, not a new trial. The forward record of the alerts is the test |

## 1. Question

Starting from $1000 and trading whole shares, which published rules beat SPY **repeatedly**, measured on total returns?
- "Repeatedly" means over the whole window, in each of its halves, and in most 3-year stretches, not in one lucky window.
- The comparison also asks how each rule compares with the adopted `sso-trend200` and what each one costs in drawdown and trading.

## 2. Candidates (registered)

Ten rules from nine families. Each one is a published rule with the publication's own parameters.

| Id | Family | Rule | Rebalance | Published |
|---|---|---|---|---|
| `sso-hold` | Leveraged buy-and-hold | Hold **SSO** (2× daily S&P 500) throughout | Month-end (idle cash only) | The baseline in Gayed & Bilello (2016), *Leverage for the Long Run* |
| `voltarget-sso` | Volatility targeting | Exposure = 15% ÷ SPY's realized volatility over the last 21 sessions (annualized), capped at 2×. Held as SSO (exposure ÷ 2 of the account) plus **BIL** for the rest | Month-end | Harvey et al. (2018), *The Impact of Volatility Targeting*; after Moreira & Muir (2017) |
| `gtaa5` | Multi-asset trend | A fifth each in US stocks (**SPYM**), foreign stocks (**EFA**), 7–10y Treasuries (**IEF**), REITs (**VNQ**) and commodities (**DBC**). Each fifth is held only while its month-end price is above its 10-month average, else in BIL | Month-end | Faber (2007), *A Quantitative Approach to Tactical Asset Allocation* |
| `vaa-g4` | Breadth momentum | 13612W momentum (12×1-month + 4×3-month + 2×6-month + 12-month return). Hold the best of SPYM, EFA, **EEM** and **AGG** while all four are positive; otherwise hold the best of **LQD**, IEF and **SHY** | Month-end | Keller & Keuning (2017), *Breadth Momentum and Vigilant Asset Allocation* |
| `adm` | Dual momentum | Score = 1 + 3 + 6-month returns. Hold the stronger of SPYM and **SCZ** (foreign small caps) while its score is positive; otherwise **TLT** (20+y Treasuries) | Month-end | EngineeredPortfolio (2018), *Accelerating Dual Momentum* |
| `hfea` | Leveraged risk parity | 55% **UPRO** (3× S&P 500), 45% **TMF** (3× 20+y Treasuries) | Quarter-end | "Hedgefundie's Excellent Adventure", Bogleheads forum (2019) |
| `halloween-sso` | Seasonality | SSO from November through April; BIL from May through October | Month-end | Bouman & Jacobsen (2002), *The Halloween Indicator* |
| `tom-sso` | Calendar anomaly | SSO over the turn of the month: the last session of each month and the first three of the next. BIL otherwise | Every close | Lakonishok & Smidt (1988); McConnell & Xu (2008), *Equity Returns at the Turn of the Month* |
| `rsi2-sso` | Short-term mean reversion | Buy SSO after SPY's 2-day RSI closes below 10 while SPY is above its 200-day average. Sell when SPY closes above its 5-day average. BIL otherwise | Every close | Connors & Alvarez (2008), *Short Term Trading Strategies That Work* |
| `mtum-hold` | Momentum factor | Hold **MTUM** (iShares MSCI USA Momentum Factor) throughout | Month-end (idle cash only) | Jegadeesh & Titman (1993); the factor as an ETF |

**Reference (not a new trial):** `sso-trend200`, the adopted method. It holds SSO while SPY closes above its 200-day average, otherwise BIL, and is checked at every close (personal-study-2).

### What is ours, not the publications'

These choices are stated before the run because they were made by the author:
- **2× versions.** The seasonal, turn-of-month and RSI(2) rules were published for the index itself. Here they hold SSO, the 2× fund, while "in". This matches the adopted method's leverage, so the comparison is about timing, not leverage. Volatility targeting uses SSO to allow up to 2× exposure.
- **SPYM for SPY.** SPYM (SPLG until 2025-10-31) tracks the S&P 500 at a fraction of SPY's share price, so a $200 slot can hold whole shares. The benchmark is still SPY.
- **BIL for T-bills.** BIL stands in for the papers' T-bills or cash.
- **Months as sessions.** 1, 3, 6 and 12 months are 21, 63, 126 and 252 sessions. Volatility uses the last 21 daily log returns.
- **Fixed parameter choices.** The RSI is Wilder's with an entry level of 10, one of the levels the book reports. HFEA uses its 55/45 version, rebalanced each quarter.

## 3. Data and windows

- **Data.** Alpaca historical **SIP** daily bars for 17 symbols: SPY, SPYM, SSO, UPRO, TMF, BIL, EFA, EEM, AGG, LQD, IEF, SHY, TLT, VNQ, DBC, SCZ and MTUM.
  - Adjusted for **splits and dividends**, so every return is a total return, SPY's too.
  - The bars start on 2016-01-04, so every rule has a year of history at the first decision.
  - Protocol-2's finality lag applies, and the run makes at most 40 provider requests.
  - Each symbol needs at least 99% of the sessions with valid prices. Otherwise the run stops `SOURCE_INVALID` and evaluates nothing.
  - **SPYM's history before 2025-10-31** comes from Alpaca's mapping of the renamed symbol. Its daily returns must track SPY's: correlation at least 0.98, and no daily gap over 3 points. Otherwise the run stops `SOURCE_INVALID`, because a wrongly mapped or spliced history would distort three rules.
- **FULL window.** The first decision is at the close of **2017-01-31**. The last session is protocol-2's last DEV session at run time, about five weeks before the run. FULL therefore adds protocol-2's PRESAMPLE years (2017 to 2019) to the DEV dates.
- **Halves.** FULL is split at its middle session. Each half starts again from $1000, so the two halves do not share a single day of returns.
- **DEV window.** The first decision is 2019-10-22, the window of studies 1 and 2. It is reported for comparison and is not one of the tests.

## 4. Simulation (`personal/backtest.py`)

The simulation is study 2's:
- $1000 in cash at the start;
- decisions at the close, fills at the next session's open, sells first;
- a buy that no longer fits the cash at the open is cut to the shares that fit;
- 5 bps of traded value per side, with 0 and 20 bps as stress;
- uninvested cash earns 0%, while BIL, when a rule holds it, earns T-bill yields through its total return;
- equity marked at each close.

**Weights to shares (`weight_orders`).** At each rebalance, each ETF's target is floor(weight × account value ÷ price) shares. The orders then run in this order:
1. Sell whatever is above its target.
2. Buy toward each target, from the largest weight down, as far as the cash allows.

**When rules decide.**
- **Monthly rules** decide at each month's last close; HFEA decides at each quarter's.
- **Daily rules** (`tom-sso`, `rsi2-sso`) also decide at every close, but trade only when the held ETF changes.
- **Missing data.** A rebalance that lacks a close for any ETF it needs is taken at the next complete close.

## 5. Benchmark

The benchmark is SPY bought with the same $1000 at the open where the method's first orders fill. It is fractional and frictionless, with dividends included through the adjusted prices.

## 6. The battery

For every rule and the reference:

| Measure | What it is |
|---|---|
| Growth (FULL) | End value of the $1000, CAGR, log growth per year and the **excess over SPY** |
| Halves | Excess over SPY in each half, from a fresh $1000 |
| DEV | Studies 1 and 2's window, for comparison |
| Risk | Maximum drawdown against SPY's, and volatility |
| Rolling windows | Over every 12-month and 36-month span of month-ends: the share of spans ahead of SPY, and the median and worst difference |
| Uncertainty | The 90% interval of the annual excess, from a **stationary block bootstrap** of the monthly excess returns (Politis & Romano 1994): 5000 resamples, 6-month mean block, seed 20261028 |
| Cost stress | The FULL excess at 0 and 20 bps a side |
| Significance | Newey–West t of the monthly excess (3 lags), one-sided p, **Holm** over the ten new trials |
| Activity | Trades per year and costs paid; calendar-year returns against SPY |

## 7. Registered rule

1. **BEATS_SPY_REPEATEDLY.** A rule earns this when all six tests pass:
   - its excess log growth over SPY is above 0 on the FULL window;
   - its excess is above 0 in the first half;
   - its excess is above 0 in the second half;
   - its FULL maximum drawdown is at most 10 points worse than SPY's;
   - it is ahead of SPY in at least 60% of rolling 36-month spans;
   - its FULL excess is still above 0 at 20 bps a side.
2. **BEATS_SPY_PARTLY.** The FULL excess is above 0, but another test failed.
3. **DOES_NOT_BEAT_SPY.** Everything else.
4. **Recommended direction.** The BEATS_SPY_REPEATEDLY rule with the largest FULL excess. The reference `sso-trend200` is listed first, so it wins a tie. If no rule qualifies, the report says so plainly.
5. **Reported, not tested.** The DEV window, the bootstrap interval and the Holm p-values are reported but do not decide anything.
6. **No tuning.** No parameter is tuned after the run. Another rule or parameter needs a new plan id.
7. **The owner decides.** The recommendation does not switch the alerts. The alerts change method only when the owner chooses a direction, through a reviewed PR that changes `PERSONAL_METHOD`, labelled with this study's result.

## 8. Published

The run writes `personal_study.json`, `PERSONAL_STUDY.md` and `data_quality_summary.json` to a 7-day artifact. A reviewed PR then copies them into `research-results/discovery-v3/<experiment_id>/` with a REVIEW.md, and adds the ten trials to the index.

Only aggregates, monthly account values and the ETFs' names are published. No bar, price or quote is written.

## 9. Limits (stated before the run)

- **DEV contamination.** The author knows roughly how 2017 to 2026 went: a long bull market, with a fast crash in 2020 and a bear market in 2022 when stocks and bonds fell together. The author also knows study 2's result for SSO and the trend filter.
  - **HFEA:** its 2022 loss was widely reported, so its result here is no surprise in either direction.
  - **Leverage:** the decade favors leveraged equity.
  - **What the author did not choose:** the rules and parameters are the publications', and every rule is published here whatever its result.
  - A pass is still **weak evidence**. The forward record of the alerts is the test.
- **One decade.** Alpaca's daily bars start in 2016, so the FULL window is under ten years: one cycle, mostly rising. Calendar rules (Halloween, turn of the month) get only about ten seasons or about 115 month-turns. The bootstrap interval shows how wide the uncertainty is. No test here covers 2000–2002 or 2008, when a 2× fund held throughout lost far more than the index.
- **Overlapping evidence.** The halves, the rolling spans and the DEV window come from the same decade. They test consistency within it, not across market history.
- **Leveraged funds.** SSO, UPRO and TMF reset daily: they compound 2× or 3× the *daily* return, not the long-run return. Choppy markets erode them. A 3× fund loses nearly everything if its index falls about a third in one day. Every rule here acts at the next open, never intraday.
- **Costs.** 5 bps a side is realistic for SPY-sized funds. It may be optimistic for TMF, DBC, SCZ or MTUM; the 20 bps stress covers that.
- **Taxes.** Frequent rules (`tom-sso`, `rsi2-sso`) realize short-term gains in a taxable account. Results are pre-tax.
- **Broker.** Ally Invest may require acknowledging the risks of leveraged ETFs before trading them. Whole shares leave some cash idle; the simulation measures that.
- **Adjusted prices.** Sizing uses split- and dividend-adjusted prices, so whole-share leftovers are approximate for earlier years.
- **Live use of `rsi2-sso`.** The live alerts know a position only once the owner reports the fill. A rule whose exit depends on being in a trade (RSI(2)) would need the alert's own record of that state before it could run live.

## 10. After the run

The scoreboard goes to the owner with the recommendation and its caveats, and the owner chooses the direction. Until then, the alerts keep `sso-trend200`. The alerts never trade: there is no group channel, no brokerage execution and no order placement anywhere.

## 11. Amendment before evaluation (2026-10-28)

**What happened.** The first registration, `personal-study-3` (sha `4b67f1db20f0ecf2cf0072b0a184ef897dce7c4b168956ed6a4a21be43c0cac0`, merged in [JaredVogler2/STONKS2026#49](https://github.com/JaredVogler2/STONKS2026/pull/49)), ran once on [2026-10-28](https://github.com/JaredVogler2/STONKS2026/actions/runs/40616902467). It stopped at the data check with `SOURCE_INVALID: incomplete or invalid daily bars for SPLG`. Nothing was simulated and no result exists.

**Why.** State Street changed the fund's ticker from SPLG to **SPYM** before the open on 2025-10-31. The fund, its index (the S&P 500) and its fee are unchanged. The old symbol stopped trading on 2025-10-30, so the bars Alpaca returned for SPLG fell short of the 99% coverage the data check requires.

**What changed.** `personal-study-3b` differs only in these points:
- the rules `gtaa5`, `vaa-g4` and `adm` hold SPYM, and the study reads SPYM's bars instead of SPLG's;
- the plan's notes say so;
- a twin check holds SPYM's history to SPY's daily moves. It is a data check, so it changes no rule or test.

Everything else is unchanged: the rules' parameters, the windows, the battery and the decision rule. The alerts would also name SPYM, the ticker the owner can actually buy.

`personal-study-3` is no longer runnable, since its plan reads a symbol that no longer trades. Its registration stays in git and in this note, and the trial index lists the stop.

**Fund history.** The fund tracked a different US large-cap index before it was renamed SPDR Portfolio S&P 500 ETF in January 2020. For the "US stocks" role in these rules, the difference is negligible.
