import json
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from apex_worker.portfolio.ledger import (
    Correction, EventType, Fill, LedgerError, LedgerEvent, PositionState, apply, fmt, parse_fill, replay,
)
from apex_worker.timeutil import parse_utc

VECTORS = json.loads((Path(__file__).resolve().parents[3] / "packages/quant/vectors/ledger.json").read_text())


def _fill(d):
    return Fill(Decimal(d["price"]), Decimal(d["quantity"]), Decimal(d["fees"]), parse_utc(d["fill_at"]))


def _event(raw, seq):
    corr = None
    if "correction" in raw:
        c = raw["correction"]
        corr = Correction(c["target"], c.get("void", False), _fill(c["replacement"]) if "replacement" in c else None)
    return LedgerEvent(raw["id"], seq, EventType(raw["type"]), _fill(raw["fill"]) if "fill" in raw else None, corr)


def _check(state, exp):
    assert state.state.value == exp["state"]
    assert fmt(state.total_quantity) == fmt(Decimal(exp["total_quantity"]))
    assert fmt(state.avg_cost) == fmt(Decimal(exp["avg_cost"]))
    assert fmt(state.realized_pnl) == fmt(Decimal(exp["realized_pnl"]))
    if "fees_total" in exp:
        assert fmt(state.fees_total) == fmt(Decimal(exp["fees_total"]))
    if "lots_remaining" in exp:
        assert [fmt(lot.quantity_remaining) for lot in state.lots] == [fmt(Decimal(x)) for x in exp["lots_remaining"]]
    if "unrealized_at" in exp:
        u = exp["unrealized_at"]
        assert fmt(state.unrealized_pnl(Decimal(u["price"]))) == fmt(Decimal(u["pnl"]))


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["name"] for c in VECTORS["cases"]])
def test_shared_vectors(case):
    kept = []
    seq = 0
    state = None
    for raw in case["events"]:
        seq += 1
        ev = _event(raw, seq)
        if "error" in raw:
            with pytest.raises(LedgerError) as err:
                apply(kept, ev)
            assert err.value.code == raw["error"]
            continue
        state = apply(kept, ev)
        kept.append(ev)
        if raw["id"] in case.get("checkpoints", {}):
            _check(state, case["checkpoints"][raw["id"]])
    _check(replay(kept), case["expect"])


def test_parse_fill_rejects_bad_input():
    now = parse_utc("2026-10-20T20:00:00Z")
    t = parse_utc("2026-10-20T14:00:00Z")
    for price, qty, code in [("-1", "1", "NON_POSITIVE"), ("0", "1", "NON_POSITIVE"), ("NaN", "1", "INVALID_NUMBER"),
                             ("Infinity", "1", "INVALID_NUMBER"), ("1e9", "1", "TOO_LARGE"),
                             ("1.00001", "1", "TOO_PRECISE"), ("1", "0.0000001", "TOO_PRECISE"), ("abc", "1", "INVALID_NUMBER")]:
        with pytest.raises(LedgerError) as err:
            parse_fill(price, qty, "0", t, now)
        assert err.value.code == code, (price, qty)
    with pytest.raises(LedgerError) as err:
        parse_fill("1", "1", "0", parse_utc("2026-10-21T20:00:00Z"), now)
    assert err.value.code == "FUTURE_FILL"
    with pytest.raises(LedgerError):
        parse_fill(1.5, "1", "0", t, now)  # floats are refused: no binary rounding of money


prices = st.decimals(min_value="1", max_value="5000", places=2, allow_nan=False, allow_infinity=False)
qtys = st.decimals(min_value="0.01", max_value="1000", places=2, allow_nan=False, allow_infinity=False)


@settings(max_examples=200, deadline=None)
@given(buys=st.lists(st.tuples(prices, qtys), min_size=1, max_size=6), sell_frac=st.floats(0, 1), px=prices)
def test_property_conservation(buys, sell_frac, px):
    """Shares are conserved; realized + remaining cost reconciles to cash flows; never negative quantity."""
    events, seq = [], 0
    for i, (p, q) in enumerate(buys):
        seq += 1
        events.append(LedgerEvent(f"b{i}", seq, EventType.OPEN if i == 0 else EventType.ADD,
                                  Fill(p, q, Decimal(0), parse_utc(f"2026-10-01T14:{30 + i:02d}:00Z"))))
    total = sum(q for _, q in buys)
    sell_q = (total * Decimal(str(sell_frac))).quantize(Decimal("0.01"))
    if sell_q > 0:
        seq += 1
        events.append(LedgerEvent("s", seq, EventType.PARTIAL_EXIT, Fill(px, sell_q, Decimal(0), parse_utc("2026-10-05T15:00:00Z"))))
    stt = replay(events)
    assert stt.total_quantity == total - sell_q >= 0
    cash_in = sum(p * q for p, q in buys)
    # realized pnl + remaining cost basis == sale proceeds - cash invested + cash invested ... reconcile:
    assert abs((sell_q * px - stt.realized_pnl) + stt.cost_remaining - cash_in) < Decimal("0.00001")
    if stt.total_quantity == 0:
        assert stt.state is PositionState.CLOSED
