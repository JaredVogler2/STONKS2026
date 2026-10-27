# Forward shadow: runbook and evidence contract (Discovery V3 PR-A)

| | |
|---|---|
| What | A prospective, append-only record of what the frozen protocol-2 (A1) arms decide after each NYSE close, and, after the label window, what happened |
| Series | `qv2a1-frozen-v1`, lane `FROZEN_CONFIRMATORY`. Decisions from **2026-10-23** (`PROTOCOL_V2.chronology.forward_shadow`) |
| Arms | the official run's selection lock `017070e1…`: **S1 E0X1, S2 E0X1, S3 C_X0, S5 R10**. S1 and S3 are Discovery V3's arms of primary interest; S2 and S5 are recorded too, because the registered forward test is Holm over all four |
| Mode | **Research only.** No orders, alerts, Discord messages, model signing, promotion or group-actionable output, whatever is recorded |
| Status | Implemented in PR-A. It **records nothing until the workflow is on `main` and its first scheduled run succeeds**. Read the ledger's health (below) before calling it "running" |
| Exploratory lanes | The evidence tournament's cohorts, `v3x-epa-tournament-v1` and `v3x-epa-tournament-c1-v1`, use the same recorder machinery, but run in their own workflow (`apex-evidence-tournament.yml`), each with its own chain and ledger issue. They share nothing with this lane: [TOURNAMENT_LANE.md](../evidence/TOURNAMENT_LANE.md), [TOURNAMENT_COHORT_1.md](../evidence/TOURNAMENT_COHORT_1.md) |

## 1. How it works

The workflow `apex-forward-shadow.yml` runs at **18:12 and 21:52 New York time on weekdays, plus 12:12 on Saturdays**. A Friday session's window stays open until Monday's open, so the Saturday run is one more attempt for it. Each run makes one idempotent pass (`apex research forward-shadow run`):

1. **Frozen inputs.** It verifies that:
   - protocol-2's sha256 is the A1 hash `33bd4e99…`;
   - the embedded selection lock hashes to `017070e1…`, both on its own and recomputed from the committed `strategy_metrics.json`;
   - the recorded arms are the locked ones.

   Any mismatch stops the run (`FROZEN_LANE_INVALID`).
2. **Ledger read and verification.** It reads every record and checks:
   - its digest;
   - its link to the previous record (`seq` and `previous_digest`);
   - the uniqueness key (series, session, record type);
   - the session order, and that every session since 2026-10-23 has exactly one DECISION or MISSED_OBSERVATION.

   An edited, inserted, deleted or reordered record, or a gap, stops the run (`CHAIN_VIOLATION`). Nothing is written.
   Only the workflow's bot can write records. The repository is public, so anyone may comment on a ledger issue: ordinary comments are ignored, and a comment that copies a record's marker is ignored too and named in the run report. It never stops the series.
3. **Outbox.** A record whose post GitHub did not confirm in an earlier run is re-posted with its **original digest and `observed_at`**. Its anchor time is the late repost, so it counts as *anchored late*.
4. **Genesis or configuration check.** On the first run it writes `SERIES_GENESIS` with the full series configuration and the behaviour fingerprint. On later runs it checks that the code's configuration still hashes to the genesis value; if not, it stops with `SERIES_CONFIG_CHANGED`.
5. **Sessions.** It takes every session since 2026-10-23 whose observation window has opened. Sessions that already have a terminal record are skipped.
   - **The window.** It opens 90 minutes after the regular close (14:30 ET on half days) and closes at the next session's regular open.
   - **Window closed, nothing recorded:** `MISSED_OBSERVATION`, with one of these reasons:
     - `NO_RUN_IN_WINDOW`;
     - `ATTEMPT_FAILED:<status>`;
     - `DECISION_COMPUTED_AFTER_DEADLINE`;
     - `BEFORE_SERIES_GENESIS`, for sessions whose deadline passed before the recorder first ran.
   - **Window open:** the run fetches fresh Alpaca historical SIP bars from the anchor session 2024-03-26 through *t* and runs protocol-2's quality inventory. It then computes the decision with the study's own functions (§3).
     - If the bar for *t* is missing or partial, it writes `OBSERVATION_ATTEMPT_FAILED` once and the job warns; the next attempt retries.
     - If the decision is ready only after the deadline, it is recorded as missed.
