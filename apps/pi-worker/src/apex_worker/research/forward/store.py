"""Durable, independently timestamped storage for one series: comments on a dedicated GitHub issue.

Why issue comments: the scheduled job then needs only `issues: write` (no `contents: write`, no push to any branch,
no external service or cost), and GitHub stamps every comment with its own server time (`created_at`) and keeps an
edit history. The comment IS the anchor: a decision counts as observed on time only if its comment was created before
the session's deadline (the next open). Records are never edited; a changed comment fails verification.

Comment format (one record; long records span several comments, `part=i/n`):

    <!-- apex-shadow-record v1 series=<id> seq=<n> part=<i>/<n> digest=<sha256> -->
    human-readable summary (not part of the record)
    ```text
    apexrec1:<base64 of gzip(canonical JSON)>
    ```

Only comments by the workflow's bot identity count. A record-marked comment by anyone else, an undecodable payload or
two different records at one position stop the recorder (fail closed). `append` reads every posted comment back and
returns only after the stored body matches: a record is never reported as posted until GitHub returns it.
"""

from __future__ import annotations

import base64
import gzip
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from apex_worker.research.forward.records import canonical, digest_of, publication_problems

BOT_LOGIN = "github-actions[bot]"
TITLE_PREFIX = "APEX forward-shadow ledger"
MAX_PART_CHARS = 60_000  # GitHub's comment limit is 65,536 characters; leave room for the header and summary
ROLLOVER_COMMENTS = 2_000  # GitHub caps comments per issue; continue the series in the next volume well before that
MARKER_RE = re.compile(r"<!-- apex-shadow-record v1 series=(?P<series>[A-Za-z0-9._-]+) seq=(?P<seq>\d+) "
                       r"part=(?P<part>\d+)/(?P<parts>\d+) digest=(?P<digest>[0-9a-f]{64}) -->")
PAYLOAD_RE = re.compile(r"```text\napexrec1:(?P<b64>[A-Za-z0-9+/=]+)\n```")
TITLE_RE = re.compile(rf"^{re.escape(TITLE_PREFIX)} · (?P<series>[A-Za-z0-9._-]+) · vol (?P<vol>\d+)$")


