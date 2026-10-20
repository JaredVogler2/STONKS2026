"""Model release manifests (laptop-free cloud training amendment, 2026-10-20).

The manifest is canonical JSON (strings/ints only) signed with Ed25519. It binds the artifact bytes
(sha256), the feature schema, the label definition, the training data hash and the code commit. The Pi
verifies the signature against keys it already trusts BEFORE downloading or activating anything.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from apex_worker.agent.canonical import canonical_json
from apex_worker.agent.crypto import sign_manifest, verify_manifest
from apex_worker.features.compute import FEATURE_VERSION
from apex_worker.research.dataset import MODEL_FEATURES
from apex_worker.research.model import MODEL_KIND
from apex_worker.timeutil import iso_utc

MANIFEST_SCHEMA = "apex-model-manifest-1"
MAX_ARTIFACT_BYTES = 2_000_000


def feature_schema_version() -> str:
    digest = hashlib.sha256(("|".join(MODEL_FEATURES) + "|" + FEATURE_VERSION).encode()).hexdigest()[:12]
    return f"{FEATURE_VERSION}+mf-{digest}"


def fmt_metric(x) -> str | None:
    if x is None:
        return None
    return f"{float(x):.6f}"


@dataclass
class ReleaseInputs:
    strategy_id: str
    artifact: bytes
    report: dict
    training_window: dict
    training_data_hash: str
    code_sha: str
    data_source: str
    is_demo: bool
    label_definition: dict
    validity_days: int
    rollback_parent: str | None = None
    created_at: datetime | None = None


def build_manifest(inp: ReleaseInputs, signer_key_version: int, now: datetime) -> dict:
    if len(inp.artifact) > MAX_ARTIFACT_BYTES:
        raise ValueError("artifact exceeds size limit")
    created = inp.created_at or now
    vu = uuid.uuid5(uuid.NAMESPACE_URL, f"apex:{inp.strategy_id}:{hashlib.sha256(inp.artifact).hexdigest()}")
    model_id = f"mdl_{inp.strategy_id.replace('_', '')[:16]}{vu.hex[:12]}"
    hold = inp.report.get("holdout", {}) or {}
    gates = inp.report.get("gates", {}) or {}
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "model_id": model_id,
        "version_uuid": str(vu),
        "model_kind": MODEL_KIND,
        "strategy_id": inp.strategy_id,
        "created_at": iso_utc(created),
        "training_window": inp.training_window,
        "validation_window": {
            "holdout_start": inp.report.get("walk_forward", {}).get("holdout_start") or "",
            "policy_version": inp.report.get("policy_version", ""),
        },
        "training_data_hash": inp.training_data_hash,
        "label_definition": inp.label_definition,
        "feature_schema_version": feature_schema_version(),
        "features_hash": hashlib.sha256("|".join(MODEL_FEATURES).encode()).hexdigest(),
        "code_sha": inp.code_sha,
        "evaluation": {
            "confidence_status": inp.report.get("confidence_status", "UNVALIDATED"),
            "holdout_n": int(hold.get("n") or 0),
            "brier_skill": fmt_metric(hold.get("brier_skill")),
            "ece": fmt_metric(hold.get("ece")),
            "selected_trades": int(gates.get("selected_trades") or 0),
            "selected_mean_lower_p10": fmt_metric(gates.get("selected_mean_lower_p10")),
            "threshold_p_target": fmt_metric(inp.report.get("threshold")),
            "status_reasons": [str(r)[:160] for r in inp.report.get("status_reasons", [])][:8],
        },
        "drift_criteria": {"max_ece": "0.080000", "min_brier_skill": "0.000000", "max_feature_psi": "0.250000"},
        "validation_expires_at": iso_utc(now + timedelta(days=inp.validity_days)),
        "artifact_size_bytes": len(inp.artifact),
        "artifact_sha256": hashlib.sha256(inp.artifact).hexdigest(),
        "storage_uri": f"apex-registry://model_versions/{model_id}",
        "data_source": inp.data_source,
        "is_demo": inp.is_demo,
        "rollback_parent": inp.rollback_parent,
        "signer_key_version": signer_key_version,
    }
    canonical_json(manifest)  # raises if any float slipped in
    return manifest


def sign(manifest: dict, private_key) -> dict:
    out = dict(manifest)
    out["signature"] = sign_manifest(private_key, manifest)
    return out


class ManifestRejected(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def verify_release(
    manifest: dict, artifact: bytes, trusted_keys: dict[int, str], *, now_iso: str, release_mode: str
) -> None:
    """All checks the Pi runs BEFORE activation. Raises ManifestRejected with a stable code."""
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ManifestRejected("UNSUPPORTED_SCHEMA", str(manifest.get("schema")))
    if not verify_manifest(manifest, trusted_keys):
        raise ManifestRejected("BAD_SIGNATURE", "signature missing, invalid or from an untrusted key")
    if manifest.get("model_kind") != MODEL_KIND:
        raise ManifestRejected("UNSUPPORTED_KIND", str(manifest.get("model_kind")))
    if len(artifact) != manifest.get("artifact_size_bytes") or len(artifact) > MAX_ARTIFACT_BYTES:
        raise ManifestRejected("SIZE_MISMATCH", "artifact size does not match manifest or exceeds limit")
    if hashlib.sha256(artifact).hexdigest() != manifest.get("artifact_sha256"):
        raise ManifestRejected("CHECKSUM_MISMATCH", "artifact sha256 does not match signed manifest")
    if manifest.get("feature_schema_version") != feature_schema_version():
        raise ManifestRejected("FEATURE_SCHEMA_INCOMPATIBLE", "model was trained on a different feature schema")
    if manifest.get("validation_expires_at", "") <= now_iso:
        raise ManifestRejected("VALIDATION_EXPIRED", "model validation window has expired")
    if manifest.get("data_source") == "synthetic_fixture" and release_mode != "DEMO":
        raise ManifestRejected("SYNTHETIC_NOT_ALLOWED", "synthetic-fixture models may only run in DEMO mode")
