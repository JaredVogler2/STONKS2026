"""Point-in-time context joined onto a symbol's feature frame: regime probabilities and catalyst summaries.

A session's decision instant is its regular close + DECISION_LAG (default 15 minutes). Catalysts are
counted for a session only if available_at <= that decision instant, so an after-hours release can
only influence the NEXT session's decision.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from apex_worker.features.panel import FeaturePanel
from apex_worker.features.regime import REGIME_STATES
from apex_worker.market.calendar import MarketCalendar, default_calendar
from apex_worker.timeutil import parse_utc

DECISION_LAG = timedelta(minutes=15)
POSITIVE = "potentially_positive"
NEGATIVE = "potentially_negative"


def decision_instant(session_day, cal: MarketCalendar | None = None) -> datetime:
    cal = cal or default_calendar()
    day = session_day.date() if hasattr(session_day, "date") else session_day
    return cal.session(day).close_utc + DECISION_LAG


def catalyst_daily(catalysts: Iterable[dict], index: pd.DatetimeIndex, cal: MarketCalendar | None = None) -> pd.DataFrame:
    """Per-session catalyst summaries using only items available at each session's decision instant."""
    cal = cal or default_calendar()
    cols = ["cat_count_5d", "cat_positive_recent_2d", "cat_positive_recent_5d", "cat_negative_recent_5d",
            "cat_impact_max_5d"]
    out = pd.DataFrame(0.0, index=index, columns=cols)
    out["cat_impact_max_5d"] = np.nan
    if len(index) == 0:
        return out
    decisions = np.array([np.datetime64(decision_instant(d, cal).replace(tzinfo=None), "us") for d in index],
                         dtype="datetime64[us]")
    for cat in catalysts:
        avail = np.datetime64(parse_utc(cat["available_at"]).replace(tzinfo=None), "us")
        first = int(np.searchsorted(decisions, avail, side="left"))  # first session whose decision sees it
        if first >= len(index):
            continue
        direction = cat.get("direction", "unknown")
        impact = cat.get("impact_score")
        for k in range(first, min(first + 5, len(index))):
            out.iat[k, 0] += 1
            if direction == POSITIVE:
                out.iat[k, 2] += 1
                if k - first < 2:
                    out.iat[k, 1] += 1
            elif direction == NEGATIVE:
                out.iat[k, 3] += 1
            if impact is not None:
                cur = out.iat[k, 4]
                out.iat[k, 4] = impact if np.isnan(cur) else max(cur, impact)
    return out


def context_frame(panel: FeaturePanel, symbol: str, catalysts: Iterable[dict] = (),
                  cal: MarketCalendar | None = None) -> pd.DataFrame:
    f = panel.features[symbol].astype(float).copy()
    reg = panel.regime.reindex(f.index)
    for s in REGIME_STATES:
        f[f"regime_p_{s}"] = reg[s]
    f["regime_label"] = reg["label"]
    f["regime_breadth"] = panel.breadth["breadth_above_sma50"].reindex(f.index)
    f["squeeze_prev"] = f["squeeze_pct_120"].shift(1)
    cats = [c for c in catalysts if c.get("symbol") == symbol]
    daily = catalyst_daily(cats, pd.DatetimeIndex(f.index), cal)
    for col in daily.columns:
        f[col] = daily[col]
    return f
