"""`apex research forward-shadow ...` (research only; no orders, alerts, signing or promotion).

  run     one recorder pass. Real: --store github --source alpaca (GITHUB_TOKEN with issues: write, GITHUB_REPOSITORY,
          ALPACA_KEY_ID / ALPACA_SECRET_KEY from the environment). DEMO: --store file --source fixture --now ISO.
  verify  read-only: re-read and verify every record, the chain and session coverage (exit 6 on any problem).
  health  read-only: capture/anchor/missed/maturity counts (never outcome values).
  export  read-only: JSONL snapshot of the records plus their GitHub anchors, for the monthly reviewed PR.
  smoke   DEMO end to end on the synthetic fixture over a simulated calendar (decisions, a pending source, a missed
          session, catch-up, maturity, verification). Never market data; output must not go under research-results/.
  tournament-smoke  the same for the exploratory evidence tournament lane (--lane tournament on the other commands).

`--lane tournament` selects the exploratory lane v3x-epa-tournament-v1 (docs/evidence/TOURNAMENT_LANE.md). `--store dry`
runs a recorder pass against an in-memory ledger that is discarded: nothing is posted (a rehearsal of the real data
path before the owner activates a lane).
"""

from __future__ import annotations

import functools
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path

import pandas as pd

from apex_worker.market.calendar import MarketCalendar, default_calendar
from apex_worker.research.forward import frozen as F
from apex_worker.research.forward import windows as W
from apex_worker.research.forward.health import health, markdown
from apex_worker.research.forward.recorder import EXIT, FrozenLane, Recorder, producer_info
from apex_worker.research.forward.records import verify_chain
from apex_worker.research.forward.source import MarketSource, bars_through
from apex_worker.research.forward.store import IssueLedgerStore, MemoryGitHubAPI, RestGitHubAPI, StoreError
from apex_worker.timeutil import ET

TOURNAMENT_ISSUE_BODY = ("Append-only ledger of the APEX evidence tournament (exploratory lane v3x-epa-tournament-v1; "
                         "PROSPECTIVE_EXPLORATORY, never confirmatory): paper books only, no orders, no alerts, no "
                         "group-actionable output. Records are written by the scheduled `apex-forward-shadow` workflow and "
                         "carry a sha256 hash chain; GitHub's comment timestamp is the independent anchor. Do not edit or "
                         "delete comments. Verify with `apex research forward-shadow verify --lane tournament --store "
                         "github`. See docs/evidence/TOURNAMENT_LANE.md.")
ISSUE_BODY = ("Append-only ledger of the APEX forward shadow (research only: no orders, no alerts, no group-actionable "
              "output). Every record comment is written by the scheduled `apex-forward-shadow` workflow and carries a "
              "sha256 hash chain; GitHub's comment timestamp is the independent anchor. Do not edit or delete comments: "
              "an edited record fails verification. Verify with `apex research forward-shadow verify --store github`. "
              "See docs/discovery-v3/FORWARD_SHADOW.md.")
FIXTURE_PER_SECTOR = 3
FIXTURE_MIN_USABLE = 20


@dataclass
class SimClock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now


def _fixture_frames(end: date):
    from apex_worker.research.realeq.fixture_v2 import factor_frames_v2

    return factor_frames_v2(per_sector=FIXTURE_PER_SECTOR, seed=5, start=date(2023, 9, 1), end=end)


def _fixture_source(frames: dict, cal: MarketCalendar, clock: SimClock, hide: dict | None = None) -> MarketSource:
    from apex_worker.research.realeq.fixture import FakeAlpaca

    def factory():
        visible = bars_through(frames, cal, clock())
        for day, syms in (hide or {}).items():
            ts = pd.Timestamp(day)
            visible = {s: (df[df.index != ts] if syms is None or s in syms else df) for s, df in visible.items()}
        return FakeAlpaca(visible, corporate_actions=[]).adapter(clock())

    return MarketSource(factory, cal, data_class="SYNTHETIC_FIXTURE_DEMO", provider="fixture-emulation",
                        min_usable_stocks=FIXTURE_MIN_USABLE)


def _file_api(path: Path, clock) -> MemoryGitHubAPI:
    data = json.loads(path.read_text()) if path.exists() else {}
    return MemoryGitHubAPI.from_json(data, clock=clock)


