"""Provider credentials delivered as device-encrypted envelopes (memory only).

GET /secrets?provider=alpaca returns an X25519-HKDF-SHA256-A256GCM envelope addressed to THIS device's registered
X25519 key (5-minute expiry, metadata bound as AAD). It is opened with the private key from device_keys.json, the
plaintext is parsed into `MarketCredentials` and kept in process memory only: never written to disk, never logged,
never included in heartbeats (only the integer secret version is). The version is acked via POST /secrets/ack.
A newer version announced by the server (heartbeat/config `secret_versions`) or an Alpaca 401/403 triggers a refetch.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from apex_worker.agent.client import AgentClient, AgentHTTPError, AgentUnavailable
from apex_worker.agent.crypto import DeviceKeys, EnvelopeError, open_envelope
from apex_worker.timeutil import iso_utc

log = logging.getLogger("apex.agent.secrets")


@dataclass(frozen=True)
class MarketCredentials:
    key_id: str = field(repr=False)
    secret_key: str = field(repr=False)
    version: int
    source: str  # envelope | manual_env

    def __repr__(self) -> str:
        return f"MarketCredentials(provider=alpaca, version={self.version}, source={self.source}, key=***)"


def announced_versions(*payloads: dict | None) -> dict[str, int]:
    """Secret versions the server announces (`secret_versions: {provider: n}` on heartbeat or config responses)."""
    out: dict[str, int] = {}
    for p in payloads:
        raw = (p or {}).get("secret_versions")
        if isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(v, int) and not isinstance(v, bool):
                    out[str(k)] = max(out.get(str(k), 0), v)
    return out


class SecretManager:
    def __init__(self, client: AgentClient, keys: DeviceKeys, *, provider: str = "alpaca",
                 manual: MarketCredentials | None = None, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 retry_seconds: float = 1800):
        self.client = client
        self.retry_seconds = retry_seconds
        self._last_attempt: datetime | None = None
        self.keys = keys
        self.provider = provider
        self.manual = manual
        self.clock = clock
        self._creds: MarketCredentials | None = None
        self.status = "UNCONFIGURED"
        self.detail = "not fetched yet"

    def __repr__(self) -> str:
        return f"SecretManager(provider={self.provider}, version={self.version}, status={self.status})"

    @property
    def version(self) -> int:
        return self._creds.version if self._creds and self._creds.source == "envelope" else 0

    @property
    def credentials(self) -> MarketCredentials | None:
        return self._creds or self.manual

    def fetch(self) -> MarketCredentials | None:
        """Fetch + open + ack. On any failure the previously held credentials (if any) stay in memory."""
        self._last_attempt = self.clock()
        try:
            env = self.client.get("secrets", {"provider": self.provider}).get("envelope")
            plain = open_envelope(self.keys.x25519, env or {}, expected_device=self.client.agent_id,
                                  now_iso=iso_utc(self.clock().replace(microsecond=0)))
            fields = json.loads(plain)
            creds = MarketCredentials(str(fields["key_id"]), str(fields["secret_key"]),
                                      int(env["meta"]["secret_version"]), "envelope")
        except AgentHTTPError as exc:
            self.status, self.detail = ("UNCONFIGURED" if exc.code == "NOT_CONFIGURED" else "DEGRADED"), exc.code
            return self.credentials
        except AgentUnavailable:
            self.status, self.detail = "DEGRADED", "server unreachable; keeping credentials already in memory"
            return self.credentials
        except (EnvelopeError, KeyError, TypeError, ValueError):
            self.status, self.detail = "DEGRADED", "envelope rejected (expired, tampered or for another device)"
            log.warning("secret envelope rejected; nothing was stored")
            return self.credentials
        self._creds = creds
        self.status, self.detail = "OK", f"version {creds.version} held in memory"
        try:
            self.client.post("secrets/ack", {"provider": self.provider, "secret_version": creds.version})
        except (AgentHTTPError, AgentUnavailable):
            self.detail += " (ack pending)"
        return creds

    def maybe_refresh(self, *payloads: dict | None, auth_failed: bool = False) -> bool:
        """Refetch when the server announces a newer version, nothing is held yet, or the provider rejected the key."""
        announced = announced_versions(*payloads).get(self.provider, 0)
        retry_due = self._creds is None and (
            self._last_attempt is None or (self.clock() - self._last_attempt).total_seconds() >= self.retry_seconds)
        if auth_failed or retry_due or announced > self.version:
            before = self.version
            self.fetch()
            return self.version != before
        return False

    def forget(self) -> None:
        self._creds = None
