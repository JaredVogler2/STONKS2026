"""Bot runtime settings, single-instance guard, legacy-starter overlap check and secret-masking log filter.

Settings come from the environment first, then the owner-only .env file WorkerConfig found:
  APEX_CHANNEL_MAP            JSON {channel_key: channel_id}; else <state_dir>/discord_channels.json
  APEX_LOCAL_DB               local SQLite path (default <state_dir>/apex_local.sqlite3)
  APEX_ALLOW_LEGACY_OVERLAP   1 to start even though systemd unit `apex-pi-bot` (the starter) is active
  APEX_DISCORD_MEMBERS_INTENT 1 only if you enabled the privileged Server Members intent (instant revocation)
  APEX_OUTBOX_POLL_SECONDS    delivery poll interval (default 5)
"""

from __future__ import annotations

import fcntl
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from apex_worker.config import WorkerConfig

from .delivery import ChannelMap

LOCAL_DB_NAME = "apex_local.sqlite3"
CHANNEL_FILE_NAME = "discord_channels.json"
LOCK_NAME = "discord_bot.lock"
LEGACY_UNIT = "apex-pi-bot"
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{27,40}")
TRUE = {"1", "true", "yes", "on"}


def _values(cfg: WorkerConfig, environ: dict[str, str] | None) -> dict[str, str]:
    vals: dict[str, str] = {}
    if cfg.env_file and Path(cfg.env_file).exists():
        vals.update({k: v for k, v in dotenv_values(cfg.env_file).items() if v is not None})
    vals.update(dict(os.environ if environ is None else environ))
    return vals


@dataclass
class BotSettings:
    channel_map: ChannelMap
    channel_map_error: str | None
    allow_legacy_overlap: bool
    members_intent: bool
    poll_seconds: float
    local_db: Path
    state_dir: Path

    @classmethod
    def load(cls, cfg: WorkerConfig, environ: dict[str, str] | None = None) -> BotSettings:
        v = _values(cfg, environ)
        state = Path(cfg.state_dir)
        cmap, err = ChannelMap(), None
        try:
            if v.get("APEX_CHANNEL_MAP", "").strip():
                cmap = ChannelMap.parse(v["APEX_CHANNEL_MAP"], "APEX_CHANNEL_MAP")
            elif (state / CHANNEL_FILE_NAME).exists():
                cmap = ChannelMap.from_file(state / CHANNEL_FILE_NAME)
        except (ValueError, OSError) as exc:
            err = str(exc)
        try:
            poll = max(1.0, float(v.get("APEX_OUTBOX_POLL_SECONDS") or 5))
        except ValueError:
            poll = 5.0
        return cls(channel_map=cmap, channel_map_error=err,
                   allow_legacy_overlap=v.get("APEX_ALLOW_LEGACY_OVERLAP", "").strip().lower() in TRUE,
                   members_intent=v.get("APEX_DISCORD_MEMBERS_INTENT", "").strip().lower() in TRUE,
                   poll_seconds=poll, local_db=Path(v.get("APEX_LOCAL_DB") or state / LOCAL_DB_NAME), state_dir=state)

    def problems(self, cfg: WorkerConfig) -> list[str]:
        out = []
        if not cfg.discord_token:
            out.append("DISCORD_BOT_TOKEN is not set (put it in the owner-only .env, chmod 600; never commit it).")
        gid = cfg.discord_guild_id or ""
        if not (gid.isdigit() and 15 <= len(gid) <= 21):
            out.append("DISCORD_GUILD_ID must be the numeric ID of your private server (Developer Mode > Copy Server ID).")
        if self.channel_map_error:
            out.append(f"Channel map is invalid: {self.channel_map_error}")
        return out


# ---- single instance ---------------------------------------------------------------------------------------------
class AlreadyRunning(RuntimeError):
    pass


@dataclass
class InstanceLock:
    path: Path
    fd: int | None

    def release(self) -> None:
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None

    def __enter__(self) -> InstanceLock:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def acquire_instance_lock(state_dir: Path, name: str = LOCK_NAME) -> InstanceLock:
    """Exclusive, non-blocking flock: a second bot process (same state dir / same token) refuses to start."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / name
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        raise AlreadyRunning(f"another APEX Discord bot already holds {path}. Stop it first "
                             "(check `ps aux | grep apex` or your systemd units) - two processes with one token would answer every "
                             "interaction twice.") from exc
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return InstanceLock(path, fd)


def legacy_service_active(runner: Callable[..., subprocess.CompletedProcess] | None = None,
                          which: Callable[[str], str | None] = shutil.which) -> bool | None:
    """True if the starter's systemd unit is active; None when systemd is absent or the check fails."""
    exe = which("systemctl")
    if not exe:
        return None
    run = runner or subprocess.run
    try:
        out = run([exe, "is-active", LEGACY_UNIT], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return (out.stdout or "").strip() in ("active", "activating", "reloading")


LEGACY_MESSAGE = (
    "The starter bot service 'apex-pi-bot' appears to be ACTIVE. Running both bots with the same Discord token makes "
    "every interaction race between two processes.\n"
    "  1) Import starter entries:  apex migrate-starter            (dry run)  then  apex migrate-starter --apply\n"
    "  2) Stop the starter:        sudo systemctl disable --now apex-pi-bot\n"
    "  3) Start this bot again.    (Override at your own risk: APEX_ALLOW_LEGACY_OVERLAP=1)"
)


# ---- logging -----------------------------------------------------------------------------------------------------
class SecretMaskingFilter(logging.Filter):
    """Masks configured secrets and anything shaped like a Discord bot token in every log record."""

    def __init__(self, secrets: Iterable[str | None] = ()):
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) >= 8]

    def _mask(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, "***")
        return TOKEN_RE.sub("***", text)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        masked = self._mask(msg)
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self._mask(record.exc_text)
        if masked != msg or record.args:
            record.msg, record.args = masked, ()
        return True


def masked_handler(secrets: Iterable[str | None]) -> logging.Handler:
    h = logging.StreamHandler()
    h.addFilter(SecretMaskingFilter(secrets))
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    return h
