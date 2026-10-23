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

# ---- PILOT 1 (PR-C): four mechanism families, 10 trials, registered BEFORE any V3 outcome on market data ------------
# docs/discovery-v3/PILOT_PREREGISTRATION.md is the human copy. DEV only (contaminated history): the best possible
# disposition is PROMISING_EXPLORATORY, a nomination for a separately registered prospective shadow.
SPY_IDLE = AllocationSpec(idle_capital="SPY")
BASELINES = ("cash", "spy_hold", "exposure_matched")

_F1 = PolicySpec("f1-s1-idle-spy", "s1_leader_pullback", "E0X1", SPY_IDLE, params={"seed_name": "S1"})
_F5 = PolicySpec("f5-momentum-ew-monthly", "momentum_weights", "WEIGHTS", params={"n": 10, "every": 20})
_F2 = PolicySpec("f2-momentum-swing-dispersion-gate", "momentum_top", "E0X1", SPY_IDLE,
                 params={"n": 5, "every": 5, "min_dispersion_pct": 0.6})
_F9 = PolicySpec("f9-spy-regime-abstain", "spy_regime", "WEIGHTS", params={"abstain": ["risk_off", "volatility_shock"]},
                 cost_bps_per_side=5.0)

PILOT_1 = ExperimentPlan(
    plan_id="v3-pilot-1", fixture_only=False,
    title="V3 pilot 1: idle capital, cross-sectional momentum, opportunity-state gating, regime abstention (DEV only)",
    notes="Root cause from BASELINE_AUDIT.md: S1 has book-level selection value versus matched controls, but every swing "
    "book loses to SPY at the same exposure (wrapper drag) and leaves about half its equity idle (cash drag). The pilot "
    "tests portfolio-level mechanisms against the investable alternative, not new indicators.",
    hypotheses=(
        HypothesisSpec(
            hypothesis_id="f1-leader-idle-capital", family_id="f1-leader-continuation",
            title="S1 leaders with idle equity held in SPY",
            economic_mechanism="S1 picks sector leaders whose continuation beats same-sector peers, but its book leaves "
            "about half of equity in cash; holding that idle equity in SPY keeps the equity premium while the S1 slots "
            "add their selection value net of the swing wrapper's costs",
            competing_explanation="The wrapper (stops, 20-session exits, 25 bps per side on high turnover) costs more "
            "than selection adds, so an SPY-idle S1 book trails SPY by the wrapper drag",
            null_hypothesis="The SPY-idle S1 book's annualised log growth over DEV is no higher than SPY buy-and-hold's",
            falsification_test="Active log growth versus SPY <= 0, or no advantage over the matched-control books under "
            "the same SPY-idle allocation",
            negative_controls=("matched-control books (same execution, allocation, dates)", "random-eligible placebo"),
            baseline_ids=BASELINES, required_inputs=(DAILY_BARS,), primary_policy=_F1,
            ablations=(
                PolicySpec("f1-s1-idle-cash", "s1_leader_pullback", "E0X1", AllocationSpec(), params={"seed_name": "S1"},
                           varies_axis="idle_capital", parent_policy=_F1.policy_id),
                PolicySpec("f1-s1-idle-spy-h40", "s1_leader_pullback", "E0X1", SPY_IDLE,
                           params={"seed_name": "S1", "horizon": 40}, varies_axis="exit", parent_policy=_F1.policy_id)),
            horizon_sessions=(20, 40), author_type="agent", proposed_at="2026-10-22"),
        HypothesisSpec(
            hypothesis_id="f5-xs-momentum-monthly", family_id="f5-cross-sectional-momentum",
            title="Cross-sectional 60-session momentum, equal weight, monthly rebalance",
            economic_mechanism="Investors underreact to firm-specific news and capital moves slowly, so recent relative "
            "winners keep outperforming for weeks to months; a low-turnover monthly portfolio harvests this without the "
            "swing wrapper",
            competing_explanation="Any outperformance of SPY comes from holding equal-weighted survivors of today's index "
            "(survivorship and size), not from momentum",
            null_hypothesis="The momentum portfolio's log growth is no higher than SPY's, and no higher than an "
            "equal-weight random portfolio from the same members on the same schedule",
            falsification_test="Active log growth versus SPY <= 0, or the random equal-weight ablation matches it",
            negative_controls=("random equal-weight members, same schedule and costs",), baseline_ids=BASELINES,
            required_inputs=(DAILY_BARS,), primary_policy=_F5,
            ablations=(
                PolicySpec("f5-random-ew-monthly", "random_weights", "WEIGHTS", params={"n": 10, "every": 20},
                           varies_axis="selection", parent_policy=_F5.policy_id),
                PolicySpec("f5-momentum-ew-monthly-abstain", "momentum_weights", "WEIGHTS",
                           params={"n": 10, "every": 20, "abstain": True}, varies_axis="market_filter",
                           parent_policy=_F5.policy_id)),
            author_type="agent", proposed_at="2026-10-22"),
        HypothesisSpec(
            hypothesis_id="f2-dispersion-gated-swing", family_id="f2-opportunity-state",
            title="Momentum swing trades only when cross-sectional dispersion is high, SPY otherwise",
            economic_mechanism="Stock selection can only add value when stocks' returns differ; when dispersion is low "
            "the wrapper's costs exceed any selection gain, so capital should sit in the index and be deployed into "
            "swing trades only in high-dispersion states",
            competing_explanation="High dispersion coincides with market stress and high volatility, so the gate merely "
            "times market risk rather than selection opportunity",
            null_hypothesis="The gated book's log growth is no higher than SPY's and no higher than the ungated book's",
            falsification_test="Active log growth versus SPY <= 0, or the ungated ablation does as well",
            negative_controls=("matched-control books", "the ungated ablation"), baseline_ids=BASELINES,
            required_inputs=(DAILY_BARS,), primary_policy=_F2,
            ablations=(PolicySpec("f2-momentum-swing-ungated", "momentum_top", "E0X1", SPY_IDLE,
                                  params={"n": 5, "every": 5}, varies_axis="market_filter",
                                  parent_policy=_F2.policy_id),),
            author_type="agent", proposed_at="2026-10-22"),
        HypothesisSpec(
            hypothesis_id="f9-regime-abstention", family_id="f9-regime-abstention",
            title="SPY with cash in risk-off and volatility-shock regimes",
            economic_mechanism="An online regime classifier built from breadth, trend and volatility flags states with "
            "unfavourable expected equity returns and crash risk; stepping into cash then cuts drawdowns more than it "
            "costs in missed rebounds",
            competing_explanation="The regime label lags price, so abstention sells after losses and buys back after "
            "rebounds (whipsaw), lowering growth without a matching cut in tail risk",
            null_hypothesis="Regime-abstaining SPY's log growth is no higher than SPY buy-and-hold's",
            falsification_test="Active log growth versus SPY <= 0 (utilities and drawdown are reported, but the "
            "registered principal is growth)",
            negative_controls=("volatility-shock-only abstention",), baseline_ids=BASELINES,
            required_inputs=(DAILY_BARS,), primary_policy=_F9,
            ablations=(PolicySpec("f9-spy-shock-only", "spy_regime", "WEIGHTS", params={"abstain": ["volatility_shock"]},
                                  cost_bps_per_side=5.0, varies_axis="market_filter", parent_policy=_F9.policy_id),),
            author_type="agent", proposed_at="2026-10-22"),
    ))

