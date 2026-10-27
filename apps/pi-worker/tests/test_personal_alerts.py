"""The personal Discord alerts (personal/): commands, the account book, the runner against an in-memory channel and
synthetic prices, the REST client and the counts-only CLI. No network, no market data, no real channel."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from apex_worker.market.calendar import default_calendar
from apex_worker.personal import book as B
from apex_worker.personal.commands import CommandError, parse
from apex_worker.personal.discord_rest import DiscordError, DiscordREST, MemoryChannel
from apex_worker.personal.records import MARK, decode_all, encode
from apex_worker.personal.runner import Config, MarketUnavailable, Runner, _chunks, month_end
from apex_worker.personal.smoke import SyntheticMarket, rehearse, synthetic_bars
from apex_worker.timeutil import ET

CAL = default_calendar()


def utc(*a) -> datetime:
    return datetime(*a, tzinfo=UTC)


def et(*a) -> datetime:
    return datetime(*a, tzinfo=ET).astimezone(UTC)


SENT = et(2026, 10, 30, 11, 0)


# ---- commands -------------------------------------------------------------------------------------------------------
def test_trades_cash_and_account_commands_parse():
    c = parse("bought 2 XLK at 245.10", sent_at=SENT)
    assert (c.verb, c.symbol, c.shares, c.price, c.at) == ("buy", "XLK", 2, Decimal("245.10"), None)
    c = parse("Sold 3 xle @ 90.2 on 10/30 9:45", sent_at=SENT)
    assert (c.verb, c.symbol, c.shares, c.price, c.at) == ("sell", "XLE", 3, Decimal("90.2"), et(2026, 10, 30, 9, 45))
    assert parse("buy XLK 2 245", sent_at=SENT).shares == 2 and parse("buy XLK 2 245", sent_at=SENT).price == 245
    c = parse("bought XLK 245.10 2", sent_at=SENT)  # the integer is the share count
    assert (c.shares, c.price) == (2, Decimal("245.10"))
    c = parse("add 4 shares of XLF at $52.31 yesterday 3:55 pm", sent_at=SENT)
    assert (c.verb, c.shares, c.price, c.at) == ("buy", 4, Decimal("52.31"), et(2026, 10, 29, 15, 55))
    assert parse("trim 1 XLU at 80 at 10am", sent_at=SENT).at == et(2026, 10, 30, 10, 0)
    assert parse("start", sent_at=SENT).amount == 1000 and parse("start $1,500", sent_at=SENT).amount == 1500
    assert parse("deposit 500 on 2026-10-29", sent_at=SENT).at == et(2026, 10, 29, 12, 0)
    assert parse("deposit 500 today", sent_at=et(2026, 10, 30, 8, 0)).at == et(2026, 10, 30, 8, 0)  # never later
    c = parse("dividend XLU 3.12", sent_at=SENT)
    assert (c.verb, c.symbol, c.amount) == ("dividend", "XLU", Decimal("3.12"))
    assert parse("split XLK 2:1", sent_at=SENT).ratio == (2, 1) and parse("split XLK 2", sent_at=SENT).ratio == (2, 1)
    assert parse("split XLK 1:4", sent_at=SENT).ratio == (1, 4) and parse("split XLK 3-for-2", sent_at=SENT).ratio == (3, 2)
    assert parse("skip all", sent_at=SENT).symbol is None and parse("skip XLK", sent_at=SENT).symbol == "XLK"
    for text, verb in (("undo", "undo"), ("status", "status"), ("P/L", "status"), ("plan", "plan"),
                       ("trades", "trades"), ("help", "help")):
        assert parse(text, sent_at=SENT).verb == verb
    assert parse("bought 1 XLK at 100 on 12/31", sent_at=SENT).at.year == 2025  # a later month-day is last year's


@pytest.mark.parametrize("text, code", [
    ("bought 2.5 XLK at 245", "WHOLE"), ("bought 2 XLK for 490", "FOR"), ("bought 2 XLK XLF at 245", "SYMBOL"),
    ("hello there", "UNKNOWN"), ("bought XLK", "TRADE"), ("bought 1 XLK at 245 at 3:55pm", "FUTURE"),
    ("bought 1 XLK at 245 on 2/30", "DATE"), ("sold 1 XLK at 0", "NUMBER"), ("deposit", "AMOUNT"),
    ("bought 1 XLK at 245.123456", "NUMBER"), ("bought 1 XLK at 245 #!", "TOKEN"), ("", "EMPTY"),
    ("start 1000 on 2010-01-04", "DATE"),
])
def test_commands_it_cannot_read_are_rejected_with_a_reason(text, code):
    with pytest.raises(CommandError) as err:
        parse(text, sent_at=SENT)
    assert err.value.code == code and str(err.value)


def test_records_round_trip_and_only_the_marked_lines_count():
    rec = {"k": "buy", "sym": "XLK", "n": 2, "px": "245.1", "at": "2026-10-30T13:45:00Z", "ref": "55"}
    text = f"✅ Recorded\nsome line\n{encode(rec)}\n{MARK}{{not json\n{MARK}[1, 2]"
    assert decode_all(text) == [rec] and decode_all("") == []


# ---- the book -------------------------------------------------------------------------------------------------------
def R(i: int, **data) -> B.Record:
    return B.Record(str(i), data)


def at(day: int, hour: int = 11) -> str:
    return B.iso(et(2026, 11, day, hour, 0))


def test_cash_fifo_realized_pnl_and_the_benchmark_units():
    recs = [R(1, k="start", amt="1000", spy="500", at=at(2)), R(2, k="buy", sym="XLF", n=2, px="100", at=at(3)),
            R(3, k="buy", sym="XLF", n=1, px="130", at=at(4)), R(4, k="sell", sym="XLF", n=2, px="150", at=at(5)),
            R(5, k="deposit", amt="500", spy="550", at=at(6)), R(6, k="withdraw", amt="100", spy="600", at=at(9)),
            R(7, k="dividend", sym="XLF", amt="1.50", at=at(10))]
    b = B.build(recs)
    assert b.cash == Decimal("1000") - 200 - 130 + 300 + 500 - 100 + Decimal("1.50")
    h = b.holdings["XLF"]
    assert (h.shares, h.avg_cost, h.realized, h.dividends) == (1, Decimal(130), Decimal(100), Decimal("1.50"))
    assert b.net_in == 1400 and b.realized == 100 and b.started_at == et(2026, 11, 2, 11)
    assert b.spy_units() == Decimal(1000) / 500 + Decimal(500) / 550 - Decimal(100) / 600
    divs = [(date(2026, 11, 6), Decimal("2")), (date(2026, 11, 20), Decimal("1"))]
    # the 11/6 ex-date pays on the units bought before that day (the start only); 11/20 on all of them
    assert B.spy_dividend_cash(b, divs) == 2 * 2 + 1 * b.spy_units()


def test_records_that_would_not_add_up_are_refused():
    start = R(1, k="start", amt="1000", spy="500", at=at(2))
    with pytest.raises(B.BookError) as err:
        B.build([start, R(2, k="sell", sym="XLF", n=1, px="100", at=at(3))])
    assert err.value.code == "OVERSELL"
    with pytest.raises(B.BookError) as err:
        B.build([start, R(2, k="buy", sym="XLF", n=1, px="100", at=at(1))])  # dated before the start
    assert err.value.code == "NOT_STARTED"
    with pytest.raises(B.BookError) as err:
        B.build([start, R(2, k="start", amt="5", spy="500", at=at(3))])
    assert err.value.code == "STARTED"
    with pytest.raises(B.BookError) as err:
        B.build([start, R(2, k="buy", sym="XLF", n=3, px="100", at=at(3)),
                 R(3, k="split", sym="XLF", new=1, old=2, at=at(4))])
    assert err.value.code == "SPLIT_FRACTION"


def test_a_backdated_trade_applies_in_time_order_and_voids_and_splits_rebuild():
    recs = [R(1, k="start", amt="1000", spy="500", at=at(2)), R(2, k="sell", sym="XLF", n=1, px="120", at=at(6)),
            R(3, k="buy", sym="XLF", n=2, px="100", at=at(3))]  # reported after the sell, dated before it
    with pytest.raises(B.BookError):
        B.build(recs[:2])
    b = B.build(recs)
    assert b.holdings["XLF"].shares == 1 and b.holdings["XLF"].realized == 20
    b = B.build([*recs, R(4, k="split", sym="XLF", new=2, old=1, at=at(9))])
    assert b.holdings["XLF"].shares == 2 and b.holdings["XLF"].avg_cost == 50
    voided = B.build([*recs, R(4, k="buy", sym="XLK", n=1, px="200", at=at(9)), R(5, k="void", target="4")])
    assert "XLK" not in voided.held() and voided.cash == b.cash


def test_undo_finds_the_latest_owner_record_made_before_it():
    recs = [R(10, k="start", amt="1000", spy="500", at=at(2), ref="1"),
            R(12, k="alert", day="2026-11-02", orders=[]),
            R(13, k="buy", sym="XLF", n=1, px="100", at=at(3), ref="5")]
    b = B.build(recs)
    assert b.last_undoable((6, 0)).msg_id == "13"  # the buy answered message 5 (< 6), though written later
    assert b.last_undoable((5, 0)).msg_id == "10" and b.last_undoable((1, 0)) is None
    two = B.build([*recs, R(14, k="buy", sym="XLF", n=1, px="101", at=at(3), ref="7", line=1)])
    assert two.last_undoable((7, 2)).msg_id == "14" and two.last_undoable((7, 1)).msg_id == "13"


def test_pending_orders_count_reports_after_the_decision_close_and_skips():
    close = CAL.session(date(2026, 10, 30)).close_utc
    alert = R(20, k="alert", day="2026-10-30", orders=[{"a": "EXIT", "s": "XLE", "n": 3, "px": "90"},
                                                       {"a": "BUY", "s": "XLK", "n": 1, "px": "245"},
                                                       {"a": "ADD", "s": "XLF", "n": 4, "px": "52"}])
    recs = [R(1, k="start", amt="1000", spy="500", at=B.iso(et(2026, 10, 1, 11))),
            R(2, k="buy", sym="XLE", n=3, px="88", at=B.iso(et(2026, 10, 2, 11))),
            R(3, k="buy", sym="XLF", n=2, px="50", at=B.iso(et(2026, 10, 30, 11))),  # before the close: not a fill
            alert, R(21, k="sell", sym="XLE", n=3, px="90.5", at=B.iso(et(2026, 11, 2, 9, 40))),
            R(22, k="buy", sym="XLF", n=1, px="52", at=B.iso(et(2026, 11, 2, 9, 41)))]
    left = B.pending(B.build(recs), alert, close)
    assert [(o["s"], o["left"]) for o in left] == [("XLK", 1), ("XLF", 3)]
    skipped = B.pending(B.build([*recs, R(23, k="skip", sym="XLK", alert="2026-10-30")]), alert, close)
    assert [o["s"] for o in skipped] == ["XLF"]
    assert B.pending(B.build([*recs, R(23, k="skip", sym=None, alert="2026-10-30")]), alert, close) == []


# ---- the runner -----------------------------------------------------------------------------------------------------
def cheap_bars() -> dict:
    """Synthetic bars scaled so every ETF costs about $100 in late October 2026 (well within a $333 slot)."""
    bars = synthetic_bars(CAL, date(2025, 6, 2), date(2027, 6, 30))
    return {s: df * (100.0 / float(df.loc["2026-10-23", "close"])) for s, df in bars.items()}


@pytest.fixture
def world():
    ch = MemoryChannel(start=et(2026, 10, 27, 14, 0))
    mk = SyntheticMarket(CAL, lambda: ch.clock, bars=cheap_bars())
    return ch, mk


def run(ch, mk, when, **cfg):
    ch.clock = when
    return Runner(Config("chan", **cfg), ch, mk, now=when, cal=CAL).run()


def records(ch, kind=None) -> list[dict]:
    out = [r for m in ch.messages if m["author"]["id"] == ch.bot_id for r in decode_all(m["content"])]
    return [r for r in out if kind is None or r["k"] == kind]


def replies_to(ch, msg) -> list[str]:
    return [m["content"] for m in ch.messages
            if m["author"]["id"] == ch.bot_id and (m.get("message_reference") or {}).get("message_id") == msg["id"]]


def fill_all(ch, mk, when):
    """The owner reports every order of the latest alert at that session's open."""
    alert = records(ch, "alert")[-1]
    d = when.astimezone(ET).date()
    for i, o in enumerate(alert["orders"]):
        px = round(float(mk.bars[o["s"]].loc[str(d), "open"]), 2)
        verb = "sold" if o["a"] in ("EXIT", "TRIM") else "bought"
        ch.post_as_owner(f"{verb} {o['n']} {o['s']} at {px}", when + timedelta(minutes=i))


