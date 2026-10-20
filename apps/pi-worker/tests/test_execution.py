import numpy as np
import pytest

from apex_worker.research.execution import ExecConfig, simulate
from apex_worker.signals.levels import LevelRejection, Levels, compute_levels

CFG = ExecConfig(cost_bps_per_side=0.0, entry_valid_sessions=2, horizon_sessions=5)


def bars(rows):
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def test_fill_at_open_inside_zone_then_target():
    o, h, l, c = bars([[100, 101, 99, 100], [100.2, 101, 99.8, 100.5], [100.5, 106.5, 100.4, 106]])
    r = simulate(o, h, l, c, 0, 99.5, 100.5, 97, 106, CFG)
    assert r.filled and r.entry_index == 1 and r.entry_price == pytest.approx(100.2)
    assert r.outcome == "TARGET" and r.exit_price == pytest.approx(106)


def test_open_above_zone_fills_only_if_limit_touched():
    o, h, l, c = bars([[100, 101, 99, 100], [101.5, 102, 100.4, 101], [101, 103, 100.9, 102]])
    r = simulate(o, h, l, c, 0, 99.5, 100.5, 97, 106, CFG)
    assert r.entry_index == 1 and r.entry_price == pytest.approx(100.5)
    o2, h2, l2, c2 = bars([[100, 101, 99, 100], [101.5, 102, 100.6, 101], [101.5, 103, 100.7, 102], [99, 99, 98, 98]])
    assert simulate(o2, h2, l2, c2, 0, 99.5, 100.5, 97, 106, CFG).outcome == "NO_FILL"


def test_no_fill_when_open_below_zone():
    o, h, l, c = bars([[100, 101, 99, 100], [98, 101, 97.5, 100], [98.5, 99, 98, 98.6], [100, 100, 99, 99]])
    assert simulate(o, h, l, c, 0, 99.5, 100.5, 97, 106, CFG).outcome == "NO_FILL"


def test_same_bar_target_and_stop_is_stop_first():
    o, h, l, c = bars([[100, 101, 99, 100], [100, 101, 99.5, 100.2], [100, 107, 96, 101]])
    r = simulate(o, h, l, c, 0, 99.5, 100.5, 97, 106, CFG)
    assert r.outcome == "STOP" and r.ambiguous_same_bar and r.exit_price == pytest.approx(97)


def test_gap_through_stop_exits_at_open_not_stop():
    o, h, l, c = bars([[100, 101, 99, 100], [100, 101, 99.5, 100.2], [92, 93, 91, 92.5]])
    r = simulate(o, h, l, c, 0, 99.5, 100.5, 97, 106, CFG)
    assert r.outcome == "STOP" and r.gap_through_stop and r.exit_price == pytest.approx(92)
    assert r.r_multiple < -1  # worse than planned risk


def test_fill_day_high_does_not_count_as_target():
    o, h, l, c = bars([[100, 101, 99, 100], [101.5, 108, 100.2, 101], [101, 102, 100.5, 101.5]] + [[101, 102, 100.5, 101]] * 6)
    r = simulate(o, h, l, c, 0, 99.5, 100.5, 97, 106, CFG)
    assert r.outcome == "TIME" and r.sessions_held == 5


def test_costs_reduce_returns():
    o, h, l, c = bars([[100, 101, 99, 100], [100, 101, 99.5, 100.2], [100.5, 106.5, 100.4, 106]])
    free = simulate(o, h, l, c, 0, 99.5, 100.5, 97, 106, ExecConfig(0, 2, 5))
    costly = simulate(o, h, l, c, 0, 99.5, 100.5, 97, 106, ExecConfig(25, 2, 5))
    assert costly.return_net < free.return_net


def test_data_end_flagged():
    o, h, l, c = bars([[100, 101, 99, 100], [100, 101, 99.5, 100.2], [100.5, 101, 100.4, 101]])
    r = simulate(o, h, l, c, 0, 99.5, 100.5, 97, 106, CFG)
    assert r.outcome == "DATA_END" and "ENDED_BEFORE_HORIZON" in r.flags


def test_levels_geometry_and_rejections():
    lv = compute_levels(close=50.0, atr=1.5, invalidation_hint=47.0, resistance=None)
    assert isinstance(lv, Levels)
    assert 0 < lv.stop < lv.entry_min < lv.entry_max < lv.target
    assert lv.entry_max - 50.0 <= 0.75 + 1e-9 and lv.reward_to_risk_net >= 1.5
    capped = compute_levels(close=50.0, atr=1.5, invalidation_hint=47.0, resistance=52.0)
    assert isinstance(capped, LevelRejection) and capped.code == "REWARD_RISK_TOO_LOW"
    assert isinstance(compute_levels(close=50.0, atr=float("nan"), invalidation_hint=None, resistance=None), LevelRejection)
