# Codebase review: why APEX finds no edge, and what to change

| | |
|---|---|
| Scope | `apps/pi-worker`: about 38,600 lines of Python under `apex_worker` and 719 test functions. Reviewed at `main` `03f0538` (the PR #28 merge) |
| Question | Why has every research batch ended NO_TRADE? Which parts of the code constrain the research, and which could help it? |
| Outcome | The engineering is sound, and the binding constraint is methodological. The replacement method is in [EVIDENCE_TOURNAMENT.md](EVIDENCE_TOURNAMENT.md) |

File references are relative to `apps/pi-worker/src/apex_worker/`.

## 1. What is sound (keep it)

| Area | Evidence |
|---|---|
| **No trading path exists** | The only brokerage URL is `TRADING_BASE` (`market/adapters/alpaca.py:25`), and it is used only for `GET /v2/assets` (`:150`). A search of `src/` finds no `v2/orders`, `submit_order`, `place_order` or `TradingClient`. `tests/test_real_equities.py` asserts that research never references Discord, the agent client or orders |
| **Research integrity** | Present throughout:<br>- hash-chained trial registries (`discovery/v3/registry.py`);<br>- plans pinned by sha256 before their run;<br>- engine self-checks (a look-ahead trap that must be detected, and a placebo that must stay quiet);<br>- a publication guard (aggregates only);<br>- a frozen forward lane with a pinned behaviour fingerprint (`research/forward/frozen.py:646`) |
| **Execution realism** | A measured cost model (`cost-v3-2`, from SIP quotes) and close and open execution arms. Two independent book engines agree on the pilot-2 result to 0.04 pp |
| **One strategy codebase** | Research and the live engine import the same strategies (`signals/strategies/quant_v2.py`, `library.py`), so research results describe the code that would run |

## 2. Methodological findings (the binding constraints)

| Id | Finding | Consequence |
|---|---|---|
| **M-1** | **Validate on history, then allocate.** The path is DEV search, then Holm-gated dispositions, then (never reached) nomination. DEV is now contaminated twice, PRESAMPLE is spent, and U1 is a survivor universe | The method cannot say yes to a realistic edge (power, §3) and cannot trust a yes from this history (mirages). NO_TRADE follows by construction |
| **M-2** | **Fixed-horizon inference on a forward shadow that publishes every session.** The frozen lane posts each decision to issue #24, but its registered evaluation is a single fixed-horizon look | Reading interim results is invalid: a t-test read at every session gives **34%** false claims at nominal 5% ([battery A](EPA_BATTERY.md)). An anytime-valid readout is needed |
| **M-3** | **Concentrated, high-tracking-error designs.** S1 and S3 swing books and the 10-name momentum books track SPY with 15–27% error | Evidence velocity is decades. G2 needs about 35 years even at its DEV estimate ([method §2](EVIDENCE_TOURNAMENT.md)). The design space was never searched for information rate |
| **M-4** | **Binary dispositions that do not accumulate.** Each batch restarts Holm over its own trials, while the cumulative trial count only grows (17) | Evidence is thrown away between batches. A batch's prior information is either ignored or used in a way that invalidates the next test |
| **M-5** | **Survivorship and point-in-time data are treated as historical blockers.** U2 membership and point-in-time event timestamps are `SOURCE_BLOCKED` | Both blockers apply only to *historical* research. Prospective records have no survivorship, and live event capture is point-in-time by construction. A prospective-first program dissolves them (C-1) |

## 3. Code-level findings

| Id | Where | Finding | Suggested change |
|---|---|---|---|
| **C-1** | `catalysts/sec.py`, `catalysts/form4.py`, `catalysts/earnings.py`; `runtime/worker.py:619-627`; `runtime/engine.py:85` | A tested point-in-time SEC pipeline exists:<br>- 8-K and Form 4 filings with EDGAR acceptance times;<br>- `form4_to_catalyst` refuses filings without an acceptance time;<br>- `reported_from_8k` gives before-open or after-close earnings timing.<br>**Nothing consumes it.** Research marks S4, H3 and H7 `NOT_EVALUABLE` (`research/realeq/study_v2.py:974-976, 1044-1046`). The live `Worker` is built without a `catalysts_provider`, so the engine defaults to `lambda: []`, and earnings status is `UNKNOWN` (`signals/candidates.py:309`) | Feed the SEC pipeline to a **prospective** lane. It needs no backfill, so S4, H3 and H7 become evaluable from the first recorded session |
| **C-2** | `catalysts/sec.py:172-175`; `catalysts/tickers.py:180-185` | Only `filings.recent` is parsed; the older `filings.files` pages are never fetched. Where the ticker alias table has no history for a company, its historical filings take its current ticker | Matters only for backfills. A prospective lane avoids both |
| **C-3** | `features/panel.py:58-85`; `features/regime.py` | Cross-sectional ranks and breadth use every symbol supplied unless `membership` is passed, and only `study_v2` passes it | Any new research path must pass point-in-time membership. Prospectively, today's membership is point-in-time |
| **C-4** | `market/history.py:47-53`; `research/realeq/data_v2.py:73` | Bars are split-adjusted only, so dividends are excluded for SPY and for stocks alike. Low-yield books (momentum, growth) gain by roughly the dividend-yield gap: SPY yielded about 1.2–1.9% over DEV. This slightly inflates G2-type results against SPY | Use total-return bars (`adjustment="all"`) for the prospective lane and any new protocol; never change a published protocol retroactively |
| **C-5** | `market/universe.py:65-102` | The live universe is today's active assets, filtered on their last 20 bars. The code warns about survivorship itself (`survivorship_warning`) | Fine for live use; must not feed historical research |
| **C-6** | Tests | No direct tests for `signals/context.py`, `signals/monitor.py`, `market/ingest.py`, `runtime/universe_job.py` or `operations/doctor.py` | Add tests before any of them feeds a prospective lane |

## 4. Diagnosis in one paragraph

The code does what it was asked, with unusual care. What it was asked to do cannot succeed:
- **Power.** Validating a realistic long-only edge over SPY needs decades of data, on any history and by any test. The information limit is IR²/2 nats a year.
- **History.** The available history is contaminated and survivor-biased, so even a pass would be suspect.
- **What follows.** More signals, more DEV pilots or stronger multiple-testing corrections would not change the outcome. Only the method can:
  - collect evidence prospectively, where it cannot be contaminated;
  - read it with anytime-valid statistics, so it can be watched;
  - let paper capital follow it, so exploration is cheap and bounded;
  - design candidates for information rate, so evidence arrives in years rather than decades.

That is the evidence tournament ([EVIDENCE_TOURNAMENT.md](EVIDENCE_TOURNAMENT.md)). Its simulated behaviour is in [EPA_BATTERY.md](EPA_BATTERY.md).