def test_first_contact_welcome_once_then_start_brings_the_starting_allocation(world):
    ch, mk = world
    rep = run(ch, mk, et(2026, 10, 27, 14, 0))
    assert rep.welcome and [r["k"] for r in records(ch)] == ["hello"] and not mk.requests  # no market data needed
    assert run(ch, mk, et(2026, 10, 27, 14, 30)).posted == 0
    msg = ch.post_as_owner("start 1000", et(2026, 10, 27, 14, 35))
    rep = run(ch, mk, et(2026, 10, 27, 14, 37))
    assert rep.answered == 1 and rep.alert and not rep.summary  # the account started after Monday's close
    (start,) = records(ch, "start")
    assert start["amt"] == "1000" and start["ref"] == msg["id"] and float(start["spy"]) > 0
    (alert,) = records(ch, "alert")
    assert alert["day"] == "2026-10-26" and alert["m"] == "sector-mom-3" and len(alert["sel"]) == 3
    assert all(o["a"] == "BUY" and o["n"] >= 1 for o in alert["orders"]) and len(alert["orders"]) == 3
    assert sum(o["n"] * float(o["px"]) for o in alert["orders"]) <= 1000 and float(alert["cash"]) >= 0
    text = ch.bot_texts()[-1]
    assert "Starting allocation" in text and "Trade today" in text and "UNVALIDATED" in text
    assert run(ch, mk, et(2026, 10, 27, 15, 7)).posted == 0 and ch.duplicates == 0  # idempotent


