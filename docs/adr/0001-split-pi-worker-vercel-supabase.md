# 0001 — Pi worker, Vercel API, Supabase record

**Context.** The platform needs a 24/7 Discord gateway connection, a quote stream (Alpaca IEX websocket) and scheduled scans, at no recurring cost. Vercel Hobby functions are short-lived, and Hobby cron runs at most daily.

**Decision.**
- The Raspberry Pi 5 runs everything long-running: the scanner, the stream, assessments, the bot and model sync.
- Vercel hosts the owner dashboard and short, signed ingestion/admin APIs.
- Supabase Postgres is the durable record.
- The Pi keeps a local SQLite spool and outbox so it keeps working when the cloud is unreachable.

**Alternatives.**
- Serverless scanning on Vercel cron: not possible on Hobby at the needed cadence.
- A VPS: recurring cost.
- Pi-only with no cloud: no private dashboard, no off-device record.

**Consequences.** Two runtimes (Python, TypeScript) share contracts (ADR 0004). The Pi must be resilient to offline periods, which is why it has a spool, idempotent ids and replay.