class StoreError(Exception):
    """Storage failed. `kind`: PERMISSION (owner setup required), AUTH, UNAVAILABLE (transient) or INTEGRITY."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


# ---- payload encoding ------------------------------------------------------------------------------------------------
def encode_parts(rec: dict, max_chars: int = MAX_PART_CHARS) -> list[str]:
    b64 = base64.b64encode(gzip.compress(canonical(rec), mtime=0)).decode()
    return [b64[i : i + max_chars] for i in range(0, len(b64), max_chars)] or [""]


def decode_payload(b64: str) -> dict:
    return json.loads(gzip.decompress(base64.b64decode(b64)).decode())


def summary_line(rec: dict) -> str:
    body = rec.get("body") or {}
    note = body.get("summary") or ""
    return (f"**APEX forward shadow — research only, no orders, no alerts.** `{rec['record_type']}` "
            f"session `{rec.get('session') or '—'}` · seq {rec['seq']} · observed {rec['observed_at']}\n"
            f"digest `{rec['digest']}` · previous `{rec['previous_digest'][:16]}…`" + (f"\n\n{note}" if note else ""))


def comment_bodies(rec: dict) -> list[str]:
    parts = encode_parts(rec)
    out = []
    for i, p in enumerate(parts, 1):
        head = (f"<!-- apex-shadow-record v1 series={rec['series_id']} seq={rec['seq']} part={i}/{len(parts)} "
                f"digest={rec['digest']} -->")
        text = summary_line(rec) if i == 1 else f"(continuation {i}/{len(parts)} of seq {rec['seq']})"
        out.append(f"{head}\n{text}\n\n```text\napexrec1:{p}\n```")
    return out


# ---- GitHub API (REST) and an in-memory twin for tests and fixture smoke runs -----------------------------------------
class RestGitHubAPI:
    """Minimal REST client for issues and issue comments, authenticated with the job's GITHUB_TOKEN (never logged)."""

    def __init__(self, token: str, repo: str, base: str = "https://api.github.com", client=None,
                 sleep: Callable[[float], None] = time.sleep, retries: int = 3):
        import httpx

        if not token or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
            raise StoreError("AUTH", "GITHUB_TOKEN and GITHUB_REPOSITORY are required for the issue-comment ledger")
        self.repo, self.base, self.sleep, self.retries = repo, base.rstrip("/"), sleep, retries
        self.client = client or httpx.Client(timeout=30.0)
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "apex-forward-shadow"}
        self.requests = 0

    def _req(self, method: str, url: str, **kw):
        import httpx

        for attempt in range(self.retries + 1):
            self.requests += 1
            try:
                r = self.client.request(method, url, headers=self.headers, **kw)
            except httpx.HTTPError as exc:
                if attempt == self.retries:
                    raise StoreError("UNAVAILABLE", f"GitHub API unreachable: {type(exc).__name__}") from None
                self.sleep(2.0 * 2**attempt)
                continue
            limited = r.status_code == 429 or (r.status_code == 403 and ("rate limit" in r.text.lower()
                                                                          or r.headers.get("retry-after")))
            if r.status_code >= 500 or limited:
                if attempt == self.retries:
                    raise StoreError("UNAVAILABLE", f"GitHub API {r.status_code} after {attempt + 1} attempts")
                self.sleep(float(r.headers.get("retry-after") or 2.0 * 2**attempt))
                continue
            if r.status_code == 401:
                raise StoreError("AUTH", "GitHub rejected the token (401)")
            if r.status_code in (403, 404, 410):
                where = url.rsplit("/repos/", maxsplit=1)[-1]
                msg = r.json().get("message", "")[:120] if r.content else ""
                raise StoreError("PERMISSION", f"GitHub {r.status_code} on {method} {where}: {msg}")
            r.raise_for_status()
            return r
        raise StoreError("UNAVAILABLE", "unreachable")  # pragma: no cover

    def _paged(self, url: str, params: dict) -> list[dict]:
        out: list[dict] = []
        nxt: str | None = url
        while nxt:
            r = self._req("GET", nxt, params=params if nxt == url else None)
            out.extend(r.json())
            m = re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("link", ""))
            nxt = m.group(1) if m else None
        return out

    def list_issues(self) -> list[dict]:
        # every issue (bots cannot be filtered reliably server-side); the store keeps only bot-created ledger titles
        return self._paged(f"{self.base}/repos/{self.repo}/issues", {"state": "all", "per_page": 100})

    def create_issue(self, title: str, body: str) -> dict:
        return self._req("POST", f"{self.base}/repos/{self.repo}/issues", json={"title": title, "body": body}).json()

    def list_comments(self, number: int) -> list[dict]:
        return self._paged(f"{self.base}/repos/{self.repo}/issues/{number}/comments", {"per_page": 100})

    def create_comment(self, number: int, body: str) -> dict:
        return self._req("POST", f"{self.base}/repos/{self.repo}/issues/{number}/comments", json={"body": body}).json()

    def get_comment(self, comment_id: int) -> dict:
        return self._req("GET", f"{self.base}/repos/{self.repo}/issues/comments/{comment_id}").json()


