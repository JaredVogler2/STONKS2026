"""`apex research forward-shadow ...` (research only; no orders, alerts, signing or promotion).

  run     one recorder pass. Real: --store github --source alpaca (GITHUB_TOKEN with issues: write, GITHUB_REPOSITORY,
          ALPACA_KEY_ID / ALPACA_SECRET_KEY from the environment). DEMO: --store file --source fixture --now ISO.
  verify  read-only: re-read and verify every record, the chain and session coverage (exit 6 on any problem).
  health  read-only: capture/anchor/missed/maturity counts (never outcome values).
  export  read-only: JSONL snapshot of the records plus their GitHub anchors, for the monthly reviewed PR.
  smoke   DEMO end to end on the synthetic fixture over a simulated calendar (decisions, a pending source, a missed
          session, catch-up, maturity, verification). Never market data; output must not go under research-results/.
  tournament-smoke  the same for a cohort of the exploratory evidence tournament (--lane tournament|tournament-c1).
  run-tournament    one recorder pass for every tournament cohort, sharing one market-data fetch (the scheduled
                    workflow): --out-dir DIR gets each cohort's outbox{suffix}.json and report{suffix}.json and one
                    summary.md (cohort 0: no suffix; cohort 1: -c1).
  run-picks         one recorder pass of the live engine's picks (v3x-live-picks-v1, picks/README.md): --out-dir DIR
                    gets outbox.json, report.json, summary.md and view/picks/ (the track record rendered from the
                    verified ledger, which the workflow publishes on the picks-ledger branch).
  picks-smoke       DEMO end to end of the picks lane on the synthetic fixture (never market data).

`--lane tournament` selects the exploratory lane v3x-epa-tournament-v1 (cohort 0, docs/evidence/TOURNAMENT_LANE.md);
`--lane tournament-c1` its cohort 1, v3x-epa-tournament-c1-v1 (docs/evidence/TOURNAMENT_COHORT_1.md); `--lane picks`
the live engine's picks. `--store dry` runs a recorder pass against an in-memory ledger that is discarded: nothing is
posted (a rehearsal of the real data path before the owner activates a lane).
"""

from __future__ import annotations

import functools
import json
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path

import pandas as pd

from apex_worker.market.calendar import MarketCalendar, default_calendar
from apex_worker.research.forward import frozen as F
from apex_worker.research.forward import windows as W
from apex_worker.research.forward.health import health, markdown
from apex_worker.research.forward.recorder import EXIT, SEVERITY, FrozenLane, Recorder, producer_info
from apex_worker.research.forward.records import verify_chain
from apex_worker.research.forward.source import MarketSource, bars_through
from apex_worker.research.forward.store import IssueLedgerStore, MemoryGitHubAPI, RestGitHubAPI, StoreError
from apex_worker.timeutil import ET


def _tournament_issue_body(series_id: str, lane_flag: str, doc: str) -> str:
    return (f"Append-only ledger of the APEX evidence tournament (exploratory lane {series_id}; "
            "PROSPECTIVE_EXPLORATORY, never confirmatory): paper books only, no orders, no alerts, no group-actionable "
            "output. Records are written by the scheduled `apex-evidence-tournament` workflow and carry a sha256 hash "
            "chain; GitHub's comment timestamp is the independent anchor. Do not edit or delete comments. Verify with "
            f"`apex research forward-shadow verify --lane {lane_flag} --store github`. See {doc}.")


TOURNAMENT_ISSUE_BODIES = {
    "tournament": _tournament_issue_body("v3x-epa-tournament-v1", "tournament", "docs/evidence/TOURNAMENT_LANE.md"),
    "tournament-c1": _tournament_issue_body("v3x-epa-tournament-c1-v1", "tournament-c1",
                                            "docs/evidence/TOURNAMENT_COHORT_1.md"),
}
TOURNAMENT_ISSUE_BODY = TOURNAMENT_ISSUE_BODIES["tournament"]
ISSUE_BODY = ("Append-only ledger of the APEX forward shadow (research only: no orders, no alerts, no group-actionable "
              "output). Every record comment is written by the scheduled `apex-forward-shadow` workflow and carries a "
              "sha256 hash chain; GitHub's comment timestamp is the independent anchor. Do not edit or delete comments: "
              "an edited record fails verification. Verify with `apex research forward-shadow verify --store github`. "
              "See docs/discovery-v3/FORWARD_SHADOW.md.")
