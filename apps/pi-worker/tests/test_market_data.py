import asyncio
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pandas as pd
import pytest

from apex_worker.demo.synthetic import generate
from apex_worker.market.adapters.alpaca import AlpacaAdapter, AlpacaError
from apex_worker.market.adapters.replay import Faults, ProviderRateLimited, ReplayAdapter
from apex_worker.market.calendar import MarketPhase, default_calendar
from apex_worker.market.priority import SlotRequest, plan_slots
from apex_worker.market.quote_cache import QuoteCache
from apex_worker.market.ratelimit import BudgetExceeded, Priority, QuotaTracker, TokenBucket
from apex_worker.market.stream import IexStream, StreamAuthError
from apex_worker.market.universe import UniverseOverrides, evaluate_universe
from apex_worker.market.validation import price_decision_class, validate_price
from apex_worker.timeutil import ET, ReplayClock

NOW = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)


# ---------------- calendar (dates with known actual status) ----------------
@pytest.mark.parametrize(
    "day,is_session",
    [
        ("2026-04-03", False),  # Good Friday
        ("2026-01-19", False),  # MLK Day
        ("2026-06-19", False),  # Juneteenth
        ("2026-11-26", False),  # Thanksgiving
        ("2026-12-25", False),  # Christmas
        ("2027-12-24", False),  # Christmas (Sat) observed Friday
        ("2026-07-03", False),  # Independence Day (Sat) observed Friday
        ("2026-11-27", True),  # day after Thanksgiving: half day
        ("2026-10-20", True),
    ],
)
def test_calendar_known_holidays(day, is_session):
    assert default_calendar().is_session(date.fromisoformat(day)) is is_session


def test_half_day_close_and_after_hours():
    cal = default_calendar()
    s = cal.session(date(2026, 11, 27))
    assert s.early_close and s.close_utc.astimezone(ET).hour == 13
    assert cal.phase(datetime(2026, 11, 27, 18, 30, tzinfo=UTC)).phase is MarketPhase.AFTER
    assert (
        cal.phase(datetime(2026, 11, 27, 22, 30, tzinfo=UTC)).phase is MarketPhase.CLOSED
    )  # after-hours ends 17:00 ET


def test_dst_open_times():
    cal = default_calendar()
    assert cal.session(date(2026, 3, 6)).open_utc.hour == 14  # EST: 09:30 = 14:30Z
    assert cal.session(date(2026, 3, 9)).open_utc.hour == 13  # EDT after spring-forward: 13:30Z
    assert cal.session(date(2026, 11, 2)).open_utc.hour == 14


def test_horizon_counts_sessions_not_days():
    cal = default_calendar()
    # 2026-11-25 + 1 session skips Thanksgiving
    assert cal.add_sessions(date(2026, 11, 25), 1) == date(2026, 11, 27)
    assert cal.count_sessions_after(date(2026, 11, 20), date(2026, 11, 30)) == 5


def test_weekend_is_closed_with_next_open():
    info = default_calendar().phase(datetime(2026, 10, 24, 16, 0, tzinfo=UTC))
    assert info.phase is MarketPhase.CLOSED and info.next_open_utc.date() == date(2026, 10, 26)


# ---------------- validation / cache ----------------
def _v(**kw):
    base = dict(
        price=100.0,
        bid=99.99,
        ask=100.01,
        ts_exchange=NOW,
        ts_received=NOW,
        now=NOW,
        last_accepted_price=99.5,
        last_accepted_ts=NOW - timedelta(seconds=30),
        reference_price=99.0,
    )
    base.update(kw)
    return validate_price(**base)


def test_validation_levels():
    assert _v().level == 2
    assert _v(consolidated_reference=100.2).level == 3
    assert _v(price=-1).level == 0
    assert _v(ts_exchange=NOW + timedelta(minutes=5)).level == 0
    assert _v(ts_exchange=NOW - timedelta(minutes=5)).quarantined  # out of order vs last accepted
    assert "CROSSED_QUOTE" in _v(bid=101, ask=100).flags and _v(bid=101, ask=100).quarantined
    assert _v(bid=-1, ask=100).quarantined
    assert "OUTLIER_VS_REFERENCE" in _v(price=150).flags
    assert "DIVERGES_FROM_CONSOLIDATED" in _v(consolidated_reference=104).flags
    assert _v(halted=True).quarantined


