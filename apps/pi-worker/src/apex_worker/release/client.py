"""HMAC-signed client for the APEX release API (`/api/v1/release/*`).

* Identity: a `rel_*` release agent id + `APEX_RELEASE_HMAC_SECRET` (base64, >= 32 bytes), both GitHub Environment
  secrets of `model-release`. The signature scheme is APEX-HMAC-SHA256-V1 from `apex_worker.agent.signing`, signed over
  the exact path+query and raw body bytes that are sent.
* Optional `APEX_VERCEL_BYPASS_SECRET` is sent as `x-vercel-protection-bypass`; the web proxy honours it only on the
  signed machine routes, so it never replaces the HMAC.
* Every release endpoint is idempotent (candidate insert is ON CONFLICT DO NOTHING, training runs upsert on
  workflow+run id+attempt, promotion of the champion is a no-op), so bounded retries on 5xx/429/network errors are
  safe. Each attempt is re-signed with a fresh nonce (nonces are single use server side).
* Secrets are never logged: `ReleaseConfig.__repr__` masks them and errors carry only status/code/message.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

import httpx

from apex_worker.agent.signing import sign

ENV_URL = "APEX_RELEASE_URL"
ENV_AGENT = "APEX_RELEASE_AGENT_ID"
ENV_SECRET = "APEX_RELEASE_HMAC_SECRET"  # noqa: S105 - the variable NAME, not a secret
ENV_BYPASS = "APEX_VERCEL_BYPASS_SECRET"
RELEASE_AGENT_RE = re.compile(r"^rel_[a-z0-9]{8,32}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
USER_AGENT = "apex-release-ci/1 (GitHub Actions; no order routing)"


class ReleaseConfigError(ValueError):
    """Missing or malformed release configuration (message never contains secret values)."""


class ReleaseError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"release API HTTP {status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True, repr=False)
class ReleaseConfig:
    base_url: str
    agent_id: str
    secret: bytes
    bypass_secret: str | None = None

    def __repr__(self) -> str:  # never leak secrets through logs or tracebacks
        bypass = "set" if self.bypass_secret else "unset"
        return f"ReleaseConfig(base_url={self.base_url!r}, agent_id={self.agent_id!r}, secret=***, bypass={bypass})"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ReleaseConfig:
        import base64
        import binascii

        env = os.environ if env is None else env
        url = (env.get(ENV_URL) or "").strip().rstrip("/")
        agent = (env.get(ENV_AGENT) or "").strip()
        raw_secret = (env.get(ENV_SECRET) or "").strip()
        missing = [name for name, v in ((ENV_URL, url), (ENV_AGENT, agent), (ENV_SECRET, raw_secret)) if not v]
        if missing:
            raise ReleaseConfigError(
                "release API not configured: missing " + ", ".join(missing) + " (GitHub Environment `model-release`)"
            )
        parts = urlsplit(url)
        if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS):
            raise ReleaseConfigError(f"{ENV_URL} must be an https:// URL")
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ReleaseConfigError(f"{ENV_URL} must not contain credentials, a query or a fragment")
        if not RELEASE_AGENT_RE.match(agent):
            raise ReleaseConfigError(f"{ENV_AGENT} must look like rel_<8-32 lowercase alphanumerics>")
        try:
            secret = base64.b64decode(raw_secret, validate=True)
        except (binascii.Error, ValueError):
            raise ReleaseConfigError(f"{ENV_SECRET} must be base64") from None
        if len(secret) < 32:
            raise ReleaseConfigError(f"{ENV_SECRET} must decode to at least 32 bytes")
        bypass = (env.get(ENV_BYPASS) or "").strip() or None
        return cls(url, agent, secret, bypass)


class ReleaseClient:
    def __init__(
        self,
        cfg: ReleaseConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ):
        self.cfg = cfg
        parts = urlsplit(cfg.base_url)
        self._origin = f"{parts.scheme}://{parts.netloc}"
        self._prefix = parts.path.rstrip("/")
        self._http = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=False,  # a redirect would drop the signed path; treat it as an error instead
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        self.max_attempts = max(1, max_attempts)
        self._sleep = sleep
        self._clock = clock
        self.requests = 0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ReleaseClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- transport -------------------------------------------------------------------------------
    def request(self, method: str, path: str, body: dict | None = None, query: Mapping[str, str] | None = None) -> dict:
        if not path.startswith("/api/v1/release/"):
            raise ValueError("release client only talks to /api/v1/release/* routes")
        raw = b"" if body is None else json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
        path_q = self._prefix + path + (("?" + urlencode(dict(query))) if query else "")
        last: ReleaseError | None = None
        for attempt in range(self.max_attempts):
            headers = sign(self.cfg.secret, self.cfg.agent_id, method, path_q, raw, timestamp=int(self._clock())).as_http()
            if body is not None:
                headers["Content-Type"] = "application/json"
            if self.cfg.bypass_secret:
                headers["x-vercel-protection-bypass"] = self.cfg.bypass_secret
            self.requests += 1
            try:
                resp = self._http.request(method, self._origin + path_q, content=raw if body is not None else None, headers=headers)
            except httpx.TransportError as exc:
                last = ReleaseError(0, "NETWORK", type(exc).__name__)
                self._backoff(attempt, None)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last = _error_of(resp)
                self._backoff(attempt, resp.headers.get("Retry-After"))
                continue
            if resp.status_code >= 300:
                raise _error_of(resp)
            try:
                data = resp.json()
            except ValueError:
                raise ReleaseError(resp.status_code, "INVALID_JSON", "response was not JSON") from None
            if not isinstance(data, dict):
                raise ReleaseError(resp.status_code, "INVALID_JSON", "response was not a JSON object")
            return data
        assert last is not None
        raise last

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        if attempt + 1 >= self.max_attempts:
            return
        try:
            wait = float(retry_after) if retry_after else 0.0
        except ValueError:
            wait = 0.0
        self._sleep(min(30.0, wait or 2.0 * (2**attempt)))

    # ---- endpoints -------------------------------------------------------------------------------
    def champions(self, *, include_artifacts: bool = False) -> dict:
        return self.request("GET", "/api/v1/release/champions", query={"include": "artifacts"} if include_artifacts else None)

    def policy(self) -> dict:
        return self.request("GET", "/api/v1/release/policy")

    def register_candidate(self, manifest: dict, artifact: bytes, training_run_id: str | None = None) -> dict:
        import base64

        body: dict = {"manifest": manifest, "artifact_b64": base64.b64encode(artifact).decode("ascii")}
        if training_run_id:
            body["training_run_id"] = training_run_id
        return self.request("POST", "/api/v1/release/models", body)

    def report_run(self, run: dict) -> dict:
        return self.request("POST", "/api/v1/release/training-runs", run)

    def promotion(self, action: str, model_id: str) -> dict:
        if action not in ("promote", "rollback"):
            raise ValueError("action must be promote or rollback")
        return self.request("POST", "/api/v1/release/promotions", {"action": action, "model_id": model_id})


def _error_of(resp: httpx.Response) -> ReleaseError:
    code, message = "HTTP_ERROR", resp.reason_phrase or ""
    try:
        err = resp.json().get("error") or {}
        code = str(err.get("code") or code)[:64]
        message = str(err.get("message") or message)[:240]
    except (ValueError, AttributeError):
        pass
    return ReleaseError(resp.status_code, code, message)


def client_from_env(**kwargs) -> ReleaseClient:
    return ReleaseClient(ReleaseConfig.from_env(), **kwargs)
