"""Interpretable competing-outcome model: L2-regularized multinomial logistic regression over
{TARGET, STOP, TIME} with an isotonic calibrator on P(TARGET first).

Pure numpy (deterministic Newton/gradient iterations, no randomness), so CI training and Pi inference
are bit-reproducible. The artifact is plain JSON (coefficients, standardization, isotonic knots) —
never a pickle/joblib file, so loading a remote model cannot execute code.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

MODEL_KIND = "logistic_competing_v1"
CLASSES = ("TARGET", "STOP", "TIME")


def _softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def fit_multinomial(x: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 400, lr: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """Full-batch gradient descent with backtracking; returns (W [d x k], b [k])."""
    n, d = x.shape
    k = len(CLASSES)
    w = np.zeros((d, k))
    b = np.log(np.bincount(y, minlength=k) + 1.0)
    b = b - b.mean()
    onehot = np.eye(k)[y]

    def loss(w_, b_):
        p = _softmax(x @ w_ + b_)
        return -np.mean(np.log(np.clip((p * onehot).sum(axis=1), 1e-12, 1))) + 0.5 * l2 / n * float((w_ ** 2).sum())

    cur = loss(w, b)
    step = lr
    for _ in range(iters):
        p = _softmax(x @ w + b)
        g = p - onehot
        gw = x.T @ g / n + l2 / n * w
        gb = g.mean(axis=0)
        while step > 1e-6:
            w2, b2 = w - step * gw, b - step * gb
            new = loss(w2, b2)
            if new <= cur:
                w, b, cur = w2, b2, new
                step = min(step * 1.25, 4.0)
                break
            step *= 0.5
        if float(np.abs(gw).max()) < 1e-6:
            break
    return w, b


def fit_isotonic(score: np.ndarray, outcome: np.ndarray) -> tuple[list[float], list[float]]:
    """Pool-adjacent-violators; returns monotone knots (x, y) for piecewise-linear interpolation."""
    order = np.argsort(score, kind="mergesort")
    s, o = score[order], outcome[order].astype(float)
    blocks: list[list[float]] = []  # [sum_y, count, min_x, max_x]
    for xi, yi in zip(s, o, strict=True):
        blocks.append([yi, 1.0, xi, xi])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] >= blocks[-1][0] / blocks[-1][1]:
            sy, c, mn, mx = blocks.pop()
            blocks[-1][0] += sy
            blocks[-1][1] += c
            blocks[-1][3] = mx
    xs: list[float] = []
    ys: list[float] = []
    for sy, c, mn, mx in blocks:
        v = sy / c
        xs += [float(mn), float(mx)] if mx > mn else [float(mn)]
        ys += [v, v] if mx > mn else [v]
    return [round(v, 8) for v in xs], [round(v, 8) for v in ys]


@dataclass
class CompetingModel:
    feature_names: list[str]
    means: list[float]
    stds: list[float]
    weights: list[list[float]]  # d x 3
    intercepts: list[float]
    iso_x: list[float]
    iso_y: list[float]
    metadata: dict

    def _matrix(self, rows: Sequence[Mapping[str, float | None]]) -> np.ndarray:
        x = np.array([[np.nan if r.get(f) is None else float(r[f]) for f in self.feature_names] for r in rows], dtype=float)
        mu = np.array(self.means)
        x = np.where(np.isnan(x), mu, x)  # missing -> training mean (neutral), never 0
        return (x - mu) / np.array(self.stds)

    def raw_proba(self, rows: Sequence[Mapping[str, float | None]]) -> np.ndarray:
        return _softmax(self._matrix(rows) @ np.array(self.weights) + np.array(self.intercepts))

    def calibrated_target(self, raw_target: np.ndarray) -> np.ndarray:
        if not self.iso_x:
            return raw_target
        return np.interp(raw_target, self.iso_x, self.iso_y)

    def predict(self, rows: Sequence[Mapping[str, float | None]]) -> np.ndarray:
        """Coherent partition [P(target first), P(stop first), P(time expiry)] with calibrated P(target)."""
        raw = self.raw_proba(rows)
        pt = np.clip(self.calibrated_target(raw[:, 0]), 0.001, 0.999)
        rest = raw[:, 1:]
        rest = rest / np.clip(rest.sum(axis=1, keepdims=True), 1e-12, None) * (1 - pt)[:, None]
        return np.column_stack([pt, rest])

    def to_json(self) -> str:
        return json.dumps({
            "kind": MODEL_KIND, "classes": list(CLASSES), "feature_names": self.feature_names,
            "standardize": {"means": [round(v, 10) for v in self.means], "stds": [round(v, 10) for v in self.stds]},
            "weights": [[round(v, 10) for v in row] for row in self.weights],
            "intercepts": [round(v, 10) for v in self.intercepts],
            "isotonic_target": {"x": self.iso_x, "y": self.iso_y},
            "metadata": self.metadata,
        }, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str, *, max_features: int = 64) -> CompetingModel:
        data = json.loads(text)
        if data.get("kind") != MODEL_KIND or tuple(data.get("classes", ())) != CLASSES:
            raise ValueError("unsupported model kind")
        names = data["feature_names"]
        w = data["weights"]
        if not (0 < len(names) <= max_features) or len(w) != len(names) or any(len(r) != len(CLASSES) for r in w):
            raise ValueError("model shape mismatch")
        stds = data["standardize"]["stds"]
        if any((not math.isfinite(s)) or s <= 0 for s in stds):
            raise ValueError("invalid standardization")
        iso = data.get("isotonic_target", {"x": [], "y": []})
        if iso["x"] != sorted(iso["x"]) or iso["y"] != sorted(iso["y"]):
            raise ValueError("isotonic calibrator must be monotone")
        return cls(names, data["standardize"]["means"], stds, w, data["intercepts"], iso["x"], iso["y"],
                   data.get("metadata", {}))


def train(rows: Sequence[Mapping[str, float | None]], labels: Sequence[str], feature_names: Sequence[str],
          calib_rows: Sequence[Mapping[str, float | None]] | None = None, calib_labels: Sequence[str] | None = None,
          l2: float = 4.0, metadata: dict | None = None) -> CompetingModel:
    names = list(feature_names)
    x = np.array([[np.nan if r.get(f) is None else float(r[f]) for f in names] for r in rows], dtype=float)
    with np.errstate(all="ignore"):
        counts = np.sum(~np.isnan(x), axis=0)
        means = np.where(counts > 0, np.nansum(x, axis=0) / np.maximum(counts, 1), 0.0)
    x = np.where(np.isnan(x), means, x)
    stds = x.std(axis=0)
    stds = np.where(stds < 1e-9, 1.0, stds)
    xs = (x - means) / stds
    y = np.array([CLASSES.index(lbl) for lbl in labels])
    w, b = fit_multinomial(xs, y, l2=l2)
    model = CompetingModel(names, means.tolist(), stds.tolist(), w.tolist(), b.tolist(), [], [], dict(metadata or {}))
    if calib_rows is not None and calib_labels is not None and len(calib_rows) >= 30:
        raw = model.raw_proba(calib_rows)[:, 0]
        hit = np.array([1.0 if lbl == "TARGET" else 0.0 for lbl in calib_labels])
        model.iso_x, model.iso_y = fit_isotonic(raw, hit)
    return model
