# 0002 — Private schema, RLS and a member role

**Context.** Members' self-reported fills are personal data. Supabase exposes the `public` schema through its Data API by default, so a mis-set policy there could leak rows.

**Decision.**
- All tables live in the `apex` schema, which the Data API does not expose.
- RLS is enabled on every table. A CI test fails if any table lacks it.
- Member-scoped requests run in a transaction with `SET LOCAL ROLE apex_member` and `apex.actor_user_id`; policies filter by that actor.
- The owner dashboard reads other members only as aggregate counts. There is no impersonation feature.
- Event tables are append-only by trigger.

**Alternatives.**
- Supabase Auth with client-side queries: members would need web accounts, and anon keys would ship to browsers.
- App-level filtering only: one missed WHERE clause leaks data.

**Consequences.** The migration must work for a non-superuser owner role. This is verified against local Postgres with the same privilege shape as Supabase.
