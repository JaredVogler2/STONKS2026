"""CLI for the CI model workflows: `apex release ...`, `apex drift`, `apex budget check`.

Registered from apex_worker.cli via `register(sub)`. Conventions:
* `--out FILE` writes the JSON result (never secrets); `--github-output` appends single-line `key=value` pairs to
  $GITHUB_OUTPUT for later workflow steps.
* exit 0 = done / proceed, 1 = failure that must fail the job, 2 = configuration error, 3 = budget skip (budget check).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

EXIT_CONFIG = 2
_SAFE_OUTPUT = re.compile(r"^[A-Za-z0-9_.,:=@/+ -]{0,2000}$")


# ---- helpers --------------------------------------------------------------------------------------------------
def _write_json(path: str | None, obj) -> None:
    if path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n")


def _read_json(path: str | None) -> dict | None:
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def github_output(pairs: dict, enabled: bool = True) -> None:
    """Append key=value lines to $GITHUB_OUTPUT. Values are restricted to a safe single-line charset."""
    target = os.environ.get("GITHUB_OUTPUT")
    if not (enabled and target):
        return
    lines = []
    for k, v in pairs.items():
        val = "" if v is None else (str(v).lower() if isinstance(v, bool) else str(v))
        if not _SAFE_OUTPUT.match(val):
            val = re.sub(r"[^A-Za-z0-9_.,:=@/+ -]", "_", val)[:2000]
        lines.append(f"{k}={val}")
    with open(target, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _client():
    from apex_worker.release.client import client_from_env

    return client_from_env()


def _config_error(exc: Exception) -> int:
    print(f"configuration error: {exc}", file=sys.stderr)
    return EXIT_CONFIG


def _policy(args) -> tuple[dict | None, str]:
    """Policy from --policy FILE, else the release API (unless --offline). Returns (policy, source)."""
    from apex_worker.release.client import ReleaseConfigError, ReleaseError

    pol = _read_json(getattr(args, "policy", None))
    if pol is not None:
        return pol, "file"
    if getattr(args, "offline", False):
        return None, "offline"
    try:
        with _client() as c:
            return c.policy(), "release_api"
    except (ReleaseConfigError, ReleaseError) as exc:
        print(f"warning: release policy unavailable ({exc})", file=sys.stderr)
        return None, "unavailable"


def _trusted():
    from apex_worker.release.upload import trusted_keys_from_env

    return trusted_keys_from_env(os.environ.get("APEX_MODEL_TRUSTED_KEYS"))


# ---- apex release preflight ------------------------------------------------------------------------------------
REGISTRY_UNCONFIGURED = "PRODUCTION_REGISTRY_UNCONFIGURED"


def cmd_preflight(args) -> int:
    """No network. Is the production release registry configured for this job's environment?

    * none of APEX_RELEASE_URL / _AGENT_ID / _HMAC_SECRET set -> PRODUCTION_REGISTRY_UNCONFIGURED: configured=false and
      exit 0 (the workflow then skips every registry step), or exit 2 with --fail-when-unconfigured (manual runs);
    * set but incomplete or malformed -> configuration error, exit 2 (someone started configuring: fail loudly);
    * valid -> configured=true. Authentication (HMAC + GitHub OIDC) is unchanged: this only reads which names are set.
    """
    from apex_worker.release.client import ENV_AGENT, ENV_SECRET, ENV_URL, ReleaseConfig, ReleaseConfigError

    names = (ENV_URL, ENV_AGENT, ENV_SECRET)
    present = [n for n in names if (os.environ.get(n) or "").strip()]
    body: dict = {"names_checked": list(names), "present": present}
    if not present:
        body |= {"configured": False, "status": REGISTRY_UNCONFIGURED,
                 "detail": "the `model-release` GitHub Environment has no release-registry secrets; registry steps skipped",
                 "remediation": "docs/AUTONOMOUS_TRAINING.md: deploy the dashboard + Supabase registry, create the "
                 "`model-release` environment (main only) with APEX_RELEASE_URL, APEX_RELEASE_AGENT_ID and "
                 "APEX_RELEASE_HMAC_SECRET. Real-equity research does not need any of this "
                 "(apex-real-equities-research.yml)."}
        rc = EXIT_CONFIG if args.fail_when_unconfigured else 0
    else:
        try:
            ReleaseConfig.from_env()
            body |= {"configured": True, "status": "CONFIGURED"}
            rc = 0
        except ReleaseConfigError as exc:
            body |= {"configured": False, "status": "PRODUCTION_REGISTRY_MISCONFIGURED", "detail": str(exc)}
            rc = EXIT_CONFIG
    _write_json(args.out, body)
    github_output({"configured": body["configured"], "registry_status": body["status"]}, args.github_output)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and not body["configured"]:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"## {body['status']}\n\n{body['detail']}\n\n{body.get('remediation', '')}\n")
    if not body["configured"]:
        level = "error" if rc else "warning"
        print(f"::{level} title={body['status']}::{body['detail']}" if os.environ.get("GITHUB_ACTIONS")
              else f"{body['status']}: {body['detail']}", file=sys.stderr)
    print(json.dumps(body, sort_keys=True))
    return rc


# ---- apex train --source alpaca support -----------------------------------------------------------------------
def alpaca_loader_from_env(args):
    """(loader, info) for `apex train --source alpaca`; credentials come from ALPACA_KEY_ID / ALPACA_SECRET_KEY."""
    from apex_worker.market.history import HistorySpec
    from apex_worker.research.training_data import LoadInfo, make_alpaca_loader, universe_symbols

    spec = HistorySpec(
        symbols=universe_symbols(),
        years=args.history_years,
        feed=args.history_feed,
        max_requests=args.history_max_requests,
    )
    key, secret = (os.environ.get("ALPACA_KEY_ID") or "").strip(), (os.environ.get("ALPACA_SECRET_KEY") or "").strip()
    if not (key and secret):
        info = LoadInfo(spec, error="Alpaca credentials not configured (ALPACA_KEY_ID / ALPACA_SECRET_KEY)")
        return (lambda: None), info
    return make_alpaca_loader(key, secret, Path(args.history_cache), spec)


def add_history_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--history-cache", default=".apex-local/history", help="bounded incremental bar cache directory")
    p.add_argument("--history-years", type=int, default=8)
    p.add_argument("--history-feed", choices=["sip", "iex"], default="sip")
    p.add_argument("--history-max-requests", type=int, default=300)


# ---- apex release ... ------------------------------------------------------------------------------------------
def cmd_policy(args) -> int:
    from apex_worker.release.client import ReleaseConfigError, ReleaseError

    try:
        with _client() as c:
            pol = c.policy()
    except ReleaseConfigError as exc:
        return _config_error(exc)
    except ReleaseError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _write_json(args.out, pol)
    budgets = pol.get("budgets") or {}
    github_output(
        {
            "mode": pol.get("mode"),
            "promotion_mode": (pol.get("policy") or {}).get("promotion_mode"),
            "drift_enabled": budgets.get("drift_enabled"),
            "budget_minutes": budgets.get("actions_monthly_minutes"),
        },
        args.github_output,
    )
    print(json.dumps({k: pol.get(k) for k in ("mode", "policy", "budgets", "usage_month")}, sort_keys=True))
    return 0


def cmd_champions(args) -> int:
    from apex_worker.release.client import ReleaseConfigError, ReleaseError
    from apex_worker.release.upload import CandidateInvalid, verify_champions

    try:
        with _client() as c:
            data = c.champions(include_artifacts=args.verify)
        trusted = _trusted() if args.verify else {}
    except (ReleaseConfigError, CandidateInvalid) as exc:
        return _config_error(exc)
    except ReleaseError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    champions = data.get("champions") or {}
    _write_json(args.out, champions)  # the exact shape `apex train --champions FILE` expects
    print(f"champions: {len(champions)} ({', '.join(sorted(champions)) or 'none'})")
    if args.verify:
        checked = verify_champions(data, trusted)
        for mid, r in sorted(checked.items()):
            print(f"  {'OK ' if r['ok'] else 'BAD'} {mid} {r.get('strategy_id')} {r.get('error', '')}")
        if not all(r["ok"] for r in checked.values()):
            return 1
    return 0


def cmd_upload(args) -> int:
    from apex_worker.release.client import ReleaseConfigError, ReleaseError
    from apex_worker.release.upload import CandidateInvalid, upload_candidates

    out_dir = Path(args.dir)
    if not (out_dir / "report.json").exists():
        print(f"no report.json in {out_dir}: training did not produce output", file=sys.stderr)
        return 1
    policy, psrc = _policy(args)
    try:
        trusted = _trusted()
        with _client() as c:
            result = upload_candidates(
                c,
                out_dir,
                policy=policy,
                trusted=trusted,
                include_non_promotable=args.include_non_promotable,
                training_run_id=args.training_run_id,
            )
    except (ReleaseConfigError, CandidateInvalid) as exc:
        return _config_error(exc)
    except ReleaseError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    result["policy_source"] = psrc
    result["signature_checked_locally"] = bool(trusted)
    _write_json(args.out, result)
    ids = [r["model_id"] for r in result["registered"]]
    github_output(
        {
            "outcome": result["outcome"],
            "promotion_mode": result.get("promotion_mode") or "unknown",
            "registered_count": len(ids),
            "candidate_ids": ",".join(ids),
            "candidate_model_id": ids[0] if ids else "",
        },
        args.github_output,
    )
    for r in result["registered"]:
        promo = r.get("promotion") or {}
        print(f"registered {r['model_id']} ({r['strategy_id']}) created={r['created']} promoted={promo.get('promoted')} "
              f"reason={promo.get('reason')}")
    for r in result["skipped"]:
        print(f"skipped {r['model_id']}: {r['reason']}")
    for r in result["rejected"]:
        print(f"REJECTED {r['model_id']}: HTTP {r['status']} {r['code']} {r['message']}", file=sys.stderr)
    print(f"outcome={result['outcome']}")
    return 1 if result["rejected"] else 0


def _ids(raw: str) -> list[str]:
    from apex_worker.release.upload import MODEL_ID_RE

    ids = [x for x in re.split(r"[\s,]+", raw or "") if x]
    bad = [x for x in ids if not MODEL_ID_RE.match(x)]
    if bad or not ids:
        raise ValueError(f"model ids must look like mdl_<id>; got {raw[:80]!r}")
    return ids


def cmd_promotion(args) -> int:
    from apex_worker.release.client import ReleaseConfigError, ReleaseError
    from apex_worker.release.upload import CandidateInvalid, verify_champions

    action = args.release_cmd
    try:
        ids = _ids(args.model_ids)
        trusted = _trusted()
    except (ValueError, CandidateInvalid) as exc:
        return _config_error(exc)
    results: list[dict] = []
    try:
        with _client() as c:
            for mid in ids:
                try:
                    r = c.promotion(action, mid)
                except ReleaseError as exc:
                    if exc.status in (404, 409, 422):
                        results.append({"requested_model_id": mid, "ok": False, "status": exc.status, "code": exc.code,
                                        "reason": exc.message})
                        continue
                    raise
                ok = bool(r.get("promoted") or r.get("rolled_back") or r.get("reason") == "already champion")
                results.append({**r, "ok": ok})
            after = c.champions(include_artifacts=True)
    except ReleaseConfigError as exc:
        return _config_error(exc)
    except ReleaseError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    champion_ids = {str(v.get("model_id")) for v in (after.get("champions") or {}).values()}
    verified = verify_champions(after, trusted)
    inconsistent = []
    for r in results:
        if action == "promote" and r["ok"]:
            r["verified_champion"] = r["requested_model_id"] in champion_ids
            if not r["verified_champion"]:
                inconsistent.append(r["requested_model_id"])
        if action == "rollback" and r.get("rolled_back"):
            r["verified_champion"] = r.get("model_id") in champion_ids
            if not r["verified_champion"]:
                inconsistent.append(str(r.get("model_id")))
    bad_registry = sorted(mid for mid, v in verified.items() if not v["ok"])
    outcome = None
    if action == "promote":
        outcome = "promoted" if any(r.get("promoted") for r in results) else ("rejected" if results else None)
    payload = {
        "action": action,
        "reason": args.reason,
        "results": results,
        "outcome": outcome,
        "champions_after": sorted(champion_ids),
        "registry_verification": verified,
        "signature_checked": bool(trusted),
        "inconsistent": inconsistent,
    }
    _write_json(args.out, payload)
    github_output({"outcome": outcome or "", "all_ok": all(r["ok"] for r in results)}, args.github_output)
    for r in results:
        print(f"{action} {r['requested_model_id']}: ok={r['ok']} reason={r.get('reason')} verified={r.get('verified_champion')}")
    if bad_registry:
        print(f"REGISTRY VERIFICATION FAILED for {bad_registry}", file=sys.stderr)
    if inconsistent or bad_registry:
        return 1
    if args.fail_on_reject and not all(r["ok"] for r in results):
        return 1
    return 0


def cmd_report_run(args) -> int:
    from apex_worker.release.client import ReleaseConfigError, ReleaseError
    from apex_worker.release.runs import build_run, compact_training, parse_time

    report: dict = {}
    is_demo = args.is_demo
    candidate = args.candidate or None
    outcome = None if args.outcome in (None, "", "auto", "none") else args.outcome
    status = args.status
    budget = _read_json(args.budget)
    train = _read_json(str(Path(args.dir) / "report.json")) if args.dir else None
    upload = _read_json(args.upload)
    drift = _read_json(args.drift)
    release = _read_json(args.release_result)
    history = _read_json(str(Path(args.dir) / "history.json")) if args.dir else None
    if train is not None:
        report["training"] = compact_training(train)
        is_demo = is_demo or train.get("data_source") == "synthetic_fixture"
        if train.get("detail"):
            report["detail"] = str(train["detail"])[:400]
    elif args.workflow == "train_weekly" and status in ("succeeded", "success"):
        status = "failed"  # a "successful" job without a training report is not a success
        report["detail"] = "training produced no report.json"
    if history is not None:
        report["history"] = {k: history.get(k) for k in sorted(history) if k != "bias_warnings"}
        report["bias_warnings"] = history.get("bias_warnings")
    if upload is not None:
        report["upload"] = {k: upload.get(k) for k in ("registered", "skipped", "rejected", "outcome", "server_mode")}
        if outcome is None and args.outcome == "auto":
            outcome = upload.get("outcome")
        if candidate is None and upload.get("registered"):
            candidate = upload["registered"][0]["model_id"]
    elif args.workflow == "train_weekly" and args.outcome == "auto" and train is not None and train.get("status") == "no_data":
        outcome = "no_data"
    elif args.workflow == "train_weekly" and args.outcome == "auto" and train is not None and not train.get("candidates"):
        outcome = "champion_retained"
    if drift is not None:
        report["drift"] = {k: drift.get(k) for k in ("outcome", "data_source", "data_hash", "detail", "status")}
        report["drift"]["champions"] = [
            {k: c.get(k) for k in ("model_id", "strategy_id", "status", "breaches", "latest_resolved")}
            for c in drift.get("champions") or []
        ]
        is_demo = is_demo or bool(drift.get("is_demo"))
        if outcome is None and args.outcome == "auto" and drift.get("outcome"):
            outcome = drift["outcome"]
        if drift.get("status") == "skipped_holiday":
            status = "skipped_holiday"
    if release is not None:
        report["release"] = {k: release.get(k) for k in ("action", "results", "outcome", "inconsistent", "champions_after")}
        if outcome is None and args.outcome == "auto":
            outcome = release.get("outcome")
    for key in ("reason", "source_requested", "source_used"):
        if getattr(args, key, None):
            report[key] = str(getattr(args, key))[:200]
    try:
        run = build_run(
            workflow=args.workflow,
            status=status,
            started_at=parse_time(args.started_at) if args.started_at else datetime.now(UTC),
            outcome=outcome,
            trigger=args.trigger or None,
            candidate_model_id=candidate,
            budget=budget,
            is_demo=is_demo,
            report=report,
        )
    except ValueError as exc:
        return _config_error(exc)
    _write_json(args.out, run)
    if args.dry_run:
        print(json.dumps(run, indent=2, sort_keys=True))
        return 0
    try:
        with _client() as c:
            res = c.report_run(run)
    except ReleaseConfigError as exc:
        return _config_error(exc)
    except ReleaseError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"training run reported: id={res.get('id')} workflow={run['workflow']} status={run['status']} "
          f"outcome={run['outcome']} minutes~{run['minutes_used']}")
    return 0


def cmd_sla(args) -> int:
    from apex_worker.release.runs import parse_time
    from apex_worker.release.schedule import SCHEDULES, sla_status

    pol, _ = _policy(args)
    if pol is None:
        print("release policy unavailable: SLA status UNKNOWN", file=sys.stderr)
        return 1
    now = datetime.now(UTC)
    out = {}
    for wf, sched in SCHEDULES.items():
        last = ((pol.get("last_runs") or {}).get(wf) or {}).get("started_at")
        out[wf] = sla_status(sched, parse_time(last) if last else None, now)
    _write_json(args.out, out)
    github_output({f"{k}_missed": v["missed"] for k, v in out.items()}, args.github_output)
    for wf, s in out.items():
        flag = "MISSED" if s["missed"] else "ok"
        print(f"{wf}: {flag} expected={s['expected_at']} last={s['last_reported_at']} next={s['next_expected_at']}")
    return 0


# ---- apex budget check ------------------------------------------------------------------------------------------
def cmd_budget(args) -> int:
    from apex_worker.release.budget import DEFAULT_BUDGET_MINUTES, decide

    pol, psrc = _policy(args)
    budgets = (pol or {}).get("budgets") or {}
    raw_env = (os.environ.get("APEX_ACTIONS_MONTHLY_BUDGET") or "").strip()
    if args.budget_minutes is not None:
        budget, bsrc = args.budget_minutes, "flag"
    elif raw_env:
        try:
            budget, bsrc = int(raw_env), "env APEX_ACTIONS_MONTHLY_BUDGET"
        except ValueError:
            return _config_error(ValueError("APEX_ACTIONS_MONTHLY_BUDGET must be an integer"))
    elif isinstance(budgets.get("actions_monthly_minutes"), int):
        budget, bsrc = budgets["actions_monthly_minutes"], "server settings"
    else:
        budget, bsrc = DEFAULT_BUDGET_MINUTES, "default"
    usage = (pol or {}).get("usage_month") or {}
    measured = usage.get("minutes_used") if pol is not None else None
    dec = decide(
        args.job,
        datetime.now(UTC),
        budget_minutes=budget,
        measured_minutes=None if measured is None else float(measured),
        measured_runs=usage.get("runs"),
        measured_source=f"release API training_runs ({psrc})" if pol is not None else psrc,
        drift_enabled=bool(budgets.get("drift_enabled", True)),
        promotion_mode=str(((pol or {}).get("policy") or {}).get("promotion_mode") or "manual"),
    )
    out = dec.to_dict() | {"budget_source": bsrc, "server_mode": (pol or {}).get("mode")}
    _write_json(args.out, out)
    github_output(
        {
            "proceed": dec.proceed,
            "reason": dec.reason,
            "budget_minutes": dec.budget_minutes,
            "max_possible_month": dec.max_possible_month,
            "promotion_mode": dec.promotion_mode,
            "server_mode": (pol or {}).get("mode") or "unknown",
        },
        args.github_output,
    )
    print(f"budget[{args.job}]: {'PROCEED' if dec.proceed else 'SKIP'} - {dec.reason} "
          f"(max possible this month {dec.max_possible_month} min; budget {budget} from {bsrc})")
    return dec.exit_code


# ---- apex drift ---------------------------------------------------------------------------------------------------
def cmd_drift(args) -> int:  # linear orchestration, kept in one place for auditability
    from apex_worker.market.calendar import default_calendar
    from apex_worker.release.client import ReleaseConfigError, ReleaseError
    from apex_worker.release.drift import DriftConfig, champion_drift, overall_outcome, render_markdown
    from apex_worker.release.schedule import ET
    from apex_worker.release.upload import CandidateInvalid, verify_champions
    from apex_worker.research.model import CompetingModel

    now = datetime.now(UTC)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    payload: dict = {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "champions": [], "is_demo": False}

    def finish(rc: int = 0) -> int:
        _write_json(str(out / "drift.json"), payload)
        (out / "drift.md").write_text(render_markdown(payload))
        github_output({"outcome": payload.get("outcome") or "", "status": payload.get("status") or ""}, args.github_output)
        print(f"drift outcome={payload.get('outcome')} status={payload.get('status')} {payload.get('detail') or ''}")
        return rc

    if args.skip_non_session and not default_calendar().is_session(now.astimezone(ET).date()):
        payload.update({"status": "skipped_holiday", "outcome": None, "detail": "not an XNYS session: no new bar"})
        return finish()
    try:
        if args.champions_file:
            data = _read_json(args.champions_file) or {}
        else:
            with _client() as c:
                data = c.champions(include_artifacts=True)
        trusted = _trusted()
    except (ReleaseConfigError, CandidateInvalid) as exc:
        return _config_error(exc)
    except ReleaseError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    import base64

    checked = verify_champions(data, trusted)
    champs = []
    for m in data.get("manifests") or []:
        mid = str(m.get("model_id"))
        if not checked.get(mid, {}).get("ok"):
            payload["champions"].append({"model_id": mid, "strategy_id": m.get("strategy_id"), "status": "unverified",
                                         "breaches": [checked.get(mid, {}).get("error", "verification failed")]})
            continue
        art = base64.b64decode(str((data.get("artifacts") or {}).get(mid)))
        champs.append((m, CompetingModel.from_json(art.decode("utf-8"))))
    payload["signature_checked"] = bool(trusted)
    if not champs:
        payload.update({"status": "succeeded", "outcome": "no_data", "detail": "no verified champions to assess"})
        return finish(1 if payload["champions"] else 0)

    has_keys = bool((os.environ.get("ALPACA_KEY_ID") or "").strip() and (os.environ.get("ALPACA_SECRET_KEY") or "").strip())
    source = args.source if args.source != "auto" else ("alpaca" if has_keys else "synthetic")
    payload["source_used"] = source
    from apex_worker.research.dataset import DatasetSpec, build_events, data_hash
    from apex_worker.signals.strategies.library import STRATEGIES

    if source == "alpaca":
        loader, info = alpaca_loader_from_env(args)
        loaded = loader()
        payload["history"] = info.to_dict()
        if loaded is None:
            payload.update({"status": "succeeded", "outcome": "no_data", "detail": info.error or "history unavailable"})
            return finish()
        panel, bars, earnings, cats, ds = loaded
    else:
        from apex_worker.demo.synthetic import generate
        from apex_worker.features.panel import panel_from_synthetic

        market = generate(stocks_per_sector=24, start=date(2016, 1, 4))
        panel, bars, earnings, cats, ds = (
            panel_from_synthetic(market), {s: market.adjusted(s) for s in market.bars}, market.earnings, [], "synthetic_fixture")
        payload["is_demo"] = True
    payload["data_source"] = ds
    payload["data_hash"] = data_hash(bars)
    usable = []
    for m, model in champs:
        if ds == "synthetic_fixture" and not m.get("is_demo"):
            payload["champions"].append({"model_id": m.get("model_id"), "strategy_id": m.get("strategy_id"),
                                         "status": "insufficient_data", "breaches": [],
                                         "reason": "synthetic data cannot assess a live model"})
        elif m.get("strategy_id") in STRATEGIES:
            usable.append((m, model))
        else:
            payload["champions"].append({"model_id": m.get("model_id"), "strategy_id": m.get("strategy_id"),
                                         "status": "insufficient_data", "breaches": [],
                                         "reason": "strategy is not in this code version's library: no events to score"})
    if usable:
        import pandas as pd

        last = max(pd.Timestamp(b.index.max()) for b in bars.values() if len(b))
        spec = DatasetSpec(strategies=[STRATEGIES[m["strategy_id"]] for m, _ in usable],
                           start=last - timedelta(days=int(365.25 * args.window_years)))
        events = build_events(panel, bars, spec, catalysts=cats, earnings=earnings)
        cfg = DriftConfig()
        for m, model in usable:
            payload["champions"].append(champion_drift(m, model, events, cfg))
    payload["outcome"] = overall_outcome(payload["champions"])
    payload["status"] = "succeeded"
    if payload["outcome"] == "no_data":
        payload["detail"] = "too few resolved events or unusable champions: drift UNKNOWN (not a pass)"
    return finish()


# ---- registration -------------------------------------------------------------------------------------------------
def register(sub) -> None:
    rel = sub.add_parser("release", help="CI release API: policy, champions, upload, report-run, promote, rollback, sla")
    rs = rel.add_subparsers(dest="release_cmd", required=True)

    def common(p, *, policy: bool = False):
        p.add_argument("--out", help="write the JSON result here (no secrets)")
        p.add_argument("--github-output", action="store_true", help="append key=value pairs to $GITHUB_OUTPUT")
        if policy:
            p.add_argument("--policy", help="policy JSON from `apex release policy --out` (else fetched)")
            p.add_argument("--offline", action="store_true", help="never call the release API for policy")

    pf = rs.add_parser("preflight", help="no network: report PRODUCTION_REGISTRY_UNCONFIGURED before any registry call")
    common(pf)
    pf.add_argument("--fail-when-unconfigured", action="store_true", help="exit 2 instead of skipping (manual runs)")
    pf.set_defaults(func=cmd_preflight)
    po = rs.add_parser("policy", help="deployment mode, promotion policy, budgets, month usage, last runs")
    common(po)
    po.set_defaults(func=cmd_policy)
    ch = rs.add_parser("champions", help="champion summaries (for `apex train --champions FILE`)")
    common(ch)
    ch.add_argument("--verify", action="store_true", help="also fetch artifacts and re-verify checksum/signature")
    ch.set_defaults(func=cmd_champions)
    up = rs.add_parser("upload", help="register signed candidates from a training output dir")
    common(up, policy=True)
    up.add_argument("--dir", required=True)
    up.add_argument("--include-non-promotable", action="store_true")
    up.add_argument("--training-run-id")
    up.set_defaults(func=cmd_upload)
    rr = rs.add_parser("report-run", help="report a training/drift/release run for the dashboard")
    common(rr)
    rr.add_argument("--workflow", required=True, choices=["train_weekly", "drift", "release", "local_replay"])
    rr.add_argument("--status", required=True, help="succeeded|failed|cancelled|skipped_budget|skipped_holiday or job.status")
    rr.add_argument("--outcome", default="auto", help="auto (derive from result files), none, or an explicit outcome")
    rr.add_argument("--trigger", help="schedule|workflow_dispatch|admin_dispatch|local (default: from GITHUB_EVENT_NAME)")
    rr.add_argument("--started-at", help="ISO-8601 or epoch seconds (first step of the job)")
    rr.add_argument("--dir", help="training output dir (report.json, history.json)")
    rr.add_argument("--upload", help="JSON from `apex release upload --out`")
    rr.add_argument("--budget", help="JSON from `apex budget check --out`")
    rr.add_argument("--drift", help="drift.json from `apex drift`")
    rr.add_argument("--release-result", help="JSON from `apex release promote|rollback --out`")
    rr.add_argument("--candidate")
    rr.add_argument("--reason")
    rr.add_argument("--source-requested")
    rr.add_argument("--source-used")
    rr.add_argument("--is-demo", action="store_true")
    rr.add_argument("--dry-run", action="store_true", help="print the payload, do not send")
    rr.set_defaults(func=cmd_report_run)
    for action in ("promote", "rollback"):
        pp = rs.add_parser(action, help=f"{action} model id(s) through the release API (server re-applies the frozen policy)")
        common(pp)
        pp.add_argument("--model-ids", required=True, help="mdl_... (several: comma separated)")
        pp.add_argument("--reason", default="")
        pp.add_argument("--fail-on-reject", action="store_true")
        pp.set_defaults(func=cmd_promotion)
    sl = rs.add_parser("sla", help="missed-run status of the scheduled workflows (from the latest reported runs)")
    common(sl, policy=True)
    sl.set_defaults(func=cmd_sla)

    dr = sub.add_parser("drift", help="lightweight drift diagnostic of registry champions on the latest window")
    dr.add_argument("--source", choices=["auto", "alpaca", "synthetic"], default="auto")
    dr.add_argument("--out", default=".apex-local/drift")
    dr.add_argument("--champions-file", help="offline: JSON shaped like GET /api/v1/release/champions?include=artifacts")
    dr.add_argument("--skip-non-session", action="store_true", help="report skipped_holiday when today is not a session")
    dr.add_argument("--window-years", type=float, default=3.0)
    dr.add_argument("--github-output", action="store_true")
    add_history_args(dr)
    dr.set_defaults(func=cmd_drift)

    bg = sub.add_parser("budget", help="GitHub Actions minutes budget gate")
    bs = bg.add_subparsers(dest="budget_cmd", required=True)
    bc = bs.add_parser("check", help="exit 0 proceed, 3 skip (budget/disabled), 2 config error")
    common(bc, policy=True)
    bc.add_argument("--job", required=True, choices=["train", "release", "drift"])
    bc.add_argument("--budget-minutes", type=int, help="override (else APEX_ACTIONS_MONTHLY_BUDGET, server setting, 1500)")
    bc.set_defaults(func=cmd_budget)
