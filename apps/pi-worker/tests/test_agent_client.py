import json
import logging

import httpx
import pytest

from agent_helpers import AGENT, BASE, SECRET, Clock, client, server
from apex_worker.agent.client import (
    AgentClient,
    AgentHTTPError,
    AgentUnavailable,
    PayloadTooLarge,
    PostSpool,
    dedupe_key,
)
from apex_worker.agent.signing import sign


def _hb(agent=AGENT):
    return {"agent_id": agent, "sent_at": "2026-10-20T20:15:00Z", "worker_version": "0.1.0", "release_mode": "DEMO",
            "components": {}, "resources": {}}


def test_every_request_is_signed_with_a_fresh_nonce_and_verified():
    clock = Clock()
    seen = []
    srv = server(clock)

    def handler(req):
        seen.append(dict(req.headers))
        return srv.handle(req)

    c = AgentClient(BASE, AGENT, SECRET, transport=httpx.MockTransport(handler), clock=clock, sleep=lambda _s: None)
    assert c.get("config")["release_mode"] == "DEMO"
    assert c.post("heartbeat", _hb()).data["ok"] is True
    nonces = [h["x-apex-nonce"] for h in seen]
    assert len(set(nonces)) == 2 and all(len(n) >= 22 for n in nonces)
    assert all({"x-apex-signature", "x-apex-body-sha256", "x-apex-timestamp", "x-apex-agent-id"} <= set(h) for h in seen)


def test_signed_path_includes_the_exact_query_string():
    clock = Clock()
    srv = server(clock)
    captured = []

    def handler(req):
        captured.append(req)
        return srv.handle(req)

    c = AgentClient(BASE, AGENT, SECRET, transport=httpx.MockTransport(handler), clock=clock)
    c.get("positions", {"discord_user_id": "100000000000000001", "include_closed": None})
    req = captured[0]
    path = req.url.raw_path.decode()
    assert path == "/api/v1/agent/positions?discord_user_id=100000000000000001"
    h = sign(SECRET, AGENT, "GET", path, b"", timestamp=int(req.headers["x-apex-timestamp"]),
             nonce=req.headers["x-apex-nonce"])
    assert h.signature == req.headers["x-apex-signature"]


def test_5xx_retried_with_bounded_jittered_backoff_then_succeeds():
    clock = Clock()
    srv = server(clock)
    srv.fail_5xx_next = 2
    sleeps = []
    c = client(srv, clock, sleeps=sleeps)
    assert c.get("config")["config_version"] == 1
    assert [s for m, p, s in srv.log] == [503, 503, 200]
    assert len(sleeps) == 2 and all(0 <= s <= c.backoff_cap for s in sleeps) and sleeps[1] > sleeps[0]


def test_4xx_is_final_and_never_retried():
    clock = Clock()
    srv = server(clock)
    c = client(srv, clock)
    with pytest.raises(AgentHTTPError) as e:
        c.post("heartbeat", {"agent_id": AGENT})  # contract violation
    assert e.value.status == 422 and e.value.code == "CONTRACT_VIOLATION"
    assert len(srv.log) == 1


def test_nonce_replay_409_is_resigned_with_a_new_nonce():
    clock = Clock()
    srv = server(clock)
    calls = []

    def handler(req):
        calls.append(req.headers["x-apex-nonce"])
        if len(calls) == 1:
            srv.nonces.add(req.headers["x-apex-nonce"])  # as if a lost earlier attempt had used it
        return srv.handle(req)

    c = AgentClient(BASE, AGENT, SECRET, transport=httpx.MockTransport(handler), clock=clock, sleep=lambda _s: None)
    assert c.get("config")
    assert len(calls) == 2 and calls[0] != calls[1]


def test_429_honours_retry_after():
    clock = Clock()
    srv = server(clock)
    srv.rate_limit_next, srv.retry_after = 1, 3
    sleeps = []
    c = client(srv, clock, sleeps=sleeps)
    assert c.get("config")
    assert sleeps == [3.0]


