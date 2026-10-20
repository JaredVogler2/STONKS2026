# STONKS2026 — APEX Swing-Trading Intelligence

Private research and alert platform: liquid U.S. equities, 5–20 trading-session opportunity windows, empirical probability calibration, zero-required-subscription architecture, protected Vercel management dashboard, Raspberry Pi 5 continuous scanner, private Discord group alerts and independently monitored user-reported entry positions.

## For the coding agent — start here

1. **[Copy-ready coding agent launch prompt](build-prompts/START_HERE_COPY_TO_AGENT.md)**
2. **[Complete ordered 14-phase specification](build-prompts/README.md)**
3. **[Master permanent agent directive](build-prompts/00_MASTER_AGENT_DIRECTIVE.md)**
4. **[Architecture, schema and API contracts](build-prompts/ARCHITECTURE_AND_CONTRACTS.md)**
5. **[Source licensing, verified free limits and cost register](build-prompts/SOURCE_AND_COST_REGISTER.md)**

Follow Phase 01 through Phase 13 in sequence. The code agent must implement code, tests and documentation and must report what has actually been deployed/verified. A mock dashboard does NOT count as a finished application.

## For the person building the hardware

**[Beginner Raspberry Pi hardware, Amazon shopping and Discord starter guide](raspberry-pi/README.md)**

This folder already contains a standalone first-stage Discord bot, instructions to connect a Pi without a monitor, and operating/troubleshooting documentation. The coding agent must inspect and preserve/migrate this starter instead of overwriting it blindly.

## Important safety and operating constraints

- No automated brokerage execution, no Robinhood credential scraping.
- Market-data licensing and group/personalized securities-advice requirements must be reviewed prior to sending actionable personalized recommendations to a group.
- The free Alpaca feed is live *IEX-only* for priority symbols; consolidated data is not fully real time on the Basic tier.
- Vercel Hobby eligibility applies to personal noncommercial usage, and Hobby cron is not a continuous scanner.
- Investment probability estimates require actual point-in-time backtesting and out-of-sample calibration; do not label ranking scores as historical confidence.
- Secret keys must never be placed in GitHub, frontend bundles or public Discord messages.

**Status:** The detailed build prompts and Raspberry Pi starter are in the repository. The full APEX application is **specified but not yet implemented/deployed**.