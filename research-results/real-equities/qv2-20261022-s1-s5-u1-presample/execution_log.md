# Execution log — `qv2-20261022-s1-s5-u1-presample`

* Run status: **COMPLETED**
* GitHub Actions run: https://github.com/JaredVogler2/STONKS2026/actions/runs/39849016753
* Workflow: apex-real-equities-research; event workflow_dispatch; ref refs/heads/main; attempt 1
* Runner: Linux X64 image ubuntu24 20261012.334.1; Python 3.11.15
* Code commit: `eda340a152ac126f29ec92ba21f11ebd1ae51b07`
* Started 2026-10-22T20:27:49Z — finished 2026-10-22T20:33:47Z
* Credentials: ALPACA_KEY_ID present, ALPACA_SECRET_KEY present (presence only; values never logged)

## Steps

| Step | Status | Detail |
|---|---|---|
| validate inputs | ok | protocol=realeq-protocol-2 source=alpaca universe=u1-sp500 research_only=true |
| credentials | ok | ALPACA_KEY_ID / ALPACA_SECRET_KEY present (values never logged) |
| fetch historical SIP daily bars (U1 + references) | ok | 511/512 symbols, 137 requests, data end 2026-10-20 (finality lag 2 sessions) |
| data-quality and bias inventory | ok | usable 507/512, stocks 495; corporate actions available |
| freeze protocol, boundaries and hashes (manifest.json) | ok | PRESAMPLE 2017-01-03..2019-09-20; DEV 2019-10-23..2026-09-18; manifest sha256 be792db3eca3 |
| lock the DEV-only arm selection (before any PRESAMPLE outcome) | ok | {'S1': 'E0X1', 'S2': 'E0X1', 'S3': 'C_X0', 'S5': 'R10'}; lock sha256 017070e1654680bb; manifest be792db3eca3 |
| evaluate S1-S5, legacy H rules, baselines, controls, self-checks | ok | 32 configurations; aggregate NO_TRADE |
| write reports | ok | manifest, data_quality, strategy_metrics, trades_summary, comparisons, recommendations |

## Data / API errors

None recorded.

## Explicitly NOT run

Nothing skipped.

## Never done by this workflow

* no model signing, registry upload, promotion or champion change; no Discord/group messages; no orders
* no raw OHLCV, keys or user positions in outputs or artifacts (bars lived only in the runner's temp dir)


## Limitations

* SURVIVORSHIP: U1 = current S&P 500 constituents (snapshot 2026-10-22); members that left the index are absent. Eligibility starts at each member's date_added, which removes later additions from earlier cross-sections but not the survivor effect; paired same-date controls from the same member set partly cancel it
* SECTOR LABELS: current GICS sectors applied to all history (the 2018 communication services reclassification)
* EARNINGS UNKNOWN: no point-in-time earnings calendar; S4 is NOT_EVALUABLE and earnings vetoes are not modelled
* PRICE-ONLY RETURNS: split-adjusted bars, dividends not modelled (affects absolute, much less paired, returns)
* DAILY BARS: intraday order is unknown; ambiguous outcomes carry pessimistic and optimistic legs (exec-v2.1); a missing session while held is an ambiguity too
* PRESAMPLE IS EARLIER, NOT LATER: the evaluation window precedes the design data (backward-time historical robustness, not forward out-of-sample); forward evidence is still required
* PRESAMPLE EXPOSURE: the cancelled A0 run 39829472789 computed and published PRESAMPLE outcomes (job log, job summary, artifact). The analyst never read them and no design choice responded to them, but the window is not untouched; read every PRESAMPLE result with that in mind (PROTOCOL_V2.md section 12)
* SURVIVORSHIP-LIMITED UNIVERSE: U1 is not an unbiased all-market sample; results do not generalise to delisted or smaller stocks
* INFERENCE SIZE: on production-shaped synthetic nulls the decision rule (with the A1 Student-t floor) rejects 5.8-6.6% at nominal 5% and 1.6-1.9% at the Holm first step (nominal 1.25%); dependence beyond the block length (slowly varying factor premia) is not fully covered, so a Holm p between 0.03 and 0.05 is borderline evidence
* LARGE MINIMUM DETECTABLE EFFECT: on production-shaped synthetic panels the PRESAMPLE window detects only about +1.5-2 pp per signal at 80% power (5-7x the +0.30 pp economic minimum); a true edge at the economic minimum will most likely read INCONCLUSIVE, and any VALIDATED effect size is likely inflated (winner's curse)
* SPLIT ADJUSTMENT: 2 symbol(s) show a >25% jump on a split ex-date; those trade windows are quarantined
* CORPORATE ACTIONS BY CURRENT TICKER: a spin-off by a parent under a former ticker may be missing from the quarantine; dividends and other distributions are not modelled

