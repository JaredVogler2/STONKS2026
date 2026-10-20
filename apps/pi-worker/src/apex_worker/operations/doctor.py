"""`apex doctor`: safe environment diagnostics with remedial messages. Never prints secret values."""

from __future__ import annotations

import os
import platform
import shutil
import socket
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from apex_worker.config import WorkerConfig


@dataclass
class Check:
    name: str
    status: str  # PASS | WARN | FAIL | SKIP
    detail: str
    fix: str = ""


def _mask_present(value: str | None) -> str:
    return "configured" if value else "not set"


def run_checks(cfg: WorkerConfig, *, network: bool = False) -> list[Check]:
    out: list[Check] = []
    py = sys.version_info
    out.append(
        Check(
            "python",
            "PASS" if py >= (3, 11) else "FAIL",
            f"Python {py.major}.{py.minor}.{py.micro}",
            "Install Python 3.11+ (uv python install 3.12)" if py < (3, 11) else "",
        )
    )
    arch = platform.machine()
    out.append(Check("platform", "PASS", f"{platform.system()} {arch}"))
    try:
        total, _used, free = shutil.disk_usage(cfg.state_dir if cfg.state_dir.exists() else Path.home())
        pct = free / total * 100
        out.append(
            Check(
                "disk",
                "PASS" if pct > 10 else ("WARN" if pct > 5 else "FAIL"),
                f"{pct:.1f}% free",
                "Free disk space: raw bar retention stops below 5% free" if pct <= 10 else "",
            )
        )
    except OSError as exc:
        out.append(Check("disk", "WARN", f"unable to read: {exc}"))
    try:
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        probe = sqlite3.connect(cfg.state_dir / ".doctor.sqlite3")
        probe.execute("PRAGMA journal_mode=WAL").fetchone()
        probe.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
        probe.close()
        (cfg.state_dir / ".doctor.sqlite3").unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            (cfg.state_dir / f".doctor.sqlite3{suffix}").unlink(missing_ok=True)
        out.append(Check("local_sqlite", "PASS", f"WAL spool writable at {cfg.state_dir}"))
    except (OSError, sqlite3.Error) as exc:
        out.append(
            Check(
                "local_sqlite", "FAIL", f"cannot write state dir: {exc}", "Check ownership/permissions of the state dir"
            )
        )
    env_file = cfg.env_file
    if env_file and env_file.exists():
        mode = env_file.stat().st_mode & 0o777
        out.append(
            Check(
                "env_permissions",
                "PASS" if not mode & 0o077 else "FAIL",
                f"{env_file.name} mode {oct(mode)}",
                f"chmod 600 {env_file}" if mode & 0o077 else "",
            )
        )
    try:
        from apex_worker.market.calendar import default_calendar

        cal = default_calendar()
        info = cal.phase(datetime.now(UTC))
        out.append(Check("market_calendar", "PASS", f"XNYS {cal.first_day}..{cal.last_day}; now {info.phase.value}"))
        if cal.last_day.year <= datetime.now(UTC).year:
            out.append(Check("calendar_horizon", "WARN", "calendar ends this year", "Upgrade exchange_calendars"))
    except Exception as exc:
        out.append(Check("market_calendar", "FAIL", f"{type(exc).__name__}: {exc}", "Reinstall dependencies"))
    try:
        from apex_worker.demo.synthetic import generate

        m = generate(symbols=["DMSPY", "DMSEMI1"])
        out.append(Check("mock_feed", "PASS", f"replay fixture OK ({len(m.sessions)} sessions, DEMO data)"))
    except Exception as exc:
        out.append(Check("mock_feed", "FAIL", f"{type(exc).__name__}: {exc}", "Reinstall dependencies"))
    out.append(
        Check(
            "release_mode",
            "PASS"
            if cfg.release_mode in ("DEMO", "PAPER_LIVE_DATA", "PERSONAL_RESEARCH", "GROUP_POSITION_ALERTS")
            else "FAIL",
            cfg.release_mode,
            ""
            if cfg.release_mode in ("DEMO", "PAPER_LIVE_DATA", "PERSONAL_RESEARCH", "GROUP_POSITION_ALERTS")
            else "Set APEX_MODE to DEMO, PAPER_LIVE_DATA or PERSONAL_RESEARCH",
        )
    )
    out.append(
        Check(
            "discord_token",
            "PASS" if cfg.discord_token else ("SKIP" if cfg.release_mode == "DEMO" else "WARN"),
            _mask_present(cfg.discord_token),
            "Add DISCORD_BOT_TOKEN to the Pi .env (never commit it)" if not cfg.discord_token else "",
        )
    )
    out.append(
        Check(
            "discord_guild",
            "PASS" if (cfg.discord_guild_id or "").isdecimal() else "WARN",
            "configured" if (cfg.discord_guild_id or "").isdecimal() else "not set",
            "" if (cfg.discord_guild_id or "").isdecimal() else "Set DISCORD_GUILD_ID (numeric server id)",
        )
    )
    try:
        import discord  # noqa: F401

        out.append(Check("discord_library", "PASS", "discord.py importable (dry-run adapter available)"))
    except ImportError:
        out.append(Check("discord_library", "WARN", "discord.py missing", "uv sync --extra discord"))
    out.append(
        Check(
            "cloud_api",
            "PASS" if cfg.api_base_url and cfg.agent_hmac_secret else "SKIP",
            "configured" if cfg.api_base_url and cfg.agent_hmac_secret else "offline (local spool only)",
            "" if cfg.api_base_url else "Optional: set APEX_API_BASE_URL + AGENT_HMAC_SECRET and run `apex pair`",
        )
    )
    out.append(
        Check(
            "market_credentials",
            "PASS" if cfg.has_market_credentials() else "SKIP",
            "configured" if cfg.has_market_credentials() else "not configured (DEMO replay only)",
            "" if cfg.has_market_credentials() else "Configure Alpaca in the dashboard Settings, then `apex pair`",
        )
    )
    out.append(
        Check(
            "model_trust",
            "PASS" if cfg.model_trusted_keys else "SKIP",
            f"{len(cfg.model_trusted_keys)} trusted model signing key(s)" if cfg.model_trusted_keys else "none",
            "" if cfg.model_trusted_keys else "Set APEX_MODEL_TRUSTED_KEYS to receive cloud-trained models",
        )
    )
    if network:
        for host in ("discord.com", "data.alpaca.markets"):
            try:
                socket.create_connection((host, 443), timeout=4).close()
                out.append(Check(f"net:{host}", "PASS", "reachable on 443"))
            except OSError as exc:
                out.append(Check(f"net:{host}", "WARN", f"unreachable: {type(exc).__name__}", "Check internet/DNS"))
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        out.append(Check("user", "WARN", "running as root", "Run the worker as your normal user (systemd User=)"))
    return out


def render(checks: list[Check]) -> str:
    icon = {"PASS": "[ OK ]", "WARN": "[WARN]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}
    lines = [
        f"{icon[c.status]} {c.name:<20} {c.detail}" + (f"\n         fix: {c.fix}" if c.fix else "") for c in checks
    ]
    fails = sum(c.status == "FAIL" for c in checks)
    lines.append("")
    lines.append("Result: " + ("FAIL - fix the items above" if fails else "OK (no secrets were printed)"))
    return "\n".join(lines)
