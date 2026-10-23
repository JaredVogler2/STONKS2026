"""Discovery V3 trial registry: every attempted variant is registered BEFORE its evaluation and closed AFTER it.

07_STATISTICAL_DISCOVERY_AND_ANTI_OVERFIT.md section 1. The registry is an append-only, hash-chained JSONL file:
* REGISTER_HYPOTHESIS: the full typed spec, its sha256, the objective hash and the data provenance;
* REGISTER_TRIAL: one per policy (primary + ablations). The trial id is derived from (hypothesis sha, policy sha,
  dataset hash, objective hash), so re-running the same experiment on the same data returns the SAME ids (no "second
  independent discovery"), while a new data revision or objective is visibly a new trial;
* LOOK: every evaluation of a trial's outcome (a repeated look is counted; see `looks`);
* RESULT or ABANDON: exactly one per trial.
A batch is valid only if every registered trial is closed (`validate_complete`): a search that tried 100 variants and
kept 3 cannot be reported as 3 trials. Multiplicity uses every registered trial, by family and overall.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from apex_worker.discovery.v3.contracts import EVIDENCE_TIERS, DataProvenance, HypothesisSpec, sha256_json, tier_for

GENESIS = "0" * 64
KINDS = ("REGISTER_HYPOTHESIS", "REGISTER_TRIAL", "LOOK", "RESULT", "ABANDON")


class RegistryError(ValueError):
    pass


class SearchIncomplete(RegistryError):
    """Some registered trials have no RESULT or ABANDON: the batch cannot be reported."""


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()


def trial_id(h: HypothesisSpec, policy_sha: str, dataset_hash: str, objective_sha: str) -> str:
    return "t-" + hashlib.sha256(f"{h.sha256()}|{policy_sha}|{dataset_hash}|{objective_sha}".encode()).hexdigest()[:16]


@dataclass
class TrialRegistry:
    path: Path | None = None
    events: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> TrialRegistry:
        reg = cls(path)
        if path.exists():
            reg.events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            problems = reg.verify()
            if problems:
                raise RegistryError("; ".join(problems[:3]))
        return reg

    # ---- chain ------------------------------------------------------------------------------------------------------
    def _append(self, kind: str, body: dict) -> dict:
        if kind not in KINDS:
            raise RegistryError(f"unknown event {kind}")
        ev = {"seq": len(self.events), "kind": kind, "previous": self.events[-1]["digest"] if self.events else GENESIS,
              "body": body}
        ev["digest"] = hashlib.sha256(_canon(ev)).hexdigest()
        self.events.append(ev)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev, sort_keys=True) + "\n")
        return ev

    def verify(self) -> list[str]:
        out, prev = [], GENESIS
        for i, ev in enumerate(self.events):
            d = {k: v for k, v in ev.items() if k != "digest"}
            if ev.get("seq") != i or ev.get("previous") != prev:
                out.append(f"event {i}: broken chain (inserted, deleted or reordered)")
            if hashlib.sha256(_canon(d)).hexdigest() != ev.get("digest"):
                out.append(f"event {i}: digest mismatch (edited)")
            prev = ev.get("digest")
        return out

    # ---- views ------------------------------------------------------------------------------------------------------
    def _of(self, kind: str) -> list[dict]:
        return [e for e in self.events if e["kind"] == kind]

    @property
    def trials(self) -> dict[str, dict]:
        return {e["body"]["trial_id"]: e["body"] for e in self._of("REGISTER_TRIAL")}

    @property
    def hypotheses(self) -> dict[str, dict]:
        return {e["body"]["hypothesis_id"]: e["body"] for e in self._of("REGISTER_HYPOTHESIS")}

    def closed(self) -> dict[str, dict]:
        return {e["body"]["trial_id"]: e for e in self.events if e["kind"] in ("RESULT", "ABANDON")}

    def looks(self, tid: str) -> int:
        return sum(1 for e in self._of("LOOK") if e["body"]["trial_id"] == tid)

    def family_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.trials.values():
            out[t["family_id"]] = out.get(t["family_id"], 0) + 1
        return out

    @property
    def total_trials(self) -> int:
        return len(self.trials)

    # ---- writes -----------------------------------------------------------------------------------------------------
    def register(self, h: HypothesisSpec, prov: DataProvenance, objective_sha: str) -> list[str]:
        """Register the hypothesis and one trial per policy, before any outcome. Idempotent on identical inputs."""
        existing = self.hypotheses.get(h.hypothesis_id)
        if existing is not None and existing["spec_sha256"] != h.sha256():
            raise RegistryError(f"{h.hypothesis_id} is already registered with a different spec: bump its id/version")
        if existing is None:
            self._append("REGISTER_HYPOTHESIS", {
                "hypothesis_id": h.hypothesis_id, "family_id": h.family_id, "spec": h.to_dict(),
                "spec_sha256": h.sha256(), "objective_sha256": objective_sha, "provenance": prov.__dict__,
                "status": h.status_at_registration, "blocked_inputs": h.blocked_inputs,
                "max_tier": prov.max_tier()})
        ids = []
        for pol in h.policies:
            tid = trial_id(h, pol.sha256(), prov.dataset_hash, objective_sha)
            if tid not in self.trials:
                self._append("REGISTER_TRIAL", {
                    "trial_id": tid, "hypothesis_id": h.hypothesis_id, "family_id": h.family_id,
                    "policy_id": pol.policy_id, "policy_sha256": pol.sha256(), "varies_axis": pol.varies_axis,
                    "dataset_hash": prov.dataset_hash, "objective_sha256": objective_sha,
                    "status": h.status_at_registration})
            ids.append(tid)
        return ids

    def look(self, tid: str, purpose: str) -> int:
        if tid not in self.trials:
            raise RegistryError(f"unregistered trial {tid}: evaluate only registered trials")
        self._append("LOOK", {"trial_id": tid, "purpose": purpose})
        return self.looks(tid)

    def record(self, tid: str, result: dict, prov: DataProvenance) -> dict:
        if tid not in self.trials:
            raise RegistryError(f"unregistered trial {tid}: a result without a prior registration is invalid")
        if tid in self.closed():
            raise RegistryError(f"trial {tid} already closed; a re-run on the same data is the same trial")
        tier = tier_for(prov, result.get("evidence_tier", "DEV_DIAGNOSTIC"))
        if tier not in EVIDENCE_TIERS:
            raise RegistryError("unknown evidence tier")
        body = result | {"trial_id": tid, "evidence_tier": tier, "looks": self.looks(tid),
                         "result_sha256": sha256_json(result)}
        return self._append("RESULT", body)

    def abandon(self, tid: str, reason: str) -> dict:
        if tid not in self.trials or tid in self.closed():
            raise RegistryError(f"cannot abandon {tid}")
        return self._append("ABANDON", {"trial_id": tid, "reason": reason})

    def validate_complete(self) -> None:
        open_ = sorted(set(self.trials) - set(self.closed()))
        if open_:
            raise SearchIncomplete(f"{len(open_)} of {self.total_trials} registered trials have no RESULT or ABANDON: "
                                   f"{open_[:5]}")
        orphan = sorted(set(self.hypotheses) - {t["hypothesis_id"] for t in self.trials.values()})
        if orphan:
            raise SearchIncomplete(f"hypotheses without trials: {orphan}")

    def index(self) -> list[dict]:
        """One row per registered trial (for TRIAL_INDEX.md): losers, ablations and abandoned trials included."""
        closed = self.closed()
        rows = []
        for tid, t in self.trials.items():
            c = closed.get(tid)
            b = (c or {}).get("body", {})
            rows.append({"trial_id": tid, "hypothesis_id": t["hypothesis_id"], "family_id": t["family_id"],
                         "policy_id": t["policy_id"], "varies_axis": t["varies_axis"],
                         "closed": c["kind"] if c else None, "disposition": b.get("disposition"),
                         "evidence_tier": b.get("evidence_tier"), "looks": self.looks(tid),
                         "reason": b.get("reason") or b.get("disposition_reasons")})
        return rows
