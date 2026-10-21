"""APEX-001: the feature vector a published model sees live must equal the one it was trained on.

For the same symbol, strategy and session, the research event builder (training rows) and the live path
(CandidateEngine.scan -> compute_levels -> policy.model_row) must produce the same value for EVERY model input.
A mismatch silently shifts a calibrated forecast (missing inputs are imputed to the training mean).
"""

import math
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from apex_worker.demo.synthetic import generate
from apex_worker.features.panel import panel_from_synthetic
from apex_worker.research.dataset import MODEL_FEATURES, DatasetSpec, build_events
from apex_worker.signals.candidates import CandidateEngine
from apex_worker.signals.levels import Levels, compute_levels
from apex_worker.signals.policy import model_row
from apex_worker.signals.strategies.library import STRATEGIES


@pytest.fixture(scope="module")
def pairs():
    market = generate(start=date(2023, 1, 2), end=date(2024, 12, 31), stocks_per_sector=3)
    panel = panel_from_synthetic(market)
    bars = {s: market.adjusted(s) for s in market.bars}
    strategies = list(STRATEGIES.values())
    events = build_events(panel, bars, DatasetSpec(strategies=strategies))
    assert len(events) > 20, "fixture must produce research events"
    engine = CandidateEngine(strategies=strategies, is_demo=True)
    by_key = {(e.symbol, e.strategy_id, pd.Timestamp(e.decision_date)): e for e in events.itertuples()}
    out = []
    for day in sorted({pd.Timestamp(d) for d in events["decision_date"]})[-40:]:
        for cand in engine.scan(panel, day).candidates:
            ev = by_key.get((cand["symbol"], cand["strategy_id"], day))
            if ev is None:
                continue
            i = cand["_internal"]
            lv = compute_levels(i["close"], i["atr"], i.get("invalidation_hint"), i.get("resistance"))
            assert isinstance(lv, Levels)
            out.append((ev, model_row(cand, lv)))
    assert len(out) >= 10, f"need matched live/research pairs, got {len(out)}"
    return out


def _same(a, b) -> bool:
    a_nan = a is None or (isinstance(a, float) and math.isnan(a))
    b_nan = b is None or (isinstance(b, float) and math.isnan(b))
    if a_nan or b_nan:
        return a_nan and b_nan
    return bool(np.isclose(float(a), float(b), rtol=0, atol=1e-9))


def test_live_model_row_has_exactly_the_training_columns(pairs):
    _ev, live = pairs[0]
    assert set(MODEL_FEATURES) <= set(live), set(MODEL_FEATURES) - set(live)


@pytest.mark.parametrize("feature", MODEL_FEATURES)
def test_every_model_input_matches_training(pairs, feature):
    bad = [(ev.symbol, str(ev.decision_date.date()), getattr(ev, feature), live[feature])
           for ev, live in pairs if not _same(getattr(ev, feature), live[feature])]
    assert not bad, f"{feature}: {len(bad)}/{len(pairs)} live values differ from training, e.g. {bad[:3]}"


class _FakeModel:
    def predict(self, rows):
        assert all(set(MODEL_FEATURES) <= set(r) for r in rows)
        return [[0.55, 0.30, 0.15]]


def _active(status="CALIBRATED", source="historical_market_data"):
    from types import SimpleNamespace

    return SimpleNamespace(model=_FakeModel(), manifest={
        "model_id": "mdl_test0001", "data_source": source, "validation_expires_at": "2099-01-01T00:00:00Z",
        "evaluation": {"confidence_status": status, "holdout_n": 240, "threshold_p_target": 0.5},
    })


def test_a_candidate_without_the_training_vector_gets_no_forecast(pairs):
    from apex_worker.signals.policy import confidence_block

    _ev, live = pairs[0]
    stale = {"_internal": {"setup_score": 0.5}, "quality_score": 80.0, "group_scores": {}}  # older engine: no inputs
    lv = compute_levels(50.0, 1.5, 47.0, None)
    assert model_row(stale, lv) is None
    conf, fc = confidence_block(_active(), None, "2026-10-20T00:00:00Z", "PAPER_LIVE_DATA")
    assert conf["status"] == "UNVALIDATED" and fc is None and "input vector unavailable" in conf["label"]
    conf_ok, fc_ok = confidence_block(_active(), live, "2026-10-20T00:00:00Z", "PAPER_LIVE_DATA")
    assert conf_ok["status"] == "CALIBRATED" and fc_ok is not None


def test_apex002_no_unsupported_interval_is_published(pairs):
    """APEX-002: a binomial band from the holdout count is not a fitted-model interval; publish the estimate only."""
    from apex_worker.contracts import validate
    from apex_worker.discord_bot.samples import sample_signal
    from apex_worker.notifications.render import confidence_text
    from apex_worker.signals.policy import confidence_block

    _ev, live = pairs[0]
    for status in ("CALIBRATED", "EXPERIMENTAL"):
        conf, _ = confidence_block(_active(status), live, "2026-10-20T00:00:00Z", "PAPER_LIVE_DATA")
        assert not {"interval_80", "interval_95", "effective_sample_size"} & set(conf)
        assert "interval not established" in conf["label"] and conf["sample_count"] == 240
        assert "interval" not in confidence_text(conf).replace("interval not established", "")
        sig = sample_signal(datetime(2026, 10, 20, tzinfo=UTC))
        validate("TradeSignal", {**sig, "confidence": conf})  # still a valid published contract
