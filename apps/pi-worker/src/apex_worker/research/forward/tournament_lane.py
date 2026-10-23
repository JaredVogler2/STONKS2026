"""Exploratory lanes of the prospective evidence tournament, one series per cohort: `v3x-epa-tournament-v1` (cohort 0,
docs/evidence/TOURNAMENT_LANE.md) and `v3x-epa-tournament-c1-v1` (cohort 1, docs/evidence/TOURNAMENT_COHORT_1.md).

Research only: paper books, no orders, no alerts, no group-actionable output. Labelled PROSPECTIVE_EXPLORATORY and never
confirmatory. Each cohort has its own series, chain and ledger issue: nothing is shared with the frozen lane or between
cohorts, except one market-data fetch per scheduled pass (`SharedSource`). Every cohort's prior masses come from one
budget of 1 (`assert_mass_budget`), so claims stay family-wise valid across cohorts.

Records:
* DECISION (after the close of t, before the next open): each book's target weights decided from data through t (None:
  hold), traded at the close of t + 1. Committed before anything after t is known.
* OUTCOME (once t is final, after the 2-session lag): the daily net returns of every book and of SPY for the sessions
  since the previous outcome, computed by replaying the committed targets on final bars, and the tournament state
  (evidence, allocation, claims) after them. The state is a pure function of the posted returns: anyone can replay it.
Returns: close to close on split-adjusted SIP bars, the same data as every published V3 result. Dividends are excluded
for SPY and for every book (price returns). Trading at the close costs 2 bps per side for stocks and 5 for SPY
(cost-v3-2). A held stock without a bar that session earns nothing that day; a quarantined (symbol, session) earns nothing.
"""

from __future__ import annotations

import functools
import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import ClassVar

import pandas as pd

from apex_worker.discovery.v3 import tournament_books as TB
from apex_worker.discovery.v3 import tournament_books_c1 as TB1
from apex_worker.evidence.tournament import CLAIM_RULE, Tournament
from apex_worker.market.calendar import MarketCalendar
from apex_worker.research.forward import exploratory as X
from apex_worker.research.forward import windows as W
from apex_worker.research.forward.frozen import WINDOW_ANCHOR, WINDOW_ANCHOR_INDEX, config_sha256
from apex_worker.research.forward.health import ANYTIME_READING
from apex_worker.research.forward.recorder import Recorder, RunReport, _Stop
from apex_worker.research.forward.records import DECISION, OUTCOME, Ledger, canonical, clean
from apex_worker.research.forward.source import DecisionData, SourceUnavailable
from apex_worker.research.realeq import universe_v2 as U
from apex_worker.research.training_data import BENCHMARK

RECORDER_VERSION = "forward-tournament-1"
DECISIONS_FROM = date(2026, 10, 26)
COSTS_BPS = TB.COSTS_BPS
TOURNAMENT = TB.TOURNAMENT


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    book: str
    twin: str | None
    prior_mass: float
    loss_bound: float
    twin_loss_bound: float | None
    mechanism: str
    claims: tuple[str, ...] | None = None  # its claim tests (None: every test it has)

    def __post_init__(self) -> None:
        if self.claims is not None:
            object.__setattr__(self, "claims", tuple(self.claims))


CANDIDATES: tuple[Candidate, ...] = tuple(Candidate(**c) for c in TB.CANDIDATES)
POLICY = X.EPA_TOURNAMENT
SERIES_ID = POLICY.series_id
LANE = X.LANE
# sha256 of each cohort's golden decisions on the fixture (golden_decisions). Pinned by tests/test_tournament_lane.py and
# checked by every recording run: a behaviour change in the decision logic changes it and stops the series.
PINNED_FINGERPRINT = "f6064b1f00c660d26117cd5c9f8e840d64a44d12bb9e8591ab8f658a5fa505fa"
PINNED_FINGERPRINT_C1 = "09e110d6889112cca1bc669cb848260fb80c417328f4bff701de446dadec567e"


