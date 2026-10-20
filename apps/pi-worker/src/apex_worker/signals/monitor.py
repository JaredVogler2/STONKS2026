"""Published-signal lifecycle monitoring.

End of day (authoritative): the signal's own levels are replayed through research.execution.simulate on
completed daily bars after the decision session — the SAME conservative rules as the backtests — giving
ACTIVATED / EXPIRED / INVALIDATED / CLOSED(TARGET|STOP|TIME) transitions with deterministic dedupe keys.
Intraday (advisory): fresh VERIFIED prints that touch the stop/target create annotations that drive urgent
private member updates; they do not decide the official outcome (fills are never assumed).
Stale or missing data produces STALE_DATA annotations and never creates a fresh alert.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from apex_worker.market.calendar import MarketCalendar, default_calendar
from apex_worker.market.quote_cache import CachedQuote
from apex_worker.research.execution import ExecConfig, simulate
from apex_worker.signals.lifecycle import SignalEventRecord, SignalState, annotate, outcome_transition, transition
from apex_worker.timeutil import parse_utc

TIME_WARNING_SESSIONS = 3


def _levels(signal: dict) -> tuple[float, float, float, float]:
    lv = signal["levels"]
    return float(lv["entry_min"]), float(lv["entry_max"]), float(lv["stop_level"]), float(lv["target_primary"])


def evaluate_daily(
    signal: dict, state: str, bars: pd.DataFrame, now: datetime, cal: MarketCalendar | None = None
) -> list[SignalEventRecord]:
    cal = cal or default_calendar()
    st = SignalState(state)
    if st not in (SignalState.PUBLISHED, SignalState.ACTIVE):
        return []
    sid = signal["id"]
    decision_day = cal.latest_completed_session(parse_utc(signal["asof_at"]))
    last_day = cal.latest_completed_session(now)
    df = bars.loc[: pd.Timestamp(last_day)]
    if pd.Timestamp(decision_day) not in df.index:
        return []
    t = df.index.get_loc(pd.Timestamp(decision_day))
    emin, emax, stop, target = _levels(signal)
    cfg = ExecConfig(
        cost_bps_per_side=float(signal["risk"]["cost_bps_per_side"]),
        entry_valid_sessions=int(signal["horizon"]["entry_valid_sessions"]),
        horizon_sessions=int(signal["horizon"]["max_sessions"]),
    )
    o, h, low, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    res = simulate(o, h, low, c, t, emin, emax, stop, target, cfg)
    events: list[SignalEventRecord] = []

    def close_at(i: int) -> datetime:
        return cal.session(df.index[i].date()).close_utc

    if not res.filled:
        sessions_after = len(df) - 1 - t
        if sessions_after < cfg.entry_valid_sessions:
            return []  # entry window still open
        window = slice(t + 1, t + 1 + cfg.entry_valid_sessions)
        i_end = min(len(df) - 1, t + cfg.entry_valid_sessions)
        if np.nanmin(low[window]) <= stop:
            cause = "price traded through the invalidation level before any entry"
            outcome = "THESIS_INVALIDATED"
        elif np.nanmin(o[window]) >= target:
            cause, outcome = "upside consumed: opened above target before entry (do not chase)", "ENTRY_EXPIRED"
        else:
            cause, outcome = (
                f"no fill inside the entry zone within {cfg.entry_valid_sessions} sessions",
                "ENTRY_EXPIRED",
            )
        events.append(
            outcome_transition(
                sid,
                st,
                outcome,
                at=close_at(i_end),
                cause=cause,
                evidence_asof_at=close_at(i_end),
                payload={"theoretical_entry": None},
            )
        )
        return events
    cur = st
    if cur is SignalState.PUBLISHED:
        ev = transition(
            sid,
            cur,
            SignalState.ACTIVE,
            at=close_at(res.entry_index),
            cause="price traded inside the entry zone (theoretical entry; members' own fills are tracked separately)",
            discriminator="ENTRY",
            evidence_asof_at=close_at(res.entry_index),
            payload={
                "theoretical_entry": round(res.entry_price, 4),
                "entry_session": str(df.index[res.entry_index].date()),
            },
        )
        events.append(ev)
        cur = SignalState.ACTIVE
    if res.outcome in ("TARGET", "STOP", "TIME"):
        cause = {
            "TARGET": "target reached (limit at target; not a guaranteed fill)",
            "STOP": "stop/invalidation reached"
            + (" via overnight gap - exit modeled at the open" if res.gap_through_stop else "")
            + (" (same bar touched target: STOP-first rule)" if res.ambiguous_same_bar else ""),
            "TIME": f"maximum horizon of {cfg.horizon_sessions} sessions reached",
        }[res.outcome]
        events.append(
            outcome_transition(
                sid,
                cur,
                res.outcome,
                at=close_at(res.exit_index),
                cause=cause,
                evidence_asof_at=close_at(res.exit_index),
                payload={
                    "exit_price_modeled": round(res.exit_price, 4),
                    "return_net_modeled": res.return_net,
                    "r_multiple_modeled": res.r_multiple,
                    "sessions_held": res.sessions_held,
                    "ambiguous_same_bar": res.ambiguous_same_bar,
                    "gap_through_stop": res.gap_through_stop,
                },
            )
        )
        return events
    held = len(df) - 1 - res.entry_index
    remaining = cfg.horizon_sessions - held
    if 0 < remaining <= TIME_WARNING_SESSIONS:
        events.append(
            annotate(
                sid,
                cur,
                "TIME_WARNING",
                at=close_at(len(df) - 1),
                cause=f"{remaining} trading session(s) left before the time exit",
                discriminator=f"remaining-{remaining}",
                payload={"sessions_remaining": remaining},
            )
        )
    return events


def evaluate_intraday(
    signal: dict, state: str, quote: CachedQuote | None, now: datetime, fresh_sla_seconds: int, market_open: bool
) -> list[SignalEventRecord]:
    if state != SignalState.ACTIVE.value:
        return []
    sid = signal["id"]
    bucket = now.strftime("%Y%m%d%H")
    if quote is None or (market_open and quote.age_seconds(now) > fresh_sla_seconds):
        if market_open:
            return [
                annotate(
                    sid,
                    SignalState.ACTIVE,
                    "STALE_DATA",
                    at=now,
                    cause="no fresh verified price; status is stale and no price-based alert will be sent",
                    discriminator=f"stale-{bucket}",
                )
            ]
        return []
    if not quote.validation.usable_for_exit_alerts:
        return []
    _, _, stop, target = _levels(signal)
    day = now.strftime("%Y%m%d")
    if quote.price <= stop:
        return [
            annotate(
                sid,
                SignalState.ACTIVE,
                "INTRADAY_STOP_TOUCH",
                at=now,
                cause="verified print at or below the "
                f"invalidation level ({quote.feed.upper()} single-venue data; check your broker)",
                discriminator=f"stop-{day}",
                evidence_asof_at=quote.ts_exchange,
                payload={"price": quote.price, "feed": quote.feed},
            )
        ]
    if quote.price >= target:
        return [
            annotate(
                sid,
                SignalState.ACTIVE,
                "INTRADAY_TARGET_TOUCH",
                at=now,
                cause="verified print at or above the target; a limit order may or may not have filled",
                discriminator=f"target-{day}",
                evidence_asof_at=quote.ts_exchange,
                payload={"price": quote.price, "feed": quote.feed},
            )
        ]
    return []
