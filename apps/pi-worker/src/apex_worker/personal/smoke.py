"""An offline rehearsal of the personal alerts: an in-memory channel, synthetic prices (never market data) and a scripted
owner, from `start` through a month-end rebalance. `apex personal smoke` prints the conversation."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd

from apex_worker.market.calendar import MarketCalendar, default_calendar
from apex_worker.personal.discord_rest import MemoryChannel
from apex_worker.personal.market import Request, Snapshot
from apex_worker.personal.method import SECTOR_MOM_3
from apex_worker.personal.records import MARK, decode_all
from apex_worker.personal.runner import Config, Runner
from apex_worker.research.training_data import BENCHMARK
from apex_worker.timeutil import ET

SYMBOLS = (BENCHMARK, *SECTOR_MOM_3.universe)
EXTRA = ("SSO", "UPRO", "EFA", "AGG", "BIL")  # the single-asset methods' funds, derived from SPY's synthetic path


def synthetic_bars(cal: MarketCalendar, start: date, end: date, seed: int = 7) -> dict[str, pd.DataFrame]:
    """Deterministic random walks with different drifts (SYNTHETIC: no relation to any real price)."""
    days = cal.sessions_between(start, end)
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    rng = np.random.default_rng(seed)
    out = {}
    for i, sym in enumerate(SYMBOLS):
        drift = 0.0004 + 0.00025 * ((i * 7) % 11 - 5)
        r = drift + 0.011 * rng.standard_normal(len(days))
        close = (40.0 + 23.0 * i) * np.exp(np.cumsum(r))
        prev = np.concatenate([[close[0]], close[:-1]])
        opn = prev * (1 + 0.002 * rng.standard_normal(len(days)))
        hi = np.maximum(opn, close) * (1 + np.abs(0.004 * rng.standard_normal(len(days))))
        lo = np.minimum(opn, close) * (1 - np.abs(0.004 * rng.standard_normal(len(days))))
        out[sym] = pd.DataFrame({"open": opn, "high": hi, "low": lo, "close": close}, index=idx)
    extra = np.random.default_rng(seed + 1)  # a separate stream: the series above stay as they were
    spy = np.diff(np.log(out[BENCHMARK]["close"].to_numpy()), prepend=np.log(out[BENCHMARK]["close"].iloc[0]))
    rets = {"SSO": 2 * spy, "UPRO": 3 * spy, "EFA": 0.8 * spy + 0.006 * extra.standard_normal(len(days)),
            "AGG": 0.00008 + 0.003 * extra.standard_normal(len(days)), "BIL": np.full(len(days), 0.00012)}
    for i, sym in enumerate(EXTRA):
        close = (60.0 + 15.0 * i) * np.exp(np.cumsum(rets[sym]))
        opn = np.concatenate([[close[0]], close[:-1]])
        out[sym] = pd.DataFrame({"open": opn, "high": np.maximum(opn, close) * 1.002,
                                 "low": np.minimum(opn, close) * 0.998, "close": close}, index=idx)
    return out


class SyntheticMarket:
    """A Request -> Snapshot source over synthetic bars that never shows a bar before it would be published (15
    minutes after the close) nor a trade after `now`."""

    def __init__(self, cal: MarketCalendar, clock, bars: dict[str, pd.DataFrame] | None = None):
        self.cal, self.clock = cal, clock
        self.bars = bars or synthetic_bars(cal, date(2025, 6, 2), date(2027, 6, 30))
        self.requests: list[Request] = []
        self.actions: list[dict] | None = []

    def __call__(self, req: Request) -> Snapshot:
        self.requests.append(req)
        now = self.clock()
        last = self.cal.latest_completed_session(now - timedelta(minutes=15))
        bars = {s: df[(df.index >= pd.Timestamp(req.start)) & (df.index <= pd.Timestamp(min(last, req.end)))]
                for s, df in self.bars.items() if s in req.daily}
        latest = {}
        today = now.astimezone(ET).date()
        for s in req.latest:
            if s in self.bars:
                df = self.bars[s]
                if self.cal.is_open(now) and pd.Timestamp(today) in df.index:
                    latest[s] = (float(df.at[pd.Timestamp(today), "open"]), now)  # "intraday": the day's open
                elif len(bars.get(s, ())):
                    latest[s] = (float(bars[s]["close"].iloc[-1]), now)
        return Snapshot(bars, latest, self.actions, now)


def _fills(ch: MemoryChannel, market: SyntheticMarket, when: datetime) -> list[str]:
    """The owner's reports for the latest alert, at that day's open (whole shares as alerted)."""
    alert = [r for m in ch.messages if m["author"]["id"] == ch.bot_id for r in decode_all(m["content"])
             if r["k"] == "alert"][-1]
    day = pd.Timestamp(when.astimezone(ET).date())
    out = []
    for o in alert["orders"]:
        px = round(float(market.bars[o["s"]].at[day, "open"]), 2)
        out.append(f"{'sold' if o['a'] in ('EXIT', 'TRIM') else 'bought'} {o['n']} {o['s']} at {px}")
    return out


def rehearse() -> list[str]:
    cal = default_calendar()
    ch = MemoryChannel(start=datetime(2026, 10, 27, 18, 0, tzinfo=UTC))
    market = SyntheticMarket(cal, lambda: ch.clock)
    cfg = Config("smoke-channel")

    def run(at: datetime) -> None:
        ch.clock = at
        Runner(cfg, ch, market, now=at, cal=cal).run()

    run(datetime(2026, 10, 27, 18, 0, tzinfo=UTC))  # first contact: the welcome
    ch.post_as_owner("start 1000", datetime(2026, 10, 27, 18, 5, tzinfo=UTC))
    run(datetime(2026, 10, 27, 18, 7, tzinfo=UTC))  # the start, then the starting allocation (Mon 10/26 closes)
    t = datetime(2026, 10, 27, 18, 30, tzinfo=UTC)
    for text in _fills(ch, market, t):
        ch.post_as_owner(text, t)
        t += timedelta(minutes=1)
    run(datetime(2026, 10, 27, 19, 7, tzinfo=UTC))
    run(datetime(2026, 10, 27, 22, 7, tzinfo=UTC))  # the Tue 10/27 summary
    ch.post_as_owner("status", datetime(2026, 10, 28, 15, 0, tzinfo=UTC))
    run(datetime(2026, 10, 28, 15, 7, tzinfo=UTC))
    run(datetime(2026, 10, 30, 22, 7, tzinfo=UTC))  # Fri 10/30: summary and the month-end decision
    run(datetime(2026, 11, 2, 12, 37, tzinfo=UTC))  # Mon 11/2 before the open: the reminder
    t = datetime(2026, 11, 2, 14, 45, tzinfo=UTC)
    for text in _fills(ch, market, t)[:1]:
        ch.post_as_owner(text, t)
    ch.post_as_owner("deposit 250", datetime(2026, 11, 2, 15, 0, tzinfo=UTC))
    ch.post_as_owner("undo", datetime(2026, 11, 2, 15, 1, tzinfo=UTC))
    ch.post_as_owner("skip all", datetime(2026, 11, 2, 15, 2, tzinfo=UTC))
    ch.post_as_owner("trades", datetime(2026, 11, 2, 15, 3, tzinfo=UTC))
    run(datetime(2026, 11, 2, 15, 7, tzinfo=UTC))
    lines = ["SYNTHETIC REHEARSAL: in-memory channel, made-up prices (not market data, not advice)", ""]
    for m in ch.messages:
        who = "bot" if m["author"]["id"] == ch.bot_id else "owner"
        at = datetime.fromisoformat(m["timestamp"]).astimezone(ET)
        body = "\n".join(ln for ln in m["content"].splitlines() if not ln.startswith(MARK))
        lines.append(f"[{at:%a %m/%d %I:%M %p} ET] {who}: {body}")
    return lines


__all__ = ["EXTRA", "SYMBOLS", "SyntheticMarket", "rehearse", "synthetic_bars"]