PICKS_ISSUE_BODY = ("Append-only ledger of the APEX live picks (exploratory lane v3x-live-picks-v1; "
                    "PROSPECTIVE_EXPLORATORY, never confirmatory): the would-be picks of an unvalidated engine, recorded "
                    "before any outcome exists. Research only: no orders, no alerts, not investment advice. Records are "
                    "written by the scheduled `apex-live-picks` workflow and carry a sha256 hash chain; GitHub's comment "
                    "timestamp is the independent anchor. Do not edit or delete comments. The readable track record is "
                    "picks/ on the picks-ledger branch. Verify with `apex research forward-shadow verify --lane picks "
                    "--store github`. See picks/README.md.")
PICKS_FIXTURE_FROM = date(2026, 8, 31)  # the synthetic fixture has picks and open picks in the following sessions
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
    return getattr(args, "lane", "frozen") in TOURNAMENT_ISSUE_BODIES


def _tournament_lane(name: str, source: str, decisions_from: date | None = None):
    from apex_worker.research.forward import tournament_lane as T

    cohort = T.LANES[name]
    if source == "fixture":
        return T.TournamentLane.fixture(decisions_from or cohort.decisions_from, cohort)
    return T.TournamentLane.for_cohort(cohort)


def _picks(args) -> bool:
    return getattr(args, "lane", "frozen") == "picks"


def _picks_lane(source: str, decisions_from: date | None = None, cal: MarketCalendar | None = None):
    from apex_worker.research.forward import picks_lane as PL

    if source == "fixture":
        return PL.PicksLane.fixture(decisions_from or PICKS_FIXTURE_FROM)
    return PL.PicksLane(earnings=PL.SecEarnings.from_env(cal or default_calendar()))


def _lane(args):
    if _tournament(args):
        return _tournament_lane(args.lane, args.source, args.decisions_from)
    if _picks(args):
        return _picks_lane(args.source, args.decisions_from)
    if args.source == "fixture":
        return FrozenLane.fixture(args.decisions_from or F.DECISIONS_FROM)
    return FrozenLane()


def _expected_config_sha(args, lane) -> str:
    if _tournament(args):
        from apex_worker.research.forward import tournament_lane as T

        return T.config_sha256(lane.config(lane.cohort.pinned_fingerprint))
    if _picks(args):
        from apex_worker.research.forward import picks_lane as PL

        return F.config_sha256(lane.config(PL.PINNED_FINGERPRINT))
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


def _context(args, cal: MarketCalendar):
    """(clock, source, api) for a run, or an exit code: the fixture emulation for DEMO runs, Alpaca otherwise."""
    if args.store == "github" and args.source != "alpaca":
        print("the GitHub ledger records the real lane only: use --source alpaca", file=sys.stderr)
        return 5
    if args.source == "alpaca" and args.now:
        print("--now is for fixture runs only; real runs use the wall clock", file=sys.stderr)
        return 5
    now0 = _parse_now(args.now) or datetime.now(UTC)
    clock = SimClock(now0) if args.source == "fixture" else _wall_clock
    if args.source == "fixture":
        frames = _fixture_frames(cal.add_sessions(now0.astimezone(ET).date(), 5))
        source = _fixture_source(frames, cal, clock)
        api = MemoryGitHubAPI(clock=clock) if args.store == "dry" else _file_api(Path(args.store_file), clock)
        return clock, source, api
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
    return clock, source, api


def _record_tournament(lane, lane_flag: str, api, source, cal, clock, outbox: Path | None, store_kind: str,
                       decide_only: bool):
    """One cohort's recorder pass: (report, report dict, summary markdown)."""
    from apex_worker.research.forward import tournament_lane as T

    store = IssueLedgerStore(api, lane.series_id, issue_body=TOURNAMENT_ISSUE_BODIES[lane_flag])
    rep = T.TournamentRecorder(lane, store, source, cal, clock=clock, producer=producer_info(), outbox=outbox,
                               decide_only=decide_only).run()
    out = rep.to_dict() | {"store": store_kind}
    md = _dry_banner(store_kind) + markdown(rep.health, out)
    try:
        md += T.books_summary_md(store.read().records)
    except StoreError as exc:
        md += f"\n_Tournament summary unavailable: {exc}_\n"
    return rep, out, md


def _dry_banner(store_kind: str) -> str:
    if store_kind != "dry":
        return ""
    return ("**DRY RUN: nothing was posted** (in-memory ledger, discarded). Set the repository variable that "
            "activates this lane to record for real.\n\n")


