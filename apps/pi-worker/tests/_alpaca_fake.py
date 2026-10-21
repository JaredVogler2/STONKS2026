"""In-memory fake of Alpaca's GET /v2/stocks/bars for history/loader tests (re-exported from the package fixture).

Serves the deterministic synthetic market under the real training-universe symbols, so no network and no keys.
"""

from apex_worker.research.realeq.fixture import NY, FakeAlpaca, _no_sleep, universe_frames

__all__ = ["NY", "FakeAlpaca", "_no_sleep", "universe_frames"]
