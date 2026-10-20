import math

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from apex_worker.demo.synthetic import generate
from apex_worker.features.compute import FEATURE_CATALOG, compute_features


@pytest.fixture(scope="module")
def market():
    return generate(symbols=["DMSPY", "DMXLK", "DMSEMI1", "DMSPLIT1", "DMTHIN1"])


def _bars(closes, vol=1e6, spread=0.01):
    idx = pd.date_range("2024-01-02", periods=len(closes), freq="B")
    c = np.asarray(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * (1 + spread), "low": np.minimum(o, c) * (1 - spread),
                         "close": c, "volume": np.full(len(c), vol)}, index=idx)


def test_no_future_leakage_by_truncation(market):
    bars, bench, sec = market.adjusted("DMSEMI1"), market.adjusted("DMSPY"), market.adjusted("DMXLK")
    full = compute_features(bars, bench, sec)
    for cut in (300, 777, 1500, len(bars) - 2):
        t = bars.index[cut]
        trunc = compute_features(bars.loc[:t], bench.loc[:t], sec.loc[:t])
        a, b = full.loc[t], trunc.loc[t]
        for col in FEATURE_CATALOG:
            x, y = a[col], b[col]
            assert (math.isnan(x) and math.isnan(y)) or math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12), (col, t, x, y)


def test_future_shock_does_not_change_past(market):
    bars = market.adjusted("DMSEMI1").copy()
    base = compute_features(bars)
    shocked = bars.copy()
    shocked.iloc[-5:, :4] *= 3.0  # wild prints in the last 5 sessions
    after = compute_features(shocked)
    pd.testing.assert_frame_equal(base.iloc[:-5], after.iloc[:-5])


def test_insufficient_history_is_null_not_zero():
    f = compute_features(_bars(np.linspace(50, 60, 30)))
    last = f.iloc[-1]
    assert math.isnan(last["ret_60"]) and math.isnan(last["dist_sma200_atr"]) and math.isnan(last["squeeze_pct_120"])
    assert last["bars_available"] == 30


def test_split_adjusted_history_has_no_split_artifact(market):
    raw = compute_features(market.bars["DMSPLIT1"])
    adj = compute_features(market.adjusted("DMSPLIT1"))
    day = pd.Timestamp("2023-06-12")
    assert raw.loc[day, "gap_1"] < -0.5  # unadjusted shows a fake -69% gap
    assert abs(adj.loc[day, "gap_1"]) < 0.15


@pytest.mark.parametrize("kind", ["flat", "up", "down", "volatile", "illiquid"])
def test_synthetic_shapes_are_finite(kind):
    n = 260
    rng = np.random.default_rng(1)
    if kind == "flat":
        closes = np.full(n, 25.0)
    elif kind == "up":
        closes = 20 * np.exp(np.linspace(0, 0.8, n))
    elif kind == "down":
        closes = 80 * np.exp(np.linspace(0, -0.9, n))
    elif kind == "volatile":
        closes = 50 * np.exp(np.cumsum(rng.normal(0, 0.06, n)))
    else:
        closes = 30 + rng.normal(0, 0.2, n).cumsum() * 0.1
    bars = _bars(closes, vol=1e6 if kind != "illiquid" else 0, spread=0.0 if kind == "flat" else 0.01)
    if kind == "illiquid":
        bars.iloc[::3, bars.columns.get_loc("volume")] = 150.0
    f = compute_features(bars)
    assert not np.isinf(f.to_numpy(dtype=float)).any()
    tail = f.iloc[-1]
    assert tail["atr_14"] >= 0 and tail["rv_20"] >= 0
    if kind == "up":
        assert tail["ret_60"] > 0 and tail["above_sma200"] == 1.0
    if kind == "down":
        assert tail["ret_60"] < 0 and tail["above_sma200"] == 0.0
    if kind == "flat":
        assert tail["rv_20"] == 0 and math.isnan(tail["vol_expansion"])  # 0/0 is undefined, not invented


@settings(max_examples=40, deadline=None)
@given(st.lists(st.floats(min_value=1.0, max_value=5000.0, allow_nan=False, allow_infinity=False), min_size=25, max_size=90))
def test_property_any_positive_series_never_inf(closes):
    f = compute_features(_bars(closes))
    assert not np.isinf(f.to_numpy(dtype=float)).any()
    assert (f["atr_14"].dropna() >= 0).all()
    assert ((f["clv_1"].dropna() >= -1 - 1e-9) & (f["clv_1"].dropna() <= 1 + 1e-9)).all()
