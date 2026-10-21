"""Drive the connected-mode Pi code against a REAL APEX server (apps/web/tests/integration/pi-agent.test.ts).

Each step runs worker jobs through the production wiring (`build_worker`, the same objects `apex worker` uses) and
prints ONE JSON object on stdout. All Pi state lives in APEX_STATE_DIR, so consecutive invocations behave like
worker restarts. `--now` pins the Pi clock (the DEMO replay time) so expiry checks are deterministic. Test use only:
it simulates the bot's delivery by marking local outbox rows sent, because no Discord gateway runs here.

    uv run --no-sync python -I tests/support/real_server_driver.py STEP [--now ISO]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from apex_worker.agent.client import AgentHTTPError, AgentUnavailable
from apex_worker.config import WorkerConfig
from apex_worker.notifications import intents
from apex_worker.runtime.worker import build_worker
from apex_worker.timeutil import ReplayClock, iso_utc, parse_utc


def _worker(now: datetime):
    worker = build_worker(WorkerConfig.load())
    clock = ReplayClock(now)
    worker.clock = clock
    worker.model_sync.clock = clock.now
    return worker


def step_heartbeat(worker, now: datetime) -> dict:
    worker.start()  # GET /config
    worker.job_heartbeat(now)  # POST /heartbeat
    s = worker.server
    return {
        "device_status": worker.device_status,
        "server_component": worker.status.get("server"),
        "config": {"intraday_minutes": s.intraday_minutes, "max_slots": s.max_slots, "model_poll_minutes": s.model_poll_minutes,
                   "strategies": s.strategies, "budgets": s.budgets, "training": s.training,
                   "risk_min_quality": s.risk.get("min_quality"), "compliance": s.compliance,
                   "raw_keys": sorted(s.raw)},
        "secret_versions": s.raw.get("secret_versions"),
        # X25519 envelope fetched, decrypted and acked when the heartbeat announces a newer version (never printed)
        "market_credentials": {"status": worker.secrets.status, "version": worker.secrets.version,
                               "key_id_suffix": (worker.secrets.credentials.key_id[-4:]
                                                 if worker.secrets.credentials else None)} if worker.secrets else None,
        "skew_seconds": worker.client.skew_seconds,
    }


def step_work(worker, now: datetime) -> dict:
    worker.start()
    worker.job_work(now)  # GET /work -> bridge ingest; ack pass
    monitored = worker.sink.monitored_positions()  # GET /positions per member (server PositionSummary -> ledger)
    rows = worker.local.query("SELECT event_key, server_outbox_id, local_outbox_id, route_type, acked_state "
                              "FROM agent_outbox_bridge ORDER BY event_key")
    return {
        "bridge_component": worker.status.get("outbox_bridge"),
        "work_positions": worker.sink._work_positions,
        "monitored": [{"user_id": m.user_id, "position_id": m.position_id, "symbol": m.symbol,
                       "state": m.ledger_state.state.value, "total_quantity": str(m.ledger_state.total_quantity)}
                      for m in monitored],
        "missing_user_ids": worker.sink.missing_user_ids,
        "bridge": [dict(r) for r in rows],
        "local_outbox": [{"id": r.id, "event_key": r.event_key, "route": r.route, "state": r.state}
                         for r in worker.local.outbox_rows()],
    }


def step_deliver(worker, now: datetime) -> dict:
    """What the bot does: deliver every pending local row (DMs too), then the worker's ack pass reports it."""
    worker.start()
    delivered, renderable = [], {}
    for i, r in enumerate(worker.local.outbox_rows()):
        renderable[r.event_key] = worker.bridge._rendered(json.dumps(r.payload)) is not None  # positive control
        if r.state == "pending":
            worker.local.ack_outbox(r.id, "delivered", now, discord_message_id=str(1300000000000000000 + i))
            delivered.append(r.event_key)
    acks = worker.bridge.ack_pass(now)
    rows = worker.local.query("SELECT event_key, route_type, acked_state FROM agent_outbox_bridge ORDER BY event_key")
    return {"delivered": delivered, "renderable": renderable, "acks": acks, "bridge": [dict(r) for r in rows]}


