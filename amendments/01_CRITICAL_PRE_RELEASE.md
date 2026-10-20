# Amendment 01 — Critical Pre-release Release Gates and Focused Repairs

**When to execute:** Before live high-confidence alerting; immediate coordination only for reproducible P0 risks. PR #1 should continue uninterrupted unless a release blocker needs intervention.

## Why these gates are necessary

A beautifully styled trading dashboard with incorrect data age, falsely precise probabilities, training/live feature differences, data leakage or exposed positions is a dangerous product. The correct goal is an auditable system that can say NO TRADE without bluffing predictive ability.

## P0A — Train/live feature-vector parity

Review APEX-001. Implement an immutable typed `FeatureVector` contract (units, as-of UTC, market source, provenance, calculation version, feature ordering, missing-value policy). Use shared construction for live and research or exact cross-language tests. Specifically replace silent `squeeze_prev=None` path with observed prior-session value when available. If unknown, reject confidence or demonstrate training included equivalent missingness. Sign model manifest with feature schema; fail closed if incompatible.

Test at least 30 scenarios: squeeze, no squeeze, incomplete 20-day window, split, holiday, catalyst before/after disclosure, stale quote, varying source feed. Compare model probabilities for same as-of observation across research/inference to numerical tolerance. No signal on incompatible vector.

## P0B — Probability claims and confidence interval truth

Review APEX-002. Distinguish Quality/100 ranking, observed empirical target-before-stop event probability within horizon, model calibration, and uncertainty of estimated conditional probability. The existing `sqrt(p(1-p)/n)` band is not defensible as a pointwise fitted-model interval by default. Remove its interval label or implement time/sector-block bootstrap with refit and coverage on truly held-out data. Report dependence-adjusted effective sample size and prior calibration drift. Never convert a model score to win rate.

Acceptance: if uncertainty cannot be established, use `Calibrated estimate (interval not established)` or `UNVALIDATED`; Discord and dashboard use one shared schema.

## P0C — Causality, look-ahead, holidays and source timing

Audit exact candidate t, market bar formation, next executable entry, earnings/news publication, SEC `filed_at` vs transaction, politician disclosure, split factors, synthetic fixture carry-forward, latest feed restriction, model scaler/calibrator fit scope and final holdout. Replace `BDay` purge with exchange session indexing and exact label-window overlap tests, including early closes. Test at least 6 boundary dates. Make data leaks CI-blocking.

Acceptance: deliberate look-ahead fixture fails, holdout never used to choose threshold, zero future filings in old signal snapshots.

## P0D — User confidentiality and service authentication

Review every Next.js route, SQL RLS policy, Pi HMAC request, Discord command/button/modal, owner session, queue and audit endpoint. Confirm users can only access own lots; no public channel fills/P&L. Protected Vercel production and previews require both perimeter login and application-level authorization; Pi machine API must validate request signature after any approved deployment bypass. All keys server-only and encrypted; revocation and replay protection tested. No raw logging of tokens or note text.

Acceptance: hostile A→B attempts denied; stolen component ID invalid; revoked member blocked; signed model mismatch refuses load; unprotected routes fail integration checks.

## P0E — Market data realism and compliance

Verify actual IEX vs SIP source and bar maturity; no 15-minute-delayed data mislabeled LIVE. Guard invalid/outlier/isolated prints and quote gaps; return DATA UNCERTAIN when feeds stale. Confirm Robinhood eligibility state is inferred unless manually verified; do not claim brokerage API connectivity. Do not multicast protected price feeds or personalized buy/sell guidance without legal/data-contract review. DEMO never produces real-looking current values.

Acceptance: bad print, halt, quote-only, gap and unknown tradability are flagged, not actionable.

## P0F — Autonomous model lifecycle

GitHub-hosted training, source hashes, frozen independent acceptance gates, Ed25519/other verified signature, private non-executable artifact, Pi fetch/verify/stage/atomic activate, rollback, missed-job detector, quota pressure handling and readable dashboard status. No laptop needed after initial setup. Do not relax tests to force promotion.

Acceptance: simulated failed model stays on champion; bad signature rejected; Pi offline catches up safely, model expires => suppress “high conviction”.

## Deliverables for each finding

1. Root-cause narrative with actual code pointers and source SHA.
2. Failing minimal reproducible test BEFORE change.
3. Narrow patch on separate branch or coordinated PR #1 patch.
4. Updated schema/manifest/version if needed.
5. Test logs before and after; operational rollback.
6. Correct severity and status in `docs/PRE_RELEASE_AUDIT.md` and this folder.
7. Clear blocked/unblocked release decision.

**Never interpret a lack of profitable signals as a bug to be fixed by weakening research rigor.**