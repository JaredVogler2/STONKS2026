"""personal-study-3 (docs/discovery-v3/PERSONAL_STUDY_3.md): ten more published rules as target weights, their whole-share
orders, their rebalance schedules in the simulator and the alerts, the battery's statistics and the registered plan.
Synthetic prices only; no network."""

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
from apex_worker.personal.discord_rest import MemoryChannel
from apex_worker.personal.runner import PERSONAL_METHOD
from apex_worker.personal.smoke import SyntheticMarket

CAL = default_calendar()
R = M.METHODS
# Registered before the run that evaluates it; any change needs a new plan id.
PERSONAL_STUDY_3_SHA256 = "4b67f1db20f0ecf2cf0072b0a184ef897dce7c4b168956ed6a4a21be43c0cac0"


def series(values, start=date(2024, 1, 2)) -> pd.Series:
    days = CAL.sessions_between(start, CAL.add_sessions(start, len(values) - 1))
    return pd.Series(values, index=pd.DatetimeIndex([pd.Timestamp(d) for d in days]), dtype=float)


def trend(daily: float, n: int = 300, p0: float = 100.0) -> pd.Series:
    return series(p0 * np.exp(daily * np.arange(n)))


def weights(spec_id: str, closes: dict, t, holdings: dict | None = None):
    return M.rule_weights(R[spec_id], closes, pd.Timestamp(t), holdings or {})


def bars_from(closes: dict[str, pd.Series]) -> dict[str, pd.DataFrame]:
    return {s: pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "close": c}) for s, c in closes.items()}


# ---- the rules ------------------------------------------------------------------------------------------------------
def test_hold_and_the_fixed_mix_need_no_signal():
    assert weights("sso-hold", {}, "2026-10-30")[0] == {"SSO": 1.0}
    assert weights("mtum-hold", {}, "2026-10-30")[0] == {"MTUM": 1.0}
    w, _, why = weights("hfea", {}, "2026-10-30")
    assert w == {"UPRO": 0.55, "TMF": 0.45} and why == "55% UPRO + 45% TMF"


def test_volatility_targeting_scales_2x_exposure_by_last_months_volatility():
    calm = series(100 * np.exp(0.0005 * np.arange(60) + 0.001 * (-1.0) ** np.arange(60)))  # about 3% a year
    w, sig, _ = weights("voltarget-sso", {"SPY": calm}, calm.index[-1])
    assert sig["exposure"] == 2.0 and w == {"SSO": 1.0, "BIL": 0.0}  # capped at 2x: all in the 2x fund
    wild = series(100 * np.exp(np.cumsum(0.03 * (-1.0) ** np.arange(60))))  # ±3% a day
    w, sig, why = weights("voltarget-sso", {"SPY": wild}, wild.index[-1])
    vol = float(np.diff(np.log(wild.to_numpy()))[-21:].std(ddof=1) * np.sqrt(252))
    assert sig["vol"] == pytest.approx(vol) and sig["exposure"] == pytest.approx(0.15 / vol)
    assert w["SSO"] == pytest.approx(0.15 / vol / 2) and w["SSO"] + w["BIL"] == pytest.approx(1.0)
    assert "exposure 0.31x" in why
    assert weights("voltarget-sso", {"SPY": wild.iloc[:15]}, wild.index[14])[0] is None  # under 21 returns


def test_gtaa_holds_a_fifth_of_each_asset_above_its_10_month_average_and_t_bills_for_the_rest():
    up, down = trend(0.001), trend(-0.001)
    closes = {"SPLG": up, "EFA": up, "IEF": down, "VNQ": up, "DBC": down}
    t = up.index[-1]
    w, sig, why = weights("gtaa5", closes, t)
    assert w == pytest.approx({"SPLG": 0.2, "EFA": 0.2, "VNQ": 0.2, "BIL": 0.4})
    assert sig["IEF"] < 0 < sig["SPLG"] and "IEF below" in why and "SPLG above" in why
    assert weights("gtaa5", {k: v.iloc[-150:] for k, v in closes.items()}, t)[0] is None  # 7 month-ends only
    assert weights("gtaa5", closes | {"DBC": down.iloc[:-1]}, t)[0] is None  # no close at t