def step_models(worker, now: datetime) -> dict:
    worker.start()
    rep = worker.model_sync.poll()  # GET models/manifest -> GET models/{id}/artifact -> install -> POST activation
    active = worker.model_sync.active_models()
    return {
        "report": {"checked": rep.checked, "activated": rep.activated, "rejected": rep.rejected,
                   "rolled_back": rep.rolled_back, "unchanged": rep.unchanged, "registry_ok": rep.registry_ok,
                   "detail": rep.detail},
        "active": {sid: a.model_id for sid, a in active.items()},
        "probe": {sid: [round(float(x), 6) for x in a.model.predict([{}])[0]] for sid, a in active.items()},
        "local_outbox": [{"event_key": r.event_key, "kind": r.kind, "route": r.route} for r in worker.local.outbox_rows()],
        "spool_pending": worker.client.spool.pending_count() if worker.client and worker.client.spool else 0,
    }


def step_notice(worker, now: datetime) -> dict:
    worker.start()
    intent = intents.system_alert("REGISTRY_UNREACHABLE", "Model registry unreachable", "integration test notice",
                                  "Nothing to do.", now, worker.is_demo, "pi-agent-test")
    queued = worker.notify(intent, now)  # NoticeRouter: POST /notices first, local outbox only as fallback
    refused = None
    bad = {**intent, "event_key": intent["event_key"] + ":bad", "kind": "NEW_SIGNAL"}
    try:
        worker.client.post("notices", {"intents": [bad]})
    except AgentHTTPError as exc:
        refused = {"status": exc.status, "code": exc.code}
    return {"queued": queued, "route": worker.notify.last_route, "event_key": intent["event_key"], "refused": refused,
            "local_outbox": [r.event_key for r in worker.local.outbox_rows()]}


def step_engine(worker, now: datetime, sessions: int) -> dict:
    """The real engine on the deterministic DEMO market, publishing through the worker's RemoteSink: scans, signals,
    lifecycle events, observations and member assessments go to the server exactly as on a live Pi."""
    import asyncio
    import tempfile
    from pathlib import Path

    from apex_worker.demo.flow import LocalDemo

    worker.start()  # GET /config, GET /signals/open (the server's open signals seed the engine's lifecycle view)
    worker.job_work(now)  # monitored member positions (+ user_id) for the assessor
    days, d = [], now.date()
    for _ in range(sessions):
        d = worker.cal.next_session(d)
        days.append(d)
    with tempfile.TemporaryDirectory() as tmp:
        demo = LocalDemo(Path(tmp) / "engine.sqlite3", days[0], days[-1])
        demo.sink = worker.sink
        demo.engine.sink = worker.sink
        for day in days:
            asyncio.run(demo._session(day))  # intraday checks + end-of-day scan, as the worker's jobs run them
            worker.sink.flush_assessments()
            obs = [q.to_contract(demo.clock.now(), True) for q in demo.engine.cache.all().values()]
            if obs:
                worker.sink.post_observations(obs)
        published = [line.split()[-1] for line in demo.log if " PUBLISHED " in line]
    return {"sessions": [x.isoformat() for x in days], "published": published,
            "open_signals": sorted(s["id"] for s, _ in worker.sink.open_signals()),
            "spool": {"pending": worker.client.spool.pending_count(), "rejected": worker.client.spool.rejected_count()}}


