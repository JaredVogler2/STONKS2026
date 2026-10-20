# 01 — Amazon shopping list (United States)

**Updated:** October 20, 2026. These are **exact compatibility specifications** with Amazon SEARCH links, not fake verified ASINs or guaranteed live Amazon offers. Compare the result you choose to the checklist here BEFORE buying. Prices change. Check the checkout total and seller reputation.

## Recommended: buy these essentials

| # | Quantity | Search Amazon for | Exact requirement | Why it matters |
|---|---:|---|---|---|
| 1 | 1 | [Raspberry Pi 5 4GB board](https://www.amazon.com/s?k=raspberry+pi+5+4gb+board) | Genuine **Raspberry Pi 5, 4 GB RAM**, not Raspberry Pi 4; board only if buying parts separately | Your 24/7 computer |
| 2 | 1 | [Official Raspberry Pi 5 case with fan](https://www.amazon.com/s?k=official+raspberry+pi+5+case+fan) | **Raspberry Pi Case for Raspberry Pi 5**, fan and included heat sink; choose US listing | Protects and cools the Pi |
| 3 | 1 | [Official Raspberry Pi 27W USB-C power supply US](https://www.amazon.com/s?k=official+raspberry+pi+27w+usb+c+power+supply+us) | Official **27W / 5.1V 5A** power supply, US plug | Reliable, continuous operation |
| 4 | 1 | [SanDisk Extreme 128GB microSDXC A2](https://www.amazon.com/s?k=SanDisk+Extreme+128GB+microSDXC+A2+U3) | Genuine **microSDXC**, 64 or 128 GB, A1/A2; 128 GB is comfortable | Holds Linux, bot, logs and temporary DB |
| 5 | 1 if necessary | [USB microSD reader](https://www.amazon.com/s?k=usb+micro+sd+card+reader+usb+a+usb+c) | Fits your laptop's USB-A or USB-C port and accepts **microSD** | Allows OS installation from another computer |
| 6 | 1 (recommended) | [Cat6 Ethernet cable 6 ft](https://www.amazon.com/s?k=cat6+ethernet+cable+6ft) | Standard RJ45 Ethernet cable, enough length to reach router | More reliable than Wi-Fi |

## Optional, NOT required for this headless bot

| Product | Amazon search | When to buy |
|---|---|---|
| Pi 5 8 GB instead of 4 GB | [8GB board](https://www.amazon.com/s?k=raspberry+pi+5+8gb+board) | Only if planning heavy extra local applications; otherwise save money |
| USB-C UPS / purpose-built Pi UPS | [Pi 5 UPS](https://www.amazon.com/s?k=raspberry+pi+5+ups) | Only if local power interruptions are frequent; confirm Pi 5 5V/5A compatibility and safe shutdown |
| Micro-HDMI to HDMI cable | [Micro HDMI cable](https://www.amazon.com/s?k=raspberry+pi+5+micro+hdmi+to+hdmi+cable) | Troubleshooting locally with an HDMI monitor/TV |
| USB keyboard + USB mouse | [USB keyboard mouse](https://www.amazon.com/s?k=usb+keyboard+mouse+combo) | Only if using a local display |
| Larger SSD + adapter | [Pi 5 NVMe HAT](https://www.amazon.com/s?k=raspberry+pi+5+nvme+hat) | Later when database size or SD-card endurance makes it worthwhile |
| Spare identical microSD card | [128GB microSD](https://www.amazon.com/s?k=SanDisk+Extreme+128GB+microSDXC+A2+U3) | Useful for an emergency recovery card |

## Do NOT buy for this project

- AI HAT / Hailo accelerator: not needed for a Discord bot or cloud-based statistical models.
- Pi 4 case: does NOT fit Pi 5.
- Generic phone USB-C charger unless confirmed it supplies the necessary Pi 5 5V/5A profile.
- Separate active cooler **in addition to** the official fan case; you do not need two coolers.
- A monitor, mouse, keyboard or micro-HDMI cable unless you want local desktop troubleshooting.
- A complete kit **and** all the separate parts — that creates duplicates.

## Alternative: one-box kit

Search [Amazon Raspberry Pi 5 4GB starter kit official 27W fan](https://www.amazon.com/s?k=raspberry+pi+5+4gb+starter+kit+official+27w+fan).

Only select a kit if it clearly includes ALL of:
1. Genuine Pi 5 **4 GB**
2. **Pi 5-compatible** case with built-in fan or another compatible cooling solution
3. **27 W Pi 5-rated US USB-C power supply**, ideally official
4. Branded microSD card **64 GB minimum**, ideally 128 GB
5. MicroSD reader (or you already own one)

Kits vary widely. Do not assume that a photographed accessory is included.

## Honest budget / retailer reference

At the time of research, independent approved reseller PiShop US listed:
- Pi 5 **4 GB board: $110**
- Pi 5 **8 GB board: $175**
- Pi 5 **4 GB starter kit: $162.95** (inspect actual kit contents)

Sources:
- https://www.pishop.us/product-category/raspberry-pi/raspberry-pi-5/raspberry-pi-5-boards/
- https://www.pishop.us/product-category/raspberry-pi/raspberry-pi-5/raspberry-pi-5-kits/

**Planning total:** Around **$155–$210 before tax/shipping** for a complete new 4 GB setup, depending on what the kit includes, the seller, and the SD card. **These are not live Amazon price quotes.** If Amazon's price is inflated, compare https://www.pishop.us/ or the approved reseller links at https://www.raspberrypi.com/products/raspberry-pi-5/.

## Official hardware compatibility sources

- Pi 5 board: https://www.raspberrypi.com/products/raspberry-pi-5/
- Official Pi 5 case, with integrated fan and heatsink: https://www.raspberrypi.com/products/raspberry-pi-5-case/
- Power recommendations: https://www.raspberrypi.com/documentation/computers/getting-started.html

## Before checking out — final checklist

- [ ] Every part is explicitly compatible with **Raspberry Pi 5**
- [ ] Case includes fan, and I did **not** add an extra cooler
- [ ] Power supply is 27 W 5.1V 5A rated, US plug
- [ ] I have a microSD card AND way to insert it in a Windows/Mac/Linux computer
- [ ] I have an Ethernet cable or reliable home Wi-Fi
- [ ] I did not accidentally order parts twice because a starter kit already includes them

**If you already own a Pi 5 (including one fitted with an AI HAT):** reuse what you own; no need to buy a second Pi. Check case, HAT and cooler clearance; simplest is to run without the optional HAT for this bot. Do not remove a mounted active cooler casually.