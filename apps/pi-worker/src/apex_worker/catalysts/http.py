"""Polite, rate-limited HTTP fetching with caching, retry/backoff and parse quarantine.

* Global token bucket (default 2 req/s for SEC; a configured rate above SEC's 10 req/s fair-access
  ceiling is refused).
* SEC hosts require a descriptive User-Agent with a contact email; the client refuses to run without one.
* 403/429/5xx are retried with ``Retry-After`` honoured plus exponential backoff with jitter; a 403/429
  also pauses the shared bucket so every caller backs off together.
* ETag / Last-Modified conditional revalidation via an in-memory or small on-disk JSON cache.
* Unparseable documents become a structured :class:`QuarantineRecord`; the pipeline never crashes.
* Clock, sleep and jitter RNG are injectable so tests never actually sleep.
"""

from __future__ import annotations

import random
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol, TypeVar
from urllib.parse import urlsplit

import httpx

from apex_worker.catalysts.cache import CacheEntry, DiskCache, MemoryCache, ResponseCache
from apex_worker.catalysts.models import sha256_hex
from apex_worker.catalysts.quarantine import QuarantineRecord, QuarantineStore, quarantine_record, safe_parse
from apex_worker.catalysts.urlsafe import (
    REDACT_PARAMS,
    Resolver,
    UnsafeUrlError,
    check_public_https_url,
    host_of,
    redact_url,
    system_resolver,
)
from apex_worker.timeutil import ensure_utc, iso_utc, parse_utc

T = TypeVar("T")

SEC_HOSTS = frozenset({"sec.gov", "www.sec.gov", "data.sec.gov", "efts.sec.gov"})
SEC_DEFAULT_RPS = 2.0
SEC_MAX_RPS = 10.0
SEC_UA_ERROR = "SEC requires a descriptive User-Agent with contact email"
RETRYABLE_STATUS = frozenset({403, 429, 500, 502, 503, 504})
REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_GENERIC_UA = re.compile(r"^(python-httpx|python-requests|curl|wget|mozilla|java|go-http-client)\b", re.I)


