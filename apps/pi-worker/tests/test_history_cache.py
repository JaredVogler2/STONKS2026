"""Bounded, incremental, checksummed history download (market.history) and the CI training loader."""

import asyncio
import json
from datetime import UTC, date, datetime

import pandas as pd
import pytest

from _alpaca_fake import FakeAlpaca, universe_frames
from apex_worker.market.history import HistorySpec, decode_bars, encode_bars, sync_history
from apex_worker.research.training_data import BENCHMARK, make_alpaca_loader, universe_symbols

NOW = datetime(2026, 10, 20, 23, 0, tzinfo=UTC)
END = date(2026, 10, 9)


@pytest.fixture(scope="module")
def frames():
    return universe_frames()


def spec(**kw) -> HistorySpec:
    return HistorySpec(symbols=universe_symbols(), years=3, **kw)


def run(fake: FakeAlpaca, root, end=END, s=None):
    async def go():
        adapter = fake.adapter(NOW)
        try:
            return await sync_history(adapter, root, s or spec(), end=end, now=NOW)
        finally:
            await adapter.aclose()

    return asyncio.run(go())


def test_universe_is_fixed_and_bounded():
    syms = universe_symbols()
    assert len(syms) == 78 and len(set(syms)) == 78 and syms[0] == BENCHMARK
    with pytest.raises(ValueError):
        HistorySpec(symbols=syms, max_symbols=10).validate()
    with pytest.raises(ValueError):
        HistorySpec(symbols=("bad sym",)).validate()


def test_encoding_is_deterministic_and_round_trips(frames):
    df = frames["AAPL"].iloc[:50]
    a, b = encode_bars(df), encode_bars(df)
    assert a == b  # gzip mtime=0 + fixed format => stable checksums
    back = decode_bars(a)
    assert list(back.columns) == ["open", "high", "low", "close", "volume"] and len(back) == 50
    assert abs(back["close"].iloc[-1] - df["close"].iloc[-1]) < 1e-6


def test_full_then_reuse_then_incremental(frames, tmp_path):
    fake = FakeAlpaca(frames)
    first = run(fake, tmp_path)
    assert len(first.full) == 78 and not first.missing and first.requests == len(fake.requests) >= 1
    assert first.bars[BENCHMARK].index.max() == pd.Timestamp(END)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["schema"] == "apex-history-cache-1" and manifest["symbols"]["AAPL"]["sha256"]
    # the fixture includes a recent listing (served as AAPL, history starts 2026-03) and a delisting (AMZN, 2024-03)
    assert first.bars["AAPL"].index.min() > pd.Timestamp(first.start) and first.bars["AMZN"].index.max() < pd.Timestamp(END)
    # same end date: nothing new to fetch except one probe for the delisted symbol; identical hash
    n = len(fake.requests)
    again = run(fake, tmp_path)
    assert len(again.reused) == 77 and again.stale == ["AMZN"] and len(fake.requests) - n == 1
    assert again.history_hash == first.history_hash
    # a week later: small incremental request groups, overlap verified, new hash
    n = len(fake.requests)
    later = run(fake, tmp_path, end=date(2026, 10, 16))
    assert len(later.incremental) == 77 and later.stale == ["AMZN"] and later.requests <= 3
    assert later.history_hash != first.history_hash
    assert later.bars["AAPL"].index.max() == pd.Timestamp("2026-10-16")
    # incremental windows only (the delisted symbol is probed from its own last bar), never the full history
    assert all(p["start"] >= "2026-10-01" for p in fake.requests[n:] if p["symbols"] != "AMZN")
    # bounded window: nothing older than `years` is kept
    assert later.bars["AAPL"].index.min() >= pd.Timestamp(later.start)