def test_vaa_takes_the_best_risky_asset_only_while_all_four_have_positive_13612w_momentum():
    risky = {"SPLG": trend(0.0010), "EFA": trend(0.0006), "EEM": trend(0.0004), "AGG": trend(0.0001)}
    safe = {"LQD": trend(0.0002), "IEF": trend(0.0003), "SHY": trend(0.00005)}
    t = risky["SPLG"].index[-1]
    w, mom, why = weights("vaa-g4", risky | safe, t)
    assert w == {"SPLG": 1.0} and why.endswith("→ SPLG")
    s = risky["SPLG"]
    r1, r3, r6, r12 = (s.iloc[-1] / s.iloc[-1 - k] - 1 for k in (21, 63, 126, 252))
    assert mom["SPLG"] == pytest.approx(12 * r1 + 4 * r3 + 2 * r6 + r12)
    weak = risky | {"EEM": trend(-0.0002)}  # one risky asset turns negative: the best of the safe three
    assert weights("vaa-g4", weak | safe, t)[0] == {"IEF": 1.0}
    assert weights("vaa-g4", {k: v.iloc[-200:] for k, v in (risky | safe).items()}, t)[0] is None  # no 12 months


def test_accelerating_dual_momentum_holds_the_stronger_equity_while_positive_else_long_treasuries():
    closes = {"SPLG": trend(0.0008, 200), "SCZ": trend(0.0012, 200), "TLT": trend(0.0001, 200)}
    t = closes["SPLG"].index[-1]
    assert weights("adm", closes, t)[0] == {"SCZ": 1.0}
    w, score, _ = weights("adm", closes | {"SPLG": trend(-0.0005, 200), "SCZ": trend(-0.001, 200)}, t)
    assert w == {"TLT": 1.0} and max(score.values()) < 0


def test_halloween_holds_the_2x_fund_from_november_through_april():
    assert weights("halloween-sso", {}, "2025-10-31")[0] == {"SSO": 1.0}  # October's last close decides November
    assert weights("halloween-sso", {}, "2026-04-30")[0] == {"BIL": 1.0}  # April's last close: May is out
    assert weights("halloween-sso", {}, "2026-01-30")[0] == {"SSO": 1.0}
    assert weights("halloween-sso", {}, "2026-07-31")[0] == {"BIL": 1.0}


def test_turn_of_the_month_holds_the_2x_fund_over_the_last_session_and_the_first_three():
    days = CAL.sessions_between(date(2026, 10, 1), date(2026, 11, 30))
    held = {CAL.next_session(d) for d in days if weights("tom-sso", {}, d)[0] == {"SSO": 1.0}}
    october, november = [d for d in days if d.month == 10], [d for d in days if d.month == 11]
    assert {d for d in held if date(2026, 10, 15) <= d <= date(2026, 11, 15)} == {october[-1], *november[:3]}


def test_rsi2_buys_a_sharp_dip_in_an_uptrend_and_sells_above_the_5_day_average():
    base = 100 * np.exp(0.002 * np.arange(260))
    dip = np.concatenate([base, base[-1] * np.array([0.97, 0.94])])  # two down closes, still above the 200-day average
    s = series(dip)
    w, sig, why = weights("rsi2-sso", {"SPY": s}, s.index[-1])
    assert w == {"SSO": 1.0} and sig["rsi2"] < 10 and sig["trend_gap"] > 0 and "RSI(2)" in why
    assert weights("rsi2-sso", {"SPY": s}, s.index[-1], {"SSO": 3})[0] == {"SSO": 1.0}  # held: below the 5-day average
    up = series(np.concatenate([dip, [base[-1] * 1.05]]))
    assert weights("rsi2-sso", {"SPY": up}, up.index[-1], {"SSO": 3})[0] == {"BIL": 1.0}  # above it: out
    flat = series(np.concatenate([base, [base[-1] * 1.001]]))
    assert weights("rsi2-sso", {"SPY": flat}, flat.index[-1])[0] == {"BIL": 1.0}  # no dip, no entry
    crash = series(np.concatenate([base, base[-1] * np.array([0.7, 0.5])]))
    assert weights("rsi2-sso", {"SPY": crash}, crash.index[-1])[0] == {"BIL": 1.0}  # under the 200-day average


