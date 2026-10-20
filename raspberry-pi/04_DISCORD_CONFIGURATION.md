# 04 — Set up Discord for the APEX Raspberry Pi bot

**Goal:** Create a Discord server, invite friends, and add a bot that can accept each person's OWN entry price privately.

You need a **free Discord account** at https://discord.com/ and a browser on your laptop/desktop.

## A. Make a private server

1. Open Discord. Use the **+** to create a server.
2. Name it **APEX Trading Intelligence** or your choice.
3. Invite only people you trust.
4. Create text channels: `#high-conviction-alerts`, `#active-trades`, `#exit-alerts`, `#market-intelligence`, `#system-status`.
5. Keep the server invitation private. Avoid public discovery or public invite links.

These channels don't get automatic market alerts yet. They are being prepared for the APEX scanner that will be built separately.

## B. Create the Discord application and bot

1. Go to **https://discord.com/developers/applications**.
2. Click **New Application**, name it **APEX**, and accept the terms.
3. Open the application. Find its **Bot** page.
4. Create a bot user if the portal prompts you. Keep it private; don't enable public bot distribution.
5. On the Bot page, use the **Reset Token** / token-creation option to obtain the bot token when available.
6. **Do not paste that token into chat, GitHub, Discord, screenshots, or the APEX README.** It belongs in the Pi's local `.env` file later.
7. You may leave **Message Content Intent OFF** for this starter because it uses slash commands, not reading ordinary chat messages. Avoid enabling privileged intents without a concrete need.

If Discord has changed the labels, follow the portal's Bot and Installation sections and review https://discord.com/developers/docs/quick-start/getting-started.

## C. Invite the bot to your private server

1. In the Developer Portal open the application **Installation** page (or OAuth2 URL Generator if shown).
2. Set install target to **Guild/Server**, not public user install.
3. Request scopes **bot** and **applications.commands**.
4. Choose minimal bot permissions: **View Channels**, **Send Messages**, **Embed Links**; make sure members are allowed to use application commands.
5. Copy the generated install/invite URL, open it in your browser, and select YOUR server.
6. Authorize installation.
7. Verify that your bot appears in the server member list (it can show Offline until your Pi starts it).

**Do not grant Administrator permissions** to the bot.

## D. Find your Discord server (Guild) ID

1. Open Discord user settings and enable **Developer Mode** (often under Advanced).
2. Right-click your server name (or long-press on mobile), choose **Copy Server ID**.
3. Save this numeric ID for the next setup step. An ID is not a secret, but do not broadcast unnecessary identifiers.

## E. Store token safely on the Raspberry Pi

Guide 05 shows how to create `raspberry-pi/bot/.env` and fill in the token and server ID. The provided .gitignore prevents this file from being committed in normal Git use.

## F. How your members will interact

In any channel where APEX's slash commands are enabled, each invited user can type:

- `/apex_health` — check that the bot is connected.
- `/apex_entry ticker:NVDA price:181.25 shares:20 signal_id:DEMO-001` — record a personal paper-position entry.
- `/apex_positions` — see only that user's positions.
- `/apex_remove position_id:1` — delete an entry owned by that user.

Discord auto-completes slash command field names; users do not have to type the entire example verbatim.

**Privacy:** The starter replies are *ephemeral* (visible only to the person who invoked them). Never post filled trade prices/share counts in a public Discord thread. The Pi administrator who controls the SQLite file can still read the data on disk. Full production privacy/security and remote dashboard synchronization must be implemented later.

## G. The reply-to-alert feature: important nuance

Discord **ordinary text replies are visible to everyone with access to that channel**, even if a bot deletes them later. For private entry tracking, the recommended production UX is a **Record My Entry** button → an ephemeral Discord modal; slash commands are the starter equivalent.

Plain `Bought 20 @ 181.25` messages are intentionally **not parsed** by this starter. Enabling them in a later release requires Message Content intent, validation that the reply references a genuine APEX signal, opt-in privacy warnings, and explicit tests.

## What to do next

[05 — Install and test the starter bot](05_INSTALL_BOT.md)

Official docs:
- https://discord.com/developers/docs/interactions/application-commands
- https://support-dev.discord.com/hc/en-us/articles/6207308062871-What-are-Privileged-Intents