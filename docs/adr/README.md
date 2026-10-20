# Architecture decision records

Short records of decisions that shape APEX, with the alternatives considered. Status: all **Accepted** (2026-10-20).

| # | Decision |
|---|---|
| [0001](0001-split-pi-worker-vercel-supabase.md) | Long-running work on the Pi; Vercel only serves short signed requests; Supabase is the system of record |
| [0002](0002-private-schema-and-rls.md) | A private `apex` schema with RLS on every table and a NOLOGIN member role, instead of Supabase client auth |
| [0003](0003-signed-agent-protocol.md) | HMAC request signing plus Ed25519/X25519 device keys for the Pi; no inbound ports |
| [0004](0004-contracts-json-schema.md) | JSON Schema as the single source of truth for every cross-language payload |
| [0005](0005-quality-is-not-probability.md) | Quality is a ranking; probabilities only from a calibrated, unexpired champion |
| [0006](0006-signed-json-models.md) | Models are signed JSON coefficients and calibrators; never pickle |
| [0007](0007-cloud-training-on-actions.md) | Weekly retraining on GitHub-hosted runners with a frozen promotion policy (autonomous training amendment) |
| [0008](0008-demo-replays-production-code.md) | DEMO is a deterministic replay through the same ingestion services, on embedded Postgres |
| [0009](0009-outbox-and-private-delivery.md) | Transactional outbox; personal updates are DMs only and are held privately when DMs are blocked |
