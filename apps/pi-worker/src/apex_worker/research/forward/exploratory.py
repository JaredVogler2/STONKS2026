"""Discovery V3 exploratory forward lane: schema and guards only (PR-A). No policy is registered yet.

Separation from the frozen lane (amendments/discovery-v3-2026-10-22/02_FORWARD_SHADOW_FIRST.md):
* its own series id (prefix `v3x-`), lane `EXPLORATORY`, chain and ledger issue: no record, object or file is shared;
* every record body carries `evidence_tier = PROSPECTIVE_EXPLORATORY` and `confirmatory = false`; interim outcomes may
  be inspected and the policy refined, so this lane can never support an untouched confirmatory claim;
* a policy whose decision logic, feature schema or parameters change starts a NEW series (new semantic major version);
  the old series stays verifiable and is never re-run;
* the registry of exploratory policies lives here and never imports or mutates frozen-lane state.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field

from apex_worker.research.forward.frozen import SERIES_ID as FROZEN_SERIES_ID
from apex_worker.research.forward.records import canonical

LANE = "EXPLORATORY"
EVIDENCE_TIER = "PROSPECTIVE_EXPLORATORY"
REGISTRATION_STATES = ("HYPOTHESIS_UNREGISTERED", "EXPLORATORY_REGISTERED")
SERIES_RE = re.compile(r"^v3x-[a-z0-9]+(?:-[a-z0-9]+)*-v\d+$")


@dataclass(frozen=True)
class ExploratoryPolicy:
    """A V3 candidate policy recorded prospectively. `decide_ref` names a function in apex_worker.discovery (PR-B);
    it is resolved only by the exploratory recorder and never by the frozen lane."""

    policy_id: str  # lower-case slug, e.g. "f2-dispersion-gate"
    major_version: int
    hypothesis_id: str
    registration: str  # HYPOTHESIS_UNREGISTERED | EXPLORATORY_REGISTERED
    mechanism: str
    decide_ref: str
    feature_schema_sha256: str
    parameters: dict = field(default_factory=dict)
    universe: str = "U1"
    research_only: bool = True

    def __post_init__(self) -> None:
        if self.registration not in REGISTRATION_STATES:
            raise ValueError(f"registration must be one of {REGISTRATION_STATES}")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", self.policy_id):
            raise ValueError("policy_id must be a lower-case slug")
        if not self.decide_ref.startswith("apex_worker.discovery."):
            raise ValueError("exploratory decision logic must live in apex_worker.discovery (never the frozen lane)")
        if not self.research_only:
            raise ValueError("exploratory policies are research only")

    @property
    def series_id(self) -> str:
        sid = f"v3x-{self.policy_id}-v{self.major_version}"
        if not SERIES_RE.match(sid) or sid == FROZEN_SERIES_ID:
            raise ValueError(f"invalid exploratory series id {sid}")
        return sid

    def config(self) -> dict:
        return {"series_id": self.series_id, "lane": LANE, "evidence_tier": EVIDENCE_TIER, "confirmatory": False,
                "policy": asdict(self), "mode": "RESEARCH_ONLY", "orders": "none", "alerts": "none",
                "group_actionable": False}

    def config_sha256(self) -> str:
        return hashlib.sha256(canonical(self.config())).hexdigest()


# Registered exploratory policies (append-only; PR-C registers the first after its DEV pilot). Empty in PR-A.
EXPLORATORY_POLICIES: tuple[ExploratoryPolicy, ...] = ()


def label_body(body: dict) -> dict:
    """Every exploratory record body is labelled; a body claiming confirmatory status is refused."""
    if body.get("confirmatory") or body.get("evidence_tier") not in (None, EVIDENCE_TIER):
        raise ValueError("exploratory records cannot be confirmatory")
    return body | {"evidence_tier": EVIDENCE_TIER, "confirmatory": False}


def assert_separate(series_ids: list[str]) -> None:
    """No exploratory series may reuse the frozen id or another series' id (no shared chains)."""
    if FROZEN_SERIES_ID in series_ids or len(set(series_ids)) != len(series_ids):
        raise ValueError("exploratory series ids must be unique and distinct from the frozen series")
