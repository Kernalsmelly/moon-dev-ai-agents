#!/usr/bin/env python3
"""Wait for v2 sweeps to finish, then run confirm/cooldown sweep."""
from __future__ import annotations

import argparse
import os
import subprocess
import time


def log_has_complete(path: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
        return ("Sweep complete" in content) or ("Sweep complete." in content)
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-a", required=True, help="First sweep log to wait on")
    parser.add_argument("--log-b", required=True, help="Second sweep log to wait on")
    parser.add_argument("--input", default="data/meme_snapshots.jsonl", help="Snapshot JSONL")
    parser.add_argument("--regime-file", default="", help="Optional regime tags JSON")
    parser.add_argument("--hot-only", action="store_true", help="Only trade during hot regimes")
    parser.add_argument("--config-file", default="", help="Optional base config JSON")
    parser.add_argument("--poll", type=int, default=60, help="Seconds between checks")
    args = parser.parse_args()

    while True:
        if log_has_complete(args.log_a) and log_has_complete(args.log_b):
            break
        time.sleep(max(10, args.poll))

    cmd = [
        "python3",
        "scripts/meme_confirm_sweep.py",
        "--input",
        args.input,
        "--out",
        "data/meme_replay_trades.confirm_3h.csv",
    ]
    if args.config_file:
        cmd.extend(["--config-file", args.config_file])
    if args.regime_file:
        cmd.extend(["--regime-file", args.regime_file])
    if args.hot_only:
        cmd.append("--hot-only")

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["PYTHONPATH"] = project_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    subprocess.run(cmd, check=False, cwd=project_root, env=env)


if __name__ == "__main__":
    main()