def test_reports_are_recorded_once_and_a_summary_follows_each_close(world):
    ch, mk = world
    ch.post_as_owner("start 1000", et(2026, 10, 27, 14, 35))
    run(ch, mk, et(2026, 10, 27, 14, 37))
    fill_all(ch, mk, et(2026, 10, 27, 15, 0))
    rep = run(ch, mk, et(2026, 10, 27, 15, 37))
    assert rep.answered == 3 and len(records(ch, "buy")) == 3 and not rep.summary
    assert all(t.startswith("✅ Recorded: bought") for t in ch.bot_texts()[-3:])
    assert run(ch, mk, et(2026, 10, 27, 16, 7)).posted == 0  # 4:07 PM: Tuesday's bars are not final yet
    rep = run(ch, mk, et(2026, 10, 27, 18, 7))
    assert rep.summary and records(ch, "summary")[-1]["day"] == "2026-10-27"
    summary = ch.bot_texts()[-1]
    assert "Tue 10/27 close" in summary and "SPY with the same cash" in summary and summary.count("- **XL") == 3
    assert run(ch, mk, et(2026, 10, 27, 20, 37)).posted == 0  # once per session
    assert len(records(ch, "buy")) == 3 and ch.duplicates == 0


def test_month_end_decision_once_reminder_next_morning_and_late_catch_up(world):
    ch, mk = world
    ch.post_as_owner("start 1000", et(2026, 10, 27, 14, 35))
    run(ch, mk, et(2026, 10, 27, 14, 37))
    fill_all(ch, mk, et(2026, 10, 27, 15, 0))
    run(ch, mk, et(2026, 10, 27, 15, 37))
    for d in (28, 29):
        assert not run(ch, mk, et(2026, 10, d, 18, 7)).alert  # mid-month: summaries only
    ch.post_as_owner("deposit 600", et(2026, 10, 30, 12, 0))  # cash the month-end check puts to work
    rep = run(ch, mk, et(2026, 10, 30, 18, 7))
    assert rep.summary and rep.alert
    alert = records(ch, "alert")[-1]
    assert alert["day"] == "2026-10-30" and any(o["a"] in ("BUY", "ADD") for o in alert["orders"])
    assert ch.bot_texts()[-2].startswith("📊") and "Month-end" in ch.bot_texts()[-1]
    assert not run(ch, mk, et(2026, 10, 30, 21, 17)).alert
    rep = run(ch, mk, et(2026, 11, 2, 7, 37))  # Monday before the open
    assert rep.reminder and ch.bot_texts()[-1].startswith("⏰ Mon 11/2")
    assert not run(ch, mk, et(2026, 11, 2, 8, 7)).reminder  # once
    # a month-end missed entirely (no run that evening) is caught up the next time, marked late
    ch2 = MemoryChannel(start=et(2026, 11, 20, 12, 0))
    mk2 = SyntheticMarket(CAL, lambda: ch2.clock, bars=cheap_bars())
    ch2.post_as_owner("start 1000", et(2026, 11, 20, 12, 0))
    run(ch2, mk2, et(2026, 11, 20, 12, 7))
    rep = run(ch2, mk2, et(2026, 12, 1, 11, 7))  # Tue 12/1, during the session; nothing ran since 11/20
    assert rep.alert and records(ch2, "alert")[-1]["day"] == "2026-11-30" and "Posted late" in ch2.bot_texts()[-1]


