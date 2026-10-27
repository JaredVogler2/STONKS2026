"""The personal $1000 method (personal/method.py), its whole-share simulator (personal/backtest.py) and the registered
study `personal-study-1` (docs/discovery-v3/PERSONAL_STUDY_1.md). Synthetic prices only; no network."""

from __future__ import annotations

import json
import math
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
PERSONAL_STUDY_1_SHA256 = "89bd5a40a7160304447c5583da2d0665402c08e7e37d2cf3490622b282b66aba"


def _series(values, start=date(2026, 1, 2)) -> pd.Series:
    days = CAL.sessions_between(start, CAL.add_sessions(start, len(values) - 1))
    return pd.Series(values, index=pd.DatetimeIndex([pd.Timestamp(d) for d in days]), dtype=float)


def _trend(daily: float, n: int = 140, p0: float = 100.0) -> pd.Series:
    return _series(p0 * np.exp(daily * np.arange(n)))


SPEC5 = M.MethodSpec("t", "test", universe=("A", "B", "C", "D", "E", "F"), lookback=126)


def test_the_signal_is_the_lookback_return_through_t_and_needs_full_history():
    closes = {"A": _trend(0.001), "B": _trend(-0.001), "C": _trend(0.002, n=100)}  # C: too little history
    t = closes["A"].index[-1]
    sig = M.signals(closes, t, SPEC5)
    assert set(sig) == {"A", "B"}
    assert sig["A"] == pytest.approx(math.exp(0.001 * 126) - 1)
    later = {k: pd.concat([v, _series([1e9], start=CAL.next_session(v.index[-1].date()))]) for k, v in closes.items()}
    assert M.signals(later, t, SPEC5) == sig  # a bar after t is never read


def test_selection_takes_the_top_slots_and_keeps_a_held_etf_while_in_the_buffer():
    sig = {"A": 0.30, "B": 0.25, "C": 0.20, "D": 0.15, "E": 0.10, "F": -0.05}
    assert M.select(sig, set(), SPEC5)[0] == ["A", "B", "C"]
    assert M.select(sig, {"E"}, SPEC5)[0] == ["A", "B", "E"]  # rank 5 stays (keep = 5), ordered by rank
    weak = sig | {"E": -0.20}  # E falls to rank 6: replaced by the best ETF not held
    assert M.select(weak, {"E"}, SPEC5)[0] == ["A", "B", "C"]
    absolute = M.MethodSpec("t", "test", universe=SPEC5.universe, absolute=True)
    neg = {"A": 0.10, "B": -0.01, "C": -0.02, "D": -0.03}
    assert M.select(neg, {"B"}, absolute)[0] == ["A"]  # only positive returns qualify; the other slots stay in cash


def test_orders_are_whole_shares_within_cash_exit_buy_add_and_trim():
    prices = {"A": 50.0, "B": 120.0, "C": 333.0, "D": 10.0}
    ords, cash = M.orders(M.SECTOR_MOM_3, ["A", "B", "C"], {}, 1000.0, prices)
    shares = {o.symbol: o.shares for o in ords}
    assert all(o.action == M.BUY for o in ords) and shares == {"A": 6, "B": 2, "C": 1}  # floor(333.33 / price)
    assert cash == pytest.approx(1000 - 6 * 50 - 2 * 120 - 333) and cash >= 0
    ords, cash = M.orders(M.SECTOR_MOM_3, ["A", "B"], {"A": 2, "D": 10}, 500.0, prices)
    acts = [(o.action, o.symbol, o.shares) for o in ords]
    # equity 700, 233.33 a slot: D leaves (10 shares), B is new (1 share), A is topped up by 2 (2 x 50 = 100 < 233)
    assert acts == [(M.EXIT, "D", 10), (M.BUY, "B", 1), (M.ADD, "A", 2)]
    ords, _ = M.orders(M.SECTOR_MOM_3, ["A", "B", "C"], {"A": 20}, 0.0, prices)
    assert (ords[0].action, ords[0].symbol, ords[0].shares) == (M.TRIM, "A", 13)  # 1000 vs 333.33: sell back to 333