def step_bot(worker, now: datetime, members: list[str], guild: str) -> dict:
    """The connected bot backends (what /apex_entry, /apex_correct, /apex_positions, /apex_notifications and
    /apex_delete_my_data call): ledger writes, owner-scoped history, member prefs and deletion on the server."""
    from apex_worker.discord_bot.remote_backend import RemoteMemberBackend, RemotePositionBackend
    from apex_worker.services.positions import PositionNotFound

    seeded, other, fresh = members
    pb = RemotePositionBackend(worker.client, guild_id=guild, cache_seconds=0)
    mb = RemoteMemberBackend(worker.local, worker.client, guild_id=guild, positions=pb)
    out: dict = {}
    mine = pb.positions(seeded, include_closed=True)  # each row validated against the PositionSummary contract
    out["seeded_positions"] = len(mine)
    out["seeded_events"] = pb.events(seeded, mine[0]["position_id"]) if mine else []
    theirs = pb.positions(other, include_closed=True)
    try:
        pb.events(seeded, theirs[0]["position_id"])
        out["cross_member"] = "VISIBLE"
    except PositionNotFound:
        out["cross_member"] = "not_found"
    sig = sorted(worker.sink.open_signals(), key=lambda x: x[0]["created_at"])[0][0]
    key = f"itest-open-{sig['id'][-12:]}"
    fill = {"price": "101.25", "quantity": "4", "fees": "0.50", "fill_at": iso_utc(now)}
    first = pb.record(fresh, {"client_idempotency_key": key, "event_type": "OPEN", "signal_id": sig["id"],
                              "symbol": sig["symbol"], "source": "discord", "fill": fill}, now)
    again = pb.record(fresh, {"client_idempotency_key": key, "event_type": "OPEN", "signal_id": sig["id"],
                              "symbol": sig["symbol"], "source": "discord", "fill": fill}, now)
    pid = first.position["position_id"]
    out["record"] = {"first": first.status, "again": again.status, "same_position": again.position["position_id"] == pid}
    out["position_for_key"] = pb.position_for_key(fresh, key) == pid
    open_event = pb.events(fresh, pid)[0]
    corr = pb.record(fresh, {"client_idempotency_key": f"itest-fix-{sig['id'][-12:]}", "event_type": "CORRECT",
                             "position_id": pid, "source": "discord",
                             "correction": {"target_event_id": open_event["event_id"], "void": False,
                                            "replacement": {**fill, "price": "100.75"}, "reason": "typo"}}, now)
    out["corrected"] = {"status": corr.status, "avg_cost": corr.position.get("avg_cost"),
                        "events": pb.events(fresh, pid)}
    uid = worker.local.ensure_member(fresh, now)
    mb.set_dm_opt_in(uid, True)
    out["deleted"] = mb.delete_data(uid)
    out["after_delete"] = pb.positions(fresh, include_closed=True)
    out["member_spool"] = worker.local.spool_pending_count()
    return out


STEPS = {"heartbeat": step_heartbeat, "work": step_work, "deliver": step_deliver, "models": step_models,
         "notice": step_notice, "engine": step_engine, "bot": step_bot}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=sorted(STEPS))
    ap.add_argument("--now", help="pin the Pi clock (ISO UTC); default: wall clock")
    ap.add_argument("--sessions", type=int, default=3, help="engine: sessions to run after --now")
    ap.add_argument("--members", default="", help="bot: SEEDED,OTHER,FRESH Discord user ids")
    ap.add_argument("--guild", default="", help="bot: Discord guild id")
    args = ap.parse_args()
    now = parse_utc(args.now) if args.now else datetime.now(UTC).replace(microsecond=0)
    worker = _worker(now)
    try:
        fn = STEPS[args.step]
        if fn is step_engine:
            out = fn(worker, now, args.sessions)
        elif fn is step_bot:
            out = fn(worker, now, args.members.split(","), args.guild)
        else:
            out = fn(worker, now)
    except (AgentHTTPError, AgentUnavailable) as exc:
        out = {"error": type(exc).__name__, "code": getattr(exc, "code", None), "status": getattr(exc, "status", None)}
    finally:
        worker.close()
    print(json.dumps({"step": args.step, "now": iso_utc(now), **out}, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
