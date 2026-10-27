"""Exploratory lane `v3x-live-picks-v1`: a prospective track record of the live engine's own picks (picks/README.md).

Research only, PROSPECTIVE_EXPLORATORY, never confirmatory: no orders, no alerts, no Discord, no promotion, no
group-actionable output. The lane has its own series, chain and ledger issue ("APEX forward-shadow ledger ·
v3x-live-picks-v1 · vol 1"); nothing is shared with the frozen lane or the tournament.

Records:
* DECISION (after the close of t, before the next open): the engine's pick or NO PICK with its reason, the qualified
  setups, every plan in units of the close with its ordered control reserve, the picks still open and the scan's funnel
  (discovery.v3.live_picks.decide). Committed before anything after t is known.
* OUTCOME (once t + 22 sessions are final): every plan's label under the live lifecycle's rules, its first K controls,
  and SPY and the sector ETF over the holding sessions (live_picks.label).
The readable track record (picks/ on the `picks-ledger` branch) is rendered from the verified ledger by
`research.forward.picks_view`: the ledger is the record, the view is derived from it and can be rebuilt by anyone.
"""

from __future__ import annotations

import functools
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import ClassVar

import pandas as pd

from apex_worker.discovery.v3 import live_picks as LP
from apex_worker.market.calendar import MarketCalendar, default_calendar
from apex_worker.research.forward import exploratory as X
from apex_worker.research.forward import windows as W
from apex_worker.research.forward.frozen import WINDOW_ANCHOR, WINDOW_ANCHOR_INDEX
from apex_worker.research.forward.recorder import Recorder, RunReport, _Stop
from apex_worker.research.forward.records import DECISION, OUTCOME, Ledger, canonical, clean
from apex_worker.research.forward.source import DecisionData, SourceUnavailable
from apex_worker.research.realeq import universe_v2 as U
from apex_worker.research.training_data import BENCHMARK, SECTOR_ETFS

RECORDER_VERSION = "forward-picks-1"
POLICY = X.LIVE_PICKS
SERIES_ID = POLICY.series_id
LANE = X.LANE
DECISIONS_FROM = date(2026, 11, 2)
FIXTURE_SERIES_ID = "fixture-demo-live-picks"
VIEW_BRANCH = "picks-ledger"
MAX_FAILED_SHARE = 0.10  # more SEC failures than this and the decision waits for a later attempt (SOURCE_PENDING)
# sha256 of the behaviour projection of the golden decisions (golden_fingerprint). Pinned by tests/test_live_picks.py
# and checked by every recording run: a change to the live engine's decisions changes it and stops the series.
PINNED_FINGERPRINT = "ee06432e1955a76dac673d674e063efc5721afd8dfb6ee1526944e1f050dbb7f"
PICKS_READING = {"registered_status_check": None, "first_powered_look": None,
                 "outcome_values": "a descriptive track record, readable at any time; it tests no claim (picks/README.md)"}
FIXTURE_EARNINGS = {"source": "none (synthetic fixture): every stock's earnings status is UNKNOWN"}