def test_a_holding_without_a_close_is_kept_and_not_resized():
    closes = {s: _trend(0.001 * (i + 1)) for i, s in enumerate(M.SECTOR_MOM_3.universe)}
    t = closes["XLK"].index[-1]
    closes["XLE"] = closes["XLE"].iloc[:-30]  # no bar since: cannot be valued or sized
    dec = M.decide(M.SECTOR_MOM_3, closes, t.date(), {"XLE": 3}, 500.0)
    assert "XLE" not in [o.symbol for o in dec.orders] and dec.notes and "XLE" in dec.notes[0]


def test_decision_days_are_the_last_session_of_each_month():
    assert M.is_decision_day(CAL, date(2026, 10, 30)) and not M.is_decision_day(CAL, date(2026, 10, 29))
    assert M.is_decision_day(CAL, date(2026, 12, 31)) and not M.is_decision_day(CAL, date(2026, 10, 31))


def _bars(closes: dict[str, pd.Series]) -> dict[str, pd.DataFrame]:
    return {s: pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "close": c}) for s, c in closes.items()}


def test_the_simulation_fills_at_the_next_open_pays_costs_and_never_overspends():
    n = 260
    universe = M.SECTOR_MOM_3.universe
    closes = {s: _series(100 * np.exp((0.002 if s == "XLE" else -0.0005) * np.arange(n))) for s in universe}
    closes["SPY"] = _series(np.full(n, 100.0))
    bars = _bars(closes)
    first = closes["SPY"].index[130].date()
    end = closes["SPY"].index[-1].date()
    sim = B.simulate(M.SECTOR_MOM_3, bars, first, end, CAL, cost_bps=10.0)
    buys = [t for t in sim.trades if t["action"] == M.BUY]
    assert buys and buys[0]["day"] == CAL.next_session(first).isoformat()  # decided at the close, filled at the open
    assert "XLE" in {t["symbol"] for t in buys} and (sim.cash >= -1e-9).all()
    assert sim.costs_paid > 0 and sim.equity.iloc[0] == 1000.0
    bench = B.spy_benchmark(bars, sim.equity.index, 1000.0)
    m = B.metrics(sim.equity, bench)
    assert m["spy_log_growth_per_year"] == pytest.approx(0.0, abs=1e-9) and m["excess_log_growth_per_year"] > 0
    assert -1 < m["max_drawdown"] <= 0 and m["monthly_active"]["months"] > 0


def test_metrics_read_growth_and_drawdown_from_the_values():
    e = _series([1000.0, 1100.0, 990.0, 1210.0])
    b = _series([1000.0, 1000.0, 1000.0, 1000.0])
    m = B.metrics(e, b)
    assert m["log_growth_per_year"] == pytest.approx(math.log(1.21) / (3 / 252))
    assert m["max_drawdown"] == pytest.approx(990 / 1100 - 1) and m["spy_max_drawdown"] == 0.0


def test_the_study_is_registered_and_runs_on_the_fixture_without_prices(tmp_path):
    assert PS.PERSONAL_STUDY_1.sha256() == PERSONAL_STUDY_1_SHA256
    assert PS.PERSONAL_STUDY_1.candidates == ("sector-mom-3", "sector-mom-3-abs")
    from apex_worker.discovery.v3.pipeline import load_data

    data = load_data("fixture", per_sector=2)
    summary = PS.run_personal_study(PS.PERSONAL_STUDY_1, data, tmp_path, experiment_id="demo-personal")
    body = json.loads((tmp_path / "personal_study.json").read_text())
    assert summary["decision"]["personal_method"] in PS.PERSONAL_STUDY_1.candidates
    assert summary["decision"]["status"] in ("ADOPTED", "EXPERIMENT")
    assert body["window"]["label"] == "DEV_CONTAMINATED" and body["plan_sha256"] == PERSONAL_STUDY_1_SHA256
    for r in body["methods"].values():
        assert r["disposition"] in ("BEATS_SPY_ON_DEV", "DOES_NOT_BEAT_SPY_ON_DEV") and r["decisions"] > 0
        assert set(r["cost_sensitivity"]) == {"0bps", "20bps"} and r["monthly_active"]["holm_p"] is not None
    raw = (tmp_path / "personal_study.json").read_text()
    assert '"open"' not in raw and '"close"' not in raw and '"high"' not in raw and '"volume"' not in raw
    assert "DEV_CONTAMINATED" in (tmp_path / "PERSONAL_STUDY.md").read_text()
