# APEX operator runbook

This is the one-time setup and the occasional maintenance for a private APEX deployment. Day to day, nothing needs your laptop. The Raspberry Pi scans and runs the Discord bot. Vercel serves your dashboard. GitHub Actions retrains models every week (see [AUTONOMOUS_TRAINING.md](AUTONOMOUS_TRAINING.md)).

Every part runs on a free plan sized for personal use. The figures are in [COST_AND_QUOTAS.md](COST_AND_QUOTAS.md); check the current provider limits before relying on them.

```
Discord members ──(slash commands, buttons)──► Discord ◄──(gateway, outbound only)── Raspberry Pi 5
                                                                                        │  signed HTTPS (outbound only)
You (owner) ──(Vercel Authentication + Discord sign-in)──► Vercel: Next.js dashboard + API ──► Supabase Postgres (private schema)
                                                                                        ▲
GitHub Actions (weekly training, drift) ──(signed release identity)─────────────────────┘
```

The Pi never opens a port. Members never get a dashboard account. Only you can see the dashboard. Each member's positions are private to them: the database enforces this with row-level security, so it does not depend on the UI.

---

## 0. Try it locally first (no accounts, no keys)

```bash
corepack enable && pnpm install
pnpm --filter @apex/web demo         # http://localhost:3000 -> "Enter local DEMO"
cd apps/pi-worker && uv sync && uv run apex demo   # the same story on the Pi side
```

DEMO uses a deterministic synthetic market with fictional `DM*` tickers. Everything in DEMO is labelled.

---

## 1. Supabase (database)

1. Create a **free** project at https://supabase.com. Choose a strong database password and keep it in your password manager.
2. **Project Settings → Data API**: leave *Exposed schemas* as the default (`public`, `graphql_public`).
   - APEX keeps everything in the `apex` schema, which the Data API does not expose.
   - Do not add `apex` to this list.
3. **Connect → Transaction pooler**: copy the URI (port 6543). This is your `DATABASE_URL` for Vercel.
4. Apply the schema from your computer. This is the only time you need it.
   ```bash
   DATABASE_URL='postgresql://…:6543/postgres' pnpm --filter @apex/web db:migrate
   ```
   - The command prints applied and pending versions.
   - Checksums protect against edited migrations.
5. Leave Row-Level Security on. Every `apex` table has RLS enabled, and the integration tests check this.

**Size guard rails.** The dashboard turns *Database* amber at 350 MB and red at 450 MB, against a 500 MB free limit. **Operations → Run retention now** prunes:

| Data | Kept for |
|---|---|
| Quote events | 14 days |
| Heartbeats | 7 days |
| Delivery attempts | 30 days |
| Feature snapshots | 365 days |

**Inactivity pause.** A free project can pause when it is unused.
- The dashboard then shows the error page with a recovery hint.
- The Pi keeps working from its local spool and catches up after you press **Restore project** in Supabase.
- APEX does not send fake "keep-alive" traffic.

## 2. Discord application (bot + owner sign-in)

Follow [raspberry-pi/04_DISCORD_CONFIGURATION.md](../raspberry-pi/04_DISCORD_CONFIGURATION.md) for the bot. Then add OAuth for the dashboard:

1. Developer Portal → your application → **OAuth2**.
   - Add the redirect `https://<your-domain>/api/auth/discord/callback`.
   - Copy the **Client ID** and **Client Secret**.
2. Your Discord user id (Developer Mode → right-click yourself → *Copy User ID*) becomes `APEX_OWNER_DISCORD_IDS`.

Only listed ids can sign in. Members never sign in to the dashboard.

## 3. Vercel (dashboard + API)

1. **Add New → Project → Import** this GitHub repository.
   - Root directory: `apps/web`.
   - Framework: Next.js.
   - Production branch: `main`.
2. **Settings → Environment Variables.** Use [`apps/web/.env.example`](../apps/web/.env.example) as the checklist.
   - Production: `APEX_MODE=PAPER_LIVE_DATA` to start (it publishes nothing to the group channels; the owner sees the would-be setups in the scan records), `DATABASE_URL`, `APEX_PUBLIC_BASE_URL`, the four random secrets, `DISCORD_OAUTH_*`, `APEX_OWNER_DISCORD_IDS`, and `APEX_MODEL_TRUSTED_KEYS` (public key only).
   - Preview: `APEX_MODE=DEMO` and **no** `DATABASE_URL`.
     - Previews then run on an embedded demo database.
     - They cannot touch production data or Discord.
   - Never prefix a secret with `NEXT_PUBLIC_`. The lint rule `no-restricted-syntax` blocks it.
3. **Settings → Deployment Protection → Vercel Authentication → All Deployments** (this includes Production).
   - Since September 2026 this is available on every plan: https://vercel.com/changelog/protect-production-deployments-for-free-on-every-plan
   - This is the outer lock. The app's Discord sign-in is the inner one.
4. **Settings → Deployment Protection → Protection Bypass for Automation**: create a secret and give it to the Pi as `APEX_VERCEL_BYPASS_SECRET`. The app accepts that header only on the signed machine routes (`/api/v1/agent/*`, `/api/v1/release/*`). Anywhere else it returns `403`, and HMAC signatures are still required on top. The bypass is never the only trust boundary.
5. Deploy. Then, **from your computer**, run:
   ```bash
   node scripts/smoke-deployment.mjs https://<production-domain> --preview https://<a-preview-url>
   APEX_SMOKE_BYPASS=… APEX_SMOKE_AGENT_ID=agt_… APEX_SMOKE_HMAC_SECRET=… node scripts/smoke-deployment.mjs https://<production-domain>
   ```
   Every executed check must pass. A failure on the first check means Vercel Authentication is not protecting production.