def test_decision_class():
    assert price_decision_class(2, 30, 120, True) == "FRESH_VERIFIED"
    assert price_decision_class(1, 30, 120, True) == "FRESH_UNVERIFIED"
    assert price_decision_class(2, 500, 120, True) == "STALE"
    assert price_decision_class(2, None, 120, True) == "UNAVAILABLE"


def test_cache_never_lets_single_bad_print_through():
    cache = QuoteCache(reference_prices={"DMSEMI1": 100.0})
    ok = cache.ingest(
        symbol="DMSEMI1",
        price=100.5,
        bid=None,
        ask=None,
        provider="replay",
        feed="demo",
        latency_class="replay",
        ts_exchange=NOW,
        ts_received=NOW,
        now=NOW,
    )
    assert ok is not None
    bad = cache.ingest(
        symbol="DMSEMI1",
        price=30.0,
        bid=None,
        ask=None,
        provider="replay",
        feed="demo",
        latency_class="replay",
        ts_exchange=NOW + timedelta(seconds=1),
        ts_received=NOW,
        now=NOW + timedelta(seconds=2),
    )
    assert bad is None and cache.get("DMSEMI1").price == 100.5 and cache.quarantined()
    assert cache.stale_symbols(NOW + timedelta(minutes=10), 120) == ["DMSEMI1"]


# ---------------- quotas ----------------
def test_quota_thresholds_pause_optional_then_normal():
    q = QuotaTracker("alpaca", daily_limit=100)
    q.record("2026-10-20", 70)
    assert q.level() == "WARN" and q.allow(Priority.OPTIONAL)
    q.record("2026-10-20", 15)
    assert q.level() == "RESTRICTED" and not q.allow(Priority.OPTIONAL)
    with pytest.raises(BudgetExceeded):
        q.record("2026-10-20", 1, Priority.OPTIONAL)
    q.record("2026-10-20", 10, Priority.NORMAL)
    assert q.level() == "CRITICAL" and q.allow(Priority.ESSENTIAL) and not q.allow(Priority.NORMAL)
    q.roll("2026-10-21")
    assert q.level() == "OK"


def test_token_bucket_and_penalty():
    t = [0.0]
    b = TokenBucket(rate_per_sec=2, capacity=2, clock=lambda: t[0])
    assert b.try_acquire() == 0 and b.try_acquire() == 0
    assert b.try_acquire() == pytest.approx(0.5)
    b.penalize(10)
    t[0] = 5
    assert b.try_acquire() > 0


# ---------------- Alpaca adapter on a mock transport ----------------
def _alpaca(handler, now=NOW):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AlpacaAdapter("TESTKEY", "TESTSECRET", client=client, sleep=fake_sleep, now=lambda: now), sleeps


def test_alpaca_pagination_retry_after_and_feed_clamp():
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        assert req.headers["APCA-API-KEY-ID"] == "TESTKEY"
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        token = req.url.params.get("page_token")
        bar = {"t": "2026-10-16T04:00:00Z", "o": 10, "h": 11, "l": 9.5, "c": 10.5, "v": 1000, "n": 10, "vw": 10.2}
        if not token:
            return httpx.Response(200, json={"bars": {"AAA": [bar]}, "next_page_token": "p2"})
        bar2 = {**bar, "t": "2026-10-19T04:00:00Z"}
        return httpx.Response(200, json={"bars": {"AAA": [bar2]}, "next_page_token": None})

    adapter, sleeps = _alpaca(handler)
    out = asyncio.run(adapter.get_daily_bars(["AAA"], date(2026, 10, 1), date(2026, 10, 20), feed="sip"))
    assert list(out["AAA"].index.date) == [date(2026, 10, 16), date(2026, 10, 19)]
    assert 3.0 in sleeps  # honoured Retry-After
    end = datetime.fromisoformat(calls[-1].url.params["end"].replace("Z", "+00:00"))
    assert end <= NOW - timedelta(minutes=15)  # SIP never requested inside the Basic 15-minute window


