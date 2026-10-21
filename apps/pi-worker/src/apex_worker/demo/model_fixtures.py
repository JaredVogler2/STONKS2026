"""Deterministic DEMO model releases for tests and `apex replay` (no training run needed).

The artifact is a plain-JSON CompetingModel with fixed coefficients over the worker's MODEL_FEATURES; the manifest
is built by the production `build_manifest` and signed with the PUBLIC TEST-ONLY DEMO key. These models are
synthetic fixtures: their numbers say nothing about any market and they are only trusted in DEMO mode/tests.
"""

from __future__ import annotations

from datetime import datetime

from apex_worker.agent.crypto import load_signing_key, public_from_signing_key
from apex_worker.models.manifest import ReleaseInputs, build_manifest, sign
from apex_worker.research.dataset import MODEL_FEATURES
from apex_worker.research.model import CompetingModel

# PUBLIC TEST-ONLY seed (same as demo/web_seed.DEMO_MODEL_SIGNING_SEED); never trusted outside DEMO mode
DEMO_MODEL_SIGNING_SEED = "REVNTy1PTkxZLUFQRVgtTU9ERUwtU0lHTklORy1LRVk"
OTHER_TEST_SEED = "CAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAg"  # an UNTRUSTED key for rejection tests


def demo_key():
    return load_signing_key(DEMO_MODEL_SIGNING_SEED)


def demo_trusted_keys() -> dict[int, str]:
    return {1: public_from_signing_key(demo_key())}


def fixture_artifact(note: str = "v1", target_bias: float = 0.4) -> bytes:
    d = len(MODEL_FEATURES)
    model = CompetingModel(list(MODEL_FEATURES), [0.0] * d, [1.0] * d, [[0.0, 0.0, 0.0] for _ in range(d)],
                           [target_bias, 0.0, -0.2], [], [], {"note": note, "fixture": "demo"})
    return model.to_json().encode()


def fixture_release(strategy_id: str, now: datetime, *, note: str = "v1", validity_days: int = 14,
                    status: str = "CALIBRATED", demo: bool = True, key=None, signer_key_version: int = 1,
                    artifact: bytes | None = None) -> tuple[dict, bytes]:
    art = artifact if artifact is not None else fixture_artifact(note)
    report = {
        "confidence_status": status, "threshold": 0.55, "policy_version": "fixture-1", "status_reasons": ["DEMO fixture"],
        "holdout": {"n": 400, "brier_skill": 0.02, "ece": 0.03},
        "gates": {"selected_trades": 120, "selected_mean_lower_p10": 0.001},
        "walk_forward": {"holdout_start": "2026-01-02"},
    }
    inp = ReleaseInputs(
        strategy_id=strategy_id, artifact=art, report=report,
        training_window={"start": "2019-01-02", "end": "2025-12-31"}, training_data_hash="fixture-" + note,
        code_sha="fixture", data_source="synthetic_fixture" if demo else "alpaca_sip_daily", is_demo=demo,
        label_definition={"type": "TARGET_BEFORE_STOP", "same_bar_rule": "STOP_FIRST"}, validity_days=validity_days,
        created_at=now,
    )
    manifest = build_manifest(inp, signer_key_version, now)
    return sign(manifest, key or demo_key()), art
