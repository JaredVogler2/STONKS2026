from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from apex_worker.catalysts.form4 import parse_form4
from apex_worker.catalysts.http import (
    SEC_UA_ERROR,
    DiskCache,
    FetchError,
    HttpClientConfig,
    MemoryCache,
    PoliteHttpClient,
    QuarantineRecord,
    QuarantineStore,
    TokenBucket,
    UnsafeUrlError,
    UserAgentError,
    check_public_https_url,
    parse_retry_after,
    redact_url,
    safe_parse,
    validate_sec_user_agent,
)

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "catalysts"
UA = "APEX Research Demo ops@example.com"
SUB_URL = "https://data.sec.gov/submissions/CIK0009990001.json"


class FakeTime:
    def __init__(self) -> None:
        self.t = 0.0
        self.start = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds

    def now(self) -> datetime:
        return self.start + timedelta(seconds=self.t)


def _client(handler, *, config=None, cache=None, ft=None, rps=2.0):
    ft = ft or FakeTime()
    cfg = config or HttpClientConfig.for_sec(UA, requests_per_second=rps)
    client = PoliteHttpClient(
        cfg,
        transport=httpx.MockTransport(handler),
        bucket=TokenBucket(cfg.requests_per_second, time_source=ft),
        cache=cache,
        time_source=ft,
        rng=random.Random(7),
    )
    return client, ft


# --- User-Agent & rate ceiling --------------------------------------------------------------
@pytest.mark.parametrize("ua", [None, "", "   ", "APEX Research", "python-httpx/0.28 a@b.com", "x@y.io"])
def test_sec_refuses_without_descriptive_user_agent(ua):
    with pytest.raises(UserAgentError) as err:
        HttpClientConfig.for_sec(ua)
    assert SEC_UA_ERROR in str(err.value)
    assert "SEC requires a descriptive User-Agent with contact email" in str(err.value)


def test_valid_sec_user_agent_and_default_rate():
    assert validate_sec_user_agent(UA) == UA
    cfg = HttpClientConfig.for_sec(UA)
    assert cfg.requests_per_second == 2.0 and cfg.sec_compliant


def test_sec_hard_ceiling_10_per_second():
    HttpClientConfig.for_sec(UA, requests_per_second=10)
    with pytest.raises(ValueError, match="10/s"):
        HttpClientConfig.for_sec(UA, requests_per_second=10.5)


def test_sec_ceiling_cannot_be_bypassed():
    with pytest.raises(ValueError, match="10/s"):
        HttpClientConfig(user_agent=UA, requests_per_second=25, sec_compliant=True, bucket_name="sec")
    with pytest.raises(UserAgentError):
        HttpClientConfig(user_agent="anonymous", sec_compliant=True)
    cfg = HttpClientConfig.for_sec(UA)
    fast = PoliteHttpClient(
        cfg, transport=httpx.MockTransport(lambda r: httpx.Response(200)), bucket=TokenBucket(50.0)
    )
    with pytest.raises(ValueError, match="ceiling"):
        fast.get(SUB_URL)


def test_generic_client_refuses_sec_hosts():
    client, _ = _client(lambda r: httpx.Response(200), config=HttpClientConfig.for_public("APEX feed reader"))
    with pytest.raises(UserAgentError, match="SEC requires"):
        client.get(SUB_URL)


# --- rate limiting ---------------------------------------------------------------------------
def test_token_bucket_spaces_requests_without_real_sleep():
    ft = FakeTime()
    bucket = TokenBucket(2.0, time_source=ft)
    for _ in range(5):
        bucket.acquire()
    assert sum(ft.sleeps) == pytest.approx(2.0)
    assert all(s == pytest.approx(0.5) for s in ft.sleeps)


def test_client_paces_and_sends_identifying_headers():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b"{}")

    client, ft = _client(handler)
    for _ in range(3):
        client.get(SUB_URL)
    assert sum(ft.sleeps) == pytest.approx(1.0)
    assert all(r.headers["user-agent"] == UA for r in seen)
    assert "gzip" in seen[0].headers["accept-encoding"]


