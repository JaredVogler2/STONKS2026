# APEX Amendments — Change and Decision Log

## 2026-10-20 — V2 audit and improvement program established

**Status:** DRAFT ON SEPARATE PLANNING BRANCH; no v1 implementation files modified.  
**Scope:** amendments governance, evidence register, critical pre-release review, independent post-build audit, quantitative research, architecture/UX, privacy and data rights, autonomous GitHub+Pi operations, Discord individualized positions, executable QA and copy-ready coding-agent instructions.

**Inputs checked:** PR #1 description and selected source modules at `75679cbc5dfbf51bddc72487648a22e3bcbcd092`. The findings are observations/questions requiring reproduction.

**High-value review candidates:**
- APEX-001 train/serve `squeeze_prev` parity discrepancy.
- APEX-002 uncertainty band semantics for individually fitted forecasts.
- APEX-003 H7 implemented but absent from reported synthetic strategy outcomes.
- APEX-004 exchange calendar vs `BDay` embargo semantics.
- APEX-005 survival-bias/data rights/true price provenance.
- APEX-006 no proven real equity alpha despite healthy synthetic regression controls.
- APEX-007 incomplete v1 operations and no independently verified production deployment.

**Decisions made:** no change to current PR #1, no lowering empirical promotion threshold, retain DEMO/NO TRADE when unvalidated, no unauthorized multi-user trade instructions, no requirement for routine laptop-based retraining.

## Ongoing maintenance template

For each update append:
- date, author/reviewer and current HEAD/PR
- finding ID and evidence/test result
- severity and proposed implementation scope
- owner approval required?
- approved implementation PR SHA and regression command
- actual operational test/production status
- disposition CLOSED/DEFERRED/BLOCKED/OPEN and why.

Do not write “fixed” without code commit and reproducible test evidence.