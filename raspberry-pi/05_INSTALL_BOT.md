# 05 — Install, launch, and auto-start the working Discord starter bot

Complete [03 — First boot](03_FIRST_BOOT_AND_SSH.md) and [04 — Discord configuration](04_DISCORD_CONFIGURATION.md) first.

**Expected result:** Discord slash commands work for multiple users, but each user only sees their own saved demo position entries. This **is not yet a price-tracking or financial advisory app**.

## Part 1 — Connect to your Pi

On your normal computer, open Terminal or PowerShell and type:

~~~bash
ssh apex@apex-pi.local
~~~

Everything below happens INSIDE the Pi terminal (look for `apex@apex-pi` in the prompt).

## Part 2 — Give your Pi read-only access to the PRIVATE GitHub repo

Your repo is private. To download it without copying a personal GitHub password or token onto the Pi, use a **read-only repository Deploy Key**.

### 2A — Create the key on the Pi

~~~bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
ssh-keygen -t ed25519 -f ~/.ssh/apex_stonks_deploy -C "apex-pi-readonly"
~~~

You may be asked for a passphrase. A passphrase protects the key if the SD card is stolen; you will type it when manually pulling updates. Don't overwrite an existing file if prompted.

### 2B — Copy the PUBLIC key

~~~bash
cat ~/.ssh/apex_stonks_deploy.pub
~~~

You'll see one long line beginning with `ssh-ed25519`. Copy **that public line only**. NEVER copy/share the similarly named file WITHOUT `.pub`.

### 2C — Add the key in GitHub's website

1. On your computer, go to https://github.com/JaredVogler2/STONKS2026 .
2. Open **Settings → Deploy keys → Add deploy key** (requires repo administration privileges).
3. Title: `APEX Raspberry Pi (read-only)`.
4. Paste ONLY the public key from 2B.
5. Leave **Allow write access UNCHECKED**.
6. Save.

### 2D — Tell SSH which key to use

Run this in the Pi terminal (it doesn't reveal or copy the private key):

~~~bash
cat >> ~/.ssh/config <<'EOF'
Host github-apex
    HostName github.com
    User git
    IdentityFile ~/.ssh/apex_stonks_deploy
    IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
~~~

Test:

~~~bash
ssh -T git@github-apex
~~~

The first time, SSH will ask to trust the GitHub host key. Compare the shown fingerprint with the official list at https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints BEFORE accepting. GitHub usually replies that you authenticated but shell access isn't provided. **That is success.** The command may exit with code 1 despite a successful greeting.

### 2E — Download the repository

~~~bash
cd ~
git clone git@github-apex:JaredVogler2/STONKS2026.git
cd ~/STONKS2026/raspberry-pi/bot
~~~

You should now see files including `apex_bot.py` and `requirements.txt`.

## Part 3 — Install Python libraries (no subscription)

In the Pi terminal:

~~~bash
cd ~/STONKS2026/raspberry-pi/bot
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
~~~

Run the included offline database tests:

~~~bash
.venv/bin/python -m unittest discover -s tests -v
~~~

Expected: two tests pass (including checking users cannot delete someone else's entries).

## Part 4 — Add YOUR secret Discord settings

~~~bash
cp .env.example .env
nano .env
~~~

In the nano editor, replace:
- `REPLACE_WITH_PRIVATE_DISCORD_BOT_TOKEN` with your actual secret bot token from Discord Developer Portal.
- `REPLACE_WITH_NUMERIC_PRIVATE_SERVER_ID` with your numeric Discord Server ID.

Do not add quotation marks or spaces around the equals sign.

Save and exit nano: **Ctrl+O**, then **Enter**, then **Ctrl+X**.

Lock the file to your Pi login:

~~~bash
chmod 600 .env
~~~

**Never send anyone your token. Do not screenshot your .env file or run cat on it while screen-sharing.**

## Part 5 — Test the bot manually

~~~bash
.venv/bin/python apex_bot.py
~~~

Expected messages include `Registered 4 guild slash commands` and `APEX starter bot connected`.

On your iPhone or computer, open your private Discord server and type `/apex_health`. You should see a private "online" reply.

Try:

1. `/apex_entry` — fill ticker `NVDA`, price `181.25`, shares `20`, optional signal ID `DEMO-001`.
2. `/apex_positions` — see your own entry.
3. Ask another server member to enter their own test position; verify they do NOT see yours.
4. `/apex_remove` — remove your test position by its number.

Use Ctrl+C in the Pi terminal to stop manual operation. This is expected: the bot will temporarily go offline.

## Part 6 — Make the bot run ALL THE TIME

Still inside the Pi terminal:

~~~bash
cd ~/STONKS2026/raspberry-pi/bot
bash scripts/install_service.sh
~~~

This installs a systemd service and turns on **automatic startup after reboot**, so your ordinary computer can be turned off.

Check status:

~~~bash
sudo systemctl status apex-pi-bot --no-pager
~~~

Look for `active (running)`. Press `q` to exit if the command enters a pager.

Go back to Discord and run `/apex_health` again.

## Part 7 — Confirm it survives a reboot

~~~bash
sudo reboot
~~~

Wait for the Pi to reconnect, then:

~~~bash
ssh apex@apex-pi.local
sudo systemctl status apex-pi-bot --no-pager
~~~

Try `/apex_health` again. If it succeeds without your laptop terminal staying open, you have a working 24/7 Discord bot.

## Part 8 — Invite more users

Invite trusted friends into the private server. Each person can run the slash commands; their entries are associated with their Discord user ID and separated by database ownership checks.

**Limitations:** Anyone with physical/admin access to the Pi and its unencrypted local database can access its contents. Shared alerts, market monitoring, position updates, target probabilities, risk-adjusted exits, and Vercel synchronization are later APEX phases. See [07 — Architecture](07_SECURITY_AND_INTEGRATION.md).

## Getting updated code later

~~~bash
cd ~/STONKS2026
git pull --ff-only
cd raspberry-pi/bot
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
sudo systemctl restart apex-pi-bot
~~~

Do this only after reviewing changes; updating application dependencies or code can change its behavior.

**Next:** [06 — Operating and troubleshooting](06_OPERATIONS_TROUBLESHOOTING.md).