# ---- the SEC earnings view -------------------------------------------------------------------------------------------
class SecEarnings:
    """The earnings view of one decision: every U1 stock's SEC submissions fetched now, keeping only the filings EDGAR
    accepted by the decision instant (runtime.earnings_job, as the worker builds its calendar). A release filed after it
    leaves the earlier estimate in place, as in the worker's calendar refreshed the night before."""

    def __init__(self, http, cal: MarketCalendar):
        self.http, self.cal = http, cal

    @classmethod
    def from_env(cls, cal: MarketCalendar, env: dict | None = None) -> SecEarnings | None:
        """None when APEX_SEC_USER_AGENT is missing or unusable (the value is never logged)."""
        from apex_worker.research.realeq.data import RunStop
        from apex_worker.research.sec_history import sec_http, user_agent_from_env

        try:
            return cls(sec_http(user_agent_from_env(env)), cal)
        except RunStop:
            return None

    def view(self, day: date, now) -> tuple[Callable[[str, str, int], str], dict]:
        from apex_worker.runtime.earnings_job import EarningsBook, refresh_earnings, resolve_ciks
        from apex_worker.signals.context import decision_instant

        stocks = list(U.u1_stocks())
        instant = decision_instant(pd.Timestamp(day), self.cal)
        data = refresh_earnings(stocks, resolve_ciks(stocks, self.http), self.http, self.cal, now, as_of=instant)
        failed = list(data.get("failed") or [])
        if len(failed) > MAX_FAILED_SHARE * len(stocks):
            raise SourceUnavailable("SOURCE_PENDING", f"SEC submissions failed for {len(failed)} of {len(stocks)} "
                                    "stocks: the earnings gate cannot run as the worker's does yet")
        book = EarningsBook(self.cal, data, clock=lambda: instant)
        cov = book.coverage()
        return book.status, {"source": "SEC submissions API: 8-K item 2.02 history (runtime.earnings_job)",
                             "as_of": W.iso(instant), "fetched_at": data.get("generated_at"),
                             **{k: cov[k] for k in ("symbols", "with_estimate", "failed", "no_cik")},
                             "failed_symbols": failed[:20], "no_cik_symbols": list(data.get("no_cik") or [])[:20]}


