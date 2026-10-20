# 08 — Upgrade the Pi from the starter bot to full APEX

**You need nothing new to buy.** The same Pi 5 (4 GB) with its fan, official power supply and SD card runs full APEX.

**Expected result:** the Pi runs two background services.
- `apex-worker` scans the market, watches your members' self-reported positions and syncs approved models.
- `apex-discord-bot` is the upgraded bot with buttons, forms and private updates.

Both start by themselves after a power cut. Your laptop can stay off.

Before you start, complete guides 01–05 (you have a working starter bot). Your **dashboard** should also be deployed: see [docs/README_OPERATOR.md](../docs/README_OPERATOR.md) sections 1–3. If it isn't, you can still finish Parts 1–3 below and pair later.

> **Only one bot may use your Discord token.** The installer stops the old starter service (`apex-pi-bot`) for you, after asking. The new bot also refuses to start while the old one is running.

## Part 1 — Get the latest code

```bash
ssh apex@apex-pi.local
cd ~/STONKS2026
git pull --ff-only
```

If `git pull` complains about local changes, stop and ask for help instead of forcing it.

## Part 2 — Bring your starter entries along (optional, safe)

The starter bot saved members' paper entries in `raspberry-pi/bot/data/positions.sqlite3`. Preview the import. This writes nothing:

```bash
cd ~/STONKS2026/apps/pi-worker
~/.local/bin/uv run apex migrate-starter
```

If the preview looks right, import. A backup of the starter file is made first, readable only by you:

```bash
~/.local/bin/uv run apex migrate-starter --apply
```

Running it twice is safe: entries already imported are skipped. (If `uv` is not installed yet, do Part 3 first and come back.)

## Part 3 — Run the installer

```bash
cd ~/STONKS2026
./scripts/apex-install.sh
```

The installer explains each step and **asks before changing anything**. Answer `y` to:

1. **Install `uv`.** Only asked if it is missing. This is the tool that installs the exact library versions APEX was tested with.
2. **Create `~/.config/apex/apex.env`.** This is your private settings file. If you already have one, it is never overwritten.
3. **Stop the starter bot.** This only stops and disables `apex-pi-bot`; it deletes nothing (revert below).
4. **Install the APEX services.**
5. **Install the nightly backup timer.**

Want to see everything first? Run `./scripts/apex-install.sh --dry-run`. It changes nothing.

## Part 4 — Fill in your private settings

```bash
nano ~/.config/apex/apex.env
```

Fill these from guide 04 (same token, same server id as the starter bot):

| Setting | Where it comes from |
|---|---|
| `DISCORD_BOT_TOKEN` | Discord Developer Portal → Bot → Reset Token |
| `DISCORD_GUILD_ID` | your server's id |
| `APEX_OWNER_DISCORD_IDS` | your own Discord user id |
| `APEX_API_BASE_URL` | your dashboard address, e.g. `https://apex-yourname.vercel.app` |
| `APEX_VERCEL_BYPASS_SECRET` | Vercel → Settings → Deployment Protection → Protection Bypass for Automation |
| `AGENT_HMAC_SECRET` | the same value you put in Vercel |

Save with **Ctrl+O, Enter, Ctrl+X**. Never paste this file into chat, screenshots or GitHub.

**Market data keys do not go here.** Enter your Alpaca keys in the dashboard (Settings → Integrations). The Pi receives them encrypted to its own key.

Start the bot:

```bash
sudo systemctl enable --now apex-discord-bot
```

## Part 5 — Pair the Pi with your dashboard

1. On your phone or computer, open the dashboard → **Settings → Raspberry Pi pairing → Start pairing**. You get a code like `K7QX-M2PD` (valid 10 minutes, single use).
2. On the Pi:
   ```bash
   cd ~/STONKS2026/apps/pi-worker
   ~/.local/bin/uv run apex pair K7QX-M2PD --server https://apex-yourname.vercel.app
   ```
3. The Pi prints a **fingerprint** (groups of letters). The dashboard shows the device with its fingerprint. If they match, click **Approve**. If they don't match, click **Revoke** and start again.
4. Restart the worker so it picks up the pairing:
   ```bash
   sudo systemctl restart apex-worker
   ```

## Part 6 — Check everything

```bash
cd ~/STONKS2026
./scripts/apex-doctor.sh
```

Every line should say PASS. Anything else comes with the exact command that fixes it.

Then in Discord, type `/apex_health`. The dashboard's top bar should show **Pi: OK** and **Discord: OK** within a minute or two.

Unplug the Pi, plug it back in and wait two minutes. Both lights should return to OK by themselves.

## Everyday commands

| I want to… | Type |
|---|---|
| check health | `./scripts/apex-doctor.sh` |
| update to the newest version | `./scripts/apex-update.sh` (shows what changes, asks, tests, and undoes itself if something breaks) |
| undo the last update | `./scripts/apex-rollback.sh` |
| see recent logs | `journalctl -u apex-worker -u apex-discord-bot -n 50 --no-pager` |

## Going back to the starter bot (revert path)

If you need the old bot back:

```bash
sudo systemctl disable --now apex-discord-bot apex-worker
sudo systemctl enable --now apex-pi-bot
```

- The starter's database was not modified by the import; it was only read and backed up.
- Your APEX data stays in `~/.apex` for when you switch back.
- To switch back: `sudo systemctl disable --now apex-pi-bot`, then `sudo systemctl enable --now apex-worker apex-discord-bot`.

## What APEX still does NOT do

- **No orders.** It never places orders or connects to Robinhood or any brokerage. Members record what they did themselves.
- **No probabilities on research setups.** Signals stay labelled research setups, without a probability, until a model passes out-of-sample tests.
- **No personalized group guidance by default.** Private updates to members use informational wording unless you record an external compliance review (dashboard → Settings → Compliance switches).
