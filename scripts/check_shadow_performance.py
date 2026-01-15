#!/usr/bin/env python3
"""Simple performance summary for shadow-mode runs.

Reads data/execution_events.csv and prints:
- Total Simulated Exits
- Average Compute Units (unitsConsumed)
- Average Price Impact (estimated_impact_pct)

This is a lightweight script for quick baseline reporting.
"""
import csv
import json
import os
from statistics import mean

DATA_CSV = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'execution_events.csv')


def summarize(path=DATA_CSV):
    if not os.path.exists(path):
        print(f"No execution events found at {path}")
        return

    units = []
    impacts = []
    simulated_exits = 0

    with open(path, 'r', newline='') as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            et = r.get('event_type')
            try:
                d = json.loads(r.get('data_json') or '{}')
            except Exception:
                d = {}
            if et == 'exit_simulated' or et == 'chunk_executed':
                # count simulated exits for exit_simulated only
                if et == 'exit_simulated':
                    simulated_exits += 1
                uc = d.get('unitsConsumed')
                if uc is not None:
                    try:
                        units.append(float(uc))
                    except Exception:
                        pass
                imp = d.get('estimated_impact_pct')
                if imp is not None:
                    try:
                        impacts.append(float(imp))
                    except Exception:
                        pass

    avg_units = mean(units) if units else 0
    avg_impact = mean(impacts) if impacts else 0

    print(f"Total Simulated Exits: {simulated_exits}")
    print(f"Average Compute Units: {avg_units:.1f}")
    print(f"Average Price Impact: {avg_impact:.3f}%")


if __name__ == '__main__':
    summarize()