def test_device_pending_is_not_retried_and_raises_code():
    clock = Clock()
    srv = server(clock, status="pending_approval")
    c = client(srv, clock)
    assert c.get("config")  # config allows a pending device
    with pytest.raises(AgentHTTPError) as e:
        c.get("work")
    assert e.value.code == "DEVICE_PENDING_APPROVAL" and len(srv.log) == 2


def test_body_cap_refuses_before_sending():
    clock = Clock()
    srv = server(clock)
    c = client(srv, clock)
    with pytest.raises(PayloadTooLarge):
        c.post("heartbeat", {"blob": "x" * (300 * 1024)})
    assert srv.log == []
    # the observations route allows 2 MB
    c.post("observations", {"observations": [], "bars": [], "pad": "x" * (300 * 1024)})


def test_response_cap():
    big = b'{"x":"' + b"a" * (5 * 1024 * 1024) + b'"}'
    c = AgentClient(BASE, AGENT, SECRET, transport=httpx.MockTransport(lambda r: httpx.Response(200, content=big)),
                    sleep=lambda _s: None)
    with pytest.raises(AgentHTTPError) as e:
        c.get("config")
    assert e.value.code == "RESPONSE_TOO_LARGE"


def test_clock_skew_measured_and_warned(caplog):
    clock = Clock()
    srv = server(clock)
    srv.skew_seconds = 45  # inside the 60 s server tolerance, above the 30 s warning
    c = client(srv, clock)
    with caplog.at_level(logging.WARNING, logger="apex.agent.client"):
        c.get("config")  # no server_time -> HTTP Date header absent -> unknown
        c.post("heartbeat", _hb())
    assert c.skew_seconds == pytest.approx(45, abs=1)
    assert c.clock_synced is False
    assert "clock skew" in caplog.text


def test_clock_skew_rejection_is_final():
    clock = Clock()
    srv = server(clock)
    srv.skew_seconds = 120
    c = client(srv, clock)
    with pytest.raises(AgentHTTPError) as e:
        c.get("config")
    assert e.value.code == "CLOCK_SKEW" and c.skew_seconds == pytest.approx(120, abs=1)


def test_spool_on_network_failure_and_ordered_replay(tmp_path):
    clock = Clock()
    srv = server(clock)
    spool = PostSpool(tmp_path / "s.sqlite3")
    c = client(srv, clock, spool=spool)
    srv.down = True
    r1 = c.post("heartbeat", _hb(), spool_key="hb:1")
    r2 = c.post("observations", {"observations": [], "bars": []}, spool_key="obs:1")
    assert r1.spooled and r2.spooled and not r1.sent and spool.pending_count() == 2
    assert c.offline
    srv.down = False
    clock.advance(3600)
    counts = c.flush_spool()
    assert counts == {"sent": 2, "rejected": 0, "remaining": 0}
    assert [p for m, p, s in srv.log] == ["heartbeat", "observations"]
    assert c.flush_spool()["sent"] == 0


def test_new_writes_queue_behind_pending_spool(tmp_path):
    clock = Clock()
    srv = server(clock)
    spool = PostSpool(tmp_path / "s.sqlite3")
    c = client(srv, clock, spool=spool)
    srv.down = True
    c.post("heartbeat", _hb(), spool_key="hb:first")
    srv.down = False
    clock.advance(3600)  # breaker expired
    res = c.post("observations", {"observations": [], "bars": []}, spool_key="obs:second")
    assert res.sent and spool.pending_count() == 0
    assert [p for m, p, s in srv.log] == ["heartbeat", "observations"]


def test_spool_key_dedupes_after_a_lost_response(tmp_path):
    clock = Clock()
    srv = server(clock)
    spool = PostSpool(tmp_path / "s.sqlite3")
    lost = {"n": 1}

    def handler(req):
        resp = srv.handle(req)
        if lost["n"] and req.url.path.endswith("/catalysts"):
            lost["n"] -= 1
            raise httpx.ReadError("response lost after commit", request=req)
        return resp

    c = AgentClient(BASE, AGENT, SECRET, transport=httpx.MockTransport(handler), clock=clock, spool=spool,
                    sleep=lambda _s: None, max_attempts=1)
    cat = {"items": []}
    assert c.post("catalysts", cat, spool_key=dedupe_key("cat", "x")).spooled
    assert not c.post("catalysts", cat, spool_key=dedupe_key("cat", "x")).sent  # still offline: queued once
    assert spool.pending_count() == 1
    clock.advance(3600)
    assert c.flush_spool()["sent"] == 1