# ---- PILOT 2: execution timing at the MEASURED cost, low-turnover momentum (docs/discovery-v3/PILOT_2_PREREGISTRATION.md)
# Designed AFTER pilot 1 and cost-study-1 on the same DEV history (contaminated twice): 7 trials, Holm over the 7, the
# cumulative V3 trial count carries over (17), and the best possible disposition is again PROMISING_EXPLORATORY.
COST_V3_2 = {"cost_model": "cost-v3-2"}
_G1 = PolicySpec("g1-s1-close-spy-idle", "s1_leader_pullback", "ECX1", SPY_IDLE, params={"seed_name": "S1", **COST_V3_2})
_G2_PARAMS = {"n": 10, "every": 60, "keep_rank": 20, "mask": "explicit", **COST_V3_2}
_G2 = PolicySpec("g2-momentum-quarterly-buffered-close", "momentum_buffered", "WEIGHTS",
                 params={**_G2_PARAMS, "execution_at": "close", "random_books": 20})
_G3_PARAMS = {"n": 5, "every": 5, **COST_V3_2}
_G3 = PolicySpec("g3-momentum-swing-dispersion-gate-close", "momentum_top", "ECX1", SPY_IDLE,
                 params={**_G3_PARAMS, "min_dispersion_pct": 0.6})

