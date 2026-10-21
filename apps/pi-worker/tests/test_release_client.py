"""Release-API client: HMAC signing over the exact bytes sent, retries, error mapping, config validation."""

import base64
import hashlib
import hmac
import json

import httpx
import pytest

from apex_worker.agent.signing import canonical_request
from apex_worker.release.client import ReleaseClient, ReleaseConfig, ReleaseConfigError, ReleaseError

SECRET = bytes(range(32))  # TEST-ONLY
ENV = {
    "APEX_RELEASE_URL": "https://apex.example.test",
    "APEX_RELEASE_AGENT_ID": "rel_ci0000test",
    "APEX_RELEASE_HMAC_SECRET": base64.b64encode(SECRET).decode(),
}


def verify(request: httpx.Request, secret: bytes = SECRET) -> None:
    """Server-side check, mirroring apps/web/src/server/security/hmac.ts."""
    h = request.headers
    body = request.content
    assert h["x-apex-body-sha256"] == hashlib.sha256(body).hexdigest()
    path_q = request.url.raw_path.decode()
    canon = canonical_request(h["x-apex-agent-id"], request.method, path_q, h["x-apex-timestamp"], h["x-apex-nonce"],
                              h["x-apex-body-sha256"])
    assert hmac.compare_digest(h["x-apex-signature"], hmac.new(secret, canon.encode(), hashlib.sha256).hexdigest())


def make(handler, env=None, **kw) -> ReleaseClient:
    return ReleaseClient(ReleaseConfig.from_env(env or ENV), transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw)


def test_signed_get_and_post_verify_like_the_server():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        verify(req)
        seen.append((req.method, req.url.raw_path.decode(), req.content))
        return httpx.Response(200, json={"ok": True, "champions": {}})

    with make(handler) as c:
        c.champions(include_artifacts=True)
        c.report_run({"workflow": "drift", "status": "succeeded"})
    assert seen[0][:2] == ("GET", "/api/v1/release/champions?include=artifacts") and seen[0][2] == b""
    assert seen[1][0] == "POST" and json.loads(seen[1][2]) == {"status": "succeeded", "workflow": "drift"}


def test_base_url_path_prefix_is_signed_and_bypass_header_sent():
    def handler(req: httpx.Request) -> httpx.Response:
        verify(req)
        assert req.url.raw_path.decode() == "/apex/api/v1/release/policy"
        assert req.headers["x-vercel-protection-bypass"] == "bypass-value"
        return httpx.Response(200, json={"mode": "DEMO"})

    env = ENV | {"APEX_RELEASE_URL": "https://apex.example.test/apex/", "APEX_VERCEL_BYPASS_SECRET": "bypass-value"}
    with make(handler, env) as c:
        assert c.policy()["mode"] == "DEMO"


def test_retries_5xx_with_fresh_nonce_then_succeeds():
    nonces = []

    def handler(req: httpx.Request) -> httpx.Response:
        verify(req)
        nonces.append(req.headers["x-apex-nonce"])
        if len(nonces) < 3:
            return httpx.Response(503, json={"error": {"code": "UNAVAILABLE", "message": "paused"}})
        return httpx.Response(200, json={"id": "run-1"})

    with make(handler) as c:
        assert c.report_run({"workflow": "drift"}) == {"id": "run-1"}
    assert len(nonces) == 3 and len(set(nonces)) == 3  # nonces are single use server side


def test_client_errors_are_not_retried_and_carry_code():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(422, json={"error": {"code": "BAD_SIGNATURE", "message": "manifest signature invalid"}})

    with make(handler) as c, pytest.raises(ReleaseError) as ei:
        c.register_candidate({"model_id": "mdl_x"}, b"{}")
    assert ei.value.status == 422 and ei.value.code == "BAD_SIGNATURE" and len(calls) == 1


def test_network_errors_exhaust_attempts():
    def handler(req):
        raise httpx.ConnectError("down", request=req)

    with make(handler, max_attempts=2) as c, pytest.raises(ReleaseError) as ei:
        c.policy()
    assert ei.value.code == "NETWORK"


def test_redirects_are_refused():
    def handler(req):
        return httpx.Response(307, headers={"location": "https://elsewhere.example.test/"})

    with make(handler) as c, pytest.raises(ReleaseError) as ei:
        c.policy()
    assert ei.value.status == 307


def test_only_release_routes():
    with make(lambda r: httpx.Response(200, json={})) as c, pytest.raises(ValueError):
        c.request("GET", "/api/v1/agent/work")


@pytest.mark.parametrize(
    "patch,needle",
    [
        ({"APEX_RELEASE_URL": ""}, "missing APEX_RELEASE_URL"),
        ({"APEX_RELEASE_URL": "http://apex.example.test"}, "https://"),
        ({"APEX_RELEASE_URL": "https://user:pw@apex.example.test"}, "credentials"),
        ({"APEX_RELEASE_AGENT_ID": "agt_pi00000001"}, "rel_"),
        ({"APEX_RELEASE_HMAC_SECRET": base64.b64encode(b"short").decode()}, "32 bytes"),
        ({"APEX_RELEASE_HMAC_SECRET": "not base64!!"}, "base64"),
    ],
)
def test_config_validation(patch, needle):
    with pytest.raises(ReleaseConfigError) as ei:
        ReleaseConfig.from_env(ENV | patch)
    assert needle in str(ei.value)
    assert ENV["APEX_RELEASE_HMAC_SECRET"] not in str(ei.value)


def test_localhost_http_allowed_and_repr_masks_secret():
    cfg = ReleaseConfig.from_env(ENV | {"APEX_RELEASE_URL": "http://127.0.0.1:3000"})
    text = repr(cfg)
    assert "***" in text and ENV["APEX_RELEASE_HMAC_SECRET"] not in text and "SECRET" not in text.upper().replace("SECRET=***", "")
