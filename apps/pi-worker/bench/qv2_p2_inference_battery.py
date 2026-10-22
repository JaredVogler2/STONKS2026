"""Quant V2 protocol-2: size and power of the DECISION statistic (conservative over L = 20 and 40 sessions) on time axes
as short as the PRESAMPLE window after gate closures (docs/quant-v2/PROTOCOL_V2_DESIGN.md).

    uv run python bench/qv2_p2_inference_battery.py > battery_p2.json     # about 6 minutes on one core

Synthetic, seeded, offline; no market data.
"""

from __future__ import annotations

import json
import sys

from apex_worker.research.inference import Design, calibrate

LENGTHS = (20, 40)
DESIGNS = [
    Design("presample_684s_120d_x3", span=684, dates=120, per_date=3),
    Design("gate_compressed_541s_100d_x3", span=541, dates=100, per_date=3),
    Design("gate_compressed_480s_60d_x2", span=480, dates=60, per_date=2),
    Design("fat_tails_vol_regimes_541s", span=541, dates=100, per_date=3, tail_df=3, hetero=True),
    Design("dense_541s_400d_x4", span=541, dates=400, per_date=4),
    Design("dev_like_1700s_300d_x3", span=1700, dates=300, per_date=3),
]


def main(reps: int = 1000) -> dict:
    log = lambda m: print(m, file=sys.stderr, flush=True)  # noqa: E731
    size = calibrate(DESIGNS, reps=reps, effects=(0.0,), lengths=LENGTHS, n_boot=999, progress=log)
    power = calibrate(DESIGNS[:4], reps=reps // 2, effects=(0.008, 0.015, 0.025), lengths=LENGTHS, n_boot=999, progress=log)
    return {"lengths": list(LENGTHS), "size": size, "power": power,
            "note": "effect = planted mean per trade; rejection = conservative p (max over L = 20, 40) < alpha"}


if __name__ == "__main__":
    print(json.dumps(main(), indent=1))