def _print_run(out: dict, rep) -> None:
    print(json.dumps({k: out[k] for k in ("series_id", "status", "exit_code", "messages", "owner_setup")} |
                     {"actions": [(a["record_type"], a["session"], a["seq"]) for a in out["actions"]],
                      "health_status": rep.health.get("status")}, indent=1))


def cmd_run(args) -> int:
    if _picks(args):
        print("the picks lane records with `run-picks --out-dir DIR` (it also renders the track record)", file=sys.stderr)
        return 5
    cal = default_calendar()
    ctx = _context(args, cal)
    if isinstance(ctx, int):
        return ctx
    clock, source, api = ctx
    lane = _lane(args)
    outbox = Path(args.outbox) if args.outbox and args.store != "dry" else None
    if _tournament(args):
        rep, out, md = _record_tournament(lane, args.lane, api, source, cal, clock, outbox, args.store,
                                          args.decide_only)
    else:
        store = IssueLedgerStore(api, lane.series_id, issue_body=ISSUE_BODY)
        rep = Recorder(lane, store, source, cal, clock=clock, producer=producer_info(), outbox=outbox,
                       decide_only=args.decide_only).run()
        out = rep.to_dict() | {"store": args.store}
        md = _dry_banner(args.store) + markdown(rep.health, out)
    if args.store == "file":
        Path(args.store_file).write_text(json.dumps(api.to_json(), indent=1, sort_keys=True))
    _write(args.report_json, json.dumps(out, indent=2, sort_keys=True) + "\n")
    _write(args.summary_md, md)
    _print_run(out, rep)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"status={rep.status}\nexit_code={rep.exit_code}\nhealth={rep.health.get('status')}\n")
    return rep.exit_code


# The pass's exit code when cohorts disagree: the most serious one, in the recorder's own severity order.
_RANK = {EXIT[name]: i for i, name in enumerate(SEVERITY)}


def cmd_run_tournament(args) -> int:
    """Every tournament cohort in one pass, sharing one market-data fetch (the scheduled workflow's step). Each cohort
    keeps its own series, chain, ledger issue, outbox and report; one summary covers them all."""
    from apex_worker.research.forward import tournament_lane as T

    cal = default_calendar()
    ctx = _context(args, cal)
    if isinstance(ctx, int):
        return ctx
    clock, source, api = ctx
    shared = T.SharedSource(source)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    codes, parts, outputs = [], [], []
    for flag, cohort in T.LANES.items():
        lane = _tournament_lane(flag, args.source, args.decisions_from)
        head = f"# Cohort {cohort.key[1:]}: `{lane.series_id}`\n\n"
        outbox = out_dir / f"outbox{cohort.file_suffix}.json" if args.store != "dry" else None
        try:
            rep, out, md = _record_tournament(lane, flag, api, shared, cal, clock, outbox, args.store, args.decide_only)
        except Exception as exc:  # one cohort's failure must not stop the others; the pass still fails (exit 1)
            traceback.print_exc()
            parts.append(head + f"**RUN ERROR** ({type(exc).__name__}): {exc}. Nothing further was recorded for this "
                         "cohort in this pass.\n")
            codes.append(1)
            outputs.append(f"status{cohort.file_suffix}=ERROR\nexit_code{cohort.file_suffix}=1\n")
            continue
        _write(str(out_dir / f"report{cohort.file_suffix}.json"), json.dumps(out, indent=2, sort_keys=True) + "\n")
        parts.append(head + md)
        _print_run(out, rep)
        codes.append(rep.exit_code)
        outputs.append(f"status{cohort.file_suffix}={rep.status}\nexit_code{cohort.file_suffix}={rep.exit_code}\n")
    if args.store == "file":
        Path(args.store_file).write_text(json.dumps(api.to_json(), indent=1, sort_keys=True))
    _write(str(out_dir / "summary.md"), "\n".join(parts))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write("".join(outputs))
    return max(codes, key=lambda c: _RANK.get(c, len(_RANK)), default=0)


def _picks_frames():
    from apex_worker.research.forward import picks_lane as PL

    return PL._golden_frames()  # the DEMO synthetic market (universe_frames_v2), on which the live engine has setups


