#!/usr/bin/env python3
"""APEX Raspberry Pi Discord connectivity and private position-entry demo.

This does NOT read live market data, generate signals, or place orders.
"""
from __future__ import annotations

import logging
import os
import re
from decimal import Decimal
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from position_store import initialize, add_position, get_positions, delete_position

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
DATABASE = BASE / "data" / "positions.sqlite3"
TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("apex-pi")

if not TOKEN:
    raise SystemExit("Missing DISCORD_BOT_TOKEN in raspberry-pi/bot/.env")
if not GUILD_ID.isdecimal():
    raise SystemExit("DISCORD_GUILD_ID must be the numeric Discord server ID")

GUILD = discord.Object(id=int(GUILD_ID))
initialize(DATABASE)

class ApexBot(commands.Bot):
    async def setup_hook(self):
        synced = await self.tree.sync(guild=GUILD)
        logger.info("Registered %d guild slash commands", len(synced))

intents = discord.Intents.default()
# No privileged Message Content intent is required for slash commands.
bot = ApexBot(command_prefix=commands.when_mentioned, intents=intents)

@bot.event
async def on_ready():
    logger.info("APEX starter bot connected: %s", bot.user)

@bot.tree.command(name="apex_health", description="Check if the APEX Pi bot is online", guild=GUILD)
async def apex_health(interaction: discord.Interaction):
    await interaction.response.send_message(
        "✅ APEX Raspberry Pi bot is online. This is a connectivity test, NOT live market monitoring.",
        ephemeral=True,
    )

@bot.tree.command(name="apex_entry", description="Privately save YOUR own paper-position entry", guild=GUILD)
@app_commands.describe(
    ticker="Stock ticker such as NVDA",
    price="Your actual entry price in USD (positive)",
    shares="Number of shares (positive; fractions allowed)",
    signal_id="Optional APEX signal identifier, or DEMO-001 for testing"
)
async def apex_entry(interaction: discord.Interaction, ticker: str, price: float,
                     shares: float, signal_id: str = ""):
    ticker = ticker.upper().strip()
    signal_id = signal_id.strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", ticker):
        await interaction.response.send_message("Enter a valid stock ticker (1–10 characters).", ephemeral=True)
        return
    if len(signal_id) > 80:
        await interaction.response.send_message("Signal ID is too long (80-character maximum).", ephemeral=True)
        return
    entry_price, quantity = Decimal(str(price)), Decimal(str(shares))
    if not entry_price.is_finite() or not quantity.is_finite() or entry_price <= 0 or quantity <= 0:
        await interaction.response.send_message("Price and shares must be positive finite numbers.", ephemeral=True)
        return
    if entry_price > Decimal("10000000") or quantity > Decimal("100000000"):
        await interaction.response.send_message("Values exceed this starter's safety limits.", ephemeral=True)
        return
    position_id = add_position(DATABASE, interaction.user.id, ticker, entry_price, quantity, signal_id)
    await interaction.response.send_message(
        "✅ Saved private **DEMO** position #" + str(position_id) + ": **" + ticker
        + "**, " + str(quantity) + " shares at $" + str(entry_price)
        + ". No brokerage connection, current price monitoring, or trade execution is active.",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )

@bot.tree.command(name="apex_positions", description="Privately list only YOUR saved demo entries", guild=GUILD)
async def apex_positions(interaction: discord.Interaction):
    rows = get_positions(DATABASE, interaction.user.id)
    if not rows:
        await interaction.response.send_message("You have no saved demo positions.", ephemeral=True)
        return
    lines = ["**Your most recent demo entries (up to 25):**"]
    for position_id, ticker, price, shares, signal_id, _date in rows:
        label = signal_id if signal_id else "no signal ID"
        lines.append("#" + str(position_id) + " **" + ticker + "** — " + str(shares)
                     + " shares at $" + str(price) + " (" + label + ")")
    # Discord has a message length limit, and ephemerality is preserved.
    await interaction.response.send_message(
        "\n".join(lines)[:1900], ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none()
    )

@bot.tree.command(name="apex_remove", description="Delete one of YOUR stored demo entries", guild=GUILD)
@app_commands.describe(position_id="Position number displayed by /apex_positions")
async def apex_remove(interaction: discord.Interaction, position_id: int):
    removed = delete_position(DATABASE, interaction.user.id, position_id)
    await interaction.response.send_message(
        "✅ Removed your demo entry." if removed else "No matching entry owned by you.",
        ephemeral=True
    )

if __name__ == "__main__":
    # Never print the bot token to logs or commit it to version control.
    bot.run(TOKEN, log_handler=None)