def _github_api() -> RestGitHubAPI:
    return RestGitHubAPI(os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_REPOSITORY", ""))


def _tournament(args) -> bool:
    return getattr(args, "lane", "frozen") == "tournament"


def _lane(args):
    if _tournament(args):
        from apex_worker.research.forward import tournament_lane as T

        if args.source == "fixture":
            return T.TournamentLane.fixture(args.decisions_from or T.DECISIONS_FROM)
        return T.TournamentLane()
    if args.source == "fixture":
        return FrozenLane.fixture(args.decisions_from or F.DECISIONS_FROM)
    return FrozenLane()


def _expected_config_sha(args, lane) -> str:
    if _tournament(args):
        from apex_worker.research.forward import tournament_lane as T

        return T.config_sha256(lane.config(T.PINNED_FINGERPRINT))
    return F.config_sha256(lane.config(F.PINNED_FINGERPRINT))


def _wall_clock() -> datetime:
    return datetime.now(UTC)


def _parse_now(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)


def _write(path: str | None, text: str) -> None:
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text)


def cmd_run(args) -> int:
    cal = default_calendar()
    if args.store == "github" and args.source != "alpaca":
        print("the GitHub ledger records the real lane only: use --source alpaca", file=sys.stderr)
        return 5
    if args.source == "alpaca" and args.now:
        print("--now is for fixture runs only; real runs use the wall clock", file=sys.stderr)
        return 5
    lane = _lane(args)
    now0 = _parse_now(args.now) or datetime.now(UTC)
    clock = SimClock(now0) if args.source == "fixture" else _wall_clock
    if args.source == "fixture":
        frames = _fixture_frames(cal.add_sessions(now0.astimezone(ET).date(), 5))
        source = _fixture_source(frames, cal, clock)
        api = MemoryGitHubAPI(clock=clock) if args.store == "dry" else _file_api(Path(args.store_file), clock)
    else:
        from apex_worker.market.adapters.alpaca import AlpacaAdapter
        from apex_worker.research.realeq.data import credentials_from_env

        def factory():
            key, secret = credentials_from_env()
            return AlpacaAdapter(key, secret)

        source = MarketSource(factory, cal)
        try:
            api = MemoryGitHubAPI(clock=clock) if args.store == "dry" else _github_api()
        except StoreError as exc:
            print(f"STORE: {exc}", file=sys.stderr)
            return EXIT["STORE"]
    outbox = Path(args.outbox) if args.outbox and args.store != "dry" else None
    if _tournament(args):
        from apex_worker.research.forward import tournament_lane as T

        store = IssueLedgerStore(api, lane.series_id, issue_body=TOURNAMENT_ISSUE_BODY)
        rec = T.TournamentRecorder(lane, store, source, cal, clock=clock, producer=producer_info(), outbox=outbox,
                                   decide_only=args.decide_only)
    else:
        store = IssueLedgerStore(api, lane.series_id, issue_body=ISSUE_BODY)
        rec = Recorder(lane, store, source, cal, clock=clock, producer=producer_info(), outbox=outbox,
                       decide_only=args.decide_only)
    rep = rec.run()
    if args.store == "file":
        Path(args.store_file).write_text(json.dumps(api.to_json(), indent=1, sort_keys=True))
    out = rep.to_dict() | {"store": args.store}
    _write(args.report_json, json.dumps(out, indent=2, sort_keys=True) + "\n")
    md = markdown(rep.health, out)
    if args.store == "dry":
        md = ("**DRY RUN: nothing was posted** (in-memory ledger, discarded). Set the repository variable that "
              "activates this lane to record for real.\n\n" + md)
    if _tournament(args):
        from apex_worker.research.forward.tournament_lane import books_summary_md

        try:
            md += books_summary_md(store.read().records)
        except StoreError as exc:
            md += f"\n_Tournament summary unavailable: {exc}_\n"
    _write(args.summary_md, md)
    print(json.dumps({k: out[k] for k in ("series_id", "status", "exit_code", "messages", "owner_setup")} |
                     {"actions": [(a["record_type"], a["session"], a["seq"]) for a in out["actions"]],
                      "health_status": rep.health.get("status")}, indent=1))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"status={rep.status}\nexit_code={rep.exit_code}\nhealth={rep.health.get('status')}\n")
    return rep.exit_code


