"""Quarantine: structured records for documents/fetches that could not be used (hashes, never content)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from apex_worker.catalysts.models import sha256_hex
from apex_worker.catalysts.urlsafe import redact_url
from apex_worker.timeutil import iso_utc

T = TypeVar("T")


@dataclass(frozen=True)
class QuarantineRecord:
    """Structured record of a document/fetch we could not use. Holds hashes, never full content."""

    source: str
    url: str
    reason_code: str
    error_type: str
    message: str
    quarantined_at: str
    content_sha256: str | None = None
    content_length: int | None = None
    parser_version: str | None = None
    http_status: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class QuarantineStore:
    """In-memory list of quarantine records, optionally appended to a JSONL file."""

    def __init__(self, path: Path | str | None = None):
        self.records: list[QuarantineRecord] = []
        self.path = Path(path) if path else None

    def add(self, record: QuarantineRecord) -> QuarantineRecord:
        self.records.append(record)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
        return record

    def __len__(self) -> int:
        return len(self.records)


def quarantine_record(
    *,
    source: str,
    url: str,
    reason_code: str,
    error: BaseException | str,
    now: datetime,
    content: bytes | None = None,
    parser_version: str | None = None,
    http_status: int | None = None,
) -> QuarantineRecord:
    message = str(error) if not isinstance(error, str) else error
    return QuarantineRecord(
        source=source,
        url=redact_url(url),
        reason_code=reason_code,
        error_type=type(error).__name__ if not isinstance(error, str) else "Error",
        message=message[:300],
        quarantined_at=iso_utc(now),
        content_sha256=sha256_hex(content) if content is not None else None,
        content_length=len(content) if content is not None else None,
        parser_version=parser_version,
        http_status=http_status,
    )


def safe_parse(
    content: bytes,
    parser: Callable[[bytes], T],
    *,
    source: str,
    url: str,
    now: datetime,
    parser_version: str | None = None,
    quarantine: QuarantineStore | None = None,
) -> T | QuarantineRecord:
    """Run ``parser``; any exception becomes a QuarantineRecord instead of propagating."""
    try:
        return parser(content)
    except Exception as exc:
        record = quarantine_record(
            source=source,
            url=url,
            reason_code="PARSE_ERROR",
            error=exc,
            now=now,
            content=content,
            parser_version=parser_version,
        )
        if quarantine is not None:
            quarantine.add(record)
        return record


__all__ = ["QuarantineRecord", "QuarantineStore", "quarantine_record", "safe_parse"]
