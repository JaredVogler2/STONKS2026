# Personal method study `personal-study-3b` (personal-study-3b-2026-10-28)

Status **COMPLETED**. FULL window: decisions 2017-01-31 → 2026-09-23, in halves split at 2021-11-22; DEV window from 2019-10-22 (DEV_CONTAMINATED). $1000, whole shares, fills at the next open, 5 bps a side; SPY: the same $1000, fractional and frictionless. Total returns (splits and dividends), SPY's too.

**No method beat SPY repeatedly under the registered rule.**

| Method | Role | $1000 → (FULL) | SPY → | Growth / yr | Excess / yr FULL | 1st half | 2nd half | DEV | Max drawdown (SPY) | Ahead in 36-month windows | 90% bootstrap, excess / yr | Excess at 20 bps | Trades / yr | Holm p | Disposition |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sso-hold | trial | $7,463 | $3,899 | +23.2% | +6.8% | +10.3% | +3.1% | +6.7% | -59% (-34%) | 94% | +4.4% to +16.8% | +6.7% | 0.1 | 0.057 | BEATS_SPY_PARTLY |
| mtum-hold | trial | $4,452 | $3,899 | +16.8% | +1.4% | +2.5% | -0.7% | +0.2% | -34% (-34%) | 38% | -3.1% to +6.8% | +1.4% | 0.1 | 1.000 | BEATS_SPY_PARTLY |
| sso-trend200 | reference | $4,448 | $3,899 | +16.8% | +1.4% | +2.6% | -0.2% | +2.9% | -36% (-34%) | 60% | -4.7% to +10.3% | -0.2% | 10.6 | — | BEATS_SPY_PARTLY |
| hfea | trial | $3,371 | $3,899 | +13.5% | -1.5% | +15.9% | -15.5% | -5.3% | -69% (-34%) | 38% | -9.0% to +14.4% | -1.5% | 6.4 | 1.000 | DOES_NOT_BEAT_SPY |
| voltarget-sso | trial | $3,368 | $3,899 | +13.5% | -1.5% | -2.9% | -0.5% | -2.7% | -26% (-34%) | 25% | -5.8% to +2.7% | -2.1% | 20.2 | 1.000 | DOES_NOT_BEAT_SPY |
| adm | trial | $2,668 | $3,899 | +10.7% | -3.9% | -1.3% | -6.7% | -5.7% | -35% (-34%) | 1% | -8.3% to +0.2% | -5.1% | 7.6 | 1.000 | DOES_NOT_BEAT_SPY |
| halloween-sso | trial | $2,240 | $3,899 | +8.7% | -5.8% | -4.7% | -7.1% | -7.8% | -59% (-34%) | 16% | -11.2% to +3.3% | -6.3% | 4.5 | 1.000 | DOES_NOT_BEAT_SPY |
| tom-sso | trial | $1,831 | $3,899 | +6.5% | -7.9% | -8.1% | -7.7% | -4.9% | -21% (-34%) | 25% | -15.2% to -0.7% | -14.9% | 50.4 | 1.000 | DOES_NOT_BEAT_SPY |
| gtaa5 | trial | $1,747 | $3,899 | +6.0% | -8.3% | -9.9% | -7.2% | -8.3% | -7% (-34%) | 0% | -14.1% to -4.4% | -8.9% | 21.4 | 1.000 | DOES_NOT_BEAT_SPY |
| vaa-g4 | trial | $1,744 | $3,899 | +6.0% | -8.4% | -7.5% | -9.4% | -10.2% | -17% (-34%) | 1% | -14.3% to -3.8% | -10.6% | 16.0 | 1.000 | DOES_NOT_BEAT_SPY |
| rsi2-sso | trial | $1,727 | $3,899 | +5.8% | -8.5% | -12.6% | -4.4% | -5.4% | -27% (-34%) | 5% | -14.8% to -3.3% | -13.6% | 35.8 | 1.000 | DOES_NOT_BEAT_SPY |

Tests failed (FULL window unless named):

- `sso-hold`: drawdown_within_10_points
- `mtum-hold`: second_half_excess_positive, rolling36_share_at_least_60pct
- `sso-trend200`: second_half_excess_positive, positive_at_20bps
- `hfea`: full_excess_positive, second_half_excess_positive, drawdown_within_10_points, rolling36_share_at_least_60pct, positive_at_20bps
- `voltarget-sso`: full_excess_positive, first_half_excess_positive, second_half_excess_positive, rolling36_share_at_least_60pct, positive_at_20bps
- `adm`: full_excess_positive, first_half_excess_positive, second_half_excess_positive, rolling36_share_at_least_60pct, positive_at_20bps
- `halloween-sso`: full_excess_positive, first_half_excess_positive, second_half_excess_positive, drawdown_within_10_points, rolling36_share_at_least_60pct, positive_at_20bps
- `tom-sso`: full_excess_positive, first_half_excess_positive, second_half_excess_positive, rolling36_share_at_least_60pct, positive_at_20bps
- `gtaa5`: full_excess_positive, first_half_excess_positive, second_half_excess_positive, rolling36_share_at_least_60pct, positive_at_20bps
- `vaa-g4`: full_excess_positive, first_half_excess_positive, second_half_excess_positive, rolling36_share_at_least_60pct, positive_at_20bps
- `rsi2-sso`: full_excess_positive, first_half_excess_positive, second_half_excess_positive, rolling36_share_at_least_60pct, positive_at_20bps

