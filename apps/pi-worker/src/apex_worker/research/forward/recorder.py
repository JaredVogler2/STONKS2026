"""The forward-shadow recorder: one idempotent pass per run (docs/discovery-v3/FORWARD_SHADOW.md).

Order of a run, each step fail-closed:
1. frozen inputs (protocol sha256, selection lock, arms) are exactly the registered ones;
2. read the ledger from the store and verify every record and the chain (insert, delete, edit, reorder, gaps);
3. post records left in the outbox by an earlier run whose post was not confirmed, with their ORIGINAL digests;
4. genesis (first run only), or check that the series configuration is unchanged;
5. for every session since `decisions_from` whose window has opened: nothing if it already has a DECISION or
   MISSED_OBSERVATION; MISSED_OBSERVATION if its deadline (the next open) passed; otherwise decide now, from data
   observed now. A decision computed after the deadline is recorded as missed, never as a decision;
6. label every matured DECISION, in session order (the study's de-duplication is order dependent).
Exit codes: 0 ok, 3 source pending (retry later), 4 a session was missed, 5 source invalid / credentials,
6 integrity (chain, frozen inputs, series config, behaviour fingerprint), 7 store (anchor pending or no permission).
"""

from __future__ import annotations

import json
import os
import platform
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import ClassVar

import pandas as pd

from apex_worker.market.calendar import MarketCalendar
from apex_worker.research.forward import frozen as F
from apex_worker.research.forward import windows as W
from apex_worker.research.forward.health import FROZEN_READING, health
from apex_worker.research.forward.records import (
    ATTEMPT_FAILED,
    DECISION,
    GENESIS,
    MISSED,
    OUTCOME,
    ChainViolation,
    Ledger,
    digest_of,
    verify_chain,
)
from apex_worker.research.forward.source import DecisionData, MarketSource, SourceUnavailable
from apex_worker.research.forward.store import IssueLedgerStore, StoreError
from apex_worker.research.realeq import study_v2 as S

EXIT = {"OK": 0, "SOURCE_PENDING": 3, "MISSED": 4, "SOURCE_INVALID": 5, "INTEGRITY": 6, "STORE": 7}
SEVERITY = ("OK", "SOURCE_PENDING", "MISSED", "SOURCE_INVALID", "STORE", "INTEGRITY")
SOURCE_FLAG = {"SOURCE_PENDING": "SOURCE_PENDING", "RATE_LIMITED": "SOURCE_PENDING", "CREDENTIALS_MISSING":
               "SOURCE_INVALID", "SOURCE_INVALID": "SOURCE_INVALID", "INSUFFICIENT_HISTORY": "SOURCE_INVALID"}
OWNER_SETUP = {
    "CREDENTIALS_MISSING": "Repository Settings -> Environments -> real-equities-research: add the secrets "
    "ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY (read-only market-data keys; deployment branch main).",
    "STORE_PERMISSION": "Repository Settings -> Actions -> General -> Workflow permissions: either default is fine, but "
    "no organisation or repository policy may forbid a workflow from requesting `issues: write` (the job requests only "
    "contents: read and issues: write). Then re-run apex-forward-shadow from the Actions tab.",
}


