# APEX / STONKS2026 — Raspberry Pi Beginner Build Kit

**Last checked:** October 20, 2026  
**Audience:** First-time Raspberry Pi owners. No computer-assembly experience assumed.  
**Repository:** https://github.com/JaredVogler2/STONKS2026

## Start here — do these in order

1. [01 — Amazon shopping list](01_AMAZON_SHOPPING_LIST.md). Buy only what you need.
2. [02 — Assemble the Raspberry Pi](02_ASSEMBLE_HARDWARE.md). No soldering or computer building required.
3. [03 — Install the operating system](03_FIRST_BOOT_AND_SSH.md). Set up a computer you can control without a screen.
4. [04 — Create your private Discord bot](04_DISCORD_CONFIGURATION.md). Make it possible for several people to interact with APEX.
5. [05 — Install the working starter bot](05_INSTALL_BOT.md). Test private slash commands that store individual paper-position entries.
6. [06 — Everyday operation and troubleshooting](06_OPERATIONS_TROUBLESHOOTING.md). Make it start automatically after power outages and solve common issues.
7. [07 — Security, architecture and future APEX integration](07_SECURITY_AND_INTEGRATION.md). Understand what is and isn't implemented.

## What you are building

**Raspberry Pi 5 (4 GB)** — a small computer that stays plugged in at home. It maintains a Discord Gateway connection 24/7 and can later run lightweight polling and outbound alerts.

**Discord** — a private server for distributing signals to invited members. Members can record their OWN fill price and share quantity using private slash commands. Personal entries are NOT published to the general alert channel.

**Vercel APEX dashboard / market intelligence engine** — to be developed separately. The Pi does not itself provide market quotes, forecast probabilities, buy/sell recommendations, or Robinhood integration.

### IMPORTANT: What works immediately after these instructions

The included starter Python application responds to:
- `/apex_health`: verifies that the bot is online.
- `/apex_entry`: privately records YOUR ticker, entry price, share count and signal ID.
- `/apex_positions`: privately lists YOUR saved entries.
- `/apex_remove`: privately removes one of YOUR entries.

It stores demonstration data in a local SQLite database on the Pi. **It does NOT yet monitor real market prices or send personalized financial signals.** A later coding-agent phase must connect it securely to APEX backend data and synchronize positions. There is deliberately **no automated trading**.

### Why the Pi 5 4 GB?

The always-on Discord bot is light. Paying extra for 8/16 GB or an AI HAT is unnecessary unless you later decide to run heavy models locally. If you already own a Pi 5 with adequate cooling and power, use it instead of purchasing another.

### Zero monthly subscription is not zero total cost

Hardware, electricity, and existing internet access cost money. Discord, Python, and this local starter bot do not require a subscription. Future market-data/API tiers and Vercel hosting depend on provider limits and terms; do not assume unlimited free access or guaranteed uptime.

## Useful links

- Official Raspberry Pi setup: https://www.raspberrypi.com/documentation/computers/getting-started.html
- Official Raspberry Pi Imager: https://www.raspberrypi.com/software/
- Discord Developer Portal: https://discord.com/developers/applications
- Raspberry Pi 5 specifications: https://www.raspberrypi.com/products/raspberry-pi-5/

**Security rule:** Never commit Discord tokens, .env files, private API keys, SSH private keys, or market-data credentials to GitHub.