def test_corrupt_file_is_detected_and_refetched(frames, tmp_path):
    fake = FakeAlpaca(frames)
    run(fake, tmp_path)
    f = tmp_path / "bars" / "MSFT.sip.split.csv.gz"
    f.write_bytes(f.read_bytes()[:-7] + b"garbage")
    res = run(fake, tmp_path)
    assert res.corrupted == ["MSFT"] and res.full == ["MSFT"] and len(res.reused) == 76 and res.stale == ["AMZN"]


def test_retroactive_split_adjustment_triggers_full_refetch(frames, tmp_path):
    fake = FakeAlpaca(frames)
    run(fake, tmp_path)
    changed = dict(frames)
    df = frames["NVDA"].copy()
    for c in ("open", "high", "low", "close"):
        df[c] = df[c] / 4  # a 4:1 split re-adjusts the whole history
    changed["NVDA"] = df
    res = run(FakeAlpaca(changed), tmp_path, end=date(2026, 10, 16))
    assert res.refetched_after_mismatch == ["NVDA"] and "NVDA" not in res.incremental
    assert abs(res.bars["NVDA"]["close"].iloc[0] - frames["NVDA"].loc[res.bars["NVDA"].index[0], "close"] / 4) < 1e-5


def test_request_budget_is_a_hard_bound(frames, tmp_path):
    fake = FakeAlpaca(frames)
    first = run(fake, tmp_path)
    # zero remaining budget: no request is made, cached bars are kept and listed as stale, nothing is fabricated
    n = len(fake.requests)
    later = run(fake, tmp_path, end=date(2026, 10, 16), s=spec(max_requests=0))
    assert later.budget_exhausted and later.requests == 0 and len(fake.requests) == n
    assert sorted(later.stale) == sorted(first.bars) and later.bars["MSFT"].index.max() == pd.Timestamp(END)
    # a fresh cache with no budget yields no data at all (=> training reports no_data, champion retained)
    empty = run(fake, tmp_path / "fresh", s=spec(max_requests=0))
    assert empty.bars == {} and len(empty.missing) == 78


def test_alpaca_loader_feeds_run_training(frames, tmp_path):
    from apex_worker.research.pipeline import run_training

    fake = FakeAlpaca(frames)
    loader, info = make_alpaca_loader("unused", "unused", tmp_path / "cache", spec(), now=NOW,
                                      adapter_factory=lambda: fake.adapter(NOW))
    res = run_training("alpaca", tmp_path / "out", loader=loader, strategies=["h1_vcp_breakout", "h5_sector_rotation"],
                       is_demo=False, now=NOW, code_sha="test", bias_warnings=info.warnings)
    assert res.status in ("succeeded", "no_data"), res.detail
    assert info.result is not None and info.to_dict()["history_hash"] == info.result.history_hash
    assert any("SURVIVORSHIP" in w for w in info.to_dict()["bias_warnings"])
    if res.status == "succeeded":
        report = json.loads((tmp_path / "out" / "report.json").read_text())
        assert report["data_source"] == "historical_market_data" and len(report["data_hash"]) == 64
        some = next(iter(report["reports"].values()))
        assert any("SURVIVORSHIP" in w for w in some["bias_warnings"])
        for c in res.candidates:  # real-data candidates are live-eligible manifests, never DEMO-labelled
            m = json.loads((tmp_path / "out" / "releases" / c["model_id"] / "manifest.json").read_text())
            assert m["is_demo"] is False and m["training_data_hash"] == report["data_hash"]


def test_loader_without_benchmark_reports_no_data(frames, tmp_path):
    from apex_worker.research.pipeline import run_training

    fake = FakeAlpaca({k: v for k, v in frames.items() if k != BENCHMARK})
    loader, info = make_alpaca_loader("unused", "unused", tmp_path / "cache", spec(), now=NOW,
                                      adapter_factory=lambda: fake.adapter(NOW))
    res = run_training("alpaca", tmp_path / "out", loader=loader, is_demo=False, now=NOW)
    assert res.status == "no_data" and "benchmark" in (info.error or "")
