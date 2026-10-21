"""REAL-SOURCE EMULATION FIXTURE for tests and the DEMO smoke run — never market data.

An in-memory fake of Alpaca's GET /v2/stocks/bars (daily, paginated) that serves the deterministic synthetic market
under the real training-universe symbols, so the full adapter -> history -> research path runs with no network and no
keys. Anything produced from it is labelled SYNTHETIC_FIXTURE_DEMO and can never be published as a real experiment.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from apex_worker.market.adapters.alpaca import AlpacaAdapter
from apex_worker.market.ratelimit import TokenBucket
from apex_worker.research.training_data import BENCHMARK, MEMBERS, SECTOR_ETFS

NY = ZoneInfo("America/New_York")
FAKE_KEY_ID = "PKTESTONLY0000000000"
FAKE_SECRET = "test-only-secret"  # noqa: S105 - not a credential: the MockTransport never leaves the process
FIXTURE_NOW = datetime(2026, 10, 16, 23, 0, tzinfo=UTC)  # the synthetic market ends 2026-10-16


def universe_frames(start: date = date(2022, 1, 3)) -> dict[str, pd.DataFrame]:
    from apex_worker.demo.synthetic import generate

    market = generate(stocks_per_sector=6, start=start)
    out: dict[str, pd.DataFrame] = {}
    for sym, spec in market.assets.items():
        if spec.asset_kind == "benchmark" and sym == "DMSPY":
            out[BENCHMARK] = market.adjusted(sym)
        elif spec.asset_kind == "etf" and spec.sector in SECTOR_ETFS:
            out[SECTOR_ETFS[spec.sector]] = market.adjusted(sym)
    for sector, members in MEMBERS.items():
        synth = sorted(
            s for s, a in market.assets.items() if a.sector == sector and a.asset_kind == "stock" and a.instrument == "common"
        )
        for real, fake in zip(members, synth, strict=False):
            out[real] = market.adjusted(fake)
    return out


class FakeAlpaca:
    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = frames
        self.requests: list[dict] = []
        self._rows: dict[str, list[tuple[pd.Timestamp, dict]]] = {}
        for sym, df in frames.items():
            stamps = [pd.Timestamp(datetime.combine(d.date(), time(0, 0), tzinfo=NY).astimezone(UTC)) for d in df.index]
            recs = df[["open", "high", "low", "close", "volume"]].to_dict("records")
            self._rows[sym] = list(zip(stamps, recs, strict=True))

    def handler(self, req: httpx.Request) -> httpx.Response:
        if req.url.path != "/v2/stocks/bars":
            return httpx.Response(404, json={"message": "not emulated"})
        assert req.headers["APCA-API-KEY-ID"] == FAKE_KEY_ID
        p = dict(req.url.params)
        self.requests.append(p)
        assert p["timeframe"] == "1Day" and p["adjustment"] == "split" and p["feed"] in ("sip", "iex")
        start, end = pd.Timestamp(p["start"]), pd.Timestamp(p["end"])
        limit, offset = int(p["limit"]), int(p.get("page_token") or 0)
        rows = [(sym, t, r) for sym in sorted(p["symbols"].split(",")) for t, r in self._rows.get(sym, []) if start <= t <= end]
        page = rows[offset : offset + limit]
        bars: dict[str, list[dict]] = {}
        for sym, t, r in page:
            bars.setdefault(sym, []).append(
                {"t": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "o": float(r["open"]), "h": float(r["high"]), "l": float(r["low"]),
                 "c": float(r["close"]), "v": int(r["volume"]), "n": 1, "vw": float(r["close"])}
            )
        nxt = str(offset + limit) if offset + limit < len(rows) else None
        return httpx.Response(200, json={"bars": bars, "next_page_token": nxt})

    def adapter(self, now: datetime) -> AlpacaAdapter:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return AlpacaAdapter(FAKE_KEY_ID, FAKE_SECRET, client=client, bucket=TokenBucket(1e9, 1e9),
                             sleep=_no_sleep, now=lambda: now)


async def _no_sleep(_s: float) -> None:
    return None
