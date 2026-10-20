"""Pi-side model store: verified staging, atomic activation, rollback, fail-closed loading.

Layout (state_dir/models):
  versions/<model_id>/manifest.json + model.json   (immutable once written)
  current  -> symlink to versions/<model_id>       (swapped atomically with os.replace)
  previous -> symlink to the prior champion        (instant rollback target)
Discord and scanning keep running during a swap: inference reads `current` at call time and an
in-memory model is only replaced after the new one has been loaded and warmed successfully.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from apex_worker.models.manifest import ManifestRejected, verify_release
from apex_worker.research.dataset import MODEL_FEATURES
from apex_worker.research.model import CompetingModel

PROBE_ROW = {f: 0.0 for f in MODEL_FEATURES}


@dataclass
class ActiveModel:
    model_id: str
    manifest: dict
    model: CompetingModel


class ModelStore:
    def __init__(self, root: Path, trusted_keys: dict[int, str], release_mode: str):
        self.root = Path(root)
        self.versions = self.root / "versions"
        self.versions.mkdir(parents=True, exist_ok=True)
        self.trusted_keys = trusted_keys
        self.release_mode = release_mode
        self._lock = threading.Lock()
        self._active: ActiveModel | None = None

    # ---- paths -------------------------------------------------------------
    def _link(self, name: str) -> Path:
        return self.root / name

    def _target(self, name: str) -> str | None:
        link = self._link(name)
        return os.path.basename(os.readlink(link)) if link.is_symlink() else None

    def current_id(self) -> str | None:
        return self._target("current")

    def previous_id(self) -> str | None:
        return self._target("previous")

    def _swap_link(self, name: str, model_id: str) -> None:
        tmp = self._link(f".{name}.tmp")
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        os.symlink(Path("versions") / model_id, tmp)
        os.replace(tmp, self._link(name))  # atomic on POSIX

    # ---- verification + staging ----------------------------------------------
    def _load_and_warm(self, manifest: dict, artifact: bytes) -> CompetingModel:
        try:
            model = CompetingModel.from_json(artifact.decode("utf-8"))
            probs = model.predict([PROBE_ROW])[0]
        except (ValueError, KeyError, UnicodeDecodeError) as exc:
            raise ManifestRejected("ARTIFACT_INVALID", f"artifact failed to load: {exc}") from None
        if not all(math.isfinite(p) and 0 <= p <= 1 for p in probs) or abs(sum(probs) - 1) > 1e-6:
            raise ManifestRejected("WARMUP_FAILED", "probe inference produced invalid probabilities")
        if list(model.feature_names) != list(MODEL_FEATURES):
            raise ManifestRejected("FEATURE_SCHEMA_INCOMPATIBLE", "artifact feature list differs from worker schema")
        return model

    def install(self, manifest: dict, artifact: bytes, now_iso: str) -> ActiveModel:
        """Verify, stage and atomically activate. On ANY failure the current model stays active."""
        verify_release(manifest, artifact, self.trusted_keys, now_iso=now_iso, release_mode=self.release_mode)
        model = self._load_and_warm(manifest, artifact)
        mid = manifest["model_id"]
        final = self.versions / mid
        if not final.exists():
            staging = self.versions / f".staging-{mid}"
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir()
            (staging / "model.json").write_bytes(artifact)
            (staging / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
            for f in staging.iterdir():
                with open(f, "rb") as fh:
                    os.fsync(fh.fileno())
            os.replace(staging, final)
        with self._lock:
            cur = self.current_id()
            if cur and cur != mid:
                self._swap_link("previous", cur)
            self._swap_link("current", mid)
            self._active = ActiveModel(mid, manifest, model)
        self._prune(keep={mid, self.previous_id() or ""})
        return self._active

    def rollback(self, now_iso: str) -> ActiveModel | None:
        prev = self.previous_id()
        if not prev:
            return None
        manifest, artifact = self._read(prev)
        verify_release(manifest, artifact, self.trusted_keys, now_iso=now_iso, release_mode=self.release_mode)
        model = self._load_and_warm(manifest, artifact)
        with self._lock:
            cur = self.current_id()
            self._swap_link("current", prev)
            if cur:
                self._swap_link("previous", cur)
            self._active = ActiveModel(prev, manifest, model)
        return self._active

    def _read(self, model_id: str) -> tuple[dict, bytes]:
        d = self.versions / model_id
        return json.loads((d / "manifest.json").read_text()), (d / "model.json").read_bytes()

    def load_active(self, now_iso: str) -> ActiveModel | None:
        """Re-verify the on-disk champion at startup (tampered or expired files fail closed)."""
        mid = self.current_id()
        if not mid:
            return None
        try:
            manifest, artifact = self._read(mid)
            verify_release(manifest, artifact, self.trusted_keys, now_iso=now_iso, release_mode=self.release_mode)
            model = self._load_and_warm(manifest, artifact)
        except (ManifestRejected, OSError, ValueError):
            with self._lock:
                self._active = None
            return None
        with self._lock:
            self._active = ActiveModel(mid, manifest, model)
        return self._active

    @property
    def active(self) -> ActiveModel | None:
        return self._active

    def _prune(self, keep: set[str], max_versions: int = 3) -> None:
        dirs = sorted(
            (p for p in self.versions.iterdir() if p.is_dir() and not p.name.startswith(".")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for p in dirs[max_versions:]:
            if p.name not in keep:
                shutil.rmtree(p, ignore_errors=True)
