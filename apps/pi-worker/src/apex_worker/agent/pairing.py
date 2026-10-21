"""`apex pair CODE --server URL`: pair this Pi with the dashboard (proof of key possession, owner approval).

Flow (apps/web/src/server/services/pairing.ts is the server half):
 1. Generate (or reuse) the device keys: Ed25519 (identity/proof) + X25519 (secret envelopes), saved 0600.
 2. POST /pair, HMAC-signed with the bootstrap AGENT_HMAC_SECRET under a Pi-generated agent id, carrying both
    public keys and an Ed25519 signature over
        "apex-pairing-proof-v1\\n" + canonical_json({agent_id, code, server_nonce, x25519})
    where code is the NORMALIZED one-time code and server_nonce = sha256("apex-pairing|" + code)[:16] (hex).
    Binding the X25519 key into the signed message stops a relay from substituting its own encryption key.
 3. The server answers `pending_approval` + the fingerprint it computed; it must equal ours. The owner compares
    the fingerprint printed here with the dashboard before approving. Nothing secret is printed.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from apex_worker.agent.canonical import canonical_json
from apex_worker.agent.client import AgentClient, AgentHTTPError, AgentUnavailable
from apex_worker.agent.crypto import PAIRING_PROOF_PREFIX, DeviceKeys
from apex_worker.agent.identity import AgentIdentity, new_agent_id
from apex_worker.timeutil import iso_utc

CODE_ALPHABET = set("ABCDEFGHJKMNPQRSTVWXYZ23456789")
CODE_LEN = 8

FRIENDLY = {
    "PAIRING_CODE_INVALID": "The pairing code is invalid, already used or expired (codes last 10 minutes). "
    "Start a new pairing in the dashboard and run `apex pair` again with the new code.",
    "PAIRING_PROOF_INVALID": "The server rejected the key-possession proof. Re-run `apex pair` with a fresh code.",
    "DEVICE_REVOKED": "This device id was revoked in the dashboard. Run `apex pair NEW-CODE --new-identity`.",
    "UNAUTHENTICATED": "The request signature was rejected: AGENT_HMAC_SECRET on the Pi does not match the Vercel "
    "environment value.",
    "CLOCK_SKEW": "The Pi clock is more than 60 s off. Enable NTP: `sudo timedatectl set-ntp true`, then retry.",
    "AGENT_AUTH_NOT_CONFIGURED": "The server has no AGENT_HMAC_SECRET configured (Vercel env). Set it and redeploy.",
    "INVALID_KEYS": "The server rejected the public keys (malformed). Re-run with --new-identity.",
}


def normalize_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", code.upper())


def code_hash(code: str) -> str:
    return hashlib.sha256(f"apex-pairing|{normalize_code(code)}".encode()).hexdigest()


def server_nonce_for(code: str) -> str:
    return code_hash(code)[:16]


def pairing_message(agent_id: str, code: str, x25519_public: str) -> bytes:
    body = {"agent_id": agent_id, "code": normalize_code(code), "server_nonce": server_nonce_for(code),
            "x25519": x25519_public}
    return PAIRING_PROOF_PREFIX + canonical_json(body).encode()


def valid_code(code: str) -> bool:
    n = normalize_code(code)
    return len(n) == CODE_LEN and set(n) <= CODE_ALPHABET


def pairing_request(keys: DeviceKeys, agent_id: str, code: str, device_name: str) -> dict:
    norm = normalize_code(code)
    return {
        "code": norm,
        "device_name": device_name[:48],
        "x25519_public_key": keys.x25519_public,
        "ed25519_public_key": keys.ed25519_public,
        "proof": keys.sign_pairing(norm, server_nonce_for(norm), agent_id),
    }


@dataclass
class PairOutcome:
    ok: bool
    status: str
    agent_id: str
    fingerprint: str
    message: str


def _heartbeat_probe(agent_id: str, mode: str) -> dict:
    return {
        "agent_id": agent_id, "sent_at": iso_utc(datetime.now(UTC).replace(microsecond=0)),
        "worker_version": "0.1.0", "release_mode": mode,
        "components": {"pairing": {"status": "UNKNOWN", "detail": "paired; awaiting owner approval"}},
        "resources": {},
    }


def pair(client: AgentClient, keys: DeviceKeys, code: str, device_name: str) -> PairOutcome:
    agent_id = client.agent_id
    try:
        res = client.post("pair", pairing_request(keys, agent_id, code, device_name)).data or {}
    except AgentHTTPError as exc:
        return PairOutcome(False, exc.code, agent_id, keys.fingerprint, FRIENDLY.get(exc.code, f"{exc.code}: {exc.message}"))
    except AgentUnavailable as exc:
        return PairOutcome(False, "UNREACHABLE", agent_id, keys.fingerprint,
                           f"Could not reach the dashboard ({exc}). Check APEX server URL and network.")
    server_fp = str(res.get("fingerprint", ""))
    if server_fp != keys.fingerprint:
        return PairOutcome(False, "FINGERPRINT_MISMATCH", agent_id, keys.fingerprint,
                           "The server reported a DIFFERENT key fingerprint. Do NOT approve this device; check the "
                           "server URL for interception and pair again.")
    return PairOutcome(True, str(res.get("status", "pending_approval")), agent_id, keys.fingerprint,
                       "Paired - waiting for owner approval in the dashboard.")


def wait_for_approval(client: AgentClient, mode: str, wait_seconds: float, *, poll: float = 10.0,
                      sleep: Callable[[float], None] = time.sleep) -> str:
    """Poll the (pending-allowed) heartbeat until the owner approves; returns the last device status."""
    deadline = time.monotonic() + wait_seconds
    status = "pending_approval"
    while True:
        try:
            status = str(client.post("heartbeat", _heartbeat_probe(client.agent_id, mode)).data.get("status", status))
        except (AgentHTTPError, AgentUnavailable):
            status = "unknown"
        if status == "active" or time.monotonic() >= deadline:
            return status
        sleep(poll)


def _device_keys(state_dir: Path, new_identity: bool) -> DeviceKeys:
    keys = None if new_identity else DeviceKeys.load(state_dir)
    if keys is None:
        keys = DeviceKeys.generate()
        keys.save(state_dir)  # 0600, before any network call so a retry reuses the same keys
    return keys


def pair_device(code: str, *, server: str | None = None, device_name: str = "apex-pi", wait_seconds: float = 0,
                new_identity: bool = False, transport: httpx.BaseTransport | None = None,
                out: Callable[[str], None] = print) -> int:
    from apex_worker.config import WorkerConfig

    cfg = WorkerConfig.load()
    base = (server or cfg.api_base_url or "").rstrip("/")
    if not base:
        out("apex pair: pass --server https://<your-dashboard>.vercel.app (or set APEX_API_BASE_URL)")
        return 2
    if not valid_code(code):
        out("apex pair: the code must be the 8-character code shown in the dashboard (e.g. ABCD-EF23)")
        return 2
    if not cfg.agent_hmac_secret:
        out("apex pair: AGENT_HMAC_SECRET is not set in the Pi .env (same value as the Vercel environment)")
        return 2
    state = cfg.state_dir
    previous = None if new_identity else AgentIdentity.load(state)
    keys = _device_keys(state, new_identity)
    agent_id = previous.agent_id if previous and previous.server.rstrip("/") == base else new_agent_id()
    client = AgentClient(base, agent_id, cfg.agent_hmac_secret, transport=transport,
                         bypass_secret=cfg.vercel_bypass_secret, max_attempts=3)
    try:
        outcome = pair(client, keys, code, device_name)
        out(f"Device fingerprint: {outcome.fingerprint}   (compare with the dashboard before approving)")
        out(f"Agent id:           {outcome.agent_id}")
        if not outcome.ok:
            out(f"PAIRING FAILED ({outcome.status}): {outcome.message}")
            return 1
        ident = AgentIdentity(agent_id, base, device_name[:48], outcome.fingerprint, iso_utc(datetime.now(UTC)),
                              outcome.status)
        ident.save(state)
        out(outcome.message)
        if wait_seconds > 0:
            ident.status = wait_for_approval(client, cfg.release_mode, wait_seconds)
            ident.save(state)
        if ident.status == "active":
            out("APPROVED - `apex worker` and `apex bot` can now use the connected mode.")
        else:
            out("PENDING APPROVAL - open the dashboard (Settings -> Devices), check the fingerprint and approve. "
                "The worker keeps retrying on its own; nothing else is needed on the Pi.")
        return 0
    finally:
        client.close()
