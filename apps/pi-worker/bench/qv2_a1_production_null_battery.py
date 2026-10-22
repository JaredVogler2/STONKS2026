"""Quant V2 A1: size of the protocol-2 DECISION rule on PRODUCTION-SHAPED null panels (docs/quant-v2/PROTOCOL_V2_DESIGN.md
section 9; PROTOCOL_V2.md section 12).

The P2 battery (qv2_p2_inference_battery.py) pairs each trade with an independent control, so its trades carry almost
no cross-trade dependence (design effect about 1). Production values are a strategy trade minus the mean of K = 10
same-session, same-sector controls, with persistent stock identities, residual beta/style exposure that sector
matching does not remove, clustered signal times and fat tails. This battery simulates that (the panel generator is
the one the independent statistics review used) and measures the rule with and without the A1 Student-t floor.

    uv run python bench/qv2_a1_production_null_battery.py <design> <reps> > out.json    # about 0.1 s per replicate

Synthetic, seeded, offline; no market data.
"""

from __future__ import annotations

import json
import math
import sys
import zlib

import numpy as np

from apex_worker.research.inference import Design, block_tests, simulate_design, student_t_sf

T, H, K, N, NSEC = 684, 20, 10, 330, 11  # PRESAMPLE sessions, hold, controls, stocks, sectors

DESIGNS = {
    "battery_presample_684": None,  # the P2 battery's paired design, for reference
    "prod_gaussian": {"rate": 0.6, "burst": 1.0},
    "prod_fat_tails_vol_regime": {"rate": 0.6, "burst": 1.0, "df": 3, "hetero": True},
    "prod_persistent_premium_5pct_yr": {"rate": 0.6, "burst": 1.0, "mu_sd": 0.0002, "mu_phi": 0.99},
}


def panel(rng: np.random.Generator, cfg: dict) -> tuple[np.ndarray, np.ndarray]:
    """One null panel: per-signal value (strategy minus mean of K same-sector controls) and its decision session."""
    tt = T + H + 3

    def z(size):
        if cfg.get("df"):
            d = cfg["df"]
            return rng.standard_t(d, size) / math.sqrt(d / (d - 2))
        return rng.normal(0, 1, size)

    vol = np.ones(tt)
    if cfg.get("hetero"):
        a = int(rng.integers(0, tt - tt // 3))
        vol[a : a + tt // 3] = 3.0
    m, sec, f = z(tt) * 0.009 * vol, z((NSEC, tt)) * 0.006 * vol, z(tt) * 0.005 * vol
    if cfg.get("mu_sd"):  # slowly varying factor premium, stationary AR(1) with mean 0
        phi, s = cfg["mu_phi"], cfg["mu_sd"]
        mu = np.empty(tt)
        mu[0] = rng.normal(0, s)
        e_mu = rng.normal(0, s * math.sqrt(1 - phi**2), tt)
        for k in range(1, tt):
            mu[k] = phi * mu[k - 1] + e_mu[k]
        f = f + mu
    e = z((N, tt)) * 0.012 * vol
    beta, g, sector = rng.uniform(0.7, 1.3, N), rng.normal(0, 1, N), np.arange(N) % NSEC

    def cum(x):
        return np.concatenate([np.zeros((*x.shape[:-1], 1)), np.cumsum(x, axis=-1)], axis=-1)

    big_m, big_s, big_f, big_e = cum(m), cum(sec), cum(f), cum(e)

    def hold(i, t):
        a, b = t + 1, t + 1 + H
        return (beta[i] * (big_m[b] - big_m[a]) + (big_s[sector[i], b] - big_s[sector[i], a])
                + g[i] * (big_f[b] - big_f[a]) + (big_e[i, b] - big_e[i, a]))

    lead = np.flatnonzero(g >= np.quantile(g, 0.8))  # the strategy picks style-loaded, higher-beta names
    lead = lead[beta[lead] > 0.9] if (beta[lead] > 0.9).sum() > 10 else lead
    zt = np.empty(T)
    zt[0] = rng.normal()
    for k in range(1, T):
        zt[k] = 0.98 * zt[k - 1] + rng.normal() * math.sqrt(1 - 0.98**2)
    lam = cfg["rate"] * np.exp(cfg["burst"] * zt - cfg["burst"] ** 2 / 2)  # clustered signal intensity
    busy = np.full(N, -1)
    vals, idx = [], []
    for t in range(T):
        n_t = rng.poisson(rng.gamma(1.0, lam[t]))
        if n_t == 0:
            continue
        free = lead[busy[lead] < t]
        if len(free) == 0:
            continue
        picks = rng.choice(free, size=min(n_t, len(free)), replace=False)
        today = set(picks.tolist())
        for i in picks:
            busy[i] = t + H
            pool = np.array([p for p in np.flatnonzero(sector == sector[i]) if p not in today])
            cs = rng.choice(pool, size=min(K, len(pool)), replace=False)
            vals.append(hold(i, t) - np.mean([hold(c, t) for c in cs]))
            idx.append(t)
    return np.array(vals), np.array(idx)


def main(name: str, reps: int) -> dict:
    rng = np.random.default_rng([zlib.crc32(name.encode()), 2026])
    cur, flo = [], []
    for r in range(reps):
        if DESIGNS[name] is None:
            v, ix = simulate_design(Design("p", span=T, dates=120, per_date=3), rng)
        else:
            v, ix = panel(rng, DESIGNS[name])
        res = block_tests(v, ix, T, lengths=(20, 40), n_boot=999, seed=r, min_blocks=12)
        p = res["summary"]["p_one_sided_max"]
        cur.append(1.0 if p is None else p)
        flo.append(1.0 if p is None else max(p, *(student_t_sf(res[f"L{L}"]["t"], res[f"L{L}"]["nonempty_blocks"] - 1)
                                                for L in (20, 40))))
    out: dict = {"design": name, "reps": reps}
    for alpha in (0.0125, 0.05):
        for key, arr in (("a0_rule", cur), ("a1_t_floor", flo)):
            q = float((np.array(arr) < alpha).mean())
            out[f"{key}_size_at_{alpha}"] = round(q, 4)
            out[f"{key}_mcse_at_{alpha}"] = round(math.sqrt(q * (1 - q) / reps), 4)
    return out


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 1000)))