@dataclass
class MemoryGitHubAPI:
    """In-memory GitHub twin with server-side timestamps from `clock` and fault injection (tests, fixture smoke)."""

    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    login: str = BOT_LOGIN
    issues: list[dict] = field(default_factory=list)
    comments: list[dict] = field(default_factory=list)
    fail_creates: int = 0  # the next N create_comment calls fail before reaching the server
    lose_create_responses: int = 0  # the next N create_comment calls are stored but the response is lost
    deny: bool = False  # every call fails with PERMISSION (token without issues: write)
    requests: int = 0

    def _now(self) -> str:
        return self.clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _check(self) -> None:
        self.requests += 1
        if self.deny:
            raise StoreError("PERMISSION", "Resource not accessible by integration (issues: write missing)")

    def list_issues(self) -> list[dict]:
        self._check()
        return [dict(i) for i in self.issues]

    def create_issue(self, title: str, body: str) -> dict:
        self._check()
        issue = {"number": len(self.issues) + 1, "title": title, "body": body, "state": "open",
                 "user": {"login": self.login}, "created_at": self._now(),
                 "html_url": f"memory://issues/{len(self.issues) + 1}"}
        self.issues.append(issue)
        return dict(issue)

    def list_comments(self, number: int) -> list[dict]:
        self._check()
        return [dict(c) for c in self.comments if c["issue"] == number]

    def create_comment(self, number: int, body: str, login: str | None = None) -> dict:
        self._check()
        if self.fail_creates:
            self.fail_creates -= 1
            raise StoreError("UNAVAILABLE", "injected: GitHub API 502")
        now = self._now()
        c = {"id": 1000 + len(self.comments), "issue": number, "body": body, "user": {"login": login or self.login},
             "created_at": now, "updated_at": now, "html_url": f"memory://issues/{number}#c{1000 + len(self.comments)}"}
        self.comments.append(c)
        if self.lose_create_responses:
            self.lose_create_responses -= 1
            raise StoreError("UNAVAILABLE", "injected: response lost after the comment was stored")
        return dict(c)

    def get_comment(self, comment_id: int) -> dict:
        self._check()
        return dict(next(c for c in self.comments if c["id"] == comment_id))

    # test helpers: what a person with write access could do through the UI or API
    def edit_comment(self, comment_id: int, body: str) -> None:
        c = next(c for c in self.comments if c["id"] == comment_id)
        c["body"], c["updated_at"] = body, self._now()

    def delete_comment(self, comment_id: int) -> None:
        self.comments = [c for c in self.comments if c["id"] != comment_id]

    def to_json(self) -> dict:
        return {"issues": self.issues, "comments": self.comments}

    @classmethod
    def from_json(cls, data: dict, **kw) -> MemoryGitHubAPI:
        return cls(issues=list(data.get("issues", [])), comments=list(data.get("comments", [])), **kw)


# ---- the store ------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Anchor:
    """Where and when GitHub stored a record (part 1 is the anchor)."""

    created_at: str
    url: str
    comment_ids: tuple[int, ...]
    edited: bool = False

    def to_dict(self) -> dict:
        return {"created_at": self.created_at, "url": self.url, "comment_ids": list(self.comment_ids),
                "edited": self.edited}


@dataclass
class ReadResult:
    records: list[dict]
    anchors: dict[str, Anchor]  # digest -> anchor
    problems: list[str]
    volumes: list[dict]
    incomplete_tail: dict | None = None  # parts of the last record that are missing (an interrupted append)


