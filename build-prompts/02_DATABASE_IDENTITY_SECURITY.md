# PHASE 02 — Durable database, authorized identity, encrypted settings, private access

## Mission

Turn the demo slice into a trustworthy multi-user data system. Build all schema migrations from ARCHITECTURE_AND_CONTRACTS, scoped identities, authenticated Pi agent API, admin-only Vercel dashboard and fully functional secure Settings page. Privacy and secret protection are release blockers.

## Database and migration requirements

1. Build migration files from zero and a `schema.sql` snapshot; use UUID primary keys except natural uniqueness; use `timestamptz`, CHECK constraints, FK ON DELETE policies appropriate to audit history and account deletion.
2. Canonical durable event tables (`position_events`, `signal_events`, `notification_outbox`) immutable except transition metadata; projection/summary tables are re-creatable from event streams.
3. Enforce unique deterministic signal IDs and `(user_id,client_idempotency_key)` for fills; position lots ledger supports corrections, not rewriting fill history.
4. Index observed-time symbol keys; avoid storing every IEX tick in 500MB Supabase; retention/rollup jobs and quota telemetry.
5. Enable RLS on each applicable table; default deny direct anon/auth queries to privileged internals. Pi always uses server agent API; never Supabase service-role key.
6. Seed only fake/demo records, never actual user account values or real market signals under current timestamp.
7. Include migrations and rollback/rebuild/backup runbook; migration dry run and clean-db rebuild test. Supabase Free has no automatic full managed backup promise.

## Admin and member identity

- Vercel Authentication = first perimeter for protected web deployments; configure **All Deployments** including production. Confirm it is enabled in Vercel project config; merely setting env vars does not enable it.
- Web application MUST still have its own authenticated admin authorization checking trusted identity; deployment access does not grant unrestricted API administrative privileges. Use explicit allowlist / owner identity mapped securely from verifiable claims or admin-auth provider. No custom spoofable `X-User-Email` client headers.
- Discord members are identified from authenticated Discord Gateway events, matched to a configured guild and internal app user ID; verify membership/roles on initial use and revalidate/revoke when removed. No arbitrary user_id accepted from browser.
- Bootstrapped owner ID configured once; no anonymous enrollment as admin.
- Member consent/preferences: disclosures, notifications, opt-outs, personal-data export/delete and admin account deactivation.

## Secret management — functional app settings

The admin UI must have integration cards with statuses: Not Configured, Test Failed, Connected, Rate Limited, Stale, Expired, Disabled. For Alpaca (key ID + secret), Discord (bot token status, guild/channel IDs), SEC contact identity/User-Agent, macro/news options and Pi pairing:
- Server-side form action validates format and consent, encrypts each secret with a server-held AES-256-GCM key and fresh random IV; store ciphertext + key_version + authenticated metadata.
- A saved secret should show provider, created/tested date and last few safe digits only if provider-specific, NEVER reveal full value in GET, JS payload, toast, logs or exception.
- Add Save, Test Connection, Rotate, Disable and Delete; audit changes without value leakage.
- Store master key only in Vercel runtime env/bootstrap, and verify key size on startup. App Settings NEVER edits the bootstrap key in normal client UI.
- For Pi needing market-data credentials, implement paired ephemeral server transfer encrypted to Pi's authenticated public key (using well-reviewed library) OR documented manual secret provision while status accurately says "Manual Pi provisioning required." Prefer secure automated pairing with one-time code, expiration and signed HMAC envelope; NEVER transmit plaintext into a database queue or notification.
- Encrypt at rest on Pi where practical, filesystem mode 0600, different user from local OS admin if possible, never print secrets.
- Provide rotation that can tolerate overlap and report whether Pi has acknowledged new secret version.

## Pi agent authentication

Implement HMAC canonical request signing per architecture file, timestamp window, anti-replay nonces, bounded request body, device revocation, constant-time compare and audit. All agent routes authenticated even under Vercel Deployment Protection automation bypass. Do not use a plain shared querystring secret as sole authorization. Test 401 for wrong/missing signature, 403 revoked device, 409 duplicate nonce and safe repeated event idempotency.

If market data connection is unavailable, use offline fixture mode. Absence of secrets MUST NOT block ability to run automated tests/build.

## Threat model and test cases

Document adversary: public internet visitor, unauthorized Vercel project viewer, revoked Discord member, malicious guild member, lost Pi SD card, stolen webhook, replayed agent request, leaked service key, forged price payload, GitHub Actions untrusted PR. Explicit mitigations and residual risks.

Test:
- malicious client requests another member's position ID: 403/404 with no data.
- forged Discord member ID in API body: rejected.
- stale HMAC and replayed nonce rejected.
- changing client-clock alone cannot override signature check.
- database unauthorized SQL selects rejected.
- invalid/corrupt ciphertext fails closed.
- secret never appears in rendered HTML, network JSON, production build, log messages or sent Discord embeds.
- backup restore preserves permission restrictions and excludes keys from git.
- seed fixtures pass end-to-end permission test in CI.

## Acceptance

Output real SQL, migrations, app Settings screens and working tests. Provide exact owner bootstrap procedure without exposing credentials. Document any host restriction preventing guaranteed Vercel production privacy.