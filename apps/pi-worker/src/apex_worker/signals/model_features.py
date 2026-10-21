"""The ONE assembler of a model's input vector, shared by training rows and live signals (APEX-001).

`research/dataset.py` (training events) and `signals/policy.py` (live forecasts) both call `assemble`, and both take the
row-level inputs unrounded from the same `context_frame` row via `row_inputs`. A published model therefore never sees a
different vector than the one it was trained on: no silently missing column (live `squeeze_prev` used to be None and
was imputed to the training mean), no display rounding. `tests/test_feature_parity.py` compares both paths for every
model input.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from apex_worker.signals.levels import Levels

# context-frame columns a model reads directly (atr_14 only feeds stop_dist_atr)
ROW_INPUTS = (
    "atr_14",
    "atr_pct_14",
    "rs_bench_20",
    "rel_volume_1",
    "squeeze_prev",
    "dist_sma20_atr",
    "regime_p_trending_up",
    "regime_p_risk_off",
)
QUALITY_GROUPS = ("price", "sector", "volume", "volatility", "regime", "catalyst")


def row_inputs(row: Mapping) -> dict[str, float | None]:
    """Unrounded row-level inputs; missing or NaN become None (the model imputes both to the training mean)."""
    out: dict[str, float | None] = {}
    for k in ROW_INPUTS:
        v = row.get(k)
        try:
            v = None if v is None else float(v)
        except (TypeError, ValueError):
            v = None
        out[k] = None if v is None or math.isnan(v) else v
    return out


def assemble(
    inputs: Mapping[str, float | None],
    setup_score: float | None,
    quality_score: float | None,
    groups: Mapping[str, float | None],
    lv: Levels,
    earnings_unknown: bool,
) -> dict:
    atr = inputs.get("atr_14") or 0.0
    return {
        "setup_score": setup_score,
        "quality": quality_score,
        **{f"g_{k}": groups.get(k) for k in QUALITY_GROUPS},
        "atr_pct_14": inputs.get("atr_pct_14"),
        "rs_bench_20": inputs.get("rs_bench_20"),
        "rel_volume_1": inputs.get("rel_volume_1"),
        "squeeze_prev": inputs.get("squeeze_prev"),
        "dist_sma20_atr": inputs.get("dist_sma20_atr"),
        "regime_p_trending_up": inputs.get("regime_p_trending_up"),
        "regime_p_risk_off": inputs.get("regime_p_risk_off"),
        "rr_net": lv.reward_to_risk_net,
        "stop_dist_atr": (lv.mid - lv.stop) / atr if atr > 0 else None,
        "earnings_unknown": 1.0 if earnings_unknown else 0.0,
    }