# ---- orders and decisions -------------------------------------------------------------------------------------------
def test_weight_orders_sell_first_then_buy_the_largest_weight_first_in_whole_shares():
    mix = {"UPRO": 0.55, "TMF": 0.45}
    ords, cash = M.weight_orders(mix, {}, 1000.0, {"UPRO": 90.0, "TMF": 45.0})
    assert [(o.action, o.symbol, o.shares) for o in ords] == [(M.BUY, "UPRO", 6), (M.BUY, "TMF", 10)]
    assert cash == pytest.approx(10.0)  # 1000 - 6 x 90 - 10 x 45
    ords, cash = M.weight_orders(mix, {"UPRO": 6, "TMF": 10}, 10.0, {"UPRO": 180.0, "TMF": 45.0})
    # equity 1540: UPRO back to floor(847 / 180) = 4, TMF up to floor(693 / 45) = 15
    assert [(o.action, o.symbol, o.shares) for o in ords] == [(M.TRIM, "UPRO", 2), (M.ADD, "TMF", 5)]
    assert cash == pytest.approx(10 + 2 * 180 - 5 * 45)
    ords, cash = M.weight_orders({"BIL": 1.0}, {"SSO": 10}, 5.0, {"SSO": 50.0, "BIL": 91.5})
    assert [(o.action, o.symbol, o.shares) for o in ords] == [(M.EXIT, "SSO", 10), (M.BUY, "BIL", 5)]
    assert cash == pytest.approx(505 - 5 * 91.5) and cash >= 0


def test_a_rule_trades_at_its_rebalance_or_when_its_holdings_change_and_never_on_a_partial_picture():
    up, down = trend(0.001), trend(-0.001)
    closes = {"SPLG": up, "EFA": up, "IEF": down, "VNQ": up, "DBC": down, "BIL": trend(0.0001)}
    d = up.index[-1].date()
    first = M.decide(R["gtaa5"], closes, d, {}, 1000.0)
    assert {o.symbol for o in first.orders} == {"SPLG", "EFA", "VNQ", "BIL"} and first.cash_after >= 0
    assert first.weights == pytest.approx({"SPLG": 0.2, "EFA": 0.2, "VNQ": 0.2, "BIL": 0.4}) and first.ranks == {}
    held = {o.symbol: o.shares for o in first.orders}
    assert M.decide(R["gtaa5"], closes, d, held, first.cash_after, rebalance=False).orders == []  # same holdings
    gap = M.decide(R["gtaa5"], closes | {"VNQ": up.iloc[:-1]}, d, held, first.cash_after)
    assert gap.target == [] and gap.orders == [] and "no " in gap.notes[0] and "VNQ" in gap.notes[0]


def test_rebalance_days_are_month_ends_and_quarter_ends_for_the_quarterly_mix():
    october_end = date(2026, 10, 30)
    assert M.rebalance_day(M.SSO_TREND, CAL, october_end) and M.rebalance_day(R["gtaa5"], CAL, october_end)
    assert not M.rebalance_day(R["gtaa5"], CAL, date(2026, 10, 29))
    year = CAL.sessions_between(date(2026, 1, 1), date(2026, 12, 31))
    assert [d for d in year if M.rebalance_day(R["hfea"], CAL, d)] == [
        date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30), date(2026, 12, 31)]


# ---- the simulator --------------------------------------------------------------------------------------------------
def test_the_simulator_rebalances_the_mix_each_quarter_and_times_the_month_turns():
    n = 420
    spy = trend(0.0004, n)
    closes = {"SPY": spy, "UPRO": trend(0.0012, n, 60.0), "TMF": trend(-0.0002, n, 40.0), "SSO": trend(0.0008, n, 50.0),
              "BIL": trend(0.0001, n, 91.0)}
    bars = bars_from(closes)
    first, end = date(2024, 2, 14), spy.index[-1].date()
    sim = B.simulate(R["hfea"], bars, first, end, CAL)
    decided = {CAL.previous_session(date.fromisoformat(t["day"])) for t in sim.trades}
    assert first in decided and len(decided) > 3
    assert all(d == first or (M.is_decision_day(CAL, d) and d.month in (3, 6, 9, 12)) for d in decided)
    sim = B.simulate(R["tom-sso"], bars, first, end, CAL)
    buys = [date.fromisoformat(t["day"]) for t in sim.trades if t["action"] == M.BUY and t["symbol"] == "SSO"]
    sells = [date.fromisoformat(t["day"]) for t in sim.trades if t["action"] == M.EXIT and t["symbol"] == "SSO"]
    assert len(buys) >= 15 and all(M.is_decision_day(CAL, d) for d in buys)  # in on each month's last session
    assert all(len(CAL.sessions_between(d.replace(day=1), d)) == 4 for d in sells)  # out at the 4th session's open
    assert (sim.cash >= -1e-9).all()


