"""Discovery V3 shared contracts (03_DISCOVERY_ENGINE_ARCHITECTURE.md section 2).

A hypothesis is a falsifiable economic claim; a POLICY is a complete trade policy (selection + entry + exit + allocation +
what idle capital does). Every experiment evaluates registered policies against registered baselines under a
pre-registered objective, on data whose provenance and contamination status travel with the result.

Validation rules that encode the research discipline:
* a hypothesis needs a mechanism, a competing explanation, a null, a falsification test, at least one negative control
  and one baseline; "mix indicator A with B" is not a mechanism (a minimum of substance is enforced, not judged);
* every required input must be causally available; an unavailable input makes the hypothesis SOURCE_BLOCKED, never a
  zero-alpha result;
* the experiment budget (variants) is fixed at registration; release mode is always RESEARCH_ONLY;
* any window inside 2016-01-04..2026-10-22 is CONTAMINATED for V3 (inspected by earlier work): its best tier is
  DEV_DIAGNOSTIC, whatever its label.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date

EVIDENCE_TIERS = ("MECHANISM_PROPOSAL", "DEV_DIAGNOSTIC", "PROSPECTIVE_EXPLORATORY", "INDEPENDENT_CONFIRMATORY")
STATUSES = ("PROPOSED", "REGISTERED", "SOURCE_BLOCKED", "DEV_TESTED", "REJECTED", "RESEARCH_SHORTLIST", "FORWARD_SHADOW",
            "INDEPENDENTLY_REPLICATED", "OWNER_REVIEW_REQUIRED")
DISPOSITIONS = ("REJECTED_DEV", "PROMISING_EXPLORATORY", "SHADOW_ONLY_REGISTERED", "INSUFFICIENT_FORWARD_POWER",
                "CONFIRMED_NET_ECONOMIC_VALUE", "REPLICATION_REQUIRED", "NO_TRADE", "SOURCE_BLOCKED", "SIMULATOR_INVALID",
                "COMPLIANCE_BLOCKED", "INCONCLUSIVE_DEV")
AXES = ("selection", "entry", "exit", "allocation", "idle_capital", "market_filter")
CONTAMINATED_UNTIL = date(2026, 10, 22)  # every session through this date was inspected by protocol-1/-2 work
DATA_START = date(2016, 1, 4)
MAX_VARIANTS_PER_HYPOTHESIS = 3  # 1 primary + at most 2 documented ablations (05 section 5), all logged
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
INDICATORS = {"rsi", "macd", "sma", "ema", "ma", "bollinger", "stoch", "stochastic", "atr", "adx", "cci", "obv", "vwap",
              "williams", "ichimoku", "fibonacci", "band", "bands", "crossover", "cross", "crosses", "signal", "line"}
FILLER = {"with", "and", "plus", "together", "combined", "using", "above", "below", "over", "under", "of", "the", "a", "an",
          "on", "when", "then", "or", "to", "at", "in", "by", "is"}


def _indicator_soup(text: str) -> bool:
    """True when, after removing indicator names, numbers and filler words, almost nothing economic remains."""
    words = re.findall(r"[a-z]+", text.lower())
    meaningful = [w for w in words if w not in INDICATORS and w not in FILLER and len(w) >= 4]
    return len(meaningful) < 4


class ContractError(ValueError):
    pass


def sha256_json(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@dataclass(frozen=True)
class InputRequirement:
    name: str  # e.g. "daily_sip_bars", "pit_earnings_first_public", "sec_supplier_graph"
    available: bool
    causal: bool  # observable at the decision time with its own available_at timestamp
    source: str = ""
    licence_note: str = ""


@dataclass(frozen=True)
class AllocationSpec:
    slots: int = 10
    sector_cap: float = 0.30
    sizing: str = "equal_slot"  # equal share of previous-close equity per slot
    idle_capital: str = "CASH"  # CASH | SPY: what un-deployed equity holds
    max_exposure: float = 1.0
    leverage: float = 0.0

    def __post_init__(self) -> None:
        if self.idle_capital not in ("CASH", "SPY"):
            raise ContractError("idle_capital must be CASH or SPY")
        if self.leverage != 0.0 or not 0 < self.max_exposure <= 1.0:
            raise ContractError("no leverage; max_exposure in (0, 1]")
        if not 1 <= self.slots <= 50 or not 0 < self.sector_cap <= 1:
            raise ContractError("slots in 1..50, sector_cap in (0, 1]")


@dataclass(frozen=True)
class PolicySpec:
    """A complete policy. `selector` names a registered selector in agents.SELECTORS; `arm` an execution_v2 arm
    (E0X0, E0X1, ...) or "WEIGHTS" for allocation rules that trade at the next open without stops."""

    policy_id: str
    selector: str
    arm: str
    allocation: AllocationSpec = field(default_factory=AllocationSpec)
    params: dict = field(default_factory=dict)
    cost_bps_per_side: float = 25.0
    varies_axis: str | None = None  # the single axis this variant changes against its parent (None = primary)
    parent_policy: str | None = None

    def __post_init__(self) -> None:
        if not SLUG.match(self.policy_id):
            raise ContractError(f"policy_id {self.policy_id!r} must be a lower-case slug")
        if self.varies_axis is not None and self.varies_axis not in AXES:
            raise ContractError(f"varies_axis must be one of {AXES}")
        if (self.varies_axis is None) != (self.parent_policy is None):
            raise ContractError("a variant names both its parent and the single axis it changes")
        if self.cost_bps_per_side < 0:
            raise ContractError("cost must be >= 0")

    def to_dict(self) -> dict:
        return asdict(self)

    def sha256(self) -> str:
        return sha256_json(self.to_dict())


@dataclass(frozen=True)
class HypothesisSpec:
    hypothesis_id: str
    family_id: str  # mechanism family (F1..F10 or a new id); multiplicity is counted per family AND overall
    title: str
    economic_mechanism: str
    competing_explanation: str
    null_hypothesis: str
    falsification_test: str
    negative_controls: tuple[str, ...]
    baseline_ids: tuple[str, ...]
    required_inputs: tuple[InputRequirement, ...]
    primary_policy: PolicySpec
    ablations: tuple[PolicySpec, ...] = ()
    minimum_economic_effect: str = "+1.0 percentage point per year of log growth vs the exposure-matched benchmark"
    horizon_sessions: tuple[int, ...] = (20,)
    permitted_universe: str = "U1 (survivorship-limited)"
    author_type: str = "agent"
    semantic_version: str = "1.0.0"
    parent_hypothesis: str | None = None
    proposed_at: str = ""
    release_mode: str = "RESEARCH_ONLY"

    def __post_init__(self) -> None:
        if not SLUG.match(self.hypothesis_id) or not SLUG.match(self.family_id):
            raise ContractError("hypothesis_id and family_id must be lower-case slugs")
        for name in ("economic_mechanism", "competing_explanation", "null_hypothesis", "falsification_test"):
            text = getattr(self, name).strip()
            if len(text) < 40:
                raise ContractError(f"{name} needs a substantive statement (>= 40 characters)")
        if _indicator_soup(self.economic_mechanism):
            raise ContractError("an indicator combination is not an economic mechanism")
        if not self.negative_controls or not self.baseline_ids:
            raise ContractError("at least one negative control and one baseline are required")
        if 1 + len(self.ablations) > MAX_VARIANTS_PER_HYPOTHESIS:
            raise ContractError(f"at most {MAX_VARIANTS_PER_HYPOTHESIS} variants per hypothesis (1 primary + ablations)")
        for ab in self.ablations:
            if ab.parent_policy != self.primary_policy.policy_id or ab.varies_axis is None:
                raise ContractError("an ablation varies exactly one axis of the primary policy")
        if self.release_mode != "RESEARCH_ONLY":
            raise ContractError("release_mode is always RESEARCH_ONLY")
        if self.author_type not in ("human", "agent"):
            raise ContractError("author_type is human or agent")

    @property
    def blocked_inputs(self) -> list[str]:
        return [r.name for r in self.required_inputs if not (r.available and r.causal)]

    @property
    def status_at_registration(self) -> str:
        return "SOURCE_BLOCKED" if self.blocked_inputs else "REGISTERED"

    @property
    def policies(self) -> tuple[PolicySpec, ...]:
        return (self.primary_policy, *self.ablations)

    def to_dict(self) -> dict:
        return asdict(self)

    def sha256(self) -> str:
        return sha256_json(self.to_dict())


@dataclass(frozen=True)
class DataProvenance:
    """MarketSnapshot essentials carried by every result (no prices)."""

    data_class: str  # REAL_MARKET_DATA | SYNTHETIC_FIXTURE_DEMO
    provider: str
    feed: str
    adjustment: str
    fetched_at: str
    window_start: str
    window_end: str
    dataset_hash: str
    history_hash: str | None = None
    universe_id: str = "U1"
    universe_snapshot_sha256: str | None = None
    membership_rule: str = "date_added <= t (current constituents only)"
    survivorship: str = "DATA_LIMITED_SURVIVORS"
    corporate_actions: str | None = None
    rights_note: str = "derived aggregates only; no vendor OHLCV is stored or published"

    def __post_init__(self) -> None:
        if self.data_class not in ("REAL_MARKET_DATA", "SYNTHETIC_FIXTURE_DEMO"):
            raise ContractError("data_class must be REAL_MARKET_DATA or SYNTHETIC_FIXTURE_DEMO")

    def contaminated(self) -> bool:
        return date.fromisoformat(self.window_start) <= CONTAMINATED_UNTIL

    def max_tier(self) -> str:
        """The best evidence tier a result on this data can carry."""
        if self.data_class != "REAL_MARKET_DATA":
            return "MECHANISM_PROPOSAL"
        return "DEV_DIAGNOSTIC" if self.contaminated() else "PROSPECTIVE_EXPLORATORY"


@dataclass(frozen=True)
class EvaluationWindow:
    start: str
    end: str
    role: str = "DEV_CONTAMINATED"
    note: str = ("2019-10-23 onward was the protocol-2 DEV segment; 2017-2019 PRESAMPLE was evaluated (and exposed) by "
                 "the official run. Neither is untouched for V3.")

    def __post_init__(self) -> None:
        if date.fromisoformat(self.end) < date.fromisoformat(self.start):
            raise ContractError("window end before start")
        if date.fromisoformat(self.start) <= CONTAMINATED_UNTIL and self.role != "DEV_CONTAMINATED":
            raise ContractError("any window before 2026-10-23 is DEV_CONTAMINATED for V3; relabelling is not allowed")


def tier_for(prov: DataProvenance, requested: str) -> str:
    """Never more than the data allow: requesting a better tier on contaminated data is silently capped."""
    allowed = EVIDENCE_TIERS.index(prov.max_tier())
    return EVIDENCE_TIERS[min(EVIDENCE_TIERS.index(requested), allowed)]
