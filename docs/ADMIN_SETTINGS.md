# APEX configuration reference

Configuration lives in four places. Each value lives in exactly one of them.

| Where | What | Who can read it |
|---|---|---|
| **Vercel → Environment Variables** | server secrets, database URL, owner allow-list, trusted model public keys | the server only; never sent to browsers |
| **Dashboard → Settings** (stored in Postgres) | market rules, policy, channel map, budgets, freshness goals, compliance switches, encrypted integration credentials | you (owner); secrets are write-only |
| **Pi → `~/.config/apex/apex.env`** (mode 600) | Discord token, server URL, shared agent HMAC, trusted model public keys | the Pi user only |
| **GitHub → Settings → Environments → `model-release`** | release HMAC, model signing key, optional Alpaca keys for training | protected workflows on `main` only |

There is never a secret in the repository, a `NEXT_PUBLIC_*` variable, Discord, or a log.

## Vercel environment variables

The full checklist with comments is [`apps/web/.env.example`](../apps/web/.env.example).

| Variable | Required | Purpose / format | Rotate |
|---|---|---|---|
| `APEX_MODE` | yes | `DEMO`, `PAPER_LIVE_DATA`, `PERSONAL_RESEARCH`, `GROUP_POSITION_ALERTS`. Previews: `DEMO`. Signals reach the group channels only in `DEMO` and `GROUP_POSITION_ALERTS`; in the other two modes the would-be setups appear only in the owner's scan records. | change + redeploy |
| `DATABASE_URL` | outside DEMO | Supabase transaction-pooler URI (port 6543) | Supabase → reset password |
| `APEX_PUBLIC_BASE_URL` | for Discord sign-in | production URL, no trailing slash | — |
| `APEX_SESSION_SECRET` | outside local DEMO | 32+ random bytes, base64; signs session cookies | replace (everyone signs in again) |
| `APEX_CONFIG_ENCRYPTION_KEY` (+ `_VERSION`, `_PREVIOUS`, `_PREVIOUS_VERSION`) | to store integrations | exactly 32 random bytes, base64; AES-256-GCM | see [SECURITY.md](SECURITY.md#secret-rotation-summary) |
| `AGENT_HMAC_SECRET` (+ `_PREVIOUS`) | for the Pi | 32+ random bytes, base64; same value on the Pi | overlap with `_PREVIOUS` |
| `APEX_RELEASE_HMAC_SECRET` (+ `_PREVIOUS`) | for cloud training | 32+ random bytes, base64; same value in the GitHub Environment | overlap with `_PREVIOUS` |
| `APEX_RELEASE_GITHUB_REPOSITORY` | for cloud training outside DEMO | `owner/repo` whose `apex-model-*` workflows may call the release API (GitHub OIDC second factor) | — |
| `APEX_RELEASE_GITHUB_REF` / `APEX_RELEASE_OIDC_AUDIENCE` | no | release branch (default `main`) / token audience (default `apex-release`) | — |
| `APEX_RELEASE_OIDC` | no | `off` disables the OIDC factor (HMAC only, warning logged); only for a host that cannot issue GitHub OIDC tokens | — |
| `APEX_MODEL_TRUSTED_KEYS` | for any model | `{"1":"<43-char base64url Ed25519 public key>"}` | add the new version, remove the old later |
| `DISCORD_OAUTH_CLIENT_ID` / `_SECRET` | for owner sign-in | Discord application OAuth2 | Developer Portal → reset secret |
| `APEX_OWNER_DISCORD_IDS` | for owner sign-in | comma-separated Discord user ids | edit |
| `APEX_AUTO_MIGRATE` | no | `1` applies migrations at boot (default: run `db:migrate` yourself) | — |

## Pi `apex.env`

The full annotated list is [`apps/pi-worker/.env.example`](../apps/pi-worker/.env.example). The most important entries:

| Variable | Required | Notes |
|---|---|---|
| `APEX_MODE` | yes | must match the dashboard (DEMO data is never accepted by a live server) |
| `APEX_API_BASE_URL` | connected mode | dashboard URL; also stored by `apex pair` |
| `AGENT_HMAC_SECRET` | connected mode | same as Vercel |
| `APEX_VERCEL_BYPASS_SECRET` | if Vercel Authentication covers the API | only works on signed machine routes |
| `APEX_MODEL_TRUSTED_KEYS` | to use models | public keys only. They are held on the Pi and **not** taken from the server, so a compromised server cannot push a model. |
| `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, `APEX_OWNER_DISCORD_IDS`, `APEX_CHANNEL_MAP` | for the bot | see [DISCORD_BOT.md](DISCORD_BOT.md) |
| `ALPACA_KEY_ID` / `ALPACA_SECRET_KEY` | **no** | manual fallback only; prefer the dashboard, which delivers them encrypted to the Pi |
| `SEC_USER_AGENT` | for earnings dates | a name and a contact email (or the email alone), sent to the SEC only. Without it every earnings date stays `UNKNOWN`, so the earnings veto cannot fire. Never logged. |

Device keys (`device_keys.json`), the pairing identity (`agent.json`), the local store and the spool live in `APEX_STATE_DIR` (default `~/.apex`, mode 700).

## Integrations (Dashboard → Settings → Integrations)

All are optional. APEX runs fully in DEMO without any.

| Provider | Needed for | Fields | Test | Where it goes |
|---|---|---|---|---|
| Alpaca Market Data (Basic, free) | live quotes (IEX ≤ 30 symbols) and history | key id, secret | server requests the latest IEX bar for SPY | encrypted at rest; delivered to the Pi as a device-encrypted envelope; the Pi acks the version |
| FRED (free key) | macro release context | API key | one series metadata request (CPIAUCSL) | server only |
| GitHub fine-grained token | **Run training now** and Actions observability | token (Actions read/write, this repo only), `owner/repo` | lists the repository's workflows | server only |
| Owner alert webhook | owner-only alerts when the Pi is down | `https://discord.com/api/webhooks/…` only (SSRF protection) | — | server only |

Rules for every integration:
- **Save / Rotate.** Paste a new value and save. Stored values are never shown again, only a hint like `…ABCD`.
- **Test connection.** The status card shows the result and age. Tests are rate-limited to one per 30 s.
- **Disable.** Keeps the values but stops using them.
- **Delete.** Removes them.
- Every action is audited, without values.

## Dashboard settings keys

All are validated server-side and versioned. A stale tab cannot overwrite a newer version. History is under **Operations → Configuration history**.

| Key | Fields (default) | Used by |
|---|---|---|
| `market.universe` | `min_price` (10), `min_median_dollar_volume` (30 M), `min_avg_volume` (1 M), `min_history_sessions` (180), `include_etfs` (true), `exclude_leveraged` (true), `include` / `exclude` / `user_verified` symbol lists | Pi nightly universe + scan |
| `market.slots` | `max_slots` (25, ≤ 30), `reserve_for_positions` (10) | Pi IEX stream rotation (held positions first) |
| `market.cadence` | `intraday_minutes` (15 or 30), `nightly_refresh_et` (19:30) | Pi scheduler |
| `risk.policy` | `min_quality` (70), `max_new_per_session` (3), `max_open_per_sector` (2), `research_setups_enabled` (true), `research_min_quality` (75), `entry_valid_sessions` (2), `hypothetical_risk_per_trade_pct` (0.5), `portfolio_open_risk_cap_pct` (3) | Pi signal policy, alert wording |
| `discord.channels` | guild id, six channel ids, opt-in mention role id | bot routing (personal updates are always DMs) |
| `models.policy` | `promotion_mode` (`manual`), `auto_requires_status` (`CALIBRATED`), `poll_minutes` (45) | registry, Pi model sync |
| `budgets` | `actions_monthly_minutes` (1500), `train_timeout_minutes` (45), `drift_enabled` (true), `alpaca_daily_requests` (150 000) | dispatch gate, workflows, Pi quota tracker |
| `sec.contact` | `user_agent` (must include an email) | SEC EDGAR requests |
| `strategies.enabled` | per-strategy on/off overrides ({}) | Pi scan (next run) |
| `alerts.kill_switch` | `stopped` (false), `reason` | outbox leasing (system alerts still flow) |
| `sla.freshness` | quote stale 120 s, bars stale 30 min, heartbeat late after 3 beats, 2 fresh checks to recover | health lights, Pi staleness rules |
| `owner.risk_budget` | `enabled` (false), `hypothetical_account_usd` (25 000) | positions page only |
| `onboarding.manual` | owner confirmations for steps the app cannot verify | guided setup |

## Compliance switches (Settings → Compliance)

`ALLOW_EXTERNAL_MARKET_DATA_REDISPLAY`, `ALLOW_PERSONALIZED_GROUP_GUIDANCE` and `ALLOW_COMMERCIAL_HOSTING` are all **OFF**.

To turn one on you must:
1. type the exact acknowledgement sentence,
2. give an external review reference (≥ 8 characters),
3. not be in DEMO mode.

The change is audited. Turning a switch on records your decision; it does not make anything legally compliant.

## Pi pairing and devices (Settings → Raspberry Pi pairing)

1. **Start pairing.** You get a one-time code: 10 minutes, single use, stored hashed.
2. **On the Pi, run `apex pair CODE --server URL`.** The Pi proves it holds its own private key.
3. **Compare the fingerprint and approve.**
4. **Revoke when needed.** A device is cut off immediately (403 on every call).

The table shows each device's version, clock skew and when it was last seen.

## Data retention and deletion

- **Retention** (Operations → Run retention now): quote events 14 days, heartbeats 7 days, delivery attempts 30 days, feature snapshots 365 days.
- **Kept permanently** (append-only): signals, signal events, position events and audit rows. Members can delete their own data with `/apex_delete_my_data`. Shared signals and aggregate statistics stay.
