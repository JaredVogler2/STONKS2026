"""`apex research real-equities --protocol realeq-protocol-2`: inputs -> credentials -> fresh SIP download of U1 ->
quality inventory -> FROZEN manifest -> study (S1-S5, legacy, controls, self-checks) -> the same seven report files.

Exit codes as protocol-1: 0 completed, 2 BLOCKED (Alpaca secrets missing), 3 SOURCE_INVALID, 4 INSUFFICIENT_HISTORY,
5 bad input, 1 unexpected error. A non-completed run never writes strategy results.
"""

from __future__ import annotations

import os
import tempfile
import traceback
from datetime import UTC, datetime
from pathlib import Path

from apex_worker.market.calendar import default_calendar
from apex_worker.research.dataset import data_hash
from apex_worker.research.realeq import report_v2 as R
from apex_worker.research.realeq.data import (
    BLOCKED_BANNER,
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_SOURCE_INVALID,
    RunStop,
    credentials_from_env,
)
from apex_worker.research.realeq.data_v2 import (
    BIAS_WARNINGS_V2,
    data_end,
    fetch_v2,
    quality_inventory_v2,
    quarantine_from_quality,
    spec_v2,
)
from apex_worker.research.realeq.protocol_v2 import (
    PROTOCOL_V2,
    PROTOCOL_V2_VERSION,
    compute_boundaries_v2,
    protocol_v2_sha256,
)
from apex_worker.research.realeq.runner import (
    EXIT,
    OWNER_SETUP,
    REPO_ROOT,
    RunConfig,
    RunOutcome,
    _git,
    _is_inside,
    _runner,
    _sha_file,
)
from apex_worker.research.realeq.universe_v2 import snapshot_sha256, u1_symbols
from apex_worker.research.training_data import BENCHMARK

UNIVERSE_V2 = "u1-sp500"


