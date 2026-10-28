"""personal-study-2 (docs/discovery-v3/PERSONAL_STUDY_2.md): the published single-asset rules (the 200-day trend filter
on 1x/2x/3x S&P 500 funds, Dual Momentum), their whole-share orders and daily checks in the simulator, and the
registered plan. Synthetic prices only; no network."""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from apex_worker.discovery.v3 import personal_study as PS
from apex_worker.market.calendar import default_calendar
from apex_worker.personal import backtest as B
from apex_worker.personal import method as M

CAL = default_calendar()
# Registered before the run that evaluates it; any change needs a new plan id.
PERSONAL_STUDY_2_SHA256 = "1bbef24b9ce5168cce29a368f23e9469682a4bc8627f23a61a1f2352364cd6f3"


def series(values, start=date(2025, 1, 2)) -> pd.Series:
    days = CAL.sessions_between(start, CAL.add_sessions(start, len(values) - 1))
    return pd.Series(values, index=pd.DatetimeIndex([pd.Timestamp(d) for d in days]), dtype=float)


def test_the_trend_rule_holds_the_asset_above_the_200_day_average_and_t_bills_below():
    up = series(100 * np.exp(0.001 * np.arange(260)))
    t = up.index[-1]
    target, sig, words = M._target(M.SSO_TREND, {"SPY": up}, t)
    assert target == "SSO" and sig["SPY"] > 0 and "above its 200-day average" in words and words.endswith("→ SSO")
    down = pd.concat([up.iloc[:-1], pd.Series([up.iloc[-2] * 0.7], index=[t])])
    assert M._target(M.SSO_TREND, {"SPY": down}, t)[0] == "BIL"
    assert M._target(M.SSO_TREND, {"SPY": up.iloc[:150]}, up.index[149])[0] is None  # no 200-day average yet
    assert M._target(M.SSO_TREND, {"SPY": up.iloc[:-1]}, t)[0] is None  # no close at t: no decision


def test_dual_momentum_takes_the_stronger_equity_while_it_beats_t_bills_else_bonds():
    n = 300
    bil = series(100 * np.exp(0.0001 * np.arange(n)))
    spy, efa = series(100 * np.exp(0.0008 * np.arange(n))), series(100 * np.exp(0.0004 * np.arange(n)))
    closes = {"SPY": spy, "EFA": efa, "BIL": bil}
    t = spy.index[-1]
    assert M._target(M.GEM, closes, t)[0] == "SPY"
    assert M._target(M.GEM, closes | {"EFA": series(100 * np.exp(0.0012 * np.arange(n)))}, t)[0] == "EFA"
    weak = closes | {"SPY": series(100 * np.exp(-0.0002 * np.arange(n)))}  # below T-bills over 12 months
    assert M._target(M.GEM, weak, t)[0] == "AGG"


