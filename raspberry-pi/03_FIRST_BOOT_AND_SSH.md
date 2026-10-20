# 03 — Install Raspberry Pi OS and connect from your usual computer

This document assumes you have **a Windows, Mac or Linux computer** and the microSD reader from the shopping list. You do NOT need to know Linux beforehand.

## Part A — Before putting the microSD into the Pi

### A1. Install Raspberry Pi Imager

On your ordinary laptop/desktop:
1. Open **https://www.raspberrypi.com/software/**
2. Download Raspberry Pi Imager for your Windows/Mac/Linux computer.
3. Install it using the normal prompts.
4. Connect the microSD card using its reader. IMPORTANT: you are about to **erase the microSD**, not your laptop disk.

### A2. Select correct device and OS

In Raspberry Pi Imager:
1. Device: **Raspberry Pi 5**.
2. Operating system: **Raspberry Pi OS Lite (64-bit)**, current supported release. "Lite" means it doesn't load a desktop; this is intentional.
3. Storage: choose your microSD card by its displayed size. DOUBLE-CHECK you are not overwriting another device.

If Imager's menus look different, look for the same **device / OS / storage / OS customization** steps.

### A3. Customize settings BEFORE writing

Follow Imager's OS Customisation wizard. Enter:

| Setting | Recommended value |
|---|---|
| Device hostname | `apex-pi` |
| Username | `apex` (lowercase) |
| Password | a unique long password you save securely; **do not put it in GitHub** |
| Locale | US; timezone `America/New_York` |
| Wi-Fi | your normal home network name and password (even if you plan Ethernet, optional backup) |
| Remote Access / SSH | **Enable SSH** |
| SSH authentication | **Password authentication** for easy first connection; upgrade to SSH keys later |

Enable SSH now; otherwise a headless Pi can be hard to reach.

### A4. Write and verify

1. Confirm the erase/warning message is only for the intended microSD card.
2. Select **Write**; wait for completion and verification.
3. Safely eject the card.
4. Return to [02 — Assemble the hardware](02_ASSEMBLE_HARDWARE.md), starting at Step 3. Install the card and power the Pi.
5. Allow a few minutes for first boot.

## Part B — Connect from your regular computer

### B1. Open a command window

- **Windows 11:** press Start, search for **Windows Terminal** or **PowerShell**, open it.
- **Mac:** open the built-in **Terminal** app.
- **Linux:** open Terminal.

Type the next line exactly, then press Enter:

~~~bash
ssh apex@apex-pi.local
~~~

If your username or hostname differs, replace `apex` and/or `apex-pi` accordingly.

The first time, the computer may ask whether to trust this SSH host. Confirm ONLY if it is your Pi on your local home network. Enter your Pi password when prompted. The password usually shows **no characters** while typing — this is normal.

### B2. If `apex-pi.local` doesn't resolve

1. Confirm Pi has power and is connected to the same home network as your computer.
2. If using Ethernet, check both ends of the cable.
3. Find the Pi IP address in your router's connected-devices list (often called "apex-pi").
4. Connect by IP address, for example:

~~~bash
ssh apex@192.168.1.123
~~~

The IP address above is **an example**; use the IP you actually find.

Windows and Macs can use SSH without purchasing extra software.

### B3. Update Raspberry Pi OS

Once you see a prompt similar to `apex@apex-pi:~ $`, enter each command:

~~~bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot
~~~

After reboot, your connection will close. Allow the Pi to come back online, then SSH in again:

~~~bash
ssh apex@apex-pi.local
~~~

### B4. Install required utilities

~~~bash
sudo apt install -y git python3 python3-venv python3-pip sqlite3
~~~

Verify:

~~~bash
uname -m
python3 --version
hostname
vcgencmd measure_temp
~~~

Expected: CPU architecture is **aarch64** (64-bit), hostname is `apex-pi`, Python 3 is installed, and temperature is reported. Specific temperatures vary; if overheating or throttling, check the fan and case.

### B5. What "SSH" means

SSH is a secure command window into your Pi. You can close your laptop and the Pi keeps running once a system service is configured in Guide 05.

Do NOT open router port 22, expose SSH to the public internet, or install remote-control software for this project. Your Pi can reach Discord over its **outbound** internet connection.

**Checkpoint:** You can log in over SSH, update the operating system and run `python3 --version`. Next: [04 — Create your private Discord bot](04_DISCORD_CONFIGURATION.md).

### Official reference
https://www.raspberrypi.com/documentation/computers/getting-started.html