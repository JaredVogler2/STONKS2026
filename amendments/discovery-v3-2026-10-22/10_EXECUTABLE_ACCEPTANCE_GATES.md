# Discovery V3 — Auditable Implementation and Scientific Acceptance Tests

This file is a **required test specification**, not a claim that these tests already run. Agent must implement regression/property/E2E tests and show exact executed commands, GitHub run, SHA, fixture and result. If inputs unavailable, report NOT_RUN and blocker; do not label tests PASSED.

## Group A — Forward science and immutable recording (P0)

| ID | Test | Required behavior |
|---|---|---|
| FWD01 | Live decision source observed after acceptable close but before next open | Freeze actual observed/decision timestamps, earliest eligible fill, full real source provenance |
| FWD02 | Run delayed until after next day's close | Mark prior session MISSED; no hindsight backfill |
| FWD03 | GitHub scheduler fires on NYSE holiday or weekend | No false trading session/decision |
| FWD04 | Half-day close and DST change | True NYSE calendar cutoff, no 1-hour age drift |
| FWD05 | Duplicate job and transient network failure | Idempotent exactly one event keyed series+session |
| FWD06 | Modify a past decision or reorder a record | Verify chain violation, fail closed |
| FWD07 | First anchor comment fails but decision persisted | Alert ANCHOR_PENDING; retry with exact same digest, never claim posted |
| FWD08 | Mature outcome evaluated before 22 sessions+lag | Reject and leave pending |
| FWD09 | Correct mature outcome including no fill, gap, ambiguous bar | Append once; correct cost and original linkage |
| FWD10 | New V3 experimental feature schema | Frozen S1/S3 series not silently changed |
| FWD11 | API secret absent/unentitled SIP | Real lane fails SOURCE_INVALID, no fixture fallback |
| FWD12 | No current signal qualifies | Immutable NO_TRADE record still exists |
| FWD13 | A future-known catalyst injected into today | Decision/candidate event rejected |
| FWD14 | Current U1 survivor rank retroactively changed | New version/series or blocked, not old record rewritten |
| FWD15 | Restart/cloud outage and catch-up | Honest missed sessions, intact chain, no duplicate outcome |
| FWD16 | Private user data included in shared hash artifact | Artifact/secret/license scanner blocks publication |
| FWD17 | Result page says recorder running | Must have actual recent proof/run/anchor, otherwise SETUP_REQUIRED or STALE |
| FWD18 | Shadow ledger annual retention/offsite restore test | Original hash verification survives restoration and source revision |

## Group B — Portfolio-first economics and execution (P0/P1)

| ID | Test | Required behavior |
|---|---|---|
| ECO01 | 100 competing stocks on one day | ≤100% cash deployed, no leverage, ≤slot, sector and risk caps |
| ECO02 | 2 negatively correlated vs 2 highly correlated securities | covariance-aware risk correctly captures aggregate exposure (when enough data) |
| ECO03 | Benchmark long SPY during 6% user exposure | Compare same-risk exposure and full-SPY separately, not unfair absolute comparison |
| ECO04 | Benchmark dividends/cash yield missing | Explicit price-only and cash-0 limitations or source-correct total returns |
| ECO05 | Overnight stop gap 8% worse than stop | Realized/stressed loss can exceed -1R, no fantasy exact fill |
| ECO06 | Limit only hits daily low before later placement | No guaranteed fill, maintain chronological ambiguity bounds |
| ECO07 | Strategy winners rarely fill | Net per original opportunity includes no-fills and missed benchmark return |
| ECO08 | Dynamic replacement policy trades daily | Charged all entry/exit fees, turnover vs static baseline, no free rotation |
| ECO09 | Cash reallocated on same close as sell/stop | Settle/execution ordering consistent, no phantom capital |
| ECO10 | Candidate model only learns market beta | No unexplained “alpha”; exposure/sector benchmark comparison reports |
| ECO11 | Added future return feature | Perfect foresight trap triggers failure/invalid research |
| ECO12 | Utility objective coefficients changed post-test | Research protocol difference/contamination logged, never same untouched trial |
| ECO13 | Sophisticated optimizer worse than greedy baseline | Rejected, even if it has more apparently accurate predictions |
| ECO14 | Liquidity/fractional/short eligibility unavailable | Research-only or fail closed; no broker certainty invented |
| ECO15 | Crash regime / risk-off, all candidates negative | NO TRADE or allowed alternative; negative expected-return not forced into top slot |
| ECO16 | 10 slots filled, new strong candidate | Portfolio replacement decision evaluates cost and opportunity, not retrospective winner |

## Group C — Hypothesis search and scientific integrity

