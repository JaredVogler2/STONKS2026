# 0007 — Autonomous weekly training on GitHub Actions

**Context.** The 2026-10-20 amendment requires retraining without a laptop.

**Decision.**
- Scheduled workflows (weekly train, weekday drift, manual release) run on GitHub-hosted runners from the default branch only.
- The protected `model-release` Environment holds the release credentials; pull-request code never sees them.
- Candidates are registered through a separate signed release identity.
- The server re-runs the frozen promotion policy. Manual promotion is the default, and auto-promotion requires CALIBRATED status.
- The Pi polls, verifies and swaps.
- A minutes budget gate runs before training. A missed-run detector (8 days) alerts through #system-status.

**Consequences.** Actions minutes are shared account-wide, so the budget is shown and forecast at the maximum possible usage. Without account access the workflows are validated syntactically only, and that is reported as unverified.
