# Personal method study `personal-study-4` (personal-study-4-2026-10-28)

Status **COMPLETED**. Daily US market history (Kenneth R. French Data Library), first decision 1927-05-05, last session 2026-09-30. Decided at a close, traded at the close 1 session later, 5 bps a side; the benchmark is the 1x fund (the market less SPY's fee). Total returns. A robustness replication on well-known history, not validation.

| Method | Role | $1000 → | Market → | Growth / yr | Excess / yr | Decades ahead | Max drawdown (market) | Worst day | Ahead in 10-year windows | Post-publication excess / yr | Same-close excess | Excess at 20 bps | Switches / yr | Disposition |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sso-trend200 | reference | $1.11e+09 | $1.38e+07 | +15.0% | +4.4% | 8 of 9 | -68% (-84%) | -35% | 84% | +2.8% since 2017 | +6.0% | +2.7% | 5.7 | HOLDS_UP |
| sso-hold | replication | $1.09e+08 | $1.38e+07 | +12.4% | +2.1% | 6 of 9 | -98% (-84%) | -35% | 74% | +6.7% since 2017 | +2.1% | +2.1% | 0.0 | HOLDS_UP_PARTLY |
| voltarget-sso | replication | $4.01e+07 | $1.38e+07 | +11.3% | +1.1% | 6 of 9 | -68% (-84%) | -15% | 64% | -3.2% since 2018 | +1.1% | +0.5% | 11.1 | HOLDS_UP_PARTLY |
| halloween-sso | replication | $3.7e+07 | $1.38e+07 | +11.2% | +1.0% | 7 of 9 | -88% (-84%) | -24% | 76% | -0.9% since 2003 | +1.3% | +0.4% | 2.0 | HOLDS_UP_PARTLY |
| tom-sso | replication | $1.5e+07 | $1.38e+07 | +10.2% | +0.1% | 4 of 9 | -54% (-84%) | -19% | 47% | -5.0% since 1989 | +1.8% | -7.1% | 24.0 | HOLDS_UP_PARTLY |
| spy-trend200 | reference | $1.16e+07 | $1.38e+07 | +9.9% | -0.2% | 5 of 9 | -41% (-84%) | -17% | 32% | -3.3% since 2017 | +0.6% | -1.9% | 5.7 | DOES_NOT_HOLD_UP |
| rsi2-sso | replication | $83,362 | $1.38e+07 | +4.6% | -5.1% | 3 of 9 | -57% (-84%) | -35% | 21% | -7.4% since 2009 | -9.1% | -10.2% | 16.7 | DOES_NOT_HOLD_UP |

Excess log growth per year over the market, by decade:

| Method | 1930s | 1940s | 1950s | 1960s | 1970s | 1980s | 1990s | 2000s | 2010s |
|---|---|---|---|---|---|---|---|---|---|
| sso-trend200 | +5.4% | +7.9% | +8.2% | +5.2% | +2.4% | +2.1% | +2.4% | +2.3% | -0.9% |
| sso-hold | -11.4% | +5.3% | +12.6% | +1.9% | -3.3% | +3.3% | +8.6% | -9.4% | +8.6% |
| voltarget-sso | +0.7% | -0.8% | +6.3% | -0.3% | -0.5% | +1.4% | +1.2% | +0.8% | +0.1% |
| halloween-sso | -10.0% | -4.9% | +6.1% | +5.3% | +6.7% | +1.0% | +4.8% | +0.6% | +4.8% |
| tom-sso | +18.7% | -1.1% | -1.7% | +2.2% | +4.8% | -2.0% | -4.8% | +0.4% | -8.5% |
| spy-trend200 | +4.9% | +0.2% | -2.7% | +0.9% | +1.8% | -1.3% | -3.7% | +3.2% | -5.6% |
| rsi2-sso | +4.1% | -9.0% | -12.7% | -12.8% | -1.5% | -8.9% | +0.4% | +8.0% | -10.1% |

Tests failed:

- `sso-trend200`: none
- `sso-hold`: drawdown_within_10_points
- `voltarget-sso`: post_publication_excess_positive
- `halloween-sso`: post_publication_excess_positive
- `tom-sso`: ahead_in_6_of_9_decades, rolling_10y_share_at_least_60pct, post_publication_excess_positive, positive_at_20bps
- `spy-trend200`: full_excess_positive, ahead_in_6_of_9_decades, rolling_10y_share_at_least_60pct, post_publication_excess_positive, positive_at_20bps
- `rsi2-sso`: full_excess_positive, ahead_in_6_of_9_decades, rolling_10y_share_at_least_60pct, post_publication_excess_positive, positive_at_20bps

KNOWN_HISTORY: these decades are well studied (the rules were found on parts of them), so a pass is evidence of robustness, not of future returns. The forward record of the alerts is the test.