# --- 403 / 429 / backoff --------------------------------------------------------------------
def test_403_then_429_retry_after_then_success():
    responses = iter(
        [
            httpx.Response(403, content=b"blocked"),
            httpx.Response(429, headers={"Retry-After": "7"}, content=b"slow down"),
            httpx.Response(200, content=b'{"ok": true}'),
        ]
    )
    client, ft = _client(lambda r: next(responses))
    result = client.get(SUB_URL)
    assert result.status == 200 and result.attempts == 3
    assert [s for s, _ in client.request_log] == [SUB_URL] * 3
    assert [code for _, code in client.request_log] == [403, 429, 200]
    retry_sleeps = [s for s in ft.sleeps if s > 0.5]
    assert retry_sleeps[0] >= 60.0  # SEC 403: conservative minimum cooldown
    assert retry_sleeps[1] >= 7.0  # Retry-After honoured


def test_backoff_is_exponential_with_jitter():
    client, _ = _client(lambda r: httpx.Response(503))
    delays = [client._backoff(n) for n in range(1, 6)]
    for n, d in enumerate(delays, start=1):
        base = min(client.config.backoff_cap_seconds, 2 ** (n - 1))
        assert base / 2 <= d <= base


def test_persistent_403_gives_up_with_structured_error():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(403)

    client, _ = _client(handler)
    with pytest.raises(FetchError) as err:
        client.get(SUB_URL)
    assert err.value.reason_code == "HTTP_403_RETRIES_EXHAUSTED"
    assert len(calls) == client.config.max_forbidden_retries + 1


def test_non_retryable_status_fails_fast():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(404)

    client, _ = _client(handler)
    with pytest.raises(FetchError) as err:
        client.get(SUB_URL)
    assert err.value.reason_code == "HTTP_404" and len(calls) == 1


def test_transport_errors_are_retried():
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, content=b"ok")

    client, ft = _client(handler)
    assert client.get(SUB_URL).content == b"ok"
    assert state["n"] == 2 and any(s >= 0.5 for s in ft.sleeps)


def test_retry_after_http_date():
    now = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)
    assert parse_retry_after("120", now) == 120.0
    assert parse_retry_after("Tue, 20 Oct 2026 14:00:30 GMT", now) == pytest.approx(30.0)
    assert parse_retry_after("garbage", now) is None


# --- caching -------------------------------------------------------------------------------
def test_etag_conditional_revalidation_memory_cache():
    seen = []

    def handler(request):
        seen.append(dict(request.headers))
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, headers={"ETag": '"v1"'}, content=b"payload-v1")

    cache = MemoryCache()
    client, _ = _client(handler, cache=cache)
    first = client.get(SUB_URL)
    second = client.get(SUB_URL)
    assert not first.from_cache and second.from_cache and second.revalidated
    assert second.content == b"payload-v1"
    assert "if-none-match" not in seen[0] and seen[1]["if-none-match"] == '"v1"'


