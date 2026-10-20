"""Response caches keyed by redacted URL, storing ETag/Last-Modified for conditional revalidation."""

from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from apex_worker.catalysts.models import sha256_hex
from apex_worker.catalysts.urlsafe import redact_url


@dataclass(frozen=True)
class CacheEntry:
    url: str
    status: int
    content: bytes
    content_type: str | None
    etag: str | None
    last_modified: str | None
    fetched_at: str


class ResponseCache(Protocol):
    def get(self, url: str) -> CacheEntry | None: ...

    def put(self, entry: CacheEntry) -> None: ...


class MemoryCache:
    def __init__(self) -> None:
        self._data: dict[str, CacheEntry] = {}

    def get(self, url: str) -> CacheEntry | None:
        return self._data.get(redact_url(url))

    def put(self, entry: CacheEntry) -> None:
        self._data[entry.url] = entry

    def __len__(self) -> int:
        return len(self._data)


class DiskCache:
    """One small JSON file per (redacted) URL; body stored base64. Corrupt files are ignored."""

    def __init__(self, directory: Path | str, *, max_entry_bytes: int = 16 * 1024 * 1024):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_entry_bytes = max_entry_bytes

    def _path(self, url: str) -> Path:
        return self.dir / f"{sha256_hex(redact_url(url))}.json"

    def get(self, url: str) -> CacheEntry | None:
        path = self._path(url)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return CacheEntry(
                url=raw["url"],
                status=int(raw["status"]),
                content=base64.b64decode(raw["content_b64"]),
                content_type=raw.get("content_type"),
                etag=raw.get("etag"),
                last_modified=raw.get("last_modified"),
                fetched_at=raw["fetched_at"],
            )
        except (OSError, ValueError, KeyError):
            return None

    def put(self, entry: CacheEntry) -> None:
        if len(entry.content) > self.max_entry_bytes:
            return
        data = asdict(entry)
        data["content_b64"] = base64.b64encode(data.pop("content")).decode("ascii")
        tmp = self._path(entry.url).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path(entry.url))


__all__ = ["CacheEntry", "DiskCache", "MemoryCache", "ResponseCache"]
