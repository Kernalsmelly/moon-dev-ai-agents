#!/usr/bin/env python3
"""Live performance dashboard that tails `data/trades.jsonl` and prints a small table.

This is intentionally dependency-light and uses polling to tail the file so it works
in simple CI/dev environments.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()


def compute_metrics(lines: list[str]) -> dict:
    net_pnl = 0.0
    wins = 0
    total = 0
    jito_attempts = 0
    jito_success = 0
    latest_vhi = None

    for ln in lines:
        if not ln.strip():
            continue
        try:
            obj = json.loads(ln)
        except Exception:
            continue

        # PnL heuristics
        pnl = None
        if 'pnl_sol' in obj:
            try:
                pnl = float(obj.get('pnl_sol') or 0.0)
            except Exception:
                pnl = None
        else:
            # try to infer from expected_out_sol - input_amount_sol
            try:
                out = float(obj.get('expected_out_sol') or 0.0)
                inp = float(obj.get('input_amount_sol') or obj.get('amount_sol') or 0.0)
                pnl = out - inp
            except Exception:
                pnl = None

        if pnl is not None:
            net_pnl += pnl
            total += 1
            if pnl > 0:
                wins += 1

        # Jito heuristics
        if obj.get('bundle_id') or obj.get('jito_bundle_id'):
            jito_attempts += 1
            st = obj.get('bundle_status') or obj.get('jito_status') or obj.get('bundle_result', {}).get('status') if isinstance(obj.get('bundle_result'), dict) else None
            if st in ('ok', 'success', 'submitted') or obj.get('bundle_success') is True:
                jito_success += 1

        # VHI heuristics: find last value
        if latest_vhi is None:
            if 'vhi' in obj:
                latest_vhi = obj.get('vhi')
            elif 'vhi_display' in obj:
                latest_vhi = obj.get('vhi_display')

    win_rate = (wins / total * 100.0) if total else 0.0
    jito_rate = (jito_success / jito_attempts * 100.0) if jito_attempts else 0.0

    return {
        'net_pnl': net_pnl,
        'win_rate': win_rate,
        'jito_rate': jito_rate,
        'vhi': latest_vhi,
        'sample_count': total,
        'jito_attempts': jito_attempts,
    }


async def tail_loop(path: Path, interval: float = 1.0):
    # ensure file exists
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text('')

    # open once and seek to start
    with open(path, 'r', encoding='utf-8') as fh:
        # start at beginning to compute from whole file
        lines = fh.readlines()
        pos = fh.tell()

    while True:
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                fh.seek(pos)
                new = fh.read()
                if new:
                    # update lines list
                    added = new.splitlines()
                    lines.extend(added)
                    pos = fh.tell()

            # compute metrics and render
            metrics = compute_metrics(lines)
            console.clear()
            tbl = Table(title="Moon Dev — Live Performance", show_lines=False)
            tbl.add_column("Metric", style="cyan")
            tbl.add_column("Value", style="magenta")
            tbl.add_row("Net PnL (SOL)", f"{metrics['net_pnl']:.4f}")
            tbl.add_row("Win Rate %", f"{metrics['win_rate']:.1f}%")
            tbl.add_row("Jito Land Rate %", f"{metrics['jito_rate']:.1f}% ({metrics['jito_attempts']} attempts)")
            tbl.add_row("Current $VHI", f"{metrics['vhi'] if metrics['vhi'] is not None else 'N/A'}")
            tbl.add_row("Sample Count", str(metrics['sample_count']))
            console.print(Panel(tbl))
        except Exception as e:
            console.print(Panel(f"Dashboard error: {e}", style="red"))

        await asyncio.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description='Live dashboard for trades.jsonl')
    parser.add_argument('--path', '-p', default=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'trades.jsonl'))
    parser.add_argument('--interval', '-i', type=float, default=1.0)
    args = parser.parse_args()

    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(tail_loop(Path(args.path), args.interval))
    except KeyboardInterrupt:
        print('\nExiting dashboard.')


if __name__ == '__main__':
    main()
