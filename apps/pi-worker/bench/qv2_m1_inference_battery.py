"""Quant V2 M1: size and power of research.inference on simulated trade panels (docs/quant-v2/M1_ENGINE.md).

    uv run python bench/qv2_m1_inference_battery.py > battery.json      # about 15 minutes on one core

Synthetic, seeded, offline; no market data. Each design is a strategy-minus-control (paired) or raw (unpaired) trade
panel with 20-session overlapping holds and market + sector + idiosyncratic shocks; see inference.Design.
"""

from __future__ import annotations

import json
import sys

from apex_worker.research.inference import MIN_BLOCKS, Design, calibrate

DESIGNS = [
    Design("presample_like_680s_120d_x3", span=680, dates=120, per_date=3),
    Design("dev_like_1700s_300d_x3", span=1700, dates=300, per_date=3),
    Design("dense_680s_400d_x3", span=680, dates=400, per_date=3),
    Design("fat_tails_vol_regimes_680s", span=680, dates=120, per_date=3, tail_df=3, hetero=True),
    Design("unpaired_680s_120d_x3", span=680, dates=120, per_date=3, paired=False),
    Design("h8_like_75s_15d_x9", span=75, dates=15, per_date=9),
    Design("thin_340s_40d_x2", span=340, dates=40, per_date=2),
]


def main(reps: int = 1000) -> dict:
    size = calibrate(DESIGNS, reps=reps, effects=(0.0,), n_boot=999,
                     progress=lambda m: print(m, file=sys.stderr, flush=True))
    power = calibrate(DESIGNS[:5], reps=reps // 2, effects=(0.004, 0.008, 0.015), n_boot=999,
                      progress=lambda m: print(m, file=sys.stderr, flush=True))
    return {"min_blocks": MIN_BLOCKS, "size": size, "power": power,
            "note": "effect = planted mean per trade (0.004 = +0.4 percentage points); rejection = conservative p < alpha"}


if __name__ == "__main__":
    print(json.dumps(main(), indent=1))