6. **Outcomes.** Each `DECISION` whose label window has matured gets one `OUTCOME`, in session order. Maturity means the data end has reached *t* + 22 sessions; the data end is the latest completed session minus protocol-2's 2-session finality lag. The outcome names the decision's record id and digest and never edits it. If a trade is still unresolved, it waits up to 5 more sessions.
7. **Report.** The job summary shows counts only. Outcome values are deliberately never summarised before the registered look.

**Exit codes and alarms.** A red job is the alarm: GitHub notifies the owner of a failed scheduled run.

| Code | Status | Job |
|---|---|---|
| 0 | ok | green |
| 3 | source pending | warning only; retried in the window |
| 4 | a session was missed | red |
| 5 | source invalid or credentials missing | red |
| 6 | integrity | red |
| 7 | anchor pending or store permission | red |

## 2. Where the records live, and why that is an independent timestamp

**Storage.** Records are comments on a dedicated issue that the workflow creates: **"APEX forward-shadow ledger · qv2a1-frozen-v1 · vol 1"**. A new volume follows at 2,000 comments.

**Comment contents.** Each comment holds:
- a human-readable header;
- the record as base64 of gzip(canonical JSON). Records over 60,000 characters span several comments.

**The anchor.** GitHub stamps each comment with its own server time (`created_at`) and keeps the edit history. The **anchor of a decision is that comment's `created_at`**. A decision counts as observed on time only if GitHub created the comment before the session's deadline. The recorder reads every comment back, and only then reports it as posted.

**Why issue comments:**
- the job needs **`contents: read` and `issues: write` only**;
- it never commits, pushes or touches `main`;
- there is no external service and no cost.

**Other copies:**
- Each run uploads `report.json` (digests, anchors, counts), `summary.md` and the outbox, with 30-day retention.
- `apex research forward-shadow export` writes a JSONL snapshot plus anchors. Commit it in a reviewed PR under `research-results/forward-shadow/` at least monthly; that is the off-site copy (test FWD18).

**What a record may contain:**
- symbols;
- gate codes;
- unitless features;
- order plans (limit, open guard, stop, target, in dollars at decision time);
- control draw orders;
- returns;
- hashes.

Bars (OHLCV) are never stored. The store refuses any record with a raw bar field or a credential-shaped string (`PUBLICATION_BLOCKED`), and the repository is private.

## 3. What is recorded

**DECISION (after the close of *t*, before any outcome):**
- **Window and source:** the observation window, and the source provenance:
  - provider, feed and adjustment;
  - `fetched_at` and requests;
  - history and dataset hashes;
  - the quality counts and the excluded symbols;
  - the SPY volume ratio and the bar-*t* coverage.
- **U1 funnel:** one gate code per member:
  - `PASS`, `NOT_MEMBER`, `NO_BAR_T`;
  - `PRICE`, `DOLLAR_VOLUME`, `AVG_VOLUME`, `HISTORY`, `EXTENSION`;
  - `RISK_OFF_BREAKER`, `META_EXCLUDED`.

  The codes are asserted against the study's gate mask.
- **Regime:** the label and probabilities.
- **Per arm:** the funnel (triggered, regime-filtered, not eligible, signals, level rejections, planned). Per signal:
  - unitless trigger features;
  - the order plan at 25 bps, the stop distance in ATR and the net reward/risk;
  - a 252-session beta;
  - a 16-hex hash of the decision bar;
  - the **matched-control reserve**: the stratum, the seeded draw order, and each candidate's own plan, up to 30.
- **S5:** the target weights on rebalance sessions (every 10th session of the protocol axis).

**OUTCOME:**
- **The study's labelling of the frozen plans:**
  - corporate-action quarantine;
  - de-duplication (one position per arm and symbol until the pessimistic exit);
  - incomplete labels.
- **Values:** per-signal net on both legs at 0/10/25/50 bps, and the touch-fill sensitivity.
- **Controls and pairing:** the first K = 10 reserve candidates that survive quarantine; the paired value; and a *hypothetical* paper market leg (minus SPY, minus β·SPY; no borrow, financing or hedge cost).
- **S5:** the *t*+1 return of the forward S5 book and its exposure-matched SPY/cash benchmark.
- **Revisions:** data revisions, with both bar hashes.