def test_one_message_can_report_every_fill_one_line_each(world):
    ch, mk = world
    ch.post_as_owner("start 1000", et(2026, 10, 27, 14, 35))
    run(ch, mk, et(2026, 10, 27, 14, 37))
    batch = ch.post_as_owner("bought 1 XLF at 60\n\nbought 2 XLE at 70\nundo\nsold 9 XLU at 50",
                             et(2026, 10, 27, 15, 0))
    rep = run(ch, mk, et(2026, 10, 27, 15, 7))
    assert rep.answered == 4 and len(replies_to(ch, batch)) == 4
    buys = records(ch, "buy")
    assert [(b["sym"], b.get("line", 0)) for b in buys] == [("XLF", 0), ("XLE", 1)]
    (void,) = records(ch, "void")  # the undo on line 2 removes line 1's buy, the latest before it
    xle = next(m for m in ch.messages if any(r.get("sym") == "XLE" for r in decode_all(m["content"])))
    assert void["target"] == xle["id"] and replies_to(ch, batch)[-1].startswith("❌ Not recorded")
    assert run(ch, mk, et(2026, 10, 27, 15, 37)).answered == 0 and ch.duplicates == 0


def test_wrong_reports_are_answered_and_never_recorded(world):
    ch, mk = world
    early = ch.post_as_owner("bought 1 XLK at 100", et(2026, 10, 27, 14, 0))
    run(ch, mk, et(2026, 10, 27, 14, 7))
    assert replies_to(ch, early)[0].startswith("❌ Not recorded") and not records(ch, "buy")
    ch.post_as_owner("start 1000", et(2026, 10, 27, 14, 10))
    oversell = ch.post_as_owner("sold 5 XLF at 50", et(2026, 10, 27, 14, 11))
    nonsense = ch.post_as_owner("what should I buy?", et(2026, 10, 27, 14, 12))
    fraction = ch.post_as_owner("bought 1.5 XLF at 50", et(2026, 10, 27, 14, 13))
    run(ch, mk, et(2026, 10, 27, 14, 37))
    assert "only" in replies_to(ch, oversell)[0] or "no XLF shares" in replies_to(ch, oversell)[0]
    assert replies_to(ch, nonsense)[0].startswith("❓") and "whole shares" in replies_to(ch, fraction)[0]
    assert not records(ch, "sell") and {r.get("err") for r in records(ch, "ack")} >= {"OVERSELL", "UNKNOWN", "WHOLE"}
    assert run(ch, mk, et(2026, 10, 27, 15, 7)).answered == 0  # each message is answered once