def test_a_rebalance_without_a_close_is_taken_at_the_next_complete_close():
    n = 360
    closes = {s: trend(0.0006 + 0.0001 * i, n, 50.0 + 10 * i) for i, s in
              enumerate(("SPY", "SPLG", "EFA", "IEF", "VNQ", "DBC", "BIL"))}
    gap = pd.Timestamp("2025-01-31")
    closes["DBC"] = closes["DBC"].drop(gap)
    bars = bars_from(closes)
    sim = B.simulate(R["gtaa5"], bars, date(2024, 10, 31), closes["SPY"].index[-1].date(), CAL)
    days = {x["day"]: x for x in sim.decisions}
    assert days["2025-01-31"]["target"] == [] and days["2025-02-03"]["target"]  # taken at Monday's close instead
    assert days["2024-12-31"]["target"] and "2025-01-30" not in days


# ---- the battery's statistics ---------------------------------------------------------------------------------------
def test_the_stationary_bootstrap_is_reproducible_and_brackets_the_mean():
    x = 0.01 + 0.03 * np.random.default_rng(1).standard_normal(120)
    lo, hi = PS.stationary_bootstrap(x, 2000, 6, 7)
    assert (lo, hi) == PS.stationary_bootstrap(x, 2000, 6, 7)
    assert lo < 12 * x.mean() < hi and hi - lo < 0.2
    assert PS.stationary_bootstrap(x, 2000, 6, 8) != (lo, hi)
    assert PS.stationary_bootstrap(x[:11], 2000, 6, 7) is None


def test_rolling_windows_compare_growth_over_every_span_of_months():
    keys = [f"2020-{m:02d}" for m in range(1, 13)]
    spy = {k: 100.0 * (1.01 ** i) for i, k in enumerate(keys)}
    method = {k: 100.0 * (1.02 ** i) if i < 8 else 100.0 * (1.02 ** 7) * 0.9 for i, k in enumerate(keys)}
    out = PS.rolling_vs_spy(method, spy, 3)
    assert out["windows"] == 9
    assert out["beat_share"] == pytest.approx(5 / 9)  # ahead until the drop in month 9 enters the window
    assert out["worst_excess"] == pytest.approx(0.9 - 1.01 ** 3)  # months 8 → 11: the 10% drop, no growth after it
    assert out["median_excess"] == pytest.approx(1.02 ** 3 - 1.01 ** 3)  # 4 windows behind, 5 ahead
    assert PS.rolling_vs_spy(method, spy, 12) == {"windows": 0, "beat_share": None, "median_excess": None,
                                                  "worst_excess": None}


# ---- the registered study -------------------------------------------------------------------------------------------
def test_ten_distinct_published_rules_are_registered_beside_the_adopted_method():
    plan = PS.PERSONAL_STUDY_3
    assert plan.sha256() == PERSONAL_STUDY_3_SHA256
    assert PS.PERSONAL_PLANS["personal-study-1"].sha256() == "89bd5a40a7160304447c5583da2d0665402c08e7e37d2cf3490622b282b66aba"
    assert PS.PERSONAL_PLANS["personal-study-2"].sha256() == "1bbef24b9ce5168cce29a368f23e9469682a4bc8627f23a61a1f2352364cd6f3"
    specs = [R[k] for k in plan.candidates]
    assert len(specs) == 10 and all(isinstance(s, M.RuleSpec) and s.source for s in specs)
    assert len({s.kind for s in specs}) == 9  # two buy-and-holds of different funds
    assert plan.references == (PERSONAL_METHOD,) and plan.symbols[0] == "SPY" and plan.adjustment == "all"
    needed = {sym for k in (*plan.references, *plan.candidates) for sym in R[k].data_symbols}
    assert needed <= set(plan.symbols)
    assert plan.full_first == "2017-01-31" and "first_half" not in plan.notes and "each half" in plan.notes


def test_every_registered_method_simulates_on_the_fixture_within_its_cash():
    plan = PS.PERSONAL_STUDY_3
    bars = PS.synthetic_etf_bars(plan.symbols, date(2016, 1, 4), date(2026, 9, 30))
    for k in (*plan.references, *plan.candidates):
        sim = B.simulate(R[k], bars, date(2024, 1, 31), date(2025, 12, 31), CAL)
        assert sim.trades and sim.decisions and (sim.cash >= -1e-9).all() and sim.equity.iloc[-1] > 0, k
        assert all(t["symbol"] in R[k].universe for t in sim.trades), k


