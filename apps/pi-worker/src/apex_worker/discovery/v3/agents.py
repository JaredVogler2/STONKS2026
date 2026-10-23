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
Registered WEIGHTS agents: spy (100% SPY); momentum_weights (equal weight in the top-n by 60-session return, rebalanced
every `every` sessions, cash in risk-off regimes when `abstain` is set); random_weights (its negative control: n random
eligible members, same schedule); spy_regime (SPY, cash while the causal regime label is risk-off or a volatility shock).
momentum_top accepts `min_dispersion_pct`: trade only when the cross-sectional dispersion of 20-session returns is at
or above that percentile of its own trailing year (an opportunity-state gate; idle capital otherwise).
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


def dispersion_percentile(pre: S.Prepared, lookback: int = 252, col: str = "ret_20") -> np.ndarray:
    """Cross-sectional standard deviation of `col` among eligible members at each session, as a percentile of its own
    trailing `lookback` sessions (values <= t only). NaN until 60 sessions of history exist."""
    x = np.vstack([pre.frames[s][col].to_numpy(float) for s in pre.stocks])
    el = np.vstack([pre.eligible[s] for s in pre.stocks]) & np.isfinite(x)
    disp = np.array([np.std(x[el[:, t], t]) if el[:, t].sum() >= 10 else np.nan for t in range(x.shape[1])])
    out = np.full(len(disp), np.nan)
    for t in range(len(disp)):
        w = disp[max(0, t - lookback + 1) : t + 1]
        w = w[np.isfinite(w)]
        if len(w) >= 60 and np.isfinite(disp[t]):
            out[t] = float((w <= disp[t]).mean())
    return out


def sel_momentum_top(pre: S.Prepared, b: BoundariesV2, params: dict) -> list[S.Signal]:
    n, every, col = int(params.get("n", 5)), int(params.get("every", 5)), params.get("feature", "ret_60")
    gate = params.get("min_dispersion_pct")
    syms = pre.stocks
    x = np.vstack([pre.frames[s][col].to_numpy(float) for s in syms])
    el = np.vstack([pre.eligible[s] for s in syms]) & np.isfinite(x)
    dpct = dispersion_percentile(pre) if gate is not None else None
    out = []
    for t in _window(b):
        if t % every or not el[:, t].any():
            continue
        if dpct is not None and not (np.isfinite(dpct[t]) and dpct[t] >= float(gate)):
            continue  # the opportunity-state gate: stay out (idle capital) when dispersion is low
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


def w_random(pre: S.Prepared, b: BoundariesV2, params: dict) -> pd.DataFrame:
    """Negative control for momentum_weights: n seeded random eligible members, same rebalance and weighting. It
    measures what an equal-weight survivor portfolio earns without any selection skill."""
    n, every = int(params.get("n", 10)), int(params.get("every", 20))
    rng = np.random.default_rng(int(params.get("seed", 20261022)))
    syms = pre.stocks
    el = np.vstack([pre.eligible[s] for s in syms])
    w = pd.DataFrame(np.nan, index=pre.sessions, columns=syms)
    for t in range(b.dev_range[0] - 1, b.dev_range[1] + 1):
        if (t - (b.dev_range[0] - 1)) % every:
            continue
        row = np.zeros(len(syms))
        cand = np.flatnonzero(el[:, t])
        if len(cand):
            pick = rng.choice(cand, size=min(n, len(cand)), replace=False)
            row[pick] = 1.0 / n
        w.iloc[t] = row
    return w


def w_spy_regime(pre: S.Prepared, b: BoundariesV2, params: dict) -> pd.DataFrame:
    """SPY, except cash while the online regime label (causal, rolling windows only) is in `abstain`."""
    abstain = tuple(params.get("abstain", ABSTAIN_REGIMES))
    reg = pre.regime.reindex(pre.sessions).astype(str).to_numpy()
    w = pd.DataFrame(np.nan, index=pre.sessions, columns=[BENCHMARK])
    prev = None
    for t in range(b.dev_range[0] - 1, b.dev_range[1] + 1):
        target = 0.0 if reg[t] in abstain else 1.0
        if target != prev:  # trade only on changes (decided at the close of t, executed at the next open)
            w.iloc[t] = target
            prev = target
    return w


WEIGHT_AGENTS: dict[str, Callable[[S.Prepared, BoundariesV2, dict], pd.DataFrame]] = {
    "spy": w_spy, "momentum_weights": w_momentum, "random_weights": w_random, "spy_regime": w_spy_regime,
}