PILOT_2 = ExperimentPlan(
    plan_id="v3-pilot-2", fixture_only=False, objective_id="obj-v3-2",
    title="V3 pilot 2: close execution at the measured cost, low-turnover fully invested momentum (DEV only)",
    notes="From pilot 1 (friction binds; S1 selection value is small; F5 lost to costs and to inherited risk-off cash "
    "months) and cost-study-1 (an entry chasing the open costs about 20 bps per side, an order at the close about 2). "
    "Post hoc on the same DEV history: an exploratory batch whose best outcome is a nomination for the prospective lane.",
    hypotheses=(
        HypothesisSpec(
            hypothesis_id="g1-s1-close-execution", family_id="g1-leader-execution-timing",
            title="S1 leader decisions executed at the next close instead of the next open",
            economic_mechanism="S1 picks leaders whose continuation beats matched peers by a little, but buying at "
            "the next open costs about 20 bps per side (cost-study-1), most of the measured friction; buying the same "
            "decisions with a limit-on-close order at the next close costs about 2 bps, so the selection value may "
            "survive the friction that erased it in pilot 1",
            competing_explanation="The entry session's open-to-close move is part of S1's edge (overnight and "
            "intraday continuation), so buying at the close forfeits as much as it saves in costs",
            null_hypothesis="The SPY-idle S1 book executed at the close has annualised log growth no higher than SPY "
            "buy-and-hold over the DEV sessions, at the measured cost model cost-v3-2",
            falsification_test="Active log growth versus SPY <= 0, or the open-executed ablation does as well under "
            "the same cost model, or no advantage over its matched-control books",
            negative_controls=("matched-control books (same close timing, cost model, allocation, dates)",
                               "the open-executed ablation"),
            baseline_ids=BASELINES, required_inputs=(DAILY_BARS,), primary_policy=_G1,
            ablations=(PolicySpec("g1-s1-open-spy-idle", "s1_leader_pullback", "E0X1", SPY_IDLE,
                                  params={"seed_name": "S1", **COST_V3_2}, varies_axis="entry",
                                  parent_policy=_G1.policy_id),),
            author_type="agent", proposed_at="2026-10-23"),
        HypothesisSpec(
            hypothesis_id="g2-low-turnover-momentum", family_id="g2-cross-sectional-momentum-low-turnover",
            title="Fully invested 60-session momentum, quarterly with a rank buffer, traded at the close",
            economic_mechanism="Recent relative winners keep outperforming for weeks to months because investors "
            "underreact; pilot 1's monthly momentum beat SPY before costs but sat in cash for inherited risk-off "
            "months and turned over fully every month, so a fully invested book that rebalances quarterly, keeps "
            "holdings while they stay in the top 20, and trades at the cheap close keeps the premium and drops the drag",
            competing_explanation="Any edge over SPY comes from holding equal-weighted survivors of today's index "
            "(survivorship and size), not momentum: random buffered books from the same members would match it",
            null_hypothesis="The momentum book's log growth is no higher than SPY's at cost-v3-2, and at zero cost no "
            "higher than the mean of 20 random buffered books from the same members on the same schedule",
            falsification_test="Active log growth versus SPY <= 0, or the zero-cost book does not beat the mean of the "
            "20 random buffered books (the selection test), or the open-executed ablation does as well",
            negative_controls=("20 random buffered books: same mask, schedule, buffer and close execution, zero cost",
                               "the open-executed ablation", "the monthly unbuffered ablation"),
            baseline_ids=BASELINES, required_inputs=(DAILY_BARS,), primary_policy=_G2,
            ablations=(
                PolicySpec("g2-momentum-quarterly-buffered-open", "momentum_buffered", "WEIGHTS",
                           params={**_G2_PARAMS, "execution_at": "open"}, varies_axis="entry",
                           parent_policy=_G2.policy_id),
                PolicySpec("g2-momentum-monthly-unbuffered-close", "momentum_buffered", "WEIGHTS",
                           params={**_G2_PARAMS, "every": 20, "keep_rank": 10, "execution_at": "close"},
                           varies_axis="exit", parent_policy=_G2.policy_id)),
            author_type="agent", proposed_at="2026-10-23"),
        HypothesisSpec(
            hypothesis_id="g3-dispersion-gated-close", family_id="g3-opportunity-state-close",
            title="Dispersion-gated momentum swing trades executed at the close, SPY otherwise",
            economic_mechanism="Pilot 1's dispersion gate helped mainly by trading less; with entries at the cheap "
            "close the remaining question is whether high cross-sectional dispersion still marks states where "
            "selection adds value once friction is small, or whether trading in every state is then as good",
            competing_explanation="High dispersion coincides with market stress, so the gate times market risk rather "
            "than selection opportunity; with cheap execution the ungated book does as well",
            null_hypothesis="The gated close-executed book's log growth is no higher than SPY's at cost-v3-2 and no "
            "higher than the ungated close-executed book's",
            falsification_test="Active log growth versus SPY <= 0, or the ungated ablation does as well",
            negative_controls=("matched-control books", "the ungated ablation"), baseline_ids=BASELINES,
            required_inputs=(DAILY_BARS,), primary_policy=_G3,
            ablations=(PolicySpec("g3-momentum-swing-ungated-close", "momentum_top", "ECX1", SPY_IDLE,
                                  params=dict(_G3_PARAMS), varies_axis="market_filter",
                                  parent_policy=_G3.policy_id),),
            author_type="agent", proposed_at="2026-10-23"),
    ))

PLANS: dict[str, ExperimentPlan] = {p.plan_id: p for p in (ENGINE_SMOKE, PILOT_1, PILOT_2)}