def test_the_battery_runs_on_the_fixture_and_publishes_aggregates_only(tmp_path):
    from dataclasses import replace

    plan = replace(PS.PERSONAL_STUDY_3, candidates=("sso-hold", "hfea", "tom-sso"), bootstrap_resamples=300)
    data = PS.load_etf_data(plan, "fixture")
    assert data.provenance["data_class"] == "SYNTHETIC_FIXTURE_DEMO"
    summary = PS.run_battery(plan, data, tmp_path, experiment_id="demo-personal-3")
    body = json.loads((tmp_path / "personal_study.json").read_text())
    assert body["schema"] == "apex-discovery-v3-personal-study-3" and body["returns"] == "total"
    w = body["windows"]
    assert w["full"]["first_decision"] == "2017-01-31" and w["dev"]["first_decision"] == "2019-10-22"
    assert w["first_half"]["last_session"] == w["second_half"]["first_decision"]
    assert w["full"]["last_session"] == w["second_half"]["last_session"] == w["dev"]["last_session"]
    rows = body["methods"]
    assert set(rows) == {"sso-trend200", "sso-hold", "hfea", "tom-sso"} and rows["sso-trend200"]["role"] == "reference"
    assert rows["sso-trend200"]["full"]["monthly_active"]["holm_p"] is None  # Holm covers the new trials only
    for r in rows.values():
        assert set(r["tests"]) == {"full_excess_positive", "first_half_excess_positive", "second_half_excess_positive",
                                   "drawdown_within_10_points", "rolling36_share_at_least_60pct", "positive_at_20bps"}
        expected = ("BEATS_SPY_REPEATEDLY" if all(r["tests"].values()) else
                    "BEATS_SPY_PARTLY" if r["tests"]["full_excess_positive"] else "DOES_NOT_BEAT_SPY")
        assert r["disposition"] == expected and r["rolling_full"]["36m"]["windows"] > 0
        lo, hi = r["bootstrap_full_annual_excess_90"]
        assert lo < hi
    rec = body["recommendation"]
    assert rec["qualified"] == [k for k in rows if rows[k]["disposition"] == "BEATS_SPY_REPEATEDLY"] or \
        set(rec["qualified"]) == {k for k in rows if rows[k]["disposition"] == "BEATS_SPY_REPEATEDLY"}
    if rec["qualified"]:
        best = max(rows[k]["full"]["excess_log_growth_per_year"] for k in rec["qualified"])
        assert rows[rec["method"]]["full"]["excess_log_growth_per_year"] == best
    else:
        assert rec["method"] is None
    assert summary["recommendation"] == rec
    raw = (tmp_path / "personal_study.json").read_text()
    assert '"open"' not in raw and '"close"' not in raw and '"volume"' not in raw
    md = (tmp_path / "PERSONAL_STUDY.md").read_text()
    assert "Total returns" in md and "Tests failed" in md and "| hfea | trial |" in md


# ---- the alerts follow a rule's schedule ----------------------------------------------------------------------------
def test_the_alerts_follow_a_quarterly_rule_and_word_its_orders_by_weight():
    from test_personal_alerts import cheap_bars, et, fill_all, records, run

    base = cheap_bars()
    ch = MemoryChannel(start=et(2026, 10, 27, 14, 0))
    mk = SyntheticMarket(CAL, lambda: ch.clock, bars=base | {"UPRO": base["XLK"], "TMF": base["XLU"]})
    mix = {"method_id": "hfea"}
    ch.post_as_owner("start 1000", et(2026, 10, 27, 14, 35))
    run(ch, mk, et(2026, 10, 27, 14, 37), **mix)
    assert "each quarter" in ch.bot_texts()[0]
    (alert,) = records(ch, "alert")
    assert alert["m"] == "hfea" and alert["sel"] == ["TMF", "UPRO"]
    assert [(o["a"], o["s"]) for o in alert["orders"]] == [("BUY", "UPRO"), ("BUY", "TMF")]
    text = ch.bot_texts()[-1]
    assert "the rule's target (55%)" in text and "rank" not in text and "a slot" not in text
    fill_all(ch, mk, et(2026, 10, 27, 15, 0))
    run(ch, mk, et(2026, 10, 27, 15, 37), **mix)
    assert not run(ch, mk, et(2026, 10, 30, 18, 7), **mix).alert  # a month-end, not a quarter-end
    ch.post_as_owner("plan", et(2026, 11, 2, 12, 0))
    run(ch, mk, et(2026, 11, 2, 12, 7), **mix)
    assert "If the quarter ended" in ch.bot_texts()[-1] and "next quarter-end after the close of Thu 12/31" in \
        ch.bot_texts()[-1]
    rep = run(ch, mk, et(2026, 12, 31, 18, 7), **mix)
    assert rep.alert and records(ch, "alert")[-1]["day"] == "2026-12-31"
    assert "Quarter-end" in ch.bot_texts()[-1] and ch.duplicates == 0
