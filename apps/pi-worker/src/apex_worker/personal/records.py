"""The machine-readable line the bot appends to its own Discord messages: the account's only store.

Every record is one subtext line, `-# apex1 {json}`, so the owner sees exactly what was recorded. Only messages the
bot itself wrote are ever read as records (a person cannot forge one), and the account is rebuilt from them on every
run: nothing personal is kept in the repository, the Actions log or any artifact.

Kinds: hello · start · deposit · withdraw · buy · sell · dividend · split · skip · void (undo) · ack (a reply that
changed nothing) · alert (the method's orders) · summary · remind · note.
"""

from __future__ import annotations

import hashlib
import json

MARK = "-# apex1 "


def encode(rec: dict) -> str:
    return MARK + json.dumps(rec, separators=(",", ":"), sort_keys=True)


def decode_all(content: str) -> list[dict]:
    out = []
    for line in (content or "").splitlines():
        if line.startswith(MARK):
            try:
                rec = json.loads(line[len(MARK):])
            except ValueError:
                continue
            if isinstance(rec, dict) and isinstance(rec.get("k"), str):
                out.append(rec)
    return out


def nonce(rec: dict) -> str:
    """A deterministic Discord nonce (at most 25 characters): a retried send of the same record is deduplicated."""
    return hashlib.sha256(encode(rec).encode()).hexdigest()[:25]


__all__ = ["MARK", "decode_all", "encode", "nonce"]