| ID | Test | Required behavior |
|---|---|---|
| SCI01 | Generate 100 random parameter variants, report best 3 | Registry preserves all 100, confidence adjusted/not valid |
| SCI02 | Same data held as “unseen” but researcher read previous r2 | Flag data as contaminated for new ideas |
| SCI03 | Hold out different symbols in same dates | Robustness, NOT prospective unseen time |
| SCI04 | Null correlated simulated markets with no edge | Properly bounded type-I error; false-positive estimate + CI reported |
| SCI05 | Planted true edge vs shifted-label negative control | Power reasonable under design; label-shuffle collapses edge |
| SCI06 | Stop/target touched intraday after limit or before | Correct time-ordered result or honest bounds |
| SCI07 | Market-wide earnings day 80 stocks | Independent cluster inference not n=80 independent |
| SCI08 | Stop test repeated weekly until passing | Sequential peeks logged/corrected or invalid for confirmation |
| SCI09 | New Graph F3 uses future company link | PIT feature test rejects; cannot call it validated |
| SCI10 | Rejected agent hypothesis not committed | Attempt registry remains complete or batch invalid |
| SCI11 | Model high Brier skill but negative net return | No live strategy endorsement or false economic value |
| SCI12 | Net positive DEV on survivor-only universe | EXPLORATORY, not validated all-market alpha |
| SCI13 | Strong result at 0 costs dies at 25/50 bps | No robust trade release |
| SCI14 | Positive S1 in bull regime but loss in risk-off | Explicit regime limitation/abstention sensitivity, no broad repeatability claim |
| SCI15 | Hypothesis uses data requiring paid license | Owner approval flag; research SOURCE_BLOCKED rather than unauthorized fetch |
| SCI16 | New registered future series changes after outcome | Series fork or violation, not overwrite |

## Group D — Product, security and cloud

| ID | Test | Required behavior |
|---|---|---|
| OPS01 | GitHub job budget reached / other repos consume minutes | Refuse optional discovery jobs, never auto-pay |
| OPS02 | Trusted/unknown PR tries to run secret-bearing workflow | No secret available, pinned least permissions |
| OPS03 | Model schema/hash/signature bad | Pi rejects safely and preserves valid champion or NO HIGH CONFIDENCE |
| OPS04 | Supabase/Alpaca outage | Durable pending state, accurate stale/missed warnings and recovery |
| OPS05 | Discord blocked DM | No group data leak or misleading delivery success |
| OPS06 | Unauthorized member queries friend's private fill | RLS and app route deny; audit sanitized |
| OPS07 | Owner dashboard experimental rank "95" | Never labeled 95% probability of profit |
| OPS08 | Browser 390px and desktop full workflows | Functional controls, real persisted outcomes and accessible UI |
| OPS09 | Attempt GROUP_ACTIONABLE without legal/data rights approval | Cannot enable regardless of admin toggle |
| OPS10 | Research output contains vendor OHLCV/raw copyrighted news or secret | Block commit/artifact |
| OPS11 | Hosting has not been deployed/paired | Clearly SETUP_REQUIRED rather than green production |
| OPS12 | Run synthetic fixture | DEMO label and separate folder, never a real-equity alpha report |

## Measured gates, not marketing

**Gate G0 — Known science:** current baseline report and CI verified; active code SHA/manifest documented.
**G1 — Forward capture:** actual event recorded before a future outcome and independently anchored, or honestly OWNER_SETUP_REQUIRED; passes FWD01–18 on fixtures.
**G2 — Discovery infrastructure:** typed registry, bounded real DEV runner, all attempts logged, objective/fair baselines and ECO invariants; initial hypothesized strategy may be negative.
**G3 — Controlled hypothesis pilot:** 3–5 mechanistically distinct ideas registered, negative controls, valid real-data DEV results with all failed and ambiguous outcomes, adequate cost/regime analysis; no claim of validation.
**G4 — Prospective research:** series and outcome separation, no look-ahead and ongoing observations; statistically insufficient until a genuinely powered independent observation window.
**G5 — Model/research utility:** demonstration of net economic superiority to matched SPY/cash and other alternatives under prespecified credible uncertainty **on genuinely independent evidence**; complexity penalties matter.
**G6 — Human review / operational compliance:** market data rights, cloud and private group advisory obligations, secure model release and owner permissions. G5 alone never enables actionable group trade messages.

## Artifact acceptance

- Each implementation PR has current main SHA, finding IDs, before/after tests, sample fixtures, actual real vs fixture source, run URL and G gate status.
- Each experiment has non-reused ID, immutable manifest, attempted variant ledger, real data source hash/revisions, net portfolio results, uncertainty, negative controls, honest disposition.
- A novel strategy without proven benefit is still a completed research experiment when falsification was executed correctly.
- NO TRADE is **expected** until valid independent future evidence.

**Do not turn the ability to generate plausible hypotheses into a claim that APEX has discovered a superior profitable strategy.**