def test_undo_and_skip_act_on_what_came_before_them(world):
    ch, mk = world
    ch.post_as_owner("start 1000", et(2026, 10, 27, 14, 35))
    run(ch, mk, et(2026, 10, 27, 14, 37))
    ch.post_as_owner("bought 1 XLF at 50", et(2026, 10, 27, 15, 0))
    ch.post_as_owner("undo", et(2026, 10, 27, 15, 1))  # in the same batch as the buy it removes
    run(ch, mk, et(2026, 10, 27, 15, 7))
    (void,) = records(ch, "void")
    buy_msg = next(m for m in ch.messages if any(r["k"] == "buy" for r in decode_all(m["content"])))
    assert void["target"] == buy_msg["id"] and "↩️ Removed: bought 1 XLF" in ch.bot_texts()[-1]
    ch.post_as_owner("status", et(2026, 10, 27, 15, 10))
    run(ch, mk, et(2026, 10, 27, 15, 37))
    assert "XLF" not in ch.bot_texts()[-1].split("SPY with")[1].split("Cash")[0]
    first = records(ch, "alert")[0]["orders"][0]["s"]
    ch.post_as_owner(f"skip {first}", et(2026, 10, 27, 15, 40))
    ch.post_as_owner("skip XYZ", et(2026, 10, 27, 15, 41))
    run(ch, mk, et(2026, 10, 27, 16, 7))
    assert records(ch, "skip")[-1]["sym"] == first and "No open XYZ order" in ch.bot_texts()[-1]
    ch.post_as_owner("undo", et(2026, 10, 27, 16, 10))  # removes the skip, the latest owner record
    run(ch, mk, et(2026, 10, 27, 16, 12))
    assert records(ch, "void")[-1]["target"] != void["target"] and "skip" in ch.bot_texts()[-1]


