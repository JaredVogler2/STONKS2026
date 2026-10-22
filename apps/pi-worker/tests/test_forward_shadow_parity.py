"""Golden parity: the forward shadow's frozen lane reproduces realeq-protocol-2's study exactly (synthetic fixture only).

What is proven, on a fixture with planted S3 breakout-retest patterns and corporate-action quarantines:
1. a decision computed from a window ending at t (the recorder's view) equals the decision computed from the full
   history at t: no feature reads later data and the window anchor does not change any value (causality);
2. perturbing every bar after t (including a 'future catalyst' volume spike) leaves the decision at t unchanged;
3. frozen decisions + matured outcomes, processed in session order, give the study's run_arm rows: kept signals,
   quarantines, de-duplication, per-signal net on both legs and every cost, touch-fill sensitivity and the K matched
   controls (same symbols, same values) for S1 E0X1, S2 E0X1, S3 C_X0 and S2 E0X0 (many target exits);
4. the forward S5 book equals the study's S5 series from the same rebalance row;
5. the golden behaviour fingerprint equals the pinned constant the recorder checks before recording.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from apex_worker.research.forward import frozen as F
from apex_worker.research.realeq import study_v2 as S
from apex_worker.research.realeq.fixture_v2 import factor_frames_v2
from apex_worker.research.realeq.protocol_v2 import BoundariesV2

LO, HI, ANCHOR = 770, 900, 150  # decision sessions [LO, HI); window anchor (multiple of 10) for the truncation check
TOL = 1e-6


def plant_retest(df: pd.DataFrame, T: int, rng: np.random.Generator) -> pd.DataFrame:
    """Uptrend, a tight 30-session base, a high-volume breakout at T and a retest that holds at T+1 (S3 arm C)."""
    df = df.copy()
    c = df["close"].to_numpy(float).copy()
    p0 = c[T - 90]
    seg = [p0 * 1.004 ** (k + 1) for k in range(60)]
    base = seg[-1]
    seg += [base * (1 + rng.uniform(-0.002, 0.002)) for _ in range(30)]
    pivot = max(seg[-30:]) * 1.002
    seg += [pivot * 1.08, pivot * 1.01]
    lo_i, n = T - 90, len(seg)
    tail = c[lo_i + n :] / c[lo_i + n - 1] * seg[-1]
    close = np.concatenate([c[:lo_i], seg, tail])
    prev = np.concatenate([[close[0]], close[:-1]])
    o = prev * (1 + rng.normal(0, 0.0005, len(close)))
    span = np.full(len(close), 0.003)
    span[lo_i:T] = 0.008  # wide intraday ranges, tight closes: a larger ATR and a stop that clears the costs
    h, lo = np.maximum(o, close) * (1 + span), np.minimum(o, close) * (1 - span)
    o[T], h[T], lo[T] = pivot * 1.001, close[T] * 1.004, pivot * 0.998
    o[T + 1], lo[T + 1] = close[T] * 0.998, pivot * 1.0005
    h[T + 1] = max(o[T + 1], close[T + 1]) * 1.002
    v = df["volume"].to_numpy(float).copy()
    v[T] *= 3.0
    df["open"], df["high"], df["low"], df["close"], df["volume"] = o, h, lo, close, v
    return df


@pytest.fixture(scope="module")
def world():
    bars = factor_frames_v2(per_sector=6, seed=11, start=date(2021, 1, 4), end=date(2024, 12, 31))
    rng = np.random.default_rng(1)
    stocks = sorted(s for s in bars if s != "SPY" and not s.startswith("XL"))
    for j, s in enumerate(stocks[:16]):
        bars[s] = plant_retest(bars[s], 780 + 7 * j, rng)
    pre = S.prepare(bars)
    ctx = F.FrozenContext(pre, 0)
    decisions = {t: F.decide_at(ctx, t) for t in range(LO, HI)}
    # quarantine: ex-dates inside some label windows (signals and controls are replaced/excluded like the study)
    q = {s: [pre.sessions[LO + 25 + 13 * k]] for k, s in enumerate(stocks[20:26])}
    return {"bars": bars, "pre": pre, "ctx": ctx, "decisions": decisions, "q": q, "stocks": stocks}


def _bounds(pre) -> BoundariesV2:
    return BoundariesV2("", "", "", "", "", "", (-10, -5), (LO, HI - 1), len(pre.sessions))


def _forward_rows(world, key: str, decisions: dict[int, dict]) -> tuple[list[tuple[int, dict]], dict]:
    pre, bars, q = world["pre"], world["bars"], world["q"]
    busy: dict = {}
    rows, counts = [], {"quarantined": 0, "dedup_skipped": 0, "kept": 0, "incomplete_label": 0}
    for t in range(LO, HI):
        d = {"body": decisions[t]}
        od = F.OutcomeData(pre.sessions[t:], {s: x[x.index >= pre.sessions[t]] for s, x in bars.items()}, q,
                           pre.sessions[-1].date())
        out = F.mature(d, od, busy)["arms"][key]
        for c in counts:
            counts[c] += out["counts"][c]
        rows += [(t, r) for r in out["signals"] if r["status"] == "KEPT"]
    return rows, counts


def _compare(hist: pd.DataFrame, fw: list[tuple[int, dict]]) -> list[str]:
    bad = []
    hk = {(int(x.t), x.symbol): x for x in hist.itertuples()} if len(hist) else {}
    fk = {(t, r["symbol"]): r for t, r in fw}
    if set(hk) != set(fk):
        bad.append(f"kept sets differ: hist-only {sorted(set(hk) - set(fk))[:5]}, forward-only {sorted(set(fk) - set(hk))[:5]}")
    for key in set(hk) & set(fk):
        h, r = hk[key], fk[key]
        pairs = [(f, getattr(h, f), r[f]) for f in ("value", "value_opt", "value_tt", "value_tt_opt",
                                                    *(f"value_{c}" for c in S.COSTS), *(f"value_opt_{c}" for c in S.COSTS))]
        pairs += [("ctrl_value", h.ctrl_value, r["ctrl"].get("value")), ("ctrl_value_opt", h.ctrl_value_opt,
                                                                         r["ctrl"].get("value_opt")),
                  ("ctrl_value_tt", h.ctrl_value_tt, r["ctrl"].get("value_tt")),
                  ("ctrl_value_25", h.ctrl_value_25, r["ctrl"].get("value_25"))]
        for name, hv, fv in pairs:
            hnan = hv is None or (isinstance(hv, float) and np.isnan(hv))
            if hnan != (fv is None) or (not hnan and abs(float(hv) - fv) > TOL):
                bad.append(f"{key} {name}: study {hv} vs forward {fv}")
        if list(h.ctrl_symbols) != r["ctrl"]["symbols"]:
            bad.append(f"{key} control symbols differ")
        if h.ambiguity != r["ambiguity"] or bool(h.filled) != r["filled"]:
            bad.append(f"{key} fill/ambiguity differ")
    return bad


def test_window_ending_at_t_reproduces_full_history_decisions(world):
    pre, bars, ctx = world["pre"], world["bars"], world["ctx"]
    busy_days = [t for t in range(LO, HI) if any(a.get("funnel", {}).get("planned") for k, a in
                                                 world["decisions"][t]["arms"].items() if not k.startswith("S5"))]
    picks = sorted({busy_days[0], busy_days[len(busy_days) // 2], busy_days[-1]})
    for t in picks:
        wb = {s: df[(df.index >= pre.sessions[ANCHOR]) & (df.index <= pre.sessions[t])] for s, df in bars.items()}
        wctx = F.FrozenContext(S.prepare(wb), ANCHOR)
        assert F._round_all(F.decide_at(wctx, t - ANCHOR), 6) == F._round_all(F.decide_at(ctx, t), 6), t


def test_fwd13_future_bars_and_a_future_catalyst_cannot_change_a_decision(world):
    pre, bars = world["pre"], world["bars"]
    t = next(t for t in range(LO + 40, HI) if world["decisions"][t]["decision"] == "SHADOW_PLANS")
    cut = pre.sessions[t]
    shocked = {}
    for s, df in bars.items():
        x = df.copy()
        after = x.index > cut
        x.loc[after, ["open", "high", "low", "close"]] *= 1.5  # a huge future re-rating
        x.loc[after, "volume"] *= 10.0  # and a 'future catalyst' volume spike
        shocked[s] = x
    sctx = F.FrozenContext(S.prepare(shocked), 0)
    assert F._round_all(F.decide_at(sctx, t), 6) == F._round_all(world["decisions"][t], 6)


@pytest.mark.parametrize(("sid", "arm", "plugin"), [*F.ARMS, ("S2", "E0X0", "s2_residual_momentum")])
def test_frozen_decisions_plus_outcomes_equal_the_study_rows(world, sid, arm, plugin):
    pre, ctx = world["pre"], world["ctx"]
    key = f"{sid}:{arm}"
    if key in world["decisions"][LO]["arms"]:
        decisions = world["decisions"]
    else:  # an unlocked registered arm (more target exits than S3 alone): same code path, same function
        decisions = {t: {"session_index": t, "arms": {key: F._arm(ctx, sid, arm, plugin, t, t, set())}}
                     for t in range(LO, HI)}
    b = _bounds(pre)
    sigs, _counts = S.signals_for(S.QV2_STRATEGIES[plugin], pre, b)
    hist = S.run_arm(sid, arm, sigs, pre, b, world["q"])
    fw, counts = _forward_rows(world, key, decisions)
    fun = hist.funnel["DEV"]
    planned = sum(d["arms"][key]["funnel"]["planned"] for d in decisions.values())
    assert planned == fun["signals"] - fun["levels_rejected"], (planned, fun)
    assert sum(d["arms"][key]["funnel"]["signals"] for d in decisions.values()) == fun["signals"]
    for c in ("quarantined", "dedup_skipped", "kept", "incomplete_label"):
        assert counts[c] == fun[c], (c, counts, fun)
    assert _compare(hist.rows, fw) == []
    assert fun["kept"] > 0, f"{key}: the fixture must exercise kept trades"


def test_fixture_exercises_quarantine_dedup_and_s3(world):
    b = _bounds(world["pre"])
    fun = {}
    for sid, arm, plugin in F.ARMS:
        sigs, _ = S.signals_for(S.QV2_STRATEGIES[plugin], world["pre"], b)
        fun[sid] = S.run_arm(sid, arm, sigs, world["pre"], b, world["q"], controls=False).funnel["DEV"]
    assert fun["S3"]["kept"] >= 1 and sum(f["quarantined"] for f in fun.values()) >= 1
    assert sum(f["dedup_skipped"] for f in fun.values()) >= 1


def test_forward_s5_book_equals_the_study_series(world):
    pre, bars = world["pre"], world["bars"]
    st = LO - LO % 10
    data5 = S.S5Data(pre)
    r, _x, _xo, bench = data5.series(pre, "R10", S.C, start=st)
    decs = [{"body": F.decide_at(world["ctx"], t)} for t in range(st, HI)]
    od5 = F.OutcomeData(pre.sessions, bars, {}, pre.sessions[-1].date())
    out = F.s5_outcomes(decs, od5, 0, [pre.sessions[t].date() for t in range(st, HI - 1)])
    rows = [out[pre.sessions[t].date().isoformat()] for t in range(st, HI - 1)]
    assert all(row["book_started"] for row in rows)
    assert np.allclose([row["net"] for row in rows], r.iloc[st + 1 : HI].to_numpy(), atol=1e-7)
    assert np.allclose([row["bench"] for row in rows], bench.iloc[st + 1 : HI].to_numpy(), atol=1e-7)
    assert not any(row["rebalance_missed"] for row in rows)


def test_golden_fingerprint_is_pinned():
    """If this fails, frozen-lane behaviour changed: that requires a NEW series id and a new pinned value, never a
    silent update of this constant (the recorder refuses to record under a different fingerprint)."""
    assert F.golden_fingerprint() == F.PINNED_FINGERPRINT


def test_decision_records_carry_no_raw_bars(world):
    from apex_worker.research.forward.records import publication_problems

    for t in range(LO, HI, 7):
        assert publication_problems({"body": world["decisions"][t]}, env={}) == []
