"""personal-study-4 (docs/discovery-v3/PERSONAL_STUDY_4.md): the S&P 500 timing methods rebuilt on a century of daily
market returns: the French library file's parser and checks, the rebuilt funds, each rule's decisions (the same as the
live engine's on the same series), the next-close simulation and the registered plan. Synthetic data only; no network."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date

import numpy as np
import pandas as pd
import pytest

from apex_worker.discovery.v3 import long_history as L
from apex_worker.discovery.v3 import personal_study as PS
from apex_worker.market.calendar import default_calendar
from apex_worker.personal import method as M
from apex_worker.research.realeq.data import RunStop

CAL = default_calendar()
R = M.METHODS
# Registered before the run that evaluates it; any change needs a new plan id.
PERSONAL_STUDY_4_SHA256 = "ea525e61d08455ed2bd734a75f3f508f1c85dc152ab3cd9f504b27c44541d333"


def french_zip(days: list[str], mkt_rf: list[float], rf: list[float], header: bool = True) -> bytes:
    lines = ["This file was created by using the 202609 CRSP database.", "The Tbill return is the simple daily rate.", ""]
    if header:
        lines.append(",Mkt-RF,SMB,HML,RF")
    lines += [f"{d},{m:8.2f},{0.0:8.2f},{0.0:8.2f},{r:8.2f}" for d, m, r in zip(days, mkt_rf, rf, strict=True)]
    lines += ["", "Copyright 2026 Eugene F. Fama and Kenneth R. French"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("F-F_Research_Data_Factors_daily.csv", "\r\n".join(lines))
    return buf.getvalue()


def history(closes: pd.Series, bills: float = 0.0001) -> L.DailyHistory:
    """A rebuilt history whose market index is proportional to `closes` (the live engine's SPY series)."""
    market = closes.pct_change().fillna(0.0).to_numpy()
    dates = np.array([d.date() for d in closes.index], dtype="datetime64[D]")
    return L.DailyHistory(dates, market, np.full(len(market), bills), {}, {})


def sessions_series(values, start=date(2023, 1, 3)) -> pd.Series:
    days = CAL.sessions_between(start, CAL.add_sessions(start, len(values) - 1))
    return pd.Series(values, index=pd.DatetimeIndex([pd.Timestamp(d) for d in days]), dtype=float)


def wavy(n: int = 700, seed: int = 3) -> pd.Series:
    """A rising, choppy synthetic index with dips (it crosses its 200-day average and its RSI(2) extremes)."""
    rng = np.random.default_rng(seed)
    r = 0.0003 + 0.012 * rng.standard_normal(n) + 0.01 * np.sin(np.arange(n) / 40)
    return sessions_series(100 * np.exp(np.cumsum(r)))


# ---- the file -------------------------------------------------------------------------------------------------------
def test_the_daily_factors_file_parses_to_market_and_t_bill_returns():
    raw = french_zip(["19260701", "19260702", "19260706"], [0.09, 0.45, 0.17], [0.01, 0.01, 0.01])
    dates, market, bills, notes = L.parse_french_daily(raw)
    assert [str(d) for d in dates] == ["1926-07-01", "1926-07-02", "1926-07-06"]
    assert market == pytest.approx([0.0010, 0.0046, 0.0018]) and bills == pytest.approx([0.0001] * 3)
    assert "202609 CRSP" in notes["header"] and notes["rows"] == 3
    with pytest.raises(ValueError, match="header"):
        L.parse_french_daily(french_zip(["19260701"], [0.1], [0.01], header=False))
    with pytest.raises(ValueError, match="zip"):
        L.parse_french_daily(b"not a zip")


def test_the_history_checks_reject_a_short_unordered_or_implausible_file():
    d = np.arange(np.datetime64("1926-07-01"), np.datetime64("1926-07-01") + np.timedelta64(30_000, "D"),
                     dtype="datetime64[D]")
    ok = np.full(len(d), 0.0004)
    assert L.check_history(d, ok, ok / 4) == []
    assert "only 10 daily rows" in L.check_history(d[:10], ok[:10], ok[:10])[0]
    assert any("not 1926-07-01" in b for b in L.check_history(d[1:], ok[1:], ok[1:]))
    assert any("strictly increasing" in b for b in L.check_history(d[::-1], ok, ok))
    wild = ok.copy()
    wild[5] = 0.5
    assert any("plausible" in b for b in L.check_history(d, wild, ok / 4))


def test_the_download_records_its_sha256_and_failures_stop_the_run():
    days = np.arange(np.datetime64("1926-07-01"), np.datetime64("1926-07-01") + np.timedelta64(40_000, "D"),
                     dtype="datetime64[D]")
    days = days[np.is_busday(days)][:26_000]
    raw = french_zip([str(d).replace("-", "") for d in days], [0.04] * len(days), [0.01] * len(days))
    h = L.load_history(L.PERSONAL_STUDY_4, "french", fetch=lambda url: raw)
    assert h.provenance["data_class"] == "REAL_MARKET_DATA" and len(h.provenance["file_sha256"]) == 64
    assert h.provenance["url"] == L.FRENCH_DAILY_URL and h.data_quality["rows"] == 26_000

    def refused(url):
        raise OSError("connection reset")

    with pytest.raises(RunStop) as err:
        L.load_history(L.PERSONAL_STUDY_4, "french", fetch=refused)
    assert err.value.status == "SOURCE_BLOCKED" and "OSError" in str(err.value)
    with pytest.raises(RunStop) as err:
        L.load_history(L.PERSONAL_STUDY_4, "french", fetch=lambda url: raw[: len(raw) // 2])
    assert err.value.status == "SOURCE_INVALID"


def test_the_rebuilt_funds_charge_fees_and_the_2x_funds_borrowing():
    h = L.DailyHistory(np.array(["2000-01-03", "2000-01-04"], dtype="datetime64[D]"), np.array([0.01, -0.02]),
                       np.array([0.0002, 0.0002]), {}, {})
    plan = L.PERSONAL_STUDY_4
    r = L.fund_returns(plan, h)
    assert r["1x"] == pytest.approx(h.market - 0.0009 / 252)
    assert r["2x"] == pytest.approx(2 * h.market - h.bills - (0.0050 + 0.0090) / 252)
    assert r["bills"] == pytest.approx(h.bills - 0.0014 / 252) and (r["cash"] == 0).all()


# ---- the rules: the same decisions as the live engine ---------------------------------------------------------------
def held(targets: dict[int, dict[str, float]], t: int) -> dict[str, float]:
    return targets[max(k for k in targets if k <= t)]


ROLE = {"2x": "SSO", "1x": "SPY", "bills": "BIL"}


def live(spec_id: str, closes: pd.Series, t: pd.Timestamp, holdings: dict | None = None) -> dict[str, float]:
    spec = R[spec_id]
    if isinstance(spec, M.TrendSpec):
        return {M._target(spec, {"SPY": closes}, t)[0]: 1.0}
    w = M.rule_weights(spec, {"SPY": closes}, t, holdings or {})[0]
    return {k: v for k, v in w.items() if v > 0}


@pytest.mark.parametrize("spec_id", ["sso-trend200", "spy-trend200", "halloween-sso", "tom-sso", "rsi2-sso"])
def test_each_rule_decides_as_the_live_engine_does_on_the_same_series(spec_id):
    closes = wavy()
    h = history(closes)
    first = 260
    targets = L.decisions(R[spec_id], h, first)
    assert len(targets) > 3  # the series makes every rule switch
    for t in range(first, len(closes) - 2):  # the file's last session cannot know whether its month goes on
        mine = {ROLE[k]: v for k, v in held(targets, t).items()}
        state = {"SSO": 1} if spec_id == "rsi2-sso" and t > first and "SSO" in {
            ROLE[k] for k in held(targets, t - 1)} else {}
        assert mine == live(spec_id, closes, closes.index[t], state), (spec_id, closes.index[t].date())


def test_volatility_targeting_rebalances_at_month_ends_to_the_live_engines_weights():
    closes = wavy()
    h = history(closes)
    targets = L.decisions(R["voltarget-sso"], h, 260)
    days = [closes.index[t] for t in targets]
    assert all(M.is_decision_day(CAL, d.date()) for d in days[1:]) and len(days) > 12
    for t, w in targets.items():
        expect = live("voltarget-sso", closes, closes.index[t])
        assert {ROLE[k]: v for k, v in w.items() if v > 0} == pytest.approx(expect)


def test_buy_and_hold_decides_once_and_unsupported_rules_say_so():
    h = history(wavy())
    assert L.decisions(R["sso-hold"], h, 260) == {260: {"2x": 1.0}}
    with pytest.raises(ValueError, match="needs more than"):
        L.decisions(R["gtaa5"], h, 260)
    with pytest.raises(ValueError, match="needs more than"):
        L.decisions(R["gem"], h, 260)


# ---- the simulation -------------------------------------------------------------------------------------------------
def test_decisions_fill_at_the_next_close_and_costs_fall_on_the_funds_traded():
    rets = {"1x": np.array([0.0, 0.10, 0.10, 0.10]), "2x": np.array([0.0, 0.20, 0.20, -0.50]),
            "bills": np.zeros(4), "cash": np.zeros(4)}
    run = L.simulate({0: {"2x": 1.0}, 2: {"bills": 1.0}}, rets, first=0, lag=1, cost_bps=10.0)
    # bought at close 1 (missing day 1's +20%), earns day 2's +20%, sold into bills at close 3 (after day 3's -50%)
    assert run.equity == pytest.approx([1.0, 0.999, 0.999 * 1.2, 0.999 * 1.2 * 0.5 * (1 - 2 * 0.001)])
    assert run.bench == pytest.approx([1.0, 1.0, 1.1, 1.21])  # the 1x fund from the method's first fill
    assert run.switches == 2 and run.turnover == pytest.approx(3.0)
    same = L.simulate({0: {"2x": 1.0}}, rets, first=0, lag=0, cost_bps=0.0)
    assert same.equity[1] == pytest.approx(1.2)  # filled at the deciding close: day 1's return counts


def test_window_statistics_split_decades_and_the_post_publication_years():
    days = np.arange(np.datetime64("1925-01-01"), np.datetime64("2026-01-01"), dtype="datetime64[D]")
    days = days[np.is_busday(days)]
    eq = np.exp(0.0002 * np.arange(len(days)))
    bench = np.exp(0.0001 * np.arange(len(days)))
    plan = L.PERSONAL_STUDY_4
    s = L.window_stats(L.LongRun(eq, bench, 0.0, 0), days, plan, 1988)
    years = (days[-1] - days[0]).astype(int) / 365.25
    assert s["excess_log_growth_per_year"] == pytest.approx(0.0001 * (len(days) - 1) / years)
    assert set(s["decades"]) == {f"{d}s" for d in plan.decades} and all(
        v["excess_log_growth_per_year"] > 0 for v in s["decades"].values())
    assert s["post_publication"]["from"].startswith("1989-01") and s["rolling_10y"]["beat_share"] == 1.0
    assert s["max_drawdown"] == 0.0


# ---- the registered study -------------------------------------------------------------------------------------------
def test_the_century_replication_is_registered_for_the_index_only_methods():
    plan = PS.PERSONAL_PLANS["personal-study-4"]
    assert plan is L.PERSONAL_STUDY_4 and plan.sha256() == PERSONAL_STUDY_4_SHA256
    assert plan.references == ("sso-trend200", "spy-trend200")
    assert set(plan.candidates) <= set(PS.PERSONAL_STUDY_3.candidates)
    assert set(dict(plan.published)) == {*plan.references, *plan.candidates}
    assert plan.lag_sessions == 1 and plan.cost_bps == 5.0 and plan.source_url == L.FRENCH_DAILY_URL
    for k in (*plan.references, *plan.candidates):
        assert set(R[k].data_symbols) <= set(L.ASSET), k  # nothing beyond the index and T-bills
    assert PS.PERSONAL_STUDY_3.sha256() == "4b67f1db20f0ecf2cf0072b0a184ef897dce7c4b168956ed6a4a21be43c0cac0"


def test_the_replication_runs_on_the_fixture_and_writes_aggregates_only(tmp_path):
    plan = L.PERSONAL_STUDY_4
    data = L.load_history(plan, "fixture")
    assert data.provenance["data_class"] == "SYNTHETIC_FIXTURE_DEMO"
    summary = L.run_long_history(plan, data, tmp_path, experiment_id="demo-personal-4", progress=lambda m: None)
    body = json.loads((tmp_path / "personal_study.json").read_text())
    assert body["schema"] == "apex-discovery-v3-personal-study-4" and body["plan_sha256"] == plan.sha256()
    assert body["window"]["label"] == "KNOWN_HISTORY" and set(summary["dispositions"]) == {
        *plan.references, *plan.candidates}
    for k, r in body["methods"].items():
        expected = ("HOLDS_UP" if all(r["tests"].values()) else
                    "HOLDS_UP_PARTLY" if r["tests"]["full_excess_positive"] else "DOES_NOT_HOLD_UP")
        assert r["disposition"] == expected and len(r["tests"]) == 6
        assert (r["full"]["monthly_active"]["holm_p"] is None) == (k in plan.references)
    raw = (tmp_path / "personal_study.json").read_text()
    assert len(raw) < 200_000 and '"market":' not in raw  # aggregates, not the daily series
    assert "Excess log growth per year over the market, by decade" in (tmp_path / "PERSONAL_STUDY.md").read_text()