def _read(args):
    cal = default_calendar()
    lane = _lane(args)
    if args.store == "dry":
        api = MemoryGitHubAPI()  # a dry run keeps nothing: there is no ledger to read
    else:
        api = _file_api(Path(args.store_file), _wall_clock) if args.store == "file" else _github_api()
    store = IssueLedgerStore(api, lane.series_id)
    return cal, lane, store.read()


def cmd_verify(args) -> int:
    try:
        cal, lane, rr = _read(args)
    except StoreError as exc:
        print(f"STORE: {exc}", file=sys.stderr)
        return EXIT["STORE"]
    recs = rr.records
    last = max((r["session"] for r in recs if r["record_type"] in ("DECISION", "MISSED_OBSERVATION")), default=None)
    exp = [d.isoformat() for d in cal.sessions_between(lane.decisions_from, date.fromisoformat(last))] if last else []
    problems = list(rr.problems) + verify_chain(recs, series_id=lane.series_id, lane=lane.lane, expected_sessions=exp)
    if recs and recs[0]["body"].get("config_sha256") != _expected_config_sha(args, lane):
        problems.append("genesis configuration differs from this code's series configuration")
    print(json.dumps({"series_id": lane.series_id, "records": len(recs), "verified": not problems,
                      "problems": problems, "head": recs[-1]["digest"] if recs else None,
                      "volumes": rr.volumes}, indent=1))
    return EXIT["INTEGRITY"] if problems else 0


def cmd_health(args) -> int:
    try:
        cal, lane, rr = _read(args)
    except StoreError as exc:
        print(f"STORE: {exc}", file=sys.stderr)
        return EXIT["STORE"]
    h = health(rr.records, rr.anchors, cal, datetime.now(UTC), lane.decisions_from, rr.problems)
    print(markdown(h) if args.markdown else json.dumps(h, indent=1, sort_keys=True))
    return 0


def cmd_export(args) -> int:
    try:
        _cal, lane, rr = _read(args)
    except StoreError as exc:
        print(f"STORE: {exc}", file=sys.stderr)
        return EXIT["STORE"]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{lane.series_id}.records.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rr.records))
    (out / f"{lane.series_id}.anchors.json").write_text(json.dumps({d: a.to_dict() for d, a in rr.anchors.items()},
                                                                   indent=1, sort_keys=True))
    print(f"exported {len(rr.records)} records of {lane.series_id} to {out}")
    return 0 if not rr.problems else EXIT["INTEGRITY"]


def at_et(day: date, hh: int, mm: int) -> datetime:
    return datetime.combine(day, time(hh, mm), ET).astimezone(UTC)