def run_v2(cfg: RunConfig) -> RunOutcome:
    env = os.environ if cfg.env is None else cfg.env
    now = cfg.now or datetime.now(UTC)
    steps: list[dict] = []
    demo = cfg.source == "fixture"
    m: dict = {
        "experiment_id": cfg.experiment_id, "data_class": "SYNTHETIC_FIXTURE_DEMO" if demo else "REAL_MARKET_DATA",
        "run_status": "FAILED", "aggregate": {}, "protocol_sha256": protocol_v2_sha256(), "protocol": PROTOCOL_V2_VERSION,
        "started_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code_sha": env.get("GITHUB_SHA") or _git("rev-parse", "HEAD") or "unknown", "runner": _runner(env),
        "secret_presence": {k: ("present" if (env.get(k) or "").strip() else "absent") for k in ("ALPACA_KEY_ID", "ALPACA_SECRET_KEY")}
        if not demo else {"ALPACA_KEY_ID": "not used (fixture)", "ALPACA_SECRET_KEY": "not used (fixture)"},
        "errors": [], "bias_warnings": list(BIAS_WARNINGS_V2),
    }

    def step(name: str, status: str, detail: str = "") -> None:
        steps.append({"step": name, "status": status, "detail": detail})
        cfg.progress(f"[{status}] {name} {detail}")

    out = cfg.out_dir
    from apex_worker.research.realeq.protocol import sanitize_experiment_id

    try:
        eid = sanitize_experiment_id(cfg.experiment_id)
        m["experiment_id"] = eid
        if cfg.universe != UNIVERSE_V2:
            raise ValueError(f"universe must be {UNIVERSE_V2} under {PROTOCOL_V2_VERSION}")
        if cfg.source not in ("alpaca", "fixture"):
            raise ValueError("source must be alpaca (real) or fixture (DEMO smoke test)")
        if cfg.time_start is not None:
            raise ValueError(f"time_start is fixed by {PROTOCOL_V2_VERSION} (PRESAMPLE/DEV chronology); leave it blank")
        if demo and not eid.startswith("demo-"):
            raise ValueError("fixture (DEMO) runs need an experiment_id starting with 'demo-'")
        if demo and _is_inside(out, REPO_ROOT / "research-results"):
            raise ValueError("DEMO fixture output may never be written under research-results/")
    except ValueError as exc:
        step("validate inputs", "failed", str(exc))
        return RunOutcome("INVALID_INPUT", EXIT["INVALID_INPUT"], out, str(exc))
    out.mkdir(parents=True, exist_ok=True)
    step("validate inputs", "ok", f"protocol={PROTOCOL_V2_VERSION} source={cfg.source} universe={cfg.universe} research_only=true")

    cal = default_calendar()
    try:
        spec = spec_v2(cfg.max_requests)
        m |= {"feed": spec.feed, "adjustment": spec.adjustment}
        if demo:
            from apex_worker.research.realeq.fixture import FIXTURE_NOW, FakeAlpaca
            from apex_worker.research.realeq.fixture_v2 import factor_frames_v2

            now = FIXTURE_NOW
            end = data_end(cal, now, cfg.time_end)
            fake = FakeAlpaca(factor_frames_v2(per_sector=int(env.get("APEX_DEMO_PER_SECTOR") or 12)),
                              corporate_actions=[])  # the endpoint is emulated (and empty): protocol-2 requires it
            factory = cfg.adapter_factory or (lambda: fake.adapter(FIXTURE_NOW))
            step("credentials", "skipped", "DEMO fixture: in-process emulation of the Alpaca bars API, no keys, no network")
        else:
            end = data_end(cal, now, cfg.time_end)
            key, secret = credentials_from_env(env)
            step("credentials", "ok", "ALPACA_KEY_ID / ALPACA_SECRET_KEY present (values never logged)")
            if cfg.adapter_factory is not None:
                factory = cfg.adapter_factory
            else:
                from apex_worker.market.adapters.alpaca import AlpacaAdapter

                def factory():
                    return AlpacaAdapter(key, secret)

        with tempfile.TemporaryDirectory(prefix="apex-realeq-v2-") as tmp:
            loaded = fetch_v2(factory, Path(tmp), spec, end=end, now=now)
        res = loaded.result
        step("fetch historical SIP daily bars (U1 + references)", "ok",
             f"{len(res.bars)}/{len(spec.symbols)} symbols, {res.requests} requests, data end {end} (finality lag "
             f"{PROTOCOL_V2['data']['finality_lag_sessions']} sessions)")
        try:
            dq, usable = quality_inventory_v2(loaded, cal)
        except RunStop as stop:
            body = getattr(stop, "body", None)
            if body:
                R.write_json(out / "data_quality.json", body)
            raise
        R.write_json(out / "data_quality.json", dq)
        m["data_quality"] = dq
        m["bias_warnings"] = list(dq["bias_warnings"])  # fixed warnings plus this run's data findings (A1)
        step("data-quality and bias inventory", "ok",
             f"usable {dq['counts']['usable']}/{len(u1_symbols())}, stocks {dq['counts']['candidate_stocks_usable']}; "
             f"corporate actions {dq['corporate_actions']['status']}")
        b = compute_boundaries_v2(usable[BENCHMARK].index)
        dhash = data_hash(usable)
        manifest = {
            "schema": "apex-realeq-manifest-2", "experiment_id": eid, "data_class": m["data_class"],
            "frozen_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "status_at_freeze": "FROZEN_BEFORE_METRICS",
            "research_only": True, "promotion": "none (no signing, no registry, no champion change)",
            "code": {"commit_sha": m["code_sha"], "ref": env.get("GITHUB_REF"),
                     "dirty_worktree": bool(_git("status", "--porcelain")) if not env.get("GITHUB_SHA") else False},
            "runner": m["runner"],
            "source": {"provider": "fixture-emulation" if demo else "alpaca", "feed": spec.feed, "adjustment": spec.adjustment,
                       "timeframe": "1Day", "fetched_at": loaded.fetched_at, "provider_requests": res.requests,
                       "history_hash": res.history_hash, "dataset_hash": dhash, "corporate_actions": dq["corporate_actions"],
                       "entitlement": "Alpaca Basic: historical SIP older than 15 minutes" if not demo else "none (synthetic)"},
            "universe": {"id": PROTOCOL_V2["universe"]["id"], "snapshot": PROTOCOL_V2["universe"]["snapshot"],
                         "snapshot_sha256": snapshot_sha256(), "classification": PROTOCOL_V2["universe"]["classification"],
                         "usable": sorted(usable)},
            "boundaries": b.to_dict(),
            "versions": {"protocol": PROTOCOL_V2_VERSION, "protocol_sha256": m["protocol_sha256"]},
            "protocol": PROTOCOL_V2, "warnings": list(m["bias_warnings"]),
        }
        R.write_json(out / "manifest.json", manifest)
        m |= {"boundaries": b.to_dict(), "dataset_hash": dhash, "manifest_sha256": _sha_file(out / "manifest.json")}
        step("freeze protocol, boundaries and hashes (manifest.json)", "ok",
             f"PRESAMPLE {b.presample_start}..{b.presample_end}; DEV {b.dev_start}..{b.dev_end}; "
             f"manifest sha256 {m['manifest_sha256'][:12]}")
        from apex_worker.research.realeq.study_v2 import prepare, run_study

        pre = prepare(usable, cfg.progress)

        def on_lock(lock: dict) -> None:
            step("lock the DEV-only arm selection (before any PRESAMPLE outcome)", "ok",
                 f"{lock['chosen_arms']}; lock sha256 {lock['lock_sha256'][:16]}; manifest {m['manifest_sha256'][:12]}")

        res2 = run_study(pre, b, quarantine_from_quality(dq), cfg.progress, on_lock, m["manifest_sha256"])
        step("evaluate S1-S5, legacy H rules, baselines, controls, self-checks", "ok",
             f"{len(res2.configurations)} configurations; aggregate {res2.aggregate['conclusion']}")
        m |= {"run_status": STATUS_COMPLETED, "strategies": res2.strategies, "legacy": res2.legacy,
              "baselines": res2.baselines, "engine_self_checks": res2.self_checks, "aggregate": res2.aggregate}
        metrics = {
            "schema": "apex-realeq-strategy-metrics-2", "experiment_id": eid, "data_class": m["data_class"],
            "run_status": STATUS_COMPLETED, "manifest_sha256": m["manifest_sha256"], "protocol_version": PROTOCOL_V2_VERSION,
            "protocol_sha256": m["protocol_sha256"], "index": R.INDEX, "aggregate": res2.aggregate,
            "strategies": res2.strategies, "legacy": res2.legacy, "baselines": res2.baselines,
            "engine_self_checks": res2.self_checks, "multiplicity": res2.multiplicity, "coverage": res2.coverage,
            "selection_lock": res2.selection_lock, "bias_warnings": m["bias_warnings"],
            "corporate_actions": dq["corporate_actions"] | {"quarantined_symbols": len(dq["quarantine"])},
            "configurations_attempted": res2.configurations,
        }
        R.write_json(out / "strategy_metrics.json", metrics)
        R.write_csv_v2(out / "trades_summary.csv", res2.summary_rows)
        (out / "comparisons.md").write_text(R.comparisons_md_v2(m))
        (out / "recommendations.md").write_text(R.recommendations_md_v2(m))
        step("write reports", "ok", "manifest, data_quality, strategy_metrics, trades_summary, comparisons, recommendations")
        return _finish_v2(m, steps, out, STATUS_COMPLETED, "")
    except RunStop as stop:
        m |= {"run_status": stop.status, "run_reason": stop.reason}
        m["errors"].append(stop.reason)
        m["blocker_action"] = OWNER_SETUP if stop.status == STATUS_BLOCKED else (
            "Check the Alpaca plan's historical SIP entitlement and key validity, then re-run; the protocol never "
            "falls back to IEX or synthetic data." if stop.status == STATUS_SOURCE_INVALID else
            "History is too short or incomplete for the frozen protocol; see data_quality.json.")
        step("run stopped", "failed", f"{stop.status}: {stop.reason}")
        return _finish_v2(m, steps, out, stop.status, stop.reason)
    except Exception as exc:
        m |= {"run_status": "FAILED", "run_reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
        m["errors"].append(traceback.format_exc(limit=3).splitlines()[-1][:300])
        step("unexpected error", "failed", m["run_reason"])
        return _finish_v2(m, steps, out, "FAILED", m["run_reason"])


def _finish_v2(m: dict, steps: list[dict], out: Path, status: str, reason: str) -> RunOutcome:
    m["finished_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if status != STATUS_COMPLETED:
        for name in ("evaluate S1-S5, legacy H rules, baselines, controls, self-checks", "write reports"):
            if not any(s["step"] == name for s in steps):
                steps.append({"step": name, "status": "not_run", "detail": f"run status {status}"})
        if not (out / "manifest.json").exists():
            R.write_json(out / "manifest.json", {
                "schema": "apex-realeq-manifest-2", "experiment_id": m["experiment_id"], "data_class": m["data_class"],
                "status_at_freeze": "NOT_FROZEN", "run_status": status, "reason": reason, "research_only": True,
                "code": {"commit_sha": m.get("code_sha")}, "runner": m.get("runner"),
                "versions": {"protocol": PROTOCOL_V2_VERSION, "protocol_sha256": m["protocol_sha256"]}})
        R.write_json(out / "strategy_metrics.json", {
            "schema": "apex-realeq-strategy-metrics-2", "experiment_id": m["experiment_id"], "data_class": m["data_class"],
            "run_status": status, "reason": reason, "aggregate": {"conclusion": "NOT RUN"}, "protocol_version": PROTOCOL_V2_VERSION})
        if not (out / "data_quality.json").exists():
            R.write_json(out / "data_quality.json", {"schema": "apex-realeq-data-quality-2", "run_status": status,
                                                     "reason": "no data was downloaded", "symbols": []})
        R.write_csv_v2(out / "trades_summary.csv", [])
        (out / "comparisons.md").write_text(R.comparisons_md_v2(m))
        (out / "recommendations.md").write_text(R.recommendations_md_v2(m))
    (out / "execution_log.md").write_text(R.execution_log_md(m, steps) + R.limitations_md(m))  # protocol-2 only
    if status == STATUS_BLOCKED:
        reason = f"{BLOCKED_BANNER}. {OWNER_SETUP}"
    return RunOutcome(status, EXIT.get(status, 1), out, reason)
