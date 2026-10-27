# Personal $1000 method study: preregistration (plan `personal-study-1`)

| | |
|---|---|
| Machine copy | `apex_worker/discovery/v3/personal_study.py::PERSONAL_STUDY_1`, with both methods in `apex_worker/personal/method.py`. `apex research discovery plans` prints its sha256, and the run's `personal_study.json` must show the same value |
| Plan sha256 | `89bd5a40a7160304447c5583da2d0665402c08e7e37d2cf3490622b282b66aba` (it covers both methods' parameters) |
| Registered | 2026-10-27, before this study reads any real price |
| Why | The owner wants a repeatable method that starts with $1000 and aims to beat the S&P 500. They trade by hand at Ally Invest, which charges no commission but has **no fractional shares**, and want Discord alerts. Earlier V3 work found that friction and turnover sink stock-level swing methods at this size. This study asks a narrower question: which whole-share method should the alerts follow? |
| Kind | A **method choice on DEV dates** (DEV_CONTAMINATED). The result picks what the alerts follow; it is never validation. The forward record of the alerts is the test |

## 1. Question

Starting from $1000 in cash and trading whole shares only, which of two pre-specified monthly sector-ETF momentum methods should the alerts follow?

Did either beat SPY bought with the same $1000 on the DEV window without a much deeper drawdown?

## 2. Candidates (registered; the simpler first)

**Universe:** the 11 Select Sector SPDR ETFs (XLB, XLC, XLE, XLF, XLI, XLK, XLP, XLRE, XLU, XLV, XLY). They traded throughout the window, so unlike the U1 stock studies the universe has no survivorship bias.

| Id | Rule |
|---|---|
| `sector-mom-3` | Signal: the 126-session price return through the decision close. Hold the top 3. A held ETF stays while it ranks within the top 5, a buffer that cuts turnover |
| `sector-mom-3-abs` | The same, but an ETF qualifies only with a positive 126-session return. An empty slot stays in cash |

**Both candidates share these rules:**

- **Timing.** Decisions are taken after the close of the last session of each month and traded at the next session's open. The first decision is on the window's first day, starting in cash.
- **Sizing (whole shares).** The target per slot is equity / 3.
  - EXIT: a holding that leaves the selection is sold in full.
  - TRIM: a holding worth more than 1.5× its target is sold back to the target.
  - BUY: a new pick gets floor(target / price) shares.
  - ADD: a kept holding at least one share below its target is topped up.
- **Order.** Orders are placed in that sequence, as far as cash allows.

## 3. Data and window

- **Data.** The study uses protocol-2's own data path, the same one every V3 study uses: Alpaca historical SIP daily bars, split-adjusted, from 2016-01-04, with the 2-session finality lag and the quality inventory. The study reads only SPY and the 11 sector ETFs.
- **Window.** This is protocol-2's DEV decision range.
  - The first decision is at the close of the session before the first DEV session: 2019-10-22, for the 2019-10-23 cut.
  - The last session is protocol-2's last DEV decision session.
  - That is about 7 years, including the 2020 crash, the 2022 bear market and the 2023–2025 recovery.
- **DEV_CONTAMINATED.** These dates have been studied before. The methods are textbook industry momentum, and the author knew their published history. Nothing here is out of sample.

## 4. Simulation (`personal/backtest.py`)

- **Start and fills.** The account starts with $1000 in cash. Orders fill at the next session's open, sells first, then buys in the method's order.
  - A buy that no longer fits the cash at the open is cut to the shares that fit.
  - A symbol without an open that session is retried at the next open.
- **Costs.** 5 bps of traded value per side covers spread and slippage; Ally charges no commission on ETFs. The sensitivity runs use 0 and 20 bps.
- **Cash and marking.** Cash earns 0%. Equity is marked at each close.
- **Returns.** Price returns only: dividends are excluded for both the methods and SPY.

## 5. Benchmark

The benchmark is SPY bought with the same $1000 at the open where the methods' first orders fill. It is fractional, frictionless and held to the end.

That is what $1000 earns without any method. It is a harder bar than a whole-share SPY purchase.

## 6. Metrics (reported for each candidate)

- **Growth:** end value and SPY's end value; log growth per year; excess log growth per year vs SPY; CAGR.
- **Risk:** maximum drawdown and SPY's; volatility; log-growth Sharpe.
- **Monthly active return vs SPY:** mean; Newey-West t (3 lags); one-sided p, Holm-adjusted over the 2 candidates.
- **Consistency:** the share of rolling 12-month windows that beat SPY; calendar-year returns vs SPY.
- **Activity:** trades per year, costs paid, mean cash share, mean positions, buys by ETF.
- **Cost sensitivity:** excess growth at 0 and 20 bps.

## 7. Registered rule

1. **BEATS_SPY_ON_DEV.** A candidate beats SPY when, at 5 bps a side, both conditions hold:
   - its log growth per year exceeds SPY's;
   - its maximum drawdown is at most 10 percentage points worse than SPY's.
2. **ADOPTED.** The personal method is the beating candidate with the larger excess growth; a tie goes to `sector-mom-3`. ADOPTED means adopted for the alerts, not validated.
3. **EXPERIMENT.** If neither candidate beats SPY, the one with the larger excess runs as an EXPERIMENT. The report and the alerts then say plainly that no registered method beat the index on DEV.
4. **No tuning.** No parameter is tuned after the run. Another candidate needs a new plan id, a new registration and a trial-index entry.

## 8. Published

The run writes `personal_study.json` and `PERSONAL_STUDY.md` to a 7-day artifact. A reviewed PR then copies them into `research-results/discovery-v3/<experiment_id>/` and adds the 2 trials to the trial index.

Only aggregates, the monthly account values of the $1000 simulations and the ETFs' names are published. No bar, price or quote is written.

## 9. Limits (stated before the run)

- **No guarantee.** A momentum rotation can trail the index for years. For example, technology may lead the market while the method holds two other sectors. The DEV result is a diagnostic.
- **Whole shares in a small account.**
  - A $333 slot holds only one or two shares of a high-priced ETF, so up to about a fifth of a slot can sit in cash. The simulation measures that drag.
  - Sizing uses split-adjusted prices, so it is approximate for an ETF that split during the window.
- **Taxes.** Monthly rotation realizes short-term gains in a taxable account. Results are pre-tax; an IRA avoids the tax.
- **Dividends** are excluded for both sides. Sector ETFs' yields differ from SPY's, so the excess could shift a little either way.
- **Cash account.** Sells settle T+1. The method holds each position for at least a month, so following it cannot cause a good-faith violation.
- **Execution.** The alerts arrive after the close, and trades are expected at or near the next open. A trade taken later fills at a different price. The live record counts, not the simulation.

## 10. After the run

The chosen method drives the owner's personal Discord alerts, built in a separate PR:

- signals and orders sized on the owner's actual reported holdings and cash;
- trade confirmations and positions;
- add/exit suggestions at each monthly decision;
- P/L against SPY with the same cash flows.

The alerts are labelled UNVALIDATED and serve the owner's own manual trading only. There is no group channel, no brokerage execution and no order placement anywhere.