def cmd_smoke(args) -> int:
    """DEMO: the recorder on the synthetic fixture over a simulated calendar. Exit 0 only if the scenario behaves."""
    cal = default_calendar()
    out = Path(args.out)
    if "research-results" in out.resolve().parts:
        print("DEMO output may never be written under research-results/", file=sys.stderr)
        return 5
    out.mkdir(parents=True, exist_ok=True)
    start = F.DECISIONS_FROM
    days = cal.sessions_between(start, cal.add_sessions(start, 6))
    mature_day = cal.add_sessions(days[2], W.LABEL_SESSIONS + W.FINALITY_LAG + 1)
    frames = _fixture_frames(cal.add_sessions(mature_day, 2))
    clock = SimClock(at_et(days[0], 18, 12))
    api = MemoryGitHubAPI(clock=clock)
    lane = FrozenLane.fixture(start)
    fp = functools.cache(F.golden_fingerprint)
    log = []
    hide: dict = {}

    def run(when: datetime, note: str):
        clock.now = when
        src = _fixture_source(frames, cal, clock, hide)
        rec = Recorder(lane, IssueLedgerStore(api, lane.series_id, issue_body=ISSUE_BODY), src, cal, clock=clock,
                       producer=producer_info() | {"note": "DEMO smoke"}, outbox=out / "outbox.json", fingerprint_fn=fp)
        rep = rec.run()
        log.append({"at": W.iso(when), "note": note, "status": rep.status, "exit_code": rep.exit_code,
                    "actions": [(a["record_type"], a["session"]) for a in rep.actions], "messages": rep.messages})
        print(f"[smoke] {W.iso(when)} {note}: {rep.status} {log[-1]['actions']}", flush=True)
        return rep

    run(at_et(days[0], 18, 12), "first scheduled run: genesis + decision")
    run(at_et(days[0], 21, 52), "retry run: idempotent no-op")
    run(at_et(days[1], 18, 12), "decision")
    hide[days[2]] = None  # the provider has not published session 3 yet at the first attempt
    run(at_et(days[2], 18, 12), "source pending")
    hide.clear()
    run(at_et(days[2], 21, 52), "retry: decision")
    # days[3]: no run at all (scheduler dropped both): noticed and recorded as missed on the next run
    run(at_et(days[4], 18, 12), "catch-up: day 4 missed, day 5 decided")
    run(at_et(mature_day, 18, 12), "maturity: catch-up misses, then outcomes for the matured decisions")
    rr = IssueLedgerStore(api, lane.series_id).read()
    exp = [d.isoformat() for d in cal.sessions_between(start, max(date.fromisoformat(r["session"]) for r in rr.records
                                                                   if r["record_type"] in ("DECISION", "MISSED_OBSERVATION")))]
    problems = rr.problems + verify_chain(rr.records, series_id=lane.series_id, lane=lane.lane, expected_sessions=exp)
    h = health(rr.records, rr.anchors, cal, clock.now, start, problems)
    types = [r["record_type"] for r in rr.records]
    checks = {
        "genesis_first": types[0] == "SERIES_GENESIS",
        "decisions": types.count("DECISION") >= 4,
        "pending_recorded": "OBSERVATION_ATTEMPT_FAILED" in types,
        "missed_recorded": any(r["record_type"] == "MISSED_OBSERVATION" and r["session"] == days[3].isoformat()
                               for r in rr.records),
        "outcomes": types.count("OUTCOME") >= 3, "chain_verifies": not problems,
        "no_duplicate_on_retry": len([r for r in rr.records if r["session"] == days[0].isoformat()
                                      and r["record_type"] == "DECISION"]) == 1,
    }
    (out / "ledger.records.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rr.records))
    (out / "smoke_report.json").write_text(json.dumps({"data_class": "SYNTHETIC_FIXTURE_DEMO", "checks": checks,
                                                       "runs": log, "health": h}, indent=1, sort_keys=True))
    (out / "summary.md").write_text("# Forward-shadow DEMO smoke (synthetic fixture, never market data)\n\n"
                                    + "\n".join(f"- {k}: {'PASS' if v else 'FAIL'}" for k, v in checks.items())
                                    + "\n\n" + markdown(h))
    print(json.dumps(checks, indent=1))
    return 0 if all(checks.values()) else 1


def cmd_tournament_smoke(args) -> int:
    """DEMO: the tournament lane on the synthetic fixture over a simulated calendar. Exit 0 only if it behaves."""
    from apex_worker.research.forward import tournament_lane as T

    cal = default_calendar()
    out = Path(args.out)
    if "research-results" in out.resolve().parts:
        print("DEMO output may never be written under research-results/", file=sys.stderr)
        return 5
    out.mkdir(parents=True, exist_ok=True)
    start = T.DECISIONS_FROM
    days = cal.sessions_between(start, cal.add_sessions(start, 9))
    frames = _fixture_frames(cal.add_sessions(days[-1], 3))
    clock = SimClock(at_et(days[0], 18, 12))
    api = MemoryGitHubAPI(clock=clock)
    lane = T.TournamentLane.fixture(start)
    fp = functools.cache(T.golden_fingerprint)
    log = []

    def run(when: datetime, note: str):
        clock.now = when
        rec = T.TournamentRecorder(lane, IssueLedgerStore(api, lane.series_id, issue_body=TOURNAMENT_ISSUE_BODY),
                                   _fixture_source(frames, cal, clock), cal, clock=clock,
                                   producer=producer_info() | {"note": "DEMO tournament smoke"},
                                   outbox=out / "outbox.json", fingerprint_fn=fp, expected_fingerprint=fp())
        rep = rec.run()
        log.append({"at": W.iso(when), "note": note, "status": rep.status, "exit_code": rep.exit_code,
                    "actions": [(a["record_type"], a["session"]) for a in rep.actions], "messages": rep.messages})
        print(f"[tournament-smoke] {W.iso(when)} {note}: {rep.status} {log[-1]['actions']}", flush=True)
        return rep

    for i, d in enumerate(days):
        if i == 4:
            continue  # no run that day: recorded as missed by the next run, never back-filled
        run(at_et(d, 18, 12), "decision" + (" + outcomes" if i >= 2 else ""))
    rr = IssueLedgerStore(api, lane.series_id).read()
    last = max(r["session"] for r in rr.records if r["record_type"] in ("DECISION", "MISSED_OBSERVATION"))
    exp = [d.isoformat() for d in cal.sessions_between(start, date.fromisoformat(last))]
    problems = rr.problems + verify_chain(rr.records, series_id=lane.series_id, lane=lane.lane, expected_sessions=exp)
    outs = [r for r in rr.records if r["record_type"] == "OUTCOME"]
    replayed = T.tournament_state({s: v for r in outs for s, v in r["body"]["sessions"].items()}) if outs else None
    checks = {
        "genesis_first": rr.records[0]["record_type"] == "SERIES_GENESIS",
        "decisions": sum(r["record_type"] == "DECISION" for r in rr.records) >= 8,
        "missed_recorded": any(r["record_type"] == "MISSED_OBSERVATION" and r["session"] == days[4].isoformat()
                               for r in rr.records),
        "outcomes": len(outs) >= 5, "chain_verifies": not problems,
        "state_replays": replayed is not None and replayed == outs[-1]["body"]["tournament"],
        "exploratory_labels": all(r["body"].get("evidence_tier") == "PROSPECTIVE_EXPLORATORY"
                                  for r in rr.records if r["record_type"] in ("DECISION", "OUTCOME")),
    }
    (out / "ledger.records.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rr.records))
    (out / "smoke_report.json").write_text(json.dumps({"data_class": "SYNTHETIC_FIXTURE_DEMO", "checks": checks,
                                                       "runs": log}, indent=1, sort_keys=True))
    (out / "summary.md").write_text("# Tournament lane DEMO smoke (synthetic fixture, never market data)\n\n"
                                    + "\n".join(f"- {k}: {'PASS' if v else 'FAIL'}" for k, v in checks.items())
                                    + "\n" + T.books_summary_md(rr.records))
    print(json.dumps(checks, indent=1))
    return 0 if all(checks.values()) else 1


def register(rsub) -> None:
    fs = rsub.add_parser("forward-shadow", help="prospective forward shadow of the frozen protocol-2 arms (research only)")
    sub = fs.add_subparsers(dest="forward_cmd", required=True)

    def common(p, store_default="github"):
        p.add_argument("--lane", choices=["frozen", "tournament"], default="frozen")
        p.add_argument("--store", choices=["github", "file", "dry"], default=store_default)
        p.add_argument("--store-file", default="forward-shadow-ledger.json", help="ledger file for --store file (DEMO)")
        p.add_argument("--source", choices=["alpaca", "fixture"], default="alpaca")
        p.add_argument("--decisions-from", type=date.fromisoformat, default=None, help="fixture lane only")

    r = sub.add_parser("run", help="one idempotent recorder pass")
    common(r)
    r.add_argument("--outbox", help="JSON file of records whose post was not confirmed (restored between runs)")
    r.add_argument("--report-json")
    r.add_argument("--summary-md")
    r.add_argument("--now", help="ISO instant; fixture runs only")
    r.add_argument("--decide-only", action="store_true", help="skip outcome labelling this run")
    r.set_defaults(func=cmd_run)
    v = sub.add_parser("verify", help="verify every record and the chain (read-only)")
    common(v)
    v.set_defaults(func=cmd_verify)
    h = sub.add_parser("health", help="capture/anchor/missed/maturity status (read-only)")
    common(h)
    h.add_argument("--markdown", action="store_true")
    h.set_defaults(func=cmd_health)
    e = sub.add_parser("export", help="JSONL snapshot + anchors for the monthly reviewed PR (read-only)")
    common(e)
    e.add_argument("--out", required=True)
    e.set_defaults(func=cmd_export)
    s = sub.add_parser("smoke", help="DEMO end-to-end on the synthetic fixture (never market data)")
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_smoke)
    ts = sub.add_parser("tournament-smoke", help="DEMO end-to-end of the tournament lane (never market data)")
    ts.add_argument("--out", required=True)
    ts.set_defaults(func=cmd_tournament_smoke)

