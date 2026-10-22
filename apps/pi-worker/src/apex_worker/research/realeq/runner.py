"""`apex research real-equities` orchestration: inputs -> credentials -> fresh SIP download -> data-quality inventory
-> FROZEN manifest (boundaries, hashes, protocol) -> H1-H8 + baselines -> reports. execution_log.md is always written.

Exit codes: 0 completed, 2 BLOCKED (Alpaca secrets missing), 3 SOURCE_INVALID, 4 INSUFFICIENT_HISTORY, 5 bad input,
1 unexpected error. A non-completed run never writes strategy results and never claims a backtest.
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import tempfile
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from apex_worker.features.panel import build_panel
from apex_worker.market.calendar import default_calendar
from apex_worker.research.dataset import MODEL_FEATURES, data_hash
from apex_worker.research.evaluate import POLICY_VERSION
from apex_worker.research.realeq import report as R
from apex_worker.research.realeq.data import (
    BLOCKED_BANNER,
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_INSUFFICIENT,
    STATUS_SOURCE_INVALID,
    RunStop,
    credentials_from_env,
    default_spec,
    fetch,
    quality_inventory,
)
from apex_worker.research.realeq.protocol import (
    PROTOCOL,
    PROTOCOL_VERSION,
    check_years,
    compute_boundaries,
    protocol_sha256,
    sanitize_experiment_id,
)
from apex_worker.research.training_data import BENCHMARK, MEMBERS, SECTOR_ETFS, universe_meta
from apex_worker.signals.strategies.library import STRATEGIES

EXIT = {STATUS_COMPLETED: 0, STATUS_BLOCKED: 2, STATUS_SOURCE_INVALID: 3, STATUS_INSUFFICIENT: 4, "INVALID_INPUT": 5,
        "FAILED": 1}
REPO_ROOT = Path(__file__).resolve().parents[6]
OWNER_SETUP = (
    "One-time owner setup (GitHub UI, no laptop needed): Settings → Environments → New environment "
    "`real-equities-research`; set Deployment branches to `main` only (optionally add yourself as required reviewer); "
    "add environment secrets `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY` from an Alpaca account's read-only market-data "
    "keys (paper keys are fine; never paste them into issues, chat or files). Then Actions → apex-real-equities-research → "
    "Run workflow on `main`. Never reuse a connected-tool (e.g. ChatGPT connector) credential for CI."
)


@dataclass
class RunConfig:
    source: str  # alpaca | fixture (DEMO)
    experiment_id: str
    out_dir: Path
    years: int = 8
    universe: str = "fixed78"
    time_start: date | None = None
    time_end: date | None = None
    max_requests: int = 300
    env: Mapping[str, str] | None = None
    now: datetime | None = None
    adapter_factory: Callable[[], object] | None = None  # tests only (fake transport)
    progress: Callable[[str], None] = field(default=lambda _m: None)
    protocol: str = PROTOCOL_VERSION  # realeq-protocol-2 is opt-in only (no silent default migration)


@dataclass
class RunOutcome:
    status: str
    exit_code: int
    out_dir: Path
    reason: str = ""


def _git(*args: str) -> str | None:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, timeout=10, cwd=REPO_ROOT,  # noqa: S603,S607
                              check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _runner(env: Mapping[str, str]) -> dict:
    server, repo, run_id = env.get("GITHUB_SERVER_URL"), env.get("GITHUB_REPOSITORY"), env.get("GITHUB_RUN_ID")
    return {
        "run_id": run_id, "run_attempt": env.get("GITHUB_RUN_ATTEMPT"),
        "run_url": f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else None,
        "workflow": env.get("GITHUB_WORKFLOW"), "event": env.get("GITHUB_EVENT_NAME"), "ref": env.get("GITHUB_REF"),
        "repository": repo, "runner_os": env.get("RUNNER_OS"), "runner_arch": env.get("RUNNER_ARCH"),
        "image_os": env.get("ImageOS"), "image_version": env.get("ImageVersion"),
        "python": platform.python_version(), "platform": platform.platform(terse=True),
    }


def _sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def run(cfg: RunConfig) -> RunOutcome:
    if cfg.protocol != PROTOCOL_VERSION:
        from apex_worker.research.realeq.protocol_v2 import PROTOCOL_V2_VERSION

        if cfg.protocol != PROTOCOL_V2_VERSION:
            return RunOutcome("INVALID_INPUT", EXIT["INVALID_INPUT"], cfg.out_dir, f"unknown protocol {cfg.protocol!r}")
        from apex_worker.research.realeq.runner_v2 import run_v2

        return run_v2(cfg)
    env = os.environ if cfg.env is None else cfg.env
    now = cfg.now or datetime.now(UTC)
    steps: list[dict] = []
    started = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    demo = cfg.source == "fixture"
    m: dict = {
        "experiment_id": cfg.experiment_id, "data_class": "SYNTHETIC_FIXTURE_DEMO" if demo else "REAL_MARKET_DATA",
        "run_status": "FAILED", "aggregate": {}, "protocol_sha256": protocol_sha256(), "started_at": started,
        "code_sha": env.get("GITHUB_SHA") or _git("rev-parse", "HEAD") or "unknown", "runner": _runner(env),
        "secret_presence": {k: ("present" if (env.get(k) or "").strip() else "absent") for k in ("ALPACA_KEY_ID", "ALPACA_SECRET_KEY")}
        if not demo else {"ALPACA_KEY_ID": "not used (fixture)", "ALPACA_SECRET_KEY": "not used (fixture)"},
        "errors": [], "bias_warnings": [],
    }

    def step(name: str, status: str, detail: str = "") -> None:
        steps.append({"step": name, "status": status, "detail": detail})
        cfg.progress(f"[{status}] {name} {detail}")

    out = cfg.out_dir
    # ---- 1. inputs ----------------------------------------------------------------------------------------------
    try:
        eid = sanitize_experiment_id(cfg.experiment_id)
        m["experiment_id"] = eid
        check_years(cfg.years)
        if cfg.universe != "fixed78":
            raise ValueError("universe must be fixed78 under realeq-protocol-1")
        if cfg.source not in ("alpaca", "fixture"):
            raise ValueError("source must be alpaca (real) or fixture (DEMO smoke test)")
        if cfg.time_start and cfg.time_end and cfg.time_start >= cfg.time_end:
            raise ValueError("time_start must be before time_end")
        if demo and not eid.startswith("demo-"):
            raise ValueError("fixture (DEMO) runs need an experiment_id starting with 'demo-'")
        if demo and _is_inside(out, REPO_ROOT / "research-results"):
            raise ValueError("DEMO fixture output may never be written under research-results/")
    except ValueError as exc:  # nothing is written: a rejected run leaves no files (and none under research-results/)
        step("validate inputs", "failed", str(exc))
        return RunOutcome("INVALID_INPUT", EXIT["INVALID_INPUT"], out, str(exc))
    out.mkdir(parents=True, exist_ok=True)
    step("validate inputs", "ok", f"source={cfg.source} years={cfg.years} universe={cfg.universe} research_only=true")

    cal = default_calendar()
    try:
        latest = cal.latest_completed_session(now)
        end = min(latest, cfg.time_end) if cfg.time_end else latest
        spec = default_spec(cfg.years, cfg.max_requests)
        m |= {"feed": spec.feed, "adjustment": spec.adjustment}
        # ---- 2. credentials (real) or fixture -----------------------------------------------------------------
        if demo:
            from apex_worker.research.realeq.fixture import FIXTURE_NOW, FakeAlpaca, universe_frames

            now = FIXTURE_NOW
            end = min(cal.latest_completed_session(now), cfg.time_end) if cfg.time_end else cal.latest_completed_session(now)
            fake = FakeAlpaca(universe_frames(date(end.year - cfg.years, end.month, 1)))
            factory = cfg.adapter_factory or (lambda: fake.adapter(FIXTURE_NOW))
            step("credentials", "skipped", "DEMO fixture: in-process emulation of the Alpaca bars API, no keys, no network")
        else:
            key, secret = credentials_from_env(env)
            step("credentials", "ok", "ALPACA_KEY_ID / ALPACA_SECRET_KEY present (values never logged)")
            if cfg.adapter_factory is not None:
                factory = cfg.adapter_factory
            else:
                from apex_worker.market.adapters.alpaca import AlpacaAdapter

                def factory():
                    return AlpacaAdapter(key, secret)

        # ---- 3. fetch (ephemeral cache: deleted when the run ends) ----------------------------------------------
        with tempfile.TemporaryDirectory(prefix="apex-realeq-") as tmp:
            loaded = fetch(factory, Path(tmp), spec, end=end, now=now)
        res = loaded.result
        step("fetch historical SIP daily bars", "ok",
             f"{len(res.bars)}/{len(spec.symbols)} symbols, {res.requests} requests, window {res.start}..{res.end}")
        # ---- 4. data quality ------------------------------------------------------------------------------------
        try:
            dq, usable = quality_inventory(loaded, cal)
        except RunStop as stop:
            body = getattr(stop, "body", None)
            if body:
                R.write_json(out / "data_quality.json", body)
            raise
        R.write_json(out / "data_quality.json", dq)
        m["data_quality"] = dq
        m["bias_warnings"] = dq["bias_warnings"]
        step("data-quality and bias inventory", "ok",
             f"usable {dq['counts']['usable']}/78, candidate stocks {dq['counts']['candidate_stocks_usable']}/66")
        # ---- 5. freeze ------------------------------------------------------------------------------------------
        b = compute_boundaries(usable[BENCHMARK].index, cfg.time_start)
        dhash = data_hash(usable)
        meta = {s: mm for s, mm in universe_meta().items() if s in usable}
        manifest = {
            "schema": "apex-realeq-manifest-1", "experiment_id": eid, "data_class": m["data_class"],
            "frozen_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "status_at_freeze": "FROZEN_BEFORE_METRICS",
            "research_only": True, "promotion": "none (no signing, no registry, no champion change)",
            "code": {"commit_sha": m["code_sha"], "ref": env.get("GITHUB_REF"), "dirty_worktree": bool(_git("status", "--porcelain"))
                     if not env.get("GITHUB_SHA") else False},
            "runner": m["runner"],
            "source": {"provider": "fixture-emulation" if demo else "alpaca", "feed": spec.feed, "adjustment": spec.adjustment,
                       "timeframe": "1Day", "entitlement": "Alpaca Basic: historical SIP older than 15 minutes"
                       if not demo else "none (synthetic)", "fetched_at": loaded.fetched_at, "provider_requests": res.requests,
                       "history_hash": res.history_hash, "dataset_hash": dhash,
                       "corporate_actions": dq["corporate_actions"]},
            "universe": {"name": "fixed78", "benchmark": BENCHMARK, "sector_etfs": SECTOR_ETFS,
                         "candidate_stocks": {k: list(v) for k, v in MEMBERS.items()},
                         "usable": sorted(usable), "traded": sorted(s for s, mm in meta.items() if mm.asset_kind == "stock")},
            "coverage": {"first_session": b.data_start, "last_session": b.data_end, "requested_years": cfg.years,
                         "time_start": cfg.time_start.isoformat() if cfg.time_start else None,
                         "time_end": cfg.time_end.isoformat() if cfg.time_end else None},
            "boundaries": b.to_dict(),
            "label_method": {"type": "TARGET_BEFORE_STOP", "primary_horizon_sessions": PROTOCOL["primary_horizon"],
                             "horizons": PROTOCOL["horizons"], "same_bar_rule": "STOP_FIRST", "entry_valid_sessions": 2},
            "costs_bps_per_side": PROTOCOL["costs_bps_per_side"],
            "seeds": {"bootstrap": PROTOCOL["bootstrap"]["seed"], "matched_random": PROTOCOL["matched_random"]["seed"],
                      "validation": 20261020, "PYTHONHASHSEED": env.get("PYTHONHASHSEED")},
            "versions": {"protocol": PROTOCOL_VERSION, "protocol_sha256": m["protocol_sha256"], "promotion_policy": POLICY_VERSION,
                         "strategies": {s: st.spec.version for s, st in STRATEGIES.items()}, "model_features": list(MODEL_FEATURES)},
            "protocol": PROTOCOL,
            "warnings": dq["bias_warnings"],
        }
        R.write_json(out / "manifest.json", manifest)
        m |= {"boundaries": b.to_dict(), "dataset_hash": dhash, "manifest_sha256": _sha_file(out / "manifest.json")}
        step("freeze protocol, boundaries and hashes (manifest.json)", "ok",
             f"holdout {b.holdout_start}..{b.holdout_end}; manifest sha256 {m['manifest_sha256'][:12]}")
        # ---- 6. evaluate ------------------------------------------------------------------------------------------
        from apex_worker.research.realeq.analysis import run_analysis

        panel = build_panel(usable, meta, BENCHMARK, {k: v for k, v in SECTOR_ETFS.items() if v in usable})
        stocks = sorted(s for s, mm in meta.items() if mm.asset_kind == "stock")
        a = run_analysis(panel, usable, stocks, b, dq["bias_warnings"], m["data_class"], cfg.progress)
        step("evaluate H1-H8, baselines, portfolio, robustness", "ok",
             f"{len(a.configurations)} configurations; aggregate {a.aggregate['conclusion']}")
        m |= {"run_status": STATUS_COMPLETED, "strategies": a.strategies, "baselines": a.baselines, "aggregate": a.aggregate,
              "configurations_attempted": len(a.configurations)}
        metrics = {
            "schema": "apex-realeq-strategy-metrics-1", "experiment_id": eid, "data_class": m["data_class"],
            "run_status": STATUS_COMPLETED, "manifest_sha256": m["manifest_sha256"], "protocol_version": PROTOCOL_VERSION,
            "primary_configuration": {"horizon": PROTOCOL["primary_horizon"], "cost_bps_per_side": PROTOCOL["primary_cost_bps"],
                                      "segment": "holdout", "rule": PROTOCOL["rule_definition"]},
            "aggregate": a.aggregate, "strategies": a.strategies, "baselines": a.baselines,
            "configurations_attempted": a.configurations,
        }
        R.write_json(out / "strategy_metrics.json", metrics)
        R.write_csv(out / "trades_summary.csv", a.trades_rows)
        (out / "comparisons.md").write_text(R.comparisons_md(m))
        (out / "recommendations.md").write_text(R.recommendations_md(m))
        step("write reports", "ok", ", ".join(R.OUTPUT_FILES))
        return _finish(m, steps, out, STATUS_COMPLETED, "")
    except RunStop as stop:
        m |= {"run_status": stop.status, "run_reason": stop.reason}
        m["errors"].append(stop.reason)
        m["blocker_action"] = OWNER_SETUP if stop.status == STATUS_BLOCKED else (
            "Check the Alpaca plan's historical SIP entitlement and key validity, then re-run; the protocol never "
            "falls back to IEX or synthetic data." if stop.status == STATUS_SOURCE_INVALID else
            "History is too short or incomplete for the frozen protocol; see data_quality.json.")
        step("run stopped", "failed", f"{stop.status}: {stop.reason}")
        return _finish(m, steps, out, stop.status, stop.reason)
    except Exception as exc:
        m |= {"run_status": "FAILED", "run_reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
        m["errors"].append(traceback.format_exc(limit=3).splitlines()[-1][:300])
        step("unexpected error", "failed", m["run_reason"])
        return _finish(m, steps, out, "FAILED", m["run_reason"])


def _finish(m: dict, steps: list[dict], out: Path, status: str, reason: str) -> RunOutcome:
    m["finished_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if status != STATUS_COMPLETED:
        for name in ("evaluate H1-H8, baselines, portfolio, robustness", "write reports"):
            if not any(s["step"] == name for s in steps):
                steps.append({"step": name, "status": "not_run", "detail": f"run status {status}"})
        if not (out / "manifest.json").exists():
            R.write_json(out / "manifest.json", {
                "schema": "apex-realeq-manifest-1", "experiment_id": m["experiment_id"], "data_class": m["data_class"],
                "status_at_freeze": "NOT_FROZEN", "run_status": status, "reason": reason, "research_only": True,
                "code": {"commit_sha": m.get("code_sha")}, "runner": m.get("runner"),
                "versions": {"protocol": PROTOCOL_VERSION, "protocol_sha256": m["protocol_sha256"]},
            })
        R.write_json(out / "strategy_metrics.json", {
            "schema": "apex-realeq-strategy-metrics-1", "experiment_id": m["experiment_id"], "data_class": m["data_class"],
            "run_status": status, "reason": reason, "aggregate": {"conclusion": "NOT RUN"},
            "strategies": {sid: {"title": st.spec.title, "disposition": None, "not_run_reason": f"{status}: no evaluation performed"}
                           for sid, st in STRATEGIES.items()},
        })
        if not (out / "data_quality.json").exists():
            R.write_json(out / "data_quality.json", {"schema": "apex-realeq-data-quality-1", "run_status": status,
                                                     "reason": "no data was downloaded", "symbols": []})
        R.write_csv(out / "trades_summary.csv", [])
        (out / "comparisons.md").write_text(R.comparisons_md(m))
        (out / "recommendations.md").write_text(R.recommendations_md(m))
    (out / "execution_log.md").write_text(R.execution_log_md(m, steps))
    if status == STATUS_BLOCKED:
        reason = f"{BLOCKED_BANNER}. {OWNER_SETUP}"
    return RunOutcome(status, EXIT.get(status, 1), out, reason)
