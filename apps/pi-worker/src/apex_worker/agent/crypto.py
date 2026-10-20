"""Pi-side key material: X25519 (secret delivery), Ed25519 (pairing proof) and model-manifest verification.

Private keys are stored owner-only (0600) under the worker state directory and never logged.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from apex_worker.agent.canonical import b64url, canonical_json, from_b64url

ENVELOPE_ALG = "X25519-HKDF-SHA256-A256GCM"
ENVELOPE_INFO = b"apex-secret-envelope-v1"
PAIRING_PROOF_PREFIX = b"apex-pairing-proof-v1\n"
MANIFEST_PREFIX = b"apex-model-manifest-v1\n"


class EnvelopeError(ValueError):
    pass


def _raw_private(key) -> bytes:
    return key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())


def _raw_public(key) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def fingerprint(public_b64url: str) -> str:
    digest = hashlib.sha256(from_b64url(public_b64url)).hexdigest()[:16].upper()
    return "-".join(digest[i : i + 4] for i in range(0, 16, 4))


@dataclass
class DeviceKeys:
    x25519: X25519PrivateKey
    ed25519: Ed25519PrivateKey

    @property
    def x25519_public(self) -> str:
        return b64url(_raw_public(self.x25519.public_key()))

    @property
    def ed25519_public(self) -> str:
        return b64url(_raw_public(self.ed25519.public_key()))

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.ed25519_public)

    def sign_pairing(self, code: str, server_nonce: str, agent_id: str) -> str:
        msg = (
            PAIRING_PROOF_PREFIX
            + canonical_json(
                {"agent_id": agent_id, "code": code, "server_nonce": server_nonce, "x25519": self.x25519_public}
            ).encode()
        )
        return b64url(self.ed25519.sign(msg))

    @classmethod
    def generate(cls) -> DeviceKeys:
        return cls(X25519PrivateKey.generate(), Ed25519PrivateKey.generate())

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        path = directory / "device_keys.json"
        tmp = path.with_suffix(".tmp")
        data = {"x25519": b64url(_raw_private(self.x25519)), "ed25519": b64url(_raw_private(self.ed25519)), "v": 1}
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)

    @classmethod
    def load(cls, directory: Path) -> DeviceKeys | None:
        path = directory / "device_keys.json"
        if not path.exists():
            return None
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            raise PermissionError(f"{path} must be owner-only (chmod 600); found {oct(mode)}")
        data = json.loads(path.read_text())
        return cls(
            X25519PrivateKey.from_private_bytes(from_b64url(data["x25519"])),
            Ed25519PrivateKey.from_private_bytes(from_b64url(data["ed25519"])),
        )


def open_envelope(private_key: X25519PrivateKey, envelope: dict, *, expected_device: str, now_iso: str) -> str:
    """Decrypt a server-issued secret envelope. Fails closed on any mismatch, expiry or tamper."""
    try:
        if envelope.get("v") != 1 or envelope.get("alg") != ENVELOPE_ALG:
            raise EnvelopeError("unsupported envelope")
        meta = envelope["meta"]
        if meta["device_id"] != expected_device:
            raise EnvelopeError("envelope addressed to a different device")
        if meta["expires_at"] < now_iso:
            raise EnvelopeError("envelope expired")
        epk_raw = from_b64url(envelope["epk"])
        shared = private_key.exchange(X25519PublicKey.from_public_bytes(epk_raw))
        key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=from_b64url(envelope["salt"]), info=ENVELOPE_INFO + epk_raw
        ).derive(shared)
        ct = from_b64url(envelope["ct"]) + from_b64url(envelope["tag"])
        plain = AESGCM(key).decrypt(from_b64url(envelope["iv"]), ct, canonical_json(meta).encode())
        return plain.decode("utf-8")
    except (KeyError, InvalidTag, ValueError) as exc:
        if isinstance(exc, EnvelopeError):
            raise
        raise EnvelopeError("envelope could not be opened") from None


# ---- model manifests -----------------------------------------------------------------------------
def manifest_message(manifest: dict) -> bytes:
    body = {k: v for k, v in manifest.items() if k != "signature"}
    return MANIFEST_PREFIX + canonical_json(body).encode()


def sign_manifest(private_key: Ed25519PrivateKey, manifest: dict) -> str:
    return b64url(private_key.sign(manifest_message(manifest)))


def verify_manifest(manifest: dict, trusted_keys: dict[int, str]) -> bool:
    try:
        version = int(manifest["signer_key_version"])
        pub = trusted_keys.get(version)
        if not pub:
            return False
        Ed25519PublicKey.from_public_bytes(from_b64url(pub)).verify(
            from_b64url(manifest["signature"]), manifest_message(manifest)
        )
        return True
    except (KeyError, ValueError, InvalidSignature, TypeError):
        return False


def load_signing_key(b64: str) -> Ed25519PrivateKey:
    raw = from_b64url(b64.strip())
    if len(raw) != 32:
        raise ValueError("model signing key must be a 32-byte Ed25519 seed (base64url)")
    return Ed25519PrivateKey.from_private_bytes(raw)


def public_from_signing_key(key: Ed25519PrivateKey) -> str:
    return b64url(_raw_public(key.public_key()))
