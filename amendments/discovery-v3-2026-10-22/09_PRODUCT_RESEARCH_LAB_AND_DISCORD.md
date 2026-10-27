# Product V3 — APEX Research Lab, Evidence Visualization, Owner Decisions and Private Discord

## Why a redesigned research view matters

A sophisticated research engine that publishes a confident-sounding score without credible net performance is counterproductive. The owner should see **why a policy might outperform**, what assumptions matter, what failed, what is still untested, what observable future evidence would change the verdict, and why NO TRADE is often the correct economic decision.

Do not repaint the web application at the expense of starting the forward recorder. Build the front-end only after types, persisted data and research contracts exist; prefer changes to existing Next.js/TypeScript pages/routes over a parallel app.

## 1. Three independent axes on the home page

**System operations:** Pi/bot connected, last completed scan, last licensed quote seen, provider coverage, data health, model version expiry, GitHub training job and daily shadow record status.

**Scientific evidence:** strategy/portfolio policy family, version and source SHA, research evidence tier (IDEA/DEV/PROSPECTIVE/INDEPENDENT), number of independent decision blocks, window/regime coverage, finality, return comparison, costs and interval, exposed historical data caveat, null-control outcome and relevant license limitations. A “95/100 Quality” is a descriptive rule ranking, not a 95% chance of a profitable trade.

**Legal/authorization:** DEMO, PAPER_LIVE_DATA, PERSONAL_RESEARCH, GROUP_ACTIONABLE separately. Group personalized action disabled until reviewed; model validation does not override Alpaca redistribution or investment-adviser concerns. Do not let admin bypass with one toggle.

## 2. Research Lab navigation

### Experiments
Searchable, owner-only table/list of *all* attempts, not only winners: trial_id, mechanism family (F1–F10), title, registered date and parent, code version, dataset/source as-of, status, test mode, last run, net active portfolio result and uncertainty, drawdown/exposure/cost, evidence tier, negative controls, short “why rejected.” Failed attempts visible and filterable. Deep link to GitHub PR/run and SHA; source secrets and raw vendor history absent.

### Economic frontier
Interactive chart of real DATA-backed portfolio policy candidates (only after results), with axes selectable between net growth, drawdown, expected shortfall, capital deployment and turnover; hover shows regime, benchmark, source and statistical maturity. DEMO fixture views explicitly labeled. Points must represent comparable cash/market assumptions and same sample. Never rank top CAGR without risk and inference. Default a clean table/text summary for mobile accessibility.

### Opportunity diagnostics
Causal chain `event/feature→eligible universe→strategy candidate→execution feasibility→cost/expected net marginal utility→risk/capital constraint→decision/NO TRADE`. Display stock selection vs entry/exit vs allocation opportunity contribution (interaction term where necessary), expected holding time, available matched alternative and degree of uncertainty. A rejected candidate can be a useful research observation; don't hide it.

### Forward Shadow
Daily calendar of expected trading sessions and actual captured observations. Show source actually known at decision time, exact run and independent hash anchor, finalized/late/missed indicator, decision chain head, maturity countdown, pending outcomes, version changes/series boundary and first valid confirmatory date. Never show backfilled missing day as on-time. Separate frozen S1/S3 lane from new V3 exploratory ideas with different badges **as status metadata, not colorful promotional boxes**.

### Hypothesis Builder (owner-side development)
Structured idea form or agent-generated proposal preview with:
- causal/economic mechanism;
- data required and whether licensed;
- competing null and negative control;
- chosen benchmark and cost assumptions;
- assumed account instrument constraints and horizon;
- expected experiments/compute budget;
- whether prior data contaminated;
- reviewer approval before dispatch.
Click “register hypothesis” only if it persists immutable manifest and budget. “Execute” dispatch only through authenticated backend with trusted allowed workflow and limited inputs. No browser GitHub token or arbitrary shell. If backend not configured, display disabled state/why.

## 3. Discord: researched opportunity without pretending executable advice

A group research message if licensed contains:
`data source/age, symbol, mechanism title/version, evidence tier, reasons for considering, falsifying evidence, holding-horizon assumption, costs/spread uncertainty, risk profile and link to research detail`. Experimental underpowered candidate labeled EXPERIMENTAL, no “high confidence win probability.” By default unvalidated hypotheses publish no trade calls; allowed group educational research status only. User-personalized ADD/SELL actions not enabled without external review.

Private member portfolio:
- Existing “Record My Entry” ephemeral interaction with true Discord actor, database RLS, validated signal ID and self-reported fill. Never embed quantity, price or P&L in shared message.
- Personal risk derives from individual fill and actual position/account constraints; a signal model calibrated for the original hypothetical entry is not automatically valid for late entry. “Touched stop” is not “broker sold.”
- Member consent and opt-out, blocked DMs pending private queue, revocation on server departure and partial exit idempotency.
- Research model promotion cannot rewrite original member transactions or signal probabilities retroactively.

## 4. Design and interaction requirements

Clean responsive layout 390px iPhone and desktop 1440px; accessible labels, focus and keyboard, relative and absolute UTC/ET times, clear source/source-lag, no interactive inert controls. Long statistical table summary should be understandable without PhD (“p=0.23 after correcting for trials; this does not establish repeatability”). Historical losses and benchmark underperformance displayed as prominently as exploratory gains.

Charts may show real derived *aggregated* equity/return curves only if license permits; no raw redistributed Alpaca prices. Never create simulated “current winners” masquerading as actual live quotes.

## 5. Acceptance scenarios

1. Fresh new owner without credentials: DEMO only, with plain no-real-alpha state.
2. Valid real source and 0 validated strategies: **NO TRADE** and trial registry accessible; no buy alerts.
3. Novel F3 hypothesis with no historical graph provenance: status SOURCE_BLOCKED; “execute” disabled, no synthetic fill.
4. Repeat same failed experiment: returns same immutable ID/artifact or explicit new source revision, not two independent discoveries.
5. A prospective day was missed: marked MISSED, not a retroactive success; chain validated.
6. S1 frozen vs V3 exploration side-by-side without promoting exploratory as confirmatory.
7. Experimental risk optimizer outperforms dev benchmark only: DEV status, no high confidence or trade call.
8. Member A/B same signal but different fills: independent private records and risk; neither exposed publicly.
9. Model expired / source stale: signal suppressed, operations state not falsely green.
10. All controls persist and return proper server acknowledgment; iPhone and desktop screenshot/browser tests pass.

**Deliverable:** updated web contracts/routes/components E2E with real stored demo/actual research run metadata, operator health, privacy tests and no new paid dependency.