def test_without_message_content_it_says_so_once_a_day_and_answers_later(world):
    ch, mk = world
    run(ch, mk, et(2026, 10, 27, 14, 0))
    ch.blank_content = True
    ch.post_as_owner("start 1000", et(2026, 10, 27, 14, 5))
    rep = run(ch, mk, et(2026, 10, 27, 14, 7))
    assert rep.notices == 1 and rep.answered == 0 and "Message Content Intent" in ch.bot_texts()[-1]
    assert run(ch, mk, et(2026, 10, 27, 14, 37)).posted == 0  # once a day
    ch.blank_content = False
    rep = run(ch, mk, et(2026, 10, 27, 15, 7))
    assert rep.answered == 1 and records(ch, "start")


def test_only_the_allowed_people_count_and_a_mention_is_stripped(world):
    ch, mk = world
    run(ch, mk, et(2026, 10, 27, 14, 0))
    stranger = ch.post_as_owner("start 1000", et(2026, 10, 27, 14, 5), author_id="777")
    owner = ch.post_as_owner(f"<@{ch.bot_id}> start 1000", et(2026, 10, 27, 14, 6))
    run(ch, mk, et(2026, 10, 27, 14, 7), allowed_users=frozenset({ch.owner_id}))
    (start,) = records(ch, "start")
    assert start["ref"] == owner["id"] and not replies_to(ch, stranger)  # the stranger is ignored, not answered


def test_dry_mode_reads_and_computes_but_posts_nothing(world):
    ch, mk = world
    ch.post_as_owner("start 1000", et(2026, 10, 27, 14, 5))
    before = len(ch.messages)
    rep = run(ch, mk, et(2026, 10, 27, 14, 7), live=False)
    assert rep.dry and rep.welcome and rep.answered == 1 and rep.alert and rep.posted >= 3
    assert len(ch.messages) == before


def test_incomplete_market_data_never_produces_a_decision(world):
    ch, mk = world
    for s in ("XLK", "XLF", "XLE"):
        mk.bars[s] = mk.bars[s][mk.bars[s].index < "2026-09-01"]  # three ETFs stopped updating
    ch.post_as_owner("start 1000", et(2026, 10, 27, 14, 5))
    rep = run(ch, mk, et(2026, 10, 27, 14, 7))
    assert not rep.alert and rep.waiting_for_bars and not records(ch, "alert")


def test_a_deleted_confirmation_is_recorded_again_exactly_once(world):
    ch, mk = world
    ch.post_as_owner("start 1000", et(2026, 10, 27, 14, 5))
    run(ch, mk, et(2026, 10, 27, 14, 7))
    buy = ch.post_as_owner("bought 2 XLF at 60", et(2026, 10, 27, 15, 0))
    run(ch, mk, et(2026, 10, 27, 15, 7))
    ch.messages = [m for m in ch.messages if (m.get("message_reference") or {}).get("message_id") != buy["id"]]
    run(ch, mk, et(2026, 10, 27, 15, 37))
    (rec,) = records(ch, "buy")
    assert rec["ref"] == buy["id"] and rec["at"] == B.iso(et(2026, 10, 27, 15, 0))


def test_price_typo_unknown_symbol_and_negative_cash_are_flagged(world):
    ch, mk = world
    ch.post_as_owner("start 100", et(2026, 10, 27, 14, 5))
    run(ch, mk, et(2026, 10, 27, 14, 7))
    typo = ch.post_as_owner("bought 1 XLF at 1.20", et(2026, 10, 27, 15, 0))
    other = ch.post_as_owner("bought 3 QQQ at 500", et(2026, 10, 27, 15, 1))
    run(ch, mk, et(2026, 10, 27, 15, 7))
    assert "far from XLF" in replies_to(ch, typo)[0]
    reply = replies_to(ch, other)[0]
    assert "isn't one of the method's ETFs" in reply and "Cash is now -$" in reply and len(records(ch, "buy")) == 2


