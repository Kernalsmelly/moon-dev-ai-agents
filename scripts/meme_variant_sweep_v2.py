#!/usr/bin/env python3
"""Run a focused v2 grid sweep of meme replay variants.

This version includes newer microstructure filters (5m txns, 5m buy/sell,
volume floors, mcap/liquidity ratio, entry cooldown) but keeps the grid size
manageable for faster iteration.

Usage:
  python scripts/meme_variant_sweep_v2.py --input data/meme_snapshots.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Snapshot JSONL")
    parser.add_argument("--out", default="data/meme_replay_trades.csv", help="Baseline output CSV")
    args = parser.parse_args()

    grids = []
    # Core momentum/quality ranges
    min_scores = [45, 50, 55]
    max_pumps = [20, 30, 40]
    min_1h = [-5, 0, 5]
    liq_accel = [0, 5, 10]
    vol_accel = [0, 5, 10]
    decay_per_hour = [0.0]
    max_boost_age = [0, 600]  # 0=disabled, 10m
    max_top10 = [0.65]        # keep stable for now

    # Microstructure filters
    min_txns_5m = [0, 8, 15]
    min_bs_5m = [0.0, 1.1, 1.3]
    min_vol_1h = [0.0, 20000.0, 50000.0]
    min_vol_5m = [0.0, 1000.0, 2000.0]
    max_mcap_liq_ratio = [0.0, 10.0, 15.0]
    entry_cooldown = [0, 120]
    confirm_n = [0, 2]

    for ms in min_scores:
        for mp in max_pumps:
            for m1h in min_1h:
                for la in liq_accel:
                    for va in vol_accel:
                        for dc in decay_per_hour:
                            for mba in max_boost_age:
                                for mt in max_top10:
                                    for tx5 in min_txns_5m:
                                        for bs5 in min_bs_5m:
                                            for v1h in min_vol_1h:
                                                for v5m in min_vol_5m:
                                                    for mlr in max_mcap_liq_ratio:
                                                        for cd in entry_cooldown:
                                                            for cn in confirm_n:
                                                                name = (
                                                                    f"s{ms}_p{mp}_h{m1h}_la{la}_va{va}"
                                                                    f"_d{dc}_mba{mba}_t10{int(mt*100)}"
                                                                    f"_tx5{tx5}_bs5{bs5}_v1h{int(v1h)}"
                                                                    f"_v5m{int(v5m)}_mlr{int(mlr)}_cd{cd}"
                                                                    f"_cn{cn}"
                                                                )
                                                                grids.append({
                                                                    "name": name,
                                                                "MIN_VHI_SCORE": ms,
                                                                "MAX_5M_PUMP": mp,
                                                                "MIN_PRICE_CHANGE_1H": m1h,
                                                                "MIN_LIQ_ACCEL_PCT": la,
                                                                "MIN_VOL_ACCEL_PCT": va,
                                                                "SCORE_DECAY_PER_HOUR": dc,
                                                                "MAX_BOOST_AGE_SECONDS": mba,
                                                                "MAX_TOP10_HOLDER_PCT": mt,
                                                                "USE_TOP10_CHECK": True,
                                                                "MIN_TXNS_5M": tx5,
                                                                "MIN_BUY_SELL_RATIO_5M": bs5,
                                                                "MIN_VOLUME_1H": v1h,
                                                                "MIN_VOLUME_5M": v5m,
                                                                    "MAX_MCAP_LIQ_RATIO": mlr,
                                                                    "ENTRY_COOLDOWN_SECONDS": cd,
                                                                    "CONFIRM_N": cn,
                                                                })

    variants_path = "data/meme_variants_v2.json"
    with open(variants_path, "w", encoding="utf-8") as fh:
        json.dump(grids, fh)

    cmd = [
        "python3",
        "scripts/meme_replay.py",
        "--input",
        args.input,
        "--out",
        args.out,
        "--variants-file",
        variants_path,
    ]
    # Optional hot-only regime gating if tags exist
    if os.path.exists("data/meme_regime_tags_3h.json"):
        cmd.extend(["--regime-file", "data/meme_regime_tags_3h.json", "--hot-only"])

    print(f"Running v2 sweep with {len(grids)} variants...")
    subprocess.run(cmd, check=False)
    print("Sweep complete. Review outputs in data/")


if __name__ == "__main__":
    main()