def producer_info(env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    import exchange_calendars
    import numpy
    import pandas

    repo, server = env.get("GITHUB_REPOSITORY"), env.get("GITHUB_SERVER_URL", "https://github.com")
    run = env.get("GITHUB_RUN_ID")
    return {"recorder_version": F.RECORDER_VERSION, "git_sha": env.get("GITHUB_SHA") or _git_sha(),
            "run_id": run, "run_attempt": env.get("GITHUB_RUN_ATTEMPT"), "workflow": env.get("GITHUB_WORKFLOW"),
            "event": env.get("GITHUB_EVENT_NAME"), "run_url": f"{server}/{repo}/actions/runs/{run}" if repo and run else None,
            "python": platform.python_version(),
            "packages": {"numpy": numpy.__version__, "pandas": pandas.__version__,
                         "exchange_calendars": exchange_calendars.__version__}}


def _git_sha() -> str | None:
    import subprocess

    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,  # noqa: S607
                              timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


@dataclass
class FrozenLane:
    """The frozen confirmatory lane, or its synthetic DEMO twin (`fixture`): same logic, separate series and lane."""

    series_id: str = F.SERIES_ID
    lane: str = F.LANE
    decisions_from: date = F.DECISIONS_FROM
    data_class: str = "REAL_MARKET_DATA"
    check_inputs: bool = True
    reading: ClassVar[dict] = FROZEN_READING  # outcomes are read at the registered looks only

    @classmethod
    def fixture(cls, decisions_from: date = F.DECISIONS_FROM) -> FrozenLane:
        return cls("fixture-demo-qv2a1-frozen", "FIXTURE_DEMO", decisions_from, "SYNTHETIC_FIXTURE_DEMO", True)

    def config(self, fingerprint: str) -> dict:
        cfg = F.series_config(fingerprint)
        return cfg | {"series_id": self.series_id, "lane": self.lane, "decisions_from": self.decisions_from.isoformat(),
                      "data_class": self.data_class}

    def decide(self, dd: DecisionData, day: date, ledger: Ledger, cal: MarketCalendar) -> dict:
        pre = S.prepare(dd.bars)
        first = W.session_index(cal, pre.sessions[0].date())
        if first != F.WINDOW_ANCHOR_INDEX or pre.sessions[-1].date() != day:
            raise SourceUnavailable("SOURCE_INVALID", f"window {pre.sessions[0].date()}..{pre.sessions[-1].date()} is "
                                    f"not the registered anchor..{day}")
        ctx = F.FrozenContext(pre, first)
        t = len(pre.sessions) - 1
        held = F.held_from_ledger(ledger.of_type(DECISION), ctx, t)
        body = F.decide_at(ctx, t, held)
        w = W.window(cal, day)
        return body | {"source": dd.meta, "window": {"close_utc": W.iso(w.close_utc), "earliest_utc": W.iso(w.earliest_utc),
                                                     "deadline_utc": W.iso(w.deadline_utc),
                                                     "next_session": w.next_session.isoformat(),
                                                     "early_close": w.early_close}}


class _Stop(Exception):
    pass


@dataclass
class RunReport:
    series_id: str
    lane: str
    started_at: str
    status: str = "OK"
    exit_code: int = 0
    actions: list[dict] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    owner_setup: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    health: dict = field(default_factory=dict)
    provider_requests: int = 0
    github_requests: int = 0
    finished_at: str | None = None

    def flag(self, kind: str, message: str) -> None:
        self.flags.append(kind)
        self.messages.append(f"{kind}: {message}")
        worst = max(self.flags, key=SEVERITY.index)
        self.status, self.exit_code = worst, EXIT[worst]

    def to_dict(self) -> dict:
        return asdict(self)


class Recorder:
    def __init__(self, lane: FrozenLane, store: IssueLedgerStore, source: MarketSource, cal: MarketCalendar, *,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC), producer: dict | None = None,
                 outbox: Path | None = None, fingerprint_fn: Callable[[], str] = F.golden_fingerprint,
                 expected_fingerprint: str = F.PINNED_FINGERPRINT, decide_only: bool = False):
        self.lane, self.store, self.source, self.cal, self.clock = lane, store, source, cal, clock
        self.producer = producer if producer is not None else producer_info()
        self.outbox_path, self.fingerprint_fn, self.expected_fp = outbox, fingerprint_fn, expected_fingerprint
        self.decide_only = decide_only
        self._fp_ok = False
        self.pending: list[dict] = []

    # ---- helpers ---------------------------------------------------------------------------------------------------
    def _load_outbox(self) -> list[dict]:
        if self.outbox_path is None or not self.outbox_path.exists():
            return []
        try:
            data = json.loads(self.outbox_path.read_text() or "[]")
        except ValueError:
            return []
        return [r for r in data if isinstance(r, dict) and r.get("series_id") == self.lane.series_id]

    def _save_outbox(self) -> None:
        if self.outbox_path is not None:
            self.outbox_path.parent.mkdir(parents=True, exist_ok=True)
            self.outbox_path.write_text(json.dumps(self.pending, indent=1, sort_keys=True))

    def _post(self, ledger: Ledger, rep: RunReport, rec: dict, anchors: dict) -> None:
        try:
            anchor = self.store.append(rec)
        except StoreError as exc:
            if exc.kind == "INTEGRITY":  # e.g. PUBLICATION_BLOCKED: never retried, never published
                rep.flag("INTEGRITY", f"{rec['record_type']} {rec.get('session') or ''} not published: {exc}")
                raise _Stop from None
            self.pending.append(rec)
            if exc.kind == "PERMISSION":
                rep.owner_setup.append(OWNER_SETUP["STORE_PERMISSION"])
            rep.flag("STORE", f"ANCHOR_PENDING: {rec['record_type']} {rec.get('session') or ''} seq {rec['seq']} was not "
                     f"confirmed by GitHub ({exc}); it is kept in the outbox and re-posted with the same digest "
                     f"{rec['digest'][:16]}… by the next run")
            raise _Stop from None
        ledger.commit(rec)
        anchors[rec["digest"]] = anchor
        rep.actions.append({"record_type": rec["record_type"], "session": rec.get("session"), "seq": rec["seq"],
                            "digest": rec["digest"], "anchored_at": anchor.created_at, "anchor_url": anchor.url})

    def _append(self, ledger: Ledger, rep: RunReport, anchors: dict, record_type: str, session: str | None,
                body: dict) -> dict:
        rec = ledger.build(record_type, session, body, W.iso(self.clock()), self.producer)
        self._post(ledger, rep, rec, anchors)
        return rec

    def _fingerprint(self, rep: RunReport) -> None:
        if self._fp_ok:
            return
        fp = self.fingerprint_fn()
        if fp != self.expected_fp:
            rep.flag("INTEGRITY", f"SERIES_BEHAVIOUR_CHANGED: the frozen decision logic produces fingerprint {fp[:16]}… "
                     f"on the golden fixture, not the registered {self.expected_fp[:16]}…; a changed implementation "
                     "needs a new series, so nothing is recorded")
            raise _Stop
        self._fp_ok = True

    def _expected_sessions(self, ledger: Ledger) -> list[str]:
        last = max((r["session"] for r in ledger.records if r["record_type"] in (DECISION, MISSED)), default=None)
        if last is None:
            return []
        return [d.isoformat() for d in self.cal.sessions_between(self.lane.decisions_from, date.fromisoformat(last))]

    # ---- the run ---------------------------------------------------------------------------------------------------
    def run(self) -> RunReport:
        now = self.clock()
        rep = RunReport(self.lane.series_id, self.lane.lane, W.iso(now))
        anchors: dict = {}
        ledger = Ledger(self.lane.series_id, self.lane.lane)
        try:
            inputs = self._check_inputs(rep)
            rr = self._read(rep)
            anchors = dict(rr.anchors)
            try:
                ledger = Ledger(self.lane.series_id, self.lane.lane, rr.records)
            except ChainViolation as exc:
                rep.flag("INTEGRITY", f"CHAIN_VIOLATION: {exc}")
                raise _Stop from None
            problems = verify_chain(ledger.records, series_id=self.lane.series_id, lane=self.lane.lane,
                                    expected_sessions=self._expected_sessions(ledger))
            if problems:
                rep.flag("INTEGRITY", f"CHAIN_VIOLATION: {'; '.join(problems[:5])}")
                raise _Stop
            self._flush_outbox(ledger, rep, anchors)
            self._genesis(ledger, rep, anchors, inputs)
            self._sessions(ledger, rep, anchors)
            if not self.decide_only:
                self._outcomes(ledger, rep, anchors)
        except _Stop:
            pass
        finally:
            self._save_outbox()
        rep.health = health(ledger.records, anchors, self.cal, self.clock(), self.lane.decisions_from,
                            [m for m in rep.messages if m.startswith("INTEGRITY")], self.lane.reading)
        rep.provider_requests = self.source.requests_used
        rep.github_requests = int(getattr(self.store.api, "requests", 0))
        rep.finished_at = W.iso(self.clock())
        return rep

    def _check_inputs(self, rep: RunReport) -> dict:
        if not self.lane.check_inputs:
            return {}
        try:
            return F.check_frozen_inputs()
        except F.FrozenLaneInvalid as exc:
            rep.flag("INTEGRITY", f"FROZEN_LANE_INVALID: {exc}")
            raise _Stop from None

    def _read(self, rep: RunReport):
        try:
            rr = self.store.read()
        except StoreError as exc:
            if exc.kind == "PERMISSION":
                rep.owner_setup.append(OWNER_SETUP["STORE_PERMISSION"])
            rep.flag("STORE", f"LEDGER_UNREADABLE: {exc}")
            raise _Stop from None
        if rr.problems:
            rep.flag("INTEGRITY", f"LEDGER_INVALID: {'; '.join(rr.problems[:5])}")
            raise _Stop
        if rr.incomplete_tail:
            rep.messages.append(f"an interrupted multi-part post (seq {rr.incomplete_tail['seq']}) is completed from the "
                                "outbox or superseded")
        return rr

    def _flush_outbox(self, ledger: Ledger, rep: RunReport, anchors: dict) -> None:
        stored = {r["digest"] for r in ledger.records}
        for rec in sorted(self._load_outbox(), key=lambda r: r.get("seq", 0)):
            if rec.get("digest") in stored:
                continue  # the earlier post reached GitHub after all (its response was lost)
            ok = (rec.get("seq") == len(ledger.records) and rec.get("previous_digest") == ledger.head
                  and rec.get("digest") == digest_of(rec))
            if not ok:
                rep.messages.append(f"outbox record seq {rec.get('seq')} no longer extends the chain: discarded")
                continue
            self._post(ledger, rep, rec, anchors)
            rep.messages.append(f"outbox record seq {rec['seq']} posted with its original digest (anchored late)")

    def _genesis(self, ledger: Ledger, rep: RunReport, anchors: dict, inputs: dict) -> None:
        cfg = self.lane.config(self.expected_fp)
        sha = F.config_sha256(cfg)
        if ledger.genesis is None:
            self._fingerprint(rep)
            body = {"config": cfg, "config_sha256": sha, "frozen_inputs_check": inputs,
                    "summary": f"Series {self.lane.series_id} starts; decisions from {self.lane.decisions_from}. "
                    f"Arms {', '.join(cfg['arms'])}. Research only."}
            self._append(ledger, rep, anchors, GENESIS, None, body)
        elif ledger.genesis["body"].get("config_sha256") != sha:
            rep.flag("INTEGRITY", "SERIES_CONFIG_CHANGED: the code's series configuration differs from this series' "
                     "genesis record; a changed implementation needs a new series id")
            raise _Stop

    def _sessions(self, ledger: Ledger, rep: RunReport, anchors: dict) -> None:
        genesis_at = W.utc(ledger.genesis["observed_at"])
        for w in W.due_windows(self.cal, self.lane.decisions_from, self.clock()):
            day = w.session.isoformat()
            if ledger.terminal(day) is not None:
                continue
            if w.state(self.clock()) == "CLOSED":
                self._missed(ledger, rep, anchors, w, genesis_at)
                continue
            self._fingerprint(rep)
            try:
                dd = self.source.decision_data(w.session, self.clock())
                body = self.lane.decide(dd, w.session, ledger, self.cal)
            except SourceUnavailable as exc:
                kind = SOURCE_FLAG.get(exc.status, "SOURCE_INVALID")
                if exc.status in OWNER_SETUP:
                    rep.owner_setup.append(OWNER_SETUP[exc.status])
                rep.flag(kind, f"{day}: {exc.status}: {exc.reason}")
                if not ledger.has(day, ATTEMPT_FAILED):
                    self._append(ledger, rep, anchors, ATTEMPT_FAILED, day,
                                 {"status": exc.status, "reason": exc.reason, "deadline_utc": W.iso(w.deadline_utc),
                                  "summary": f"{exc.status}; a later run before {W.iso(w.deadline_utc)} may still decide"})
                continue
            if self.clock() >= w.deadline_utc:
                self._missed(ledger, rep, anchors, w, genesis_at, reason="DECISION_COMPUTED_AFTER_DEADLINE")
                continue
            self._append(ledger, rep, anchors, DECISION, day, body)

    def _missed(self, ledger: Ledger, rep: RunReport, anchors: dict, w: W.SessionWindow, genesis_at: datetime,
                reason: str | None = None) -> None:
        day = w.session.isoformat()
        attempt = next((r for r in ledger.records if r["session"] == day and r["record_type"] == ATTEMPT_FAILED), None)
        if reason is None:
            if w.deadline_utc <= genesis_at:
                reason = "BEFORE_SERIES_GENESIS"
            elif attempt is not None:
                reason = f"ATTEMPT_FAILED:{attempt['body'].get('status')}"
            else:
                reason = "NO_RUN_IN_WINDOW"
        body = {"reason": reason, "window": {"earliest_utc": W.iso(w.earliest_utc), "deadline_utc": W.iso(w.deadline_utc)},
                "noticed_at": W.iso(self.clock()), "attempt_seq": attempt["seq"] if attempt else None,
                "summary": f"MISSED ({reason}): no decision was recorded before the next open; never back-filled"}
        self._append(ledger, rep, anchors, MISSED, day, body)
        if reason != "BEFORE_SERIES_GENESIS":
            rep.flag("MISSED", f"{day} missed ({reason})")

    def _outcomes(self, ledger: Ledger, rep: RunReport, anchors: dict) -> None:
        data_end = W.outcome_data_end(self.cal, self.clock())
        done = {r["session"] for r in ledger.of_type(OUTCOME)}
        decisions = ledger.of_type(DECISION)
        due = [d for d in decisions if d["session"] not in done and W.is_mature(self.cal, date.fromisoformat(d["session"]),
                                                                                    data_end)]
        if not due:
            return
        start = date.fromisoformat(due[0]["session"])
        try:
            ob = self.source.outcome_data(start, data_end, self.clock())
            ob5 = self.source.outcome_data(self.lane.decisions_from, data_end, self.clock(), MarketSource.s5_symbols())
        except SourceUnavailable as exc:
            rep.flag(SOURCE_FLAG.get(exc.status, "SOURCE_INVALID"), f"outcomes deferred: {exc.status}: {exc.reason}")
            return
        busy: dict[str, int] = {}
        for r in ledger.of_type(OUTCOME):
            busy.update(r["body"].get("busy_until") or {})
        od5 = F.OutcomeData(ob5.sessions, ob5.bars, {}, data_end)
        first5 = W.session_index(self.cal, ob5.sessions[0].date())
        for d in due:
            t = pd.Timestamp(d["session"])
            sub = F.OutcomeData(ob.sessions[ob.sessions >= t], {s: x[x.index >= t] for s, x in ob.bars.items()},
                                ob.quarantine, data_end)
            waited = W.sessions_after(self.cal, t.date(), data_end)
            n_open = F.unresolved(d, sub)
            if n_open and waited < W.LABEL_SESSIONS + W.EXTRA_SESSIONS:
                rep.messages.append(f"outcome {d['session']} waits: {n_open} trade(s) not yet resolved on final data")
                break  # later decisions wait too: the study labels in time order
            before = dict(busy)
            m = F.mature(d, sub, busy)
            s5 = F.s5_outcomes([x for x in decisions if x["session"] <= d["session"]], od5, first5, [t.date()])
            counts = {k: a["counts"] for k, a in m["arms"].items()}
            body = {"decision": {"record_id": d["record_id"], "digest": d["digest"], "session": d["session"]},
                    "data_end": data_end.isoformat(), "sessions_after_decision": waited,
                    "unresolved_at_label_end": n_open, "source": ob.meta, "arms": m["arms"],
                    f"S5:{F.S5_ARM}": s5.get(d["session"]), "revisions": m["revisions"],
                    "busy_until": {k: v for k, v in busy.items() if before.get(k) != v},
                    "summary": "labels: " + " · ".join(f"{k} kept {c['kept']}/{c['planned']}" for k, c in counts.items())
                    + (f" · {len(m['revisions'])} data revision(s)" if m["revisions"] else "")}
            self._append(ledger, rep, anchors, OUTCOME, d["session"], body)
