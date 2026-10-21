"""Phase 14 real-equity research runner: protocol, data checks, execution/returns, dispositions and output hygiene.

Everything here runs offline. The end-to-end run uses the REAL-SOURCE EMULATION fixture (Alpaca wire format over the
synthetic market) and must come out labelled DEMO; nothing in this file is evidence about real equities.
"""

import asyncio
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest

from apex_worker.market.calendar import default_calendar
from apex_worker.market.history import HistoryResult, HistorySpec
from apex_worker.research.dataset import DatasetSpec, build_events
from apex_worker.research.evaluate import net_at_cost
from apex_worker.research.execution import ExecConfig, simulate
from apex_worker.research.realeq import analysis as A
from apex_worker.research.realeq import report as R
from apex_worker.research.realeq.data import (
    STATUS_BLOCKED,
    STATUS_INSUFFICIENT,
    STATUS_SOURCE_INVALID,
    InspectingAdapter,
    Loaded,
    RawStats,
    RunStop,
    credentials_from_env,
    default_spec,
    fetch,
    quality_inventory,
    symbol_roles,
)
from apex_worker.research.realeq.fixture import FIXTURE_NOW, FakeAlpaca, universe_frames
from apex_worker.research.realeq.protocol import (
    PROTOCOL,
    PROTOCOL_VERSION,
    check_years,
    compute_boundaries,
    protocol_sha256,
    sanitize_experiment_id,
)
from apex_worker.research.realeq.runner import REPO_ROOT, RunConfig, run
from apex_worker.research.training_data import BENCHMARK, universe_meta, universe_symbols
from apex_worker.signals.strategies.library import STRATEGIES

CAL = default_calendar()
PKG = Path(__file__).resolve().parents[1] / "src" / "apex_worker" / "research" / "realeq"


@pytest.fixture(scope="module")
def frames():
    return universe_frames(date(2022, 10, 3))


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    out = tmp_path_factory.mktemp("realeq") / "demo-pytest-fixed78"
    outcome = run(RunConfig(source="fixture", experiment_id="demo-pytest-fixed78", out_dir=out, years=3))
    return outcome, out


# ---- protocol --------------------------------------------------------------------------------------------------
def test_experiment_id_is_a_safe_slug():
    assert sanitize_experiment_id(" 2026-10-Fixed78-SIP-8y ") == "2026-10-fixed78-sip-8y"
    for bad in ("../etc", "a b c d", "x;rm -rf", "abc--def", "ab", "-abc", "abc-", "a" * 70, "über-run", ""):
        with pytest.raises(ValueError):
            sanitize_experiment_id(bad)
    assert check_years(8) == 8
    for bad in (2, 11, 0):
        with pytest.raises(ValueError):
            check_years(bad)


def test_protocol_document_matches_the_frozen_code():
    doc = (REPO_ROOT / "research-results" / "real-equities" / "PROTOCOL.md").read_text()
    assert PROTOCOL_VERSION in doc and protocol_sha256() in doc, "PROTOCOL.md must quote the version and sha256"
    assert PROTOCOL["primary_horizon"] == 20 and PROTOCOL["primary_cost_bps"] == 25
    assert PROTOCOL["costs_bps_per_side"] == [0, 10, 25, 50] and PROTOCOL["horizons"] == [5, 10, 15, 20]
    assert protocol_sha256() == protocol_sha256(json.loads(json.dumps(PROTOCOL)))  # stable under round trip