@dataclass(frozen=True)
class Cohort:
    """One cohort of the tournament: its own series, books, slate, settings and decision logic."""

    key: str
    policy: X.ExploratoryPolicy
    books: dict
    candidates: tuple[Candidate, ...]
    tournament: dict
    decisions_from: date
    decide: Callable[..., dict]
    pinned_fingerprint: str
    extra_config: dict = field(default_factory=dict)  # cohort-specific series configuration (none for cohort 0)
    file_suffix: str = ""  # its files in a scheduled pass: outbox{suffix}.json, report{suffix}.json
    fixture_series_id: str = "fixture-demo-epa-tournament"

    @property
    def series_id(self) -> str:
        return self.policy.series_id

    @property
    def prior_mass(self) -> float:
        return sum(c.prior_mass for c in self.candidates)


COHORT_0 = Cohort("c0", POLICY, TB.BOOKS, CANDIDATES, TB.TOURNAMENT, DECISIONS_FROM, TB.decide, PINNED_FINGERPRINT)
_C1_CANDIDATES = tuple(Candidate(**c) for c in TB1.CANDIDATES)
COHORT_1 = Cohort(
    "c1", X.EPA_TOURNAMENT_C1, TB1.BOOKS, _C1_CANDIDATES, TB1.TOURNAMENT, DECISIONS_FROM, TB1.decide,
    PINNED_FINGERPRINT_C1,
    extra_config={"cohort": {
        "index": 1, "prior_mass": round(sum(c.prior_mass for c in _C1_CANDIDATES), 12),
        "earlier_cohorts": [{"series_id": COHORT_0.series_id, "prior_mass": round(COHORT_0.prior_mass, 12)}],
        "mass_used_after": round(COHORT_0.prior_mass + sum(c.prior_mass for c in _C1_CANDIDATES), 12),
        "budget": "every cohort's prior masses sum to at most 1, so P(any false claim in any cohort, ever) <= alpha",
        "claims": "against the twin only; the tests against SPY drive the allocation",
        "designs_measured_by": TB1.NEUTRALIZATION["measured_by"]}},
    file_suffix="-c1", fixture_series_id="fixture-demo-epa-tournament-c1")
COHORTS: tuple[Cohort, ...] = (COHORT_0, COHORT_1)
LANES = {"tournament": COHORT_0, "tournament-c1": COHORT_1}  # CLI --lane names


def assert_mass_budget(cohorts: tuple[Cohort, ...] = COHORTS) -> float:
    """The union bound behind every claim needs the prior masses of every cohort ever admitted to sum to at most 1."""
    total = sum(c.prior_mass for c in cohorts)
    ids = [c.series_id for c in cohorts]
    X.assert_separate(ids)
    if total > 1 + 1e-12:
        raise ValueError(f"the cohorts' prior masses sum to {total} > 1")
    return total


