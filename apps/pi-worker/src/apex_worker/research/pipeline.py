"""Unattended training run (GitHub-hosted runner or local sandbox): data -> events -> purged walk-forward ->
calibration -> frozen promotion gates -> champion/challenger -> signed candidate manifests.

Outputs (out_dir):
  report.json            all strategy reports + run metadata (no secrets)
  summary.md             human summary for the GitHub job summary / dashboard
  releases/<model_id>/   model.json + manifest.json for every candidate that passed gates
A candidate is only *promotable* if it passed the frozen policy AND beat the incumbent; this module never
publishes anything by itself (release is a separate, protected step).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from apex_worker.models.manifest import ReleaseInputs, build_manifest, sign
from apex_worker.research.dataset import MODEL_FEATURES, DatasetSpec, baseline_events, build_events, data_hash
from apex_worker.research.evaluate import PromotionPolicy, challenger_beats_champion, evaluate_strategy
from apex_worker.research.redteam import REDTEAM_STRATEGY, planted_alpha_events
from apex_worker.signals.strategies.library import STRATEGIES

LABEL_DEFINITION = {
    "type": "TARGET_BEFORE_STOP",
    "horizon_sessions": 20,
    "same_bar_rule": "STOP_FIRST",
    "entry": "next-session limit over entry zone, 2-session validity",
    "cost_bps_per_side": "10",
}


@dataclass
class TrainingResult:
    status: str  # succeeded | no_data | failed
    data_source: str
    reports: dict[str, dict]
    candidates: list[dict] = field(default_factory=list)
    decisions: dict[str, dict] = field(default_factory=dict)
    seconds: float = 0.0
    detail: str = ""


def _load_synthetic(stocks_per_sector: int, start: date) -> tuple:
    from apex_worker.demo.synthetic import generate
    from apex_worker.features.panel import panel_from_synthetic

    market = generate(stocks_per_sector=stocks_per_sector, start=start)
    panel = panel_from_synthetic(market)
    bars = {s: market.adjusted(s) for s in market.bars}
    return panel, bars, market.earnings, [], "synthetic_fixture"


def run_training(
    source: str,
    out_dir: Path,
    *,
    strategies: list[str] | None = None,
    code_sha: str = "unknown",
    signing_key=None,
    signer_key_version: int = 1,
    champions: dict[str, dict] | None = None,
    policy: PromotionPolicy = PromotionPolicy(),
    is_demo: bool = True,
    now: datetime | None = None,
    stocks_per_sector: int = 24,
    start: date = date(2016, 1, 4),
    loader=None,
) -> TrainingResult:
    t0 = time.monotonic()
    now = now or datetime.now(UTC)
    out_dir.mkdir(parents=True, exist_ok=True)
    reports: dict[str, dict] = {}
    events = pd.DataFrame()
    bars: dict = {}
    if source == "redteam":
        events = planted_alpha_events()
        ids = [REDTEAM_STRATEGY]
        ds = "synthetic_fixture"
        dhash = "redteam-planted-alpha-seed99"
    else:
        if source == "synthetic":
            panel, bars, earnings, cats, ds = _load_synthetic(stocks_per_sector, start)
        elif loader is not None:
            loaded = loader()
            if loaded is None:
                return TrainingResult(
                    "no_data",
                    "historical_market_data",
                    {},
                    detail="historical data source unavailable "
                    "(credentials missing or provider error); champion retained",
                )
            panel, bars, earnings, cats, ds = loaded
        else:
            return TrainingResult("no_data", source, {}, detail=f"unknown source {source}")
        ids = strategies or [s for s, st in STRATEGIES.items() if st.spec.default_enabled]
        spec = DatasetSpec(strategies=[STRATEGIES[s] for s in ids if s in STRATEGIES])
        events = build_events(panel, bars, spec, catalysts=cats, earnings=earnings)
        dhash = data_hash(bars)
    if events.empty:
        return TrainingResult("no_data", ds, {}, detail="no events produced")
    candidates: list[dict] = []
    decisions: dict[str, dict] = {}
    base = None
    for sid in ids:
        if source != "redteam" and base is None and bars:
            ref = events[events["strategy_id"] == sid]
            base = baseline_events(panel, bars, spec, ref) if not ref.empty else None
        rep, wf = evaluate_strategy(events, sid, MODEL_FEATURES, ds, baselines=base, policy=policy)
        d = rep.to_dict()
        reports[sid] = d
        champ = (champions or {}).get(sid)
        promotable, why = challenger_beats_champion(d, champ, policy)
        decision = {
            "promotable": bool(promotable and wf.final_model is not None),
            "reason": why,
            "status": d["confidence_status"],
        }
        if wf.final_model is not None and d["confidence_status"] != "UNVALIDATED":
            artifact = wf.final_model.to_json().encode()
            ev = events[events["strategy_id"] == sid]
            manifest = build_manifest(
                ReleaseInputs(
                    strategy_id=sid,
                    artifact=artifact,
                    report=d,
                    training_window={
                        "start": str(ev["decision_date"].min().date()),
                        "end": wf.final_model.metadata.get("trained_through", ""),
                    },
                    training_data_hash=dhash,
                    code_sha=code_sha,
                    data_source=ds,
                    is_demo=is_demo or ds == "synthetic_fixture",
                    label_definition=LABEL_DEFINITION,
                    validity_days=policy.validity_days,
                    rollback_parent=(champ or {}).get("model_id"),
                ),
                signer_key_version,
                now,
            )
            if signing_key is not None:
                manifest = sign(manifest, signing_key)
            rel = out_dir / "releases" / manifest["model_id"]
            rel.mkdir(parents=True, exist_ok=True)
            (rel / "model.json").write_bytes(artifact)
            (rel / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
            decision["model_id"] = manifest["model_id"]
            candidates.append(
                {
                    "strategy_id": sid,
                    "model_id": manifest["model_id"],
                    "promotable": decision["promotable"],
                    "signed": "signature" in manifest,
                }
            )
        decisions[sid] = decision
    res = TrainingResult("succeeded", ds, reports, candidates, decisions, round(time.monotonic() - t0, 1))
    write_outputs(res, out_dir, code_sha, now)
    return res


def write_outputs(res: TrainingResult, out_dir: Path, code_sha: str, now: datetime) -> None:
    payload = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code_sha": code_sha,
        "status": res.status,
        "data_source": res.data_source,
        "seconds": res.seconds,
        "decisions": res.decisions,
        "candidates": res.candidates,
        "reports": res.reports,
    }
    (out_dir / "report.json").write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
    lines = [
        f"# APEX training run — {payload['generated_at']}",
        "",
        f"* data source: **{res.data_source}**",
        f"* code: `{code_sha}`",
        f"* runtime: {res.seconds}s",
        "",
    ]
    if res.data_source == "synthetic_fixture":
        lines += ["> SYNTHETIC FIXTURE DATA — pipeline demonstration only, not evidence of a real edge.", ""]
    lines += [
        "| strategy | status | holdout n | Brier skill | ECE | promotable | reason |",
        "|---|---|---|---|---|---|---|",
    ]
    for sid, r in res.reports.items():
        h = r.get("holdout") or {}
        dec = res.decisions.get(sid, {})
        lines.append(
            f"| {sid} | {r['confidence_status']} | {h.get('n')} | {h.get('brier_skill')} | {h.get('ece')} | "
            f"{dec.get('promotable')} | {dec.get('reason', '')[:80]} |"
        )
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n")