# ---------------------------------------------------------------------------------------------
# time source
# ---------------------------------------------------------------------------------------------
class TimeSource(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...

    def now(self) -> datetime: ...


class RealTimeSource:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def now(self) -> datetime:
        return datetime.now(UTC)


# ---------------------------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------------------------
class UserAgentError(ValueError):
    pass


class FetchError(RuntimeError):
    def __init__(
        self, url: str, reason_code: str, message: str, *, status: int | None = None, attempts: int = 0
    ):
        super().__init__(f"{reason_code}: {message} ({redact_url(url)})")
        self.url = redact_url(url)
        self.reason_code = reason_code
        self.status = status
        self.attempts = attempts


# ---------------------------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------------------------
def validate_sec_user_agent(user_agent: str | None) -> str:
    """Return the UA if it names the requester and includes a contact email; otherwise refuse."""
    if not user_agent or not user_agent.strip():
        raise UserAgentError(
            f"{SEC_UA_ERROR}; none configured (set APEX_SEC_USER_AGENT, e.g. 'APEX Research ops@example.com')"
        )
    ua = user_agent.strip()
    if not _EMAIL_RE.search(ua):
        raise UserAgentError(f"{SEC_UA_ERROR}; {ua!r} has no contact email")
    descriptive = _EMAIL_RE.sub("", ua).strip(" <>()[],;:-\t")
    if len(descriptive) < 3 or _GENERIC_UA.match(descriptive):
        raise UserAgentError(f"{SEC_UA_ERROR}; {ua!r} does not name the organisation/application")
    return ua


def is_sec_url(url: str) -> bool:
    return host_of(url) in SEC_HOSTS


# ---------------------------------------------------------------------------------------------
# rate limiting
# ---------------------------------------------------------------------------------------------
class TokenBucket:
    """Thread-safe token bucket; ``capacity=1`` gives strictly spaced requests."""

    def __init__(
        self, rate_per_second: float, capacity: float = 1.0, *, time_source: TimeSource | None = None
    ):
        if rate_per_second <= 0:
            raise ValueError("rate must be positive")
        self.rate = float(rate_per_second)
        self.capacity = max(1.0, float(capacity))
        self._time = time_source or RealTimeSource()
        self._tokens = self.capacity
        self._last = self._time.monotonic()
        self._paused_until = 0.0
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
        self._last = now

    def acquire(self) -> float:
        """Block (via the injected sleep) until a token is available; returns seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                now = self._time.monotonic()
                self._refill(now)
                pause = self._paused_until - now
                if pause <= 0 and self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return waited
                wait = max(pause, (1.0 - self._tokens) / self.rate)
            self._time.sleep(wait)
            waited += wait

    def pause_for(self, seconds: float) -> None:
        """Shared cooldown (after 403/429) so every caller of this bucket backs off."""
        with self._lock:
            self._paused_until = max(self._paused_until, self._time.monotonic() + max(0.0, seconds))


_GLOBAL_BUCKETS: dict[str, TokenBucket] = {}
_GLOBAL_LOCK = threading.Lock()


def global_bucket(name: str, rate_per_second: float) -> TokenBucket:
    """Process-wide bucket per name (e.g. ``"sec"``); a later, stricter rate wins."""
    with _GLOBAL_LOCK:
        bucket = _GLOBAL_BUCKETS.get(name)
        if bucket is None or rate_per_second < bucket.rate:
            bucket = TokenBucket(rate_per_second)
            _GLOBAL_BUCKETS[name] = bucket
        return bucket


# ---------------------------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    content: bytes
    content_type: str | None
    fetched_at: datetime
    from_cache: bool = False
    revalidated: bool = False
    attempts: int = 1

    @property
    def sha256(self) -> str:
        return sha256_hex(self.content)


# ---------------------------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class HttpClientConfig:
    user_agent: str
    requests_per_second: float = 1.0
    max_retries: int = 4
    max_forbidden_retries: int = 2
    backoff_base_seconds: float = 1.0
    backoff_cap_seconds: float = 300.0
    forbidden_min_delay_seconds: float = 0.0
    timeout_seconds: float = 20.0
    max_bytes: int = 15 * 1024 * 1024
    fresh_ttl_seconds: float = 0.0
    max_redirects: int = 3
    sec_compliant: bool = False
    bucket_name: str = "public"
    extra_headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        if self.sec_compliant:
            validate_sec_user_agent(self.user_agent)
            if self.requests_per_second > SEC_MAX_RPS:
                raise ValueError(
                    f"SEC fair access caps total requests at {SEC_MAX_RPS:g}/s; "
                    f"refusing {self.requests_per_second}/s"
                )

    @classmethod
    def for_sec(
        cls, user_agent: str | None, requests_per_second: float = SEC_DEFAULT_RPS, **kw: Any
    ) -> HttpClientConfig:
        ua = validate_sec_user_agent(user_agent)
        if requests_per_second > SEC_MAX_RPS:
            raise ValueError(
                f"SEC fair access caps total requests at {SEC_MAX_RPS:g}/s; refusing {requests_per_second}/s"
            )
        kw.setdefault("forbidden_min_delay_seconds", 60.0)
        return cls(
            user_agent=ua,
            requests_per_second=requests_per_second,
            sec_compliant=True,
            bucket_name="sec",
            **kw,
        )

    @classmethod
    def for_public(cls, user_agent: str, requests_per_second: float = 1.0, **kw: Any) -> HttpClientConfig:
        if not user_agent or not user_agent.strip():
            raise UserAgentError("a descriptive User-Agent is required")
        return cls(user_agent=user_agent.strip(), requests_per_second=requests_per_second, **kw)


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - ensure_utc(now)).total_seconds())


class PoliteHttpClient:
    def __init__(
        self,
        config: HttpClientConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        bucket: TokenBucket | None = None,
        cache: ResponseCache | None = None,
        time_source: TimeSource | None = None,
        rng: random.Random | None = None,
        url_guard: Callable[[str], str] | None = None,
    ):
        self.config = config
        self._time = time_source or RealTimeSource()
        self.bucket = bucket or global_bucket(config.bucket_name, config.requests_per_second)
        self.cache = cache
        self._rng = rng or random.Random()  # noqa: S311 - jitter only, not security sensitive
        self._url_guard = url_guard or check_public_https_url  # SSRF guard, also applied to redirects
        headers = {
            "User-Agent": config.user_agent,
            "Accept-Encoding": "gzip, deflate",
            **config.extra_headers,
        }
        self._client = httpx.Client(
            transport=transport, timeout=config.timeout_seconds, follow_redirects=False, headers=headers
        )
        self.request_log: list[tuple[str, int | None]] = []

    # -- lifecycle -----------------------------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteHttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- internals -----------------------------------------------------------------------------
    def _check_url(self, url: str) -> None:
        if is_sec_url(url):
            if not self.config.sec_compliant:
                raise UserAgentError(f"{SEC_UA_ERROR}; build the client with HttpClientConfig.for_sec(...)")
            if self.bucket.rate > SEC_MAX_RPS:
                raise ValueError(
                    f"rate limiter at {self.bucket.rate}/s exceeds SEC's {SEC_MAX_RPS:g}/s ceiling"
                )
        if urlsplit(url).scheme.lower() != "https":
            raise UnsafeUrlError("only https:// URLs are fetched")
        self._url_guard(url)

    def _backoff(self, attempt: int) -> float:
        base = min(self.config.backoff_cap_seconds, self.config.backoff_base_seconds * (2 ** (attempt - 1)))
        return base / 2 + self._rng.uniform(0, base / 2)  # "equal jitter"

    def _from_entry(self, entry: CacheEntry, *, revalidated: bool, attempts: int) -> FetchResult:
        return FetchResult(
            url=entry.url,
            status=entry.status,
            content=entry.content,
            content_type=entry.content_type,
            fetched_at=parse_utc(entry.fetched_at),
            from_cache=True,
            revalidated=revalidated,
            attempts=attempts,
        )

    # -- public --------------------------------------------------------------------------------
    def get(self, url: str, *, accept: str | None = None) -> FetchResult:
        """Fetch ``url`` politely. Raises :class:`FetchError` once retries are exhausted."""
        self._check_url(url)
        cached = self.cache.get(url) if self.cache else None
        if cached is not None and self.config.fresh_ttl_seconds > 0:
            age = (self._time.now() - parse_utc(cached.fetched_at)).total_seconds()
            if age <= self.config.fresh_ttl_seconds:
                return self._from_entry(cached, revalidated=False, attempts=0)

        attempts = 0
        forbidden = 0
        redirects = 0
        current = url
        last_status: int | None = None
        while attempts <= self.config.max_retries:
            attempts += 1
            self.bucket.acquire()
            headers: dict[str, str] = {}
            if accept:
                headers["Accept"] = accept
            if cached is not None:
                if cached.etag:
                    headers["If-None-Match"] = cached.etag
                if cached.last_modified:
                    headers["If-Modified-Since"] = cached.last_modified
            try:
                resp = self._client.get(current, headers=headers)
            except httpx.TransportError as exc:
                self.request_log.append((redact_url(current), None))
                last_status = None
                if attempts > self.config.max_retries:
                    raise FetchError(url, "TRANSPORT_ERROR", str(exc), attempts=attempts) from exc
                self._time.sleep(self._backoff(attempts))
                continue
            status = resp.status_code
            last_status = status
            self.request_log.append((redact_url(current), status))

            if status == 304 and cached is not None:
                return self._from_entry(cached, revalidated=True, attempts=attempts)
            if 200 <= status < 300:
                content = resp.content
                if len(content) > self.config.max_bytes:
                    raise FetchError(
                        url, "RESPONSE_TOO_LARGE", f"{len(content)} bytes", status=status, attempts=attempts
                    )
                now = self._time.now()
                entry = CacheEntry(
                    url=redact_url(url),
                    status=status,
                    content=content,
                    content_type=resp.headers.get("content-type"),
                    etag=resp.headers.get("etag"),
                    last_modified=resp.headers.get("last-modified"),
                    fetched_at=iso_utc(now),
                )
                if self.cache is not None and (
                    entry.etag or entry.last_modified or self.config.fresh_ttl_seconds > 0
                ):
                    self.cache.put(entry)
                return FetchResult(
                    redact_url(url), status, content, entry.content_type, now, attempts=attempts
                )
            if status in REDIRECT_STATUS and resp.headers.get("location"):
                redirects += 1
                if redirects > self.config.max_redirects:
                    raise FetchError(
                        url, "TOO_MANY_REDIRECTS", "redirect limit", status=status, attempts=attempts
                    )
                target = str(resp.url.join(resp.headers["location"]))
                self._check_url(target)
                current = target
                attempts -= 1  # a validated redirect is not a failed attempt
                continue
            if status in RETRYABLE_STATUS:
                if status == 403:
                    forbidden += 1
                    if forbidden > self.config.max_forbidden_retries:
                        break
                if attempts > self.config.max_retries:
                    break
                retry_after = parse_retry_after(resp.headers.get("retry-after"), self._time.now())
                delay = max(retry_after or 0.0, self._backoff(attempts))
                if status == 403:
                    delay = max(delay, self.config.forbidden_min_delay_seconds)
                delay = min(delay, max(self.config.backoff_cap_seconds, retry_after or 0.0))
                if status in (403, 429):
                    self.bucket.pause_for(delay)
                self._time.sleep(delay)
                continue
            raise FetchError(url, f"HTTP_{status}", "non-retryable status", status=status, attempts=attempts)
        code = f"HTTP_{last_status}_RETRIES_EXHAUSTED" if last_status else "RETRIES_EXHAUSTED"
        raise FetchError(url, code, "gave up after retries/backoff", status=last_status, attempts=attempts)

    def fetch_parsed(
        self,
        url: str,
        parser: Callable[[bytes], T],
        *,
        source: str,
        parser_version: str | None = None,
        quarantine: QuarantineStore | None = None,
    ) -> tuple[T | None, FetchResult | None, QuarantineRecord | None]:
        """Fetch + parse; fetch failures and parse errors are quarantined, never raised."""
        try:
            result = self.get(url)
        except (FetchError, UnsafeUrlError) as exc:
            record = quarantine_record(
                source=source,
                url=url,
                reason_code=getattr(exc, "reason_code", "UNSAFE_URL"),
                error=exc,
                now=self._time.now(),
                parser_version=parser_version,
                http_status=getattr(exc, "status", None),
            )
            if quarantine is not None:
                quarantine.add(record)
            return None, None, record
        parsed = safe_parse(
            result.content,
            parser,
            source=source,
            url=url,
            now=self._time.now(),
            parser_version=parser_version,
            quarantine=quarantine,
        )
        if isinstance(parsed, QuarantineRecord):
            return None, result, parsed
        return parsed, result, None


__all__ = [
    "REDACT_PARAMS",
    "SEC_DEFAULT_RPS",
    "SEC_HOSTS",
    "SEC_MAX_RPS",
    "SEC_UA_ERROR",
    "CacheEntry",
    "DiskCache",
    "FetchError",
    "FetchResult",
    "HttpClientConfig",
    "MemoryCache",
    "PoliteHttpClient",
    "QuarantineRecord",
    "QuarantineStore",
    "RealTimeSource",
    "Resolver",
    "ResponseCache",
    "TimeSource",
    "TokenBucket",
    "UnsafeUrlError",
    "UserAgentError",
    "check_public_https_url",
    "global_bucket",
    "host_of",
    "is_sec_url",
    "parse_retry_after",
    "quarantine_record",
    "redact_url",
    "safe_parse",
    "system_resolver",
    "validate_sec_user_agent",
]
