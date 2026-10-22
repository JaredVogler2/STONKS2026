"""Quant V2 M0 measurements (docs/quant-v2/M0_AUDIT.md). Synthetic, seeded, offline; no market data.

    uv run python bench/qv2_m0_measurements.py            # prints JSON

1. QV2-002: daily-bar simulator vs continuous intraday paths (tests/test_qv2_m0_audit.py generator and truth model):
   how often daily accounting is optimistic / pessimistic, split by fill type.
2. QV2-003: Type-I error of the protocol-1 statistic `p_one_sided = mean(weekly-block bootstrap means <= 0)` when the
   true mean trade return is exactly zero, for clustered/overlapping trade designs shaped like H8, H5 and an IID control.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from apex_worker.research.execution import ExecConfig, simulate
from apex_worker.research.realeq import analysis as A
from test_qv2_m0_audit import _paths, _truth


def path_study(n: int = 20000, seed: int = 20261022) -> dict:
    rng = np.random.default_rng(seed)
    out = {"scenarios": n, "filled": 0, "equal": 0, "optimistic": 0, "pessimistic": 0,
           "open_fills": 0, "limit_fills": 0, "open_fill_day_target_touched": 0, "optimistic_excess_mean": None}
    excess = []
    for _ in range(n):
        paths, o, h, low, c = _paths(rng)
        t = int(rng.integers(2, 10))
        close, atr = float(c[t]), float(np.mean(h[t - 2 : t + 1] - low[t - 2 : t + 1])) or 1.0
        emin, emax = close - 0.5 * atr, close + min(0.5 * atr, 0.02 * close)
        stop = (emin + emax) / 2 - rng.uniform(1.0, 3.0) * atr
        target = (emin + emax) / 2 + rng.uniform(1.5, 2.5) * ((emin + emax) / 2 - stop)
        d = simulate(o, h, low, c, t, emin, emax, stop, target, ExecConfig(cost_bps_per_side=0.0))
        tr = _truth(paths, t, emin, emax, stop, target)
        if not d.filled:
            continue
        out["filled"] += 1
        open_fill = emin <= o[d.entry_index] <= emax
        out["open_fills" if open_fill else "limit_fills"] += 1
        if open_fill and h[d.entry_index] >= target and low[d.entry_index] > stop:
            out["open_fill_day_target_touched"] += 1
        net_t = tr["exit"] / tr["fill"] - 1
        if d.return_net > net_t + 1e-6:
            out["optimistic"] += 1
            excess.append(d.return_net - net_t)
        elif d.return_net < net_t - 1e-6:
            out["pessimistic"] += 1
        else:
            out["equal"] += 1
    out["optimistic_excess_mean"] = round(float(np.mean(excess)), 5) if excess else None
    out["note"] = "synthetic paths; frequencies characterise the mechanism, not real-market prevalence"
    return out


def _design(rng, n_dates: int, per_date: int, hold: int = 20, span: int = 350, iid: bool = False):
    m = rng.normal(0, 0.009, span + hold)
    sectors = rng.normal(0, 0.006, (11, span + hold))
    if iid:  # independent trades: own market draw each, still 20-session sums
        vals = rng.normal(0, math.sqrt(hold) * math.sqrt(0.009**2 + 0.006**2 + 0.012**2), n_dates * per_date)
        weeks = np.array([str(i) for i in range(len(vals))])
        return vals, weeks
    starts = np.sort(rng.choice(np.arange(0, span, 5), n_dates, replace=False))
    vals, weeks = [], []
    for s in starts:
        for _ in range(per_date):
            sec = int(rng.integers(11))
            vals.append(float((m[s : s + hold] + sectors[sec, s : s + hold] + rng.normal(0, 0.012, hold)).sum()))
            weeks.append(str(s // 5))
    return np.array(vals), np.array(weeks)


def type_one(reps: int = 1000, n_boot: int = 500, seed: int = 3) -> dict:
    designs = {
        "H8_like_15_weeks_x_9": {"n_dates": 15, "per_date": 9},
        "H5_like_60_weeks_x_4": {"n_dates": 60, "per_date": 4},
        "iid_140_trades": {"n_dates": 140, "per_date": 1, "iid": True},
    }
    out = {}
    for name, kw in designs.items():
        rng = np.random.default_rng(seed)
        ps = []
        for _ in range(reps):
            v, w = _design(rng, **kw)
            means = A.boot_means(v, w, n_boot=n_boot, seed=11)
            ps.append(float((means <= 0).mean()))
        ps = np.array(ps)
        row = {"reps": reps, "n_boot": n_boot}
        for alpha in (0.05, 0.10):
            r = float((ps < alpha).mean())
            se = math.sqrt(r * (1 - r) / reps)
            row[f"reject_at_{alpha}"] = round(r, 4)
            row[f"reject_at_{alpha}_95ci"] = [round(max(0.0, r - 1.96 * se), 4), round(min(1.0, r + 1.96 * se), 4)]
        out[name] = row
    out["null"] = "true mean trade return exactly 0; nominal one-sided test rejects when p_one_sided < alpha"
    return out


if __name__ == "__main__":
    print(json.dumps({"path_study": path_study(), "type_one": type_one()}, indent=2))
