"""Selectors and reference agents for V3 policies (03 section 3; 05 section 2).

A SELECTOR turns causal features at decision session t into candidate signals (research.realeq.study_v2.Signal); the
pipeline then applies a registered execution arm (execution_v2, via study_v2.run_arm, so entries, stops, targets,
quarantine, de-duplication and matched controls are the protocol's) and the portfolio book. A WEIGHTS agent returns
target weights decided at a close and traded at the next open (no stops), evaluated with the S5 allocation engine.

Registered selectors (all read rows <= t only):
* none                 : never signals (the always-cash / NO_TRADE reference);
* s1/s2/s3a/s3c ...    : the protocol-2 plugins, unchanged;
* momentum_top         : every `every` sessions the `n` eligible stocks with the highest 60-session return;
* breakout_basic       : protocol-2's simple breakout baseline;
* random_eligible      : one seeded random eligible stock per session (placebo / negative control).
Registered WEIGHTS agents: spy (100% SPY) and momentum_weights (equal weight in the top-n by 60-session return,
rebalanced every `every` sessions, cash in risk-off regimes when `abstain` is set).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from apex_worker.research.realeq import study_v2 as S
from apex_worker.research.realeq.protocol_v2 import BoundariesV2
from apex_worker.research.training_data import BENCHMARK
from apex_worker.signals.strategies.quant_v2 import QV2_STRATEGIES

Selector = Callable[[S.Prepared, BoundariesV2, dict], list[S.Signal]]
ABSTAIN_REGIMES = ("risk_off", "volatility_shock")


def _window(b: BoundariesV2) -> range:
    return range(b.dev_range[0], b.dev_range[1] + 1)


def sel_none(pre: S.Prepared, b: BoundariesV2, params: dict) -> list[S.Signal]:
    return []


def _plugin(plugin: str) -> Selector:
    def f(pre: S.Prepared, b: BoundariesV2, params: dict) -> list[S.Signal]:
        sigs, _counts = S.signals_for(QV2_STRATEGIES[plugin], pre, b)
        return [g for g in sigs if b.dev_range[0] <= g.t <= b.dev_range[1]]

    return f


def sel_momentum_top(pre: S.Prepared, b: BoundariesV2, params: dict) -> list[S.Signal]:
    n, every, col = int(params.get("n", 5)), int(params.get("every", 5)), params.get("feature", "ret_60")
    syms = pre.stocks
    x = np.vstack([pre.frames[s][col].to_numpy(float) for s in syms])
    el = np.vstack([pre.eligible[s] for s in syms]) & np.isfinite(x)
    out = []
    for t in _window(b):
        if t % every or not el[:, t].any():
            continue
        cand = np.flatnonzero(el[:, t])
        best = sorted(cand, key=lambda i: (-x[i, t], syms[i]))[:n]
        out += [S.Signal(syms[i], t, None, float(x[i, t])) for i in best]
    return sorted(out, key=lambda g: (g.t, g.symbol))


def sel_breakout_basic(pre: S.Prepared, b: BoundariesV2, params: dict) -> list[S.Signal]:
    return [g for g in S.baseline_breakout(pre, b) if b.dev_range[0] <= g.t <= b.dev_range[1]]


def sel_random_eligible(pre: S.Prepared, b: BoundariesV2, params: dict) -> list[S.Signal]:
    rng = np.random.default_rng(int(params.get("seed", 20261022)))
    out = []
    for t in _window(b):
        el = [s for s in pre.stocks if pre.eligible[s][t]]
        if el:
            out.append(S.Signal(el[int(rng.integers(len(el)))], t, None, 0.0))
    return out


SELECTORS: dict[str, Selector] = {
    "none": sel_none,
    "s1_leader_pullback": _plugin("s1_leader_pullback"),
    "s2_residual_momentum": _plugin("s2_residual_momentum"),
    "s3a_breakout_immediate": _plugin("s3a_breakout_immediate"),
    "s3c_breakout_retest": _plugin("s3c_breakout_retest"),
    "momentum_top": sel_momentum_top,
    "breakout_basic": sel_breakout_basic,
    "random_eligible": sel_random_eligible,
}


# ---- WEIGHTS agents ---------------------------------------------------------------------------------------------------
def w_spy(pre: S.Prepared, b: BoundariesV2, params: dict) -> pd.DataFrame:
    idx = pre.sessions
    w = pd.DataFrame(np.nan, index=idx, columns=[BENCHMARK])
    w.iloc[b.dev_range[0] - 1] = 1.0  # bought at the open of the first evaluated session, then held
    return w


def w_momentum(pre: S.Prepared, b: BoundariesV2, params: dict) -> pd.DataFrame:
    n, every = int(params.get("n", 10)), int(params.get("every", 20))
    abstain = bool(params.get("abstain", False))
    syms = pre.stocks
    x = np.vstack([pre.frames[s]["ret_60"].to_numpy(float) for s in syms])
    el = np.vstack([pre.eligible[s] for s in syms]) & np.isfinite(x)
    reg = pre.regime.reindex(pre.sessions).astype(str).to_numpy()
    w = pd.DataFrame(np.nan, index=pre.sessions, columns=syms)
    for t in range(b.dev_range[0] - 1, b.dev_range[1] + 1):
        if (t - (b.dev_range[0] - 1)) % every:
            continue
        row = np.zeros(len(syms))
        if not (abstain and reg[t] in ABSTAIN_REGIMES) and el[:, t].any():
            best = sorted(np.flatnonzero(el[:, t]), key=lambda i: (-x[i, t], syms[i]))[:n]
            row[best] = 1.0 / n  # fewer qualifiers -> the rest stays in cash
        w.iloc[t] = row
    return w


WEIGHT_AGENTS: dict[str, Callable[[S.Prepared, BoundariesV2, dict], pd.DataFrame]] = {
    "spy": w_spy, "momentum_weights": w_momentum,
}
