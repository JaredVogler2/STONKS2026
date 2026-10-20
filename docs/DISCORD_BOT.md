# APEX Discord bot — operator and member guide (Phase 09)

The production bot replaces the `raspberry-pi/bot` starter. It runs on the Raspberry Pi as a persistent
**Gateway** client (no inbound ports, no Vercel interaction endpoint), serves **one private server**, and is a
front end only: positions live in the APEX ledger (local SQLite in DEMO/offline mode; the cloud API later),
not in Discord.

APEX is research only. It never places orders. Every fill a member records is **self-reported** and is not proof
of a broker execution.

Status: implemented and unit/integration tested without a network (fake transport and fake interactions).
**Not yet live-verified against Discord** — see [Known limitations](#known-limitations).

---

## 1. How it fits together

```
Discord Gateway ──> gateway.py (DiscordCommands: slash commands, buttons, modals; no logic, no SQL)
                      │ Actor = authenticated user id + interaction id + guild id
                      v
                    service.py (BotService: gating, validation, idempotency, ownership, confirmations,
                      │          message composition within Discord limits — pure Python)
                      v
                    backend.py ports: PositionBackend (record/positions/position) ─> LocalPositionService
                                      MemberBackend (prefs, mutes, private hold)  ─> LocalStore
                                      Spool (pending sync)                        ─> LocalStore.spool
delivery.py (own asyncio task): notification_outbox ─> render() ─> DiscordTransport ─> channels / DMs
```

`PositionBackend` has the same methods as `LocalPositionService`; a remote implementation backed by the signed
agent API can be swapped in. It must raise `BackendUnavailable` when the canonical store cannot be reached; the bot
then queues the event locally and answers **"Queued - pending sync … NOT saved yet"**. It never claims a save that
did not happen.

Source: `apps/pi-worker/src/apex_worker/discord_bot/` (`gateway.py`, `service.py`, `backend.py`, `delivery.py`,
`validation.py`, `limits.py`, `settings.py`, `app.py`, `samples.py`). Starter import:
`apex_worker/operations/starter_migration.py`.

---

## 2. Operator setup

### 2.1 Discord application (same as the starter)

Follow `raspberry-pi/04_DISCORD_CONFIGURATION.md` to create the application, the bot user and the private server.
If you already ran the starter, **reuse the same application, token and server**.

- Install scopes: `bot` and `applications.commands`, install target **Guild**.
- Bot permissions: **View Channels, Send Messages, Embed Links** only (permissions integer `19456`).
  Do **not** grant Administrator, Manage Roles or Mention Everyone.
- Privileged intents: leave **Message Content**, **Presence** and **Server Members** OFF. The bot uses only the
  non-privileged `GUILDS` intent. (Optional: see [Membership and revocation](#7-membership-and-revocation).)

### 2.2 Configuration (`.env`, owner-only, `chmod 600`)

| Variable | Required | Meaning |
|---|---|---|
| `DISCORD_BOT_TOKEN` | yes | Bot token. Never commit, paste in chat, or log it (the bot masks it in logs). |
| `DISCORD_GUILD_ID` | yes | Numeric ID of your private server. Commands are registered **only** there. |
| `APEX_OWNER_DISCORD_IDS` | recommended | Comma-separated Discord user IDs allowed to use `/apex_admin_*`. |
| `APEX_MODE` | no | `DEMO` (default) labels everything DEMO. |
| `APEX_STATE_DIR` | no | State directory (default `~/.apex`): local DB, component secret, lock file. |
| `APEX_LOCAL_DB` | no | Local SQLite path (default `<state_dir>/apex_local.sqlite3`). The worker that writes the outbox must use the same file. |
| `APEX_CHANNEL_MAP` | yes for alerts | JSON `{channel_key: channel_id}` (see below). Alternative: `<state_dir>/discord_channels.json`. |
| `APEX_ALLOW_LEGACY_OVERLAP` | no | `1` to start even though the starter service is active (not recommended). |
| `APEX_DISCORD_MEMBERS_INTENT` | no | `1` only if you enabled the privileged Server Members intent (instant revocation on leave). |
| `APEX_OUTBOX_POLL_SECONDS` | no | Delivery poll interval, default 5. |

The component-signing secret is created automatically in `<state_dir>/component_secret` (0600). Keep it: if it
changes, buttons on older alerts are rejected as invalid (safe, but members must use slash commands instead).

### 2.3 Channel map

Create the channels manually (the bot does not need channel-management permissions) and copy each channel ID
(Developer Mode → right-click → *Copy Channel ID*):

| channel_key | Suggested channel | What is posted |
|---|---|---|
| `high_conviction_alerts` | `#high-conviction-alerts` | `NEW_SIGNAL` (high-conviction tier) with buttons |
| `market_intelligence` | `#market-intelligence` | research-tier setups, catalysts |
| `active_trades` | `#active-trades` | shared changes to a published thesis |
| `exit_alerts` | `#exit-alerts` | group signal closure/invalidation (never personal sells) |
| `performance_reports` | `#performance-reports` | aggregate scorecards |
| `system_status` | `#system-status` | owner/admin health alerts |

```
APEX_CHANNEL_MAP={"high_conviction_alerts":"123456789012345678","market_intelligence":"123456789012345679","active_trades":"123456789012345680","exit_alerts":"123456789012345681","performance_reports":"123456789012345682","system_status":"123456789012345683"}
```

Unknown keys or non-numeric IDs make the bot refuse to start. A key that is simply missing is allowed: those
alerts stay **queued** (retried every 10 minutes, then dead-lettered after the attempt limit) and
`/apex_admin_status` lists the missing keys.

Recommended channel permissions: members can *View Channel* and *Use Application Commands* everywhere; only the
bot (and the owner) can *Send Messages* in the alert channels; `#system-status` visible to the owner only. The
bot must be able to view/send/embed in every mapped channel.

### 2.4 Dry run (no token needed, nothing connects)

```bash
cd ~/STONKS2026/apps/pi-worker
uv sync --extra discord          # discord.py 2.7.1 (pinned in uv.lock)
uv run apex bot --dry-run
```

Prints JSON with: the exact slash-command registration payloads, intents (no privileged ones), invite
permissions, channel-map status, local DB path, whether the starter service is active, and a sample DEMO
`NEW_SIGNAL` message rendered and routed exactly as it would be posted (with its Discord-limit check).

### 2.5 Run

```bash
uv run --extra discord apex bot
```

On start the bot:
1. validates the token, guild ID and channel map (exit code 2 on problems);
2. takes an exclusive `flock` on `<state_dir>/discord_bot.lock` — a second bot process exits with code 3;
3. checks `systemctl is-active apex-pi-bot` (best effort; skipped without systemd). If the starter is active it
   prints migration steps and exits with code 4 unless `APEX_ALLOW_LEGACY_OVERLAP=1`;
4. connects, syncs the guild commands, leaves any server that is not `DISCORD_GUILD_ID`, and starts the outbox
   delivery task and the pending-sync replay task.

Example systemd unit (adapt user/paths; Phase 11 owns the final service files):

```ini
[Unit]
Description=APEX Discord bot
After=network-online.target
Wants=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/STONKS2026/apps/pi-worker
EnvironmentFile=/home/pi/STONKS2026/apps/pi-worker/.env
ExecStart=/home/pi/.local/bin/uv run --extra discord apex bot
Restart=on-failure
RestartSec=15
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
```

---

## 3. Member guide

Every reply is **ephemeral** (only you see it). Slash commands only work inside the private server.

| Command | What it does |
|---|---|
| `/apex_help` | How to use APEX, privacy summary. |
| `/apex_health` | Bot online, mode (DEMO label), open signals, market-data age, delivery status. |
| `/apex_signals` | Open group signals with *Record entry / Watch / Evidence* buttons. |
| `/apex_watch signal_id: \| ticker:` | Privately watch a signal (autocomplete) or a ticker. |
| `/apex_unwatch position:` | Stop watching a watch-only position. |
| `/apex_entry ticker price shares [signal_id] [fill_time] [fees] [notes]` | Record your own buy. Starter-compatible. If you already hold a position from that signal it is recorded as an **ADD** (your earlier cost basis is kept); a watch becomes an open position. |
| `/apex_positions [position] [include_closed]` | Your positions only; with `position` a detail view (lots, history with refs, plan, data age). |
| `/apex_add position price shares …` | Another buy on your position. |
| `/apex_reduce position price shares …` | Partial sell (cannot sell more than you hold). |
| `/apex_close position price …` | Sell all remaining shares. |
| `/apex_correct position fill action [price shares fill_time fees reason]` | `void`, `replace` or `undo` a correction. **Asks for confirmation.** The original stays in your audit history. |
| `/apex_remove position_id` | Starter-compatible removal: a confirmed **void correction** of your single entry (or an unwatch). Old starter entry numbers (e.g. `1`) still work after migration. |
| `/apex_notifications [dms] [high_priority_mentions] [mute] [unmute]` | DM opt-in (sends a test DM), @mention opt-in, per-ticker mutes. |
| `/apex_export` | Private JSON copy of your data (inline, or as an attached file when large). |
| `/apex_delete_my_data` | Permanently deletes your positions, history, held notifications, mutes and preferences — **two confirmations**. Shared signals and aggregate statistics are unaffected. |
| `/apex_admin_status` | Owner only: health and **counts only** (never anyone's fills). |
| `/apex_admin_restore member` | Owner only: restore a revoked member. |

Position references: pick from autocomplete, or type the 8-character ref shown in `/apex_positions`, the ticker
(when unambiguous), or an old starter entry number.

### Buttons on shared alerts

- **Record My Entry** — opens a private form (price, shares, optional fill time, fees, notes) bound to that
  signal. Works for open (`PUBLISHED`/`ACTIVE`) signals; for an expired/closed signal the bot explains how to
  record a late entry with `/apex_entry … signal_id:` (marked *OFF-PLAN, reassessment required*).
- **Watch** — privately track the signal.
- **View Evidence** — reasons, counter-evidence, catalysts, calibration status (*UNVALIDATED* when there is no
  calibrated probability), plan, data provenance.
- **Mute Symbol** — no DMs or @mentions about that ticker for you (updates are held and shown privately).
- **Update My Position** (on private DMs) — add / sell some / close all / details for *your* position; ownership is
  re-checked on every step.

Button IDs are HMAC-signed (`notifications/components.py`) and re-verified on every click, and the signal/position
is re-loaded server-side. Tampered, foreign or stale IDs get "This button is invalid or has expired. Nothing was
recorded." Buttons keep working after a bot restart (they are routed by signed ID, not by in-memory views).

### Input rules

- Ticker: upper-cased and trimmed, must match `^[A-Z][A-Z0-9.]{0,9}$`.
- Numbers: plain decimals with a dot, no thousands separators (`1250.5`). Must be finite and > 0 (fees ≥ 0).
  Limits: price ≤ 1,000,000 with ≤ 4 decimals; shares ≤ 100,000,000 with ≤ 6 decimals; fees ≤ 100,000 with
  ≤ 4 decimals. Slash-command numbers are converted with `str()` then `Decimal` (no binary-float rounding).
- Fill time: blank/`now`, `HH:MM` (today, New York), `YYYY-MM-DD HH:MM` (New York), or ISO-8601 with a zone
  (`2026-10-20T14:31Z`). Not in the future (2-minute skew allowed); a date alone is rejected.
- Notes ≤ 280 characters, private.

Every rejected input gets a specific message (for example *"You cannot sell more shares than you hold"*,
*"Too many decimal places (prices: 4, shares: 6, fees: 4)"*); nothing is saved.

### What a confirmation shows

Symbol, quantity, price, **estimated notional** (+ fees), your **average cost** (FIFO) and state, the signal's
entry zone / **stop (invalidation)** / target / horizon / entry expiry, a **market-data line** (age and feed of the
last APEX quote, or "market data may be offline - your record is still saved"), warnings such as
*OFF-PLAN ENTRY*, and the footer *"Self-reported - not broker proof. APEX places no orders. Private: only you
can see this."* DEMO mode prefixes `[DEMO]`.

---

## 4. Privacy model

- All command/button/form replies are ephemeral (`flags 64`) with `allowed_mentions` = none.
- Shared channels receive only group research (`NEW_SIGNAL`, signal updates/closures, scorecards, system alerts).
  The delivery loop refuses to post personal kinds (`POSITION_UPDATE`) to any channel (dead-lettered instead).
- Personal updates go by **DM only after opt-in** (`/apex_notifications dms:True`). If DMs are off, Discord
  refuses the DM (403/404), the symbol is muted or the member is revoked, the update is **held privately**
  (outbox state `deferred`) and shown inside that member's next ephemeral APEX reply, then marked shown
  (`release_deferred_for_user`). It is never redirected to a channel.
- High-priority @mentions: only members who opted in are mentioned, individually (`allowed_mentions.users`), only
  on HIGH/URGENT `NEW_SIGNAL`/`SIGNAL_CLOSED` alerts, never `@everyone`/`@here`/roles. Channel readers can see who
  opted in to mentions — nothing else.
- `/apex_admin_status` shows aggregate counts only. Owners do not see member fills through the bot.
- Logs record command names and outcome codes only — never option values, fills or tokens. A logging filter masks
  the configured token and anything shaped like a Discord token.
- The local SQLite file and the state directory are created owner-only (0600/0700). Whoever controls the Pi can
  still read it: treat the Pi as sensitive.
- Members can export (`/apex_export`) and delete (`/apex_delete_my_data`) their own data.

## 5. Identity, idempotency and confirmations

- The actor is always the interaction's authenticated user; no command accepts a user ID for position actions.
  Another member's position ID behaves exactly like a non-existent one ("No position with that reference
  belongs to you").
- `client_idempotency_key = discord:<interaction id>`; confirmations use `discord:<original command interaction
  id>:confirm`. A resent/replayed interaction or a double-click returns *"Already recorded - duplicate
  ignored"*; the ledger enforces uniqueness per member.
- Destructive actions (`/apex_correct`, `/apex_remove`, `/apex_delete_my_data`) show a Confirm/Cancel prompt
  bound to the requesting member; prompts expire after 10 minutes (or on restart) and are single-use.
- Discord requires an acknowledgement within 3 seconds; the bot defers (ephemeral, "thinking") and runs the
  backend call in a worker thread. Interaction tokens stay valid for 15 minutes for the follow-up.

## 6. Delivery reliability

- Durable outbox in SQLite (`notification_outbox`, `delivery_attempts`); a restart never resends delivered
  items. Channel/DM sends carry a deterministic nonce with Discord's `enforce_nonce`, which also absorbs a crash
  between "sent" and "acknowledged" for a few minutes.
- `429`: the item is rescheduled with Discord's `retry_after` and the whole loop pauses until then.
  discord.py waits internally for short limits (`max_ratelimit_timeout=10s`); longer ones surface to the outbox.
- Other errors: exponential backoff (2^attempts s, capped at 15 min) and **dead letter** after 8 attempts
  (visible in `/apex_admin_status`).
- Pending sync: if the position backend is unreachable, the member's event is stored in the local spool
  (idempotent by key), shown under *Pending sync (NOT saved yet)* in `/apex_positions`, and replayed every 60 s.
  Rejections on replay are shown to that member.

## 7. Membership and revocation

- Commands are registered only for `DISCORD_GUILD_ID`; interactions from any other server or from DMs are refused
  politely, and the bot leaves any other server it is added to.
- Revoked members are refused on every command, button and form.
- Without privileged intents Discord does not send "member left" events. The bot therefore verifies membership
  (REST `fetch_member`, cached 10 minutes) before every private DM; a member who left is revoked and the DM is
  held. If you enable the **Server Members** intent in the Developer Portal and set
  `APEX_DISCORD_MEMBERS_INTENT=1`, `on_member_remove` revokes immediately.
- Revocation is sticky: a member who rejoins stays revoked until an owner runs `/apex_admin_restore`. Their own
  data is retained (not shown to anyone) until they ask for deletion.

## 8. Migrating from the starter bot

The starter's four commands keep working with the same names and options: `/apex_health`, `/apex_entry`
(`ticker price shares signal_id`, now with optional `fill_time fees notes`), `/apex_positions`,
`/apex_remove position_id` (now a confirmed void correction; old entry numbers still resolve).

1. Preview (read-only, writes nothing):
   ```bash
   uv run apex migrate-starter --starter-db ~/STONKS2026/raspberry-pi/bot/data/positions.sqlite3
   ```
   The report lists each starter row as `would_import`, `already_imported` or `skipped` with a reason code
   (for example `TOO_PRECISE`, `TOO_LARGE`, `INVALID_SYMBOL`) and never prints prices or share counts.
2. Apply:
   ```bash
   uv run apex migrate-starter --starter-db ~/STONKS2026/raspberry-pi/bot/data/positions.sqlite3 --apply
   ```
   First writes a consistent backup `positions.sqlite3.backup-<UTC timestamp>` (mode 0600) next to the starter
   file, then imports each row as an `OPEN` event (`source=starter_import`, idempotency key `starter:<id>`,
   `fill_at` = the starter's `created_at`). Real APEX signal IDs (`sig_` + 24 hex) of a known signal are linked;
   test refs like `DEMO-001` (or unknown IDs) are kept as a private note. Re-running is a no-op.
3. Stop the starter: `sudo systemctl disable --now apex-pi-bot`.
4. Start the new bot (`apex bot`). Its guild command sync replaces the starter's command definitions.

Schema check: the migration refuses (`unsupported_schema`) unless the starter table has
`id, discord_user_id, ticker, price, shares, signal_id, created_at`.

**Rollback**: the starter file is never modified. Stop `apex bot`, then `sudo systemctl enable --now apex-pi-bot`.
Entries recorded in the new bot after migration are not copied back to the starter.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `apex bot` exits 2 | Read the printed problem (token, guild ID, channel map JSON). |
| exits 3 | Another bot process holds `<state_dir>/discord_bot.lock`. |
| exits 4 | The starter service is active — migrate and stop it (section 8). |
| Commands missing in Discord | Check `DISCORD_GUILD_ID`; the bot must be in that server with `applications.commands`. |
| Alerts not appearing | `/apex_admin_status` → *Channels missing* / *Dead letters*; check the bot can post in the channel. |
| Member gets no DMs | `/apex_notifications dms:True`; Discord *Privacy Settings → Allow direct messages from server members*. Held updates appear in their next APEX reply. |
| "This button is invalid or has expired" | The component secret changed or the button was tampered with; use slash commands. |
| "Queued - pending sync" | The position store was unreachable; it syncs automatically — do not re-enter. |

## Known limitations

- Not live-verified: slash-command sync, modal rendering on iOS/Android, ephemeral follow-ups/edits, DM
  delivery, 429 behaviour and `enforce_nonce` dedupe were exercised with fake interactions/transports only.
- The remote (signed agent API) `PositionBackend` is not implemented yet; the local SQLite backend is canonical in
  DEMO/offline mode. The spool queues events only when a backend raises `BackendUnavailable`.
- Without the optional Server Members intent, a departed member is revoked at the next DM attempt rather than
  instantly (interactions are only possible for current server members anyway).
- Confirmation prompts are in memory: a restart within the 10-minute window asks the member to run the command
  again.
- `/apex_correct` can target fills (buys/sells) and corrections; event refs come from the position detail view.
- Text-reply parsing ("Bought 20 @ 181.25") is intentionally not implemented (needs the privileged Message
  Content intent and would expose fills publicly).