def test_rejected_spool_rows_do_not_block_the_queue(tmp_path):
    clock = Clock()
    srv = server(clock)
    spool = PostSpool(tmp_path / "s.sqlite3")
    c = client(srv, clock, spool=spool)
    srv.down = True
    c.post("heartbeat", {"agent_id": AGENT}, spool_key="bad:1")  # will be a 422 on replay
    c.post("heartbeat", _hb(), spool_key="good:1")
    srv.down = False
    clock.advance(3600)
    assert c.flush_spool() == {"sent": 1, "rejected": 1, "remaining": 0}
    assert spool.rejected_count() == 1


def test_droppable_rows_are_shed_first_when_spool_is_full(tmp_path):
    spool = PostSpool(tmp_path / "s.sqlite3", max_rows=3)
    assert spool.put("obs:1", "observations", b"{}", droppable=True)
    assert spool.put("sig:1", "signals", b"{}")
    assert spool.put("obs:2", "observations", b"{}", droppable=True)
    assert spool.put("sig:2", "signals", b"{}")  # forces out the oldest droppable
    keys = [r["dedupe_key"] for r in spool.pending()]
    assert keys == ["sig:1", "obs:2", "sig:2"]
    assert spool.put("sig:3", "signals", b"{}")  # non-droppable writes are never refused
    assert "sig:3" in [r["dedupe_key"] for r in spool.pending()]


def test_secrets_and_fill_bodies_never_logged(caplog, tmp_path):
    clock = Clock()
    srv = server(clock)
    srv.down = True
    c = client(srv, clock, spool=PostSpool(tmp_path / "s.sqlite3"), bypass_secret="TEST-ONLY-bypass-value-123")
    body = {"discord_user_id": "100000000000000001", "event": {"fill": {"price": "123.4567", "quantity": "77"}}}
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(AgentUnavailable):
            c.post("positions/events", body)
        c.post("positions/events", body, spool_key="pe:1")
    text = caplog.text + repr(c)
    for needle in ("123.4567", SECRET.decode(), "TEST-ONLY-bypass-value-123", "100000000000000001"):
        assert needle not in text


def test_https_required_and_secret_length():
    with pytest.raises(ValueError):
        AgentClient("http://example.com", AGENT, SECRET)
    with pytest.raises(ValueError):
        AgentClient(BASE, AGENT, b"short")
    AgentClient("http://localhost:3000", AGENT, SECRET).close()


def test_vercel_bypass_header_sent_only_when_configured():
    clock = Clock()
    srv = server(clock)
    seen = []

    def handler(req):
        seen.append(req.headers.get("x-vercel-protection-bypass"))
        return srv.handle(req)

    for bypass in (None, "TEST-ONLY-bypass"):
        AgentClient(BASE, AGENT, SECRET, transport=httpx.MockTransport(handler), clock=clock,
                    bypass_secret=bypass).get("config")
    assert seen == [None, "TEST-ONLY-bypass"]


def test_non_json_protection_page_is_reported_clearly():
    c = AgentClient(BASE, AGENT, SECRET, transport=httpx.MockTransport(
        lambda r: httpx.Response(401, text="<html>Vercel Authentication</html>")), sleep=lambda _s: None)
    with pytest.raises(AgentHTTPError) as e:
        c.get("config")
    assert e.value.code == "NON_JSON_RESPONSE" and "BYPASS" in e.value.message


def test_dedupe_key_hashes_long_keys():
    k = dedupe_key("sigevt", "x" * 300)
    assert k.startswith("sigevt:") and len(k) < 80
    assert json.dumps(k)