## 4. Proof that the frozen lane is the study's logic

`tests/test_forward_shadow_parity.py` uses a synthetic fixture with planted S3 retests and quarantines. It proves:
1. **Causality.** A decision computed from a window ending at *t* equals the decision computed from the full history.
2. **No future dependence.** Multiplying every later price by 1.5 and later volume by 10 changes no decision at *t*.
3. **Labels match the study.** Decisions plus outcomes reproduce the study's `run_arm` rows exactly. That covers kept trades, quarantines, de-duplication, values on every leg and cost, touch-fill, and the K control symbols and values. The arms are S1 E0X1 (7 kept), S2 E0X1 (92 kept, 2 quarantined, 81 de-duplicated), S3 C_X0 (7 kept) and S2 E0X0 (65 kept, 67 level rejections, i.e. target exits).
4. **S5 matches.** The forward S5 book equals the study's S5 series.
5. **The fingerprint is pinned.** The golden behaviour fingerprint `3d8e3b5a…` equals the pinned constant that every recording run checks. A behaviour change therefore stops the series (`SERIES_BEHAVIOUR_CHANGED`) instead of silently changing it.

Changing frozen behaviour requires a **new series id**. The old series is never re-run.

**What the reserve does:**
- Which reserve candidates become the K controls depends on later corporate actions and label completeness, exactly as in the study.
- The draw order and every plan are fixed at *t*.
- If more than 20 of the 30 were rejected, the control set would be smaller than K. This is flagged `reserve_exhausted` and has not occurred in the fixture.

## 5. Owner setup

**Already in place:**
- The `real-equities-research` environment (deployment branch `main`) holds `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY`. They were used by run 39849016753.
- No new secret is needed.

**Required:** merge PR-A into `main`. Scheduled workflows run only from the default branch. The first session that can be captured is the first session whose window is still open after the merge:
- the window of Friday 2026-10-23 is open from 17:30 ET on 10-23 until the open on Monday 2026-10-26, so it is captured if PR-A is merged in time for the 21:52 ET Friday run, the 12:12 ET Saturday run or a manual dispatch before Monday 09:30 ET;
- merged later, earlier sessions are recorded as `MISSED_OBSERVATION (BEFORE_SERIES_GENESIS)`, never back-filled.

**Recommended (optional):**
- **Start immediately after merging.** Use *Actions → apex-forward-shadow → Run workflow* (branch `main`, reason "first run"). A dispatch records exactly what a scheduled run would at that moment.
- **Permissions.** If an organisation or repository policy forbids workflows from requesting `issues: write`, the first run fails with `LEDGER_UNREADABLE` / `STORE` and `OWNER_SETUP_REQUIRED`. To fix it, allow it under *Settings → Actions → General → Workflow permissions*. Either default is fine; the job requests its own scopes.
- **Notifications.** Keep them on for failed workflow runs: *Settings → Notifications → Actions*.
- **Unsubscribing.** Unsubscribe from the ledger issue if its comments are noisy. The issue is for the recorder only; do not edit or delete its comments.

**Verify at any time (read-only):**

```bash
cd apps/pi-worker
GITHUB_TOKEN=<a token with issues:read> GITHUB_REPOSITORY=JaredVogler2/STONKS2026 \
  uv run apex research forward-shadow verify --store github
uv run apex research forward-shadow health --store github --markdown     # same env
uv run apex research forward-shadow export --store github --out <dir>   # monthly snapshot for a reviewed PR
```

**Offline DEMO** (synthetic fixture, no keys or network): `uv run apex research forward-shadow smoke --out /tmp/fs-smoke`. CI runs it in every PR as the `forward-shadow smoke` job.

## 6. Cost

| Item | Cost |
|---|---|
| Runner minutes, expected | about 4–6 per decision run and 1–2 per no-op retry: roughly 120–180 a month |
| Runner minutes, worst case | 12 × (2 × 23 + 5) = 612 a month. Reserved as REQUIRED in `release/budget.py` before optional drift |
| Alpaca requests | about 40–60 per decision (the window from 2024-03-26 for 512 symbols, plus corporate actions); outcomes add about 15. The protocol caps each fetch at 300 |
| GitHub | 2 comments per session (decision, later outcome), plus one per failed attempt or missed session |
| Paid services | none |

