"""Worker configuration from environment / owner-only .env. Secrets are held in memory and never logged."""

from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_STATE = Path(os.environ.get("APEX_STATE_DIR", Path.home() / ".apex"))


def _b64(value: str | None) -> bytes | None:
    if not value:
        return None
    try:
        raw = base64.b64decode(value.strip(), validate=True)
    except ValueError:
        return None
    return raw if len(raw) >= 32 else None


@dataclass(repr=False)
class WorkerConfig:
    release_mode: str = "DEMO"
    state_dir: Path = DEFAULT_STATE
    env_file: Path | None = None
    discord_token: str | None = None
    discord_guild_id: str | None = None
    owner_discord_ids: tuple[str, ...] = ()
    api_base_url: str | None = None
    agent_id: str | None = None
    agent_hmac_secret: bytes | None = None
    vercel_bypass_secret: str | None = None
    alpaca_key_id: str | None = None
    alpaca_secret_key: str | None = None
    sec_user_agent: str | None = None
    model_trusted_keys: dict[int, str] = field(default_factory=dict)
    component_secret: bytes | None = None

    def __repr__(self) -> str:  # never leak secrets via logging/tracebacks
        return (
            f"WorkerConfig(mode={self.release_mode}, state_dir={self.state_dir}, api={'set' if self.api_base_url else 'unset'}, "
            f"discord={'set' if self.discord_token else 'unset'})"
        )

    def has_market_credentials(self) -> bool:
        return bool(self.alpaca_key_id and self.alpaca_secret_key)

    @classmethod
    def load(cls, env_file: Path | None = None) -> WorkerConfig:
        values: dict[str, str | None] = {}
        path = env_file or Path(os.environ.get("APEX_ENV_FILE", Path.cwd() / ".env"))
        if path.exists():
            values.update(dotenv_values(path))
        values.update(
            {k: v for k, v in os.environ.items() if k.startswith(("APEX_", "DISCORD_", "AGENT_", "ALPACA_", "SEC_"))}
        )
        keys: dict[int, str] = {}
        raw_keys = values.get("APEX_MODEL_TRUSTED_KEYS")
        if raw_keys:
            try:
                keys = {int(k): str(v) for k, v in json.loads(raw_keys).items()}
            except (ValueError, AttributeError):
                keys = {}
        state = Path(values.get("APEX_STATE_DIR") or DEFAULT_STATE)
        cfg = cls(
            release_mode=(values.get("APEX_MODE") or "DEMO").upper(),
            state_dir=state,
            env_file=path if path.exists() else None,
            discord_token=(values.get("DISCORD_BOT_TOKEN") or "").strip() or None,
            discord_guild_id=(values.get("DISCORD_GUILD_ID") or "").strip() or None,
            owner_discord_ids=tuple(
                x.strip() for x in (values.get("APEX_OWNER_DISCORD_IDS") or "").split(",") if x.strip()
            ),
            api_base_url=(values.get("APEX_API_BASE_URL") or "").rstrip("/") or None,
            agent_id=(values.get("APEX_AGENT_ID") or "").strip() or None,
            agent_hmac_secret=_b64(values.get("AGENT_HMAC_SECRET")),
            vercel_bypass_secret=(values.get("APEX_VERCEL_BYPASS_SECRET") or "").strip() or None,
            alpaca_key_id=(values.get("ALPACA_KEY_ID") or "").strip() or None,
            alpaca_secret_key=(values.get("ALPACA_SECRET_KEY") or "").strip() or None,
            sec_user_agent=(values.get("SEC_USER_AGENT") or "").strip() or None,
            model_trusted_keys=keys,
        )
        cfg.component_secret = load_or_create_component_secret(state)
        return cfg


def load_or_create_component_secret(state_dir: Path) -> bytes:
    """Local secret for signing Discord component custom IDs (never leaves the Pi)."""
    path = state_dir / "component_secret"
    try:
        if path.exists():
            return base64.b64decode(path.read_text().strip())
        state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(state_dir, 0o700)
        raw = secrets.token_bytes(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(base64.b64encode(raw).decode())
        return raw
    except OSError:
        return secrets.token_bytes(32)  # ephemeral fallback: buttons from a previous run will be rejected