def test_disk_cache_last_modified(tmp_path):
    lm = "Mon, 19 Oct 2026 10:00:00 GMT"
    seen = []

    def handler(request):
        seen.append(request.headers.get("if-modified-since"))
        if request.headers.get("if-modified-since") == lm:
            return httpx.Response(304)
        return httpx.Response(200, headers={"Last-Modified": lm}, content=b"body")

    c1, _ = _client(handler, cache=DiskCache(tmp_path / "cache"))
    c1.get(SUB_URL)
    c2, _ = _client(handler, cache=DiskCache(tmp_path / "cache"))  # fresh process, same cache dir
    res = c2.get(SUB_URL)
    assert res.from_cache and res.content == b"body"
    assert seen == [None, lm]
    files = list((tmp_path / "cache").glob("*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text())["last_modified"] == lm


def test_fresh_ttl_skips_network():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, content=b"x")

    cfg = HttpClientConfig.for_sec(UA, fresh_ttl_seconds=3600)
    client, _ = _client(handler, config=cfg, cache=MemoryCache())
    client.get(SUB_URL)
    assert client.get(SUB_URL).from_cache
    assert len(calls) == 1


# --- quarantine ------------------------------------------------------------------------------
def test_unparseable_document_is_quarantined_not_raised(tmp_path):
    weird = (FIX / "quarantine_weird_html_in_xml.xml").read_bytes()
    client, _ = _client(lambda r: httpx.Response(200, content=weird))
    store = QuarantineStore(tmp_path / "q.jsonl")
    url = "https://www.sec.gov/Archives/edgar/data/9990001/000999010126000007/form4.xml"
    parsed, fetched, record = client.fetch_parsed(
        url, parse_form4, source="sec_edgar", parser_version="form4/1.0.0", quarantine=store
    )
    assert parsed is None and fetched is not None
    assert isinstance(record, QuarantineRecord)
    assert record.reason_code == "PARSE_ERROR" and record.content_length == len(weird)
    assert record.content_sha256 == fetched.sha256
    assert len(store) == 1
    line = json.loads((tmp_path / "q.jsonl").read_text().strip())
    assert line["reason_code"] == "PARSE_ERROR" and "content" not in line


def test_fetch_failure_is_quarantined():
    client, _ = _client(lambda r: httpx.Response(429, headers={"Retry-After": "1"}))
    parsed, fetched, record = client.fetch_parsed(SUB_URL, json.loads, source="sec_edgar")
    assert parsed is None and fetched is None
    assert (
        record is not None
        and record.reason_code == "HTTP_429_RETRIES_EXHAUSTED"
        and record.http_status == 429
    )


def test_safe_parse_returns_value_or_record():
    now = datetime(2026, 10, 20, tzinfo=UTC)
    assert safe_parse(b'{"a": 1}', json.loads, source="x", url="https://a.example/", now=now) == {"a": 1}
    bad = safe_parse(b"{nope", json.loads, source="x", url="https://a.example/", now=now)
    assert isinstance(bad, QuarantineRecord) and bad.error_type == "JSONDecodeError"


def test_response_size_cap():
    cfg = HttpClientConfig.for_sec(UA, max_bytes=10)
    client, _ = _client(lambda r: httpx.Response(200, content=b"x" * 11), config=cfg)
    with pytest.raises(FetchError, match="RESPONSE_TOO_LARGE"):
        client.get(SUB_URL)


def test_redirect_to_insecure_target_is_refused():
    client, _ = _client(lambda r: httpx.Response(302, headers={"Location": "http://evil.example/x"}))
    with pytest.raises(UnsafeUrlError):
        client.get(SUB_URL)


# --- URL safety ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "http://feeds.demo-wire.example/rss",
        "https://127.0.0.1/rss",
        "https://[::1]/rss",
        "https://10.0.0.5/rss",
        "https://169.254.169.254/latest/meta-data",
        "https://2130706433/rss",
        "https://0x7f000001/rss",
        "https://localhost/rss",
        "https://printer.local/rss",
        "https://svc.internal/rss",
        "https://user:pw@feeds.demo-wire.example/rss",
        "https://feeds.demo-wire.example:8443/rss",
        "ftp://feeds.demo-wire.example/rss",
    ],
)
def test_ssrf_guard_rejects(url):
    with pytest.raises(UnsafeUrlError):
        check_public_https_url(url)


def test_ssrf_guard_dns_rebinding_defence():
    assert check_public_https_url("https://feeds.demo-wire.example/rss")
    with pytest.raises(UnsafeUrlError, match="non-public"):
        check_public_https_url("https://feeds.demo-wire.example/rss", resolver=lambda h: ["10.1.2.3"])
    assert check_public_https_url("https://feeds.demo-wire.example/rss", resolver=lambda h: ["93.184.216.34"])


def test_redact_url_masks_keys():
    url = "https://api.stlouisfed.org/fred/series/observations?series_id=DMCPI&api_key=SECRET123"
    red = redact_url(url)
    assert "SECRET123" not in red and "api_key=REDACTED" in red and "series_id=DMCPI" in red
