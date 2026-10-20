# 02 — Assemble the Raspberry Pi (no prior hardware experience needed)

**Goal:** Assemble the board, cooling case, storage and network without soldering. The Pi will run **headless**: no monitor or keyboard attached.

## What the parts look like

- **Raspberry Pi 5:** small green circuit board, approximately credit-card sized. USB, Ethernet, micro-HDMI and USB-C ports around the edges.
- **Official Pi 5 case:** plastic enclosure containing a small fan, tiny heatsink, and usually rubber feet.
- **microSD:** thumbnail-sized memory card. This is your Pi's equivalent of a hard drive.
- **27 W official power supply:** a wall plug with a USB-C cable.
- **Ethernet cable (optional):** plugs into the large network socket on the Pi and your router.

Official pictures and case specification:
- https://www.raspberrypi.com/products/raspberry-pi-5/
- https://www.raspberrypi.com/products/raspberry-pi-5-case/

## Step 1 — Don't power it on yet

1. Put parts on a clean, dry wooden or plastic table; avoid metal surfaces and static-prone carpets.
2. Unplug the power adapter; NEVER assemble with power connected.
3. Open the case box and keep every part together. No tools are normally needed for the clip-together official case.
4. Inspect the board for visibly bent connectors or damage. Hold it by the edges.

## Step 2 — Install the operating system on the microSD FIRST

**STOP HERE and complete [03 — Install OS and first boot](03_FIRST_BOOT_AND_SSH.md), Part A (write the card).** It is much easier to prepare the microSD on your everyday computer before enclosing the Pi.

Then return to Step 3 below.

## Step 3 — Insert the microSD card

1. Find the **microSD slot underneath** the Pi 5 board.
2. Insert the newly prepared card gently, following the shape of the slot. The metal contacts must align with the socket; don't force it.
3. Make sure it is seated properly.

If it feels stuck or does not seat, stop and check orientation; forcing the card can damage the slot.

## Step 4 — Fit the official Pi 5 case and cooling fan

1. Identify the case base, side frame and lid. Keep the fan's thin cable clear of clips.
2. If your official case includes the small self-adhesive heatsink: remove its protective film and place it squarely onto the **large main processor chip**, per the case's included fitting diagram. Avoid neighboring components.
3. Lay the board into the case base, aligning the ports with their cutouts. **Do not push against connectors or force anything.**
4. The official fan has a tiny 4-wire plug. Connect it gently to the **dedicated fan socket on the Pi 5** (not the long 40-pin GPIO header). The connector is keyed; no force should be needed.
5. Route the fan cable so it cannot touch the fan blades.
6. Clip the case parts together as illustrated in the case packaging, checking that nothing is pinched. Add the included rubber feet.

**If using a different case:** Follow THAT case's instructions. A third-party case with separate active cooler may have different assembly requirements. Do not assume all cases fit over an active cooler or AI HAT.

## Step 5 — Connect network

**Best:** Ethernet cable from Pi's Ethernet socket to an available LAN port on your home router.  
**Alternative:** Home Wi-Fi, entered in Raspberry Pi Imager while preparing the card.

Do not connect the Pi directly to a wall telephone socket.

## Step 6 — Power it on

1. Make sure the microSD is inserted.
2. Plug in Ethernet (if using).
3. Plug the **official 27 W USB-C adapter** into a wall outlet.
4. Connect USB-C to the Pi's power socket.
5. The indicator LEDs should show activity; don't worry if the fan does not spin continuously, as fan speed responds to temperature.
6. Leave it connected for a few minutes for first-boot initialization.

## Step 7 — Continue the software setup

Return to [03 — Install OS and first boot](03_FIRST_BOOT_AND_SSH.md), Part B. You'll control it from your regular computer.

## Safe handling rules

- Never connect/disconnect internal fan connectors or rearrange a Pi HAT while powered.
- Do not put the bare board on conductive metal.
- Allow airflow around the case; don't bury it under clothing.
- For a normal shutdown use `sudo shutdown -h now` over SSH. Wait until activity stops before unplugging.
- Power outages can corrupt a microSD card; keep backups.
- A Pi 5 has a power button on the board, but that is NOT a substitute for a reliable power supply or battery backup.

**Checkpoint:** The Pi is assembled with fan connected, SD inserted, network arranged and official USB-C power. No soldering, cables inside a PC chassis or monitor are required.