def test_alpaca_auth_failure_is_clear_and_secret_free():
    adapter, _ = _alpaca(lambda req: httpx.Response(403, json={"message": "forbidden"}))
    with pytest.raises(AlpacaError) as err:
        asyncio.run(adapter.get_latest(["AAA"]))
    assert "TESTSECRET" not in str(err.value) and err.value.status == 403
    assert adapter.health().last_error.startswith("authentication failed")


def test_alpaca_latest_labels_iex():
    def handler(req):
        if "trades" in req.url.path:
            return httpx.Response(200, json={"trades": {"AAA": {"t": "2026-10-20T14:59:59Z", "p": 12.34, "s": 5}}})
        return httpx.Response(200, json={"quotes": {"AAA": {"t": "2026-10-20T14:59:59Z", "bp": 12.33, "ap": 12.35}}})

    adapter, _ = _alpaca(handler)
    lp = asyncio.run(adapter.get_latest(["AAA"]))["AAA"]
    assert lp.feed == "iex" and lp.price == 12.34 and lp.bid == 12.33


def test_alpaca_corporate_actions_unsupported_returns_none():
    adapter, _ = _alpaca(lambda req: httpx.Response(403, json={}))
    assert asyncio.run(adapter.get_corporate_actions(["AAA"], date(2026, 1, 1), date(2026, 2, 1))) is None


# ---------------- replay adapter ----------------
@pytest.fixture(scope="module")
def market():
    return generate(
        symbols=[
            "DMSPY",
            "DMSEMI1",
            "DMSPLIT1",
            "DMOTC1",
            "DMPENNY1",
            "DMLEV3X",
            "DMSPAC.WS",
            "DMNEW1",
            "DMTHIN1",
            "DMDELIST1",
        ]
    )


def test_replay_never_leaks_future(market):
    clock = ReplayClock(datetime(2026, 9, 15, 15, 0, tzinfo=UTC))  # mid-session
    ad = ReplayAdapter(market, clock)
    bars = asyncio.run(ad.get_daily_bars(["DMSEMI1"], date(2026, 1, 1), date(2026, 12, 31)))["DMSEMI1"]
    assert bars.index[-1] == pd.Timestamp("2026-09-14")  # today's bar not complete yet
    intra = asyncio.run(
        ad.get_intraday_bars(
            ["DMSEMI1"], datetime(2026, 9, 15, 13, 30, tzinfo=UTC), datetime(2026, 9, 15, 20, 0, tzinfo=UTC)
        )
    )["DMSEMI1"]
    assert intra.index.max() + timedelta(minutes=15) <= clock.now()
    lp = asyncio.run(ad.get_latest(["DMSEMI1"]))["DMSEMI1"]
    assert lp.ts_exchange <= clock.now() and lp.feed == "demo"


def test_replay_faults(market):
    ad = ReplayAdapter(market, ReplayClock(datetime(2026, 9, 15, 15, 0, tzinfo=UTC)), faults=Faults(rate_limit_next=1))
    with pytest.raises(ProviderRateLimited):
        asyncio.run(ad.get_latest(["DMSEMI1"]))
    assert asyncio.run(ad.get_latest(["DMSEMI1"]))
    ad.faults.stale_symbols.add("DMSEMI1")
    assert "DMSEMI1" not in asyncio.run(ad.get_latest(["DMSEMI1"]))