def test_historical_split_is_chronological_embargoed_and_label_complete():
    sessions = CAL.sessions_between(date(2018, 10, 22), date(2026, 10, 16))
    b = compute_boundaries(sessions)
    idx = {d: i for i, d in enumerate(sessions)}
    to = date.fromisoformat
    assert idx[to(b.decision_start)] == PROTOCOL["split"]["warmup_sessions"]
    assert to(b.decision_start) < to(b.development_end) < to(b.holdout_start) <= to(b.holdout_end) < to(b.data_end)
    assert idx[to(b.holdout_start)] == idx[to(b.development_end)] + 1
    assert idx[to(b.holdout_start)] - idx[to(b.embargo_limit)] == PROTOCOL["split"]["embargo_sessions"]
    assert len(sessions) - 1 - idx[to(b.holdout_end)] == PROTOCOL["split"]["label_complete_sessions"] == b.label_unavailable_sessions
    assert abs(b.holdout_sessions / b.decision_sessions - PROTOCOL["split"]["holdout_fraction"]) < 0.01
    later = compute_boundaries(sessions, time_start=date(2021, 1, 4))
    assert later.decision_start == "2021-01-04" and later.holdout_end == b.holdout_end
    with pytest.raises(ValueError):
        compute_boundaries(sessions[:300])


# ---- credentials / source ------------------------------------------------------------------------------------------
def test_missing_keys_block_with_no_synthetic_fallback(tmp_path):
    called = []
    outcome = run(RunConfig(source="alpaca", experiment_id="2026-10-blocked-check", out_dir=tmp_path, env={},
                            adapter_factory=lambda: called.append(1)))
    assert outcome.status == STATUS_BLOCKED and outcome.exit_code == 2 and not called, "no download may be attempted"
    assert "BLOCKED — ALPACA_ACTIONS_SECRETS_MISSING" in outcome.reason
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(R.OUTPUT_FILES)
    metrics = json.loads((tmp_path / "strategy_metrics.json").read_text())
    assert metrics["run_status"] == STATUS_BLOCKED and metrics["aggregate"] == {"conclusion": "NOT RUN"}
    assert set(metrics["strategies"]) == set(STRATEGIES) and all(s["disposition"] is None for s in metrics["strategies"].values())
    assert "No backtest is claimed" in (tmp_path / "comparisons.md").read_text()
    log = (tmp_path / "execution_log.md").read_text()
    assert "absent" in log and "real-equities-research" in log and "not_run" in log
    with pytest.raises(RunStop) as e:
        credentials_from_env({"ALPACA_KEY_ID": "PKSOMETHING", "ALPACA_SECRET_KEY": "  "})
    assert e.value.status == STATUS_BLOCKED and "PKSOMETHING" not in e.value.reason


def test_wrong_feed_is_source_invalid(tmp_path, frames):
    fake = FakeAlpaca(frames)
    with pytest.raises(RunStop) as e:
        fetch(lambda: fake.adapter(FIXTURE_NOW), tmp_path, HistorySpec(symbols=universe_symbols(), years=3, feed="iex"),
              end=date(2026, 10, 16), now=FIXTURE_NOW)
    assert e.value.status == STATUS_SOURCE_INVALID and "sip" in e.value.reason
    insp = InspectingAdapter(fake.adapter(FIXTURE_NOW))
    with pytest.raises(RunStop):
        asyncio.run(insp.get_daily_bars(["SPY"], date(2026, 1, 2), date(2026, 2, 2), feed="iex"))
    assert not fake.requests, "a wrong-feed request never reaches the provider"