class IssueLedgerStore:
    def __init__(self, api, series_id: str, *, allowed_authors: tuple[str, ...] = (BOT_LOGIN,),
                 rollover: int = ROLLOVER_COMMENTS, issue_body: str = ""):
        self.api, self.series_id, self.allowed, self.rollover = api, series_id, allowed_authors, rollover
        self.issue_body = issue_body
        self._vols: list[dict] | None = None  # volume state from the last read(), kept current by append()

    def title(self, vol: int) -> str:
        return f"{TITLE_PREFIX} · {self.series_id} · vol {vol}"

    def volumes(self) -> list[dict]:
        out = []
        for i in self.api.list_issues():
            m = TITLE_RE.match(i.get("title") or "")
            if m and m.group("series") == self.series_id and (i.get("user") or {}).get("login") in self.allowed:
                out.append(i | {"_vol": int(m.group("vol"))})
        return sorted(out, key=lambda i: (i["_vol"], i["number"]))

    def read(self) -> ReadResult:
        problems: list[str] = []
        vols = self.volumes()
        if len({v["_vol"] for v in vols}) != len(vols):
            problems.append("two ledger issues claim the same volume number")
        parts: dict[tuple[int, str], dict] = {}
        order: list[tuple[int, str]] = []
        counts: dict[int, int] = {}
        for v in vols:
            cs = sorted(self.api.list_comments(v["number"]), key=lambda c: (c["created_at"], c["id"]))
            counts[v["number"]] = len(cs)
            for c in cs:
                m = MARKER_RE.search(c.get("body") or "")
                if not m:
                    continue  # ordinary discussion is ignored
                if m.group("series") != self.series_id:
                    problems.append(f"comment {c['id']}: record of series {m.group('series')} in this ledger")
                    continue
                login = (c.get("user") or {}).get("login")
                if login not in self.allowed:
                    problems.append(f"comment {c['id']}: record-marked comment by {login!r}, not the recorder")
                    continue
                pm = PAYLOAD_RE.search(c["body"])
                if not pm:
                    problems.append(f"comment {c['id']}: record marker without a payload")
                    continue
                key = (int(m.group("seq")), m.group("digest"))
                if key not in parts:
                    parts[key] = {"n": int(m.group("parts")), "chunks": {}, "comments": [], "first": c}
                    order.append(key)
                slot = parts[key]
                i = int(m.group("part"))
                if i in slot["chunks"]:
                    if slot["chunks"][i] != pm.group("b64"):
                        problems.append(f"comment {c['id']}: conflicting copy of seq {key[0]} part {i}")
                    continue  # an identical re-post (retry after a lost response) is harmless; the first counts
                slot["chunks"][i] = pm.group("b64")
                slot["comments"].append(c)
        records, anchors = [], {}
        seen_seq: dict[int, str] = {}
        incomplete = None
        pos = {k: i for i, k in enumerate(order)}
        for idx, key in enumerate(sorted(order, key=lambda k: (k[0], pos[k]))):
            slot = parts[key]
            seq, dig = key
            if len(slot["chunks"]) != slot["n"]:
                if idx == len(order) - 1:
                    incomplete = {"seq": seq, "digest": dig, "have": sorted(slot["chunks"]), "parts": slot["n"]}
                    continue
                problems.append(f"seq {seq}: {len(slot['chunks'])}/{slot['n']} parts present")
                continue
            if seq in seen_seq:
                problems.append(f"seq {seq}: two different records ({seen_seq[seq][:12]}…, {dig[:12]}…)")
                continue
            try:
                rec = decode_payload("".join(slot["chunks"][i] for i in range(1, slot["n"] + 1)))
            except (ValueError, OSError, EOFError) as exc:
                problems.append(f"seq {seq}: payload does not decode ({type(exc).__name__})")
                continue
            if rec.get("digest") != dig or digest_of(rec) != dig:
                problems.append(f"seq {seq}: payload digest does not match the comment's digest")
                continue
            seen_seq[seq] = dig
            first = min(slot["comments"], key=lambda c: (c["created_at"], c["id"]))
            edited = any(c.get("updated_at") and c["updated_at"] != c["created_at"] for c in slot["comments"])
            anchors[dig] = Anchor(first["created_at"], first.get("html_url", ""),
                                  tuple(c["id"] for c in slot["comments"]), edited)
            records.append(rec)
        records.sort(key=lambda r: r["seq"])
        self._vols = [{"number": v["number"], "vol": v["_vol"], "comments": counts.get(v["number"], 0),
                       "url": v.get("html_url")} for v in vols]
        return ReadResult(records, anchors, problems, [dict(v) for v in self._vols], incomplete)

    def _target_issue(self) -> dict:
        if self._vols is None:
            self.read()
        vols = self._vols or []
        if vols and vols[-1]["comments"] < self.rollover:
            return vols[-1]
        n = (vols[-1]["vol"] + 1) if vols else 1
        prev = f"\n\nContinues volume {n - 1}: {vols[-1]['url']}" if vols else ""
        issue = self.api.create_issue(self.title(n), (self.issue_body or "") + prev)
        vol = {"number": int(issue["number"]), "vol": n, "comments": 0, "url": issue.get("html_url")}
        self._vols = [*vols, vol]
        return vol

    @property
    def volume_state(self) -> list[dict]:
        return [dict(v) for v in (self._vols or [])]

    def append(self, rec: dict) -> Anchor:
        """Post `rec` and read every part back. Raises StoreError if GitHub did not store it exactly."""
        if rec.get("series_id") != self.series_id:
            raise StoreError("INTEGRITY", f"record of series {rec.get('series_id')} offered to {self.series_id}")
        blocked = publication_problems(rec)
        if blocked:
            raise StoreError("INTEGRITY", f"PUBLICATION_BLOCKED: {'; '.join(blocked[:3])}")
        vol = self._target_issue()
        number = vol["number"]
        ids, first = [], None
        for body in comment_bodies(rec):
            c = self.api.create_comment(number, body)
            vol["comments"] += 1
            back = self.api.get_comment(int(c["id"]))
            if back.get("body") != body or (back.get("user") or {}).get("login") not in self.allowed:
                raise StoreError("INTEGRITY", f"comment {c['id']} read back differently from what was posted")
            ids.append(int(c["id"]))
            first = first or back
        assert first is not None
        return Anchor(first["created_at"], first.get("html_url", ""), tuple(ids))