def test_universe_filters(market):
    clock = ReplayClock(datetime(2026, 10, 16, 21, 0, tzinfo=UTC))
    ad = ReplayAdapter(market, clock)
    assets = asyncio.run(ad.get_assets())
    daily = asyncio.run(ad.get_daily_bars([a.symbol for a in assets], date(2025, 1, 1), date(2026, 10, 16)))
    dec = {
        d.symbol: d for d in evaluate_universe(assets, daily, overrides=UniverseOverrides(user_verified={"DMSEMI1"}))
    }
    assert dec["DMSEMI1"].eligible and dec["DMSEMI1"].tradability_status == "user_verified"
    assert "NOT_LISTED_EXCHANGE" in dec["DMOTC1"].reasons
    assert "PRICE_BELOW_MIN" in dec["DMPENNY1"].reasons
    assert "LEVERAGED_EXCLUDED" in dec["DMLEV3X"].reasons
    assert "WARRANT_EXCLUDED" in dec["DMSPAC.WS"].reasons
    assert "INSUFFICIENT_HISTORY" in dec["DMNEW1"].reasons
    assert "LOW_SHARE_VOLUME" in dec["DMTHIN1"].reasons
    assert not dec["DMDELIST1"].eligible


# ---------------- slots ----------------
def test_slot_planner_reserves_positions_and_caps_sectors():
    reqs = [SlotRequest(f"P{i}", "position") for i in range(5)]
    reqs += [SlotRequest(f"T{i}", "candidate", 90 - i, "technology") for i in range(30)]
    reqs += [SlotRequest(f"E{i}", "candidate", 50 - i, "energy") for i in range(5)]
    plan = plan_slots(reqs, current={"T29", "P0"}, max_slots=25)
    assert len(plan.selected) <= 25 and all(f"P{i}" in plan.selected for i in range(5))
    tech = [s for s in plan.selected if s.startswith("T")]
    assert len(tech) <= 10 and all(f"E{i}" in plan.selected for i in range(5))
    assert "T29" in plan.unsubscribe and "P0" not in plan.subscribe


# ---------------- IEX stream ----------------
class FakeWS:
    def __init__(self, frames, fail_after=None):
        self.frames = list(frames)
        self.sent = []
        self.fail_after = fail_after

    async def send(self, msg):
        self.sent.append(json.loads(msg))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.fail_after is not None and len(self.sent) and not self.frames:
            raise ConnectionError("dropped")
        if not self.frames:
            raise StopAsyncIteration
        return self.frames.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def test_stream_auth_subscribe_reconnect_backfill():
    t = "2026-10-20T14:59:59Z"
    sessions = [
        FakeWS(
            [
                json.dumps([{"T": "success", "msg": "connected"}]),
                json.dumps([{"T": "success", "msg": "authenticated"}]),
                json.dumps([{"T": "t", "S": "AAA", "p": 10.0, "s": 5, "t": t}]),
            ],
            fail_after=1,
        ),
        FakeWS(
            [
                json.dumps([{"T": "success", "msg": "authenticated"}]),
                json.dumps([{"T": "q", "S": "AAA", "bp": 10.0, "ap": 10.02, "t": "2026-10-20T15:00:00Z"}]),
            ]
        ),
    ]
    stop = asyncio.Event()
    backfilled = []

    def connect(url, **kw):
        if not sessions:
            stop.set()
            return FakeWS([])
        return sessions.pop(0)

    async def backfill(symbols):
        backfilled.append(symbols)

    async def no_sleep(_):
        return None

    cache = QuoteCache()
    s = IexStream(
        "K",
        "S",
        cache,
        connect=connect,
        now=lambda: datetime(2026, 10, 20, 15, 0, 1, tzinfo=UTC),
        backfill=backfill,
        sleep=no_sleep,
    )
    s.set_symbols(["AAA"])
    asyncio.run(s.run(stop))
    assert s.health.reconnects >= 1 and backfilled == [["AAA"]]
    assert cache.get("AAA").feed == "iex" and cache.get("AAA").latency_class == "live"
    with pytest.raises(ValueError):
        s.set_symbols([f"S{i}" for i in range(31)])


def test_stream_auth_error_raises():
    cache = QuoteCache()
    s = IexStream(
        "K",
        "S",
        cache,
        connect=lambda url, **kw: FakeWS([json.dumps([{"T": "error", "code": 402, "msg": "auth failed"}])]),
    )
    with pytest.raises(StreamAuthError):
        asyncio.run(s.run(asyncio.Event()))