def test_auth_failure_is_source_invalid_and_never_echoes_keys(tmp_path):
    from apex_worker.market.adapters.alpaca import AlpacaAdapter

    def handler(_req):
        return httpx.Response(403, json={"message": "subscription does not permit querying recent SIP data"})

    def factory():
        return AlpacaAdapter("PKSECRETKEYID123", "very-secret-value", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    outcome = run(RunConfig(source="alpaca", experiment_id="2026-10-auth-check", out_dir=tmp_path, years=3,
                            env={"ALPACA_KEY_ID": "PKSECRETKEYID123", "ALPACA_SECRET_KEY": "very-secret-value"},
                            adapter_factory=factory, now=FIXTURE_NOW))
    assert outcome.status == STATUS_SOURCE_INVALID and outcome.exit_code == 3 and "403" in outcome.reason
    blob = "".join(p.read_text() for p in tmp_path.iterdir())
    assert "PKSECRETKEYID123" not in blob and "very-secret-value" not in blob


class _StubAdapter:
    """Provider stub returning given frames verbatim (lets tests send future/duplicate/short histories)."""

    def __init__(self, frames):
        self.frames, self.requests = frames, 0

    async def get_daily_bars(self, symbols, start, end, adjustment="split", feed="sip"):
        self.requests += 1
        return {s: self.frames[s] for s in symbols if s in self.frames}

    async def aclose(self):
        return None


def _load(frames, tmp_path, end=date(2026, 10, 16)):
    return fetch(lambda: _StubAdapter(frames), tmp_path, default_spec(3), end=end, now=FIXTURE_NOW)


def test_provider_bar_after_latest_session_is_source_invalid(tmp_path, frames):
    bad = dict(frames)
    spy = frames[BENCHMARK]
    extra = spy.iloc[[-1]].copy()
    extra.index = pd.DatetimeIndex([pd.Timestamp("2026-10-19")])  # the session AFTER the run's latest completed one
    bad[BENCHMARK] = pd.concat([spy[spy.index <= "2026-10-16"], extra])
    with pytest.raises(RunStop) as e:
        quality_inventory(_load(bad, tmp_path))
    assert e.value.status == STATUS_SOURCE_INVALID and "after the latest completed session" in e.value.reason
    assert e.value.body["symbols_with_future_bars"] == 1  # the inventory is still produced for the record


def test_incomplete_history_excludes_symbols_and_short_benchmark_aborts(tmp_path, frames):
    short = dict(frames)
    short["AAPL"] = frames["AAPL"].iloc[-200:]
    short["MSFT"] = frames["MSFT"][frames["MSFT"].index <= "2026-09-01"]  # stale: stopped updating
    dupl = frames["JPM"]
    short["JPM"] = pd.concat([dupl, dupl.iloc[[10]]])  # one raw duplicate row
    body, usable = quality_inventory(_load(short, tmp_path / "a"))
    by = {s["symbol"]: s for s in body["symbols"]}
    assert by["AAPL"]["status"] == "EXCLUDED_INCOMPLETE_HISTORY" and "AAPL" not in usable
    assert by["MSFT"]["status"] == "EXCLUDED_STALE" and by["MSFT"]["stale_sessions"] > 3
    assert by["JPM"]["raw_duplicate_rows"] == 1 and by["JPM"]["status"] == "OK"
    assert by["XLC"]["notes"] and body["corporate_actions"].startswith("UNAVAILABLE")
    assert body["counts"]["requested"] == 78 and not any(k in json.dumps(body) for k in ('"open"', '"close"', '"volume"'))
    tiny = dict(frames)
    tiny[BENCHMARK] = frames[BENCHMARK].iloc[-300:]
    with pytest.raises(RunStop) as e:
        quality_inventory(_load(tiny, tmp_path / "b"))
    assert e.value.status == STATUS_INSUFFICIENT and BENCHMARK in e.value.reason


def test_dataset_counts_and_only_stocks_are_traded(frames):
    roles = symbol_roles()
    kinds = [r for r, _ in roles.values()]
    assert len(roles) == 78 and kinds.count("benchmark") == 1 and kinds.count("sector_etf") == 11
    assert kinds.count("candidate_stock") == 66 and set(roles) == set(universe_symbols())
    stocks = [s for s, m in universe_meta().items() if m.asset_kind == "stock"]
    assert len(stocks) == 66 and not set(stocks) & {"SPY", "XLK", "XLC", "XLRE"}


# ---- point-in-time correctness ---------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def panel_events(frames):
    from apex_worker.features.panel import build_panel
    from apex_worker.research.training_data import SECTOR_ETFS

    def make(bars):
        meta = {s: m for s, m in universe_meta().items() if s in bars}
        panel = build_panel(bars, meta, BENCHMARK, SECTOR_ETFS)
        stocks = sorted(s for s, m in meta.items() if m.asset_kind == "stock")
        spec = DatasetSpec(strategies=list(STRATEGIES.values()), execution=ExecConfig(cost_bps_per_side=0.0),
                           start=pd.Timestamp("2023-11-01"))
        return build_events(panel, bars, spec, symbols=stocks)

    return make


def test_no_future_bars_change_any_past_decision(frames, panel_events):
    full = panel_events(frames)
    cut = pd.Timestamp("2025-06-02")
    trunc = panel_events({s: df[df.index < cut] for s, df in frames.items()})
    horizon_ok = CAL.add_sessions(date(2025, 6, 2), 0)
    limit = pd.Timestamp(CAL.sessions_between(date(2025, 1, 1), horizon_ok)[-1 - 23])
    key = ["symbol", "strategy_id", "decision_date"]
    a = full[full["decision_date"] <= limit].set_index(key).sort_index()
    b = trunc[trunc["decision_date"] <= limit].set_index(key).sort_index()
    assert len(a) > 50 and a.index.equals(b.index), "removing future bars must not add or remove past signals"
    cols = ["quality", "setup_score", "rs_bench_20", "squeeze_prev", "entry_min", "stop", "target", "outcome", "return_gross"]
    pd.testing.assert_frame_equal(a[cols], b[cols])


def test_label_availability_no_decision_without_a_complete_label(frames):
    from apex_worker.features.panel import build_panel
    from apex_worker.research.training_data import SECTOR_ETFS

    sessions = pd.DatetimeIndex(frames[BENCHMARK].index)
    b = compute_boundaries(sessions)
    meta = {s: m for s, m in universe_meta().items() if s in frames}
    panel = build_panel(frames, meta, BENCHMARK, SECTOR_ETFS)
    stocks = sorted(s for s, m in meta.items() if m.asset_kind == "stock")
    ev = build_events(panel, frames, DatasetSpec(strategies=list(STRATEGIES.values()),
                                                 execution=ExecConfig(cost_bps_per_side=0.0),
                                                 start=pd.Timestamp(b.decision_start), end=pd.Timestamp(b.holdout_end)),
                      symbols=stocks)
    assert len(ev) and ev["decision_date"].max() <= pd.Timestamp(b.holdout_end)
    full_len = {s: len(frames[s]) for s in stocks}
    long_enough = ev[ev["symbol"].map(full_len) == len(sessions)]
    assert not (long_enough["outcome"] == "DATA_END").any(), "every in-window decision has its full 22-session label"


# ---- execution and returns ---------------------------------------------------------------------------------------
def _flat(n=40, px=100.0):
    o = np.full(n, px)
    return o.copy(), o + 0.5, o - 0.5, o.copy()


def test_horizon_expiry_exits_at_the_close_h_sessions_after_fill():
    for h in (5, 10, 15, 20):
        o, hi, lo, c = _flat()
        r = simulate(o, hi, lo, c, 3, 99.5, 100.5, 95.0, 110.0, ExecConfig(cost_bps_per_side=0.0, horizon_sessions=h))
        assert r.outcome == "TIME" and r.entry_index == 4 and r.exit_index == 4 + h and r.sessions_held == h


def test_same_bar_ambiguity_is_stop_first_and_unresolved_sensitivity_excludes_it():
    o, hi, lo, c = _flat()
    hi[6], lo[6] = 112.0, 94.0  # one bar touches both target and stop
    r = simulate(o, hi, lo, c, 3, 99.5, 100.5, 95.0, 110.0, ExecConfig(cost_bps_per_side=0.0))
    assert r.outcome == "STOP" and r.ambiguous_same_bar and r.exit_price == 95.0
    o2, hi2, lo2, c2 = _flat()
    hi2[7] = 111.0
    w = simulate(o2, hi2, lo2, c2, 3, 99.5, 100.5, 95.0, 110.0, ExecConfig(cost_bps_per_side=0.0))
    assert w.outcome == "TARGET" and not w.ambiguous_same_bar
    df = pd.DataFrame([
        {"symbol": s, "decision_date": pd.Timestamp(d), "filled": True, "outcome": x.outcome, "return_gross": x.return_gross,
         "entry_price": x.entry_price, "exit_price": x.exit_price, "stop": 95.0, "sessions_held": x.sessions_held,
         "mfe": x.mfe, "mae": x.mae, "ambiguous": x.ambiguous_same_bar, "gap_stop": x.gap_through_stop, "regime": "trending_up"}
        for s, d, x in (("AAA", "2026-01-05", r), ("BBB", "2026-01-12", w))])
    st = A.trade_stats(df, 0)
    assert st["ambiguous_same_bar"] == 1 and st["completed"] == 2
    assert st["mean_net_unresolved_excluded"] == pytest.approx(0.10, abs=1e-6)
    assert st["mean_net"] == pytest.approx((0.10 - 0.05) / 2, abs=1e-6)


def test_gap_through_stop_exits_at_the_worse_open():
    o, hi, lo, c = _flat()
    o[7], hi[7], lo[7], c[7] = 90.0, 91.0, 89.0, 90.5
    r = simulate(o, hi, lo, c, 3, 99.5, 100.5, 95.0, 110.0, ExecConfig(cost_bps_per_side=0.0))
    assert r.outcome == "STOP" and r.gap_through_stop and r.exit_price == 90.0


def test_net_return_and_r_multiple_at_cost():
    gross = 110 / 100 - 1
    for bps in (0, 10, 25, 50):
        c = bps / 1e4
        assert A.net_at([gross], bps)[0] == pytest.approx(1.1 * (1 - c) / (1 + c) - 1)
        assert A.net_at([gross], bps)[0] == pytest.approx(net_at_cost(pd.Series([gross]), bps).iloc[0])
        assert A.r_at([100], [110], [95], bps)[0] == pytest.approx((110 * (1 - c) - 100 * (1 + c)) / (100 * (1 + c) - 95))
    assert np.isnan(A.r_at([100], [110], [101], 25)[0])  # no risk => undefined, never infinite


def test_dedup_skips_signals_while_a_position_is_pending_or_open():
    d = pd.to_datetime
    df = pd.DataFrame({"strategy_id": "h1", "symbol": ["A", "A", "A", "B"],
                       "decision_date": d(["2026-01-05", "2026-01-06", "2026-01-20", "2026-01-06"]),
                       "exit_date": d(["2026-01-15", "2026-01-16", "2026-01-30", "2026-01-08"])})
    kept = A.dedup(df)
    assert list(kept["decision_date"].dt.day) == [5, 6, 20] and list(kept["symbol"]) == ["A", "B", "A"]


def test_portfolio_respects_slots_and_marks_to_market():
    sessions = pd.DatetimeIndex(CAL.sessions_between(date(2026, 1, 2), date(2026, 3, 31)))
    closes = {f"S{i}": pd.Series(100.0, index=sessions) for i in range(12)}
    rows = [{"symbol": f"S{i}", "decision_date": sessions[0], "entry_date": sessions[1], "exit_date": sessions[10],
             "entry_price": 100.0, "exit_price": 110.0, "filled": True, "outcome": "TARGET"} for i in range(12)]
    p = A.portfolio(pd.DataFrame(rows), closes, sessions, pd.Series(np.linspace(100, 101, len(sessions)), index=sessions))
    assert p["trades_taken"] == 10 and p["trades_skipped_capacity"] == 2 and p["max_positions"] == 10
    c = PROTOCOL["portfolio"]["cost_bps"] / 1e4
    assert p["total_return"] == pytest.approx(110 * (1 - c) / (100 * (1 + c)) - 1, abs=1e-6)  # all equity, +10% gross


# ---- dispositions ----------------------------------------------------------------------------------------------------
def _row(**kw):
    hold = {"completed": 150, "mean_net": 0.01, "mean_net_ci": {"p10": 0.004, "p90": 0.016}, "distinct_weeks": 40,
            "regimes": 3}
    base = {"requires_catalysts": False, "holdout": hold, "development": {"completed": 400}, "holdout_50bps_mean": 0.005,
            "matched_random": {"median": 0.001, "p95": 0.004}, "forecast_status": "EXPERIMENTAL",
            "sector_rule": {"A": {"mean_net": 0.01}, "B": {"mean_net": 0.008}}}
    for k, v in kw.items():
        if k in hold:
            base["holdout"] = {**base["holdout"], k: v}
        else:
            base[k] = v
    return base


def test_frozen_disposition_rules():
    assert A.classify(_row(), 0.02, demo=False)[0] == "VALIDATED_REAL_OOS"
    assert A.classify(_row(), 0.02, demo=True)[0] == "UNVALIDATED", "synthetic data can never be validated or promising"
    assert A.classify(_row(), 0.20, demo=False)[0] == "PROMISING_NEEDS_FORWARD_TEST"  # multiple-testing fails
    assert A.classify(_row(forecast_status="UNVALIDATED"), 0.02, demo=False)[0] == "PROMISING_NEEDS_FORWARD_TEST"
    assert A.classify(_row(mean_net=-0.01, mean_net_ci={"p10": -0.02, "p90": -0.002}), 0.9, demo=False)[0] == "FAILED_REAL_OOS"
    assert A.classify(_row(mean_net=0.0005, mean_net_ci={"p10": -0.004, "p90": 0.005}), 0.5, demo=False)[0] == "FAILED_REAL_OOS"
    assert A.classify(_row(completed=60, mean_net=-0.001, mean_net_ci={"p10": -0.01, "p90": 0.008}), 0.5, demo=False)[0] \
        == "UNVALIDATED"
    assert A.classify(_row(completed=12), None, demo=False)[0] == "INSUFFICIENT_HISTORY"
    assert A.classify(_row(requires_catalysts=True), None, demo=False)[0] == "NOT_EVALUABLE_MISSING_INPUT"


def test_holm_adjustment_is_monotone():
    adj = A.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj == {"a": 0.03, "c": 0.06, "b": 0.06}


# ---- end-to-end DEMO run and output hygiene ------------------------------------------------------------------------------
def test_demo_run_writes_the_seven_files_labelled_demo(demo):
    outcome, out = demo
    assert outcome.status == "COMPLETED" and outcome.exit_code == 0
    assert sorted(p.name for p in out.iterdir()) == sorted(R.OUTPUT_FILES)
    manifest = json.loads((out / "manifest.json").read_text())
    metrics = json.loads((out / "strategy_metrics.json").read_text())
    assert manifest["data_class"] == metrics["data_class"] == "SYNTHETIC_FIXTURE_DEMO"
    assert manifest["status_at_freeze"] == "FROZEN_BEFORE_METRICS" and manifest["research_only"] is True
    assert manifest["versions"]["protocol_sha256"] == protocol_sha256()
    import hashlib

    assert metrics["manifest_sha256"] == hashlib.sha256((out / "manifest.json").read_bytes()).hexdigest()
    for md in ("comparisons.md", "recommendations.md", "execution_log.md"):
        assert "DEMO — SYNTHETIC FIXTURE" in (out / md).read_text()


def test_demo_run_records_every_hypothesis_and_never_validates(demo):
    _, out = demo
    m = json.loads((out / "strategy_metrics.json").read_text())
    assert set(m["strategies"]) == set(STRATEGIES)
    disp = {s: r["disposition"] for s, r in m["strategies"].items()}
    assert disp["h3_post_event_drift"] == disp["h7_catalyst_compression"] == "NOT_EVALUABLE_MISSING_INPUT"
    assert not {"VALIDATED_REAL_OOS", "PROMISING_NEEDS_FORWARD_TEST"} & set(disp.values())
    assert m["aggregate"]["conclusion"] == "NO_TRADE" and "DISABLED" in m["aggregate"]["live_alerts"]
    h8 = m["strategies"]["h8_turn_of_month"]
    assert h8["default_enabled"] is False and h8["weak_prior"] is True
    for sid, r in m["strategies"].items():
        f = r["funnel"]
        assert f["triggered"] >= f["gate_eligible"] >= f["quality_scored"] >= f["levels_valid"], sid
        assert f["regime_allowed"] >= f["rule_after_dedup"] >= f["filled"] >= f["completed_horizon"], sid
    assert len(m["configurations_attempted"]) > 100
    for name in ("momentum_20", "breakout_basic", "spy_buy_and_hold", "always_flat"):
        assert name in m["baselines"]


def test_outputs_contain_no_vendor_prices_or_trade_rows(demo):
    _, out = demo
    header = (out / "trades_summary.csv").read_text().splitlines()[0].split(",")
    assert tuple(header) == R.CSV_COLUMNS
    forbidden = re.compile(r'"(open|high|low|close|volume|vwap|entry_price|exit_price|entry_min|entry_max|stop|target)"\s*:')
    for name in ("manifest.json", "data_quality.json", "strategy_metrics.json"):
        text = (out / name).read_text()
        assert not forbidden.search(text), name
        assert "NaN" not in text and "Infinity" not in text
    assert all(len(line) < 400 for line in (out / "trades_summary.csv").read_text().splitlines())


def test_fixture_runs_can_never_be_filed_as_research_results(tmp_path):
    target = REPO_ROOT / "research-results" / "real-equities" / "demo-should-not-exist"
    o = run(RunConfig(source="fixture", experiment_id="demo-should-not-exist", out_dir=target, years=3))
    assert o.status == "INVALID_INPUT" and o.exit_code == 5 and not target.exists()
    o2 = run(RunConfig(source="fixture", experiment_id="looks-real-run", out_dir=tmp_path / "x", years=3))
    assert o2.status == "INVALID_INPUT" and "demo-" in o2.reason and not (tmp_path / "x").exists()


def test_research_package_never_touches_release_signing_discord_or_orders():
    banned = ("release.client", "release.upload", "models.manifest", "discord", "agent.client", "signing_key", "/orders",
              "submit_order", "model-release")
    for py in PKG.glob("*.py"):
        text = py.read_text()
        hits = [b for b in banned if b in text]
        assert not hits, f"{py.name}: {hits}"


def test_budget_checkpoint(monkeypatch, capsys):
    from apex_worker.cli import main

    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("APEX_ACTIONS_MONTHLY_BUDGET", raising=False)
    assert main(["research", "budget-check", "--timeout-minutes", "30"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["proceed"] and body["measured_minutes"] is None and body["worst_case_month"] <= body["budget_minutes"]
    monkeypatch.setenv("APEX_ACTIONS_MONTHLY_BUDGET", "40")
    assert main(["research", "budget-check", "--timeout-minutes", "30"]) == 3


def test_cli_rejects_bad_inputs_without_writing(tmp_path):
    from apex_worker.cli import main

    assert main(["research", "real-equities", "--source", "alpaca", "--experiment-id", "../../x",
                 "--out", str(tmp_path / "o")]) == 5
    assert main(["research", "real-equities", "--source", "alpaca", "--experiment-id", "ok-run-id", "--years", "20",
                 "--out", str(tmp_path / "o")]) == 5
    assert not (tmp_path / "o").exists()


def test_inspector_counts_raw_rows_before_cleaning():
    idx = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-06", "2026-01-07"])
    df = pd.DataFrame({"open": [10, 10, 10, 10.0], "high": [11, 11, 11, 9.0], "low": [9, 9, 9, 9.5],
                       "close": [10, 10, 10, 10.0], "volume": [100, 0, 100, 100]}, index=idx)

    class One:
        requests = 0

        async def get_daily_bars(self, symbols, start, end, adjustment="split", feed="sip"):
            return {"AAA": df}

    insp = InspectingAdapter(One())
    asyncio.run(insp.get_daily_bars(["AAA"], date(2026, 1, 1), date(2026, 1, 9)))
    st = insp.raw["AAA"]
    assert (st.rows, st.duplicate_rows, st.invalid_rows, st.zero_volume_rows) == (4, 1, 1, 1)
    assert st.last == "2026-01-07" and isinstance(st, RawStats)


def test_loaded_type_is_constructible_for_offline_review():
    res = HistoryResult({}, date(2026, 1, 1), date(2026, 1, 2), "h", 0)
    Loaded(res, InspectingAdapter(object()), None, date(2026, 1, 2), datetime(2026, 1, 2, tzinfo=UTC).isoformat())