def test_single_asset_orders_buy_switch_and_only_size_idle_cash_when_rebalancing():
    up = series(100 * np.exp(0.001 * np.arange(260)))
    closes = {"SPY": up, "SSO": up * 0.5, "BIL": series(np.full(260, 91.0))}
    d = up.index[-1].date()
    dec = M.decide(M.SSO_TREND, closes, d, {}, 1000.0)
    px = float(closes["SSO"].iloc[-1])
    assert [(o.action, o.symbol, o.shares) for o in dec.orders] == [(M.BUY, "SSO", int(1000 // px))]
    assert dec.target == ["SSO"] and dec.cash_after == pytest.approx(1000 - int(1000 // px) * px)
    assert M.decide(M.SSO_TREND, closes, d, {"SSO": 5}, 1000.0, rebalance=False).orders == []
    add = M.decide(M.SSO_TREND, closes, d, {"SSO": 5}, 1000.0, rebalance=True).orders
    assert [(o.action, o.symbol) for o in add] == [(M.ADD, "SSO")]
    crash = closes | {"SPY": pd.concat([up.iloc[:-1], pd.Series([up.iloc[-2] * 0.7], index=[up.index[-1]])])}
    sw = M.decide(M.SSO_TREND, crash, d, {"SSO": 5}, 10.0, rebalance=False)
    assert [(o.action, o.symbol) for o in sw.orders] == [(M.EXIT, "SSO"), (M.BUY, "BIL")]  # sells fund the buy
    assert "below its 200-day average" in sw.explain


def test_the_simulator_checks_a_daily_rule_at_every_close_but_trades_only_on_a_switch():
    n = 420
    path = np.concatenate([0.001 * np.arange(300), 0.299 - 0.004 * np.arange(1, 121)])  # a long rise, then a slide
    spy = series(100 * np.exp(path), start=date(2025, 1, 2))
    bars = {s: pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "close": c})
            for s, c in {"SPY": spy, "SSO": spy * 0.5, "BIL": series(np.full(n, 91.0), start=date(2025, 1, 2))}.items()}
    first, end = spy.index[210].date(), spy.index[-1].date()
    sim = B.simulate(M.SSO_TREND, bars, first, end, CAL)
    acts = [(t["action"], t["symbol"]) for t in sim.trades]
    assert acts[0] == (M.BUY, "SSO") and (M.EXIT, "SSO") in acts and (M.BUY, "BIL") in acts
    switch = next(t for t in sim.trades if t["action"] == M.EXIT)
    assert not M.is_decision_day(CAL, CAL.previous_session(date.fromisoformat(switch["day"])))  # mid-month
    assert len(sim.decisions) < 30 and (sim.cash >= -1e-9).all()  # month-ends and the switch, not every close


def test_the_study_is_registered_and_runs_on_the_fixture_with_total_returns(tmp_path):
    plan = PS.PERSONAL_STUDY_2
    assert plan.sha256() == PERSONAL_STUDY_2_SHA256
    assert plan.candidates == ("spy-trend200", "sso-trend200", "upro-trend200", "gem")
    assert plan.symbols == ("SPY", "SSO", "UPRO", "EFA", "AGG", "BIL") and plan.adjustment == "all"
    assert PS.PERSONAL_PLANS["personal-study-1"].sha256() == "89bd5a40a7160304447c5583da2d0665402c08e7e37d2cf3490622b282b66aba"
    data = PS.load_etf_data(plan, "fixture")
    assert data.first == date(2019, 10, 22) and data.provenance["data_class"] == "SYNTHETIC_FIXTURE_DEMO"
    summary = PS.run_etf_study(plan, data, tmp_path, experiment_id="demo-personal-2")
    body = json.loads((tmp_path / "personal_study.json").read_text())
    assert summary["decision"]["personal_method"] in plan.candidates and body["returns"] == "total"
    assert body["plan_sha256"] == PERSONAL_STUDY_2_SHA256 and body["window"]["label"] == "DEV_CONTAMINATED"
    for r in body["methods"].values():
        assert r["disposition"] in ("BEATS_SPY_ON_DEV", "DOES_NOT_BEAT_SPY_ON_DEV") and r["decisions"] > 0
    raw = (tmp_path / "personal_study.json").read_text()
    assert '"open"' not in raw and '"close"' not in raw and '"volume"' not in raw
    assert "Total returns" in (tmp_path / "PERSONAL_STUDY.md").read_text()


def test_a_short_or_broken_series_stops_the_study():
    from apex_worker.research.realeq.data import RunStop

    plan = PS.PERSONAL_STUDY_2
    good = PS.synthetic_etf_bars(plan.symbols, date(2016, 1, 4), date(2026, 10, 14))

    def broken(_plan, _start, _end, seed=11):
        out = dict(good)
        out["BIL"] = out["BIL"].iloc[: len(out["BIL"]) // 2]
        return out

    original = PS.synthetic_etf_bars
    PS.synthetic_etf_bars = broken
    try:
        with pytest.raises(RunStop) as err:
            PS.load_etf_data(plan, "fixture")
    finally:
        PS.synthetic_etf_bars = original
    assert "BIL" in str(err.value) and err.value.status == "SOURCE_INVALID"
