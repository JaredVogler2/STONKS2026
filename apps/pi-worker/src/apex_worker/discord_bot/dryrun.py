"""Dry-run Discord delivery: renders outbox intents exactly as the gateway bot would, without a token.

Used by DEMO, replay and tests. DMs to members who have not opted in (or who block DMs) are DEFERRED and
held privately; they are never redirected to a shared channel.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from apex_worker.notifications.render import render
from apex_worker.storage.local import LocalStore


@dataclass
class DryRunDiscord:
    store: LocalStore
    component_secret: bytes
    dm_blocked: set[str] = field(default_factory=set)  # discord user ids whose DMs fail
    transcript: list[dict] = field(default_factory=list)
    out_file: Path | None = None
    _msg_seq: int = 0

    def deliver_due(self, now: datetime, limit: int = 50) -> dict[str, int]:
        counts = {"delivered": 0, "deferred": 0, "failed": 0}
        for row in self.store.due_outbox(now, limit):
            intent = row.payload
            msg = render(intent, self.component_secret).to_discord()
            route = intent["route"]
            if route["type"] == "DM":
                prefs = self.store.member_prefs(route["user_id"])
                if not prefs["dm_opt_in"] or prefs["discord_user_id"] in self.dm_blocked:
                    self.store.ack_outbox(row.id, "dm_blocked", now)
                    counts["deferred"] += 1
                    continue
                target = f"dm:{prefs['discord_user_id']}"
            else:
                target = f"channel:{route['channel_key']}"
            self._msg_seq += 1
            mid = str(1_300_000_000_000_000_000 + self._msg_seq)
            entry = {"at": now.isoformat(), "target": target, "event_key": intent["event_key"], "kind": intent["kind"],
                     "message": msg, "discord_message_id": mid}
            self.transcript.append(entry)
            if self.out_file:
                with self.out_file.open("a") as fh:
                    fh.write(json.dumps(entry, sort_keys=True) + "\n")
            self.store.ack_outbox(row.id, "delivered", now, discord_message_id=mid)
            counts["delivered"] += 1
        return counts