def test_status_plan_and_trades_replies(world):
    ch, mk = world
    ch.post_as_owner("status", et(2026, 10, 27, 14, 0))
    run(ch, mk, et(2026, 10, 27, 14, 2))
    assert "hasn't started" in ch.bot_texts()[-1]
    ch.post_as_owner("start 1000", et(2026, 10, 27, 14, 5))
    run(ch, mk, et(2026, 10, 27, 14, 7))
    fill_all(ch, mk, et(2026, 10, 27, 15, 0))
    asks = [ch.post_as_owner(text, et(2026, 10, 28, 11, 0)) for text in ("status", "plan", "trades", "help")]
    run(ch, mk, et(2026, 10, 28, 11, 7))
    status, plan, trades, helptext = (replies_to(ch, m)[0] for m in asks)
    assert "📒 **Status**" in status and "IEX last trades" in status and "Next check: after the close of Fri 10/30" in status
    assert plan.startswith("🧭 If the month ended on the close of Tue 10/27") and "Selection" in plan
    assert trades.startswith("🧾 Your records (4 of 4") and "`start 1000`" in helptext


def test_market_outage_posts_one_private_note_and_fails_the_run(world):
    ch, mk = world
    ch.post_as_owner("start 1000", et(2026, 10, 27, 14, 5))

    def down(req):
        raise ConnectionError("provider down")

    with pytest.raises(MarketUnavailable) as err:
        Runner(Config("chan"), ch, down, now=et(2026, 10, 27, 14, 7), cal=CAL).run()
    assert str(err.value) == "ConnectionError"  # the type only: "provider down" never leaves
    with pytest.raises(MarketUnavailable):
        Runner(Config("chan"), ch, down, now=et(2026, 10, 27, 14, 37), cal=CAL).run()
    assert [r["what"] for r in records(ch, "note")] == ["market-down"] and not records(ch, "start")
    run(ch, mk, et(2026, 10, 27, 15, 7))
    assert records(ch, "start")  # answered once the data is back


def test_long_messages_are_split_with_the_record_last():
    text = "\n".join(f"line {i} " + "x" * 90 for i in range(60)) + "\n" + encode({"k": "summary", "day": "d"})
    parts = _chunks(text)
    assert len(parts) > 1 and all(len(p) <= 2000 for p in parts) and decode_all(parts[-1])
    assert month_end(CAL, date(2026, 10, 5)) == date(2026, 10, 30) and month_end(CAL, date(2026, 12, 2)) == date(2026, 12, 31)


def test_the_offline_rehearsal_runs_and_says_it_is_synthetic():
    lines = rehearse()
    assert lines[0].startswith("SYNTHETIC REHEARSAL") and any("Starting allocation" in ln for ln in lines)
    assert not any(MARK in ln for ln in lines)


# ---- the REST client ------------------------------------------------------------------------------------------------
def test_rest_client_pages_history_backwards_retries_429_and_sends_safely():
    calls = []
    pages = {None: [{"id": str(i), "author": {"id": "1"}} for i in range(250, 150, -1)],
             "151": [{"id": str(i), "author": {"id": "1"}} for i in range(150, 100, -1)]}
    state = {"limited": False}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["Authorization"] == "Bot T0KEN" and request.headers["User-Agent"].startswith("DiscordBot")
        if request.method == "POST":
            if not state["limited"]:
                state["limited"] = True
                return httpx.Response(429, json={"retry_after": 0.01, "global": False})
            return httpx.Response(200, json={"id": "999", **json.loads(request.content)})
        if request.url.path.endswith("/users/@me"):
            return httpx.Response(200, json={"id": "900"})
        return httpx.Response(200, json=pages[request.url.params.get("before")])

    slept = []
    rest = DiscordREST("T0KEN", client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=slept.append)
    assert rest.me()["id"] == "900"
    hist = rest.history("42")
    assert [m["id"] for m in hist] == [str(i) for i in range(101, 251)]  # oldest first, every page
    sent = rest.send("42", "hi", reply_to="7", nonce="abc")
    body = json.loads(calls[-1].content)
    assert sent["id"] == "999" and body["allowed_mentions"] == {"parse": []} and body["enforce_nonce"] is True
    assert body["message_reference"] == {"message_id": "7", "fail_if_not_exists": False} and slept


