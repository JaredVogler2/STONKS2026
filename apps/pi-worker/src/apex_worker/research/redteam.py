"""Red-team datasets for the research lab (Phase 12 quantitative red team).

`planted_alpha_events` builds an event table whose outcome probability depends on two observable
features through a KNOWN logistic link. A correct pipeline must (a) recover positive out-of-sample
skill and calibration on it, and (b) collapse to no skill when labels are shuffled. These models are
labeled synthetic red-team artifacts and may only become DEMO champions (never live).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from apex_worker.research.dataset import MODEL_FEATURES

REDTEAM_STRATEGY = "redteam_planted_alpha"


def planted_alpha_events(n: int = 3000, seed: int = 99, strength: float = 1.6, start: str = "2018-01-02") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n // 3)
    decision = np.sort(rng.choice(dates, size=n))
    x1 = rng.normal(0, 1, n)  # e.g. 'squeeze_prev' (inverted)
    x2 = rng.normal(0, 1, n)  # e.g. 'rel_volume_1'
    noise = {f: rng.normal(0, 1, n) for f in MODEL_FEATURES}
    logit_t = -0.9 + strength * (0.8 * x1 + 0.6 * x2)
    logit_s = -0.2 - 0.6 * strength * x1
    logit_e = np.zeros(n)
    z = np.stack([logit_t, logit_s, logit_e], axis=1)
    p = np.exp(z - z.max(axis=1, keepdims=True))
    p /= p.sum(axis=1, keepdims=True)
    u = rng.random(n)
    outcome = np.where(u < p[:, 0], "TARGET", np.where(u < p[:, 0] + p[:, 1], "STOP", "TIME"))
    gross = np.where(outcome == "TARGET", 0.06, np.where(outcome == "STOP", -0.03, rng.normal(0.004, 0.02, n)))
    held = rng.integers(2, 20, n)
    df = pd.DataFrame({k: v for k, v in noise.items()})
    df["squeeze_prev"] = -x1
    df["rel_volume_1"] = x2
    df["quality"] = 50 + 10 * (0.8 * x1 + 0.6 * x2) + rng.normal(0, 3, n)
    df["decision_date"] = pd.to_datetime(decision)
    df["entry_date"] = df["decision_date"] + pd.tseries.offsets.BDay(1)
    df["exit_date"] = df["entry_date"] + pd.to_timedelta(held * 1.4, unit="D")
    df["symbol"] = [f"RT{i % 50:02d}" for i in range(n)]
    df["sector"] = [f"s{i % 7}" for i in range(n)]
    df["strategy_id"] = REDTEAM_STRATEGY
    df["regime"] = np.where(rng.random(n) < 0.6, "trending_up", "choppy_neutral")
    df["filled"] = True
    df["outcome"] = outcome
    df["return_gross"] = gross
    df["return_net"] = (1 + gross) * (1 - 0.001) / (1 + 0.001) - 1
    df["r_multiple"] = gross / 0.03
    df["ambiguous"] = False
    df["gap_stop"] = False
    df["sessions_held"] = held
    df["mfe"] = np.maximum(gross, 0)
    df["mae"] = np.minimum(gross, 0)
    return df
