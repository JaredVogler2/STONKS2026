# 0008 — DEMO replays production code

**Decision.**
- DEMO mode uses a deterministic synthetic market with fictional `DM*` tickers and planted, observable setups.
- The Pi side exports a timeline of agent API calls (`apex export-web-seed`).
- The dashboard replays them through the same ingestion services the real Pi uses, on embedded Postgres (PGlite).
- DEMO data is labelled everywhere. A live server rejects DEMO-flagged payloads, and DEMO previews have no database URL.

**Consequences.** The DEMO exercises real code paths: RLS, the outbox, lifecycle rules and the registry. PGlite and the production driver differed in one important way: postgres.js re-encoded JSON text parameters. That bug was found in Phase 12 and fixed, and the integration suites now also run against real Postgres.
