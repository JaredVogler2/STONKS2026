"""Discovery V3: size and power of the principal test (daily active book return, block bootstrap) BEFORE the pilot's
results are read (docs/discovery-v3/STATISTICAL_SIZE_POWER.md; 07 section 3).

Two batteries, both synthetic and seeded (no market data):
* `series`: production-shaped daily active returns of a book versus its investable alternative, with no true edge:
  market-beta leakage (beta error 0.1 on a fat-tailed market), AR(1) persistence, a volatility regime (3x for a third
  of the window) and t(4) shocks, n = the DEV length. The rule's rejection rate is its size; adding a drift of
  +1/+3/+5/+10 pp a year gives power.
* `books`: real book construction on the synthetic factor fixture: a RANDOM eligible stock per decision session, traded
  with the protocol's E0X1 execution in the cash-idle book, tested against its matched-control books (a true null:
  both are random draws under identical execution and allocation).

    uv run python bench/v3_size_power_battery.py series 1000 > out.json
    uv run python bench/v3_size_power_battery.py books 40 > out.json
"""

from __future__ import annotations

import json
import math
import sys

import numpy as np

from apex_worker.discovery.v3.portfolio import active_test

N_DEV = 1720  # DEV decision sessions of the official dataset (2019-10-23 .. 2026-09-18 plus the label tail)
SD = 0.004  # daily active sd of a half-invested swing book versus its exposure-matched alternative (assumed)


def series(rng: np.random.Generator, drift_annual: float) -> np.ndarray:
    n = N_DEV
    vol = np.ones(n)
    a = int(rng.integers(0, n - n // 3))
    vol[a : a + n // 3] = 3.0
    mkt = rng.standard_t(4, n) / math.sqrt(2) * 0.009 * vol
    e = rng.standard_t(4, n) / math.sqrt(2) * SD * vol
    x = np.empty(n)
    x[0] = e[0]
    for t in range(1, n):
        x[t] = 0.15 * x[t - 1] + e[t]
    return x + 0.1 * mkt * rng.choice([-1, 1]) + drift_annual / 252


def run_series(reps: int) -> dict:
    out = {"battery": "series", "reps": reps, "n_sessions": N_DEV, "daily_active_sd": SD}
    for drift in (0.0, 0.01, 0.03, 0.05, 0.10):
        rng = np.random.default_rng([2026, int(drift * 1000)])
        ps = []
        for _ in range(reps):
            b = np.zeros(N_DEV)
            r = series(rng, drift)
            p = active_test(r, b, n_boot=499)["p_one_sided"]
            ps.append(1.0 if p is None else p)
        ps = np.array(ps)
        key = "null" if drift == 0 else f"drift_{int(drift * 100)}pp"
        out[key] = {f"reject_at_{a}": round(float((ps < a).mean()), 4) for a in (0.05, 0.0125, 0.005)}
        out[key]["mcse_at_0.05"] = round(math.sqrt(out[key]["reject_at_0.05"] * (1 - out[key]["reject_at_0.05"]) / reps), 4)
    return out


def run_books(reps: int) -> dict:
    from apex_worker.discovery.v3 import portfolio as PF
    from apex_worker.discovery.v3.agents import sel_random_eligible
    from apex_worker.discovery.v3.contracts import AllocationSpec
    from apex_worker.discovery.v3.pipeline import _arrays, _control_trades, _trades, load_data
    from apex_worker.research.realeq import study_v2 as S

    data = load_data("fixture", per_sector=4)
    pre, b, q = data.pre, data.b, data.quarantine
    S.OPEN = pre.open_sessions
    lo, hi = b.dev_range
    end = min(len(pre.sessions) - 1, hi + 25)
    so, sc = _arrays(pre)
    closes = {s: pre.market.c[s] for s in pre.market.symbols}
    ps = []
    for r in range(reps):
        sigs = sel_random_eligible(pre, b, {"seed": 1000 + r})[::5]
        run = S.run_arm(f"v3-null-{r}", "E0X1", sigs, pre, b, q)
        port = PF.run_portfolio(_trades(run.rows), closes, so, sc, lo, end, AllocationSpec())
        books = [PF.run_portfolio(_control_trades(run.rows, pre, j), closes, so, sc, lo, end, AllocationSpec())
                 for j in range(S.K)]
        ctrl = np.mean([x.returns for x in books if x.taken], axis=0)
        p = PF.active_test(port.returns, ctrl, n_boot=499)["p_one_sided"]
        ps.append(1.0 if p is None else p)
    ps = np.array(ps)
    return {"battery": "books", "reps": reps, "data": "SYNTHETIC_FIXTURE_DEMO (factor fixture, 4 per sector)",
            **{f"reject_at_{a}": round(float((ps < a).mean()), 4) for a in (0.05, 0.0125)},
            "mean_p": round(float(ps.mean()), 4)}


if __name__ == "__main__":
    kind, reps = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 200
    print(json.dumps(run_series(reps) if kind == "series" else run_books(reps)))