def test_rest_errors_carry_status_and_code_only():
    def handler(request):
        return httpx.Response(403, json={"message": "Missing Access secret-ish text", "code": 50001})

    rest = DiscordREST("T", client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    with pytest.raises(DiscordError) as err:
        rest.history("42")
    assert (err.value.status, err.value.code) == (403, 50001) and "Missing" not in str(err.value)
    with pytest.raises(ValueError):
        DiscordREST("")


# ---- the CLI --------------------------------------------------------------------------------------------------------
def test_run_prints_counts_only_and_stops_without_secrets(monkeypatch, capsys):
    from apex_worker.cli import main
    from apex_worker.personal import discord_rest, market

    for k in ("DISCORD_BOT_TOKEN", "APEX_PERSONAL_CHANNEL_ID", "ALPACA_KEY_ID", "ALPACA_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert main(["personal", "run", "--mode", "live"]) == 2
    err = capsys.readouterr().err
    assert "DISCORD_BOT_TOKEN" in err and "ALPACA_SECRET_KEY" in err

    ch = MemoryChannel(start=et(2026, 10, 27, 14, 0))
    ch.post_as_owner("start 1000", et(2026, 10, 27, 14, 5))
    ch.post_as_owner("bought 2 XLF at 61.25", et(2026, 10, 27, 14, 6))
    mk = SyntheticMarket(CAL, lambda: ch.clock, bars=cheap_bars())
    ch.requests = 0
    monkeypatch.setattr(discord_rest, "DiscordREST", lambda token: _Closable(ch))
    monkeypatch.setattr(market, "alpaca_source", lambda k, s: mk)
    for k, v in (("DISCORD_BOT_TOKEN", "t"), ("APEX_PERSONAL_CHANNEL_ID", "c"), ("ALPACA_KEY_ID", "k"),
                 ("ALPACA_SECRET_KEY", "s")):
        monkeypatch.setenv(k, v)
    import apex_worker.personal.runner as runner_mod

    monkeypatch.setattr(runner_mod, "datetime", _Frozen)
    import apex_worker.personal.cli as cli_mod

    monkeypatch.setattr(cli_mod, "datetime", _Frozen)
    assert main(["personal", "run", "--mode", "live"]) == 0
    out = capsys.readouterr()
    counts = json.loads(out.out)
    assert counts["answered"] == 2 and counts["alert"] and out.err == ""
    for leak in ("XLF", "61.25", "1000", "bought", "$"):
        assert leak not in out.out


class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return et(2026, 10, 27, 14, 7)


class _Closable:
    def __init__(self, ch):
        self.ch, self.requests = ch, 0

    def __getattr__(self, name):
        return getattr(self.ch, name)

    def close(self):
        pass


def test_a_failed_run_prints_the_type_and_status_never_a_message(monkeypatch, capsys):
    from apex_worker.cli import main
    from apex_worker.personal import discord_rest, market

    class Refused:
        requests = 0

        def me(self):
            raise DiscordError(403, 50001)

        def close(self):
            pass

    class Boom(Refused):
        def me(self):
            raise RuntimeError("bought 2 XLF at 61.25")  # an exception message must never be printed

    for k, v in (("DISCORD_BOT_TOKEN", "t"), ("APEX_PERSONAL_CHANNEL_ID", "c"), ("ALPACA_KEY_ID", "k"),
                 ("ALPACA_SECRET_KEY", "s")):
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(market, "alpaca_source", lambda k, s: None)
    monkeypatch.setattr(discord_rest, "DiscordREST", lambda token: Refused())
    assert main(["personal", "run", "--mode", "live"]) == 1
    assert capsys.readouterr().err.strip() == "personal run failed: DiscordError: Discord HTTP 403 (code 50001)"
    monkeypatch.setattr(discord_rest, "DiscordREST", lambda token: Boom())
    assert main(["personal", "run", "--mode", "live"]) == 1
    assert capsys.readouterr().err.strip() == "personal run failed: RuntimeError"
