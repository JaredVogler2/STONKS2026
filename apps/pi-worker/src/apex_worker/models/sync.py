"""Autonomous model refresh on the Pi (laptop-free training amendment 2026-10-20).

Every `poll_minutes` (server config `models.poll_minutes`, default 45):
  1. GET models/manifest -> the signed champion manifests (one per strategy).
  2. For a manifest that is not already active: verify it BEFORE downloading anything — Ed25519 signature against
     the TRUSTED PUBLIC KEYS HELD ON THE PI (APEX_MODEL_TRUSTED_KEYS; never taken from the server), schema, model
     kind, feature-schema compatibility, declared size limit, expiry and DEMO/live provenance.
  3. GET models/{id}/artifact with the agent identity; decoded size capped; ModelStore.install re-verifies the
     sha256 + signature, stages on disk, warms (probe inference) and swaps the `current` symlink atomically. The
     in-memory model dict the engine reads is replaced only after that, so scanning and Discord never stop.
  4. Post-activation health check (probe inference on a fixed grid + any rows supplied by the worker); failure
     rolls back to the previous model automatically.
  5. Ack via models/activation: activated | rejected (+reason code) | rolled_back | expired.
Fail closed: `active_models()` returns only verified, UNEXPIRED models. Without one the policy has no forecast,
so it can publish only UNVALIDATED research setups, never HIGH_CONVICTION. A corrupt / unsigned / incompatible
release is refused, the previous model stays active and ONE private #system-status notice is queued per model id.
"""

from __future__ import annotations

import base64
import binascii
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from apex_worker.agent.client import AgentClient, AgentHTTPError, AgentUnavailable
from apex_worker.agent.crypto import verify_manifest
from apex_worker.models.manifest import MANIFEST_SCHEMA, MAX_ARTIFACT_BYTES, ManifestRejected, feature_schema_version
from apex_worker.models.store import PROBE_ROW, ActiveModel, ModelStore
from apex_worker.notifications import intents
from apex_worker.research.dataset import MODEL_FEATURES
from apex_worker.research.model import MODEL_KIND
from apex_worker.runtime.agent_state import AgentState
from apex_worker.timeutil import iso_utc

log = logging.getLogger("apex.models.sync")

HEALTH_GRID = [PROBE_ROW, {f: 1.0 for f in MODEL_FEATURES}, {f: -1.0 for f in MODEL_FEATURES}, {f: None for f in MODEL_FEATURES}]


def preverify(manifest: dict, trusted_keys: dict[int, str], *, now_iso: str, release_mode: str) -> None:
    """Everything that can be checked without the artifact bytes. Raises ManifestRejected."""
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ManifestRejected("UNSUPPORTED_SCHEMA", str(manifest.get("schema"))[:40])
    if not verify_manifest(manifest, trusted_keys):
        raise ManifestRejected("BAD_SIGNATURE", "signature missing, invalid or from an untrusted key")
    if manifest.get("model_kind") != MODEL_KIND:
        raise ManifestRejected("UNSUPPORTED_KIND", str(manifest.get("model_kind"))[:40])
    size = manifest.get("artifact_size_bytes")
    if not isinstance(size, int) or size <= 0 or size > MAX_ARTIFACT_BYTES:
        raise ManifestRejected("SIZE_LIMIT", "declared artifact size outside limits")
    if manifest.get("feature_schema_version") != feature_schema_version():
        raise ManifestRejected("FEATURE_SCHEMA_INCOMPATIBLE", "model was trained on a different feature schema")
    if str(manifest.get("validation_expires_at", "")) <= now_iso:
        raise ManifestRejected("VALIDATION_EXPIRED", "model validation window has expired")
    if manifest.get("data_source") == "synthetic_fixture" and release_mode != "DEMO":
        raise ManifestRejected("SYNTHETIC_NOT_ALLOWED", "synthetic-fixture models may only run in DEMO mode")
    if bool(manifest.get("is_demo")) != (release_mode == "DEMO"):
        raise ManifestRejected("MODE_MISMATCH", "DEMO models only in DEMO mode and vice versa")


