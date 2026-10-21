"""Paired-device identity (`<state_dir>/agent.json`, owner-only 0600).

Holds the agent id the Pi generated at pairing time, the dashboard URL and the key fingerprint the owner compared.
It contains NO secrets (the device private keys live in device_keys.json; the shared HMAC bootstrap secret stays in
the owner-only .env). Environment variables (APEX_AGENT_ID, APEX_API_BASE_URL) override the file.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

IDENTITY_FILE = "agent.json"
AGENT_ID_RE = re.compile(r"^agt_[a-z0-9]{8,32}$")


def new_agent_id() -> str:
    return "agt_" + secrets.token_hex(8)


@dataclass
class AgentIdentity:
    agent_id: str
    server: str
    device_name: str
    fingerprint: str
    paired_at: str
    status: str = "pending_approval"
    v: int = 1

    def save(self, state_dir: Path) -> Path:
        state_dir.mkdir(parents=True, exist_ok=True)
        path = state_dir / IDENTITY_FILE
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(asdict(self), fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, state_dir: Path) -> AgentIdentity | None:
        path = state_dir / IDENTITY_FILE
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            ident = cls(**{k: data[k] for k in ("agent_id", "server", "device_name", "fingerprint", "paired_at")},
                        status=str(data.get("status", "pending_approval")))
        except (KeyError, TypeError, ValueError):
            return None
        return ident if AGENT_ID_RE.fullmatch(ident.agent_id) else None
