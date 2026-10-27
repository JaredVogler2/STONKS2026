"""A minimal Discord REST client for the personal alerts, plus an in-memory channel for tests and the smoke run.

The alerts run as short scheduled jobs, so there is no gateway connection: each run reads the private channel's
history, answers the owner's new messages and posts what is due. The bot needs View Channel, Send Messages and Read
Message History in that channel, and the Message Content intent to read the owner's messages (personal/README.md).
Errors carry the HTTP status and Discord's numeric code only, never a message's content.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import httpx

API = "https://discord.com/api/v10"
MAX_PAGES = 200  # 20,000 messages: beyond that the run stops loudly instead of rebuilding from a partial history
NONCE_WINDOW = timedelta(minutes=5)  # Discord deduplicates an enforced nonce for a few minutes


class DiscordError(RuntimeError):
    def __init__(self, status: int, code: int | None = None):
        super().__init__(f"Discord HTTP {status}" + (f" (code {code})" if code is not None else ""))
        self.status = status
        self.code = code


class DiscordREST:
    def __init__(self, token: str, *, client: httpx.Client | None = None, sleep=time.sleep, max_retries: int = 4):
        if not token:
            raise ValueError("DISCORD_BOT_TOKEN is not set")
        self._headers = {"Authorization": f"Bot {token}",
                         "User-Agent": "DiscordBot (https://github.com/JaredVogler2/STONKS2026, 1.0)"}
        self._client = client or httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0))
        self._sleep = sleep
        self.max_retries = max_retries
        self.requests = 0

    def _call(self, method: str, path: str, *, params: dict | None = None, json: dict | None = None):
        attempt = 0
        while True:
            self.requests += 1
            try:
                resp = self._client.request(method, f"{API}{path}", params=params, json=json, headers=self._headers)
            except httpx.HTTPError:
                if attempt >= self.max_retries:
                    raise DiscordError(0) from None
                self._sleep(min(30.0, 2.0**attempt))
                attempt += 1
                continue
            if resp.status_code == 429:
                try:
                    wait = float(resp.json().get("retry_after", 1.0))
                except ValueError:
                    wait = 1.0
                if attempt >= self.max_retries:
                    raise DiscordError(429)
                self._sleep(min(60.0, wait + 0.25))
                attempt += 1
                continue
            if resp.status_code >= 500:
                if attempt >= self.max_retries:
                    raise DiscordError(resp.status_code)
                self._sleep(min(30.0, 2.0**attempt))
                attempt += 1
                continue
            if resp.status_code >= 400:
                try:
                    code = resp.json().get("code")
                except ValueError:
                    code = None
                raise DiscordError(resp.status_code, code if isinstance(code, int) else None)
            remaining = resp.headers.get("X-RateLimit-Remaining")
            if remaining == "0":
                self._sleep(min(10.0, float(resp.headers.get("X-RateLimit-Reset-After") or 1.0)))
            return resp.json() if resp.content else None

    def me(self) -> dict:
        return self._call("GET", "/users/@me")

    def history(self, channel_id: str) -> list[dict]:
        """Every message of the channel, oldest first (read newest first, 100 at a time)."""
        out: list[dict] = []
        before: str | None = None
        for _ in range(MAX_PAGES):
            params = {"limit": 100} | ({"before": before} if before else {})
            page = self._call("GET", f"/channels/{channel_id}/messages", params=params) or []
            out.extend(page)
            if len(page) < 100:
                return sorted(out, key=lambda m: int(m["id"]))
            before = min((m["id"] for m in page), key=int)
        raise DiscordError(413)  # history too long to rebuild in one run

    def send(self, channel_id: str, content: str, *, reply_to: str | None = None, nonce: str | None = None) -> dict:
        body: dict = {"content": content, "allowed_mentions": {"parse": []}, "flags": 4}  # 4: no link embeds
        if reply_to:
            body["message_reference"] = {"message_id": reply_to, "fail_if_not_exists": False}
        if nonce:
            body |= {"nonce": nonce, "enforce_nonce": True}
        return self._call("POST", f"/channels/{channel_id}/messages", json=body)

    def close(self) -> None:
        self._client.close()


class MemoryChannel:
    """An in-memory stand-in with the same three calls (tests, the smoke run). `post_as_owner` adds a person's
    message; `blank_content` emulates a bot without the Message Content intent."""

    def __init__(self, *, bot_id: str = "900", owner_id: str = "100", start: datetime | None = None):
        self.bot_id, self.owner_id = bot_id, owner_id
        self.messages: list[dict] = []
        self._next = 1_000
        self.clock = start or datetime(2026, 10, 27, 14, 0, tzinfo=UTC)
        self.blank_content = False
        self.nonces: dict[str, tuple[dict, datetime]] = {}
        self.duplicates = 0  # sends answered from the nonce cache (a real retry, or a double post to catch in tests)

    def _id(self) -> str:
        self._next += 1
        return str(self._next)

    def _msg(self, author: dict, content: str, **extra) -> dict:
        m = {"id": self._id(), "type": 19 if extra.get("message_reference") else 0, "author": author,
             "content": content, "timestamp": self.clock.isoformat(), "attachments": [], **extra}
        self.messages.append(m)
        return m

    def post_as_owner(self, content: str, at: datetime | None = None, *, author_id: str | None = None) -> dict:
        if at is not None:
            self.clock = at
        return self._msg({"id": author_id or self.owner_id, "username": "owner"}, content)

    def advance(self, **kw) -> None:
        self.clock += timedelta(**kw)

    # the DiscordREST surface
    def me(self) -> dict:
        return {"id": self.bot_id, "username": "apex", "bot": True}

    def history(self, channel_id: str) -> list[dict]:
        blank = self.blank_content
        return [m | {"content": ""} if blank and m["author"]["id"] != self.bot_id else m for m in self.messages]

    def send(self, channel_id: str, content: str, *, reply_to: str | None = None, nonce: str | None = None) -> dict:
        if len(content) > 2000:
            raise DiscordError(400, 50035)
        if nonce and nonce in self.nonces and self.clock - self.nonces[nonce][1] < NONCE_WINDOW:
            self.duplicates += 1
            return self.nonces[nonce][0]
        extra = {"message_reference": {"message_id": reply_to}} if reply_to else {}
        m = self._msg({"id": self.bot_id, "username": "apex", "bot": True}, content, **extra)
        if nonce:
            self.nonces[nonce] = (m, self.clock)
        return m

    def bot_texts(self) -> list[str]:
        return [m["content"] for m in self.messages if m["author"]["id"] == self.bot_id]


__all__ = ["API", "DiscordError", "DiscordREST", "MemoryChannel"]
