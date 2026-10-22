"""Registered experiment plans. A plan is committed (and reviewed) BEFORE the run that evaluates it; the run's manifest
records the plan sha256. PR-B registers only an ENGINE SMOKE plan, which is fixture-only by construction: it exercises
the pipeline end to end and can never be dispatched on market data. Real V3 hypotheses are registered in PR-C with
their own pre-registration document (docs/discovery-v3/PILOT_PREREGISTRATION.md)."""

from __future__ import annotations

from apex_worker.discovery.v3.contracts import AllocationSpec, HypothesisSpec, InputRequirement, PolicySpec
from apex_worker.discovery.v3.pipeline import ExperimentPlan

DAILY_BARS = InputRequirement("daily_sip_bars", True, True, "alpaca historical SIP, split-adjusted", "no redistribution")

_SMOKE_PRIMARY = PolicySpec("smoke-momentum-idle-spy", "momentum_top", "E0X1", AllocationSpec(idle_capital="SPY"),
                            params={"n": 3, "every": 10})
_SMOKE_ABLATION = PolicySpec("smoke-momentum-idle-cash", "momentum_top", "E0X1", AllocationSpec(idle_capital="CASH"),
                             params={"n": 3, "every": 10}, varies_axis="idle_capital",
                             parent_policy="smoke-momentum-idle-spy")

ENGINE_SMOKE = ExperimentPlan(
    plan_id="engine-smoke", fixture_only=True,
    title="ENGINE SMOKE (synthetic fixture only): pipeline, registry, books, benchmarks and dispositions",
    hypotheses=(
        HypothesisSpec(
            hypothesis_id="engine-smoke-idle-capital", family_id="engine-smoke",
            title="ENGINE SMOKE: one selection, idle capital in SPY versus cash",
            economic_mechanism="Idle equity left in cash forgoes the equity premium, so a mostly idle swing book should "
            "trail a broad index unless the idle capital is parked in that index",
            competing_explanation="Any difference is market beta, not selection: parking idle capital in SPY simply "
            "raises equity exposure",
            null_hypothesis="The SPY-idle book's log growth is no higher than SPY buy-and-hold's over the same sessions",
            falsification_test="Active log growth versus SPY buy-and-hold <= 0, or the advantage vanishes against the "
            "matched-control books under the same allocation",
            negative_controls=("random_eligible selection under the same execution and book",),
            baseline_ids=("cash", "spy_hold", "exposure_matched"), required_inputs=(DAILY_BARS,),
            primary_policy=_SMOKE_PRIMARY, ablations=(_SMOKE_ABLATION,), author_type="agent"),
        HypothesisSpec(
            hypothesis_id="engine-smoke-blocked-graph", family_id="engine-smoke",
            title="ENGINE SMOKE: a hypothesis whose point-in-time inputs do not exist is SOURCE_BLOCKED",
            economic_mechanism="Customers and suppliers react with a lag to each other's verified public news, so a "
            "linked laggard drifts toward the leader's re-rating",
            competing_explanation="Co-movement reflects shared industry exposure, not information propagation along "
            "economic links",
            null_hypothesis="Graph-linked laggards earn no more than same-sector peers with the same costs and timing",
            falsification_test="Degree-preserving randomised edges and time-shuffled events perform as well as the "
            "real graph",
            negative_controls=("randomised edges", "reverse-direction placebo"), baseline_ids=("sector peers",),
            required_inputs=(DAILY_BARS, InputRequirement("pit_supplier_customer_graph", False, False,
                                                          "no licensed point-in-time source")),
            primary_policy=PolicySpec("smoke-graph-laggard", "none", "E0X1"), author_type="agent"),
    ))

PLANS: dict[str, ExperimentPlan] = {p.plan_id: p for p in (ENGINE_SMOKE,)}
