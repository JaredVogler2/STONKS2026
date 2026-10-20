"""In-process DEMO/test double of the Vercel agent API (an `httpx.MockTransport` handler; no sockets).

It mirrors the semantics of apps/web/src/server/services/{agents,pairing,ingest,positions,models}.ts closely
enough to exercise the Pi client end-to-end: HMAC verification with +/-60 s tolerance and single-use nonces,
device status (pending / active / revoked), body caps, contract validation, idempotent writes (signal revision
immutability, event dedupe keys, scan ids, assessment keys, outbox event keys), the signal transition table,
outbox lease/ack, X25519 secret envelopes and the model registry. Faults (down, 5xx, 429, clock skew) can be
injected. It is NEVER a substitute for the real server: it holds everything in memory and is DEMO-only.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import time
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from apex_worker.agent.canonical import b64url, canonical_json, from_b64url
from apex_worker.agent.client import API_PREFIX, body_cap
from apex_worker.agent.crypto import ENVELOPE_ALG, ENVELOPE_INFO, fingerprint
from apex_worker.agent.pairing import code_hash, pairing_message
from apex_worker.agent.signing import canonical_request
from apex_worker.contracts import ContractViolation, validate
from apex_worker.portfolio.ledger import LedgerError
from apex_worker.services.positions import LocalPositionService, PositionNotFound, ServiceError
from apex_worker.storage.local import LocalStore
from apex_worker.timeutil import iso_utc

ALLOWED = {
    "": {"DISCOVERED"},
    "DISCOVERED": {"EVALUATING", "DISCARDED"},
    "EVALUATING": {"QUALIFIED", "DISCARDED", "NO_TRADE"},
    "QUALIFIED": {"PUBLISHED", "DISCARDED", "NO_TRADE", "EXPIRED"},
    "PUBLISHED": {"ACTIVE", "EXPIRED", "INVALIDATED"},
    "ACTIVE": {"CLOSED", "INVALIDATED"},
}
TERMINAL = {"CLOSED", "DISCARDED", "NO_TRADE", "EXPIRED", "INVALIDATED"}
PENDING_OK = {"pair", "heartbeat", "config"}
PRIORITY = {"URGENT": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}


class Http(Exception):
    def __init__(self, status: int, code: str, message: str = "", **extra):
        super().__init__(code)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def seal_envelope(device_public_b64: str, meta: dict, plaintext: str) -> dict:
    """Python mirror of apps/web/src/server/security/envelope.ts sealToDevice (server side, DEMO only)."""
    eph = X25519PrivateKey.generate()
    epk = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    shared = eph.exchange(X25519PublicKey.from_public_bytes(from_b64url(device_public_b64)))
    salt, iv = os.urandom(16), os.urandom(12)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=ENVELOPE_INFO + epk).derive(shared)
    sealed = AESGCM(key).encrypt(iv, plaintext.encode(), canonical_json(meta).encode())
    return {"v": 1, "alg": ENVELOPE_ALG, "epk": b64url(epk), "salt": b64url(salt), "iv": b64url(iv),
            "ct": b64url(sealed[:-16]), "tag": b64url(sealed[-16:]), "meta": meta}


class FakeAgentServer:
    def __init__(self, secret: bytes, *, mode: str = "DEMO", clock: Callable[[], float] = time.time):
        self.secret = secret
        self.mode = mode
        self.is_demo = mode == "DEMO"
        self.clock = clock
        self.devices: dict[str, dict] = {}
        self.codes: dict[str, dict] = {}
        self.nonces: set[str] = set()
        # fault injection
        self.down = False
        self.fail_5xx_next = 0
        self.rate_limit_next = 0
        self.retry_after = 1
        self.skew_seconds = 0
        # observability for assertions
        self.log: list[tuple[str, str, int]] = []
        self.duplicates: Counter = Counter()
        self.heartbeats: list[dict] = []
        self.config: dict = {
            "release_mode": mode, "config_version": 1, "compliance": {},
            "slots": {"max_slots": 25, "reserve_for_positions": 10},
            "cadence": {"intraday_minutes": 15, "nightly_refresh_et": "19:30"},
            "risk": {"min_quality": 70, "max_new_per_session": 3, "max_open_per_sector": 2,
                     "research_setups_enabled": True, "research_min_quality": 75, "entry_valid_sessions": 2},
            "models": {"promotion_mode": "manual", "auto_requires_status": "CALIBRATED", "poll_minutes": 45},
            "universe": {"include": [], "exclude": []},
            "strategies": {},
            "budgets": {"actions_monthly_minutes": 1500, "train_timeout_minutes": 45, "drift_enabled": True,
                        "alpaca_daily_requests": 150_000},
            "training": {"last_success_at": None, "last_run_at": None, "last_run_status": None, "champions": []},
        }
        # canonical records
        self.signals: dict[tuple[str, int], dict] = {}
        self.heads: dict[str, dict] = {}
        self.signal_events: dict[str, dict] = {}
        self.outbox: dict[str, dict] = {}
        self.outbox_keys: dict[str, str] = {}
        self.scans: dict[str, dict] = {}
        self.candidates: dict[str, dict] = {}
        self.feature_snapshots: dict[tuple, dict] = {}
        self.regimes: dict[str, dict] = {}
        self.quotes: dict[str, dict] = {}
        self.bars: dict[tuple, dict] = {}
        self.catalysts: dict[str, dict] = {}
        self.assessments: dict[tuple, dict] = {}
        self.ledger_store = LocalStore(":memory:")
        self.positions = LocalPositionService(self.ledger_store, is_demo=self.is_demo)
        self.champions: list[dict] = []
        self.artifacts: dict[str, bytes] = {}
        self.activations: list[dict] = []
        self.integrations: dict[str, dict] = {}
        self.secret_acks: list[tuple[str, int]] = []
        self.monitored_include_user_id = True
        self.member_actions: list[dict] = []
        self.high_priority: dict[str, bool] = {}

    # ---- setup helpers --------------------------------------------------------------------------------------
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.clock() + self.skew_seconds, UTC)

    def start_pairing(self, code: str, ttl_seconds: int = 600) -> None:
        self.codes[code_hash(code)] = {"expires": self.clock() + ttl_seconds, "used": False, "failed": 0}

    def register_device(self, agent_id: str, status: str = "active", x25519: str | None = None) -> None:
        self.devices[agent_id] = {"status": status, "x25519": x25519, "ed25519": None, "fingerprint": None}

    def approve(self, agent_id: str) -> None:
        self.devices[agent_id]["status"] = "active"

    def set_integration(self, provider: str, fields: dict, version: int) -> None:
        self.integrations[provider] = {"fields": dict(fields), "version": version}

    def publish_champion(self, manifest: dict, artifact: bytes) -> None:
        self.champions = [m for m in self.champions if m["strategy_id"] != manifest["strategy_id"]] + [manifest]
        self.artifacts[manifest["model_id"]] = artifact

    # ---- transport ------------------------------------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        route = request.url.path.removeprefix(API_PREFIX).strip("/")
        if self.down:
            raise httpx.ConnectError("injected outage", request=request)
        try:
            if self.fail_5xx_next > 0:
                self.fail_5xx_next -= 1
                raise Http(503, "SERVICE_UNAVAILABLE", "injected")
            if self.rate_limit_next > 0:
                self.rate_limit_next -= 1
                self.log.append((request.method, route, 429))
                return httpx.Response(429, headers={"Retry-After": str(self.retry_after)},
                                      json={"error": {"code": "RATE_LIMITED", "message": "injected"}})
            body = request.content or b""
            if len(body) > body_cap(route):
                raise Http(413, "PAYLOAD_TOO_LARGE", "body too large")
            agent = self._auth(request, route, body)
            data = json.loads(body) if body else {}
            out = self._dispatch(request.method, route, agent, data, request.url.params)
            self.log.append((request.method, route, 200))
            return httpx.Response(200, json=out)
        except Http as e:
            self.log.append((request.method, route, e.status))
            return httpx.Response(e.status, json={"error": {"code": e.code, "message": e.message, **e.extra}})

    def _auth(self, request: httpx.Request, route: str, body: bytes) -> str:
        h = request.headers
        agent, ts, nonce = h.get("x-apex-agent-id"), h.get("x-apex-timestamp"), h.get("x-apex-nonce")
        body_sha, sig = h.get("x-apex-body-sha256"), h.get("x-apex-signature")
        if not all((agent, ts, nonce, body_sha, sig)):
            raise Http(401, "UNAUTHENTICATED", "missing headers")
        if hashlib.sha256(body).hexdigest() != body_sha:
            raise Http(401, "UNAUTHENTICATED", "body hash")
        server_now = int(self.clock()) + self.skew_seconds
        if abs(server_now - int(ts)) > 60:
            raise Http(401, "CLOCK_SKEW", "outside 60 s", server_time=server_now)
        path = request.url.raw_path.decode()
        expected = hmac.new(self.secret, canonical_request(agent, request.method, path, ts, nonce, body_sha).encode(),
                            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise Http(401, "UNAUTHENTICATED", "bad signature")
        dev = self.devices.get(agent)
        if dev is None and route != "pair":
            raise Http(403, "DEVICE_NOT_PAIRED", "device is not paired")
        if dev and dev["status"] == "revoked":
            raise Http(403, "DEVICE_REVOKED", "revoked")
        if dev and dev["status"] == "pending_approval" and route not in PENDING_OK:
            raise Http(403, "DEVICE_PENDING_APPROVAL", "awaiting approval")
        if nonce in self.nonces:
            raise Http(409, "NONCE_REPLAYED", "nonce already used")
        self.nonces.add(nonce)
        return str(agent)

    def _dispatch(self, method: str, route: str, agent: str, body: dict, params) -> dict:
        if route.startswith("models/") and route.endswith("/artifact") and method == "GET":
            return self._artifact(route.split("/")[1])
        table = {
            ("POST", "pair"): self._pair, ("POST", "heartbeat"): self._heartbeat, ("GET", "config"): self._config,
            ("POST", "observations"): self._observations, ("POST", "scan-runs"): self._scan,
            ("POST", "signals"): self._signal, ("POST", "signals/events"): self._signal_events,
            ("GET", "signals/open"): self._open, ("POST", "catalysts"): self._catalysts,
            ("GET", "positions"): self._positions, ("POST", "positions/events"): self._position_event,
            ("POST", "positions/assessments"): self._assessments, ("GET", "work"): self._work,
            ("POST", "notifications/ack"): self._ack, ("GET", "secrets"): self._secrets,
            ("POST", "secrets/ack"): self._secret_ack, ("GET", "models/manifest"): self._manifest,
            ("POST", "models/activation"): self._activation, ("GET", "positions/history"): self._history,
            ("POST", "notices"): self._notices, ("POST", "members"): self._members,
        }
        fn = table.get((method, route))
        if fn is None:
            raise Http(404, "NOT_FOUND", route)
        return fn(agent, body, params)

    @staticmethod
    def _check(name: str, payload) -> None:
        try:
            validate(name, payload)
        except ContractViolation as exc:
            raise Http(422, "CONTRACT_VIOLATION", f"{name} failed validation", errors=exc.errors[:8]) from None

    # ---- pairing / health ------------------------------------------------------------------------------------
    def _pair(self, agent: str, b: dict, _p) -> dict:
        ch = self.codes.get(code_hash(str(b.get("code", ""))))
        if not ch or ch["used"] or ch["expires"] < self.clock() or ch["failed"] >= 5:
            raise Http(403, "PAIRING_CODE_INVALID", "invalid, used or expired")
        msg = pairing_message(agent, b["code"], b["x25519_public_key"])
        try:
            Ed25519PublicKey.from_public_bytes(from_b64url(b["ed25519_public_key"])).verify(from_b64url(b["proof"]), msg)
        except Exception:
            ch["failed"] += 1
            raise Http(403, "PAIRING_PROOF_INVALID", "proof failed") from None
        if self.devices.get(agent, {}).get("status") == "revoked":
            raise Http(403, "DEVICE_REVOKED", "revoked")
        fp = fingerprint(b["ed25519_public_key"])
        self.devices[agent] = {"status": "pending_approval", "x25519": b["x25519_public_key"],
                               "ed25519": b["ed25519_public_key"], "fingerprint": fp}
        ch["used"] = True
        return {"status": "pending_approval", "fingerprint": fp}

    def _heartbeat(self, agent: str, hb: dict, _p) -> dict:
        self._check("WorkerHeartbeat", hb)
        if hb["agent_id"] != agent:
            raise Http(403, "AGENT_MISMATCH", "heartbeat agent id does not match signer")
        self.heartbeats.append(hb)
        status = self.devices[agent]["status"]
        return {"ok": True, "status": status, "server_time": iso_utc(self.now()), "skew_ms": 0,
                "secret_versions": self._secret_versions() if status == "active" else {}}

    def _secret_versions(self) -> dict:
        return {k: v["version"] for k, v in self.integrations.items()}

    def _config(self, _a, _b, _p) -> dict:
        return {**copy.deepcopy(self.config), "secret_versions": self._secret_versions()}

    # ---- market + scans -------------------------------------------------------------------------------------
    def _observations(self, _a, b: dict, _p) -> dict:
        q = n = 0
        for o in (b.get("observations") or [])[:500]:
            self._check("MarketObservation", o)
            cur = self.quotes.get(o["symbol"])
            if cur is None or cur["provenance"]["ts_received"] <= o["provenance"]["ts_received"]:
                self.quotes[o["symbol"]] = o
            q += 1
        for bar in (b.get("bars") or [])[:5000]:
            self._check("MarketBar", bar)
            if bar["timeframe"] == "1Day":
                self.bars[(bar["symbol"], bar["session_date"], bar["adjustment"])] = bar
                n += 1
        return {"quotes": q, "bars": n}

    def _scan(self, _a, b: dict, _p) -> dict:
        run = b.get("scan")
        if not isinstance(run, dict) or not isinstance(run.get("id"), str):
            raise Http(422, "CONTRACT_VIOLATION", "scan.id required")
        cands = (b.get("candidates") or [])[:300]
        for c in cands:
            self._check("SignalCandidate", c)
        snaps = (b.get("feature_snapshots") or [])[:25]
        for f in snaps:
            self._check("FeatureSnapshot", f)
            if bool(f.get("is_demo")) != bool(run.get("is_demo")):
                raise Http(422, "MODE_MISMATCH", "feature snapshot mode must match the scan")
        if run["id"] in self.scans:
            self.duplicates["scan"] += 1
        self.scans.setdefault(run["id"], run)
        for c in cands:
            self.candidates.setdefault(c["id"], c)
        for f in snaps:
            self.feature_snapshots.setdefault((f["symbol"], f["asof_at"], f["feature_version"]), f)
        if b.get("regime"):
            self.regimes.setdefault(run["asof_at"], b["regime"])
        return {"candidates": len(cands)}

    def _catalysts(self, _a, b: dict, _p) -> dict:
        n = 0
        for c in (b.get("items") or [])[:500]:
            self._check("Catalyst", c)
            if c["id"] not in self.catalysts:
                self.catalysts[c["id"]] = c
                n += 1
        return {"inserted": n}

    # ---- signals ------------------------------------------------------------------------------------------
    def _enqueue(self, intent: dict) -> bool:
        self._check("NotificationIntent", intent)
        if intent["event_key"] in self.outbox_keys:
            self.duplicates["outbox"] += 1
            return False
        oid = str(uuid.uuid4())
        route = intent["route"]
        self.outbox[oid] = {"id": oid, "event_key": intent["event_key"], "intent": intent, "state": "pending",
                            "attempts": 0, "lease_until": 0.0, "leased_by": None, "next_attempt": 0.0,
                            "user_id": route.get("user_id") if route["type"] == "DM" else None,
                            "priority": intent["priority"], "seq": len(self.outbox), "discord_message_id": None}
        self.outbox_keys[intent["event_key"]] = oid
        return True

    def _apply_event(self, ev: dict) -> bool:
        self._check("SignalEvent", ev)
        head = self.heads.get(ev["signal_id"])
        if head is None:
            raise Http(404, "UNKNOWN_SIGNAL", "signal not found")
        if ev["dedupe_key"] in self.signal_events:
            self.duplicates["signal_event"] += 1
            return False
        cur = "" if head["events"] == 0 else head["state"]
        frm, to = ev.get("from_state") or "", ev["to_state"]
        if frm and frm == to:
            if cur in TERMINAL:
                raise Http(409, "TERMINAL_SIGNAL", "terminal signals are immutable")
            if cur != frm:
                raise Http(409, "STATE_CONFLICT", f"signal is {cur}, annotation expects {frm}")
        else:
            if frm != cur:
                raise Http(409, "STATE_CONFLICT", f"signal is {cur or '(new)'}, event expects {frm or '(new)'}")
            if to not in ALLOWED.get(frm, set()):
                raise Http(422, "ILLEGAL_TRANSITION", f"{frm} -> {to}")
            head["state"] = to
        self.signal_events[ev["dedupe_key"]] = ev
        head["events"] += 1
        return True

    def _transaction(self, fn: Callable[[], dict]) -> dict:
        snapshot = copy.deepcopy((self.heads, self.signal_events, self.outbox, self.outbox_keys, self.signals))
        try:
            return fn()
        except Http:
            self.heads, self.signal_events, self.outbox, self.outbox_keys, self.signals = snapshot
            raise

    def _signal(self, _a, b: dict, _p) -> dict:
        sig = b.get("signal") or {}
        self._check("TradeSignal", sig)
        if bool(sig["is_demo"]) != (sig["release_mode"] == "DEMO") or bool(sig["is_demo"]) != self.is_demo:
            raise Http(422, "MODE_MISMATCH", "signal mode does not match the server")
        conf = sig["confidence"]
        if conf["status"] == "UNVALIDATED" and "p_target_first" in conf:
            raise Http(422, "UNVALIDATED_WITH_PROBABILITY", "unvalidated signals cannot carry a probability")
        key = (sig["id"], int(sig["revision"]))

        def tx() -> dict:
            existing = self.signals.get(key)
            if existing is not None:
                if existing["immutable_hash"] != sig["immutable_hash"]:
                    raise Http(409, "IMMUTABLE_REVISION", "different body for this revision")
                self.duplicates["signal"] += 1
                return {"created": False}
            self.signals[key] = {**sig, "tier": b.get("tier")}
            self.heads.setdefault(sig["id"], {"state": "DISCOVERED", "events": 0, "symbol": sig["symbol"]})
            self.heads[sig["id"]]["revision"] = int(sig["revision"])
            for ev in b.get("events") or []:
                self._apply_event(ev)
            if b.get("intent"):
                self._enqueue(b["intent"])
            return {"created": True}

        return self._transaction(tx)

    def _signal_events(self, _a, b: dict, _p) -> dict:
        intents = {str(i["event_key"]).removeprefix("SIGEVT:"): i for i in b.get("intents") or []}

        def tx() -> dict:
            applied = 0
            for ev in (b.get("events") or [])[:50]:
                if self._apply_event(ev):
                    applied += 1
                    if ev["dedupe_key"] in intents:
                        self._enqueue(intents[ev["dedupe_key"]])
            return {"applied": applied}

        return self._transaction(tx)

    def _open(self, _a, _b, _p) -> dict:
        out = []
        for sid, head in self.heads.items():
            sig = self.signals.get((sid, head.get("revision", 1)))
            if sig is None:
                continue
            snap = {k: v for k, v in sig.items() if k != "tier"}
            out.append({"signal": {**snap, "tier": sig.get("tier"), "state": head["state"]}, "state": head["state"]})
        return {"signals": out}

    # ---- members / positions -------------------------------------------------------------------------------
    def member_id(self, discord_user_id: str) -> str:
        return self.ledger_store.ensure_member(discord_user_id, self.now())

    def set_dm_opt_in(self, discord_user_id: str, opt_in: bool) -> None:
        self.ledger_store.set_dm_opt_in(self.member_id(discord_user_id), opt_in)

    def _positions(self, _a, _b, params) -> dict:
        duid = params.get("discord_user_id") or ""
        if not duid:
            raise Http(422, "DISCORD_USER_REQUIRED", "discord_user_id required")
        return {"positions": self.positions.positions(duid, include_closed=params.get("include_closed") == "1")}

    def _position_event(self, _a, b: dict, _p) -> dict:
        duid = str(b.get("discord_user_id") or "")
        if not duid:
            raise Http(422, "DISCORD_USER_REQUIRED", "discord_user_id required")
        ev = b.get("event")
        self._check("PositionEvent", ev)
        if ev["source"] not in ("discord", "starter_import") and not (self.is_demo and ev["source"] == "replay"):
            raise Http(422, "INVALID_SOURCE", "agent position events must come from Discord")
        try:
            res = self.positions.record(duid, ev, self.now())
        except LedgerError as exc:
            raise Http(422, exc.code, str(exc)) from None
        except ServiceError as exc:
            raise Http(403 if exc.code == "MEMBERSHIP_REVOKED" else 422, exc.code, str(exc)) from None
        except PositionNotFound:
            raise Http(404, "NOT_FOUND", "position not found") from None
        if res.status == "duplicate":
            self.duplicates["position_event"] += 1
        return {"status": res.status, "event_id": res.event_id, "position": res.position, "warnings": res.warnings,
                "pending_private_notifications": []}

    def _assessments(self, _a, b: dict, _p) -> dict:
        stored = 0
        for it in (b.get("items") or [])[:200]:
            uid = self.ledger_store.member_id(str(it.get("discord_user_id")))
            if uid is None:
                raise Http(404, "NOT_FOUND", "member not found")
            a = it["assessment"]
            self._check("PositionAssessment", a)
            owned = self.ledger_store.query("SELECT 1 FROM positions WHERE id=? AND user_id=?", (a["position_id"], uid))
            if not owned:
                continue  # not this member's position: silently ignored
            key = (a["position_id"], str(it["assessment_key"]))
            if key in self.assessments:
                self.duplicates["assessment"] += 1
                continue
            intent = it.get("intent")
            if intent and (intent["route"]["type"] != "DM" or intent["route"].get("user_id") != uid):
                raise Http(422, "ROUTE_MISMATCH", "position updates are private DMs to the owner")
            self.assessments[key] = a
            if intent:
                self._enqueue(intent)
            stored += 1
        return {"stored": stored}

    # ---- outbox ---------------------------------------------------------------------------------------------
    def _work(self, agent: str, _b, params) -> dict:
        now = self.clock()
        limit = max(1, min(50, int(params.get("limit") or 20)))
        due = sorted(
            (r for r in self.outbox.values()
             if (r["state"] == "pending" or (r["state"] == "leased" and r["lease_until"] < now)) and r["next_attempt"] <= now),
            key=lambda r: (PRIORITY[r["priority"]], r["seq"]))[:limit]
        items = []
        for r in due:
            r.update(state="leased", lease_until=now + 120, leased_by=agent)
            duid, opt = None, False
            if r["user_id"]:
                row = self.ledger_store.query("SELECT discord_user_id, dm_opt_in, revoked_at FROM app_users WHERE id=?",
                                              (r["user_id"],))
                if row and not row[0]["revoked_at"]:
                    duid, opt = row[0]["discord_user_id"], bool(row[0]["dm_opt_in"])
            items.append({"outbox_id": r["id"], "attempt": r["attempts"] + 1, "intent": r["intent"],
                          "discord_user_id": duid, "dm_opt_in": opt})
        monitored = []
        for p in self.ledger_store.all_open_positions():
            u = self.ledger_store.query("SELECT discord_user_id FROM app_users WHERE id=?", (p["user_id"],))
            item = {"position_id": p["id"], "discord_user_id": u[0]["discord_user_id"] if u else None,
                    "symbol": p["symbol"], "signal_id": p["signal_id"], "state": p["state"]}
            if self.monitored_include_user_id:
                item["user_id"] = p["user_id"]
            monitored.append(item)
        return {"server_time": iso_utc(self.now()), "release_mode": self.mode, "outbox": items,
                "monitored_positions": monitored,
                "champion_models": [{"model_id": m["model_id"], "strategy_id": m["strategy_id"],
                                     "validation_expires_at": m["validation_expires_at"]} for m in self.champions]}

    def _ack_one(self, agent: str, a: dict) -> str:
        r = self.outbox.get(str(a.get("outbox_id")))
        if r is None:
            raise Http(404, "NOT_FOUND", "outbox item not found")
        if r["state"] == "sent":
            self.duplicates["ack"] += 1
            return "sent"
        if r["leased_by"] and r["leased_by"] != agent:
            raise Http(409, "LEASE_CONFLICT", "leased by another agent")
        r["attempts"] += 1
        status = a.get("status")
        if status == "delivered":
            r.update(state="sent", discord_message_id=a.get("discord_message_id"), lease_until=0.0)
            rendered = a.get("rendered")
            if (isinstance(rendered, dict) and r["intent"]["route"]["type"] == "CHANNEL"
                    and len(json.dumps(rendered)) <= 12_000):
                r["rendered"] = rendered
            return "sent"
        if status == "dm_blocked":
            r.update(state="deferred", lease_until=0.0)
            return "deferred"
        if r["attempts"] >= 8:
            r.update(state="dead_letter", lease_until=0.0)
            return "dead_letter"
        r.update(state="pending", lease_until=0.0,
                 next_attempt=self.clock() + float(a.get("retry_after_seconds") or min(900, 2 ** r["attempts"])))
        return "pending"

    def _ack(self, agent: str, b: dict, _p) -> dict:
        # like the TS route: per-item results; a 4xx on one item never drops the acknowledgements after it
        results = []
        for a in (b.get("acks") or [])[:100]:
            try:
                results.append({"outbox_id": a.get("outbox_id"), "state": self._ack_one(agent, a)})
            except Http as e:
                if e.status >= 500:
                    raise
                results.append({"outbox_id": a.get("outbox_id"), "error": {"code": e.code, "status": e.status}})
        return {"results": results}

    def _notices(self, _a, b: dict, _p) -> dict:
        queued = 0
        for i in (b.get("intents") or [])[:20]:
            route = i.get("route") or {}
            if i.get("kind") != "SYSTEM_ALERT" or route.get("type") != "CHANNEL" or route.get("channel_key") != "system_status":
                raise Http(422, "NOT_A_SYSTEM_NOTICE", "only SYSTEM_ALERT notices to #system-status are accepted")
            queued += int(self._enqueue(i))
        return {"queued": queued}

    # ---- member history / member actions --------------------------------------------------------------------
    @staticmethod
    def _pg_num(value) -> str | None:
        return None if value is None else f"{Decimal(str(value)):.6f}"  # numeric(18,6) text, as node-postgres returns

    @staticmethod
    def _pg_ts(value: str | None) -> str | None:
        return None if not value else value.replace("Z", ".000Z") if "." not in value else value

    def _history(self, _a, _b, params) -> dict:
        duid = params.get("discord_user_id") or ""
        uid = self.ledger_store.member_id(duid)
        if uid is None:
            raise Http(404, "NOT_FOUND", "no positions for this member")
        pid = params.get("position_id")
        key = params.get("client_idempotency_key")
        if not pid and key:
            row = self.ledger_store.query("SELECT position_id FROM position_events WHERE user_id=? AND "
                                          "client_idempotency_key=?", (uid, key))
            if not row:
                raise Http(404, "NOT_FOUND", "no event with that key")
            pid = row[0]["position_id"]
        if not pid:
            raise Http(422, "POSITION_REQUIRED", "position_id or client_idempotency_key required")
        try:
            summary = self.positions.position(duid, pid)
        except PositionNotFound:
            raise Http(404, "NOT_FOUND", "position not found") from None
        events = []
        for r in self.ledger_store.position_event_rows(None, uid, pid):
            body = json.loads(r["body"])
            f = body.get("fill") or {}
            c = body.get("correction")
            events.append({"id": r["id"], "seq": r["seq"], "event_type": r["event_type"], "price": self._pg_num(f.get("price")),
                           "quantity": self._pg_num(f.get("quantity")), "fees": self._pg_num(f.get("fees")),
                           "event_at": self._pg_ts(f.get("fill_at")), "correction_json": c, "notes": body.get("notes"),
                           "source": body.get("source"), "recorded_at": self._pg_ts(r["recorded_at"])})
        return {"summary": summary, "events": events}

    def _members(self, _a, b: dict, _p) -> dict:
        duid = str(b.get("discord_user_id") or "")
        action = b.get("action")
        self.member_actions.append({k: v for k, v in b.items()})
        if action == "revoke":
            self.ledger_store.revoke_member(duid, self.now())
            return {"ok": True}
        if action == "prefs":
            uid = self.member_id(duid)
            self.ledger_store.set_dm_opt_in(uid, bool(b.get("dm_opt_in")))
            self.high_priority[uid] = bool(b.get("high_priority_mentions"))
            return {"ok": True}
        if action == "delete_my_data":
            uid = self.ledger_store.member_id(duid)
            if uid is None:
                raise Http(404, "NOT_FOUND", "member not found")
            n = self.ledger_store.delete_member_data(uid)
            for r in self.outbox.values():
                if r["user_id"] == uid and r["state"] != "sent":
                    r.update(state="suppressed", intent={})
            return {"ok": True, "positions_deleted": n}
        raise Http(422, "UNKNOWN_ACTION", "unknown member action")

    # ---- secrets --------------------------------------------------------------------------------------------
    def _secrets(self, agent: str, _b, params) -> dict:
        provider = params.get("provider") or ""
        if provider != "alpaca":
            raise Http(404, "NOT_FOUND", "no deliverable secret for that provider")
        dev = self.devices[agent]
        if dev["status"] != "active" or not dev.get("x25519"):
            raise Http(403, "DEVICE_NOT_ACTIVE", "device not approved")
        integ = self.integrations.get(provider)
        if not integ:
            raise Http(404, "NOT_CONFIGURED", "provider credentials are not configured in Settings")
        issued = self.now().replace(microsecond=0)
        meta = {"device_id": agent, "provider": provider, "secret_version": integ["version"],
                "issued_at": iso_utc(issued), "expires_at": iso_utc(issued + timedelta(seconds=300))}
        return {"envelope": seal_envelope(dev["x25519"], meta, json.dumps(integ["fields"], separators=(",", ":")))}

    def _secret_ack(self, agent: str, b: dict, _p) -> dict:
        self.secret_acks.append((str(b.get("provider")), int(b.get("secret_version") or 0)))
        return {"ok": True}

    # ---- models ---------------------------------------------------------------------------------------------
    def _manifest(self, _a, _b, _p) -> dict:
        return {"champions": copy.deepcopy(self.champions)}

    def _artifact(self, model_id: str) -> dict:
        art = self.artifacts.get(model_id)
        if art is None:
            raise Http(404, "NOT_FOUND", "model artifact not available")
        return {"model_id": model_id, "artifact_b64": base64.b64encode(art).decode()}

    def _activation(self, agent: str, b: dict, _p) -> dict:
        if b.get("status") not in ("activated", "rejected", "rolled_back", "expired"):
            raise Http(422, "INVALID_STATUS", "bad activation status")
        self.activations.append({"agent_id": agent, "model_id": str(b.get("model_id"))[:60], "status": b["status"],
                                 "detail": (b.get("detail") or "")[:240]})
        return {"ok": True}
