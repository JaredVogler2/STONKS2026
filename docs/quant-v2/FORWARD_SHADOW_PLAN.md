# Quant V2 forward shadow: a prospective, append-only validation process

| | |
|---|---|
| Status | **Plan (Phase 6 of the M6 assignment).** Not implemented, and nothing runs yet. |
| Registered window | Decisions from **2026-10-23** (`PROTOCOL_V2.chronology.forward_shadow`). Nothing earlier counts. |
| Protocol | `realeq-protocol-2` as amended (A1), with the arms locked by the first A1 run (its `selection_lock`) |
| Mode | Research only: no Discord signals, alerts, group recommendations, promotion or orders, whatever the result |

## 1. Why a forward shadow is still required

The historical evaluation has two weaknesses that a forward window removes:
- **PRESAMPLE is earlier, not later.** It is backward-time evidence.
- **PRESAMPLE is exposed.** The cancelled A0 run published its outcomes (PROTOCOL_V2.md §12).

Beyond that, the universe is survivorship-limited and the data are daily bars. A positive historical result is a reason to watch prospectively, not a reason to trade.

## 2. What is recorded, and when

Every NYSE session *t*, after the close, a scheduled GitHub Actions job (research environment, read-only market-data secrets) writes **one decision record** before any outcome exists.

| Field | Content |
|---|---|
| Identity | session date *t*; record sequence number; the sha256 of the previous record (a hash chain) |
| Versions | code commit SHA; protocol sha256; `selection_lock.lock_sha256`; execution, inference, features and strategies versions; levels version |
| Data provenance | provider and feed (Alpaca SIP, split-adjusted, 15-minute-delayed entitlement), `fetched_at`, request count, per-symbol content hashes of the bars used |
| Universe at *t* | every U1 member with `date_added ≤ t`. For each one: gate status (pass, or the failing gate) and sector. **All eligible candidates are recorded**, not only signals. |
| Signals | per strategy and registered arm: symbol, trigger values, `setup_score`, invalidation hint |
| Decision-time observables | the close of *t*, ATR(14), the levels from `compute_levels` at 25 bps (entry zone, stop, target, net R/R), and the planned order (E0/E1 limit, open guard, validity t+1..t+2, horizon 20) |
| Controls | the K = 10 matched-control symbols, drawn at *t* with the registered seed and the same geometry, so controls are fixed before outcomes exist |
| S5 | the target weights decided at the close of *t* (rebalance sessions) and the regime label |

**Outcomes come later, in a separate record.** They are computed only when a decision's label window has closed: 22 sessions plus the 2-session finality lag. They are keyed by the decision record's id and never written into it. A data revision after a decision is recorded as a revision with both hashes, never as a silent change.

## 3. No retrospective editing

**Append-only files:**
- One JSONL decision file and one JSONL outcome file per month.
- Each record carries the previous record's sha256, so any later edit breaks the chain.

**Independent timestamps:**
- After each run, the job posts the day's chain head (a hash only, no prices) as a comment on a dedicated tracking issue.
- GitHub timestamps the comment and keeps its edit history. That gives a third-party record that the decision existed before its outcome.

**Storage within the repository's rules:**
- No workflow pushes to `main` or holds a write token.
- Records are uploaded as artifacts and copied into `research-results/forward-shadow/` by a reviewed PR at least monthly, inside the 90-day artifact retention.
- The issue-comment hashes let anyone verify that the committed files match what was recorded at the time.

**Licensing:** the files hold decision-time levels and returns. Raw vendor OHLCV is never committed. The per-symbol bar hashes allow verification without redistributing licensed data.

**Fixed code:** code changes after 2026-10-23 do not change how earlier sessions are recorded. A changed implementation starts a new shadow series with its own version stamp; earlier records are never re-run under it.

## 4. How it is evaluated

**Statistics:** the same estimand and statistics as PRESAMPLE:
- per-signal paired net versus the session's matched controls, at 25 bps, pessimistic leg;
- the conservative block test over L = 20 and 40 on gate-open sessions;
- Holm over the locked S1, S2, S3 and S5 arms;
- the same dispositions.

**Cluster feasibility.** The frozen rule needs at least 12 non-empty blocks at every decision block length:

| Requirement | Sessions | First date with matured labels |
|---|---|---|
| L = 20 | 240 decision sessions | 2027-10-07 + 22 sessions |
| L = 40 | 480 decision sessions | 2028-09-20 + 22 sessions, about **late October 2028**; later if risk-off breaker closures remove sessions |

**Consequence for the registered date:** the registered earliest evaluation, 2027-04-30, comes after only 130 sessions. It can only return `INCONCLUSIVE (INSUFFICIENT_CLUSTERS)`, so it should be read as a **status and integrity check**, not a test.

**Rules that stay fixed:**
- No threshold is relaxed to make an earlier test possible.
- Interim looks are descriptive only.
- The single confirmatory forward look is at the first date both cluster minimums are met.

**What a positive forward result would still need:**
- an independent replication on data the design never touched: a point-in-time universe (U2) once a licensed source exists, or a later window;
- review of execution realism (intraday replay of the ambiguous fills);
- the release gates and the legal and data-rights review in docs/RELEASE_AUDIT.md.

Only then could an owner consider any non-research use. Group-actionable or high-conviction alerts remain disabled until all of that is done.

## 5. Cost

**Runner minutes:** the daily job fetches one session of U1 daily bars (about 6 requests) and computes features on a rolling window, about 2–5 runner minutes. About 22 sessions a month comes to roughly 50–110 minutes a month, within the free Actions allowance.

**Data and training:** no paid data and no laptop training.

**Quiet days:** a missed day is recorded as missed and is never back-filled with later data.