# ---- the series --------------------------------------------------------------------------------------------------------
def series_config(fingerprint: str | None = None) -> dict:
    """Everything that defines the series. A change needs a new major version (a new series id)."""
    return clean(POLICY.config() | {
        "recorder_version": RECORDER_VERSION, "decisions_from": DECISIONS_FROM, "promotion": "none",
        "arms": [LP.PICK, LP.QUALIFIED],
        "timing": "decided after the close of t from bars through t; a plan's entry window is the next 2 sessions",
        "axis": {"window_anchor": WINDOW_ANCHOR, "window_anchor_index": WINDOW_ANCHOR_INDEX,
                 "calendar": "XNYS (exchange_calendars)"},
        "windows": {"finality_buffer_minutes": int(W.FINALITY_BUFFER.total_seconds() // 60),
                    "deadline": "next session open", "finality_lag_sessions": W.FINALITY_LAG,
                    "label_sessions": W.LABEL_SESSIONS},
        "universe": {"id": "U1", "snapshot_sha256": U.snapshot_sha256(),
                     "membership": "the S&P 500 snapshot plus SPY and the 11 sector ETFs; later index changes are not "
                                   "captured",
                     "worker_difference": "the Pi worker scans the 400 most liquid listed names (stocks and ETFs); this "
                                          "lane scans U1, where most of them are"},
        "source": {"provider": "alpaca", "feed": "sip", "adjustment": "split", "timeframe": "1Day"},
        "earnings": {"source": "SEC submissions (8-K item 2.02)", "as_of": "the decision instant", "required": True,
                     "max_failed_share": MAX_FAILED_SHARE},
        "view": {"branch": VIEW_BRANCH, "folder": "picks/", "derived_from": "the verified ledger, never edited by hand"},
        "golden_fingerprint": fingerprint,
    })


def _window(cal: MarketCalendar, day: date) -> dict:
    w = W.window(cal, day)
    return {"close_utc": W.iso(w.close_utc), "earliest_utc": W.iso(w.earliest_utc), "deadline_utc": W.iso(w.deadline_utc),
            "next_session": w.next_session.isoformat(), "early_close": w.early_close}


def earlier_picks(ledger: Ledger) -> list[tuple[str, dict]]:
    return [(r["session"], e) for r in ledger.of_type(DECISION) for e in r["body"].get("plans") or []
            if e.get("kind") == LP.PICK]


@dataclass
class PicksLane:
    series_id: str = SERIES_ID
    lane: str = LANE
    decisions_from: date = DECISIONS_FROM
    data_class: str = "REAL_MARKET_DATA"
    check_inputs: bool = False
    earnings: SecEarnings | None = None
    reading: ClassVar[dict] = PICKS_READING

    @classmethod
    def fixture(cls, decisions_from: date) -> PicksLane:
        return cls(FIXTURE_SERIES_ID, "FIXTURE_DEMO", decisions_from, "SYNTHETIC_FIXTURE_DEMO")

    def config(self, fingerprint: str) -> dict:
        return series_config(fingerprint) | {"series_id": self.series_id, "lane": self.lane,
                                             "decisions_from": self.decisions_from.isoformat(),
                                             "data_class": self.data_class}

    def decide(self, dd: DecisionData, day: date, ledger: Ledger, cal: MarketCalendar) -> dict:
        if self.data_class == "REAL_MARKET_DATA" and self.earnings is None:
            raise SourceUnavailable("SOURCE_INVALID", "the SEC contact is missing or unusable, so the earnings gate "
                                    "cannot run as the worker's does: set the repository secret APEX_SEC_USER_AGENT "
                                    "(a name and an email)")
        if self.earnings is not None:
            status, emeta = self.earnings.view(day, pd.Timestamp.now(tz="UTC").to_pydatetime())
        else:
            status, emeta = None, FIXTURE_EARNINGS
        body = LP.decide(dd.bars, day, earlier_picks(ledger), status, cal)
        return X.label_body(body | {"earnings": emeta, "source": dd.meta, "window": _window(cal, day)})


# ---- golden behaviour fingerprint -------------------------------------------------------------------------------------
GOLDEN = {"per_sector": 3, "first": date(2026, 9, 2), "last": date(2026, 9, 16)}


@functools.cache
def _golden_frames() -> dict[str, pd.DataFrame]:
    from apex_worker.research.realeq.fixture_v2 import universe_frames_v2

    return universe_frames_v2(per_sector=GOLDEN["per_sector"])


@functools.cache
def golden_decisions() -> tuple[dict, ...]:
    """The lane's decisions on the synthetic fixture over the golden sessions, each from bars through its session
    (computed once per process; callers must not mutate them)."""
    cal = default_calendar()
    frames = _golden_frames()
    earlier: list[tuple[str, dict]] = []
    out = []
    for d in cal.sessions_between(GOLDEN["first"], GOLDEN["last"]):
        body = LP.decide(frames, d, earlier, None, cal)
        earlier += [(d.isoformat(), e) for e in body["plans"] if e["kind"] == LP.PICK]
        out.append(body)
    return tuple(out)


def _behaviour(body: dict) -> dict:
    """What the fingerprint pins: picks, plans, controls, rejections and the funnel, rounded so that floating-point
    noise between machines cannot move it while any change of behaviour does."""
    def rnd(x, n):
        return None if x is None else round(float(x), n)

    plans = [{"kind": e["kind"], "symbol": e["symbol"], "strategy": e["strategy_id"], "quality": rnd(e["quality"], 1),
              "plan": {k: rnd(e["plan"][k], 4) for k in ("entry_min", "entry_max", "stop", "target")},
              "stop_atr": rnd(e["plan"]["stop_atr"], 3), "stratum": e["controls"]["stratum"],
              "reserve": [[c[0], *(rnd(v, 4) for v in c[1:])] for c in e["controls"]["reserve"]]}
             for e in body["plans"]]
    return {"plans": plans, "no_trade_reason": body["no_trade_reason"],
            "candidates": [[c["symbol"], c["strategy_id"], c["outcome"], c["reasons"]] for c in body["candidates"]],
            "open": [[o["symbol"], o["status_at_scan"], o["status_t"]] for o in body["open_picks"]],
            "funnel": {k: body["scan"][k] for k in ("eligible", "triggered", "vetoes")}}


def golden_fingerprint() -> str:
    return hashlib.sha256(canonical(clean([_behaviour(b) for b in golden_decisions()]))).hexdigest()


# ---- the recorder ---------------------------------------------------------------------------------------------------
class PicksRecorder(Recorder):
    """The forward-shadow recorder with this lane's input checks and outcomes (decisions are the base class's)."""

    def __init__(self, lane: PicksLane, *args, **kw):
        kw.setdefault("fingerprint_fn", golden_fingerprint)
        kw.setdefault("expected_fingerprint", PINNED_FINGERPRINT)
        super().__init__(lane, *args, **kw)

    def _check_inputs(self, rep: RunReport) -> dict:
        try:  # the registry's ids are unique, and this lane's id is none of the others' nor the frozen lane's
            X.assert_separate([self.lane.series_id, *(p.series_id for p in X.EXPLORATORY_POLICIES
                                                      if p.series_id != self.lane.series_id)])
        except ValueError as exc:
            rep.flag("INTEGRITY", f"SERIES_INVALID: {exc}")
            raise _Stop from None
        return {"policy_config_sha256": POLICY.config_sha256()}

    def _outcomes(self, ledger: Ledger, rep: RunReport, anchors: dict) -> None:
        data_end = W.outcome_data_end(self.cal, self.clock())
        done = {r["session"] for r in ledger.of_type(OUTCOME)}
        due = [d for d in ledger.of_type(DECISION) if d["session"] not in done
               and W.is_mature(self.cal, date.fromisoformat(d["session"]), data_end)]
        if not due:
            return
        symbols = {BENCHMARK, *SECTOR_ETFS.values()}
        for d in due:
            for e in d["body"].get("plans") or []:
                symbols |= {e["symbol"], *(c[0] for c in (e.get("controls") or {}).get("reserve") or [])}
        try:
            ob = self.source.outcome_data(date.fromisoformat(due[0]["session"]), data_end, self.clock(),
                                          tuple(sorted(symbols)))
        except SourceUnavailable as exc:
            rep.flag("SOURCE_PENDING" if exc.status in ("SOURCE_PENDING", "RATE_LIMITED") else "SOURCE_INVALID",
                     f"outcomes deferred: {exc.status}: {exc.reason}")
            return
        for d in due:
            body = LP.label(d["body"], d["session"], ob.sessions, ob.bars, ob.quarantine)
            waited = W.sessions_after(self.cal, date.fromisoformat(d["session"]), data_end)
            open_ = [p["symbol"] for p in body["plans"] if p["outcome"] in ("NO_DATA", "DATA_END")]
            if open_ and waited < W.LABEL_SESSIONS + W.EXTRA_SESSIONS:
                rep.messages.append(f"outcome {d['session']} waits: no final data yet for {', '.join(open_)}")
                break  # later decisions wait too, so outcomes stay in session order; a gap is labelled as such later
            self._append(ledger, rep, anchors, OUTCOME, d["session"], X.label_body(body | {
                "decision": {"record_id": d["record_id"], "digest": d["digest"], "session": d["session"]},
                "data_end": data_end.isoformat(), "sessions_after_decision": waited, "source": ob.meta}))


def summary_md(records: list[dict]) -> str:
    """The latest decision and outcome, for the job summary (the full track record is the rendered view)."""
    decisions = [r for r in records if r["record_type"] == DECISION]
    outcomes = [r for r in records if r["record_type"] == OUTCOME]
    lines = ["", "### Live picks (research only; an unvalidated engine's would-be picks, not advice)", ""]
    if decisions:
        d = decisions[-1]
        lines.append(f"Latest decision {d['session']}: {d['body']['summary']}")
    if outcomes:
        o = outcomes[-1]
        lines.append(f"\nLatest outcome {o['session']}: {o['body']['summary']}")
    picks = sum(1 for r in decisions for e in r["body"].get("plans") or [] if e.get("kind") == LP.PICK)
    lines.append(f"\n{len(decisions)} decisions, {picks} picks, {len(outcomes)} outcomes recorded.")
    return "\n".join(lines) + "\n"


__all__ = ["DECISIONS_FROM", "FIXTURE_SERIES_ID", "PINNED_FINGERPRINT", "POLICY", "SERIES_ID", "VIEW_BRANCH",
           "PicksLane", "PicksRecorder", "SecEarnings", "earlier_picks", "golden_decisions", "golden_fingerprint",
           "series_config", "summary_md"]
