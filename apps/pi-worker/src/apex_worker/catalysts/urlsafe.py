"""URL safety helpers: SSRF guard (https only, public DNS names, no IP literals/private ranges),
host extraction and credential redaction for logs/caches."""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACT_PARAMS = frozenset({"api_key", "apikey", "token", "access_token", "key", "secret"})
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet", ".corp")


class UnsafeUrlError(ValueError):
    pass


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def redact_url(url: str) -> str:
    """Mask credential-like query parameters (FRED api_key etc.) before logging or caching."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable-url>"
    if not parts.query:
        return url
    query = [
        (k, "REDACTED" if k.lower() in REDACT_PARAMS else v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _is_ip_literal(host: str) -> bool:
    candidate = host.strip("[]")
    try:
        ipaddress.ip_address(candidate)
        return True
    except ValueError:
        pass
    # integer / hex / octal shorthands that some resolvers accept (e.g. 2130706433, 0x7f000001, 127.1)
    return bool(re.fullmatch(r"(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+)){0,3}", candidate, re.I))


def _is_public_ip(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


Resolver = Callable[[str], list[str]]


def system_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    return sorted({str(info[4][0]) for info in infos})


def check_public_https_url(url: str, *, resolver: Resolver | None = None) -> str:
    """SSRF guard: https only, no credentials, no IP literals, no localhost/private names or ranges.

    When ``resolver`` is supplied every resolved address must be public (DNS-rebinding defence).
    """
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise UnsafeUrlError(f"unparseable URL: {exc}") from exc
    if parts.scheme.lower() != "https":
        raise UnsafeUrlError("only https:// URLs are allowed")
    if parts.username or parts.password:
        raise UnsafeUrlError("credentials in URL are not allowed")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or "." not in host:
        raise UnsafeUrlError("URL host must be a public DNS name")
    if _is_ip_literal(host):
        raise UnsafeUrlError("IP-literal hosts are not allowed")
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        raise UnsafeUrlError("local/internal host names are not allowed")
    if port not in (None, 443):
        raise UnsafeUrlError("non-standard ports are not allowed")
    if resolver is not None:
        addrs = resolver(host)
        if not addrs:
            raise UnsafeUrlError(f"{host} did not resolve")
        bad = [a for a in addrs if not _is_public_ip(a)]
        if bad:
            raise UnsafeUrlError(f"{host} resolves to a non-public address")
    return url.strip()


__all__ = [
    "REDACT_PARAMS",
    "Resolver",
    "UnsafeUrlError",
    "check_public_https_url",
    "host_of",
    "redact_url",
    "system_resolver",
]