def health_check(active: ActiveModel, extra_rows: list[dict] | None = None) -> str | None:
    """None when healthy, else a reason. Probabilities must be finite, in [0,1] and sum to 1 on every probe row."""
    rows = HEALTH_GRID + list(extra_rows or [])[:50]
    try:
        probs = active.model.predict(rows)
    except (ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        return f"probe inference failed: {type(exc).__name__}"
    for p in probs:
        if not all(math.isfinite(float(x)) and 0 <= float(x) <= 1 for x in p) or abs(float(sum(p)) - 1) > 1e-6:
            return "probe inference produced invalid probabilities"
    return None


@dataclass
class SyncReport:
    checked: int = 0
    activated: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    rolled_back: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    registry_ok: bool = True
    detail: str = ""


class ModelSync:
    def __init__(
        self,
        client: AgentClient | None,
        root: Path,
        trusted_keys: dict[int, str],
        release_mode: str,
        state: AgentState,
        *,
        clock: Callable[[], datetime],
        notify: Callable[[dict, datetime], bool] | None = None,
        health_rows: Callable[[], list[dict]] | None = None,
    ):
        self.client = client
        self.root = Path(root)
        self.trusted_keys = trusted_keys
        self.release_mode = release_mode
        self.state = state
        self.clock = clock
        self.notify = notify
        self.health_rows = health_rows or (lambda: [])
        self.stores: dict[str, ModelStore] = {}
        self.last_success_at: datetime | None = None
        self.last_error: str | None = None
        self.consecutive_failures = 0

    @property
    def is_demo(self) -> bool:
        return self.release_mode == "DEMO"

    def _store(self, strategy_id: str) -> ModelStore:
        if strategy_id not in self.stores:
            safe = "".join(ch for ch in strategy_id if ch.isalnum() or ch in "_-")[:48] or "unknown"
            self.stores[strategy_id] = ModelStore(self.root / "strategies" / safe, self.trusted_keys, self.release_mode)
        return self.stores[strategy_id]

    # ---- startup ------------------------------------------------------------------------------------------------
    def load_local(self) -> dict[str, ActiveModel]:
        """Re-verify every on-disk champion (tampered or expired files fail closed). Works fully offline."""
        base = self.root / "strategies"
        if base.exists():
            for d in sorted(p for p in base.iterdir() if p.is_dir()):
                self._store(d.name).load_active(iso_utc(self.clock()))
        return self.active_models()

    def active_models(self) -> dict[str, ActiveModel]:
        """Only verified, UNEXPIRED models are offered to the policy (fail closed)."""
        now_iso = iso_utc(self.clock())
        out = {}
        for sid, store in self.stores.items():
            a = store.active
            if a is None or self.state.get(f"model_quarantined:{a.model_id}"):
                continue  # failed its post-activation health check: never offered to the policy
            if str(a.manifest.get("validation_expires_at", "")) > now_iso:
                out[sid] = a
        return out

    def expiring(self, within_days: float) -> list[ActiveModel]:
        now = self.clock()
        out = []
        for a in self.active_models().values():
            exp = datetime.fromisoformat(a.manifest["validation_expires_at"].replace("Z", "+00:00"))
            if (exp - now).total_seconds() <= within_days * 86400:
                out.append(a)
        return out

    # ---- poll -----------------------------------------------------------------------------------------------------
    def poll(self) -> SyncReport:
        rep = SyncReport()
        if self.client is None:
            rep.registry_ok, rep.detail = False, "not connected (no paired agent identity)"
            return rep
        try:
            champions = self.client.get("models/manifest").get("champions") or []
        except (AgentUnavailable, AgentHTTPError) as exc:
            self.consecutive_failures += 1
            self.last_error = type(exc).__name__ if isinstance(exc, AgentUnavailable) else exc.code
            rep.registry_ok, rep.detail = False, f"registry unreachable ({self.last_error}); keeping current models"
            return rep
        self.consecutive_failures = 0
        self.last_success_at = self.clock()
        for manifest in champions[:20]:
            if not isinstance(manifest, dict):
                continue
            rep.checked += 1
            self._sync_one(manifest, rep)
        self._expire_notices()
        return rep

    def _sync_one(self, manifest: dict, rep: SyncReport) -> None:
        mid = str(manifest.get("model_id", ""))[:60]
        sid = str(manifest.get("strategy_id", ""))
        store = self._store(sid) if sid else None
        if store is not None and store.current_id() == mid and store.active is not None:
            rep.unchanged.append(mid)
            return
        if self.state.get(f"model_rejected:{mid}"):
            rep.rejected[mid] = str(self.state.get(f"model_rejected:{mid}"))
            return  # refused before: no re-download, no repeated alert
        if self.state.get(f"model_quarantined:{mid}"):
            rep.rejected[mid] = "QUARANTINED"
            return  # failed its health check here before; the server must publish a new release
        now_iso = iso_utc(self.clock())
        try:
            if store is None:
                raise ManifestRejected("UNSUPPORTED_SCHEMA", "manifest has no strategy_id")
            preverify(manifest, self.trusted_keys, now_iso=now_iso, release_mode=self.release_mode)
            artifact = self._download(mid, int(manifest["artifact_size_bytes"]))
            active = store.install(manifest, artifact, now_iso)  # re-verifies sha256 + signature, atomic swap
        except ManifestRejected as exc:
            self._reject(mid, sid, exc.code, exc.detail, rep)
            return
        except (AgentUnavailable, AgentHTTPError) as exc:
            code = exc.code if isinstance(exc, AgentHTTPError) else "DOWNLOAD_FAILED"
            rep.registry_ok = not isinstance(exc, AgentUnavailable)
            rep.detail = f"artifact fetch failed ({code}); will retry next poll"
            return
        problem = health_check(active, self.health_rows())
        if problem:
            self.state.put(f"model_quarantined:{mid}", problem[:120])
            try:
                back = store.rollback(now_iso)
            except ManifestRejected:
                back = None  # previous model no longer valid: run with none (research setups only)
            rep.rolled_back.append(mid)
            self._ack(mid, "rolled_back", f"post-activation health check failed: {problem}")
            self._alert("MODEL_ROLLED_BACK", f"Model {mid} rolled back on the Pi",
                        f"Post-activation health check failed ({problem}). "
                        f"{'Previous model ' + back.model_id + ' restored.' if back else 'No previous model: running without one.'}",
                        "Investigate the release in Models & Automation; the Pi keeps scanning.", mid)
            return
        rep.activated.append(mid)
        self._ack(mid, "activated", f"active for {sid}")
        log.info("model %s activated for %s", mid, sid)

    def _download(self, model_id: str, declared: int) -> bytes:
        data = self.client.get(f"models/{model_id}/artifact")  # type: ignore[union-attr]
        b64 = data.get("artifact_b64")
        if not isinstance(b64, str) or len(b64) > (MAX_ARTIFACT_BYTES * 4) // 3 + 8:
            raise ManifestRejected("SIZE_LIMIT", "artifact missing or larger than the limit")
        try:
            raw = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise ManifestRejected("ARTIFACT_INVALID", "artifact is not valid base64") from None
        if len(raw) != declared:
            raise ManifestRejected("SIZE_MISMATCH", "artifact size does not match the signed manifest")
        return raw

    def _reject(self, mid: str, sid: str, code: str, detail: str, rep: SyncReport) -> None:
        rep.rejected[mid] = code
        self.state.put(f"model_rejected:{mid}", code)
        keep = self.stores[sid].current_id() if sid in self.stores else None
        self._ack(mid, "expired" if code == "VALIDATION_EXPIRED" else "rejected", f"{code}: {detail}")
        self._alert("MODEL_REJECTED", f"Pi refused model {mid}",
                    f"{code}: {detail}. The previous model {'(' + keep + ') ' if keep else ''}stays active; "
                    "no unverified model is ever loaded.",
                    "Check the release pipeline and signing key version. Nothing to do on the Pi.", mid)
        log.warning("model %s refused: %s", mid, code)

    def _ack(self, model_id: str, status: str, detail: str) -> None:
        if self.client is None:
            return
        try:
            self.client.post("models/activation", {"model_id": model_id, "status": status, "detail": detail[:240]},
                             spool_key=f"model_ack:{model_id}:{status}")
        except AgentHTTPError as exc:
            log.warning("activation ack for %s rejected: %s", model_id, exc.code)

    def _alert(self, kind: str, title: str, detail: str, action: str, bucket: str) -> None:
        if self.notify is None:
            return
        now = self.clock()
        self.notify(intents.system_alert(kind, title, detail, action, now, self.is_demo, bucket), now)

    def _expire_notices(self) -> None:
        now = self.clock()
        now_iso = iso_utc(now)
        for sid, store in self.stores.items():
            a = store.active
            expired = a is not None and str(a.manifest.get("validation_expires_at", "")) <= now_iso
            if expired and self.state.once(f"model_expired_ack:{a.model_id}"):
                self._ack(a.model_id, "expired", f"validation expired for {sid}; research setups only")
