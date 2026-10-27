# Claude Code Master Mission — APEX Autonomous Discovery V3

## Role and definition of success

Act as a joint quantitative-research lead, adversarial software architect, operations-research optimizer, security reviewer and coding agent. You are authorized to explore **any lawful, scientifically credible and executable strategy idea** rather than staying inside H1–H8/S1–S5. The owner's goal is repeatable **portfolio-level net economic performance** with constrained risk, predominantly 1–4-week swings but flexible horizons when improved utility/robustness warrants. Realize that no algorithm can guarantee best future returns; implement a disciplined search and falsification process that can discover candidates without overfitting.

**Read entire folder. Work from latest main. Do not modify legacy research records.** Review existing source/test/docs, verify the baseline, write a short execution plan and then IMPLEMENT phase by phase. Do not create dozens of new strategies first.

## Exact build order (milestones)

**M0 — Baseline & release-provenance review (same first code PR as M1 if feasible):** Inspect main SHA, PRs #15/#16, full report and code, current workflows, budget, source/data provider rights. Independently reproduce one deterministic original book/strategy case and statistical case. Record known limitations. If prior model-release pipeline is non-operational, do not let it block isolated research.

**M1 — Ship production-quality RESEARCH-ONLY forward shadow NOW:** Existing `docs/quant-v2/FORWARD_SHADOW_PLAN.md` is unimplemented. Begin no earlier than first lawful post-install completed NYSE session (registered forward target 2026-10-23 if implementation is live by then), never fake missed historical decisions. Record S1 E0X1 and S3 C_X0 exactly as frozen, plus market-relative/hedged observation legs without redefining selected baseline. Make idempotent scheduled GitHub Actions or correctly scoped Pi daily job, hash-chained immutable decision/outcome split, externally timestamped digest, catch-up detection, owner health, zero-cost no-laptop. DO NOT tie this critical path to completion of novel discovery. For days where complete decision data unavailable by deadline, log MISSED, not backfill. Full contract in `02`.

**M2 — Research engine skeleton:** Add a separate `discovery/v3`-style package (locate existing patterns first), typed `HypothesisSpec`/`DataProvenance`/`CandidateDecision`/`PolicySpec`/`StudyManifest`/`ResearchResult` and auditable registry. Reuse source adapters, NYSE calendar, validated book simulator, feature panels, event/levels, trade execution and inference. Novel research experiments under separate namespace, no altering frozen V2 arms. Implement three deliberately basic reference agents (SPY/cash, simple momentum, zero-turnover/no trade); property-test budgets and causality. Separate stock selection/entry/exit/portfolio scheduling as orthogonal axes.

**M3 — Portfolio utility and execution accuracy:** Evaluate total-return daily book with bounded capital and realistic fill, cost and alternative opportunity. Distinguish strategy may beat random selections while still lose to SPY/cash. Implement long-only constrained optimizer or deterministic rule selector on discrete candidates, with locked entry/exit, max positions, sector, gross/net exposure, turnover, serial cash accounting. Add market-relative/hedged PAPER research leg using finance-constrained hedge assumptions; actual shorting/margin borrow is not assumed available. Return Pareto/risk-adjusted frontier and credible decision NO_TRADE. `05`.

**M4 — Broad hypothesis laboratory:** Research candidate families with *economic or behavioral mechanism*, including underexplored cross-sectional dispersion, lead-lag graph propagation, information diffusion, post-event reaction, volatility state, conditional exits, opportunity-duration optimization, attention/liquidity pressure and cost-aware ranking. Implement a small budgeted pilot with mechanistically distinct families after dev registration; no random endless indicator grid. Recycle R/S baselines and discarded evidence. Novel models allowed if validated against simple baselines, negative controls and transaction costs. `04`.

**M5 — Independent research methodology:** Build hypothesis search ledger with all attempts and dev folds, block-dependent testing, pseudo-studies of overfit, false-discovery corrections and honest small-cluster uncertainty. New ideas cannot treat now-exposed 2017–2019 presample or V2 2025–2026 as untouched. Historical testing can rank development hypotheses only; prospective shadow/independent licensed PIT cohort required for confirmation. Objective not to cherry-pick something with apparent >5% monthly returns. `07`.

**M6 — Data expansion & causal validation:** Investigate realistic freely permitted PIT sources, inactive/delisted identifiers, versioned industry metadata, macro/issuer events with original public availability, microstructure, spreads and dividends. Implement only data access with verifiable timestamps and provider terms. If source free unavailable, keep transparent blocked status and research price-only ideas. Do not silently rely on today's survivor cohort to make new edge claims. `06`.

**M7 — UX, orchestration and forward research reporting:** Add owner research dashboard showing experiment registry/failed trials/net economic distribution/uncertainty/Pareto and expected next information gain. Discord group remains informational/research allowed only; private member fills protected. Signed Pi fetch and repo-hosted cloud model release unchanged until proven. CI smoke, workflow dispatch and budget report in PR. `08/09`.

## Bounded implementation and milestones

- Deliver initial focused **PR-A: prospective shadow runner + tests + operational setup** before or independent of strategy exploration. If cannot activate real daily run due missing permissions or secrets, complete integration tests and say `OWNER_SETUP_REQUIRED` with exact 1-time setting and observed pending state. Do not call it “running”.
- **PR-B:** hypothesis registry / typed research pipeline / basic comparator and portfolio utility, no alpha claims.
- **PR-C:** first preregistered discovery pilot with 3–5 distinct mechanism families, true provider data DEV run and complete attempted-trials manifest. Research-only, not promoted.
- **PR-D:** improved data provenance/microstructure tests/UX, where supported and scientifically needed.
- Keep PR small, reviewable, rollbackable. Never merge an unrelated active PR, force-push or alter third-party secrets.
- The research shadow for original frozen arms must not pick up a different branch's experimental feature evolution. Pin and report code/protocol versions; incompatible changes start a new series.
- Account-wide GitHub Actions minutes compete with other projects; before scheduling each experiment measure and bound minutes/API requests. Github-hosted standard runner only; Pi is always-on scanning, not a training cluster.

## Always report

At each phase return: current main SHA; issue/milestone; code/test scope and PR; exact commands/test pass counts and real CI links; actual cloud job/health and data scope; scientific status; budget impact; blockers; next prioritized step. Never describe a code stub as deployed or a backtest average as forward-validated alpha.

## Release and safety barriers

Private Discord group “what should YOU sell/buy” may require regulatory review and provider group-display license; app must not infer clearance. Never place live broker orders or ingest Robinhood passwords. No mandatory paid tiers. All strategy and model changes research-only and default disabled for action until independent evidence plus operations/legal approvals.