def _render_picks(store: IssueLedgerStore, lane, cal: MarketCalendar, out: Path, now: datetime) -> bool:
    """Write view/picks/ from a fresh read of the ledger, only if every record and the chain verify."""
    from apex_worker.research.forward import picks_view as PV

    try:
        rr = store.read()
    except StoreError as exc:
        print(f"view not rendered: {exc}", file=sys.stderr)
        return False
    terminal = [r["session"] for r in rr.records if r["record_type"] in ("DECISION", "MISSED_OBSERVATION")]
    exp = [d.isoformat() for d in cal.sessions_between(lane.decisions_from, date.fromisoformat(max(terminal)))] \
        if terminal else []
    problems = rr.problems + verify_chain(rr.records, series_id=lane.series_id, lane=lane.lane, expected_sessions=exp)
    if problems or not rr.records:
        print(f"view not rendered: {'; '.join(problems[:3]) or 'no records'}", file=sys.stderr)
        return False
    files = PV.render(rr.records, rr.anchors, series_id=lane.series_id,
                      ledger_url=(rr.volumes[0].get("url") if rr.volumes else None), generated_at=W.iso(now))
    for rel, text in files.items():
        _write(str(out / rel), text)
    return True


def cmd_run_picks(args) -> int:
    """One recorder pass of the picks lane, then the track record rendered from the verified ledger (view/picks/)."""
    from apex_worker.research.forward import picks_lane as PL

    cal = default_calendar()
    out_dir = Path(args.out_dir)
    if "research-results" in out_dir.resolve().parts:
        print("run output may never be written under research-results/", file=sys.stderr)
        return 5
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.source == "fixture":
        now0 = _parse_now(args.now) or datetime.now(UTC)
        clock = SimClock(now0)
        source = _fixture_source(_picks_frames(), cal, clock)
        api = MemoryGitHubAPI(clock=clock) if args.store == "dry" else _file_api(Path(args.store_file), clock)
    else:
        ctx = _context(args, cal)
        if isinstance(ctx, int):
            return ctx
        clock, source, api = ctx
    lane = _picks_lane(args.source, args.decisions_from, cal)
    store = IssueLedgerStore(api, lane.series_id, issue_body=PICKS_ISSUE_BODY)
    outbox = out_dir / "outbox.json" if args.store != "dry" else None
    rep = PL.PicksRecorder(lane, store, source, cal, clock=clock, producer=producer_info(), outbox=outbox,
                           decide_only=args.decide_only).run()
    out = rep.to_dict() | {"store": args.store}
    view = _render_picks(store, lane, cal, out_dir / "view", clock())
    md = _dry_banner(args.store) + markdown(rep.health, out)
    try:
        md += PL.summary_md(store.read().records)
    except StoreError as exc:
        md += f"\n_Picks summary unavailable: {exc}_\n"
    md += "\nTrack record rendered for publication (picks/ on the picks-ledger branch).\n" if view and \
        args.store == "github" else ""
    if args.store == "file":
        Path(args.store_file).write_text(json.dumps(api.to_json(), indent=1, sort_keys=True))
    _write(str(out_dir / "report.json"), json.dumps(out | {"view_rendered": view}, indent=2, sort_keys=True) + "\n")
    _write(str(out_dir / "summary.md"), md)
    _print_run(out, rep)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"status={rep.status}\nexit_code={rep.exit_code}\nview={'true' if view else 'false'}\n")
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
    h = health(rr.records, rr.anchors, cal, datetime.now(UTC), lane.decisions_from, rr.problems, lane.reading)
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
    """DEMO: a tournament cohort on the synthetic fixture over a simulated calendar. Exit 0 only if it behaves."""
    from apex_worker.research.forward import tournament_lane as T

    cohort = T.LANES[args.lane]
    cal = default_calendar()
    out = Path(args.out)
    if "research-results" in out.resolve().parts:
        print("DEMO output may never be written under research-results/", file=sys.stderr)
        return 5
    out.mkdir(parents=True, exist_ok=True)
    start = cohort.decisions_from
    days = cal.sessions_between(start, cal.add_sessions(start, 9))
    frames = _fixture_frames(cal.add_sessions(days[-1], 3))
    clock = SimClock(at_et(days[0], 18, 12))
    api = MemoryGitHubAPI(clock=clock)
    lane = T.TournamentLane.fixture(start, cohort)
    fp = functools.cache(functools.partial(T.golden_fingerprint, cohort))
    log = []

    def run(when: datetime, note: str):
        clock.now = when
        rec = T.TournamentRecorder(lane, IssueLedgerStore(api, lane.series_id, issue_body=TOURNAMENT_ISSUE_BODIES[args.lane]),
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
    replayed = (T.tournament_state({s: v for r in outs for s, v in r["body"]["sessions"].items()}, cohort)
                if outs else None)
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
    (out / "summary.md").write_text(f"# Tournament cohort {cohort.key[1:]} DEMO smoke (synthetic fixture, never market "
                                    "data)\n\n"
                                    + "\n".join(f"- {k}: {'PASS' if v else 'FAIL'}" for k, v in checks.items())
                                    + "\n" + T.books_summary_md(rr.records))
    print(json.dumps(checks, indent=1))
    return 0 if all(checks.values()) else 1


def cmd_picks_smoke(args) -> int:
    """DEMO: the picks lane on the synthetic fixture over a simulated calendar, then its rendered track record. Exit 0
    only if the scenario behaves."""
    from apex_worker.research.forward import picks_lane as PL
    from apex_worker.research.forward import picks_view as PV
    from apex_worker.research.forward.records import publication_problems

    cal = default_calendar()
    out = Path(args.out)
    if "research-results" in out.resolve().parts:
        print("DEMO output may never be written under research-results/", file=sys.stderr)
        return 5
    out.mkdir(parents=True, exist_ok=True)
    start = PICKS_FIXTURE_FROM
    days = cal.sessions_between(start, cal.add_sessions(start, 9))
    final = date(2026, 10, 16)  # the fixture's last session: the earliest decisions have matured by then
    frames = _picks_frames()
    clock = SimClock(at_et(days[0], 19, 23))
    api = MemoryGitHubAPI(clock=clock)
    lane = PL.PicksLane.fixture(start)
    fp = functools.cache(PL.golden_fingerprint)
    log = []
    hide: dict = {}

    def run(when: datetime, note: str):
        clock.now = when
        store = IssueLedgerStore(api, lane.series_id, issue_body=PICKS_ISSUE_BODY)
        rep = PL.PicksRecorder(lane, store, _fixture_source(frames, cal, clock, hide), cal, clock=clock,
                               producer=producer_info() | {"note": "DEMO picks smoke"}, outbox=out / "outbox.json",
                               fingerprint_fn=fp, expected_fingerprint=fp()).run()
        log.append({"at": W.iso(when), "note": note, "status": rep.status, "exit_code": rep.exit_code,
                    "actions": [(a["record_type"], a["session"]) for a in rep.actions], "messages": rep.messages})
        print(f"[picks-smoke] {W.iso(when)} {note}: {rep.status} {log[-1]['actions']}", flush=True)

    for i, d in enumerate(days):
        if i == 4:
            continue  # no run that day: recorded as missed by the next run, never back-filled
        if i == 2:
            hide[d] = None  # the provider has not published the session yet at the first attempt
            run(at_et(d, 19, 23), "source pending")
            hide.clear()
            run(at_et(d, 22, 53), "retry: decision")
            continue
        run(at_et(d, 19, 23), "decision" + (" (retry: idempotent no-op follows)" if i == 0 else ""))
        if i == 0:
            run(at_et(d, 22, 53), "retry: idempotent no-op")
    run(at_et(final, 19, 23), "catch-up misses, a decision, then outcomes for the matured decisions")
    store = IssueLedgerStore(api, lane.series_id)
    rr = store.read()
    last = max(r["session"] for r in rr.records if r["record_type"] in ("DECISION", "MISSED_OBSERVATION"))
    exp = [d.isoformat() for d in cal.sessions_between(start, date.fromisoformat(last))]
    problems = rr.problems + verify_chain(rr.records, series_id=lane.series_id, lane=lane.lane, expected_sessions=exp)
    viewed = _render_picks(store, lane, cal, out / "view", clock())
    decisions = [r for r in rr.records if r["record_type"] == "DECISION"]
    outs = [r for r in rr.records if r["record_type"] == "OUTCOME"]
    labelled = [p for r in outs for p in r["body"]["plans"] if p.get("outcome") not in (None, "NO_DATA")]
    readme = (out / "view" / "picks" / "README.md").read_text() if viewed else ""
    csv_rows = (out / "view" / "picks" / "picks.csv").read_text().count("\n") - 1 if viewed else -1
    checks = {
        "genesis_first": rr.records[0]["record_type"] == "SERIES_GENESIS",
        "decisions": len(decisions) >= 9,
        "pending_recorded": any(r["record_type"] == "OBSERVATION_ATTEMPT_FAILED" for r in rr.records),
        "missed_recorded": any(r["record_type"] == "MISSED_OBSERVATION" and r["session"] == days[4].isoformat()
                               for r in rr.records),
        "no_duplicate_on_retry": sum(1 for r in decisions if r["session"] == days[0].isoformat()) == 1,
        "picks_recorded": any(e["kind"] == "pick" for r in decisions for e in r["body"]["plans"]),
        "open_picks_tracked": any(r["body"]["open_picks"] for r in decisions),
        "controls_frozen_at_decision": all(e["controls"]["reserve"] for r in decisions for e in r["body"]["plans"]),
        "outcomes": len(outs) >= 5 and bool(labelled), "chain_verifies": not problems,
        "exploratory_labels": all(r["body"].get("evidence_tier") == "PROSPECTIVE_EXPLORATORY"
                                  for r in rr.records if r["record_type"] in ("DECISION", "OUTCOME")),
        "no_raw_bar_fields": not any(publication_problems(r, env={}) for r in rr.records),
        "view_rendered": viewed and PV.DISCLAIMER in readme and "## Track record: picks" in readme
        and csv_rows == sum(len(r["body"]["plans"]) for r in decisions),
    }
    (out / "ledger.records.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rr.records))
    (out / "smoke_report.json").write_text(json.dumps({"data_class": "SYNTHETIC_FIXTURE_DEMO", "checks": checks,
                                                       "runs": log}, indent=1, sort_keys=True))
    (out / "summary.md").write_text("# Live picks DEMO smoke (synthetic fixture, never market data)\n\n"
                                    + "\n".join(f"- {k}: {'PASS' if v else 'FAIL'}" for k, v in checks.items())
                                    + "\n" + PL.summary_md(rr.records))
    print(json.dumps(checks, indent=1))
    return 0 if all(checks.values()) else 1


def register(rsub) -> None:
    fs = rsub.add_parser("forward-shadow", help="prospective forward shadow of the frozen protocol-2 arms (research only)")
    sub = fs.add_subparsers(dest="forward_cmd", required=True)

    def common(p, store_default="github"):
        p.add_argument("--lane", choices=["frozen", *TOURNAMENT_ISSUE_BODIES, "picks"], default="frozen")
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
    ts = sub.add_parser("tournament-smoke", help="DEMO end-to-end of a tournament cohort (never market data)")
    ts.add_argument("--out", required=True)
    ts.add_argument("--lane", choices=list(TOURNAMENT_ISSUE_BODIES), default="tournament")
    ts.set_defaults(func=cmd_tournament_smoke)
    rt = sub.add_parser("run-tournament", help="one recorder pass for every tournament cohort (one shared data fetch)")
    rt.add_argument("--store", choices=["github", "file", "dry"], default="github")
    rt.add_argument("--store-file", default="forward-shadow-ledger.json", help="ledger file for --store file (DEMO)")
    rt.add_argument("--source", choices=["alpaca", "fixture"], default="alpaca")
    rt.add_argument("--decisions-from", type=date.fromisoformat, default=None, help="fixture lanes only")
    rt.add_argument("--out-dir", required=True, help="outbox, report and summary files of every cohort")
    rt.add_argument("--now", help="ISO instant; fixture runs only")
    rt.add_argument("--decide-only", action="store_true", help="skip outcome labelling this run")
    rt.set_defaults(func=cmd_run_tournament)
    rp = sub.add_parser("run-picks", help="one recorder pass of the live engine's picks, then its rendered track record")
    rp.add_argument("--store", choices=["github", "file", "dry"], default="github")
    rp.add_argument("--store-file", default="forward-shadow-ledger.json", help="ledger file for --store file (DEMO)")
    rp.add_argument("--source", choices=["alpaca", "fixture"], default="alpaca")
    rp.add_argument("--decisions-from", type=date.fromisoformat, default=None, help="fixture lane only")
    rp.add_argument("--out-dir", required=True, help="outbox.json, report.json, summary.md and view/picks/")
    rp.add_argument("--now", help="ISO instant; fixture runs only")
    rp.add_argument("--decide-only", action="store_true", help="skip outcome labelling this run")
    rp.set_defaults(func=cmd_run_picks)
    ps = sub.add_parser("picks-smoke", help="DEMO end-to-end of the picks lane (never market data)")
    ps.add_argument("--out", required=True)
    ps.set_defaults(func=cmd_picks_smoke)

