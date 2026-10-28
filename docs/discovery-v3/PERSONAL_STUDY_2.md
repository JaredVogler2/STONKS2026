# Personal $1000 method study 2: preregistration (plan `personal-study-2`)

| | |
|---|---|
| Machine copy | `apex_worker/discovery/v3/personal_study.py::PERSONAL_STUDY_2`, with the rules in `apex_worker/personal/method.py`. `apex research discovery plans` prints its sha256, and the run's `personal_study.json` must show the same value |
| Plan sha256 | `1bbef24b9ce5168cce29a368f23e9469682a4bc8627f23a61a1f2352364cd6f3` (it covers every rule's parameters) |
| Registered | 2026-10-28, before this study reads any real price of the ETFs below |
| Why | [`personal-study-1`](../../research-results/discovery-v3/personal-study-1-2026-10-27/REVIEW.md) found that neither sector rotation beat SPY: the better one trailed it by 4.0% a year. The owner's goal is still a $1000 method that beats the S&P 500. This study tests four rules **published before the DEV window, with their published parameters**. Nothing is fitted to these dates |
| Kind | A **method choice on DEV dates** (`DEV_CONTAMINATED`). Four new trials, added to the [trial index](../../research-results/discovery-v3/TRIAL_INDEX.md): 23 cumulative V3 trials. The forward record of the alerts is the test |

## 1. Question

Starting from $1000 and trading whole shares, does any of four published single-asset rules beat SPY, both measured on total returns, without a much deeper drawdown?

## 2. Candidates (registered; the simpler first)

| Id | Rule | Published |
|---|---|---|
| `spy-trend200` | Hold **SPY** while SPY closes above its 200-session simple moving average; otherwise hold **BIL**, a 1–3 month T-bill fund | Gayed & Bilello (2016), *Leverage for the Long Run* (the unlevered case) |
| `sso-trend200` | The same signal, holding **SSO**, a 2× daily S&P 500 fund, while above the average; otherwise BIL | Gayed & Bilello (2016) |
| `upro-trend200` | The same signal, holding **UPRO**, a 3× daily S&P 500 fund; otherwise BIL | Gayed & Bilello (2016) |
| `gem` | At each month-end, compare 252-session total returns. If SPY beat BIL, hold whichever of **SPY** and **EFA** (developed markets outside North America) did better; otherwise hold **AGG** (US aggregate bonds) | Antonacci (2014), *Dual Momentum Investing* (Global Equities Momentum) |

**Timing:**
- **The trend rules** read their signal at every close. A switch trades at the next session's open. At each month-end they also put idle cash to work.
- **`gem`** decides at month-ends only.
- **All four** take their first decision on the window's first day, starting in cash.

**Sizing (one asset at a time, whole shares):**
- EXIT whatever is not the target.
- BUY floor(cash / price) shares of the target.
- At a month-end, ADD floor(cash / price) when idle cash buys at least one share.

These are the published rules' choices. Every parameter (200 sessions, 252 sessions, the leverage, the assets) is the publication's own. The risk-off asset BIL stands in for the papers' T-bills.

## 3. Data and window

- **Data.** Alpaca historical **SIP** daily bars for SPY, SSO, UPRO, EFA, AGG and BIL, adjusted for **splits and dividends**: total returns for every asset and for the benchmark.
  - The bars run from 2016-01-04, so the 200-session average and the 12-month returns exist before the first decision.
  - Protocol-2's finality lag applies. The run makes at most 40 provider requests.
  - Each symbol needs at least 99% of the sessions with valid prices. Otherwise the run stops `SOURCE_INVALID` and never evaluates partial data.
- **Window.** The same as personal-study-1, for comparison: protocol-2's DEV range on SPY's sessions. The first decision is 2019-10-22, and the last session is protocol-2's last DEV session at run time.

## 4. Simulation (`personal/backtest.py`)

The simulation is personal-study-1's:
- $1000 in cash at the start;
- fills at the next session's open, sells first;
- a buy that no longer fits the cash at the open is cut to the shares that fit;
- 5 bps of traded value per side, with 0 and 20 bps as sensitivity;
- cash earns 0%;
- equity marked at each close.

The one difference: a daily rule is checked at every close, and only a switch makes orders.

## 5. Benchmark

The benchmark is SPY bought with the same $1000 at the open where the methods' first orders fill. It is fractional and frictionless, with dividends included through the adjusted prices.

## 6. Metrics

The same as personal-study-1:
- **Growth:** end values; log growth per year; excess log growth per year vs SPY; CAGR.
- **Risk:** maximum drawdowns; volatility.
- **Monthly active return vs SPY:** Newey-West t (3 lags), one-sided p, Holm-adjusted over the 4 candidates.
- **Consistency:** the share of rolling 12-month windows ahead of SPY; calendar years.
- **Activity:** trades per year and costs.
- **Cost sensitivity:** the excess at 0 and 20 bps.

## 7. Registered rule (personal-study-1's)

1. **BEATS_SPY_ON_DEV.** A candidate beats SPY when, at 5 bps a side, both conditions hold:
   - its log growth per year exceeds SPY's;
   - its maximum drawdown is at most 10 percentage points worse than SPY's.
2. **ADOPTED.** The personal method is the beating candidate with the larger excess growth; a tie goes to the candidate listed earlier. ADOPTED means adopted for the alerts, not validated.
3. **EXPERIMENT.** If no candidate beats SPY, the one with the larger excess runs as an EXPERIMENT. The report and the alerts then say plainly that no registered method beat the index.
4. **No tuning.** No parameter is tuned after the run. Another candidate needs a new plan id.

## 8. Published

The run writes `personal_study.json`, `PERSONAL_STUDY.md` and `data_quality_summary.json` to a 7-day artifact. A reviewed PR then copies them into `research-results/discovery-v3/<experiment_id>/` and adds the 4 trials to the index.

Only aggregates, monthly account values and the ETFs' names are published. No bar, price or quote is written.

## 9. Limits (stated before the run)

- **DEV contamination.** The author knew roughly how these years went: a long bull market interrupted by a fast 2020 crash and a 2022 bear market. Those conditions favor leverage with a trend filter. Two things the author did not choose: the rules and parameters were published in 2014 and 2016, before the window, and losers stay in the index. Still, **a pass here is weak evidence**. The forward record of the alerts is the test.
- **Leveraged funds.** SSO and UPRO reset daily: they compound 2× or 3× the index's *daily* return, not its long-run return. Choppy markets erode them (volatility decay).
  - A 3× fund loses almost everything if the index falls about a third in a single day. The worst day since 1987 was about −12% (2020-03-16).
  - The trend filter is the only protection, and it acts at the next open, not intraday.
- **Whipsaws.** The 200-day average gets crossed back and forth in sideways markets. Each false switch costs the spread and a bit of the move.
- **Taxes.** Switches realize gains, short-term when held under a year, in a taxable account. Results are pre-tax.
- **Broker.** Ally Invest lets accounts trade leveraged ETFs, but it may require acknowledging their risks first. Five of the six funds cost well under $1000 a share. SPY costs several hundred dollars a share, so a $1000 account holds one share and keeps the rest in cash; the simulation measures that drag.
- **Adjusted prices.** Sizing uses split- and dividend-adjusted prices, so whole-share leftovers are approximate for earlier dates.

## 10. After the run

**If a candidate is adopted,** the owner's alerts switch to it (`PERSONAL_METHOD`), labelled with this study's result:
- the starting allocation and a **signal-change** alert when the trend flips, each explaining the signal in words;
- a reminder the next morning;
- month-end sizing of idle cash;
- the same trade reporting, positions and P/L against SPY with the same cash.

**If none is adopted,** the alerts keep a labelled EXPERIMENT and say that no registered method beat the index. Either way, there is no group channel, no brokerage execution and no order placement anywhere.
