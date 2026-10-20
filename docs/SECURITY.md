# APEX security model

This document says what APEX protects, from whom, and how. Each control names where it is enforced and how it is tested. Report suspected issues privately to the repository owner; do not open a public issue.

## Assets

| Asset | Why it matters |
|---|---|
| Members' self-reported positions (fills, sizes, P&L) | Personal financial information |
| Integration credentials (Alpaca, FRED, GitHub token, webhook) | Misuse, quota theft |
| Discord bot token | Impersonation in your server |
| Agent and release HMAC secrets, Pi device keys, model signing key | Forged data or models |
| Signal and model records | Integrity of the research record |

## Trust boundaries

```
Internet ──► Vercel Authentication (outer lock, all deployments)
              └─► Next.js proxy: bypass header only on signed machine routes
                    ├─► owner routes: Discord OAuth session + CSRF checks
                    ├─► agent routes: HMAC (shared secret) + paired device + nonce + clock window
                    └─► release routes: separate release HMAC identity
                          └─► Postgres `apex` schema (not exposed by Supabase Data API), RLS on every table
Raspberry Pi: outbound connections only (Discord gateway, Vercel API, Alpaca, SEC); no listening ports
GitHub Actions: release credentials only in a protected Environment, never available to pull-request code
```

## Controls

| Threat | Control | Where enforced | Tested by |
|---|---|---|---|
| Anyone on the internet reads the dashboard | Vercel Authentication on **all** deployments, plus Discord OAuth with an owner allow-list and server-side revocable sessions (HttpOnly, SameSite=Strict) | Vercel project settings; `src/server/auth/owner.ts` | `scripts/smoke-deployment.mjs`; e2e "unauthenticated visitors" |
| Leaked Vercel bypass secret opens the app | The proxy rejects the bypass header outside `/api/v1/agent/*` and `/api/v1/release/*`; those still require HMAC | `apps/web/src/proxy.ts` | smoke "bypass secret cannot open the dashboard" |
| Cross-site request forgery on owner mutations | Same-origin check + custom `x-apex-csrf` header + SameSite=Strict cookie | `requireOwner` | integration tests; e2e mutations |
| Forged or replayed Pi requests | HMAC-SHA256 over the method, path, timestamp, nonce and body hash; 60 s clock window; single-use nonces; paired and active device required; revocation is immediate | `security/hmac.ts`, `services/agents.ts` | `server.test.ts` "agent request signing" (replay 409, tamper 401, skew 401, unknown 403, revoked 403); shared vectors with Python |
| Someone types a stolen pairing code | Code is single-use, 10-minute TTL and stored hashed; the Pi must sign a proof with its own Ed25519 key; the owner compares fingerprints before approving | `services/pairing.ts`, `apex pair` | `server.test.ts` "pairing"; Python pairing parity vector |
| Credentials leak from the database or logs | AES-256-GCM field encryption, key only in Vercel env, AAD binds provider and field; values never returned, logged or audited (hint only); errors never echo input | `security/fieldCrypto.ts`, `services/settings.ts` | `server.test.ts` "encrypted settings" (no plaintext in DB or audit, corrupt ciphertext fails closed) |
| Credentials leak in transit to the Pi | X25519-HKDF-A256GCM envelope to the device key, 5-minute TTL, version acknowledgement | `security/envelope.ts`, `agent/secrets.py` | shared envelope vectors |
| Secrets shipped to browsers | No `NEXT_PUBLIC_*` secrets (lint rule); client components cannot import server modules (lint rule) | `eslint.config.mjs` | e2e "no secrets … reach the browser bundle"; `scripts/secret-scan.mjs` in CI |
| Member A reads member B's positions | All member-scoped queries run as NOLOGIN role `apex_member` with `apex.actor_user_id`; RLS policies on every position table; dashboard shows other members only as counts; no impersonation feature | migration `20261020000100`, `services/positions.ts` | `server.test.ts` "member B cannot read, list or mutate member A's position" |
| Personal updates posted publicly | DM-only routing enforced at ingestion, DB constraint (no rendered body for DMs), bot refuses personal kinds on channels; DM-blocked updates are held privately | `ingest.ts`, outbox CHECKs, `discord_bot/delivery.py` | bot tests; `server.test.ts` outbox controls |
| Tampered research record | Signals are immutable revisions (hash), event tables are append-only by trigger, audit rows cannot be deleted | migration triggers | `server.test.ts` "event tables are append-only" |
| Malicious or corrupt model | Ed25519-signed manifest (trusted public keys only), SHA-256 and size checks, JSON-only models (never pickle), feature-schema compatibility, atomic swap and rollback on the Pi, frozen promotion gates on the server | `services/models.ts`, `models/manifest.py`, `models/sync.py` | `server.test.ts` "model registry"; Python registry and sync tests |
| CI release credentials stolen by a pull request | Release secrets only in the protected `model-release` Environment on `main`; `permissions: contents: read`; actions pinned to SHAs | `.github/workflows/*` | actionlint; workflow review |
| Pi compromised over the network | No inbound ports; systemd sandboxing (ProtectSystem=strict, NoNewPrivileges, read-only home except state dir); env file mode 600 | `deploy/systemd/*.service.in` | `scripts/apex-doctor.sh` port and permission checks |
| Lost or stolen Pi | Revoke the device in Settings (immediate 403); rotate `AGENT_HMAC_SECRET` (with `_PREVIOUS` overlap); re-pair; the device key never leaves the Pi | dashboard; `agents.ts` | integration tests (revoked device) |
| Formula injection in CSV exports | Cells starting with `= + - @` are prefixed | `explorer.ts`, positions export | code review |
| Clickjacking and content injection | CSP (`default-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'`), X-Frame-Options DENY, nosniff, HSTS, no-referrer | `next.config.ts` | smoke "security headers present" |
| Denial of wallet | No Vercel cron, bounded request bodies, rate-limited tests and dispatch, Actions minutes budget gate, retention | various | cost report |

## Known limitations (accepted for a personal deployment)

- Anyone with physical access to the Pi and its unencrypted SD card can read the Pi's local store. Nightly backups can be age-encrypted.
- The owner can see aggregate member counts but cannot view a member's fills. A deliberate, audited support feature does not exist.
- Free market data is single-venue (IEX) and may lag. APEX labels sources and ages but cannot make the feed authoritative.
- Vercel Authentication cannot be verified from inside the app; run the smoke script from outside after every settings change.

## Secret rotation summary

| Secret | Rotate by |
|---|---|
| `APEX_SESSION_SECRET` | Replace it. All sessions end; sign in again. |
| `APEX_CONFIG_ENCRYPTION_KEY` | Set the new key, bump `_VERSION`, move the old key to `_PREVIOUS`, re-save integrations, remove `_PREVIOUS`. |
| `AGENT_HMAC_SECRET` | Set the new one in Vercel with the old one as `_PREVIOUS`, update the Pi env, restart, then drop `_PREVIOUS`. |
| `APEX_RELEASE_HMAC_SECRET` | Same pattern; update the GitHub Environment secret. |
| Model signing key | Generate a new key version (`apex keygen`), add its public key to `APEX_MODEL_TRUSTED_KEYS` and the Pi, switch the GitHub secret, remove the old public key once no champion uses it. |
| Discord bot token | Reset it in the Developer Portal, update the Pi env, `sudo systemctl restart apex-discord-bot`. |
