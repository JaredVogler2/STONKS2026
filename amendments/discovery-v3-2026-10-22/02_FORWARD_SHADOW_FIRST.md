# P0 — Implement Prospective, Immutable Research Shadow BEFORE More Alpha Searches

**The owner cannot recover days that were never recorded before outcomes.** First implement `docs/quant-v2/FORWARD_SHADOW_PLAN.md` as actual code and operational workflow, and test it on fake time. The existing file states the pipeline is unimplemented.

## 1. Two completely separate lanes

**Frozen confirmatory lane:** only selected S1 E0X1 and S3 C_X0 with signed/hashed V2 A1 protocol and selection lock from actual run. If exact model feature/spec parity cannot be reconstructed as-of, fail closed and mark session missed. Record a separate SPY-relative or *synthetic unlevered hedged accounting metric* without changing frozen decision/exits. Do not introduce S2/S5 as newly “confirmed”, since already statistically rejected in historical record.

**Open exploration lane:** all new V3 candidates may make time-stamped decisions but are tagged HYPOTHESIS_UNREGISTERED or EXPLORATORY; cannot be claimed confirmatory later for observations they motivated. New pre-registered trial series begins with explicit date/manifest before first future outcomes. Historical V3 data used for development only. No cross-lane mutable objects or shared retrospective P&L overwrite.

## 2. Daily capture spec

At first implementable NYSE close (target 2026-10-23 if configured in time; otherwise next actual completed session, never retroactive):
- Check exchange calendar, post-close finalized source time, true Alpaca SIP/authorized feed and at least known finality. If not available timely, store `SOURCE_PENDING` and stop; do not use later matured price to recreate a 4:00 PM decision under old “available at” fiction. A deliberate following-day delayed observation can be registered as **different latency policy** with actual timestamp and earliest executable following session.
- Stable session ID, complete data cutoff, actual observed_at UTC, verified active code/protocol/feature model hashes, full candidate-universe membership source and exclusions, strategy configs, all gate reasons, actual setup features as-of t, timestamps, expected cost, currency.
- Freeze selected S1/S3 candidate set, action (research-only planned order, NO TRADE), intended entry zone, stop/trailing policy, time horizon/expiry, account capital/sector eligibility under a specified *paper* model, no-fills counted.
- K matched controls selected from decision-time eligible universe with seed/hash and identical execution constraints. Freeze random assignment before observing outcome.
- Versioned risk framework including beta-neutral reference (paper **accounting** hedge not an instruction to short SPY) and SPY/cash opportunity alternative; record hedge prices and costs only when available as-of.
- Record a decision timestamp and **immutable hash chain**: `record_id, seq, previous_digest, SHA256(canonical_decision_json)`, run URL, git_sha, schema_version. Chain must detect any insertion/deletion/reorder, not merely content edits.
- Write *outcomes* to separately versioned append-only records after 22 NYSE sessions plus required finality lag. Link decision digest and realized source hash, reason for missing or censor, costs/ambiguity and fill. Never edit original decisions.
- Store no raw multi-year vendor bars in GitHub; comply with quote redistribution and member privacy.

## 3. Persistence and independent timestamp proof

GitHub scheduled workflow can be delayed or missed. Preferred design:
- Bounded standard GitHub Actions after regular NYSE market close at a valid safe ET time, weekday schedule with NYSE calendar and DST aware logic; near-market-close scheduling is best-effort, NOT guaranteed. Include independent provider-source observed time and actual run timestamp. For short close-to-open latency constraints, use Pi always-on worker and upload signed digest asynchronously if permitted.
- A durable object storage or private permitted Supabase table with immutable unique constraint by `series_id+NYSE_session+record_type`. Outbox/retry idempotent and server-side permissions deny mutation; append amendment record for genuine source correction.
- Short-retention compressed artifact as export plus monthly reviewed PR of compact non-sensitive derived decision manifests when rights permit. Hash-only GitHub issue comment is an external timestamp **only after actual GitHub comment creation**, cannot guarantee at-the-close availability. Don't put a powerful issue-write PAT in an untrusted Actions PR.
- Use dedicated issue for hash anchors, automatic idempotent posting via least-privilege GitHub App/Actions approved environment; if action lacks permission, record missing anchor and show operator EXACT owner configuration. Never claim posted until URL fetched.
- Missing day: append `MISSED_OBSERVATION` on next possible run with actual timestamp, preserve gap and reason. Do not fill in old decisions as if observed on time.
- Data revision must have prior and new hashes, revision known_at, and no rewrite to original decision.

## 4. Mature-outcome job and integrity alarms

A separate job:
1. Enumerate all decisions with mature horizon and no outcome append.
2. Verify decision hash chain, as-of data cutoff, policy/schema/signatures and controls.
3. Fetch actual subsequent prices within licensed scopes, pass through execution_v2 or equivalent causal replay; bracket daily ambiguity.
4. Append an outcome row linked to frozen original, not modify decision, update aggregate dashboard status and delay counters.
5. If fills underdetermined, outcome range/unknown rather than pretending order execution.
6. Failure leaves the queue pending; repeat run idempotently, no double-counting.

Metrics: sessions expected vs captured, feed age, missing sessions, hash/anchor status, latency, backlog, symbol coverage, no-fills, labels matured and frozen config count. Owner gets private “shadow health” summary. No automated group trade advice or personal account action.

## 5. Test matrix (mandatory)

- Before scheduled start, fixture with no data; no write. Just after close with delayed source; accurately label first actionable session.
- Duplicate workflow dispatch, crash after writing decision but before external anchor, retry and monthly export; one immutable decision, no conflicting hash.
- Old hash modified or record deleted; verification FAIL closed.
- NYSE holiday/half day, DST switch and Friday→Monday; exact maturity date.
- Long Alpaca outage, 429, bad SIP feed or missing key: log MISSED/PENDING, never synthetic fallback into real ledger.
- Monday replay after three missed sessions: none falsely marked originally observed.
- Premature mature outcome request: reject; legitimate matured outcome appends once, fee/stops correct.
- Future V3 changes feature schemas while V2 frozen remains unchanged; old series still verifiable.
- Unauthorized user cannot inspect private restricted data or edit/delete prior records; no key/positions in Actions logs.
- Look-ahead trap injected in source; decision cannot reference t+1 feature.
- Share-class/sector and corporate events cause explicit policy/status.

## 6. Definition of DONE for PR-A

Functional scheduled/workflow_dispatch + CLI code; ≥15 deterministic tests; actual **research-only** smoke job URL; code SHA; producer and verifier utilities; initial immutable daily record or accurate OWNER_SETUP_REQUIRED with exact UI steps; cost budget and missed-run health. Existing frozen selected-arm decision logic matched with golden fixture to protocol-2; separate V3 lane schema ready.

**Do not wait to finish the entire discovery project before installing this recorder.**