## 7. Limitations (read before using any forward result)

**Universe and data:**
- **Survivorship-limited universe.** U1 is the 2026-10-22 S&P 500 snapshot. Forward index additions and deletions are not captured; a changed snapshot hash forks the series.
- **Preliminary decision bar.** The decision uses the post-close bar of *t*. Revisions are detected at maturity (bar hashes) and reported, never silently applied to the decision.

**Calendar and anchors:**
- **Calendar closures.** An unscheduled market closure that the pinned `exchange_calendars` does not know makes SPY look incomplete. Recording then fails closed (`SOURCE_INVALID`) until the calendar dependency is updated, and those sessions are missed.
- **Anchor trust.** GitHub's timestamps and the hash chain prove order and the absence of later edits against everyone except GitHub itself. A second, external anchor such as OpenTimestamps is not implemented.

**Evidence and statistics:**
- **What a forward result can show.** A positive forward result would still need:
  - an independent replication;
  - an intraday execution review;
  - the release and legal gates in docs/RELEASE_AUDIT.md.

  NO_TRADE remains the expected state.
- **Registered look dates.** The registered status check on 2027-04-30 can only report `INSUFFICIENT_CLUSTERS`. The first powered look is about late October 2028 (docs/quant-v2/FORWARD_SHADOW_PLAN.md §4).

## 8. Acceptance tests (FWD01–FWD18, `10_EXECUTABLE_ACCEPTANCE_GATES.md`)

| ID | Test(s) |
|---|---|
| FWD01, FWD12 | `test_fwd01_fwd12_decision_in_window_is_recorded_and_anchored_on_time` |
| FWD02, FWD15 | `test_fwd02_fwd15_late_run_records_missed_and_catches_up_without_backfill`, `test_pending_until_deadline_becomes_missed_with_the_attempt_reason` |
| FWD03 | `test_fwd03_weekend_and_holiday_runs_create_no_session` |
| FWD04 | `test_fwd04_windows_follow_dst_and_half_days` |
| FWD05 | `test_fwd05_duplicate_dispatch_and_retry_are_idempotent`, `test_fwd05_lost_response_does_not_duplicate` |
| FWD06 | `test_fwd06_edit_insert_delete_reorder_all_fail_verification`, `test_fwd06_edited_comment_is_detected`, `test_fwd06_tampered_ledger_stops_recording`, `test_store_rejects_foreign_or_conflicting_record_comments` |
| FWD07 | `test_fwd07_unconfirmed_post_goes_to_outbox_and_is_reposted_with_the_same_digest` |
| FWD08 | `test_fwd08_outcome_waits_for_maturity_and_is_appended_once` |
| FWD09 | `test_frozen_decisions_plus_outcomes_equal_the_study_rows[...]` (no fills, gaps, ambiguity, quarantine, de-duplication, costs) |
| FWD10 | `test_fwd10_exploratory_lane_is_separate_and_never_confirmatory` |
| FWD11 | `test_fwd11_missing_credentials_fail_with_owner_setup_and_no_fallback` |
| FWD13 | `test_fwd13_future_bars_and_a_future_catalyst_cannot_change_a_decision`, `test_window_ending_at_t_reproduces_full_history_decisions` |
| FWD14 | `test_behaviour_fingerprint_or_config_change_fails_closed` (snapshot change → `SERIES_CONFIG_CHANGED`) |
| FWD16 | `test_fwd16_publication_guard_blocks_raw_bars_and_secrets`, `test_decision_records_carry_no_raw_bars` |
| FWD17 | `test_fwd17_health_states` |
| FWD18 | `test_fwd18_export_restore_keeps_hash_verification` |

**Also covered:**
- the frozen-input checks;
- server-side anchor time;
- volume rollover and multi-part records;
- REST permission and rate-limit mapping;
- source-pending handling;
- the S5 book start;
- the workflow's static invariants (`tests/test_forward_shadow_workflow.py`).

The V3 exploratory lane (`research/forward/exploratory.py`) has its schema and guards, but no policy is registered. PR-C registers the first exploratory policies.
