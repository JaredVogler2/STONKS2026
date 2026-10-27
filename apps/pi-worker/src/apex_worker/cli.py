"""`apex` command-line interface (Raspberry Pi worker, research lab and DEMO tooling)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def cmd_doctor(args) -> int:
    from apex_worker.config import WorkerConfig
    from apex_worker.operations.doctor import render, run_checks

    checks = run_checks(WorkerConfig.load(Path(args.env) if args.env else None), network=args.network)
    print(render(checks))
    return 1 if any(c.status == "FAIL" for c in checks) else 0


def cmd_demo(args) -> int:
    from apex_worker.demo.flow import main

    res = main(Path(args.out))
    print(
        f"DEMO complete: {len(res.signals)} signals, {len(res.transcript)} Discord payloads (dry run), "
        f"{res.deferred_private} private notifications held for members with DMs off."
    )
    print(f"Summary: {Path(args.out) / 'demo_summary.json'}")
    return 0


def cmd_ingest(args) -> int:
    from apex_worker.config import WorkerConfig
    from apex_worker.market.ingest import run_ingest

    cfg = WorkerConfig.load()
    report = asyncio.run(
        run_ingest(
            args.mode,
            cfg,
            dry_run=args.dry_run,
            symbols=args.symbols or None,
            asof=date.fromisoformat(args.date) if args.date else None,
        )
    )
    _print(report)
    return 0 if report.get("status") in ("ok", "dry_run") else 2


def cmd_features(args) -> int:
    from apex_worker.demo.synthetic import generate
    from apex_worker.features.panel import panel_from_synthetic
    from apex_worker.signals.candidates import CandidateEngine

    if args.mode != "fixture":
        print("features build in live mode runs inside the worker; use --mode fixture here", file=sys.stderr)
        return 2
    market = generate()
    panel = panel_from_synthetic(market, lookback=None if args.full else 420)
    import pandas as pd

    day = pd.Timestamp(args.date)
    res = CandidateEngine().scan(panel, day)
    rows = [
        {
            "symbol": c["symbol"],
            "strategy": c["strategy_id"],
            "quality": c["quality_score"],
            "gate": c["gate_status"],
            "reasons": c["reason_codes"][:4],
            "groups": c["group_scores"],
            "positive": [e["code"] for e in c["evidence"]["positive"]],
            "negative": [e["code"] for e in c["evidence"]["negative"]],
            "feature_hash": c["feature_snapshot_hash"][:12],
        }
        for c in res.candidates[: args.top]
    ]
    _print(
        {
            "session": res.session,
            "asof_at": res.asof_at,
            "regime": res.regime,
            "stats": res.stats.to_dict(),
            "no_trade_reason": res.no_trade_reason,
            "candidates": rows,
            "is_demo": True,
        }
    )
    return 0


def cmd_train(args) -> int:
    from apex_worker.agent.crypto import load_signing_key
    from apex_worker.research.pipeline import run_training

    key = None
    if args.sign:
        raw = os.environ.get(args.key_env)
        if not raw:
            print(f"--sign requires the signing key in ${args.key_env} (GitHub Environment secret)", file=sys.stderr)
            return 2
        key = load_signing_key(raw)
        os.environ.pop(args.key_env, None)  # held in memory only; never inherited by anything this process starts
    champions = (
        json.loads(Path(args.champions).read_text()) if args.champions and Path(args.champions).exists() else None
    )
    loader, info = None, None
    if args.source == "alpaca":  # bounded, incremental, checksummed history (apex_worker.market.history)
        from apex_worker.release.cli import alpaca_loader_from_env

        loader, info = alpaca_loader_from_env(args)
    res = run_training(
        args.source,
        Path(args.out),
        strategies=args.strategies or None,
        code_sha=os.environ.get("GITHUB_SHA", args.code_sha),
        signing_key=key,
        signer_key_version=args.key_version,
        champions=champions,
        is_demo=args.source != "alpaca",
        loader=loader,
        bias_warnings=info.warnings if info else None,
    )
    if info is not None:
        (Path(args.out) / "history.json").write_text(json.dumps(info.to_dict(), indent=2, sort_keys=True) + "\n")
    if not (Path(args.out) / "report.json").exists():  # no_data before any output: still leave evidence
        from apex_worker.research.pipeline import write_outputs

        write_outputs(res, Path(args.out), os.environ.get("GITHUB_SHA", args.code_sha), datetime.now(UTC))
    print(f"training status={res.status} data={res.data_source} candidates={len(res.candidates)} seconds={res.seconds}")
    if res.detail:
        print(res.detail)
    return 0 if res.status in ("succeeded", "no_data") else 1


def cmd_model(args) -> int:
    from apex_worker.config import WorkerConfig
    from apex_worker.models.manifest import ManifestRejected, verify_release
    from apex_worker.models.store import ModelStore
    from apex_worker.timeutil import iso_utc

    cfg = WorkerConfig.load()
    now = iso_utc(datetime.now(UTC))
    if args.action == "verify":
        manifest = json.loads(Path(args.manifest).read_text())
        artifact = Path(args.artifact).read_bytes()
        try:
            verify_release(manifest, artifact, cfg.model_trusted_keys, now_iso=now, release_mode=cfg.release_mode)
        except ManifestRejected as exc:
            print(f"REJECTED {exc.code}: {exc.detail}")
            return 1
        print("VERIFIED")
        return 0
    root = cfg.state_dir / "models" / "strategies"  # same per-strategy layout the worker's model sync uses
    if args.action == "status":
        out = {}
        for d in sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []:
            st = ModelStore(d, cfg.model_trusted_keys, cfg.release_mode)
            act = st.load_active(now)
            out[d.name] = {"current": st.current_id(), "previous": st.previous_id(), "loaded": act.model_id if act else None}
        _print(out)
        return 0
    if args.action == "install":
        manifest = json.loads(Path(args.manifest).read_text())
        store = ModelStore(root / str(manifest.get("strategy_id", "unknown")), cfg.model_trusted_keys, cfg.release_mode)
        try:
            active = store.install(manifest, Path(args.artifact).read_bytes(), now)
        except ManifestRejected as exc:
            print(f"REJECTED {exc.code}: {exc.detail} (previous model kept: {store.current_id()})")
            return 1
        print(f"ACTIVE {active.model_id}")
        return 0
    if not args.strategy:
        print("rollback needs --strategy STRATEGY_ID", file=sys.stderr)
        return 2
    store = ModelStore(root / args.strategy, cfg.model_trusted_keys, cfg.release_mode)
    back = store.rollback(now)
    print(f"ACTIVE {back.model_id}" if back else "no previous model to roll back to")
    return 0 if back else 1


def cmd_keygen(args) -> int:
    import base64
    import secrets

    from apex_worker.agent.canonical import b64url

    if args.kind == "hmac":
        print("AGENT_HMAC_SECRET=" + base64.b64encode(secrets.token_bytes(32)).decode())
        print(
            "# Put this SAME value in the Pi .env (chmod 600) and in Vercel env (Production + Preview). Never commit it."
        )
    elif args.kind == "session":
        print("APEX_SESSION_SECRET=" + base64.b64encode(secrets.token_bytes(32)).decode())
    elif args.kind == "encryption":
        print("APEX_CONFIG_ENCRYPTION_KEY=" + base64.b64encode(secrets.token_bytes(32)).decode())
        print("APEX_CONFIG_ENCRYPTION_KEY_VERSION=1")
    elif args.kind == "signing":
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        from apex_worker.agent.crypto import public_from_signing_key

        key = Ed25519PrivateKey.generate()
        from cryptography.hazmat.primitives import serialization

        raw = key.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
        )
        print("APEX_MODEL_SIGNING_KEY=" + b64url(raw) + "   # GitHub Environment secret ONLY (apex-model-release)")
        print('APEX_MODEL_TRUSTED_KEYS={"1":"' + public_from_signing_key(key) + '"}   # public: Pi .env + Vercel env')
    return 0


def cmd_calendar(args) -> int:
    from apex_worker.market.calendar import default_calendar

    Path(args.out).write_text(json.dumps(default_calendar().export(), separators=(",", ":")) + "\n")
    print(f"wrote {args.out}")
    return 0


def cmd_seed(args) -> int:
    from apex_worker.demo.web_seed import export_web_seed

    path = export_web_seed(Path(args.out), Path(args.work))
    print(f"wrote {path}")
    return 0


def cmd_replay(args) -> int:
    from apex_worker.demo.replay import SCENARIOS, run_all, run_scenario

    out = Path(args.out) if args.out else None
    if args.scenario and args.scenario not in SCENARIOS:
        print(f"unknown scenario {args.scenario!r}; choose one of: {', '.join(SCENARIOS)}", file=sys.stderr)
        return 2
    results = [run_scenario(args.scenario, out)] if args.scenario else run_all(out)
    ok = all(r["passed"] for r in results)
    for r in results:
        print(f"[{'PASS' if r['passed'] else 'FAIL'}] {r['name']}: {r['summary']}")
        for f in r.get("failures", []):
            print(f"     - {f}")
    if results:
        print(f"Reports: {Path(results[0]['report']).parent}")
    return 0 if ok else 1


def cmd_bot(args) -> int:
    from apex_worker.discord_bot.app import run_bot

    return run_bot(dry_run=args.dry_run)


def cmd_worker(args) -> int:
    from apex_worker.runtime.worker import run_worker

    return run_worker(once=args.once)


def cmd_pair(args) -> int:
    from apex_worker.agent.pairing import pair_device

    return pair_device(args.code, server=args.server, device_name=args.name, wait_seconds=args.wait,
                       new_identity=args.new_identity)


def cmd_migrate_starter(args) -> int:
    from apex_worker.operations.starter_migration import migrate_starter

    _print(migrate_starter(Path(args.starter_db), dry_run=not args.apply))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="apex", description="APEX worker - research and alerting only; no order execution")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor", help="check environment, storage, calendar, credentials (never prints secrets)")
    d.add_argument("--network", action="store_true")
    d.add_argument("--env")
    d.set_defaults(func=cmd_doctor)
    dm = sub.add_parser("demo", help="offline DEMO journey (no keys)")
    dm.add_argument("action", choices=["run"])
    dm.add_argument("--out", default=".apex-local/demo")
    dm.set_defaults(func=cmd_demo)
    ing = sub.add_parser("ingest", help="market data ingest")
    ing.add_argument("--mode", choices=["fixture", "alpaca"], default="fixture")
    ing.add_argument("--dry-run", action="store_true")
    ing.add_argument("--symbols", nargs="*")
    ing.add_argument("--date")
    ing.set_defaults(func=cmd_ingest)
    ft = sub.add_parser("features", help="feature/candidate build")
    ft.add_argument("action", choices=["build"])
    ft.add_argument("--date", required=True)
    ft.add_argument("--mode", choices=["fixture"], default="fixture")
    ft.add_argument("--top", type=int, default=15)
    ft.add_argument("--full", action="store_true")
    ft.set_defaults(func=cmd_features)
    tr = sub.add_parser("train", help="unattended training run (GitHub runner or local sandbox)")
    tr.add_argument("--source", choices=["synthetic", "redteam", "alpaca"], default="synthetic")
    tr.add_argument("--out", default=".apex-local/train")
    tr.add_argument("--strategies", nargs="*")
    tr.add_argument("--sign", action="store_true")
    tr.add_argument("--key-env", default="APEX_MODEL_SIGNING_KEY")
    tr.add_argument("--key-version", type=int, default=1)
    tr.add_argument("--champions")
    tr.add_argument("--code-sha", default="local")
    from apex_worker.personal.cli import register as register_personal_commands
    from apex_worker.release.cli import add_history_args
    from apex_worker.release.cli import register as register_release_commands
    from apex_worker.research.realeq.cli import register as register_research_commands

    add_history_args(tr)  # --history-cache/--history-years/--history-feed/--history-max-requests (alpaca source)
    tr.set_defaults(func=cmd_train)
    md = sub.add_parser("model", help="verify/install/rollback/status of signed models")
    md.add_argument("action", choices=["verify", "install", "rollback", "status"])
    md.add_argument("--manifest")
    md.add_argument("--artifact")
    md.add_argument("--strategy", help="strategy id (rollback)")
    md.set_defaults(func=cmd_model)
    kg = sub.add_parser("keygen", help="generate secrets locally (printed once; store them yourself)")
    kg.add_argument("kind", choices=["hmac", "session", "encryption", "signing"])
    kg.set_defaults(func=cmd_keygen)
    cal = sub.add_parser("calendar", help="export the XNYS calendar JSON for the web app")
    cal.add_argument("--out", default="../../packages/quant/calendar/xnys.json")
    cal.set_defaults(func=cmd_calendar)
    sd = sub.add_parser("export-web-seed", help="regenerate the deterministic DEMO seed for the dashboard")
    sd.add_argument("--out", default="../../fixtures/demo/web-seed.json")
    sd.add_argument("--work", default=".apex-local/seed")
    sd.set_defaults(func=cmd_seed)
    rp = sub.add_parser("replay", help="run deterministic replay scenarios (DEMO fixtures, no network, no keys)")
    rg = rp.add_mutually_exclusive_group(required=True)
    rg.add_argument("--all", action="store_true", help="run every scenario")
    rg.add_argument("--scenario", help="run one scenario by name")
    rp.add_argument("--out", help="report directory (default <state_dir>/replay)")
    rp.set_defaults(func=cmd_replay)
    bt = sub.add_parser("bot", help="run the Discord gateway bot")
    bt.add_argument("--dry-run", action="store_true")
    bt.set_defaults(func=cmd_bot)
    wk = sub.add_parser("worker", help="run the scanner/monitor worker")
    wk.add_argument("--once", action="store_true")
    wk.set_defaults(func=cmd_worker)
    pr = sub.add_parser("pair", help="pair this Pi with the dashboard using a one-time code")
    pr.add_argument("code")
    pr.add_argument("--server", help="dashboard base URL, e.g. https://apex-xyz.vercel.app (or APEX_API_BASE_URL)")
    pr.add_argument("--name", default="apex-pi")
    pr.add_argument("--wait", type=float, default=0, help="seconds to wait for owner approval (default: do not wait)")
    pr.add_argument("--new-identity", action="store_true", help="new keys + agent id (after a revocation)")
    pr.set_defaults(func=cmd_pair)
    ms = sub.add_parser("migrate-starter", help="import raspberry-pi/bot starter entries (dry run by default)")
    ms.add_argument("--starter-db", default=str(Path.home() / "STONKS2026/raspberry-pi/bot/data/positions.sqlite3"))
    ms.add_argument("--apply", action="store_true")
    ms.set_defaults(func=cmd_migrate_starter)
    register_release_commands(sub)  # CI: `apex release ...`, `apex drift`, `apex budget check`
    register_research_commands(sub)  # research only: `apex research real-equities`, `apex research budget-check`
    register_personal_commands(sub)  # `apex personal run|smoke`: the owner's Discord alerts (no orders)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
