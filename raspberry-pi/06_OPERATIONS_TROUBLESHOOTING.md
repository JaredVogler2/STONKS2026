# 06 — Everyday operation, maintenance and troubleshooting

This is the **beginner quick-reference** page. Bookmark it.

## Everyday use

- Leave Pi connected to official power and Ethernet/Wi-Fi.
- Your laptop can be off; the Pi operates independently.
- Members interact through Discord slash commands. Their responses are private to them.
- The bot currently verifies connectivity and manages **demo** entries. It does NOT yet check stock prices.

## Most useful commands

Open Terminal/PowerShell on your laptop, connect using `ssh apex@apex-pi.local`, then use:

| What I want | Exact Pi command |
|---|---|
| Check whether APEX is running | `sudo systemctl status apex-pi-bot --no-pager` |
| Restart APEX after changes | `sudo systemctl restart apex-pi-bot` |
| Stop APEX temporarily | `sudo systemctl stop apex-pi-bot` |
| Start APEX | `sudo systemctl start apex-pi-bot` |
| See latest log messages | `sudo journalctl -u apex-pi-bot -n 50 --no-pager` |
| Check storage remaining | `df -h /` |
| Check temperature | `vcgencmd measure_temp` |
| Properly shut down | `sudo shutdown -h now` |
| Reboot | `sudo reboot` |

If the terminal appears frozen after a service log command, press `q` (quit) or Ctrl+C. Never post your full `.env` contents.

## Problem: the Pi is completely unresponsive

1. Check the official power supply and wall outlet.
2. Check whether Pi LEDs are illuminated or flashing.
3. Verify Ethernet cable is connected on both ends if using wired network.
4. If using Wi-Fi, check the router still has the same SSID/password.
5. Allow several minutes for boot.
6. Find the Pi's IP on the router's connected device list.
7. Try `ssh apex@ACTUAL_PI_IP`.
8. If the Pi does not appear on the network, temporarily attach a monitor with a **micro-HDMI-to-HDMI** cable and USB keyboard, or re-image a backup microSD card using Guide 03.

## Problem: SSH says "could not resolve hostname"

Try `ssh apex@ACTUAL_PI_IP` with your Pi's actual LAN IP. mDNS hostnames ending in `.local` do not resolve on every network/computer. If it works, the Pi is fine.

## Problem: "permission denied" during SSH login

- Check you entered the username configured in Raspberry Pi Imager (example `apex`).
- Passwords are not shown as you type; this is normal.
- If you mistyped the Imager password, try again carefully. Do not brute-force it.
- For GitHub-only SSH failures, see the separate GitHub deploy-key steps in Guide 05.

## Problem: Discord bot is offline

1. Run `sudo systemctl status apex-pi-bot --no-pager`.
2. Check logs using `sudo journalctl -u apex-pi-bot -n 50 --no-pager`.
3. Check internet connectivity: `ping -c 3 discord.com`.
4. Confirm the Discord bot token has not been regenerated or revoked.
5. If the token changed: edit `~/STONKS2026/raspberry-pi/bot/.env` using nano; never paste it into public tools; run `sudo systemctl restart apex-pi-bot`.
6. If you see `Missing DISCORD_BOT_TOKEN`, the local .env file is absent or incorrect.
7. If you see `DISCORD_GUILD_ID must be ...`, fix your numeric server ID.
8. Check the bot has been invited to the same server whose ID you configured.

## Problem: Discord slash commands do not appear

- Confirm the bot was installed with the `applications.commands` scope and sufficient channel permissions.
- Verify guild ID corresponds to the correct server.
- Restart service; the bot registers guild commands on startup.
- Check the logs for "Registered 4 guild slash commands".
- It can take a short time for Discord clients to update their command list; try reopening Discord.

## Problem: heating, random restarts, or lightning-bolt warnings

- Use the official Pi 5 **27 W** PSU. Not all USB-C phone chargers provide 5V/5A for a Pi.
- Make sure the case's fan connector is plugged into the board's dedicated fan socket and not jammed.
- Clear airflow around the enclosure.
- Check `vcgencmd measure_temp`. If temperatures become abnormal or overheating is reported, shut down and inspect installation.

## Problem: GitHub says "Repository not found" when cloning

- Your GitHub repository is **private**.
- Recheck that the public Deploy Key from this Pi is listed in **that repository's** Settings → Deploy keys.
- Verify the SSH host alias was installed correctly.
- Run `ssh -T git@github-apex`. A welcome message explaining there is no shell access means key authentication was successful.
- Confirm the clone URL is exactly `git@github-apex:JaredVogler2/STONKS2026.git`.

## Backups and reliability

1. The local demo positions database is `~/STONKS2026/raspberry-pi/bot/data/positions.sqlite3`. It may be lost if the microSD fails.
2. Before changing hardware or wiping the SD, **stop the service**, make a private backup of the whole `data` directory, and then restart.
3. Store backups somewhere secure because they contain member position information.
4. Do not upload databases, tokens or backups to GitHub.
5. Update OS periodically: `sudo apt update && sudo apt full-upgrade -y`, then reboot when needed.
6. Check `df -h /` monthly; a full SD card can crash the bot.
7. Set the clock/timezone correctly; Discord and later market scans depend on timestamps.
8. Have a fallback: the Vercel/Discord system should report missing or stale data rather than silently presenting a healthy signal scanner.

## If electricity or internet stops

The Discord bot cannot reply while your Pi or internet is offline. systemd automatically starts it after reboot once networking returns; it cannot guarantee uninterrupted service. In the future, health-check missed heartbeats through an independently hosted provider so Discord alerts can disclose a Pi outage.

## Do not use this as a live recommendation engine

The included code has no market API connection, no stock-price monitoring, no backtest or calibrated recommendation probabilities, no transaction execution, and no live alerts. APEX must implement, validate, and deploy these before the bot provides financial signals.

For architecture, see [07 — Security and future integration](07_SECURITY_AND_INTEGRATION.md).