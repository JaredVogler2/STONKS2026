"""Lightweight drift diagnostic for registry champions (`apex drift`, weekday GitHub workflow).

For each champion (manifest + JSON artifact, re-verified before use) on the latest data window:

* calibration: ECE and Brier skill of the champion's calibrated P(target first) on RESOLVED events whose decision date is
  after the model's `trained_through` and inside the latest window. Brier skill uses the base rate of resolved events on
  or before `trained_through` as the reference. Right-censored events (label window still open) are excluded, which
  under-represents slow outcomes — reported, not hidden.
* feature drift: population stability index (PSI) of each model feature, latest window vs the reference window.

Each metric is compared with the champion's signed `drift_criteria`. Too few resolved events => "insufficient_data"
(UNKNOWN), never a fabricated pass. A breach is a finding (outcome `drift_breach`), not a job failure; acting on it
(suppressing high-conviction signals, retraining, rollback) is owner/Pi policy.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

import numpy as np
import pandas as pd

from apex_worker.research import metrics as M
from apex_worker.research.dataset import MODEL_FEATURES
from apex_worker.research.model import CompetingModel


@dataclass(frozen=True)
class DriftConfig:
    latest_sessions: int = 120  # decision dates within the last ~6 months of data
    reference_sessions: int = 500  # reference window ending at trained_through
    min_resolved: int = 30
    min_psi_rows: int = 30
    psi_bins: int = 5


def psi(reference: np.ndarray, latest: np.ndarray, bins: int = 5) -> float | None:
    ref = reference[np.isfinite(reference)]
    cur = latest[np.isfinite(latest)]
    if len(ref) < bins * 4 or len(cur) < bins * 4:
        return None
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:  # (near-)constant feature: no meaningful bins
        return None
    edges[0], edges[-1] = -np.inf, np.inf
    r = np.histogram(ref, edges)[0] / len(ref)
    c = np.histogram(cur, edges)[0] / len(cur)
    r, c = np.clip(r, 1e-4, None), np.clip(c, 1e-4, None)
    return float(np.sum((c - r) * np.log(c / r)))


def _criteria(manifest: dict) -> dict[str, float]:
    raw = manifest.get("drift_criteria") or {}
    out = {"max_ece": 0.08, "min_brier_skill": 0.0, "max_feature_psi": 0.25}
    for k, default in list(out.items()):
        with contextlib.suppress(TypeError, ValueError):
            out[k] = float(raw.get(k, default))
    return out


def _rows(df: pd.DataFrame, names: list[str]) -> list[dict]:
    cols = [c for c in names if c in df.columns]
    return df[cols].astype(float).replace([np.inf, -np.inf], np.nan).to_dict("records")


DEFAULT_DRIFT = DriftConfig()


def champion_drift(manifest: dict, model: CompetingModel, events: pd.DataFrame, cfg: DriftConfig = DEFAULT_DRIFT) -> dict:
    sid = manifest.get("strategy_id")
    crit = _criteria(manifest)
    through = model.metadata.get("trained_through") or (manifest.get("training_window") or {}).get("end")
    out: dict = {"model_id": manifest.get("model_id"), "strategy_id": sid, "trained_through": through, "criteria": crit}
    ev = events[events["strategy_id"] == sid] if not events.empty else events
    if ev.empty or not through:
        return out | {"status": "insufficient_data", "reason": "no events for this strategy in the drift window"}
    cut = pd.Timestamp(through)
    dates = np.sort(ev["decision_date"].unique())
    latest_start = pd.Timestamp(dates[max(0, len(dates) - cfg.latest_sessions)])
    ref_all = ev[ev["decision_date"] <= cut]
    rdates = np.sort(ref_all["decision_date"].unique())
    ref_start = pd.Timestamp(rdates[max(0, len(rdates) - cfg.reference_sessions)]) if len(rdates) else cut
    ref = ref_all[ref_all["decision_date"] >= ref_start]
    latest = ev[(ev["decision_date"] > cut) & (ev["decision_date"] >= latest_start)]
    resolved = latest[latest["filled"] & (latest["outcome"] != "DATA_END")]
    ref_resolved = ref[ref["filled"] & (ref["outcome"] != "DATA_END")]
    out.update(
        {
            "latest_window_start": str(latest_start.date()),
            "latest_events": len(latest),
            "latest_resolved": len(resolved),
            "censored_excluded": int((latest["filled"] & (latest["outcome"] == "DATA_END")).sum()),
            "reference_events": len(ref),
        }
    )
    breaches: list[str] = []
    measured = False
    if len(resolved) >= cfg.min_resolved and len(ref_resolved) >= cfg.min_resolved:
        p = model.predict(_rows(resolved, model.feature_names))[:, 0]
        y = (resolved["outcome"] == "TARGET").to_numpy(float)
        base = float((ref_resolved["outcome"] == "TARGET").mean())
        e, bs = M.ece(p, y), M.brier_skill(p, y, base)
        out["calibration"] = {
            "n": len(y),
            "ece": round(e, 5),
            "brier_skill": round(bs, 5) if np.isfinite(bs) else None,
            "observed_rate": round(float(y.mean()), 4),
            "mean_predicted": round(float(p.mean()), 4),
            "reference_base_rate": round(base, 4),
        }
        measured = True
        if e > crit["max_ece"]:
            breaches.append(f"ECE {e:.4f} > {crit['max_ece']}")
        if np.isfinite(bs) and bs < crit["min_brier_skill"]:
            breaches.append(f"Brier skill {bs:.4f} < {crit['min_brier_skill']}")
    else:
        out["calibration"] = None
    psis: dict[str, float] = {}
    if len(latest) >= cfg.min_psi_rows and len(ref) >= cfg.min_psi_rows:
        for f in MODEL_FEATURES:
            if f in ev.columns:
                v = psi(ref[f].to_numpy(float), latest[f].to_numpy(float), cfg.psi_bins)
                if v is not None:
                    psis[f] = round(v, 4)
    if psis:
        measured = True
        worst = max(psis, key=lambda k: psis[k])
        out["feature_psi_max"] = {"feature": worst, "psi": psis[worst]}
        out["feature_psi"] = dict(sorted(psis.items(), key=lambda kv: -kv[1])[:6])
        if psis[worst] > crit["max_feature_psi"]:
            breaches.append(f"PSI({worst}) {psis[worst]:.3f} > {crit['max_feature_psi']}")
    if breaches:
        status = "breach"
    elif out["calibration"] is None:
        status = "insufficient_data" if not measured else "ok_features_only"
    else:
        status = "ok"
    return out | {"status": status, "breaches": breaches}


def overall_outcome(results: list[dict]) -> str:
    if any(r["status"] == "breach" for r in results):
        return "drift_breach"
    if any(r["status"] in ("ok", "ok_features_only") for r in results):
        return "drift_ok"
    return "no_data"


def render_markdown(payload: dict) -> str:
    lines = [
        f"# APEX drift diagnostic — {payload.get('generated_at')}",
        "",
        f"* outcome: **{payload.get('outcome')}**",
        f"* data source: **{payload.get('data_source')}**",
        f"* data hash: `{str(payload.get('data_hash') or '')[:16]}`",
        "",
    ]
    if payload.get("is_demo"):
        lines += ["> DEMO / SYNTHETIC data — demonstrates the diagnostic only.", ""]
    if payload.get("detail"):
        lines += [f"> {payload['detail']}", ""]
    lines += ["| strategy | model | status | resolved n | ECE | Brier skill | max PSI | breaches |", "|---|---|---|---|---|---|---|---|"]
    for r in payload.get("champions", []):
        cal = r.get("calibration") or {}
        mx = r.get("feature_psi_max") or {}
        lines.append(
            f"| {r.get('strategy_id')} | `{r.get('model_id')}` | {r.get('status')} | {r.get('latest_resolved')} | "
            f"{cal.get('ece')} | {cal.get('brier_skill')} | {mx.get('feature')}={mx.get('psi')} | "
            f"{'; '.join(r.get('breaches') or [])[:120]} |"
        )
    return "\n".join(lines) + "\n"
