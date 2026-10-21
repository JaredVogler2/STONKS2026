"""Purged, embargoed walk-forward validation with an untouched chronological holdout.

* The last `holdout_fraction` of decision dates is set aside BEFORE any fitting or threshold choice.
* Development folds roll forward: train on everything before the test block whose label window ends
  at least `embargo_sessions` exchange sessions before the test block starts (purging overlapping 20-session
  labels), predict the block out of sample.
* Thresholds are chosen on development OOS predictions only, then applied once to the holdout.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from apex_worker.market.calendar import MarketCalendar, OutOfCalendarRange, default_calendar
from apex_worker.research import metrics as M
from apex_worker.research.model import CompetingModel, train


@dataclass(frozen=True)
class ValidationConfig:
    holdout_fraction: float = 0.2
    n_folds: int = 4
    embargo_sessions: int = 5
    min_train: int = 150
    calibration_fraction: float = 0.25
    l2: float = 4.0
    seed: int = 20261020


@dataclass
class FoldResult:
    fold: int
    train_n: int
    test_n: int
    test_start: str
    test_end: str
    brier: float
    brier_skill: float
    log_loss: float
    ece: float
    base_rate: float


@dataclass
class WalkForwardResult:
    folds: list[FoldResult]
    oos: pd.DataFrame  # development OOS predictions (columns p_target, p_stop, p_time, y)
    holdout: pd.DataFrame
    holdout_start: pd.Timestamp
    final_model: CompetingModel | None
    holdout_metrics: dict = field(default_factory=dict)
    purged_count: int = 0


def _labels(df: pd.DataFrame) -> list[str]:
    return ["TIME" if o in ("TIME", "DATA_END") else o for o in df["outcome"]]


def _rows(df: pd.DataFrame, features: tuple[str, ...]) -> list[dict]:
    return df[list(features)].astype(float).replace([np.inf, -np.inf], np.nan).to_dict("records")


def _fit(train_df: pd.DataFrame, features: tuple[str, ...], cfg: ValidationConfig, meta: dict) -> CompetingModel:
    n = len(train_df)
    cut = int(n * (1 - cfg.calibration_fraction))
    fit_part, cal_part = train_df.iloc[:cut], train_df.iloc[cut:]
    return train(
        _rows(fit_part, features),
        _labels(fit_part),
        features,
        _rows(cal_part, features),
        _labels(cal_part),
        l2=cfg.l2,
        metadata=meta,
    )


def embargo_limit(test_start: pd.Timestamp, embargo: int, cal: MarketCalendar | None = None) -> pd.Timestamp:
    """The exchange session `embargo` trading sessions before `test_start` (APEX-004: sessions, not weekdays, so a
    holiday never shortens the embargo). Outside the pinned calendar (very old fixtures) it falls back to weekdays."""
    cal = cal or default_calendar()
    day = pd.Timestamp(test_start).date()
    try:
        for _ in range(embargo):
            day = cal.previous_session(day)
    except OutOfCalendarRange:
        return test_start - pd.tseries.offsets.BDay(embargo)
    return pd.Timestamp(day)


def _purge(train_df: pd.DataFrame, test_start: pd.Timestamp, embargo: int) -> pd.DataFrame:
    """Keep training events whose label window (exit, or decision if unfilled) ends before the embargo limit."""
    limit = embargo_limit(test_start, embargo)
    ends = train_df["exit_date"].fillna(train_df["decision_date"])
    return train_df[ends < limit]


def walk_forward(
    events: pd.DataFrame,
    features: tuple[str, ...],
    cfg: ValidationConfig = ValidationConfig(),
    shuffle_labels: bool = False,
    holdout_start: pd.Timestamp | None = None,
) -> WalkForwardResult:
    """`holdout_start` freezes the holdout boundary on the calendar (real-equity protocol); default: the last
    `holdout_fraction` of this event set's decision dates."""
    ev = events[events["filled"]].sort_values("decision_date").reset_index(drop=True)
    if shuffle_labels:
        rng = np.random.default_rng(cfg.seed)
        ev = ev.copy()
        ev["outcome"] = rng.permutation(ev["outcome"].to_numpy())
    dates = np.sort(ev["decision_date"].unique())
    if len(dates) < 10:
        return WalkForwardResult([], pd.DataFrame(), pd.DataFrame(), pd.NaT, None)
    if holdout_start is None:
        holdout_start = pd.Timestamp(dates[int(len(dates) * (1 - cfg.holdout_fraction))])
    holdout_start = pd.Timestamp(holdout_start)
    dev = ev[ev["decision_date"] < holdout_start]
    holdout = ev[ev["decision_date"] >= holdout_start].copy()
    dev_dates = np.sort(dev["decision_date"].unique())
    blocks = np.array_split(dev_dates, cfg.n_folds + 1)
    folds: list[FoldResult] = []
    oos_parts = []
    purged_total = 0
    for k in range(1, len(blocks)):
        if len(blocks[k]) == 0:
            continue
        t0, t1 = pd.Timestamp(blocks[k][0]), pd.Timestamp(blocks[k][-1])
        train_all = dev[dev["decision_date"] < t0]
        tr = _purge(train_all, t0, cfg.embargo_sessions)
        purged_total += len(train_all) - len(tr)
        te = dev[(dev["decision_date"] >= t0) & (dev["decision_date"] <= t1)]
        if len(tr) < cfg.min_train or te.empty:
            continue
        model = _fit(tr, features, cfg, {"fold": k})
        p = model.predict(_rows(te, features))
        y = (te["outcome"] == "TARGET").to_numpy(float)
        base = float((tr["outcome"] == "TARGET").mean())
        folds.append(
            FoldResult(
                k,
                len(tr),
                len(te),
                str(t0.date()),
                str(t1.date()),
                round(M.brier(p[:, 0], y), 5),
                round(M.brier_skill(p[:, 0], y, base), 5),
                round(M.log_loss(p[:, 0], y), 5),
                round(M.ece(p[:, 0], y), 5),
                round(base, 4),
            )
        )
        part = te.copy()
        part["p_target"], part["p_stop"], part["p_time"] = p[:, 0], p[:, 1], p[:, 2]
        part["y"] = y
        part["fold"] = k
        oos_parts.append(part)
    oos = pd.concat(oos_parts) if oos_parts else pd.DataFrame()
    final_train = _purge(dev, holdout_start, cfg.embargo_sessions)
    purged_total += len(dev) - len(final_train)
    final = None
    hm: dict = {}
    if len(final_train) >= cfg.min_train and not holdout.empty:
        final = _fit(
            final_train,
            features,
            cfg,
            {
                "trained_through": str(final_train["decision_date"].max().date()),
                "holdout_start": str(holdout_start.date()),
            },
        )
        p = final.predict(_rows(holdout, features))
        y = (holdout["outcome"] == "TARGET").to_numpy(float)
        base = float((final_train["outcome"] == "TARGET").mean())
        holdout["p_target"], holdout["p_stop"], holdout["p_time"] = p[:, 0], p[:, 1], p[:, 2]
        holdout["y"] = y
        hm = {
            "n": len(holdout),
            "base_rate_train": round(base, 4),
            "observed_rate": round(float(y.mean()), 4),
            "brier": round(M.brier(p[:, 0], y), 5),
            "brier_skill": round(M.brier_skill(p[:, 0], y, base), 5),
            "log_loss": round(M.log_loss(p[:, 0], y), 5),
            "ece": round(M.ece(p[:, 0], y), 5),
            "reliability": M.reliability(p[:, 0], y),
        }
    return WalkForwardResult(folds, oos, holdout, holdout_start, final, hm, purged_total)
