# Execution log — `2026-10-21-fixed78-sip-8y-r2`

* Run status: **COMPLETED**
* GitHub Actions run: https://github.com/JaredVogler2/STONKS2026/actions/runs/39696547618
* Workflow: apex-real-equities-research; event workflow_dispatch; ref refs/heads/main; attempt 1
* Runner: Linux X64 image ubuntu24 20261012.334.1; Python 3.11.15
* Code commit: `2acad29f75a81dbde0a469d1634d8b273de5c9ea`
* Started 2026-10-21T23:56:11Z — finished 2026-10-21T23:57:17Z
* Credentials: ALPACA_KEY_ID present, ALPACA_SECRET_KEY present (presence only; values never logged)

## Steps

| Step | Status | Detail |
|---|---|---|
| validate inputs | ok | source=alpaca years=8 universe=fixed78 research_only=true |
| credentials | ok | ALPACA_KEY_ID / ALPACA_SECRET_KEY present (values never logged) |
| fetch historical SIP daily bars | ok | 78/78 symbols, 16 requests, window 2018-10-21..2026-10-21 |
| data-quality and bias inventory | ok | usable 78/78, candidate stocks 66/66 |
| freeze protocol, boundaries and hashes (manifest.json) | ok | holdout 2025-05-02..2026-09-21; manifest sha256 ac30ce8b338d |
| evaluate H1-H8, baselines, portfolio, robustness | ok | 229 configurations; aggregate NO_TRADE |
| write reports | ok | manifest.json, data_quality.json, strategy_metrics.json, trades_summary.csv, comparisons.md, recommendations.md, execution_log.md |

## Data / API errors

None recorded.

## Explicitly NOT run

Nothing skipped.

## Never done by this workflow

* no model signing, registry upload, promotion or champion change; no Discord/group messages; no orders
* no raw OHLCV, keys or user positions in outputs or artifacts (bars lived only in the runner's temp dir)

