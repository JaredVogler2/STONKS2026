"""Canonical JSON identical to apps/web/src/server/security/canonical.ts.

Sorted keys, no whitespace, printable-ASCII strings, integers/strings/bools/null only. Floats are
rejected so Python and TypeScript serialize byte-for-byte identically (decimals travel as strings).
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

_ASCII = re.compile(r"^[\x20-\x7e]*$")
MAX_SAFE_INT = 2**53 - 1


def canonical_json(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INT:
            raise ValueError("canonical JSON allows only safe integers")
        return str(value)
    if isinstance(value, float):
        raise ValueError("canonical JSON forbids floats; encode decimals as strings")
    if isinstance(value, str):
        if not _ASCII.match(value):
            raise ValueError("canonical JSON strings must be printable ASCII")
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(v) for v in value) + "]"
    if isinstance(value, dict):
        items = sorted((k, v) for k, v in value.items() if v is not None or k is not None)
        parts = []
        for k, v in items:
            if not isinstance(k, str):
                raise ValueError("object keys must be strings")
            parts.append(f"{canonical_json(k)}:{canonical_json(v)}")
        return "{" + ",".join(parts) + "}"
    raise ValueError(f"unsupported type for canonical JSON: {type(value).__name__}")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def from_b64url(text: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]*", text):
        raise ValueError("invalid base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