**Vercel Hobby notice.** Hobby is for personal, non-commercial use: https://vercel.com/docs/plans/hobby. A multi-user or commercial service needs a plan and legal re-evaluation (see the compliance switches).

No Vercel Cron is used. Everything scheduled runs on the Pi or in GitHub Actions.

## 4. Raspberry Pi

Beginner path: [raspberry-pi/08_APEX_UPGRADE.md](../raspberry-pi/08_APEX_UPGRADE.md). In short:

```bash
cd ~/STONKS2026 && git pull --ff-only
./scripts/apex-install.sh       # asks before every change; never overwrites ~/.config/apex/apex.env
nano ~/.config/apex/apex.env    # Discord token, guild id, owner ids, server URL, bypass secret
# Dashboard → Settings → Pi pairing → Start pairing, then on the Pi:
cd apps/pi-worker && uv run apex pair ABCD-EFGH --server https://<your-domain>
# compare the printed fingerprint with the dashboard, click Approve
./scripts/apex-doctor.sh
```

| Task | Command |
|---|---|
| Health check with specific fixes | `./scripts/apex-doctor.sh` |
| Update (shows commits, confirms, self-tests, restarts, auto-reverts on failure) | `./scripts/apex-update.sh` |
| Roll back to the previous deploy (refuses if the local data schema is newer) | `./scripts/apex-rollback.sh` |
| Logs | `journalctl -u apex-worker -u apex-discord-bot -n 100 --no-pager` |
| Restart | `sudo systemctl restart apex-worker apex-discord-bot` |

Services restart on failure with bounded backoff (5 tries in 10 minutes). They do **not** loop on a bad token or a duplicate instance. Those exit with code 2, 3 or 4, and `apex-doctor.sh` explains which.

## 5. Credentials (Alpaca, FRED, GitHub)

Enter them in **Dashboard → Settings → Integrations**, then press **Test connection**.

- They are encrypted with `APEX_CONFIG_ENCRYPTION_KEY` and shown only as `…ABCD` afterwards.
- Alpaca reaches the Pi only as a short-lived envelope encrypted to the Pi's own key. The card shows when the Pi has acknowledged the current version.
- To rotate, paste a new value and save.

**Encryption key rotation.**
1. Set the new key as `APEX_CONFIG_ENCRYPTION_KEY` and bump `APEX_CONFIG_ENCRYPTION_KEY_VERSION`.
2. Move the old key to `*_PREVIOUS`.
3. Re-save each integration.
4. Remove `*_PREVIOUS`.

## 6. Backups and restore test

Supabase Free promises no point-in-time recovery, so export what matters.

```bash
sudo apt install -y postgresql-client age
./scripts/apex-backup.sh --supabase          # asks for the connection string once, encrypts with age
./scripts/apex-restore-check.sh ~/apex-backups/supabase-apex-<stamp>.dump.age
RESTORE_DATABASE_URL=postgres://…scratch… ./scripts/apex-restore-check.sh <file>   # full restore into a SCRATCH database
```

- The Pi snapshots its own store nightly (`apex-backup.timer`, keeps 7).
- Set `APEX_BACKUP_AGE_RECIPIENT` (an age *public* key) in `~/.config/apex/backup.env` to encrypt those snapshots.
- Backups live in `~/apex-backups` (mode 700). Never put them in the repository or a public cloud folder.

## 7. Freshness goals (editable: Settings → Freshness goals)

| Signal | Default goal | What happens when missed |
|---|---|---|
| Streamed quote (market open, subscribed) | stale after 2 min | price-based wording suppressed; last observation time stamped |
| Broad daily bars | stale after 30 min | scan postponed; scanner light turns STALE |
| Pi heartbeat | late after 3 missed 60-second beats | Pi light amber, then red; `#system-status` notice after hysteresis |
| Recovery | 2 consecutive fresh checks | warnings clear |

These are goals for a free, single-venue feed, not guarantees.

During outages:
- The Pi keeps a disk-backed spool and replays it in order. The server deduplicates by id, so nothing is posted twice.
- Late notices carry their original event time.

## 8. Outage playbook

| Symptom | Likely cause | Do this |
|---|---|---|
| Dashboard shows "This view could not load" | Supabase paused or `DATABASE_URL` wrong | Restore the project in Supabase; check the env var; `pnpm --filter @apex/web db:migrate` |
| Pi light red | power, network, crashed service | `./scripts/apex-doctor.sh` on the Pi |
| Market feed STALE while open | Alpaca outage or expired key | Settings → Integrations → Alpaca → Test; the Pi suppresses price-based alerts until fresh |
| Bot offline | token revoked, starter bot fighting | `apex-doctor.sh` (exit code 2 or 4 explained) |
| Weekly training missed | GitHub schedule delay, minutes exhausted | Models & Automation; the Pi keeps the last valid model until it expires, then publishes research setups only |
| Need everything quiet now | — | Alerts & Delivery → **STOP all investment alerts** (system alerts still flow) |

## 9. Security checklist (also see [SECURITY.md](SECURITY.md))

- [ ] `smoke-deployment.mjs` passes for production and a preview.
- [ ] `./scripts/apex-doctor.sh` reports no inbound ports besides SSH.
- [ ] Pi device fingerprint compared before approval; old devices revoked.
- [ ] `~/.config/apex/apex.env` is mode 600; no secrets in the repository (`node scripts/secret-scan.mjs`).
- [ ] Compliance switches OFF unless an external review is recorded.