Calendar years, FULL window (method vs SPY):

- `sso-hold`: 2017 +39% vs +19% · 2018 -15% vs -5% · 2019 +63% vs +31% · 2020 +22% vs +18% · 2021 +60% vs +29% · 2022 -39% vs -18% · 2023 +47% vs +26% · 2024 +43% vs +25% · 2025 +26% vs +18% · 2026 +22% vs +13%
- `mtum-hold`: 2017 +32% vs +19% · 2018 -2% vs -5% · 2019 +27% vs +31% · 2020 +30% vs +18% · 2021 +13% vs +29% · 2022 -18% vs -18% · 2023 +9% vs +26% · 2024 +33% vs +25% · 2025 +22% vs +18% · 2026 +27% vs +13%
- `sso-trend200`: 2017 +39% vs +19% · 2018 -14% vs -5% · 2019 +26% vs +31% · 2020 +9% vs +18% · 2021 +60% vs +29% · 2022 -25% vs -18% · 2023 +18% vs +26% · 2024 +43% vs +25% · 2025 +16% vs +18% · 2026 +17% vs +13%
- `hfea`: 2017 +42% vs +19% · 2018 -13% vs -5% · 2019 +69% vs +31% · 2020 +70% vs +18% · 2021 +39% vs +29% · 2022 -63% vs -18% · 2023 +28% vs +26% · 2024 +13% vs +25% · 2025 +18% vs +18% · 2026 +7% vs +13%
- `voltarget-sso`: 2017 +37% vs +19% · 2018 -4% vs -5% · 2019 +20% vs +31% · 2020 -3% vs +18% · 2021 +27% vs +29% · 2022 -14% vs -18% · 2023 +23% vs +26% · 2024 +27% vs +25% · 2025 +16% vs +18% · 2026 +10% vs +13%
- `adm`: 2017 +25% vs +19% · 2018 +7% vs -5% · 2019 +10% vs +31% · 2020 +20% vs +18% · 2021 +21% vs +29% · 2022 -29% vs -18% · 2023 +15% vs +26% · 2024 +17% vs +25% · 2025 +20% vs +18% · 2026 +8% vs +13%
- `halloween-sso`: 2017 +18% vs +19% · 2018 -18% vs -5% · 2019 +55% vs +31% · 2020 -8% vs +18% · 2021 +33% vs +29% · 2022 -29% vs -18% · 2023 +51% vs +26% · 2024 +16% vs +25% · 2025 -12% vs +18% · 2026 +11% vs +13%
- `tom-sso`: 2017 +1% vs +19% · 2018 -13% vs -5% · 2019 +3% vs +31% · 2020 +51% vs +18% · 2021 +7% vs +29% · 2022 +1% vs -18% · 2023 +10% vs +26% · 2024 -1% vs +25% · 2025 -4% vs +18% · 2026 +19% vs +13%
- `gtaa5`: 2017 +7% vs +19% · 2018 -2% vs -5% · 2019 +7% vs +31% · 2020 +3% vs +18% · 2021 +21% vs +29% · 2022 -4% vs -18% · 2023 +4% vs +26% · 2024 +5% vs +25% · 2025 +8% vs +18% · 2026 +11% vs +13%
- `vaa-g4`: 2017 +14% vs +19% · 2018 +9% vs -5% · 2019 +5% vs +31% · 2020 +11% vs +18% · 2021 +8% vs +29% · 2022 -13% vs -18% · 2023 +9% vs +26% · 2024 -0% vs +25% · 2025 +14% vs +18% · 2026 +5% vs +13%
- `rsi2-sso`: 2017 +7% vs +19% · 2018 -18% vs -5% · 2019 +4% vs +31% · 2020 +13% vs +18% · 2021 +17% vs +29% · 2022 -5% vs -18% · 2023 +0% vs +26% · 2024 +26% vs +25% · 2025 +15% vs +18% · 2026 +4% vs +13%

DEV_CONTAMINATED diagnostics, never validation: the forward record of the alerts is the test.
