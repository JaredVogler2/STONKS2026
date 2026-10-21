"""Resource benchmark for the Pi workload at the Phase 12 load profile.

    uv run python bench/bench_scan.py [--stocks-per-sector 46] [--years 2] [--positions 100] [--json out.json]

Builds a synthetic universe (11 sectors x N stocks + sector ETFs + benchmark), computes the full causal feature panel,
runs the end-of-day candidate scan for the last session and a quick incremental re-scan, and evaluates
FIFO assessments for simulated member positions. Reports wall time, CPU time and peak RSS. Results are from the machine it
runs on; numbers for a Raspberry Pi 5 are only valid when this script is executed on one.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import time
from datetime import date, timedelta
from decimal import Decimal

from apex_worker.demo.synthetic import generate
from apex_worker.features.panel import panel_from_synthetic
from apex_worker.portfolio.ledger import EventType, Fill, LedgerEvent, replay
from apex_worker.signals.candidates import CandidateEngine


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux reports KiB


def timed(label: str, fn, out: dict):
    t0, c0 = time.perf_counter(), time.process_time()
    res = fn()
    out[label] = {
        "wall_s": round(time.perf_counter() - t0, 3),
        "cpu_s": round(time.process_time() - c0, 3),
        "peak_rss_mb": round(rss_mb(), 1),
    }
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks-per-sector", type=int, default=46)
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--positions", type=int, default=100)
    ap.add_argument("--json")
    a = ap.parse_args()
    out: dict = {"machine": {"platform": platform.platform(), "machine": platform.machine(), "cpus": os.cpu_count(),
                             "python": platform.python_version()}, "params": vars(a)}
    end = date(2026, 10, 16)
    start = end - timedelta(days=int(365 * a.years))
    market = timed("generate_synthetic", lambda: generate(start=start, end=end, stocks_per_sector=a.stocks_per_sector), out)
    out["universe_size"] = len(market.bars)
    panel = timed("feature_panel", lambda: panel_from_synthetic(market), out)
    session = panel.sessions[-1]
    engine = CandidateEngine(is_demo=True)
    res = timed("eod_scan", lambda: engine.scan(panel, session), out)
    out["scan"] = {"scanned": res.stats.scanned, "eligible": res.stats.eligible, "candidates": len(res.candidates)}
    timed("rescan_cached", lambda: engine.scan(panel, session), out)

    def assess():
        n = 0
        for i in range(a.positions):
            evs = [LedgerEvent(event_id=f"e{i}a", seq=1, event_type=EventType.OPEN,
                               fill=Fill(Decimal("100.25"), Decimal("20"), Decimal("1"), session.to_pydatetime())),
                   LedgerEvent(event_id=f"e{i}b", seq=2, event_type=EventType.ADD,
                               fill=Fill(Decimal("101.10"), Decimal("10"), Decimal("1"), session.to_pydatetime())),
                   LedgerEvent(event_id=f"e{i}c", seq=3, event_type=EventType.PARTIAL_EXIT,
                               fill=Fill(Decimal("104.00"), Decimal("15"), Decimal("1"), session.to_pydatetime()))]
            st = replay(evs)
            n += int(st.total_quantity)
        return n

    timed("ledger_replay_positions", assess, out)
    out["peak_rss_mb"] = round(rss_mb(), 1)
    text = json.dumps(out, indent=2, default=str)
    print(text)
    if a.json:
        with open(a.json, "w") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
