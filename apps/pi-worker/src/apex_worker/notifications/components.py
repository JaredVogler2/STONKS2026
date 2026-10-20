"""Signed Discord component custom IDs: apex:v1:<action>:<subject>:<mac16>.

The MAC (HMAC-SHA256, truncated to 64 bits) prevents forged/tampered buttons from naming arbitrary signal
or position IDs; the server additionally re-checks that the signal exists and the position belongs to the
clicking user. Max length stays well under Discord's 100-character custom_id limit.
"""

from __future__ import annotations

import hashlib
import hmac
import re

ACTIONS = {"entry", "watch", "evidence", "mute", "posupd", "confirm", "cancel"}
_RE = re.compile(r"^apex:v1:([a-z]{3,10}):([A-Za-z0-9_.-]{1,48}):([0-9a-f]{16})$")


def make(secret: bytes, action: str, subject: str) -> str:
    if action not in ACTIONS:
        raise ValueError("unknown component action")
    mac = hmac.new(secret, f"{action}|{subject}".encode(), hashlib.sha256).hexdigest()[:16]
    cid = f"apex:v1:{action}:{subject}:{mac}"
    assert len(cid) <= 100
    return cid


def parse(secret: bytes, custom_id: str) -> tuple[str, str] | None:
    m = _RE.match(custom_id or "")
    if not m:
        return None
    action, subject, mac = m.groups()
    if action not in ACTIONS:
        return None
    expected = hmac.new(secret, f"{action}|{subject}".encode(), hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(expected, mac):
        return None
    return action, subject
