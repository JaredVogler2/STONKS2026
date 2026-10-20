"""Register signed training candidates with the release API, and re-verify registry champions.

Only candidates that the frozen local policy marked `promotable` are uploaded by default (a failed or non-improving
candidate never reaches the registry, so the incumbent is untouched). Each candidate is checked locally first: model id
shape, signature present, artifact sha256/size match the manifest and, when `APEX_MODEL_TRUSTED_KEYS` (public keys) is
available, the Ed25519 signature verifies. The server repeats every check and re-applies the promotion policy.
DEMO/synthetic candidates are never sent to a live (non-DEMO) registry.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from apex_worker.agent.crypto import verify_manifest
from apex_worker.models.manifest import MAX_ARTIFACT_BYTES
from apex_worker.release.client import ReleaseClient, ReleaseError

MODEL_ID_RE = re.compile(r"^mdl_[a-z0-9]{1,60}$")
EXPECTED_SKIP_CODES = {"MODE_MISMATCH"}  # older servers without /policy: a DEMO model offered to a live registry


@dataclass
class Candidate:
    model_id: str
    strategy_id: str
    promotable: bool
    manifest: dict
    artifact: bytes


class CandidateInvalid(ValueError):
    pass


def trusted_keys_from_env(raw: str | None) -> dict[int, str]:
    if not raw:
        return {}
    try:
        return {int(k): str(v) for k, v in json.loads(raw).items()}
    except (ValueError, AttributeError, TypeError):
        raise CandidateInvalid("APEX_MODEL_TRUSTED_KEYS is not a JSON object of {version: public_key}") from None


def check_candidate(manifest: dict, artifact: bytes, model_id: str, trusted: dict[int, str]) -> None:
    if manifest.get("model_id") != model_id:
        raise CandidateInvalid(f"{model_id}: manifest model_id mismatch")
    if not isinstance(manifest.get("signature"), str):
        raise CandidateInvalid(f"{model_id}: manifest is unsigned (training ran without --sign)")
    if len(artifact) > MAX_ARTIFACT_BYTES or len(artifact) != manifest.get("artifact_size_bytes"):
        raise CandidateInvalid(f"{model_id}: artifact size mismatch or over limit")
    if hashlib.sha256(artifact).hexdigest() != manifest.get("artifact_sha256"):
        raise CandidateInvalid(f"{model_id}: artifact sha256 does not match the signed manifest")
    if trusted and not verify_manifest(manifest, trusted):
        raise CandidateInvalid(f"{model_id}: signature does not verify against APEX_MODEL_TRUSTED_KEYS")


def collect_candidates(out_dir: Path, trusted: dict[int, str] | None = None) -> tuple[dict, list[Candidate]]:
    report = json.loads((out_dir / "report.json").read_text())
    out: list[Candidate] = []
    for c in report.get("candidates") or []:
        mid = str(c.get("model_id") or "")
        if not MODEL_ID_RE.match(mid):  # never join an unvalidated id into a path
            raise CandidateInvalid(f"invalid model id in report: {mid[:40]!r}")
        rel = out_dir / "releases" / mid
        manifest = json.loads((rel / "manifest.json").read_text())
        artifact = (rel / "model.json").read_bytes()
        check_candidate(manifest, artifact, mid, trusted or {})
        out.append(Candidate(mid, str(c.get("strategy_id")), bool(c.get("promotable")), manifest, artifact))
    return report, out


def upload_candidates(
    client: ReleaseClient,
    out_dir: Path,
    *,
    policy: dict | None,
    trusted: dict[int, str] | None = None,
    include_non_promotable: bool = False,
    training_run_id: str | None = None,
) -> dict:
    report, candidates = collect_candidates(out_dir, trusted)
    server_mode = (policy or {}).get("mode")
    result: dict = {
        "training_status": report.get("status"),
        "data_source": report.get("data_source"),
        "server_mode": server_mode,
        "promotion_mode": ((policy or {}).get("policy") or {}).get("promotion_mode"),
        "registered": [],
        "skipped": [],
        "rejected": [],
    }
    for c in candidates:
        if not (c.promotable or include_non_promotable):
            result["skipped"].append({"model_id": c.model_id, "reason": "not promotable under the frozen local policy"})
            continue
        if c.manifest.get("is_demo") and server_mode not in (None, "DEMO"):
            result["skipped"].append(
                {"model_id": c.model_id, "reason": f"DEMO/synthetic candidate is never registered with a {server_mode} registry"}
            )
            continue
        try:
            res = client.register_candidate(c.manifest, c.artifact, training_run_id)
        except ReleaseError as exc:
            if exc.status == 422 and exc.code in EXPECTED_SKIP_CODES:
                result["skipped"].append({"model_id": c.model_id, "reason": f"{exc.code}: {exc.message}"})
                continue
            if 400 <= exc.status < 500:
                result["rejected"].append({"model_id": c.model_id, "status": exc.status, "code": exc.code, "message": exc.message})
                continue
            raise
        result["registered"].append(
            {
                "model_id": res.get("model_id", c.model_id),
                "strategy_id": c.strategy_id,
                "created": bool(res.get("created")),
                "promotion": res.get("promotion") or {},
            }
        )
    result["outcome"] = upload_outcome(result)
    return result


def upload_outcome(result: dict) -> str:
    if result.get("training_status") == "no_data":
        return "no_data"
    if any((r.get("promotion") or {}).get("promoted") for r in result["registered"]):
        return "promoted"
    if result["registered"]:
        return "candidate_registered"
    if result["rejected"]:
        return "rejected"
    return "champion_retained"


def verify_champions(data: dict, trusted: dict[int, str]) -> dict:
    """Re-verify registry champions (manifests + artifacts from `champions?include=artifacts`)."""
    artifacts = data.get("artifacts") or {}
    checked: dict[str, dict] = {}
    for m in data.get("manifests") or []:
        mid = str(m.get("model_id"))
        try:
            art = base64.b64decode(str(artifacts.get(mid, "")), validate=True)
            check_candidate(m, art, mid, trusted)
            checked[mid] = {"strategy_id": m.get("strategy_id"), "ok": True, "signature_checked": bool(trusted)}
        except (CandidateInvalid, binascii.Error, ValueError) as exc:
            checked[mid] = {"strategy_id": m.get("strategy_id"), "ok": False, "error": str(exc)[:200]}
    return checked
