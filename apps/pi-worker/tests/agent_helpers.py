"""Shared fakes for the connected-mode tests (no network: httpx.MockTransport over the in-process fake API)."""

from __future__ import annotations

from datetime import UTC, datetime

from apex_worker.agent.client import AgentClient, PostSpool
from apex_worker.demo.agent_server import FakeAgentServer

SECRET = b"TEST-ONLY-agent-hmac-secret-00000"  # obviously fake, >= 32 bytes
AGENT = "agt_testpi0001"
BASE = "https://apex.test"
T0 = datetime(2026, 10, 20, 20, 15, tzinfo=UTC)  # Tuesday, after the close
DUID_A, DUID_B = "100000000000000001", "100000000000000002"


class Clock:
    """Manually advanced epoch-seconds clock shared by client and fake server."""

    def __init__(self, start: datetime = T0):
        self.t = start.timestamp()

    def __call__(self) -> float:
        return self.t

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.t, UTC)

    def advance(self, seconds: float) -> None:
        self.t += seconds


def server(clock: Clock, *, mode: str = "DEMO", status: str = "active", x25519: str | None = None) -> FakeAgentServer:
    s = FakeAgentServer(SECRET, mode=mode, clock=clock)
    s.register_device(AGENT, status, x25519)
    return s


def client(srv: FakeAgentServer, clock: Clock, *, spool: PostSpool | None = None, sleeps: list | None = None,
           agent: str = AGENT, **kw) -> AgentClient:
    return AgentClient(BASE, agent, SECRET, transport=srv.transport(), spool=spool, clock=clock,
                       sleep=(sleeps.append if sleeps is not None else (lambda _s: None)), rng=lambda: 0.5, **kw)


def fill(price: str, qty: str, at: datetime) -> dict:
    from apex_worker.timeutil import iso_utc

    return {"price": price, "quantity": qty, "fees": "0", "fill_at": iso_utc(at)}