def series_config(fingerprint: str | None = None, cohort: Cohort | None = None) -> dict:
    """Everything that defines a cohort's series. A change needs a new major version (a new series id)."""
    c = cohort or COHORT_0
    return clean(c.policy.config() | {
        "recorder_version": RECORDER_VERSION, "decisions_from": c.decisions_from, "promotion": "none",
        "arms": [x.candidate_id for x in c.candidates],
        "timing": "targets decided at the close of t from data through t; traded at the close of t + 1",
        "evaluation": "daily close-to-close net returns from the session after the first decision; books start in SPY",
        "axis": {"window_anchor": WINDOW_ANCHOR, "window_anchor_index": WINDOW_ANCHOR_INDEX,
                 "calendar": "XNYS (exchange_calendars)"},
        "windows": {"finality_buffer_minutes": int(W.FINALITY_BUFFER.total_seconds() // 60),
                    "deadline": "next session open", "finality_lag_sessions": W.FINALITY_LAG},
        "universe": {"id": "U1", "snapshot_sha256": U.snapshot_sha256(), "membership": "date_added <= t",
                     "limitation": "current S&P 500 survivors; prospectively, later index changes are not captured"},
        "source": {"provider": "alpaca", "feed": "sip", "adjustment": "split", "timeframe": "1Day",
                   "dividends": "excluded for every book and SPY (price returns)"},
        "golden_fingerprint": fingerprint,
    } | c.extra_config)


def _last_targets(decisions: list[dict], books: dict | None = None) -> dict[str, dict | None]:
    last: dict[str, dict | None] = dict.fromkeys(TB.BOOKS if books is None else books)
    for d in decisions:
        for book, tgt in (d["body"].get("books") or {}).items():
            if tgt is not None:
                last[book] = tgt
    return last


@dataclass
class TournamentLane:
    series_id: str = SERIES_ID
    lane: str = LANE
    decisions_from: date = DECISIONS_FROM
    data_class: str = "REAL_MARKET_DATA"
    check_inputs: bool = False
    cohort: Cohort = COHORT_0
    reading: ClassVar[dict] = ANYTIME_READING  # anytime-valid evidence: no registered look

    @classmethod
    def for_cohort(cls, cohort: Cohort) -> TournamentLane:
        return cls(cohort.series_id, LANE, cohort.decisions_from, cohort=cohort)

    @classmethod
    def fixture(cls, decisions_from: date | None = None, cohort: Cohort | None = None) -> TournamentLane:
        c = cohort or COHORT_0
        return cls(c.fixture_series_id, "FIXTURE_DEMO", decisions_from or c.decisions_from, "SYNTHETIC_FIXTURE_DEMO",
                   cohort=c)

    def config(self, fingerprint: str) -> dict:
        return series_config(fingerprint, self.cohort) | {"series_id": self.series_id, "lane": self.lane,
                                             "decisions_from": self.decisions_from.isoformat(),
                                             "data_class": self.data_class}

    def decide(self, dd: DecisionData, day: date, ledger: Ledger, cal: MarketCalendar) -> dict:
        pre = TB.light_panel(dd.bars)
        if W.session_index(cal, pre.sessions[0].date()) != WINDOW_ANCHOR_INDEX or pre.sessions[-1].date() != day:
            raise SourceUnavailable("SOURCE_INVALID", f"window {pre.sessions[0].date()}..{pre.sessions[-1].date()} is "
                                    f"not the registered anchor..{day}")
        t = len(pre.sessions) - 1
        phase = cal.count_sessions_after(self.decisions_from, day)
        mask = TB.explicit_mask(pre)[:, t]
        books = self.cohort.decide(pre, t, W.session_index(cal, day), phase,
                                   _last_targets(ledger.of_type(DECISION), self.cohort.books), mask)
        traded = [b for b, x in books.items() if x is not None]
        w = W.window(cal, day)
        return X.label_body({
            "books": books, "phase": phase, "trade_at": f"close of {w.next_session.isoformat()}",
            "eligible": int(mask.sum()), "source": dd.meta,
            "window": {"close_utc": W.iso(w.close_utc), "earliest_utc": W.iso(w.earliest_utc),
                       "deadline_utc": W.iso(w.deadline_utc), "next_session": w.next_session.isoformat(),
                       "early_close": w.early_close},
            "summary": (f"rebalance: {', '.join(traded)}" if traded else "hold (no rebalance this session)")
            + f"; traded at the close of {w.next_session.isoformat()}. Paper books, research only."})


# ---- replay: committed targets -> daily net returns ------------------------------------------------------------------
def _cost(sym: str) -> float:
    return COSTS_BPS["cash" if sym == TB.CASH else "spy" if sym == BENCHMARK else "stock_close"] / 1e4


def _session_returns(px: dict, i: int, day: pd.Timestamp, q: set) -> dict[str, float]:
    out = {}
    for sym, p in px.items():
        a, b = p[i - 1], p[i]
        if (sym, day) not in q and math.isfinite(a) and math.isfinite(b) and a > 0:
            out[sym] = float(b / a - 1.0)
    return out


def replay(decisions: list[dict], closes: pd.DataFrame, quarantine: dict[str, list] | None = None,
           books: dict | None = None) -> dict[str, dict]:
    """Daily net returns of every book (and SPY's) for each session of `closes` after the first decision session.

    `closes`: final split-adjusted closes (sessions x symbols, SPY included), starting at the first decision session.
    Books start fully in SPY; the targets decided at session s are traded at the close of the next session. `books`:
    the cohort's books (default cohort 0's)."""
    book_names = list(TB.BOOKS if books is None else books)
    by_day = {pd.Timestamp(d["session"]): d["body"].get("books") or {} for d in decisions}
    q = {(s, pd.Timestamp(x)) for s, xs in (quarantine or {}).items() for x in xs}
    sessions = list(closes.index)
    px = {s: closes[s].to_numpy(float) for s in closes.columns}
    hold = {b: {BENCHMARK: 1.0} for b in book_names}
    pending = {b: (by_day.get(sessions[0], {}).get(b) if sessions else None) for b in book_names}
    out: dict[str, dict] = {}
    for i in range(1, len(sessions)):
        day = sessions[i]
        rets = _session_returns(px, i, day, q)  # CASH, a missing bar or a quarantined pair earn nothing
        spy = rets.get(BENCHMARK, 0.0)
        row = {}
        for book in book_names:
            h = hold[book]
            gross = sum(w * rets.get(s, 0.0) for s, w in sorted(h.items()))
            h = {s: w * (1.0 + rets.get(s, 0.0)) / (1.0 + gross) for s, w in h.items()}
            cost = 0.0
            tgt = pending.get(book)
            if tgt is not None:
                new = {s: m / TB.MICRO for s, m in tgt["micro"].items()}
                cost = sum(_cost(s) * abs(new.get(s, 0.0) - h.get(s, 0.0)) for s in sorted(set(h) | set(new)))
                h = new
            hold[book] = h
            row[book] = (1.0 + gross) * (1.0 - cost) - 1.0
        out[day.date().isoformat()] = {"spy": spy, "books": row}
        pending = {b: by_day.get(day, {}).get(b) for b in book_names}
    return out


def tournament_state(sessions: dict[str, dict], cohort: Cohort | None = None) -> dict:
    """A cohort's tournament after every posted session's returns, in session order (a pure function of the ledger)."""
    co = cohort or COHORT_0
    cfg, cands = co.tournament, co.candidates
    if cfg["claim_rule"] != CLAIM_RULE:
        raise ValueError(f"the series declares claim rule {cfg['claim_rule']!r}; the engine implements {CLAIM_RULE!r}")
    t = Tournament(book=cfg["book"], null_mass=cfg["null_mass"], alpha=cfg["alpha"],
                   lambdas=tuple(cfg["lambdas"]), cost_per_side=cfg["cost_per_side"])
    days = sorted(sessions)
    for c in cands:
        if days:
            t.admit(c.candidate_id, c.prior_mass, days[0], twin=c.twin is not None, loss_bound=c.loss_bound,
                    twin_loss_bound=c.twin_loss_bound, note=c.mechanism, claims=c.claims)
    for d in days:
        books = sessions[d]["books"]
        t.settle(d, sessions[d]["spy"], {c.candidate_id: books[c.book] for c in cands},
                 {c.candidate_id: books[c.twin] for c in cands if c.twin is not None})
    rep = t.report()

    def ev(st: dict | None) -> dict | None:
        return None if st is None else {k: st[k] for k in ("e", "e_max", "p_anytime", "status")}

    return {"sessions": rep["sessions"], "last_session": rep["last_session"], "book": rep["book"],
            "claim_rule": rep["claim_rule"], "book_relative_wealth": rep["book_relative_wealth"], "global_e": rep["global_e"],
            "global_p_anytime": rep["global_p_anytime"], "allocation_spy": rep["allocation_spy"],
            "e_bh_now": rep["e_bh_now"],
            "candidates": {k: {"prior_mass": r["entry"]["prior_mass"], "claim_tests": r["claim_tests"],
                               "threshold": r["threshold"],
                               "allocation": r["allocation"], "claims": r["claims"], "vs_spy": ev(r["vs_spy"]),
                               "vs_spy_bounded_loss": ev(r.get("vs_spy_bounded_loss")), "vs_twin": ev(r.get("vs_twin")),
                               "vs_twin_bounded_loss": ev(r.get("vs_twin_bounded_loss"))}
                           for k, r in rep["candidates"].items()},
            "mode": "RESEARCH_ONLY", "orders": "none", "group_actionable": False}


# ---- golden behaviour fingerprint -------------------------------------------------------------------------------------
GOLDEN = {"per_sector": 5, "seed": 23, "start": date(2023, 1, 3), "end": date(2024, 9, 30), "sessions": 80}


@functools.cache
def _golden_panel() -> TB.Panel:
    from apex_worker.research.realeq.fixture_v2 import factor_frames_v2

    return TB.light_panel(factor_frames_v2(per_sector=GOLDEN["per_sector"], seed=GOLDEN["seed"],
                                           start=GOLDEN["start"], end=GOLDEN["end"]))


def golden_decisions(cohort: Cohort | None = None) -> list[dict]:
    co = cohort or COHORT_0
    pre = _golden_panel()
    mask = TB.explicit_mask(pre)
    n = len(pre.sessions)
    prev: dict[str, dict | None] = dict.fromkeys(co.books)
    out = []
    for phase, t in enumerate(range(n - GOLDEN["sessions"], n)):
        books = co.decide(pre, t, t, phase, prev, mask[:, t])
        prev |= {b: x for b, x in books.items() if x is not None}
        out.append(books)
    return out


def golden_fingerprint(cohort: Cohort | None = None) -> str:
    return hashlib.sha256(canonical(clean(golden_decisions(cohort)))).hexdigest()


# ---- the recorder ---------------------------------------------------------------------------------------------------
class TournamentRecorder(Recorder):
    """The forward-shadow recorder with this lane's genesis checks and outcomes (decisions are the base class's)."""

    def __init__(self, lane: TournamentLane, *args, **kw):
        kw.setdefault("fingerprint_fn", functools.partial(golden_fingerprint, lane.cohort))
        kw.setdefault("expected_fingerprint", lane.cohort.pinned_fingerprint)
        super().__init__(lane, *args, **kw)

    def _check_inputs(self, rep: RunReport) -> dict:
        try:
            X.assert_separate([self.lane.series_id])
        except ValueError as exc:
            rep.flag("INTEGRITY", f"SERIES_INVALID: {exc}")
            raise _Stop from None
        return {"policy_config_sha256": self.lane.cohort.policy.config_sha256()}

    def _outcomes(self, ledger: Ledger, rep: RunReport, anchors: dict) -> None:
        data_end = W.outcome_data_end(self.cal, self.clock())
        decisions = ledger.of_type(DECISION)
        done = {r["session"] for r in ledger.of_type(OUTCOME)}
        due = [d for d in decisions if d["session"] not in done and date.fromisoformat(d["session"]) <= data_end]
        if not due:
            return
        try:
            ob = self.source.outcome_data(self.lane.decisions_from, data_end, self.clock())
        except SourceUnavailable as exc:
            rep.flag("SOURCE_PENDING" if exc.status in ("SOURCE_PENDING", "RATE_LIMITED") else "SOURCE_INVALID",
                     f"outcomes deferred: {exc.status}: {exc.reason}")
            return
        closes = pd.DataFrame({s: df["close"].astype(float) for s, df in ob.bars.items()}).reindex(ob.sessions)
        first = decisions[0]["session"]
        series = replay(decisions, closes[closes.index >= pd.Timestamp(first)], ob.quarantine, self.lane.cohort.books)
        posted: dict[str, dict] = {}
        for r in ledger.of_type(OUTCOME):
            posted |= r["body"].get("sessions") or {}
        revisions = [{"session": s, "book": b, "posted": v, "recomputed": series[s]["books"][b]}
                     for s, row in posted.items() if s in series
                     for b, v in row["books"].items() if abs(series[s]["books"][b] - v) > 1e-9]
        last = max(posted, default=first)
        for d in due:
            new = {s: series[s] for s in sorted(series) if last < s <= d["session"]}
            posted |= new
            last = max(last, d["session"])
            state = tournament_state(posted, self.lane.cohort)
            top = max(state["candidates"].items(), key=lambda kv: kv[1]["allocation"], default=(None, {}))
            body = X.label_body({
                "decision": {"record_id": d["record_id"], "digest": d["digest"], "session": d["session"]},
                "data_end": data_end.isoformat(), "source": ob.meta, "sessions": new,
                "quarantine": {s: [str(x)[:10] for x in xs] for s, xs in ob.quarantine.items()},
                "revisions": revisions, "tournament": state,
                "summary": (f"{len(new)} session(s) labelled; book vs SPY {state['book_relative_wealth']:.4f}; "
                            f"largest allocation {top[0]} {top[1].get('allocation', 0):.3f}; "
                            f"claims {sum(len(c['claims']) for c in state['candidates'].values())}. Paper, research only.")})
            self._append(ledger, rep, anchors, OUTCOME, d["session"], body)
            revisions = []


def books_summary_md(records: list[dict]) -> str:
    """The latest tournament state as a short Markdown table (exploratory lane: interim reading is anytime-valid)."""
    outs = [r for r in records if r["record_type"] == OUTCOME]
    if not outs:
        return "\n_No tournament outcome recorded yet._\n"
    st = outs[-1]["body"]["tournament"]
    lines = [f"\n### Tournament after {st['last_session']} ({st['sessions']} sessions; paper, research only)\n",
             f"Book vs SPY: {st['book_relative_wealth']:.4f} · global evidence {st['global_e']:.3f} · SPY share "
             f"{st['allocation_spy']:.3f}\n",
             "Evidence: assumption-free · under the declared loss bound. A claim needs the threshold on one of the "
             "candidate's claim tests.\n",
             "| Candidate | e vs SPY | e vs twin | threshold | allocation | claims |", "|---|---|---|---|---|---|"]

    def pair(free: dict | None, bounded: dict | None) -> str:
        if free is None:
            return "—"
        return f"{free['e']:.3f} · {bounded['e']:.3f}" if bounded else f"{free['e']:.3f}"

    for k, c in st["candidates"].items():
        lines.append(f"| {k} | {pair(c['vs_spy'], c['vs_spy_bounded_loss'])} | "
                     f"{pair(c['vs_twin'], c['vs_twin_bounded_loss'])} | {c['threshold']:.0f} | {c['allocation']:.3f} | "
                     f"{', '.join(c['claims']) or '—'} |")
    if st["candidates"] and not any(t.startswith("EDGE") for c in st["candidates"].values() for t in c["claim_tests"]):
        lines.append("\nClaims are against the twin only; the evidence against SPY drives the allocation.")
    return "\n".join(lines) + "\n"


class SharedSource:
    """One market-data fetch for every cohort recorded in the same pass: decision and outcome data are memoised per
    request (failures are not), so a later cohort reads the bars the first one fetched. Everything else (the request
    count, the data class) is the wrapped source's."""

    def __init__(self, inner):
        self.inner = inner
        self._memo: dict = {}

    def __getattr__(self, name: str):
        return getattr(self.inner, name)

    def _get(self, key: tuple, fetch: Callable[[], object]):
        if key not in self._memo:
            self._memo[key] = fetch()
        return self._memo[key]

    def decision_data(self, day: date, now):
        return self._get(("decision", day), lambda: self.inner.decision_data(day, now))

    def outcome_data(self, start: date, end: date, now, symbols: tuple[str, ...] | None = None):
        key = ("outcome", start, end, None if symbols is None else tuple(symbols))
        return self._get(key, lambda: self.inner.outcome_data(start, end, now, symbols))


__all__ = ["CANDIDATES", "COHORTS", "COHORT_0", "COHORT_1", "DECISIONS_FROM", "LANES", "PINNED_FINGERPRINT",
           "PINNED_FINGERPRINT_C1", "POLICY", "SERIES_ID", "Cohort", "SharedSource", "TournamentLane",
           "TournamentRecorder", "assert_mass_budget", "books_summary_md", "config_sha256", "golden_fingerprint",
           "replay", "series_config", "tournament_state"]
