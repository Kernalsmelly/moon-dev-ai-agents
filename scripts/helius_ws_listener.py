#!/usr/bin/env python3
"""Helius websocket listener wrapper.

Reads program IDs from config/helius_programs.json and listens for logs.
If HELIUS_WS_URL is not set or programs are empty, it will wait and retry.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.solana.helius_ws import run_multi_log_subscribe
from src.solana.helius_event_store import append_event


def load_programs(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("program_ids", []) if isinstance(data, dict) else []
    except Exception:
        return []


async def on_message(msg: dict):
    # Minimal handler: print a short heartbeat line
    if "method" in msg and msg.get("method") == "logsNotification":
        result = msg.get("params", {}).get("result", {})
        value = result.get("value", {}) if isinstance(result, dict) else {}
        evt = {
            "program_id": msg.get("program_id"),
            "signature": value.get("signature"),
            "err": value.get("err"),
            "logs": value.get("logs"),
            "slot": result.get("context", {}).get("slot") if isinstance(result, dict) else None,
        }
        append_event(evt)
        print(f"[{datetime.now()}] logsNotification program={evt['program_id']}")
    elif "id" in msg and "result" in msg:
        # Subscription ack
        try:
            print(f"[{datetime.now()}] subscription ack: {msg.get('id')} -> {msg.get('result')}")
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--programs-file", default="config/helius_programs.json", help="JSON file with program_ids")
    parser.add_argument("--poll", type=int, default=60, help="Seconds between config checks")
    args = parser.parse_args()

    while True:
        ws_url = os.getenv("HELIUS_WS_URL") or os.getenv("HELIUS_WS")
        programs = load_programs(args.programs_file)
        if not ws_url:
            print(f"[{datetime.now()}] Waiting for HELIUS_WS_URL...")
            time.sleep(max(10, args.poll))
            continue
        if not programs:
            print(f"[{datetime.now()}] No program IDs configured yet.")
            time.sleep(max(10, args.poll))
            continue

        # Subscribe to all program IDs
        program_ids = programs
        try:
            import asyncio
            asyncio.run(run_multi_log_subscribe(program_ids, on_message))
        except Exception as e:
            print(f"[{datetime.now()}] WS listener error: {e}")
            time.sleep(max(10, args.poll))


if __name__ == "__main__":
    main()
