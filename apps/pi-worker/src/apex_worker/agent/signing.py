"""APEX agent request signing (APEX-HMAC-SHA256-V1). Mirrors apps/web/src/server/security/hmac.ts."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

SCHEME = "APEX-HMAC-SHA256-V1"


@dataclass(frozen=True)
class SignedHeaders:
    agent_id: str
    timestamp: str
    nonce: str
    body_sha256: str
    signature: str

    def as_http(self) -> dict[str, str]:
        return {
            "X-APEX-Agent-ID": self.agent_id,
            "X-APEX-Timestamp": self.timestamp,
            "X-APEX-Nonce": self.nonce,
            "X-APEX-Body-SHA256": self.body_sha256,
            "X-APEX-Signature": self.signature,
        }


def canonical_request(agent_id: str, method: str, path_with_query: str, timestamp: str, nonce: str,
                      body_sha256: str) -> str:
    return "\n".join([SCHEME, agent_id, method.upper(), path_with_query, timestamp, nonce, body_sha256])


def new_nonce() -> str:
    return secrets.token_urlsafe(18)  # 144 bits, 24 url-safe chars


def sign(secret: bytes, agent_id: str, method: str, path_with_query: str, body: bytes, *,
         timestamp: int | None = None, nonce: str | None = None) -> SignedHeaders:
    if len(secret) < 32:
        raise ValueError("agent HMAC secret must be at least 32 bytes")
    ts = str(int(timestamp if timestamp is not None else time.time()))
    nn = nonce or new_nonce()
    body_hash = hashlib.sha256(body).hexdigest()
    sig = hmac.new(secret, canonical_request(agent_id, method, path_with_query, ts, nn, body_hash).encode(),
                   hashlib.sha256).hexdigest()
    return SignedHeaders(agent_id, ts, nn, body_hash, sig)
