# Personal $1000 method study 4: preregistration (plan `personal-study-4`)

| | |
|---|---|
| Machine copy | `apex_worker/discovery/v3/long_history.py::PERSONAL_STUDY_4`, with the rules in `apex_worker/personal/method.py`. `apex research discovery plans` prints its sha256, and the run's `personal_study.json` must show the same value |
| Plan sha256 | `ea525e61d08455ed2bd734a75f3f508f1c85dc152ab3cd9f504b27c44541d333` (it covers the methods, the rebuilt funds' fees, the timing and the decision rule) |
| Registered | 2026-10-28, before any method is computed on this data. The author downloaded the file once to check the parser on its format: 26,338 rows from 1926-07-01 to 2026-09-30. No return of any method was computed |
| Why | Studies 2 and 3 can only use Alpaca's daily bars, which start in 2016: one decade, mostly rising, which flatters leverage. The adopted method and five of study 3's candidates need nothing but the S&P 500 and T-bills, so they can be rebuilt over a century: the 1929–32 crash, the 1970s, 1987, 2000–02 and 2008 |
| Kind | A **robustness replication on known history** (`KNOWN_HISTORY`). Same methods, same parameters, nothing fitted. No new trial is counted; the [trial index](../../research-results/discovery-v3/TRIAL_INDEX.md) lists it as a replication. The forward record of the alerts is the test |

## 1. Question

Do the S&P 500 timing methods beat the market across a hundred years? The test asks for this:
- decade after decade;
- in most 10-year stretches;
- after their publication;
- without a much deeper drawdown.

## 2. Methods

| Id | Role | Rule (as in `personal/method.py`) |
|---|---|---|
| `sso-trend200` | Reference (the adopted method) | 2× fund while the index closes above its 200-session average, else T-bills; checked at every close |
| `spy-trend200` | Reference (its 1× twin, study 2) | The same with the 1× fund |
| `sso-hold` | Replication | 2× fund throughout |
| `voltarget-sso` | Replication | Exposure 15% ÷ the index's realized volatility over the last 21 sessions (annualized), capped at 2×. Held in the 2× fund plus T-bills, reset at each month-end |
| `halloween-sso` | Replication | 2× fund from November through April, T-bills from May through October |
| `tom-sso` | Replication | 2× fund over the last session of each month and the first three of the next, T-bills otherwise |
| `rsi2-sso` | Replication | 2× fund after the index's 2-day RSI closes below 10 while above its 200-session average, until it closes above its 5-session average; T-bills otherwise |

**Not covered.** `gtaa5`, `vaa-g4`, `adm`, `hfea` and `mtum-hold` need foreign stocks, bonds, REITs, commodities or a momentum fund, so this file cannot rebuild them. The code refuses them.

## 3. Data

The **Kenneth R. French Data Library**'s daily factors file ([`F-F_Research_Data_Factors_daily_CSV.zip`](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_daily_CSV.zip)). It is public and free, and the run downloads it once.

Two columns are used:
- **Mkt-RF + RF:** the daily total return of the CRSP value-weighted index of US stocks, dividends included.
- **RF:** the daily T-bill return.

**What the run checks before using the file** (otherwise the run stops `SOURCE_INVALID`; a failed download stops it `SOURCE_BLOCKED`):
- the file's own header line is present;
- at least 25,000 rows, starting on 1926-07-01;
- dates strictly increasing;
- every return numeric and plausible.

The file's sha256 and its "created by using the … CRSP database" note go into the report. The file itself is never stored or published.

**Run path.** The `apex-discovery-v3` workflow passes `--source alpaca` to every personal study. For this plan, that means the French file, so no Alpaca request is made.

## 4. The rebuilt funds

| Role | Daily return | Why |
|---|---|---|
| 1× fund (SPY), also the benchmark | market − 0.09% a year | SPY's expense ratio |
| 2× fund (SSO) | 2 × market − T-bill − (0.50% + 0.90%) a year | The fund borrows the second unit at the T-bill rate plus a spread, and charges a fee. SSO's fee is 0.89%; the 0.50% spread is our assumption |
| T-bills (BIL) | T-bill − 0.14% a year | BIL's expense ratio |

**Leverage.** Like the real fund, the 2× return resets daily: it compounds twice each day's move, not twice the long-run return. Funds like SSO started in 2006, so earlier decades show what such a fund would have done.

## 5. Simulation (`long_history.py`)

- **Signals.** Every signal reads the market index (total return) at a close.
- **Timing.** The position changes at the **next close**, one session late, as the owner trades the day after an alert. Trading at the signal's own close is reported as a sensitivity.
- **Costs.** 5 bps of each fund trade, 20 bps as stress. Moving between cash and a fund counts once; a switch between two funds counts twice.
- **Weights.** Weights are continuous: at this scale, whole shares do not matter.
- **When rules act.**
  - Single-asset rules change only when their target changes.
  - Volatility targeting resets its weights at each month-end.
  - The 2× buy-and-hold decides once.
- **Start.** The first decision comes after a year of data (252 sessions, in 1927), so every signal has its history.
- **Benchmark.** The 1× fund, bought at the method's first fill.

## 6. Measures

For each method:
- **Whole history:** end value of $1000 against the market's; CAGR; log growth per year; **excess over the market**.
- **Decades:** excess log growth per year in each complete decade from the 1930s to the 2010s, nine in all.
- **Rolling 10-year windows** of month-ends: the share ahead of the market, and the median and worst difference.
- **Risk:** maximum drawdown against the market's; worst day; worst and best calendar year against the market.
- **Post-publication:** excess from the January after the publication year:
  - 2017 for the trend rules and the 2× buy-and-hold (Gayed & Bilello 2016);
  - 2018 for volatility targeting (Moreira & Muir 2017);
  - 2003 for Halloween (Bouman & Jacobsen 2002);
  - 1989 for the turn of the month (Lakonishok & Smidt 1988);
  - 2009 for RSI(2) (Connors & Alvarez 2008).
- **Sensitivity and significance:** the excess when trading at the signal's own close, and at 20 bps; Newey–West t of the monthly excess, one-sided p, Holm over the five replications.
- **Activity:** switches and turnover per year.

## 7. Registered rule

1. **HOLDS_UP.** A method earns this when all six tests pass:
   - excess log growth over the market above 0 over the whole history;
   - ahead of the market in at least 6 of the 9 complete decades;
   - maximum drawdown at most 10 points worse than the market's;
   - ahead in at least 60% of rolling 10-year windows;
   - excess above 0 after publication;
   - whole-history excess above 0 at 20 bps a side.
2. **HOLDS_UP_PARTLY.** The whole-history excess is above 0, but another test failed.
3. **DOES_NOT_HOLD_UP.** Everything else.
4. **Reported, not tested.** Same-close trading, the Holm p-values and the calendar-year extremes are reported but do not decide anything.
5. **No tuning.** Nothing is tuned after the run, and the alerts do not change because of it. The owner chooses the direction from studies 3 and 4 together.

## 8. Published

The run writes `personal_study.json`, `PERSONAL_STUDY.md` and `data_quality_summary.json` to a 7-day artifact. A reviewed PR copies them into `research-results/discovery-v3/<experiment_id>/` with a REVIEW.md.

Only aggregates are published. No daily or monthly return series is written, and the downloaded file is not stored.

## 9. Limits (stated before the run)

- **Known history.** Several of these rules were found on parts of this very history:
  - the trend filter was tested on 1928–2015;
  - Halloween and the turn of the month on long index histories.
  
  So a pass before publication is partly in-sample. The post-publication test is the out-of-sample part, and it is short for the newer rules.
- **Index.** The CRSP index holds every US-listed stock, not just the S&P 500's 500. The two move very closely, but not identically.
- **Saturday sessions.** Before 1952 the market also traded on Saturdays. "200 sessions" and "21 sessions" therefore covered fewer calendar days then, and the annualization of early volatility is approximate.
- **Costs and financing.** Trading cost far more than 5 bps before the 1970s. This study applies today's costs to show what the rules would do now, not what anyone earned then. The financing spread of the 2× fund is an assumption.
- **Daily data.** Fills at a close, so the simulation knows nothing about intraday moves. The real alerts trade at the next session's open; the next-close timing and the same-close sensitivity bracket that.
- **Taxes.** Results are pre-tax.

## 10. After the run

The owner gets the century record next to study 3's decade. Together they show which direction holds up across both. Nothing switches automatically, and nothing ever trades: there is no group channel, no brokerage execution and no order placement anywhere.
